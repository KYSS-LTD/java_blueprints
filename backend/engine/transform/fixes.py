"""Автоисправления находок (S8.2–S8.5, S9.3): OPT-02, OPT-03, OPT-06, FIX-01, FIX-02, REF-02.

Каждое исправление проверяет предусловия на CST метода и либо строит точечные правки текста, либо
отказывается с понятной причиной (``Refusal``). Нигде не меняется то, что не входит в правку.
"""

from __future__ import annotations

import re
from collections.abc import Callable
from typing import Any

from tree_sitter import Node

from engine.analysis.model import Finding
from engine.edit.core import TextEdit, line_indent, line_start, node_span, ts_node
from engine.java import cst
from engine.java.project import JavaProject
from engine.java.semantic import Context
from engine.java.symbols import base_name
from engine.transform.base import (
    Check,
    FixProposal,
    Refusal,
    ancestors,
    duplicate_index,
    ensure_imports,
    refuse,
)
from engine.transform.dedupe import build as build_dedupe
from engine.transform.dedupe import checks_for, clone_places

_LOOPS = ("for_statement", "enhanced_for_statement", "while_statement", "do_statement")
_SAFE_HASH_TYPES = frozenset(
    {
        "String",
        "Integer",
        "Long",
        "Short",
        "Byte",
        "Character",
        "Boolean",
        "Double",
        "Float",
        "UUID",
        "BigInteger",
    }
)
_READ_ONLY = frozenset(
    {
        "size",
        "isEmpty",
        "get",
        "stream",
        "iterator",
        "indexOf",
        "lastIndexOf",
        "forEach",
        "toString",
        "equals",
        "hashCode",
        "toArray",
        "subList",
        "getFirst",
        "getLast",
        "containsAll",
        "sort",
        "contains",
    }
)
_SAFE_ARGUMENT_CALLS = frozenset(
    {
        "println",
        "print",
        "printf",
        "format",
        "valueOf",
        "join",
        "copyOf",
        "unmodifiableList",
        "sort",
        "reverse",
        "shuffle",
        "max",
        "min",
        "frequency",
        "equals",
        "hashCode",
        "toString",
        "size",
        "asList",
    }
)
_LINKED_ONLY = frozenset(
    {
        "addFirst",
        "addLast",
        "removeFirst",
        "removeLast",
        "getFirst",
        "getLast",
        "peek",
        "peekFirst",
        "peekLast",
        "poll",
        "pollFirst",
        "pollLast",
        "offer",
        "offerFirst",
        "offerLast",
        "push",
        "pop",
        "descendingIterator",
        "element",
        "removeFirstOccurrence",
        "removeLastOccurrence",
    }
)
_PRIMARY = {
    "identifier",
    "method_invocation",
    "field_access",
    "string_literal",
    "array_access",
    "parenthesized_expression",
    "this",
    "object_creation_expression",
}


class Ctx:
    """Файл и метод находки: чтение текста и поиск узлов."""

    def __init__(self, project: JavaProject, finding: Finding) -> None:
        self.project = project
        self.finding = finding
        self.file = finding.location.file
        self.source = project.files[self.file].encode("utf-8")
        self.parsed = project.parsed(self.file)
        method_id = finding.location.method_id
        self.member = project.member(method_id) if method_id else None
        self.body: Node | None = None
        if self.member is not None:
            declaration = project.context(self.member)[1]
            if declaration is not None:
                self.body = declaration.child_by_field_name("body") or cst.first_child_of_type(
                    declaration, "block"
                )

    def text(self, node: Node | None) -> str:
        return "" if node is None else self.source[node.start_byte : node.end_byte].decode("utf-8")

    def statement(self) -> Node:
        node_id = self.finding.location.node_id
        if not node_id:
            raise refuse("Место находки", "у находки нет узла графа")
        file, start, end = node_span(self.project, node_id)
        return ts_node(self.project, file, start, end)

    def resolver(self) -> Context:
        """Контекст разрешения имён метода находки."""
        assert self.member is not None
        return self.project.context(self.member)[0]

    def declaration_of(self, name: str) -> Node | None:
        """``local_variable_declaration`` метода, объявляющий локальную переменную ``name``."""
        if self.body is None:
            return None
        for node in cst.walk(self.body):
            if node.type == "local_variable_declaration":
                for declarator in cst.children_of_type(node, "variable_declarator"):
                    if self.text(declarator.child_by_field_name("name")) == name:
                        return node
        return None

    def references(self, name: str, scope: Node | None = None) -> list[Node]:
        """Использования переменной ``name`` (без имён методов и полей после точки и без самого объявления)."""
        found = []
        for node in cst.walk(scope or self.body or self.parsed.root):
            if node.type != "identifier" or self.text(node) != name or node.parent is None:
                continue
            parent = node.parent
            if parent.type == "variable_declarator" and parent.child_by_field_name("name") == node:
                continue
            if parent.type == "method_invocation" and _same(
                parent.child_by_field_name("name"), node
            ):
                continue
            if parent.type == "field_access" and _same(parent.child_by_field_name("field"), node):
                continue
            found.append(node)
        return found

    def fresh_name(self, wanted: str) -> str:
        used = {self.text(n) for n in cst.walk(self.body)} if self.body is not None else set()
        name, index = wanted, 2
        while name in used:
            name = f"{wanted}{index}"
            index += 1
        return name


def _arguments(call: Node) -> list[Node]:
    holder = call.child_by_field_name("arguments")
    return cst.named_children(holder) if holder is not None else []


def _same(a: Node | None, b: Node) -> bool:
    return a is not None and a.start_byte == b.start_byte and a.end_byte == b.end_byte


def _call_matches(
    c: Ctx, statement: Node, names: set[str], types: frozenset[str] | set[str]
) -> list[Node]:
    result = []
    for node in cst.walk(statement):
        if (
            node.type != "method_invocation"
            or c.text(node.child_by_field_name("name")) not in names
        ):
            continue
        receiver = node.child_by_field_name("object")
        if receiver is None or receiver.type != "identifier":
            continue
        declared = c.resolver().scope.get(c.text(receiver)) if c.member else None
        if declared and base_name(declared) in types:
            result.append(node)
    return result


def _generic_argument(type_text: str) -> str | None:
    match = re.search(r"<\s*(.+)\s*>\s*$", type_text)
    return match.group(1).strip() if match else None


def _indent_of(c: Ctx, node: Node) -> str:
    return line_indent(c.source, node.start_byte)


def _own_line(c: Ctx, node: Node) -> bool:
    return not c.source[line_start(c.source, node.start_byte) : node.start_byte].strip()


# --- OPT-03: накопление строки → StringBuilder ----------------------------------------------------------------


def _concat_operands(c: Ctx, statement: Node, name: str) -> list[Node] | None:
    """Операнды конкатенации ``name += e`` / ``name = name + a + b`` или ``None``, если оператор иной."""
    if statement.type != "expression_statement":
        return None
    assignment = cst.first_child_of_type(statement, "assignment_expression")
    if assignment is None or c.text(assignment.child_by_field_name("left")) != name:
        return None
    operator = c.text(assignment.child_by_field_name("operator"))
    right = assignment.child_by_field_name("right")
    if right is None:
        return None
    if operator == "+=":
        return [right]
    if operator != "=" or right.type != "binary_expression":
        return None
    operands: list[Node] = []
    current = right
    while (
        current.type == "binary_expression"
        and c.text(current.child_by_field_name("operator")) == "+"
    ):
        rhs = current.child_by_field_name("right")
        if rhs is None:
            return None
        operands.append(rhs)
        left = current.child_by_field_name("left")
        if left is None:
            return None
        current = left
    if current.type != "identifier" or c.text(current) != name:
        return None
    operands.reverse()
    return operands


def opt_03(c: Ctx) -> tuple[list[TextEdit], list[Check]]:
    statement = c.statement()
    assignment = cst.first_child_of_type(statement, "assignment_expression")
    if assignment is None:
        raise refuse("Конкатенация", "оператор не является присваиванием")
    name = c.text(assignment.child_by_field_name("left"))
    loops = [a for a in ancestors(statement) if a.type in _LOOPS]
    loops = [lp for lp in loops if not _declares(c, lp, name)]
    if not loops:
        raise refuse("Цикл", "цикл накопления не найден")
    loop = loops[-1]
    block = loop.parent
    if block is None or block.type != "block":
        raise refuse("Цикл в блоке", "цикл без фигурных скобок вокруг: сначала оберните его в блок")
    if not _own_line(c, loop):
        raise refuse("Цикл на своей строке", "цикл стоит на одной строке с другим кодом")
    siblings = cst.named_children(block)
    position = next(i for i, s in enumerate(siblings) if s.start_byte == loop.start_byte)
    declaration = next(
        (
            s
            for s in siblings[:position]
            if s.type == "local_variable_declaration"
            and any(
                c.text(d.child_by_field_name("name")) == name
                for d in cst.children_of_type(s, "variable_declarator")
            )
        ),
        None,
    )
    if declaration is None:
        raise refuse(
            "Переменная объявлена перед циклом",
            f"«{name}» не объявлена в том же блоке перед циклом",
        )
    declarators = cst.children_of_type(declaration, "variable_declarator")
    value = declarators[0].child_by_field_name("value") if len(declarators) == 1 else None
    if (
        c.text(declaration.child_by_field_name("type")) != "String"
        or value is None
        or value.type != "string_literal"
    ):
        raise refuse(
            "Начальное значение — строковый литерал",
            f"«{name}» не инициализирована литералом String",
        )
    index = siblings.index(declaration)
    between = siblings[index + 1 : position]
    if any(c.references(name, node) for node in between):
        raise refuse("Нет обращений между объявлением и циклом", f"«{name}» используется до цикла")

    recognized: list[tuple[Node, list[Node]]] = []
    inside = 0
    for node in cst.walk(loop):
        if node.type == "expression_statement":
            operands = _concat_operands(c, node, name)
            if operands is not None:
                recognized.append((node, operands))
                inside += len(c.references(name, node))
    if inside != len(c.references(name, loop)) or not recognized:
        raise refuse(
            "В цикле строка только дописывается", f"«{name}» читается в цикле не только через +="
        )
    for node in ancestors(loop):
        if node.start_byte <= block.start_byte:
            break
        if node.type in {"try_statement", "try_with_resources_statement"}:
            handlers = [h for h in node.children if h.type in {"catch_clause", "finally_clause"}]
            if any(c.references(name, h) for h in handlers):
                raise refuse(
                    "Нет чтения в catch/finally", f"«{name}» читается в catch/finally вокруг цикла"
                )

    builder = c.fresh_name(f"{name}Builder")
    base = _indent_of(c, loop)
    edits = [
        TextEdit(
            c.file,
            loop.start_byte,
            loop.start_byte,
            f"StringBuilder {builder} = new StringBuilder({name});\n{base}",
        ),
        TextEdit(c.file, loop.end_byte, loop.end_byte, f"\n{base}{name} = {builder}.toString();"),
    ]
    for node, operands in recognized:
        appends = "".join(f".append({c.text(op)})" for op in operands)
        edits.append(TextEdit(c.file, node.start_byte, node.end_byte, f"{builder}{appends};"))
    checks = [
        Check("Переменная — локальная String с литералом", True),
        Check("В цикле строка только дописывается", True),
        Check("Нет чтения между объявлением и циклом", True),
    ]
    return edits, checks


def _declares(c: Ctx, scope: Node, name: str) -> bool:
    return any(
        n.type == "variable_declarator" and c.text(n.child_by_field_name("name")) == name
        for n in cst.walk(scope)
    )


# --- OPT-02: contains по списку → параллельный HashSet --------------------------------------------------------------


def _hashable(c: Ctx, element: str) -> bool:
    simple = base_name(element)
    if simple in _SAFE_HASH_TYPES:
        return True
    decl = next((t for t in c.project.types() if t.name == simple), None)
    if decl is None:
        return False
    if decl.kind in {"record", "enum"}:
        return True
    names = {m.name for m in decl.members if m.kind == "method"}
    return {"equals", "hashCode"} <= names


def opt_02(c: Ctx) -> tuple[list[TextEdit], list[Check]]:
    statement = c.statement()
    calls = _call_matches(
        c,
        statement,
        {"contains", "indexOf", "lastIndexOf", "remove"},
        {"List", "ArrayList", "LinkedList", "Vector", "Collection"},
    )
    calls = [
        n
        for n in calls
        if c.text(n.child_by_field_name("name")) in {"contains", "indexOf", "lastIndexOf", "remove"}
    ]
    if not calls:
        raise refuse("Вызов в операторе", "вызов поиска по списку не найден")
    call = calls[min(duplicate_index(c.finding.id), len(calls) - 1)]
    if c.text(call.child_by_field_name("name")) != "contains":
        raise refuse("Поддерживается contains", "замена на HashSet возможна только для contains")
    receiver = call.child_by_field_name("object")
    assert receiver is not None
    name = c.text(receiver)
    declaration = c.declaration_of(name)
    if declaration is None:
        raise refuse("Локальная переменная", f"«{name}» не локальная переменная метода")
    declarators = cst.children_of_type(declaration, "variable_declarator")
    value = declarators[0].child_by_field_name("value") if len(declarators) == 1 else None
    type_text = c.text(declaration.child_by_field_name("type"))
    element = _generic_argument(type_text)
    if (
        value is None
        or value.type != "object_creation_expression"
        or base_name(c.text(value.child_by_field_name("type"))) not in {"ArrayList", "LinkedList"}
    ):
        raise refuse(
            "Список создан в объявлении",
            f"«{name}» не инициализирован через new ArrayList/LinkedList",
        )
    if element is None or not _hashable(c, element):
        raise refuse(
            "Хеш элементов согласован с equals",
            f"для типа «{element or '?'}» нет надёжных equals/hashCode",
        )

    set_name = c.fresh_name(f"{name}Set")
    edits: list[TextEdit] = []
    adds = 0
    for ref in c.references(name):
        parent = ref.parent
        if parent is None:
            continue
        if parent.type == "method_invocation" and _same(parent.child_by_field_name("object"), ref):
            method = c.text(parent.child_by_field_name("name"))
            call_args = _arguments(parent)
            arity = len(call_args)
            if method == "contains" and arity == 1:
                edits.append(TextEdit(c.file, ref.start_byte, ref.end_byte, set_name))
            elif method == "add" and arity == 1:
                holder = parent.parent
                if (
                    holder is None
                    or holder.type != "expression_statement"
                    or holder.parent is None
                    or holder.parent.type != "block"
                ):
                    raise refuse(
                        "add — отдельный оператор в блоке",
                        "add используется как выражение или в теле без скобок",
                    )
                argument = c.text(call_args[0])
                edits.append(
                    TextEdit(
                        c.file, holder.end_byte, holder.end_byte, f" {set_name}.add({argument});"
                    )
                )
                adds += 1
            elif method in _READ_ONLY:
                continue
            else:
                raise refuse(
                    "Состав списка меняется только через add",
                    f"вызов {name}.{method}() меняет состав списка",
                )
        elif (
            parent.type == "enhanced_for_statement"
            and _same(parent.child_by_field_name("value"), ref)
            or parent.type == "return_statement"
        ):
            continue
        elif (
            parent.type == "argument_list"
            and parent.parent is not None
            and parent.parent.type == "method_invocation"
        ):
            callee = parent.parent
            if (
                c.resolver().resolve_call(callee)
                or c.text(callee.child_by_field_name("name")) not in _SAFE_ARGUMENT_CALLS
            ):
                raise refuse(
                    "Список не передаётся в другие методы",
                    f"«{name}» передаётся в {c.text(callee.child_by_field_name('name'))}()",
                )
        else:
            raise refuse(
                "Нет псевдонимов списка", f"«{name}» используется в неподдерживаемом выражении"
            )
    if not any(e.text == set_name for e in edits):
        raise refuse("Есть contains по списку", "вызовов contains не найдено")

    base = _indent_of(c, declaration)
    edits.append(
        TextEdit(
            c.file,
            declaration.end_byte,
            declaration.end_byte,
            f"\n{base}Set<{element}> {set_name} = new HashSet<>({name});",
        )
    )
    imports = ensure_imports(c.source.decode("utf-8"), ["java.util.Set", "java.util.HashSet"])
    if imports is not None:
        edits.append(TextEdit(c.file, imports.start, imports.end, imports.text))
    checks = [
        Check("Список — локальная переменная, созданная через new", True),
        Check("Состав списка меняется только через add", True),
        Check("Список не передаётся в другие методы", True),
        Check("Хеш элементов согласован с equals", True),
    ]
    return edits, checks


# --- OPT-06: LinkedList → ArrayList ------------------------------------------------------------------------------


def opt_06(c: Ctx) -> tuple[list[TextEdit], list[Check]]:
    statement = c.statement()
    calls = _call_matches(c, statement, {"get", "set"}, {"LinkedList"})
    if not calls:
        raise refuse(
            "Индексный доступ к LinkedList",
            "для вставки в начало ArrayList замена коллекции вручную: ArrayDeque",
        )
    receiver = calls[0].child_by_field_name("object")
    assert receiver is not None
    name = c.text(receiver)
    declaration = c.declaration_of(name)
    if declaration is None:
        raise refuse("Локальная переменная", f"«{name}» не локальная переменная метода")
    for ref in c.references(name):
        parent = ref.parent
        if (
            parent is not None
            and parent.type == "method_invocation"
            and _same(parent.child_by_field_name("object"), ref)
        ):
            method = c.text(parent.child_by_field_name("name"))
            if method in _LINKED_ONLY:
                raise refuse("Только методы списка", f"{name}.{method}() есть только у LinkedList")
        elif parent is not None and parent.type == "argument_list" and parent.parent is not None:
            callee = parent.parent
            if c.member and c.resolver().resolve_call(callee):
                raise refuse(
                    "Список не передаётся в методы проекта",
                    f"«{name}» передаётся в {c.text(callee.child_by_field_name('name'))}()",
                )
        elif parent is not None and parent.type in {"assignment_expression", "variable_declarator"}:
            raise refuse("Нет псевдонимов списка", f"«{name}» присваивается другой переменной")
    edits = []
    for node in cst.walk(declaration):
        if node.type == "type_identifier" and c.text(node) == "LinkedList":
            edits.append(TextEdit(c.file, node.start_byte, node.end_byte, "ArrayList"))
    if not edits:
        raise refuse("Тип объявлен как LinkedList", f"«{name}» объявлена не как LinkedList")
    imports = ensure_imports(c.source.decode("utf-8"), ["java.util.ArrayList"])
    if imports is not None:
        edits.append(TextEdit(c.file, imports.start, imports.end, imports.text))
    return edits, [
        Check("Используются только методы списка", True),
        Check("Список не передаётся в методы проекта", True),
    ]


# --- FIX-01: == → equals --------------------------------------------------------------------------------------------


def fix_01(c: Ctx) -> tuple[list[TextEdit], list[Check]]:
    from engine.analysis.rules import MemberView, is_string

    assert c.member is not None
    view = MemberView(c.project, None, c.member)
    statement = c.statement()
    matches = []
    for node in cst.walk(statement):
        if node.type != "binary_expression" or c.text(node.child_by_field_name("operator")) not in {
            "==",
            "!=",
        }:
            continue
        left, right = node.child_by_field_name("left"), node.child_by_field_name("right")
        if left is None or right is None or "null_literal" in (left.type, right.type):
            continue
        if is_string(view, left) or is_string(view, right):
            matches.append(node)
    if not matches:
        raise refuse("Сравнение строк", "сравнение строк по == не найдено")
    node = matches[min(duplicate_index(c.finding.id), len(matches) - 1)]
    left, right = node.child_by_field_name("left"), node.child_by_field_name("right")
    assert left is not None and right is not None
    negate = c.text(node.child_by_field_name("operator")) == "!="
    if right.type == "string_literal" and left.type != "string_literal":
        left, right = right, left  # литерал слева: ``"a".equals(x)`` не бросает NPE

    def operand(item: Node) -> str:
        text = c.text(item)
        return text if item.type in _PRIMARY else f"({text})"

    replacement = f"{'!' if negate else ''}{operand(left)}.equals({c.text(right)})"
    checks = [
        Check("Сравнение значений, а не ссылок", True),
        Check("Поведение меняется (исправление ошибки)", True),
    ]
    return [TextEdit(c.file, node.start_byte, node.end_byte, replacement)], checks


# --- FIX-02: пустой catch ----------------------------------------------------------------------------------------------


def fix_02(c: Ctx) -> tuple[list[TextEdit], list[Check]]:
    clause = c.statement()
    if clause.type != "catch_clause":
        raise refuse("Блок catch", "узел находки — не catch")
    body = clause.child_by_field_name("body")
    parameter = cst.first_child_of_type(clause, "catch_formal_parameter")
    if (
        body is None
        or parameter is None
        or cst.named_children(body)
        and any(
            ch.type != "block_comment" and ch.type != "line_comment"
            for ch in body.children
            if ch.is_named
        )
    ):
        raise refuse("Блок catch пуст", "в блоке уже есть операторы")
    variable = c.text(parameter.child_by_field_name("name"))
    base = line_indent(c.source, clause.start_byte)
    unit = "    "
    inner = base + unit
    close = body.end_byte - 1
    comments = [ch for ch in body.children if ch.type in {"line_comment", "block_comment"}]
    statement_text = f"System.err.println({variable}); // TODO: обработать исключение"
    if comments:
        last = comments[-1].end_byte
        edit = TextEdit(c.file, last, last, f"\n{inner}{statement_text}")
    else:
        edit = TextEdit(c.file, body.start_byte + 1, close, f"\n{inner}{statement_text}\n{base}")
    return [edit], [
        Check("Исключение больше не проглатывается", True),
        Check("Поведение меняется (добавляется вывод в stderr)", True),
    ]


# --- REF-02: клоны → общий метод ------------------------------------------------------------------------------------------


def ref_02(c: Ctx) -> tuple[list[TextEdit], list[Check]]:
    where = c.finding.location
    places = clone_places(c.project, where.file, where.start_line, where.end_line)
    if len(places) < 2:
        raise refuse("Клон найден", "вхождения клона больше не находятся в проекте")
    result = build_dedupe(c.project, places)
    return result.edits, checks_for(result)


# --- реестр ----------------------------------------------------------------------------------------------------------------

Builder = Callable[[Ctx], tuple[list[TextEdit], list[Check]]]

SPECS: dict[str, tuple[str, str, str, bool, Builder | None]] = {
    "PERF-02": (
        "OPT-02",
        "Параллельный HashSet для contains",
        "Поиск `contains` выполняется по `HashSet`, порядок и состав списка сохраняются.",
        True,
        opt_02,
    ),
    "PERF-03": (
        "OPT-03",
        "Накопление строки через StringBuilder",
        "Строка собирается в `StringBuilder`, результат присваивается после цикла.",
        True,
        opt_03,
    ),
    "PERF-06": (
        "OPT-06",
        "LinkedList → ArrayList",
        "Связный список заменяется массивным: доступ по индексу становится O(1).",
        True,
        opt_06,
    ),
    "BUG-01": (
        "FIX-01",
        "Сравнение строк через equals",
        "`==` между строками заменяется на `equals`.",
        False,
        fix_01,
    ),
    "BUG-02": (
        "FIX-02",
        "Не проглатывать исключение",
        "В пустой `catch` добавляется вывод исключения в stderr.",
        False,
        fix_02,
    ),
    "MAINT-02": (
        "REF-02",
        "Общий метод вместо клонов",
        "Одинаковый по структуре код выносится в один метод, различия становятся его параметрами.",
        True,
        ref_02,
    ),
    "PERF-01": (
        "OPT-01",
        "Индексация по ключу вместо вложенного поиска",
        "Требует подтверждения: порядок и повторы пар должны быть несущественны.",
        False,
        None,
    ),
}


def fix_id_for(rule_id: str) -> str | None:
    spec = SPECS.get(rule_id)
    return spec[0] if spec else None


def propose(project: JavaProject, finding: Finding) -> FixProposal | None:
    spec = SPECS.get(finding.rule_id)
    if spec is None:
        return None
    fix_id, title, summary, preserving, build = spec
    proposal = FixProposal(fix_id, title, summary, preserving)
    if build is None:
        proposal.checks.append(
            Check(
                "Автоматическое преобразование",
                False,
                "для этого паттерна подтверждение даёт только человек",
            )
        )
        return proposal
    try:
        edits, checks = build(Ctx(project, finding))
    except Refusal as refusal:
        proposal.checks.append(refusal.check)
        return proposal
    proposal.edits = [
        TextEdit(finding.location.file if not e.file else e.file, e.start, e.end, e.text)
        for e in edits
    ]
    proposal.checks = checks
    return proposal


def describe(proposal: FixProposal) -> dict[str, Any]:
    return {
        "fix_id": proposal.fix_id,
        "available": proposal.available,
        "behavior_preserving": proposal.behavior_preserving,
        "preconditions": [
            {"name": c.name, "passed": c.passed, "reason": c.reason} for c in proposal.checks
        ],
    }


__all__ = ["SPECS", "describe", "fix_id_for", "propose"]
