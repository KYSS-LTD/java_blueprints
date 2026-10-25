"""Достижимость: оператор, после которого управление дальше не идёт (``javac`` ругается на код после него).

Нужно исправлениям, которые дописывают ``break``/``continue``/``return``: лишний выход после ``return``, блока с ``return``
или ``try``, который всегда бросает исключение, — ошибка компиляции «unreachable statement». Оценка консервативная:
``True`` только если оператор точно не завершается нормально; в сомнительных случаях ``False``.
"""

from __future__ import annotations

from tree_sitter import Node

from engine.java import cst

TERMINAL = frozenset(
    {
        "break_statement",
        "return_statement",
        "throw_statement",
        "continue_statement",
        "yield_statement",
    }
)


def cannot_complete(node: Node | None) -> bool:
    """Оператор не завершается нормально: после него идёт выход, возврат, исключение или бесконечный цикл."""
    if node is None:
        return False
    kind = node.type
    if kind in TERMINAL:
        return True
    if kind == "block":
        items = cst.named_children(node)
        return bool(items) and cannot_complete(items[-1])
    if kind == "if_statement":
        other = node.child_by_field_name("alternative")
        return (
            other is not None
            and cannot_complete(node.child_by_field_name("consequence"))
            and cannot_complete(other)
        )
    if kind in {"try_statement", "try_with_resources_statement"}:
        finalizer = cst.first_child_of_type(node, "finally_clause")
        if finalizer is not None and cannot_complete(cst.first_child_of_type(finalizer, "block")):
            return True
        catches = cst.children_of_type(node, "catch_clause")
        return cannot_complete(node.child_by_field_name("body")) and all(
            cannot_complete(c.child_by_field_name("body")) for c in catches
        )
    if kind == "synchronized_statement":
        return cannot_complete(node.child_by_field_name("body"))
    if kind == "while_statement":
        condition = node.child_by_field_name("condition")
        endless = (
            condition is not None
            and condition.text is not None
            and condition.text.strip() in {b"(true)", b"( true )"}
        )
        return endless and not any(n.type == "break_statement" for n in cst.walk(node))
    return False
