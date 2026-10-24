"""Следующие автоисправления (S8.3–S8.4): OPT-04 (сортировка вне цикла), OPT-05 (инвариант вне цикла),
OPT-07 (повторяющееся выражение — в переменную), OPT-08 (мемоизация чистой рекурсии), OPT-11 (порядок
этапов Stream), OPT-12 (обёртка → примитив).

Каждый построитель повторно проверяет условия правила на тексте и отказывается с понятной причиной.
Типы переменных выводятся из каталога JDK-методов; если тип неизвестен, исправление недоступно.
"""

from __future__ import annotations

import re
from typing import TYPE_CHECKING

from tree_sitter import Node

from engine.edit.core import TextEdit, indent_unit, line_indent, line_start
from engine.edit.operations import _with_leading_comments, removal_range
from engine.java import cst
from engine.transform.base import Check, Refusal, ensure_imports, refuse

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


# --- OPT-08: мемоизация чистой рекурсии ---------------------------------------------------------------------------

_BOXED = {
    "int": "Integer",
    "long": "Long",
    "double": "Double",
    "float": "Float",
    "boolean": "Boolean",
    "char": "Character",
    "short": "Short",
    "byte": "Byte",
}
_KEY_TYPES = frozenset(
    {"int", "long", "char", "short", "byte", "boolean", "String", "Integer", "Long", "Character"}
)
_VALUE_TYPES = _KEY_TYPES | {"double", "float", "Double", "Float"}
_PURE_OWNERS = frozenset(
    {"Math", "Integer", "Long", "Double", "Character", "Boolean", "Objects", "StrictMath"}
)
_IMPURE_CALLS = frozenset({"random", "currentTimeMillis", "nanoTime"})


def _impure(c: Ctx, body: Node, name: str) -> str | None:
    """Причина, по которой тело метода нельзя считать чистой функцией аргумента, или ``None``."""
    scope = c.resolver().scope
    for node in cst.walk(body):
        kind = node.type
        if kind == "method_invocation":
            receiver = node.child_by_field_name("object")
            called = c.text(node.child_by_field_name("name"))
            if receiver is None and called == name:
                continue
            owner = c.text(receiver) if receiver is not None else ""
            if owner in _PURE_OWNERS and called not in _IMPURE_CALLS:
                continue
            if receiver is not None and c.resolver().expr_type(receiver) == "String":
                continue
            return f"вызывает {c.text(node)[:40]}"
        if kind in {"assignment_expression", "update_expression"}:
            target = (
                node.child_by_field_name("left")
                if kind == "assignment_expression"
                else next((ch for ch in node.children if ch.type == "identifier"), None)
            )
            if target is None or target.type != "identifier" or c.text(target) not in scope:
                return f"меняет не локальную переменную: {c.text(node)[:40]}"
        elif kind == "identifier":
            text = c.text(node)
            parent = node.parent
            if text == "System":
                return "обращается к System"
            if text in scope or parent is None or text[:1].isupper():
                continue
            if parent.type == "method_invocation" and parent.child_by_field_name("name") == node:
                continue
            if parent.type == "field_access" and parent.child_by_field_name("field") == node:
                continue
            found = c.project.table.field_type(c.resolver().owner, text)
            if found is not None and "final" not in found[0].modifiers:
                return f"читает изменяемое поле {text}"
    return None


def _fresh_name(wanted: str, used: set[str]) -> str:
    name, index = wanted, 2
    while name in used:
        name = f"{wanted}{index}"
        index += 1
    return name


def opt_08(c: Ctx) -> tuple[list[TextEdit], list[Check]]:
    member = c.member
    if member is None or member.kind != "method":
        raise refuse("Метод", "находка не привязана к методу")
    declaration = c.project.context(member)[1]
    body = declaration.child_by_field_name("body") if declaration is not None else None
    if declaration is None or body is None:
        raise refuse("Тело метода", "у метода нет тела")
    if len(member.params) != 1 or member.params[0].varargs:
        raise refuse("Один параметр", "мемоизируются методы ровно с одним параметром")
    key_type, value_type = member.params[0].type, member.type_text or ""
    if key_type not in _KEY_TYPES or value_type not in _VALUE_TYPES:
        raise refuse(
            "Простые типы ключа и результата",
            f"ключ {key_type} или результат {value_type or 'void'} не подходят для кэша",
        )
    if any(a.endswith("Override") for a in member.annotations) or member.type_params:
        raise refuse(
            "Не переопределяет и не обобщённый", "метод переопределяет другой или обобщённый"
        )
    reason = _impure(c, body, member.name)
    if reason is not None:
        raise refuse("Метод — чистая функция аргумента", reason)
    source_text = c.source.decode("utf-8")
    used = set(re.findall(r"\w+", source_text))
    base = member.name
    memo = _fresh_name(f"{base}Memo", used)
    compute = _fresh_name(f"{base}Compute", used | {memo})
    argument = member.params[0].name
    local = _fresh_name("cached", used | {memo, compute, argument})
    result = _fresh_name("result", used | {memo, compute, argument, local})
    unit = indent_unit(source_text)
    begin = _with_leading_comments(c.source, declaration)
    indent = line_indent(c.source, member.start_byte)
    header = c.source[member.start_byte : body.start_byte].decode("utf-8").rstrip()
    boxed_key, boxed_value = _BOXED.get(key_type, key_type), _BOXED.get(value_type, value_type)
    modifier = "private static final" if member.is_static else "private final"
    wrapper = (
        f"{modifier} Map<{boxed_key}, {boxed_value}> {memo} = new HashMap<>();\n\n"
        f"{indent}{header} {{\n"
        f"{indent}{unit}{boxed_value} {local} = {memo}.get({argument});\n"
        f"{indent}{unit}if ({local} != null) {{\n"
        f"{indent}{unit}{unit}return {local};\n"
        f"{indent}{unit}}}\n"
        f"{indent}{unit}{value_type} {result} = {compute}({argument});\n"
        f"{indent}{unit}{memo}.put({argument}, {result});\n"
        f"{indent}{unit}return {result};\n"
        f"{indent}}}\n\n{indent}"
    )
    original = bytearray(c.source[begin : member.end_byte])
    name_node = declaration.child_by_field_name("name")
    assert name_node is not None
    changes: list[tuple[int, int, str]] = [(name_node.start_byte, name_node.end_byte, compute)]
    modifiers = cst.first_child_of_type(declaration, "modifiers")
    access = (
        next((m for m in modifiers.children if m.type in {"public", "protected", "private"}), None)
        if modifiers is not None
        else None
    )
    if access is not None:
        changes.append((access.start_byte, access.end_byte, "private"))
    elif modifiers is None:
        changes.append((member.start_byte, member.start_byte, "private "))
    else:
        plain = [m for m in modifiers.children if "annotation" not in m.type]
        if plain:
            changes.append((plain[0].start_byte, plain[0].start_byte, "private "))
        else:
            changes.append((modifiers.end_byte, modifiers.end_byte, " private"))
    for start, end, text in sorted(changes, reverse=True):
        original[start - begin : end - begin] = text.encode("utf-8")
    edits = [TextEdit(c.file, begin, member.end_byte, wrapper + original.decode("utf-8"))]
    imports = ensure_imports(source_text, ["java.util.Map", "java.util.HashMap"])
    if imports is not None:
        edits.append(TextEdit(c.file, imports.start, imports.end, imports.text))
    return edits, [
        Check("Метод — чистая функция одного аргумента", True),
        Check(f"Кэш: Map<{boxed_key}, {boxed_value}>", True),
        Check("Рекурсивные вызовы идут через кэш", True),
    ]


# --- OPT-11: лишняя работа в стриме -------------------------------------------------------------------------------

_SIDE_EFFECTS = frozenset(
    {
        "println",
        "print",
        "printf",
        "add",
        "remove",
        "put",
        "set",
        "append",
        "peek",
        "clear",
        "System",
    }
)


def _ancestors(node: Node) -> list[Node]:
    found = []
    current = node.parent
    while current is not None:
        found.append(current)
        current = current.parent
    return found


def opt_11(c: Ctx) -> tuple[list[TextEdit], list[Check]]:
    statement = statement_of(c)
    calls = [n for n in cst.walk(statement) if n.type == "method_invocation"]

    def name_of(call: Node) -> str:
        return c.text(call.child_by_field_name("name"))

    def arguments(call: Node) -> str:
        holder = call.child_by_field_name("arguments")
        return c.text(holder)[1:-1] if holder is not None else ""

    # sorted → filter: меняем местами соседние этапы
    for call in calls:
        inner = call.child_by_field_name("object")
        if (
            name_of(call) == "filter"
            and inner is not None
            and inner.type == "method_invocation"
            and name_of(inner) == "sorted"
        ):
            predicate = call.child_by_field_name("arguments")
            effects = [
                n
                for n in (cst.walk(predicate) if predicate is not None else [])
                if (n.type == "method_invocation" and name_of(n) in _SIDE_EFFECTS)
                or (n.type == "identifier" and c.text(n) == "System")
            ]
            if effects:
                raise refuse(
                    "Предикат без побочных эффектов", "в предикате есть вывод или изменение"
                )
            source_call = inner.child_by_field_name("object")
            assert source_call is not None
            new = f"{c.text(source_call)}.filter({arguments(call)}).sorted({arguments(inner)})"
            return [TextEdit(c.file, call.start_byte, call.end_byte, new)], [
                Check("Этапы sorted и filter идут подряд", True),
                Check("Предикат без побочных эффектов", True),
            ]
    # collect(toList()).stream() → без промежуточной коллекции
    for call in calls:
        inner = call.child_by_field_name("object")
        if name_of(call) not in {"stream", "parallelStream"} or inner is None:
            continue
        if inner.type == "method_invocation" and (
            (name_of(inner) == "collect" and _squash(arguments(inner)) == "Collectors.toList()")
            or (name_of(inner) == "toList" and not arguments(inner))
        ):
            source_call = inner.child_by_field_name("object")
            assert source_call is not None
            return [TextEdit(c.file, source_call.end_byte, call.end_byte, "")], [
                Check("Промежуточная коллекция — обычный список", True),
                Check("Порядок элементов сохраняется", True),
            ]
    # stream().count() → size()
    for call in calls:
        inner = call.child_by_field_name("object")
        if name_of(call) != "count" or inner is None or inner.type != "method_invocation":
            continue
        if name_of(inner) != "stream":
            continue
        receiver = inner.child_by_field_name("object")
        if receiver is None or receiver.type != "identifier":
            continue
        kind = c.resolver().scope.get(c.text(receiver), "").split("<")[0]
        if kind not in {
            "List",
            "ArrayList",
            "LinkedList",
            "Set",
            "HashSet",
            "TreeSet",
            "Collection",
        }:
            raise refuse("Получатель — коллекция", f"«{c.text(receiver)}» не коллекция")
        declaration = next(
            (a for a in _ancestors(call) if a.type == "local_variable_declaration"), None
        )
        if declaration is not None and c.text(declaration.child_by_field_name("type")) == "Long":
            raise refuse("Тип приёмника совместим", "size() возвращает int, а переменная типа Long")
        return [TextEdit(c.file, inner.start_byte, call.end_byte, f"{c.text(receiver)}.size()")], [
            Check("Получатель — коллекция", True),
            Check("size() возвращает то же число", True),
        ]
    raise refuse("Лишний этап", "лишних этапов не найдено или они не соседние")


# --- OPT-12: обёртка → примитив ------------------------------------------------------------------------------------

_PRIMITIVES = {
    "Integer": "int",
    "Long": "long",
    "Double": "double",
    "Float": "float",
    "Short": "short",
    "Byte": "byte",
    "Character": "char",
}


def opt_12(c: Ctx) -> tuple[list[TextEdit], list[Check]]:
    assert c.body is not None
    texts = [e.text for e in c.finding.evidence.static if e.kind == "variable"]
    if not texts:
        raise refuse("Переменная", "в находке нет переменной")
    wrapper, _, name = texts[0].partition(" ")
    if wrapper not in _PRIMITIVES:
        raise refuse("Тип-обёртка", f"{wrapper} не обёртка примитива")
    declaration = c.declaration_of(name)
    if declaration is None:
        raise refuse("Локальная переменная", f"«{name}» не локальная переменная метода")
    declarators = cst.children_of_type(declaration, "variable_declarator")
    type_node = declaration.child_by_field_name("type")
    if len(declarators) != 1 or type_node is None or c.text(type_node) != wrapper:
        raise refuse(
            "Одна переменная в объявлении", "в объявлении несколько переменных или тип иной"
        )
    value = declarators[0].child_by_field_name("value")
    if value is not None and value.type == "null_literal":
        raise refuse("Нет null", f"«{name}» инициализируется null")
    primitives = set(_PRIMITIVES.values())
    for ref in c.references(name, c.body):
        parent = ref.parent
        if parent is None:
            continue
        if parent.type == "method_invocation" and parent.child_by_field_name("object") == ref:
            raise refuse("Нет вызовов методов у переменной", f"у «{name}» вызывается метод")
        if parent.type == "binary_expression":
            operator = c.text(parent.child_by_field_name("operator"))
            other = parent.child_by_field_name("right")
            if other is not None and other.id == ref.id:
                other = parent.child_by_field_name("left")
            if other is not None and other.type == "null_literal":
                raise refuse("Нет сравнения с null", f"«{name}» сравнивается с null")
            if operator in {"==", "!="} and other is not None:
                kind = c.resolver().expr_type(other)
                literal = other.type in {
                    "decimal_integer_literal",
                    "decimal_floating_point_literal",
                }
                if not literal and (kind is None or kind not in primitives):
                    raise refuse(
                        "Сравнение значений, а не ссылок",
                        f"«{name}» сравнивается через == с объектом: смысл сравнения изменится",
                    )
        if parent.type == "assignment_expression" and parent.child_by_field_name("left") == ref:
            right = parent.child_by_field_name("right")
            if right is not None and right.type == "null_literal":
                raise refuse("Нет присваивания null", f"«{name}» получает null")
    primitive = _PRIMITIVES[wrapper]
    return [TextEdit(c.file, type_node.start_byte, type_node.end_byte, primitive)], [
        Check("Переменная не бывает null", True),
        Check("У переменной не вызываются методы и нет сравнения ссылок", True),
        Check(f"{wrapper} → {primitive}", True),
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
    "PERF-08": (
        "OPT-08",
        "Мемоизация рекурсии",
        "Результаты чистой рекурсивной функции запоминаются в Map: повторные вызовы берут готовое значение.",
        True,
        opt_08,
    ),
    "PERF-11": (
        "OPT-11",
        "Лишний этап в стриме",
        "Этапы Stream API переставляются или убираются, результат остаётся тем же.",
        True,
        opt_11,
    ),
    "PERF-12": (
        "OPT-12",
        "Обёртка → примитив",
        "Накопитель в цикле объявляется примитивом: нет упаковки и распаковки.",
        True,
        opt_12,
    ),
    "PERF-07": (
        "OPT-07",
        "Повторяющееся выражение — в переменную",
        "Повторяющееся чистое выражение вычисляется один раз и сохраняется в переменную.",
        True,
        opt_07,
    ),
}
