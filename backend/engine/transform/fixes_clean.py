"""Исправления дефектов и чистки кода (S8.5): FIX-03, 07, 09, 10 и CLEAN-01, 02, 03.

FIX-* исправляют ошибки и потому ``behavior_preserving = false``: расхождения сценариев нужно подтвердить.
CLEAN-* поведение не меняют. Как и в ``fixes_more``, каждый построитель повторно проверяет условия
на тексте и отказывается с понятной причиной.
"""

from __future__ import annotations

import re
from typing import TYPE_CHECKING

from tree_sitter import Node

from engine.edit.core import EditError, TextEdit, indent_unit, line_indent, line_start
from engine.edit.operations import removal_range
from engine.edit.pipeline import apply_ops
from engine.java import cst
from engine.transform.base import Check, refuse
from engine.transform.dedupe import literal_type
from engine.transform.fixes_more import statement_of

if TYPE_CHECKING:
    from engine.transform.fixes import Ctx

_REMOVE_IF = frozenset(
    {
        "List",
        "ArrayList",
        "LinkedList",
        "Set",
        "HashSet",
        "LinkedHashSet",
        "TreeSet",
        "Collection",
        "Queue",
        "Deque",
        "ArrayDeque",
        "PriorityQueue",
    }
)
_PRIMITIVE_TYPES = frozenset({"int", "long", "short", "byte", "char", "boolean", "double", "float"})
_TERMINAL = frozenset(
    {
        "break_statement",
        "return_statement",
        "throw_statement",
        "continue_statement",
        "yield_statement",
    }
)
_NUMBER = frozenset(
    {
        "decimal_integer_literal",
        "hex_integer_literal",
        "octal_integer_literal",
        "binary_integer_literal",
        "decimal_floating_point_literal",
    }
)
_ALLOWED_NUMBERS = {"0", "1", "0L", "1L", "0.0", "1.0", "0.0f", "1.0f", "0d", "1d"}
_IO_HANDLERS = ("IOException", "Exception", "Throwable")


def _writes(c: Ctx, name: str, scope: Node) -> bool:
    for node in cst.walk(scope):
        if node.type == "assignment_expression":
            left = node.child_by_field_name("left")
            if left is not None and left.type == "identifier" and c.text(left) == name:
                return True
        elif node.type == "update_expression" and any(
            ch.type == "identifier" and c.text(ch) == name for ch in node.children
        ):
            return True
    return False


def _own_line(c: Ctx, node: Node) -> bool:
    return not c.source[line_start(c.source, node.start_byte) : node.start_byte].strip()


def _ancestor(node: Node, *types: str) -> Node | None:
    current = node.parent
    while current is not None:
        if current.type in types:
            return current
        current = current.parent
    return None


# --- FIX-07: результат метода String отброшен ----------------------------------------------------------------


def fix_07(c: Ctx) -> tuple[list[TextEdit], list[Check]]:
    statement = statement_of(c)
    call = cst.first_child_of_type(statement, "method_invocation")
    if statement.type != "expression_statement" or call is None:
        raise refuse("Вызов метода", "оператор не является вызовом метода")
    receiver = call.child_by_field_name("object")
    if receiver is None or receiver.type != "identifier":
        raise refuse(
            "Получатель — переменная", "результат некуда присвоить: получатель не переменная"
        )
    name = c.text(receiver)
    declaration = c.declaration_of(name)
    if declaration is not None and c.text(declaration).lstrip().startswith("final"):
        raise refuse("Переменная не final", f"«{name}» объявлена final")
    if declaration is None:
        found = c.project.table.field_type(c.resolver().owner, name)
        if found is not None and "final" in found[0].modifiers:
            raise refuse("Переменная не final", f"поле «{name}» объявлено final")
    return [TextEdit(c.file, statement.start_byte, statement.start_byte, f"{name} = ")], [
        Check("Получатель — изменяемая переменная", True),
        Check("Поведение меняется (результат теперь используется)", True),
    ]


# --- FIX-09: изменение коллекции при обходе → removeIf ---------------------------------------------------------


def fix_09(c: Ctx) -> tuple[list[TextEdit], list[Check]]:
    statement = statement_of(c)
    loop = (
        statement
        if statement.type == "enhanced_for_statement"
        else _ancestor(statement, "enhanced_for_statement")
    )
    if loop is None:
        raise refuse("Цикл for each", "изменение не лежит в цикле for each")
    value = loop.child_by_field_name("value")
    variable = c.text(loop.child_by_field_name("name"))
    element_type = c.text(loop.child_by_field_name("type"))
    body = loop.child_by_field_name("body")
    if value is None or body is None or value.type != "identifier":
        raise refuse("Цикл по переменной", "цикл идёт не по переменной-коллекции")
    collection = c.text(value)
    if c.resolver().scope.get(collection, "").split("<")[0] not in _REMOVE_IF:
        raise refuse("Коллекция поддерживает removeIf", f"«{collection}» не List или Set")
    if element_type in _PRIMITIVE_TYPES:
        raise refuse("Элемент — объект", "remove(int) удалял бы по индексу, а не по значению")
    statements = cst.named_children(body) if body.type == "block" else [body]
    if len(statements) != 1 or statements[0].type != "if_statement":
        raise refuse("Тело цикла — одно if", "в теле цикла не только условное удаление")
    branch = statements[0]
    if branch.child_by_field_name("alternative") is not None:
        raise refuse("Тело цикла — одно if", "у if есть ветка else")
    consequence = branch.child_by_field_name("consequence")
    inner = (
        cst.named_children(consequence)
        if consequence is not None and consequence.type == "block"
        else [consequence]
    )
    removal = inner[0] if len(inner) == 1 and inner[0] is not None else None
    call = cst.first_child_of_type(removal, "method_invocation") if removal is not None else None
    arguments = call.child_by_field_name("arguments") if call is not None else None
    items = cst.named_children(arguments) if arguments is not None else []
    if (
        call is None
        or c.text(call.child_by_field_name("object")) != collection
        or c.text(call.child_by_field_name("name")) != "remove"
        or len(items) != 1
        or c.text(items[0]) != variable
    ):
        raise refuse(
            "Удаляется элемент цикла",
            f"в ветке не `{collection}.remove({variable})` единственным оператором",
        )
    condition = branch.child_by_field_name("condition")
    assert condition is not None
    text = c.text(condition)
    if text.startswith("(") and text.endswith(")"):
        text = text[1:-1]
    for node in cst.walk(condition):
        if node.type in {"assignment_expression", "update_expression"}:
            raise refuse("Условие без побочных эффектов", "в условии есть присваивание")
        if (
            node.type == "identifier"
            and c.text(node) != variable
            and c.text(node) in c.resolver().scope
            and c.body is not None
            and _writes(c, c.text(node), c.body)
        ):
            raise refuse(
                "Переменные условия не меняются",
                f"«{c.text(node)}» меняется, лямбда её не захватит",
            )
    replacement = f"{collection}.removeIf({variable} -> {text});"
    return [TextEdit(c.file, loop.start_byte, loop.end_byte, replacement)], [
        Check("Тело цикла — условное удаление элемента", True),
        Check("Условие не меняет состояние", True),
        Check("Поведение меняется (нет ConcurrentModificationException)", True),
    ]


# --- FIX-10: switch без default и провалы ------------------------------------------------------------------------


def fix_10(c: Ctx) -> tuple[list[TextEdit], list[Check]]:
    found = statement_of(c)
    kinds = {"switch_statement", "switch_expression"}
    node = (
        found
        if found.type in kinds
        else _ancestor(found, *kinds) or next((n for n in cst.walk(found) if n.type in kinds), None)
    )
    if node is None:
        raise refuse("Оператор switch", "узел находки — не switch")
    block = node.child_by_field_name("body")
    if block is None:
        raise refuse("Тело switch", "у switch нет тела")
    groups = cst.named_children(block)
    if any(g.type == "switch_rule" for g in groups):
        raise refuse("Классический switch", "стрелочный switch не правится автоматически")
    has_default = any(
        (label.text or b"").strip().startswith(b"default")
        for group in groups
        for label in cst.children_of_type(group, "switch_label")
    )
    unit = indent_unit(c.source.decode("utf-8"))
    edits: list[TextEdit] = []
    fallthrough = 0
    for group in groups[:-1]:
        if group.type != "switch_block_statement_group":
            continue
        statements = [s for s in cst.named_children(group) if s.type != "switch_label"]
        if statements and statements[-1].type not in _TERMINAL:
            last = statements[-1]
            indent = line_indent(c.source, last.start_byte)
            edits.append(TextEdit(c.file, last.end_byte, last.end_byte, f"\n{indent}break;"))
            fallthrough += 1
    if not has_default:
        label = groups[-1] if groups else None
        case_indent = line_indent(c.source, label.start_byte) if label is not None else unit
        close = block.end_byte - 1
        at = line_start(c.source, close)
        edits.append(
            TextEdit(c.file, at, at, f"{case_indent}default:\n{case_indent}{unit}break;\n")
        )
    if not edits:
        raise refuse("Есть что исправлять", "ветка default есть и провалов нет")
    checks = [Check("Поведение может измениться: подтвердите расхождения сценариев", True)]
    if not has_default:
        checks.append(Check("Добавляется ветка default", True))
    if fallthrough:
        checks.append(
            Check(f"Добавляется break в {fallthrough} веток, которые проваливались", True)
        )
    return edits, checks


# --- FIX-03: ресурс → try-with-resources --------------------------------------------------------------------------


def fix_03(c: Ctx) -> tuple[list[TextEdit], list[Check]]:
    declaration = statement_of(c)
    if declaration.type != "local_variable_declaration":
        raise refuse("Объявление ресурса", "узел находки — не объявление переменной")
    declarators = cst.children_of_type(declaration, "variable_declarator")
    block = declaration.parent
    if len(declarators) != 1 or block is None or block.type != "block":
        raise refuse("Одно объявление в блоке", "объявление не отдельный оператор блока")
    name = c.text(declarators[0].child_by_field_name("name"))
    type_name = c.text(declaration.child_by_field_name("type"))
    siblings = cst.named_children(block)
    index = next(i for i, s in enumerate(siblings) if s.start_byte == declaration.start_byte)
    rest = siblings[index + 1 :]
    if not rest:
        raise refuse("После объявления есть код", "ресурс объявлен последним оператором блока")
    if c.body is not None and _writes(c, name, c.body):
        raise refuse("Ресурс не переприсваивается", f"«{name}» получает новое значение")
    if type_name != "Scanner":
        handled = False
        method = _ancestor(declaration, "method_declaration", "constructor_declaration")
        if method is not None and any(
            h in c.text(cst.first_child_of_type(method, "throws")) for h in _IO_HANDLERS
        ):
            handled = True
        for ancestor in _ancestors(declaration):
            if ancestor.type == "try_statement" and any(
                h in c.text(clause)
                for clause in cst.children_of_type(ancestor, "catch_clause")
                for h in _IO_HANDLERS
            ):
                handled = True
        if not handled:
            raise refuse(
                "close() может бросить IOException",
                f"{type_name}.close() бросает IOException: нужен catch или throws в методе",
            )
    unit = indent_unit(c.source.decode("utf-8"))
    indent = line_indent(c.source, declaration.start_byte)
    resource = c.text(declaration).rstrip().removesuffix(";")
    begin, end = rest[0].start_byte, rest[-1].end_byte
    lines = c.source[begin:end].decode("utf-8").split("\n")
    shifted = [lines[0]] + [(unit + line) if line.strip() else line for line in lines[1:]]
    body = f"{indent}{unit}" + "\n".join(shifted)
    text = f"try ({resource}) {{\n{body}\n{indent}}}"
    return [TextEdit(c.file, declaration.start_byte, end, text)], [
        Check("Ресурс не закрывался нигде в методе", True),
        Check("Остальные операторы блока уходят в try", True),
    ]


def _ancestors(node: Node) -> list[Node]:
    found = []
    current = node.parent
    while current is not None:
        found.append(current)
        current = current.parent
    return found


# --- CLEAN-02: неиспользуемое ------------------------------------------------------------------------------------


def clean_02(c: Ctx) -> tuple[list[TextEdit], list[Check]]:
    location = c.finding.location
    node_id = location.node_id or ""
    member = c.project.member(node_id) if node_id and "/" not in node_id else None
    if member is not None and member.kind in {"method", "field"}:
        if member.kind == "method":
            for other in c.project.members():
                if other.kind in {"method", "constructor", "initializer"} and (
                    c.project.call_counts(other).get(member.id, 0)
                ):
                    raise refuse(
                        "Метод не вызывается", f"{member.name}() вызывается из {other.name}"
                    )
        else:
            for other in c.project.members():
                if other.kind in {"method", "constructor", "initializer"} and (
                    c.project.use_counts(other)[0].get(member.id, 0)
                ):
                    raise refuse("Поле не используется", f"«{member.name}» читается в {other.name}")
            declarator = next(
                (
                    n
                    for n in cst.walk(c.parsed.root)
                    if n.type == "variable_declarator"
                    and c.text(n.child_by_field_name("name")) == member.name
                    and member.start_byte <= n.start_byte < member.end_byte
                ),
                None,
            )
            value = declarator.child_by_field_name("value") if declarator is not None else None
            if value is not None and any(
                n.type in {"method_invocation", "assignment_expression", "update_expression"}
                for n in cst.walk(value)
            ):
                raise refuse(
                    "Инициализатор без побочных эффектов", "в инициализаторе поля есть вызов"
                )
        node = _member_node(c, member.start_byte, member.end_byte)
        start, end = removal_range(c.source, node)
        return [TextEdit(c.file, start, end, "")], [
            Check("Член не используется ни в одном файле проекта", True),
            Check("Удаление не меняет поведение", True),
        ]
    # локальная переменная
    statement = statement_of(c)
    if statement.type != "local_variable_declaration":
        raise refuse("Объявление переменной", "узел находки — не объявление")
    declarators = cst.children_of_type(statement, "variable_declarator")
    if len(declarators) != 1:
        raise refuse("Одна переменная в объявлении", "в объявлении несколько переменных")
    name = c.text(declarators[0].child_by_field_name("name"))
    if c.body is not None and len(c.references(name, c.body)) > 0:
        raise refuse("Переменная не используется", f"«{name}» используется в методе")
    value = declarators[0].child_by_field_name("value")
    if value is not None and value.type in {"method_invocation", "assignment_expression"}:
        return [TextEdit(c.file, statement.start_byte, statement.end_byte, f"{c.text(value)};")], [
            Check("Значение вычисляется ради побочного эффекта и остаётся", True)
        ]
    if value is not None and any(
        n.type
        in {
            "method_invocation",
            "assignment_expression",
            "update_expression",
            "object_creation_expression",
        }
        for n in cst.walk(value)
    ):
        raise refuse(
            "Инициализатор без побочных эффектов", "инициализатор вызывает методы внутри выражения"
        )
    start, end = removal_range(c.source, statement)
    return [TextEdit(c.file, start, end, "")], [
        Check("Переменная нигде не читается", True),
        Check("Инициализатор без побочных эффектов", True),
    ]


def _member_node(c: Ctx, start: int, end: int) -> Node:
    from engine.edit.core import ts_node

    return ts_node(c.project, c.file, start, end)


# --- CLEAN-01: магическое значение → константа ------------------------------------------------------------------


def _in_label_or_annotation(node: Node) -> bool:
    parent = node.parent
    while parent is not None:
        if parent.type in {"switch_label", "annotation", "marker_annotation"}:
            return True
        if parent.type in {"block", "method_declaration"}:
            return False
        parent = parent.parent
    return False


def clean_01(c: Ctx) -> tuple[list[TextEdit], list[Check]]:
    item = next((e.text for e in c.finding.evidence.static if e.kind == "literal"), "")
    literal = item.rsplit(" × ", 1)[0].strip()
    decl_id = c.finding.location.node_id or ""
    decl = next((t for t in c.project.types() if t.id == decl_id), None)
    if not literal or decl is None:
        raise refuse("Литерал и класс найдены", "не удалось определить литерал или класс")
    occurrences: list[Node] = []
    kind = ""
    for member in decl.members:
        if member.kind not in {"method", "constructor"}:
            continue
        body = c.project.context(member)[1]
        if body is None:
            continue
        for node in cst.walk(body):
            if node.type in _NUMBER or node.type == "string_literal":
                if c.text(node) != literal or _in_label_or_annotation(node):
                    continue
                occurrences.append(node)
                kind = node.type
    if len(occurrences) < 2:
        raise refuse("Литерал повторяется", f"{literal} встречается меньше двух раз")
    value_type = literal_type(kind, literal)
    if value_type is None:
        raise refuse("Тип литерала известен", literal)
    used = {c.text(n) for n in cst.walk(c.parsed.root) if n.type == "identifier"}
    if kind == "string_literal":
        base = "TEXT"
        index = 1
        while f"{base}_{index}" in used:
            index += 1
        name = f"{base}_{index}"
    else:
        digits = re.sub(r"[^0-9A-Za-z]+", "_", literal.rstrip("lLfFdD")).strip("_").upper()
        name = f"VALUE_{digits}"
        while name in used:
            name += "_"
    node = _member_node(c, decl.start_byte, decl.end_byte)
    class_body = node.child_by_field_name("body")
    if class_body is None:
        raise refuse("Тело класса", "у класса нет тела")
    unit = indent_unit(c.source.decode("utf-8"))
    indent = line_indent(c.source, decl.start_byte) + unit
    at = class_body.start_byte + 1
    edits = [
        TextEdit(
            c.file, at, at, f"\n{indent}private static final {value_type} {name} = {literal};\n"
        )
    ]
    edits.extend(TextEdit(c.file, o.start_byte, o.end_byte, name) for o in occurrences)
    return edits, [
        Check(f"Литерал {literal} заменяется в {len(occurrences)} местах", True),
        Check(f"Константа {name} ({value_type}) не конфликтует с именами", True),
    ]


# --- CLEAN-03: переименование по соглашению ----------------------------------------------------------------------


def _words(name: str) -> list[str]:
    parts = [p for p in re.split(r"[_\s]+", name) if p]
    words: list[str] = []
    for part in parts:
        words.extend(re.findall(r"[A-Z]+(?![a-z])|[A-Z]?[a-z0-9]+", part) or [part])
    return [w.lower() for w in words]


def conventional(name: str, expected: str) -> str:
    words = _words(name)
    if not words:
        return name
    if expected == "UPPER_SNAKE_CASE":
        return "_".join(w.upper() for w in words)
    if expected == "PascalCase":
        return "".join(w.capitalize() for w in words)
    return words[0] + "".join(w.capitalize() for w in words[1:])


def clean_03(c: Ctx) -> tuple[list[TextEdit], list[Check]]:
    node_id = c.finding.location.node_id or ""
    subject = next((e.text for e in c.finding.evidence.static if e.kind == "name"), "")
    match = re.match(r"(Тип|Метод|Поле) `([^`]+)`", subject)
    if not match or not node_id:
        raise refuse("Символ найден", "не удалось определить переименовываемый символ")
    what, old = match.groups()
    if what == "Тип":
        expected = "PascalCase"
    elif what == "Метод":
        expected = "camelCase"
    else:
        member = c.project.member(node_id)
        constant = (
            member is not None and "static" in member.modifiers and "final" in member.modifiers
        )
        expected = "UPPER_SNAKE_CASE" if constant else "camelCase"
    new = conventional(old, expected)
    if new == old:
        raise refuse("Новое имя отличается", f"«{old}» нельзя привести к {expected} автоматически")
    taken = {t.name for t in c.project.types()} | {
        m.name for t in c.project.types() for m in t.members
    }
    if new in taken:
        raise refuse("Новое имя свободно", f"имя «{new}» уже занято в проекте")
    try:
        result = apply_ops(
            dict(c.project.files), [{"op": "rename_symbol", "symbol_id": node_id, "new_name": new}]
        )
    except EditError as error:
        raise refuse("Переименование возможно", error.message) from error
    edits = [
        TextEdit(path, 0, len(c.project.files[path].encode("utf-8")), result.files[path])
        for path in result.changed
    ]
    return edits, [
        Check(f"{old} → {new} ({expected})", True),
        Check(f"Обновлены все использования в {len(result.changed)} файл(ах)", True),
    ]


CLEAN_SPECS = {
    "BUG-03": (
        "FIX-03",
        "Ресурс в try-with-resources",
        "Объявление ресурса превращается в `try (…)`, остальной код блока уходит внутрь.",
        False,
        fix_03,
    ),
    "BUG-07": (
        "FIX-07",
        "Присвоить результат метода String",
        "Результат `trim`/`replace`/… присваивается обратно переменной.",
        False,
        fix_07,
    ),
    "BUG-09": (
        "FIX-09",
        "removeIf вместо удаления в цикле",
        "Условное удаление в цикле for each заменяется вызовом `removeIf`.",
        False,
        fix_09,
    ),
    "BUG-10": (
        "FIX-10",
        "Добавить default и break в switch",
        "Добавляется ветка `default` и `break` там, где ветка проваливалась.",
        False,
        fix_10,
    ),
    "MAINT-06": (
        "CLEAN-01",
        "Вынести значение в константу",
        "Повторяющийся литерал заменяется именованной `private static final` константой.",
        True,
        clean_01,
    ),
    "MAINT-07": (
        "CLEAN-02",
        "Удалить неиспользуемое",
        "Неиспользуемый приватный член или локальная переменная удаляется.",
        True,
        clean_02,
    ),
    "MAINT-10": (
        "CLEAN-03",
        "Переименовать по соглашению",
        "Символ переименовывается во всех использованиях по соглашению об именах Java.",
        True,
        clean_03,
    ),
}
