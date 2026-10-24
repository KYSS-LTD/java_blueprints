"""Поток данных фрагмента для выделения метода (S9.1, docs/06 § 5.1).

Фрагмент — подряд идущие операторы одного блока. Определяется: какие локальные переменные и параметры
он читает из окружения (будут параметрами нового метода), какие меняет или объявляет и используются
дальше (возвращаемые значения), какие проверяемые исключения не ловятся внутри, нужен ли ``this`` и
не выходят ли ``break``/``continue``/``return`` за границу фрагмента.
"""

from __future__ import annotations

import re
from dataclasses import dataclass, field

from tree_sitter import Node

from engine.java import cst
from engine.java.project import JavaProject
from engine.java.semantic import Context
from engine.java.symbols import Member, base_name

_SCOPE_TYPES = {
    "block",
    "for_statement",
    "enhanced_for_statement",
    "switch_block_statement_group",
    "switch_rule",
    "catch_clause",
    "try_with_resources_statement",
    "try_statement",
    "lambda_expression",
    "constructor_body",
}
_LOOPS = {"for_statement", "enhanced_for_statement", "while_statement", "do_statement"}
_UNCHECKED_BASES = frozenset(
    {
        "RuntimeException",
        "Error",
        "IllegalArgumentException",
        "IllegalStateException",
        "NullPointerException",
        "ArithmeticException",
        "IndexOutOfBoundsException",
        "ArrayIndexOutOfBoundsException",
        "StringIndexOutOfBoundsException",
        "NumberFormatException",
        "UnsupportedOperationException",
        "ClassCastException",
        "ConcurrentModificationException",
        "NoSuchElementException",
        "InputMismatchException",
        "ArrayStoreException",
        "NegativeArraySizeException",
        "DateTimeException",
        "UncheckedIOException",
        "AssertionError",
        "StackOverflowError",
        "OutOfMemoryError",
    }
)


@dataclass(frozen=True, slots=True)
class Declaration:
    name: str
    type_text: str
    start: int
    scope_end: int
    kind: str  # param | local | loop | catch | lambda | resource


@dataclass(slots=True)
class Variable:
    name: str
    type_text: str


@dataclass(slots=True)
class Flow:
    """Результат анализа фрагмента."""

    parameters: list[Variable] = field(default_factory=list)
    outputs: list[Variable] = field(default_factory=list)
    declared_inside: set[str] = field(default_factory=set)  # выходные, объявленные внутри фрагмента
    throws: list[str] = field(default_factory=list)
    uses_this: bool = False
    tail_return: bool = False  # последний оператор — ``return expr;``
    return_type: str | None = None
    problems: list[str] = field(default_factory=list)  # причины отказа
    warnings: list[str] = field(default_factory=list)
    type_params: list[str] = field(default_factory=list)


def _text(source: bytes, node: Node | None) -> str:
    return "" if node is None else source[node.start_byte : node.end_byte].decode("utf-8")


def _scope_end(node: Node) -> int:
    parent = node.parent
    while parent is not None:
        if parent.type in _SCOPE_TYPES:
            return parent.end_byte
        parent = parent.parent
    return node.end_byte


def declarations(source: bytes, method: Node) -> list[Declaration]:
    """Все локальные переменные и параметры метода с областью видимости."""
    found: list[Declaration] = []
    params = method.child_by_field_name("parameters")
    if params is not None:
        for item in params.children:
            if item.type in {"formal_parameter", "spread_parameter"}:
                name_node = item.child_by_field_name("name") or cst.first_child_of_type(
                    item, "variable_declarator"
                )
                if name_node is not None and name_node.type == "variable_declarator":
                    name_node = name_node.child_by_field_name("name")
                type_node = item.child_by_field_name("type")
                found.append(
                    Declaration(
                        _text(source, name_node),
                        _text(source, type_node)
                        + ("..." if item.type == "spread_parameter" else ""),
                        item.start_byte,
                        method.end_byte,
                        "param",
                    )
                )
    for node in cst.walk(method):
        kind = node.type
        if kind == "local_variable_declaration":
            type_text = _text(source, node.child_by_field_name("type"))
            for declarator in cst.children_of_type(node, "variable_declarator"):
                found.append(
                    Declaration(
                        _text(source, declarator.child_by_field_name("name")),
                        type_text,
                        declarator.start_byte,
                        _scope_end(node),
                        "local",
                    )
                )
        elif kind == "enhanced_for_statement":
            found.append(
                Declaration(
                    _text(source, node.child_by_field_name("name")),
                    _text(source, node.child_by_field_name("type")),
                    node.start_byte,
                    node.end_byte,
                    "loop",
                )
            )
        elif kind == "catch_formal_parameter":
            catch_type = cst.first_child_of_type(node, "catch_type")
            found.append(
                Declaration(
                    _text(source, node.child_by_field_name("name")),
                    _text(source, catch_type),
                    node.start_byte,
                    _scope_end(node),
                    "catch",
                )
            )
        elif kind == "resource":
            found.append(
                Declaration(
                    _text(source, node.child_by_field_name("name")),
                    _text(source, node.child_by_field_name("type")),
                    node.start_byte,
                    _scope_end(node),
                    "resource",
                )
            )
        elif kind == "lambda_expression":
            holder = node.child_by_field_name("parameters")
            if holder is not None:
                names = (
                    [holder]
                    if holder.type == "identifier"
                    else [n for n in cst.walk(holder) if n.type == "identifier"]
                )
                for name_node in names:
                    found.append(
                        Declaration(
                            _text(source, name_node), "", node.start_byte, node.end_byte, "lambda"
                        )
                    )
    return found


def _resolve(decls: list[Declaration], name: str, position: int) -> Declaration | None:
    best: Declaration | None = None
    for decl in decls:
        if (
            decl.name == name
            and decl.start <= position < decl.scope_end
            and (best is None or decl.start >= best.start)
        ):
            best = decl
    return best


def _is_reference(node: Node) -> bool:
    parent = node.parent
    if parent is None or node.type != "identifier":
        return False
    if parent.type == "variable_declarator" and parent.child_by_field_name("name") == node:
        return False
    if parent.type == "method_invocation" and parent.child_by_field_name("name") == node:
        return False
    if parent.type == "field_access" and parent.child_by_field_name("field") == node:
        return False
    if parent.type in {
        "scoped_identifier",
        "formal_parameter",
        "spread_parameter",
        "catch_formal_parameter",
        "labeled_statement",
        "break_statement",
        "continue_statement",
    }:
        return False
    if parent.type == "enhanced_for_statement" and parent.child_by_field_name("name") == node:
        return False
    return not (
        parent.type == "lambda_expression" and parent.child_by_field_name("parameters") == node
    )


def _assigned_names(region: Node, source: bytes) -> set[str]:
    names: set[str] = set()
    for node in cst.walk(region):
        if node.type == "assignment_expression":
            left = node.child_by_field_name("left")
            if left is not None and left.type == "identifier":
                names.add(_text(source, left))
        elif node.type == "update_expression":
            for child in node.children:
                if child.type == "identifier":
                    names.add(_text(source, child))
    return names


def _checked(project: JavaProject, ctx: Context, name: str) -> bool:
    simple = base_name(name)
    if simple in _UNCHECKED_BASES:
        return False
    decl = ctx.table.type_named(simple, ctx.owner)
    if decl is None:
        return simple.endswith("Exception") and simple != "RuntimeException"
    seen = {decl.id}
    for parent in ctx.table.supertypes(decl):
        if parent.name in _UNCHECKED_BASES:
            return False
        seen.add(parent.id)
    return all(base_name(extended) not in _UNCHECKED_BASES for extended in decl.extends)


def analyze(
    project: JavaProject,
    member: Member,
    block: Node,
    first: int,
    last: int,
) -> Flow:
    """Поток данных фрагмента ``block.statements[first..last]`` (включительно) метода ``member``."""
    ctx, method = project.context(member)
    flow = Flow()
    if method is None:
        flow.problems.append("объявление метода не найдено")
        return flow
    file = project.file_of(member)
    source = project.files[file].encode("utf-8")
    statements = [c for c in cst.named_children(block)]
    region_nodes = statements[first : last + 1]
    start, end = region_nodes[0].start_byte, region_nodes[-1].end_byte
    decls = declarations(source, method)
    assigned = set()
    for node in region_nodes:
        assigned |= _assigned_names(node, source)

    inputs: dict[str, Declaration] = {}
    outputs: dict[str, Declaration] = {}
    for node in cst.walk(method):
        if not _is_reference(node):
            continue
        name = _text(source, node)
        decl = _resolve(decls, name, node.start_byte)
        if decl is None:
            continue
        inside = start <= node.start_byte < end
        declared_inside = start <= decl.start < end
        if inside and not declared_inside:
            inputs.setdefault(name, decl)
        if node.start_byte >= end and (
            declared_inside and decl.scope_end > end or (not declared_inside and name in assigned)
        ):
            outputs.setdefault(name, decl)
    for name in assigned:
        # переменная, объявленная снаружи и изменяемая внутри, должна прийти в метод значением
        decl = _resolve(decls, name, start)
        if decl is not None and not (start <= decl.start < end):
            inputs.setdefault(name, decl)

    def variable(decl: Declaration) -> Variable | None:
        if decl.type_text in {"", "var"}:
            flow.problems.append(
                f"тип переменной «{decl.name}» не задан явно (var): выделить нельзя"
            )
            return None
        return Variable(decl.name, decl.type_text)

    ordered_inputs = sorted(inputs.values(), key=lambda d: d.start)
    for decl in ordered_inputs:
        if decl.kind == "lambda":
            flow.problems.append(f"параметр лямбды «{decl.name}» используется во фрагменте")
            continue
        item = variable(decl)
        if item is not None:
            flow.parameters.append(item)
    for decl in sorted(outputs.values(), key=lambda d: d.start):
        item = variable(decl)
        if item is not None:
            flow.outputs.append(item)
            if start <= decl.start < end:
                flow.declared_inside.add(decl.name)

    _control_flow(flow, region_nodes, source)
    _exceptions(project, ctx, flow, region_nodes, source)
    _this_usage(project, ctx, member, flow, region_nodes, source)

    used_types = " ".join(
        [*(v.type_text for v in flow.parameters), *(v.type_text for v in flow.outputs)]
    )
    used_types += " " + (member.type_text or "")
    flow.type_params = [
        declared
        for declared in member.type_params
        if re.search(rf"\b{re.escape(declared.split()[0])}\b", used_types)
    ]
    return flow


def _control_flow(flow: Flow, region: list[Node], source: bytes) -> None:
    last = region[-1]
    flow.tail_return = last.type == "return_statement"
    returns = 0
    for node in region:
        stack = [(node, 0, 0)]  # (узел, вложенность циклов, вложенность switch)
        while stack:
            current, loops, switches = stack.pop()
            kind = current.type
            if kind == "lambda_expression" or kind == "class_body":
                continue
            if kind == "return_statement":
                returns += 1
            elif kind == "yield_statement":
                flow.problems.append("`yield` внутри фрагмента: выделить нельзя")
            elif kind in {"break_statement", "continue_statement"}:
                label = cst.first_child_of_type(current, "identifier")
                if label is not None:
                    declared = any(
                        n.type == "labeled_statement"
                        and _text(source, cst.first_child_of_type(n, "identifier"))
                        == _text(source, label)
                        for r in region
                        for n in cst.walk(r)
                    )
                    if not declared:
                        flow.problems.append(
                            "`break`/`continue` с меткой выходит за границу фрагмента"
                        )
                elif (kind == "break_statement" and loops == 0 and switches == 0) or (
                    kind == "continue_statement" and loops == 0
                ):
                    flow.problems.append("`break`/`continue` выходит за границу фрагмента")
            next_loops = loops + (1 if kind in _LOOPS else 0)
            next_switches = switches + (
                1 if kind in {"switch_statement", "switch_expression"} else 0
            )
            for child in current.children:
                stack.append((child, next_loops, next_switches))
    if returns:
        if returns == 1 and flow.tail_return:
            pass
        else:
            flow.problems.append("`return` не только в конце фрагмента: выделить нельзя")
    if flow.tail_return and flow.outputs:
        flow.problems.append(
            "фрагмент заканчивается `return` и при этом меняет переменные, нужные ниже"
        )


def _exceptions(
    project: JavaProject, ctx: Context, flow: Flow, region: list[Node], source: bytes
) -> None:
    thrown: list[str] = []

    def caught_inside(node: Node, exception: str) -> bool:
        parent = node.parent
        while parent is not None and parent.start_byte >= region[0].start_byte:
            if parent.type in {"try_statement", "try_with_resources_statement"}:
                body = parent.child_by_field_name("body")
                if body is not None and body.start_byte <= node.start_byte < body.end_byte:
                    for clause in cst.children_of_type(parent, "catch_clause"):
                        param = cst.first_child_of_type(clause, "catch_formal_parameter")
                        types = (
                            [
                                base_name(_text(source, t))
                                for t in cst.walk(param)
                                if t.type == "type_identifier"
                            ]
                            if param is not None
                            else []
                        )
                        if base_name(exception) in types or {"Exception", "Throwable"} & set(types):
                            return True
            parent = parent.parent
        return False

    for node in region:
        for item in cst.walk(node):
            if item.type == "throw_statement":
                created = cst.first_child_of_type(item, "object_creation_expression")
                if created is not None:
                    name = base_name(_text(source, created.child_by_field_name("type")))
                    if _checked(project, ctx, name) and not caught_inside(item, name):
                        thrown.append(name)
            elif item.type in {"method_invocation", "object_creation_expression"}:
                targets = (
                    ctx.resolve_call(item)
                    if item.type == "method_invocation"
                    else ctx.resolve_creation(item)
                )
                for target in targets:
                    for exception in target.throws:
                        name = base_name(exception)
                        if _checked(project, ctx, name) and not caught_inside(item, name):
                            thrown.append(name)
    flow.throws = list(dict.fromkeys(thrown))


def _this_usage(
    project: JavaProject,
    ctx: Context,
    member: Member,
    flow: Flow,
    region: list[Node],
    source: bytes,
) -> None:
    for node in region:
        for item in cst.walk(node):
            if item.type == "this":
                flow.uses_this = True
            elif item.type == "method_invocation" and item.child_by_field_name("object") is None:
                if any(not target.is_static for target in ctx.resolve_call(item)):
                    flow.uses_this = True
            elif item.type == "identifier" and _is_reference(item):
                name = _text(source, item)
                found = ctx.table.field_type(ctx.owner, name)
                if found is not None and not found[0].is_static and name not in ctx.scope:
                    flow.uses_this = True
            elif item.type == "object_creation_expression" and ctx.table.type_named(
                base_name(_text(source, item.child_by_field_name("type"))), ctx.owner
            ):
                created = ctx.table.type_named(
                    base_name(_text(source, item.child_by_field_name("type"))), ctx.owner
                )
                if (
                    created is not None
                    and created.parent_path
                    and "static" not in created.modifiers
                    and created.kind == "class"
                ):
                    flow.uses_this = True
