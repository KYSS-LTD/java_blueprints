"""OPT-13 (S8.4): метод-поиск по ключу в поле-коллекции → индекс ``Map``, который ведётся вместе со списком.

Исправление применимо только к закрытому полю ``private List<T>`` и методу формы ``T find(K key)`` (см.
``engine.analysis.lookup``). Все обращения к полю в классе должны быть понятны: чтение, ``add(x)`` (индекс
пополняется), любое другое изменение (индекс сбрасывается в ``null`` и строится заново при ближайшем поиске).
Если список где-то отдаётся наружу, меняется через итератор или представление, а ключ элемента можно изменить
после добавления, исправление отказывается.
"""

from __future__ import annotations

from typing import TYPE_CHECKING

from tree_sitter import Node

from engine.analysis.lookup import Lookup, chain_members, match_lookup
from engine.edit.core import TextEdit, indent_unit, line_indent, line_start, ts_node
from engine.java import cst
from engine.transform.base import Check, ensure_imports, refuse
from engine.transform.fixes_more import _BOXED, _fresh_name

if TYPE_CHECKING:
    from engine.java.project import JavaProject
    from engine.java.semantic import Context
    from engine.java.symbols import Member, TypeDecl
    from engine.transform.fixes import Ctx

_READS = frozenset(
    {
        "size",
        "isEmpty",
        "get",
        "contains",
        "containsAll",
        "indexOf",
        "lastIndexOf",
        "stream",
        "parallelStream",
        "forEach",
        "toString",
        "equals",
        "hashCode",
        "toArray",
        "getFirst",
        "getLast",
        "peek",
        "peekFirst",
        "peekLast",
        "element",
    }
)
_INVALIDATES = frozenset(
    {
        "remove",
        "removeAll",
        "removeIf",
        "retainAll",
        "clear",
        "addAll",
        "set",
        "sort",
        "replaceAll",
        "addFirst",
        "addLast",
        "removeFirst",
        "removeLast",
        "push",
        "pop",
        "poll",
        "pollFirst",
        "pollLast",
        "offer",
        "offerFirst",
        "offerLast",
    }
)
_COPIES = frozenset(
    {
        "ArrayList",
        "LinkedList",
        "HashSet",
        "LinkedHashSet",
        "TreeSet",
        "ArrayDeque",
        "PriorityQueue",
    }
)
_STATIC_READS = {
    ("Collections", "max"),
    ("Collections", "min"),
    ("Collections", "frequency"),
    ("Collections", "binarySearch"),
    ("Collections", "unmodifiableList"),
    ("List", "copyOf"),
    ("Set", "copyOf"),
    ("String", "join"),
}
_STATIC_MUTATES = frozenset(
    {"sort", "reverse", "shuffle", "swap", "fill", "rotate", "replaceAll", "addAll"}
)
_BLOCKS = {"block", "switch_block_statement_group", "constructor_body"}
_COMMENTS = ("line_comment", "block_comment")
_SHADOWS = {
    "formal_parameter",
    "spread_parameter",
    "catch_formal_parameter",
    "inferred_parameters",
    "resource",
}


class _Site:
    """Место изменения списка: куда вставить поддержку индекса."""

    __slots__ = ("argument", "kind", "statement")

    def __init__(self, kind: str, statement: Node, argument: Node | None = None) -> None:
        self.kind = kind  # append | after | before
        self.statement = statement
        self.argument = argument


def _in_block(statement: Node) -> bool:
    return statement.parent is not None and statement.parent.type in _BLOCKS


def _text(context: Context, node: Node | None) -> str:
    return cst.text_of(context.parsed, node)


def _is_this_field(context: Context, node: Node, name: str) -> bool:
    obj = node.child_by_field_name("object")
    return (
        obj is not None
        and obj.type == "this"
        and _text(context, node.child_by_field_name("field")) == name
    )


def _is_field_of(parent: Node, field: str, node: Node) -> bool:
    child = parent.child_by_field_name(field)
    return child is not None and child.id == node.id


def _references(context: Context, scope: Node, name: str) -> list[Node]:
    """Обращения к полю ``name`` в ``scope``: идентификаторы и ``this.name``; иное — отказ."""
    found: list[Node] = []
    for node in cst.walk(scope):
        if (
            node.type == "field_access"
            and _text(context, node.child_by_field_name("field")) == name
        ):
            if not _is_this_field(context, node, name):
                raise refuse(
                    "Поле читается только через имя или this", "обращение к полю другого объекта"
                )
            found.append(node)
            continue
        if node.type != "identifier" or _text(context, node) != name or node.parent is None:
            continue
        parent = node.parent
        if (parent.type == "field_access" and _is_field_of(parent, "field", node)) or (
            parent.type == "method_invocation" and _is_field_of(parent, "name", node)
        ):
            continue  # ``this.name`` разобран выше; ``name()`` — вызов метода
        if parent.type == "variable_declarator" and _is_field_of(parent, "name", node):
            if parent.parent is not None and parent.parent.type == "field_declaration":
                continue  # объявление самого поля
            raise refuse("Имя поля не затенено", f"локальная переменная «{name}» скрывает поле")
        if parent.type in _SHADOWS or (
            parent.type == "enhanced_for_statement" and _is_field_of(parent, "name", node)
        ):
            raise refuse("Имя поля не затенено", f"параметр или переменная «{name}» скрывает поле")
        found.append(node)
    return found


def _member_at(project: JavaProject, owner: TypeDecl, node: Node) -> Member | None:
    """Самый внутренний метод, конструктор или инициализатор класса, содержащий узел."""
    best: Member | None = None
    for decl in owner.iter_types():
        for member in decl.members:
            if member.kind not in {"method", "constructor", "initializer"}:
                continue
            inside = member.start_byte <= node.start_byte and node.end_byte <= member.end_byte
            if inside and (
                best is None
                or member.end_byte - member.start_byte < best.end_byte - best.start_byte
            ):
                best = member
    return best


def _classify(
    project: JavaProject,
    context: Context,
    ref: Node,
    depth: int = 0,
    readonly_only: bool = False,
) -> _Site | None:
    """``None`` — чтение; иначе место изменения. Всё непонятное — отказ."""
    parent = ref.parent
    assert parent is not None

    def text(node: Node | None) -> str:
        return _text(context, node)

    def stop(why: str) -> None:
        raise refuse("Все обращения к списку понятны", f"{why}: «{text(parent)[:50]}»")

    if parent.type == "enhanced_for_statement" and _is_field_of(parent, "value", ref):
        return None
    if parent.type == "method_invocation" and _is_field_of(parent, "object", ref):
        name = text(parent.child_by_field_name("name"))
        arguments = cst.named_children(parent.child_by_field_name("arguments") or parent)
        if name in _READS:
            return None
        if readonly_only:
            stop(f"метод {name} меняет список")
        statement = parent.parent
        if statement is None:
            stop("вызов вне оператора")
        assert statement is not None
        if name == "add" and len(arguments) == 1 and statement.type == "expression_statement":
            if not _in_block(statement):
                stop("add вне блока")
            return _Site("append", statement, arguments[0])
        if name in _INVALIDATES or name == "add":
            if statement.type == "expression_statement" and _in_block(statement):
                return _Site("after", statement)
            if statement.type == "return_statement" and _in_block(statement):
                return _Site("before", statement)
            if (
                statement.type == "variable_declarator"
                and statement.parent is not None
                and statement.parent.type == "local_variable_declaration"
                and _in_block(statement.parent)
            ):
                return _Site("after", statement.parent)
            stop(f"{name} внутри выражения")
        stop(f"метод {name} не разобран (итератор, представление)")
    if parent.type == "assignment_expression" and _is_field_of(parent, "left", ref):
        statement = parent.parent
        if (
            not readonly_only
            and statement is not None
            and statement.type == "expression_statement"
            and _in_block(statement)
        ):
            return _Site("after", statement)
        stop("присваивание поля")
    if parent.type == "argument_list" and parent.parent is not None:
        call = parent.parent
        position = next((i for i, a in enumerate(cst.named_children(parent)) if a.id == ref.id), -1)
        if call.type == "object_creation_expression":
            kind = text(call.child_by_field_name("type")).split("<")[0].strip()
            if kind in _COPIES:
                return None
            stop("список отдаётся в конструктор")
        if call.type == "method_invocation":
            receiver = text(call.child_by_field_name("object"))
            name = text(call.child_by_field_name("name"))
            if (receiver, name) in _STATIC_READS:
                return None
            if receiver == "Collections" and name in _STATIC_MUTATES and not readonly_only:
                statement = call.parent
                if (
                    statement is not None
                    and statement.type == "expression_statement"
                    and _in_block(statement)
                ):
                    return _Site("after", statement)
                stop(f"Collections.{name} внутри выражения")
            callees = context.resolve_call(call)
            if callees and depth < 1 and position >= 0:
                for callee in callees:
                    if not _readonly_parameter(project, callee, position, depth + 1):
                        stop(f"метод {callee.name} может менять переданный список")
                return None
        stop("список передаётся в вызов")
    stop("ссылка на список используется иначе")
    return None


def _readonly_parameter(project: JavaProject, callee: Member, position: int, depth: int) -> bool:
    """Параметр метода проекта только читается: перебор, ``size()``, ``get()`` и им подобное."""
    if position >= len(callee.params) or callee.params[position].varargs:
        return False
    context, declaration = project.context(callee)
    body = declaration.child_by_field_name("body") if declaration is not None else None
    if body is None:
        return False
    name = callee.params[position].name
    try:
        for ref in _references(context, body, name):
            if _classify(project, context, ref, depth, readonly_only=True) is not None:
                return False
    except Exception:  # noqa: BLE001 - Refusal: параметр используется непонятным образом
        return False
    return True


def _key_names(c: Ctx, lookup: Lookup) -> set[str]:
    """Имена, от которых зависит ключ элемента (поля и всё, что читают его геттеры)."""
    members = chain_members(c.project.table, lookup.element_type, lookup.chain)
    if members is None:
        raise refuse("Ключ элемента известен", "не удалось разобрать геттеры ключа")
    element = c.project.table.type_named(lookup.element_type.split("<")[0].strip())
    names: set[str] = set()
    for member in members:
        if member.kind == "field":
            names.add(member.name)
            continue
        if element is not None and any(
            m.id != member.id for m in c.project.table.with_overrides(element, [member])
        ):
            raise refuse(
                "Геттер ключа не переопределён", f"{member.name}() переопределяется в подклассе"
            )
        context, declaration = c.project.context(member)
        body = declaration.child_by_field_name("body") if declaration is not None else None
        if body is None or any(n.type == "method_invocation" for n in cst.walk(body)):
            raise refuse("Геттер ключа прост", f"{member.name}() не просто читает поля")
        names.update(_text(context, n) for n in cst.walk(body) if n.type == "identifier")
    return names


def _check_key_is_stable(c: Ctx, lookup: Lookup) -> None:
    names = _key_names(c, lookup)
    element = c.project.table.type_named(lookup.element_type.split("<")[0].strip())
    owners = [element, *c.project.table.supertypes(element)] if element is not None else []
    fields = [m for t in owners for m in t.members if m.kind == "field" and m.name in names]
    if fields and all("final" in m.modifiers for m in fields):
        return
    for member in c.project.members():
        if member.kind != "method" or not member.has_body:
            continue
        context, declaration = c.project.context(member)
        body = declaration.child_by_field_name("body") if declaration is not None else None
        if body is None:
            continue
        for node in cst.walk(body):
            target = None
            if node.type == "assignment_expression":
                target = node.child_by_field_name("left")
            elif node.type == "update_expression":
                target = next(
                    (n for n in node.children if n.type in {"identifier", "field_access"}), None
                )
            if target is None:
                continue
            written = (
                _text(context, target.child_by_field_name("field"))
                if target.type == "field_access"
                else _text(context, target)
            )
            if written in names and written in {m.name for m in fields}:
                raise refuse(
                    "Ключ элемента не меняется после добавления",
                    f"поле «{written}» присваивается в {member.name}",
                )


def _line_end(source: bytes, position: int) -> int:
    """Конец строки, если после ``position`` на ней только пробелы или ``//``-комментарий, иначе ``position``."""
    end = source.find(b"\n", position)
    if end < 0:
        end = len(source)
    rest = source[position:end].strip()
    return end if not rest or rest.startswith(b"//") else position


def opt_13(c: Ctx) -> tuple[list[TextEdit], list[Check]]:
    member = c.member
    if member is None:
        raise refuse("Метод-поиск", "находка не привязана к методу")
    lookup = match_lookup(c.project, member)
    if lookup is None:
        raise refuse(
            "Метод-поиск по ключу", "метод устроен иначе: нужен перебор поля и `return null`"
        )
    context = c.resolver()
    owner = context.owner
    scope = ts_node(c.project, c.file, owner.start_byte, owner.end_byte)
    name = lookup.field.name

    for nested in owner.iter_types():
        if nested.id != owner.id and any(
            m.kind == "field" and m.name == name for m in nested.members
        ):
            raise refuse(
                "Имя поля не затенено", f"во вложенном классе {nested.name} есть поле «{name}»"
            )
    sites: list[_Site] = []
    for ref in _references(context, scope, name):
        user = _member_at(c.project, owner, ref)
        if user is None:
            raise refuse(
                "Обращения к полю лежат в методах", "поле используется в инициализаторе поля"
            )
        user_context = c.project.context(user)[0]
        site = _classify(c.project, user_context, ref)
        if site is not None:
            sites.append(site)
    _check_key_is_stable(c, lookup)

    source_text = c.source.decode("utf-8")
    unit = indent_unit(source_text)
    used = {c.text(n) for n in cst.walk(c.parsed.root) if n.type == "identifier"}
    last = lookup.chain[-1][0]
    suffix = (
        last[3:]
        if last.startswith("get") and len(last) > 3
        else last[2:]
        if last.startswith("is") and len(last) > 2
        else last
    )
    suffix = suffix[:1].upper() + suffix[1:]
    index = _fresh_name(f"{name}By{suffix}", used)
    built = _fresh_name("built", used | {index})
    boxed = _BOXED.get(lookup.key_type, lookup.key_type)
    element = lookup.element_type

    edits: list[TextEdit] = []
    # объявление индекса сразу после поля
    field_node = ts_node(c.project, c.file, lookup.field.start_byte, lookup.field.end_byte)
    if len(cst.children_of_type(field_node, "variable_declarator")) != 1:
        raise refuse("Одно поле в объявлении", f"в объявлении поля «{name}» несколько переменных")
    field_indent = line_indent(c.source, field_node.start_byte)
    at = _line_end(c.source, field_node.end_byte)
    edits.append(
        TextEdit(c.file, at, at, f"\n{field_indent}private Map<{boxed}, {element}> {index};")
    )
    # тело метода-поиска
    base = line_indent(c.source, lookup.method.start_byte)
    inner = base + unit
    deep = inner + unit
    body_text = (
        "{\n"
        f"{inner}if ({index} == null) {{\n"
        f"{deep}Map<{boxed}, {element}> {built} = new HashMap<>();\n"
        f"{deep}for ({element} {lookup.variable} : {name}) {{\n"
        f"{deep}{unit}{built}.putIfAbsent({lookup.key}, {lookup.variable});\n"
        f"{deep}}}\n"
        f"{deep}{index} = {built};\n"
        f"{inner}}}\n"
        f"{inner}return {index}.get({lookup.parameter});\n"
        f"{base}}}"
    )
    edits.append(TextEdit(c.file, lookup.body.start_byte, lookup.body.end_byte, body_text))
    # места изменения списка
    for site in sites:
        statement = site.statement
        pad = line_indent(c.source, statement.start_byte)
        if site.kind == "before":
            start = line_start(c.source, statement.start_byte)
            edits.append(TextEdit(c.file, start, start, f"{pad}{index} = null;\n"))
            continue
        position = _line_end(c.source, statement.end_byte)
        if site.kind == "append" and site.argument is not None:
            argument = c.text(site.argument)
            simple = site.argument.type in {"identifier"} or (
                site.argument.type == "field_access"
                and _is_this_field(
                    context, site.argument, c.text(site.argument.child_by_field_name("field"))
                )
            )
            if simple:
                key = argument + lookup.key[len(lookup.variable) :]
                text = (
                    f"\n{pad}if ({index} != null) {{\n"
                    f"{pad}{unit}{index}.putIfAbsent({key}, {argument});\n"
                    f"{pad}}}"
                )
                edits.append(TextEdit(c.file, position, position, text))
                continue
        edits.append(TextEdit(c.file, position, position, f"\n{pad}{index} = null;"))
    imports = ensure_imports(source_text, ["java.util.HashMap", "java.util.Map"])
    if imports is not None:
        edits.append(TextEdit(c.file, imports.start, imports.end, imports.text))
    appends = sum(1 for s in sites if s.kind == "append")
    return edits, [
        Check(f"`{member.name}` ищет в `{name}` по ключу `{lookup.key}`", True),
        Check(
            f"Поле закрыто (private); все обращения к нему понятны: изменений списка {len(sites)}",
            True,
        ),
        Check("Ключ элемента не меняется после добавления в список", True),
        Check(
            f"Индекс сбрасывается при изменении списка (мест: {len(sites) - appends}) и пополняется при `add` (мест: {appends})",
            True,
        ),
    ]


INDEX_SPECS = {
    "PERF-13": (
        "OPT-13",
        "Индекс для метода-поиска",
        "Метод находит элемент по ключу в Map, который ведётся вместе со списком: добавление пополняет индекс, "
        "остальные изменения сбрасывают его.",
        True,
        opt_13,
    )
}
