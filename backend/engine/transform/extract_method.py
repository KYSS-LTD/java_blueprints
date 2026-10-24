"""Выделение метода (S9.2, REF-01, docs/06 § 5): фрагмент → новый метод и вызов на его месте."""

from __future__ import annotations

import re
from dataclasses import dataclass, field
from typing import Any

from tree_sitter import Node

from engine.edit.core import (
    EditError,
    TextEdit,
    check_fragment,
    indent_unit,
    line_indent,
    node_span,
    not_found,
    offset_of,
    precondition,
    ts_node,
)
from engine.edit.operations import _with_leading_comments
from engine.java import cst
from engine.java.project import FLOWABLE, JavaProject
from engine.java.symbols import Member
from engine.transform.base import Check
from engine.transform.dataflow import Flow, Variable, analyze

NEWLINE = "\n"

_VERBS = {
    "println": "print",
    "print": "print",
    "printf": "print",
    "format": "format",
    "add": "add",
    "remove": "remove",
    "put": "store",
    "get": "read",
    "set": "update",
    "append": "build",
    "sort": "sort",
    "contains": "check",
    "equals": "compare",
    "isEmpty": "check",
    "size": "count",
    "nextLine": "read",
    "nextInt": "read",
    "next": "read",
    "readLine": "read",
    "parseInt": "parse",
    "parseDouble": "parse",
    "split": "parse",
    "trim": "parse",
    "substring": "parse",
}
_KNOWN_VERBS = (
    "print",
    "read",
    "parse",
    "check",
    "validate",
    "compute",
    "calculate",
    "build",
    "format",
    "load",
    "write",
    "find",
    "count",
    "update",
    "apply",
    "process",
    "add",
    "remove",
    "sort",
    "merge",
    "report",
    "create",
    "init",
    "handle",
    "convert",
    "collect",
    "fill",
)


@dataclass(slots=True)
class ExtractPlan:
    name: str
    signature: str
    code: str
    edits: list[TextEdit]
    flow: Flow
    checks: list[Check] = field(default_factory=list)
    warnings: list[str] = field(default_factory=list)
    member_id: str = ""

    @property
    def available(self) -> bool:
        return bool(self.edits) and all(c.passed for c in self.checks)


def _capitalize(word: str) -> str:
    return word[:1].upper() + word[1:]


def suggest_name(project: JavaProject, member: Member, nodes: list[Node], flow: Flow) -> str:
    """Глагол + объект из первых выражений фрагмента (docs/06 § 5.1, шаг 6)."""
    source = project.files[project.file_of(member)].encode("utf-8")
    verb, noun = "", ""
    for node in nodes:
        for item in cst.walk(node):
            if item.type == "method_invocation":
                name_node = item.child_by_field_name("name")
                if name_node is None:
                    continue
                called = source[name_node.start_byte : name_node.end_byte].decode()
                head = re.match(r"[a-z]+", called)
                word = head.group(0) if head else called
                verb = verb or _VERBS.get(called) or (word if word in _KNOWN_VERBS else "")
                rest = called[len(word) :] if head else ""
                noun = noun or rest
            if verb and noun:
                break
        if verb:
            break
    if not noun:
        names = [v.name for v in flow.outputs] or [v.name for v in flow.parameters]
        noun = _capitalize(names[0]) if names else ""
    name = (verb or "process") + _capitalize(noun)
    return name if name.isidentifier() else "extractedBlock"


def _unique_name(project: JavaProject, member: Member, wanted: str, arity: int) -> str:
    owner = next(t for t in project.types() if member in t.members)
    taken = {m.name for m in owner.members if m.kind == "method"}
    name, index = wanted, 2
    while name in taken:
        name = f"{wanted}{index}"
        index += 1
    return name


def select(
    project: JavaProject,
    node_ids: list[str] | None,
    range_: dict[str, Any] | None,
) -> tuple[Member, Node, int, int]:
    """Метод, блок и индексы первого и последнего оператора фрагмента."""
    if node_ids:
        spans = [node_span(project, i) for i in node_ids]
        file = spans[0][0]
        if any(s[0] != file for s in spans):
            raise precondition("выбранные блоки лежат в разных файлах")
        nodes = [ts_node(project, f, s, e) for f, s, e in spans]
    elif range_:
        file = range_["file"]
        if file not in project.files:
            raise not_found(f"файл {file} не найден")
        low = offset_of(project, file, range_["start_line"], range_["start_col"])
        high = offset_of(project, file, range_["end_line"], range_["end_col"])
        nodes = _statements_in(project, file, low, high)
    else:
        raise precondition("укажите node_ids или range")
    if not nodes:
        raise precondition("во фрагмент не попал ни один оператор")
    parent = nodes[0].parent
    if (
        parent is None
        or parent.type != "block"
        or any(n.parent is None or n.parent.id != parent.id for n in nodes)
    ):
        raise precondition("выделять можно только подряд идущие операторы одного блока")
    statements = cst.named_children(parent)
    positions = sorted(
        next(i for i, s in enumerate(statements) if s.start_byte == n.start_byte) for n in nodes
    )
    if positions != list(range(positions[0], positions[0] + len(positions))):
        raise precondition("операторы должны идти подряд")
    owner = next(
        (
            m
            for m in project.members()
            if m.kind in FLOWABLE
            and project.file_of(m) == file
            and m.start_byte <= parent.start_byte
            and parent.end_byte <= m.end_byte
        ),
        None,
    )
    if owner is None or owner.kind == "initializer":
        raise precondition("фрагмент не лежит в методе или конструкторе")
    return owner, parent, positions[0], positions[-1]


def _statements_in(project: JavaProject, file: str, low: int, high: int) -> list[Node]:
    best: list[Node] = []
    root = project.parsed(file).root
    for block in (n for n in cst.walk(root) if n.type == "block"):
        inside = [
            s for s in cst.named_children(block) if low <= s.start_byte and s.end_byte <= high
        ]
        if len(inside) > len(best) or (
            len(inside) == len(best)
            and inside
            and best
            and inside[0].start_byte > best[0].start_byte
        ):
            best = inside
    return best


def _params(flow: Flow) -> str:
    return ", ".join(f"{v.type_text} {v.name}" for v in flow.parameters)


def plan(
    project: JavaProject,
    node_ids: list[str] | None = None,
    range_: dict[str, Any] | None = None,
    name: str | None = None,
) -> ExtractPlan:
    member, block, first, last = select(project, node_ids, range_)
    flow = analyze(project, member, block, first, last)
    file = project.file_of(member)
    source = project.files[file].encode("utf-8")
    statements = cst.named_children(block)
    region = statements[first : last + 1]
    checks: list[Check] = [Check(p, False, p) for p in flow.problems]
    if not checks:
        checks = [
            Check("Фрагмент — подряд идущие операторы одного блока", True),
            Check("Нет выхода за границу (`break`/`continue`/`return`/`yield`)", True),
            Check("Типы параметров и результата известны", True),
        ]
    wanted = name.strip() if name else suggest_name(project, member, region, flow)
    try:
        wanted = check_fragment("identifier", wanted, "name")
    except EditError as error:
        checks.append(Check("Имя метода — идентификатор", False, error.message))
    new_name = _unique_name(project, member, wanted, len(flow.parameters))
    result = ExtractPlan(new_name, "", "", [], flow, checks, list(flow.warnings), member.id)
    if not result.available and any(not c.passed for c in checks):
        return result

    unit = indent_unit(project.files[file])
    member_node = ts_node(project, file, member.start_byte, member.end_byte)
    method_indent = line_indent(source, member_node.start_byte)
    body_indent = line_indent(source, region[0].start_byte)
    begin = _with_leading_comments(source, region[0])
    end = region[-1].end_byte
    text = body_indent + source[begin:end].decode("utf-8")
    inner = method_indent + unit
    lines = [
        (
            inner + line[len(body_indent) :]
            if line.startswith(body_indent)
            else inner + line.lstrip()
        )
        if line.strip()
        else ""
        for line in text.split(NEWLINE)
    ]
    body = NEWLINE.join(lines)

    modifiers = "private" + ("" if flow.uses_this else " static")
    type_params = f"<{', '.join(flow.type_params)}> " if flow.type_params else ""
    throws = f" throws {', '.join(flow.throws)}" if flow.throws else ""
    record_text = ""
    outputs = flow.outputs
    if flow.tail_return:
        returns = member.type_text or "void"
        call = f"return {new_name}({', '.join(v.name for v in flow.parameters)});"
    elif not outputs:
        returns, call = "void", f"{new_name}({', '.join(v.name for v in flow.parameters)});"
    elif len(outputs) == 1:
        out = outputs[0]
        returns = out.type_text
        args = ", ".join(v.name for v in flow.parameters)
        call = (
            f"{returns} {out.name} = {new_name}({args});"
            if out.name in flow.declared_inside
            else f"{out.name} = {new_name}({args});"
        )
        body += f"\n{inner}return {out.name};"
    else:
        record_name = _capitalize(new_name) + "Result"
        returns = record_name
        components = ", ".join(f"{v.type_text} {v.name}" for v in outputs)
        record_text = (
            f"{method_indent}private record {record_name}({components}) {{\n{method_indent}}}"
        )
        body += f"\n{inner}return new {record_name}({', '.join(v.name for v in outputs)});"
        variable = _fresh(project, member, "result")
        args = ", ".join(v.name for v in flow.parameters)
        unpack = [
            (
                f"{v.type_text} {v.name} = {variable}.{v.name}();"
                if v.name in flow.declared_inside
                else f"{v.name} = {variable}.{v.name}();"
            )
            for v in outputs
        ]
        call = "\n".join(
            [
                f"{record_name} {variable} = {new_name}({args});",
                *[body_indent + line for line in unpack],
            ]
        )
    signature = f"{new_name}({_params(flow)})"
    header = f"{method_indent}{modifiers} {type_params}{returns} {signature}{throws} {{"
    code = f"{header}\n{body}\n{method_indent}}}"
    insertion = "\n\n" + code + (("\n\n" + record_text) if record_text else "")
    replace_from = begin
    result.edits = [
        TextEdit(file, replace_from, end, call),
        TextEdit(file, member.end_byte, member.end_byte, insertion),
    ]
    result.signature = f"{returns} {signature}{throws}"
    result.code = code + (("\n\n" + record_text) if record_text else "")
    if len(outputs) > 1:
        result.warnings.append(
            f"несколько результатов ({len(outputs)}): создаётся вспомогательный record {returns}"
        )
    if flow.throws:
        result.warnings.append(
            f"проверяемые исключения вынесены в throws: {', '.join(flow.throws)}"
        )
    return result


def line_start_of(data: bytes, offset: int) -> int:
    return data.rfind(b"\n", 0, offset) + 1


def _fresh(project: JavaProject, member: Member, wanted: str) -> str:
    file = project.file_of(member)
    used = set(re.findall(r"\b\w+\b", project.files[file][member.start_byte : member.end_byte]))
    name, index = wanted, 2
    while name in used:
        name = f"{wanted}{index}"
        index += 1
    return name


__all__ = ["ExtractPlan", "Variable", "plan", "select", "suggest_name"]
