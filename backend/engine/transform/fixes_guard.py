"""REF-03 (S9): ранний возврат вместо вложенности — ветка условия в конце метода или цикла превращается в защитное условие.

``for (…) { …; if (c) { A } else { B } }`` → ``for (…) { …; if (!c) { B; continue; } A }``;
в методе ``void`` то же с ``return;``. Меняется только вид записи: условие в конце тела, после него ничего не
исполняется, поэтому ``continue`` (``return``) делают то же, что и выход за конец тела. Исправление применяется к одному
оператору — самому внешнему из тех, что дают наибольшую вложенность метода; если её всё ещё много, его можно применить снова.
"""

from __future__ import annotations

from typing import TYPE_CHECKING

from tree_sitter import Node

from engine.analysis.metrics import nesting_and_cognitive
from engine.edit.core import TextEdit, indent_unit, line_indent, line_start
from engine.java import cst
from engine.transform.base import Check, refuse

if TYPE_CHECKING:
    from engine.transform.fixes import Ctx

_COMMENTS = ("line_comment", "block_comment")
_LOOPS = {"for_statement", "enhanced_for_statement", "while_statement", "do_statement"}
_NESTING = {
    "if_statement",
    "for_statement",
    "enhanced_for_statement",
    "while_statement",
    "do_statement",
    "switch_expression",
    "switch_statement",
    "try_statement",
    "try_with_resources_statement",
}
_ABRUPT = {
    "return_statement",
    "throw_statement",
    "continue_statement",
    "break_statement",
    "yield_statement",
}
_FLIPPED = {"<": ">=", "<=": ">", ">": "<=", ">=": "<"}
_INTEGRAL = {"int", "long", "short", "byte", "char"}
_SIMPLE_OPERAND = {
    "identifier",
    "method_invocation",
    "field_access",
    "array_access",
    "parenthesized_expression",
    "true",
    "false",
    "this",
}


def _statements(block: Node | None) -> list[Node]:
    if block is None:
        return []
    items = cst.named_children(block) if block.type == "block" else [block]
    return [n for n in items if n.type not in _COMMENTS]


def _depth(node: Node | None) -> int:
    """Вложенность внутри ветки: сколько уровней условий, циклов и ``try`` она сама добавляет."""
    return nesting_and_cognitive(node)[0] if node is not None else 0


def _ancestors_nesting(node: Node, stop: Node) -> int:
    count = 0
    parent = node.parent
    while parent is not None and parent.id != stop.id:
        if parent.type in _NESTING and not _is_else_if(parent):
            count += 1
        parent = parent.parent
    return count


def _is_else_if(node: Node) -> bool:
    parent = node.parent
    if node.type != "if_statement" or parent is None or parent.type != "if_statement":
        return False
    alternative = parent.child_by_field_name("alternative")
    return alternative is not None and alternative.id == node.id


def _inside_lambda_or_class(node: Node, stop: Node) -> bool:
    parent = node.parent
    while parent is not None and parent.id != stop.id:
        if parent.type in {"lambda_expression", "class_body", "object_creation_expression"}:
            return True
        parent = parent.parent
    return False


def _tail_ifs(block: Node | None, terminator: str, found: list[tuple[Node, str]]) -> None:
    """Операторы ``if`` в хвосте блока (и в хвостах их ветвей): после них управление выходит за конец тела."""
    items = _statements(block)
    if not items or items[-1].type != "if_statement":
        return
    node = items[-1]
    found.append((node, terminator))
    _tail_ifs(node.child_by_field_name("consequence"), terminator, found)
    alternative = node.child_by_field_name("alternative")
    if alternative is not None and alternative.type == "block":
        _tail_ifs(alternative, terminator, found)


def _candidates(c: Ctx, body: Node, returns_value: bool) -> list[tuple[Node, str]]:
    found: list[tuple[Node, str]] = []
    if not returns_value:
        _tail_ifs(body, "return;", found)
    for node in cst.walk(body):
        if node.type in _LOOPS and not _inside_lambda_or_class(node, body):
            _tail_ifs(node.child_by_field_name("body"), "continue;", found)
    unique: dict[int, tuple[Node, str]] = {}
    for node, terminator in found:
        unique.setdefault(node.id, (node, terminator))
    return list(unique.values())


def _negate(c: Ctx, condition: Node) -> str:
    inner = (
        cst.named_children(condition)[0]
        if condition.type == "parenthesized_expression"
        else condition
    )
    text = c.text(inner)
    if inner.type == "unary_expression" and c.text(inner.child_by_field_name("operator")) == "!":
        operand = inner.child_by_field_name("operand")
        if operand is not None:
            shown = (
                cst.named_children(operand)[0]
                if operand.type == "parenthesized_expression"
                else operand
            )
            return c.text(shown)
    if inner.type == "binary_expression" and "\n" not in text:
        operator = c.text(inner.child_by_field_name("operator"))
        left, right = inner.child_by_field_name("left"), inner.child_by_field_name("right")
        if operator in {"==", "!="}:
            return f"{c.text(left)} {'!=' if operator == '==' else '=='} {c.text(right)}"
        if operator in _FLIPPED and left is not None and right is not None:
            # у целых чисел `!(a < b)` — это `a >= b`; у вещественных нет: NaN не меньше, не больше и не равно ничему
            kinds = {c.resolver().expr_type(left), c.resolver().expr_type(right)}
            if kinds <= _INTEGRAL:
                return f"{c.text(left)} {_FLIPPED[operator]} {c.text(right)}"
    if inner.type in _SIMPLE_OPERAND:
        return f"!{text}"
    return f"!({text})"


def _branch_text(c: Ctx, branch: Node, base: str, unit: str) -> str:
    """Операторы ветки на уровне ``base`` (ветка — блок или одиночный оператор)."""
    if branch.type != "block":
        return base + c.text(branch)
    inside = [n for n in branch.children if n.is_named]  # операторы и комментарии блока по порядку
    if not inside:
        return ""
    data = c.source
    first, last = inside[0], inside[-1]
    # комментарий или оператор на строке с `{`: берём с его позиции, а не с начала строки с самим условием
    same_line = b"\n" not in data[branch.start_byte + 1 : first.start_byte]
    begin = first.start_byte if same_line else line_start(data, first.start_byte)
    lines = data[begin : last.end_byte].decode("utf-8").split("\n")
    if same_line:  # отступ тела — по следующим строкам, первая строка стоит после `{`
        rest = [line for line in lines[1:] if line.strip()]
        current = min(
            (line[: len(line) - len(line.lstrip())] for line in rest), key=len, default=""
        )
    else:
        current = line_indent(data, first.start_byte)
    shifted = []
    for index, line in enumerate(lines):
        if not line.strip():
            shifted.append("")
        elif index == 0 and same_line:
            shifted.append(base + line)
        elif line.startswith(current):
            shifted.append(base + line[len(current) :])
        else:
            shifted.append(base + line.lstrip())
    del unit
    return "\n".join(shifted)


def _ends_abruptly(branch: Node) -> bool:
    items = _statements(branch)
    return bool(items) and items[-1].type in _ABRUPT


def ref_03(c: Ctx) -> tuple[list[TextEdit], list[Check]]:
    member = c.member
    if member is None or c.body is None:
        raise refuse("Метод", "находка не привязана к методу")
    returns_value = member.kind == "method" and (member.type_text or "void") != "void"
    candidates = _candidates(c, c.body, returns_value)
    if not candidates:
        raise refuse(
            "Условие в конце метода или цикла",
            "нет оператора if в конце тела цикла или метода void: ранний возврат здесь не равнозначен",
        )
    deepest = nesting_and_cognitive(c.body)[0]

    body = c.body

    def total(item: tuple[Node, str]) -> int:
        """Вложенность, которую даёт самая глубокая ветка этого условия внутри метода."""
        node, _ = item
        branches = [
            node.child_by_field_name("consequence"),
            node.child_by_field_name("alternative"),
        ]
        return _ancestors_nesting(node, body) + 1 + max(_depth(b) for b in branches)

    best = [item for item in candidates if total(item) == deepest]
    if not best:
        raise refuse(
            "Условие на пути к самой глубокой вложенности",
            "самое вложенное место не стоит в конце цикла или метода",
        )
    node, terminator = min(
        best, key=lambda item: (_ancestors_nesting(item[0], body), item[0].start_byte)
    )

    condition = node.child_by_field_name("condition")
    then = node.child_by_field_name("consequence")
    other = node.child_by_field_name("alternative")
    assert condition is not None and then is not None
    if other is not None and other.type == "if_statement":
        raise refuse("Ветка else — блок", "после else идёт ещё одно условие (else if)")
    unit = indent_unit(c.source.decode("utf-8"))
    base = line_indent(c.source, node.start_byte)
    inner = base + unit

    guard: Node | None
    main: Node | None
    keep_then = other is None or _depth(then) >= _depth(other)
    if keep_then:
        guard_condition = _negate(c, condition)
        guard, main = other, then
    else:
        shown = (
            cst.named_children(condition)[0]
            if condition.type == "parenthesized_expression"
            else condition
        )
        guard_condition = c.text(shown)
        guard, main = then, other
    assert main is not None
    guard_text = _branch_text(c, guard, inner, unit) if guard is not None else ""
    lines = [guard_text] if guard_text else []
    if guard is None or not _ends_abruptly(guard):
        lines.append(f"{inner}{terminator}")
    body_text = _branch_text(c, main, base, unit)
    if not body_text.strip():
        raise refuse(
            "В основной ветке есть операторы", "после преобразования ветка осталась бы пустой"
        )
    text = f"if ({guard_condition}) {{\n" + "\n".join(lines) + f"\n{base}}}\n{body_text}"
    before = nesting_and_cognitive(c.body)[0]
    edits = [TextEdit(c.file, node.start_byte, node.end_byte, text)]
    return edits, [
        Check(
            "Условие стоит в конце тела цикла или метода void: после него ничего не исполняется",
            True,
        ),
        Check(f"Выход `{terminator[:-1]}` равнозначен выходу за конец тела", True),
        Check(f"Вложенность основной ветки уменьшается на 1 (было {before})", True),
    ]


GUARD_SPECS = {
    "MAINT-03": (
        "REF-03",
        "Ранний возврат вместо вложенности",
        "Ветка условия в конце цикла или метода void выносится в защитное условие с `continue` или `return`: основной код сдвигается на уровень влево.",
        True,
        ref_03,
    )
}
