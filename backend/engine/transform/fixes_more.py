"""Следующие автоисправления (S8.3): OPT-04 (сортировка вне цикла), OPT-05 (инвариант вне цикла),
OPT-07 (повторяющееся выражение — в переменную).

Каждый построитель повторно проверяет условия правила на тексте и отказывается с понятной причиной.
Типы переменных выводятся из каталога JDK-методов; если тип неизвестен, исправление недоступно.
"""

from __future__ import annotations

import re
from typing import TYPE_CHECKING

from tree_sitter import Node

from engine.edit.core import TextEdit, line_indent, line_start
from engine.edit.operations import removal_range
from engine.java import cst
from engine.transform.base import Check, Refusal, refuse

if TYPE_CHECKING:
    from engine.transform.fixes import Ctx

_LOOPS = ("for_statement", "enhanced_for_statement", "while_statement", "do_statement")
_BLOCKS = {"block", "switch_block", "switch_block_statement_group", "constructor_body"}
_MUTATORS = frozenset(
    {
        "add",
        "addAll",
        "remove",
        "removeAll",
        "removeIf",
        "retainAll",
        "clear",
        "put",
        "putAll",
        "set",
        "sort",
        "addFirst",
        "addLast",
        "push",
        "pop",
        "poll",
        "offer",
    }
)
_STRING_RESULTS = {
    "toLowerCase": ("String", "lower"),
    "toUpperCase": ("String", "upper"),
    "trim": ("String", "trimmed"),
    "strip": ("String", "stripped"),
    "substring": ("String", "part"),
    "toCharArray": ("char[]", "letters"),
    "split": ("String[]", "parts"),
    "getBytes": ("byte[]", "bytes"),
}
_ARRAY_TYPES = {"char[]", "String[]", "byte[]"}
_STATIC_RESULTS = {
    ("Math", "pow"): ("double", "power"),
    ("Math", "sqrt"): ("double", "root"),
    ("Math", "log"): ("double", "logarithm"),
    ("Math", "log10"): ("double", "logarithm"),
    ("Math", "exp"): ("double", "exponent"),
    ("Math", "sin"): ("double", "sine"),
    ("Math", "cos"): ("double", "cosine"),
    ("Math", "tan"): ("double", "tangent"),
    ("Math", "atan2"): ("double", "angle"),
    ("Math", "hypot"): ("double", "length"),
    ("Integer", "parseInt"): ("int", "number"),
    ("Long", "parseLong"): ("long", "number"),
    ("Double", "parseDouble"): ("double", "number"),
    ("String", "format"): ("String", "formatted"),
}


def _squash(text: str) -> str:
    return re.sub(r"\s+", " ", text).strip()


def statement_of(c: Ctx) -> Node:
    """Оператор блока, на котором стоит находка: по узлу блок-схемы, иначе по первой строке находки."""
    location = c.finding.location
    if location.node_id and location.node_id != location.method_id:
        try:
            node = c.statement()
        except Refusal:
            node = None
        if node is not None and node.type not in {"method_declaration", "constructor_declaration"}:
            return node
    if c.body is not None:
        for node in cst.walk(c.body):
            parent = node.parent
            if (
                parent is not None
                and parent.type in _BLOCKS
                and c.parsed.range_of(node).start.line == location.start_line
            ):
                return node
    raise refuse("Место находки", "оператор находки не найден в методе")


def _evaluated_once(loop: Node, child: Node) -> bool:
    if loop.type == "enhanced_for_statement":
        value = loop.child_by_field_name("value")
        return value is not None and value.id == child.id
    if loop.type == "for_statement":
        return any(item.id == child.id for item in loop.children_by_field_name("init"))
    return False


def enclosing_loop(node: Node) -> Node | None:
    child, parent = node, node.parent
    while parent is not None:
        if parent.type in _LOOPS and not _evaluated_once(parent, child):
            return parent
        if parent.type in {"lambda_expression", "method_declaration", "constructor_declaration"}:
            return None
        child, parent = parent, parent.parent
    return None


def _identifiers(c: Ctx, node: Node | None) -> list[str]:
    if node is None:
        return []
    return [c.text(n) for n in cst.walk(node) if n.type == "identifier"]


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


def _mutates(c: Ctx, name: str, scope: Node, skip: Node | None = None) -> bool:
    """Переменная меняется: присваивается или у неё вызывается изменяющий метод."""
    if _writes(c, name, scope):
        return True
    for node in cst.walk(scope):
        if node.type != "method_invocation":
            continue
        if skip is not None and skip.start_byte <= node.start_byte < skip.end_byte:
            continue
        if (
            c.text(node.child_by_field_name("object")) == name
            and c.text(node.child_by_field_name("name")) in _MUTATORS
        ):
            return True
    return False


def _inputs(c: Ctx, call: Node) -> set[str]:
    """Переменные, от которых зависит вызов: получатель и аргументы (без имени метода и класса)."""
    names = set(_identifiers(c, call.child_by_field_name("arguments")))
    receiver = call.child_by_field_name("object")
    if receiver is not None and receiver.type == "identifier":
        names.add(c.text(receiver))
    names.discard("Collections")
    names.discard("Math")
    return names


def _find_calls(c: Ctx, scope: Node, text: str) -> list[Node]:
    wanted = _squash(text)
    return [
        n for n in cst.walk(scope) if n.type == "method_invocation" and _squash(c.text(n)) == wanted
    ]


def _evidence_call(c: Ctx) -> str:
    texts = [e.text for e in c.finding.evidence.static if e.kind == "call"]
    if not texts:
        raise refuse("Вызов в находке", "в находке нет вызова, который можно вынести")
    return texts[0]


def _generic_argument(type_text: str) -> str | None:
    match = re.search(r"<\s*(.+)\s*>\s*$", type_text)
    return match.group(1).strip() if match else None


def _result_type(c: Ctx, call: Node) -> tuple[str, str]:
    """Тип и подсказка для имени переменной под результат вызова."""
    name = c.text(call.child_by_field_name("name"))
    receiver = call.child_by_field_name("object")
    if receiver is None:
        raise refuse("Тип результата известен", f"у {name}() нет получателя")
    owner = c.text(receiver)
    if (owner, name) in _STATIC_RESULTS:
        return _STATIC_RESULTS[(owner, name)]
    if owner == "Collections" and name in {"max", "min"}:
        arguments = call.child_by_field_name("arguments")
        items = cst.named_children(arguments) if arguments is not None else []
        if items and items[0].type == "identifier":
            declared = c.resolver().scope.get(c.text(items[0]), "")
            element = _generic_argument(declared)
            if element and "?" not in element:
                return element, "largest" if name == "max" else "smallest"
        raise refuse("Тип результата известен", "не удалось определить тип элементов коллекции")
    if (
        name in _STRING_RESULTS
        and receiver.type == "identifier"
        and c.resolver().scope.get(owner, "").split("<")[0] == "String"
    ):
        return _STRING_RESULTS[name]
    raise refuse("Тип результата известен", f"тип результата {owner}.{name}() не определён")


def _own_line(c: Ctx, node: Node) -> bool:
    return not c.source[line_start(c.source, node.start_byte) : node.start_byte].strip()


def _fresh(c: Ctx, wanted: str, extra: set[str] | None = None) -> str:
    used = {c.text(n) for n in cst.walk(c.body)} if c.body is not None else set()
    used |= extra or set()
    name, index = wanted, 2
    while name in used:
        name = f"{wanted}{index}"
        index += 1
    return name


# --- OPT-05: инвариант — в переменную перед циклом ----------------------------------------------------------


def opt_05(c: Ctx) -> tuple[list[TextEdit], list[Check]]:
    statement = statement_of(c)
    text = _evidence_call(c)
    matches = _find_calls(c, statement, text)
    if not matches:
        raise refuse("Вызов найден", f"«{text}» не найден в операторе находки")
    call = matches[0]
    loop = enclosing_loop(call)
    if loop is None:
        raise refuse("Цикл", "вызов не лежит в цикле")
    block = loop.parent
    if block is None or block.type != "block" or not _own_line(c, loop):
        raise refuse("Цикл в блоке на своей строке", "цикл не стоит отдельным оператором блока")
    receiver = call.child_by_field_name("object")
    names = _inputs(c, call)
    loop_variable = (
        c.text(loop.child_by_field_name("name")) if loop.type == "enhanced_for_statement" else ""
    )
    scope = c.resolver().scope
    for name in names:
        declared_inside = any(
            n.type == "variable_declarator" and c.text(n.child_by_field_name("name")) == name
            for n in cst.walk(loop)
        )
        if name not in scope or declared_inside or name == loop_variable or _mutates(c, name, loop):
            raise refuse("Аргументы вызова не меняются в цикле", f"«{name}» меняется в цикле")
    type_text, hint = _result_type(c, call)
    occurrences = _find_calls(c, loop, text)
    if type_text in _ARRAY_TYPES:
        for item in occurrences:
            parent = item.parent
            read_only = parent is not None and (
                (parent.type == "field_access" and parent.child_by_field_name("object") == item)
                or (parent.type == "array_access" and parent.child_by_field_name("array") == item)
                or (
                    parent.type == "enhanced_for_statement"
                    and parent.child_by_field_name("value") == item
                )
            )
            if not read_only:
                raise refuse(
                    "Массив только читается",
                    f"результат `{text}` — массив, и его могут менять: общий экземпляр небезопасен",
                )
    variable = _fresh(c, hint)
    base = line_indent(c.source, loop.start_byte)
    at = line_start(c.source, loop.start_byte)
    edits = [TextEdit(c.file, at, at, f"{base}{type_text} {variable} = {text};\n")]
    edits.extend(TextEdit(c.file, o.start_byte, o.end_byte, variable) for o in occurrences)
    assert receiver is not None
    return edits, [
        Check("Вызов не зависит от переменных цикла", True),
        Check("Получатель и аргументы не меняются в цикле", True),
        Check(f"Тип результата известен: {type_text}", True),
    ]


# --- OPT-04: сортировка — перед циклом ------------------------------------------------------------------------


def opt_04(c: Ctx) -> tuple[list[TextEdit], list[Check]]:
    statement = statement_of(c)
    calls = [
        n
        for n in cst.walk(statement)
        if n.type == "method_invocation" and c.text(n.child_by_field_name("name")) == "sort"
    ]
    if not calls:
        raise refuse("Сортировка", "в операторе нет вызова sort")
    call = calls[0]
    receiver = call.child_by_field_name("object")
    arguments = call.child_by_field_name("arguments")
    items = cst.named_children(arguments) if arguments is not None else []
    if receiver is not None and c.text(receiver) == "Collections" and items:
        target = c.text(items[0])
    elif receiver is not None and receiver.type == "identifier":
        target = c.text(receiver)
    else:
        raise refuse("Сортируется переменная", "цель сортировки — не простая переменная")
    if not target.isidentifier():
        raise refuse("Сортируется переменная", f"«{target}» не переменная")
    loop = enclosing_loop(statement)
    if loop is None:
        raise refuse("Цикл", "сортировка не лежит в цикле")
    body = loop.child_by_field_name("body")
    if body is None or statement.parent is None or statement.parent.id != body.id:
        raise refuse(
            "Сортировка прямо в теле цикла", "сортировка вложена в условие или другой блок"
        )
    block = loop.parent
    if block is None or block.type != "block" or not _own_line(c, loop):
        raise refuse("Цикл в блоке на своей строке", "цикл не стоит отдельным оператором блока")
    if (
        loop.type == "enhanced_for_statement"
        and c.text(loop.child_by_field_name("value")) == target
    ):
        raise refuse("Цикл не идёт по сортируемой коллекции", f"цикл перебирает {target}")
    scope = c.resolver().scope
    if target not in scope:
        raise refuse("Коллекция — локальная переменная", f"«{target}» не локальная переменная")
    loop_variable = (
        c.text(loop.child_by_field_name("name")) if loop.type == "enhanced_for_statement" else ""
    )
    for name in {n for n in _identifiers(c, statement) if n != "Collections"}:
        declared_inside = any(
            n.type == "variable_declarator" and c.text(n.child_by_field_name("name")) == name
            for n in cst.walk(loop)
        )
        if name in scope and (declared_inside or name == loop_variable or _writes(c, name, loop)):
            raise refuse("Аргументы сортировки не меняются в цикле", f"«{name}» зависит от цикла")
    if _mutates(c, target, loop, skip=statement):
        raise refuse("Коллекция не меняется в цикле", f"{target} изменяется в теле цикла")
    for ref in c.references(target, loop):
        if ref.start_byte < statement.start_byte:
            raise refuse(
                "Коллекция не читается до сортировки", f"{target} используется в цикле до sort"
            )
    for node in cst.walk(body):
        if node.start_byte >= statement.start_byte:
            break
        if node.type in {"break_statement", "continue_statement", "return_statement"}:
            raise refuse(
                "Сортировка выполняется всегда", "до неё в теле цикла есть выход из итерации"
            )
    assert c.body is not None
    if any(ref.start_byte >= loop.end_byte for ref in c.references(target, c.body)):
        raise refuse(
            "Коллекция не используется после цикла",
            f"{target} читается после цикла: при пустом цикле порядок изменился бы",
        )
    removal = removal_range(c.source, statement)
    base = line_indent(c.source, loop.start_byte)
    at = line_start(c.source, loop.start_byte)
    edits = [
        TextEdit(c.file, at, at, f"{base}{c.text(statement)}\n"),
        TextEdit(c.file, removal[0], removal[1], ""),
    ]
    return edits, [
        Check("Сортировка стоит прямо в теле цикла", True),
        Check("Коллекция не меняется и не читается до сортировки", True),
        Check("Коллекция не используется после цикла", True),
    ]


# --- OPT-07: повторяющееся выражение — в переменную -------------------------------------------------------------


def _unconditional(node: Node, anchor: Node) -> bool:
    """Между узлом и оператором нет условного вычисления (``?:``, ``&&``, ``||``, лямбда)."""
    current = node
    while current.id != anchor.id and current.parent is not None:
        parent = current.parent
        if parent.type == "lambda_expression":
            return False
        if (
            parent.type == "ternary_expression"
            and parent.child_by_field_name("condition") != current
        ):
            return False
        if parent.type == "binary_expression" and parent.child_by_field_name("right") == current:
            operator = parent.child_by_field_name("operator")
            if operator is not None and operator.type in {"&&", "||"}:
                return False
        current = parent
    return True


def _climb(node: Node) -> Node:
    current = node
    while current.parent is not None and current.parent.type not in _BLOCKS:
        current = current.parent
    return current


def opt_07(c: Ctx) -> tuple[list[TextEdit], list[Check]]:
    assert c.body is not None
    text = _evidence_call(c)
    found = _find_calls(c, c.body, text)
    if len(found) < 2:
        raise refuse("Повтор найден", f"«{text}» встречается меньше двух раз")
    first = found[0]
    if c.text(first.child_by_field_name("object")) == "Collections":
        raise refuse(
            "Выражение чистое", "результат зависит от содержимого коллекции, которая может меняться"
        )
    for name in _inputs(c, first):
        if _writes(c, name, c.body):
            raise refuse("Аргументы не меняются", f"«{name}» изменяется в методе")
        if any(
            c.text(n.child_by_field_name("object")) == name
            and c.text(n.child_by_field_name("name")) in _MUTATORS
            for n in cst.walk(c.body)
            if n.type == "method_invocation"
        ):
            raise refuse("Аргументы не меняются", f"«{name}» изменяется в методе")
    anchor = _climb(first)
    block = anchor.parent
    if block is None or block.type != "block" or not _own_line(c, anchor):
        raise refuse("Оператор в блоке на своей строке", "место вставки переменной не найдено")
    if not _unconditional(first, anchor):
        raise refuse(
            "Первое вычисление безусловное", "первое вхождение лежит в условной части выражения"
        )
    replace = [
        o
        for o in found
        if o.start_byte == first.start_byte
        or (
            o.start_byte > first.end_byte
            and block.start_byte <= o.start_byte
            and o.end_byte <= block.end_byte
        )
    ]
    if len(replace) < 2:
        raise refuse("Повторы лежат в одном блоке", "второе вхождение не достигается после первого")
    type_text, hint = _result_type(c, first)
    variable = _fresh(c, hint)
    base = line_indent(c.source, anchor.start_byte)
    at = line_start(c.source, anchor.start_byte)
    edits = [TextEdit(c.file, at, at, f"{base}{type_text} {variable} = {text};\n")]
    edits.extend(TextEdit(c.file, o.start_byte, o.end_byte, variable) for o in replace)
    return edits, [
        Check("Выражение чистое, аргументы не меняются", True),
        Check(f"Все повторы достижимы после первого вычисления ({len(replace)})", True),
        Check(f"Тип результата известен: {type_text}", True),
    ]


MORE_SPECS = {
    "PERF-04": (
        "OPT-04",
        "Сортировка вне цикла",
        "Сортировка выполняется один раз перед циклом: коллекция в цикле не меняется.",
        True,
        opt_04,
    ),
    "PERF-05": (
        "OPT-05",
        "Инвариант вне цикла",
        "Неизменный вызов вычисляется один раз перед циклом и сохраняется в переменную.",
        True,
        opt_05,
    ),
    "PERF-07": (
        "OPT-07",
        "Повторяющееся выражение — в переменную",
        "Повторяющееся чистое выражение вычисляется один раз и сохраняется в переменную.",
        True,
        opt_07,
    ),
}
