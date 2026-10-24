"""Операции над графом → правки текста (S5.3–S5.5, S5.7, docs/04 § 5.2).

Каждая операция строит ``TextEdit`` по CST и применяется через ``Editor.apply``: после правки файл
перепарсивается, при появлении ``ERROR``/``MISSING`` правка отклоняется. Остальной текст не меняется.
"""

from __future__ import annotations

import re
from collections.abc import Callable
from dataclasses import dataclass, field
from typing import Any

from tree_sitter import Node

from engine.edit import templates
from engine.edit.core import (
    EditError,
    TextEdit,
    apply_text_edits,
    check_fragment,
    check_syntax,
    indent_unit,
    indented,
    invalid,
    line_indent,
    line_start,
    node_span,
    not_found,
    offset_of,
    precondition,
    reparse,
    ts_node,
    unsupported,
)
from engine.graph.ids import split_node_id
from engine.graph.model import FlowGraph, FlowNode
from engine.java import cst
from engine.java.project import JavaProject

_COMMENTS = ("line_comment", "block_comment")
_LOOP_NODES = ("for_statement", "enhanced_for_statement", "while_statement", "do_statement")
_BODY_OWNERS = (*_LOOP_NODES, "synchronized_statement", "labeled_statement")


@dataclass(slots=True)
class EditResult:
    files: dict[str, str]
    changed: list[str]
    focus_node_id: str | None
    id_map: dict[str, str]
    methods: list[str] = field(default_factory=list)  # методы, чьи графы изменились


class Editor:
    """Рабочее состояние пакета операций: файлы после уже применённых операций."""

    def __init__(self, files: dict[str, str]) -> None:
        self.files = dict(files)
        self.original = dict(files)
        self.changed: set[str] = set()
        self.focus: tuple[str, int] | None = None
        self.touched: set[str] = set()  # методы, затронутые правками

    @property
    def project(self) -> JavaProject:
        return reparse(self.files)

    def unit(self, file: str) -> str:
        return indent_unit(self.files[file])

    def bytes_of(self, file: str) -> bytes:
        return self.files[file].encode("utf-8")

    def apply(self, edits: list[TextEdit], focus: tuple[str, int] | None = None) -> None:
        before = self.project
        updated = apply_text_edits(self.files, edits)
        check_syntax(before, reparse(updated), {e.file for e in edits})
        self.files = updated
        self.changed.update(e.file for e in edits)
        self.focus = focus

    def span(self, node_id: str) -> tuple[str, int, int]:
        return node_span(self.project, node_id)

    def flow_node(self, node_id: str) -> FlowNode:
        member_id, path = split_node_id(node_id)
        graph = self.project.flow(member_id)
        found = next((n for n in graph.nodes if n.id == node_id), None) if graph else None
        if found is None:
            raise not_found(f"узел {node_id} не найден")
        return found

    def graph_of(self, node_id: str) -> FlowGraph | None:
        return self.project.flow(split_node_id(node_id)[0])


def replace_node_text(ed: Editor, node: Node, file: str, text: str) -> TextEdit:
    return TextEdit(file, node.start_byte, node.end_byte, text)


# --- блоки и вставка ------------------------------------------------------------------------------------------------


def statements_of(block: Node) -> list[Node]:
    return [c for c in cst.named_children(block) if c.type not in _COMMENTS]


def child_block(container: Node, slot: str) -> Node | None:
    """Блок ``{ … }`` или одиночный оператор, лежащий в слоте контейнера."""
    kind = container.type
    if kind in {
        "method_declaration",
        "constructor_declaration",
        "static_initializer",
        "initializer",
    }:
        return container.child_by_field_name("body") or cst.first_child_of_type(container, "block")
    if slot == "body" and kind in {*_BODY_OWNERS, "try_statement", "try_with_resources_statement"}:
        return container.child_by_field_name("body")
    if kind == "if_statement":
        if slot == "then":
            return container.child_by_field_name("consequence")
        if slot == "else":
            return container.child_by_field_name("alternative")
    if kind in {"try_statement", "try_with_resources_statement"}:
        if slot.startswith("catch:") and slot[6:].isdigit():
            clauses = cst.children_of_type(container, "catch_clause")
            index = int(slot[6:])
            return clauses[index].child_by_field_name("body") if index < len(clauses) else None
        if slot == "finally":
            clause = cst.first_child_of_type(container, "finally_clause")
            return cst.first_child_of_type(clause, "block") if clause is not None else None
    if kind == "block" and slot == "body":
        return container
    return None


def ensure_block(ed: Editor, parent_id: str, slot: str) -> None:
    """Одиночный оператор в теле превращается в блок: ``if (x) f();`` → ``if (x) { f(); }``."""
    file, start, end = ed.span(parent_id)
    container = ts_node(ed.project, file, start, end)
    body = child_block(container, slot)
    if body is None or body.type == "block":
        return
    data = ed.bytes_of(file)
    base = line_indent(data, container.start_byte)
    inner = base + ed.unit(file)
    text = data[body.start_byte : body.end_byte].decode("utf-8")
    header = body.prev_sibling  # «)» заголовка или ``else``: скобка встаёт на ту же строку
    begin = header.end_byte if header is not None else body.start_byte
    lead = " " if header is not None else ""
    ed.apply(
        [TextEdit(file, begin, body.end_byte, lead + "{\n" + inner + text + "\n" + base + "}")]
    )


def insertion_edit(
    ed: Editor, parent_id: str, slot: str, index: int, text: str
) -> tuple[TextEdit, int]:
    """Правка, вставляющая ``text`` (оператор) в слот; второе значение — смещение оператора в новом тексте."""
    group_file, group_start, group_end = ed.span(parent_id)
    group = ts_node(ed.project, group_file, group_start, group_end)
    if group.type == "switch_block_statement_group":
        return case_insertion(ed, group_file, group, index, text)
    ensure_block(ed, parent_id, slot)
    file, start, end = ed.span(parent_id)
    container = ts_node(ed.project, file, start, end)
    block = child_block(container, slot)
    if block is None:
        raise unsupported(f"слот «{slot}» узла {parent_id} недоступен для вставки")
    if block.type != "block":
        raise unsupported("тело без фигурных скобок")
    data = ed.bytes_of(file)
    unit = ed.unit(file)
    inner_indent = line_indent(data, block.start_byte) + unit
    statements = statements_of(block)
    if statements:
        reference = statements[min(index, len(statements) - 1)]
        inner_indent = line_indent(data, reference.start_byte)
    body = indented(text, inner_indent)
    if not statements:
        brace_end = block.end_byte - 1  # позиция закрывающей «}»
        close_indent = line_indent(data, block.start_byte)
        replacement = "\n" + body + "\n" + close_indent
        return TextEdit(
            file, block.start_byte + 1, brace_end, replacement
        ), block.start_byte + 2 + len(inner_indent)
    if index >= len(statements):
        last = statements[-1]
        return TextEdit(file, last.end_byte, last.end_byte, "\n" + body), last.end_byte + 1 + len(
            inner_indent
        )
    target = statements[index]
    begin = _with_leading_comments(data, target)
    at = line_start(data, begin)
    if data[at:begin].strip():  # оператор на одной строке с «{» или другим оператором
        return TextEdit(file, begin, begin, text + "\n" + inner_indent), begin
    return TextEdit(file, at, at, body + "\n"), at + len(inner_indent)


def case_insertion(
    ed: Editor, file: str, group: Node, index: int, text: str
) -> tuple[TextEdit, int]:
    """Вставка оператора в ветку классического ``switch`` (операторы лежат прямо в группе, без ``{}``)."""
    data = ed.bytes_of(file)
    statements = [c for c in cst.named_children(group) if c.type != "switch_label"]
    labels = cst.children_of_type(group, "switch_label")
    if statements:
        indent = line_indent(data, statements[0].start_byte)
        body = indented(text, indent)
        if index >= len(statements):
            last = statements[-1]
            return TextEdit(
                file, last.end_byte, last.end_byte, "\n" + body
            ), last.end_byte + 1 + len(indent)
        target = statements[index]
        begin = _with_leading_comments(data, target)
        at = line_start(data, begin)
        if data[at:begin].strip():
            return TextEdit(file, begin, begin, text + "\n" + indent), begin
        return TextEdit(file, at, at, body + "\n"), at + len(indent)
    colons = [c for c in group.children if c.type == ":"]
    if not labels or not colons:
        raise unsupported("в ветке нет метки")
    anchor = colons[-1].end_byte
    indent = line_indent(data, labels[-1].start_byte) + ed.unit(file)
    body = indented(text, indent)
    return TextEdit(file, anchor, anchor, "\n" + body), anchor + 1 + len(indent)


def _with_leading_comments(data: bytes, node: Node) -> int:
    """Начало оператора вместе с комментариями, стоящими прямо над ним."""
    begin = node.start_byte
    previous = node.prev_sibling
    while previous is not None and previous.type in _COMMENTS:
        gap = data[previous.end_byte : begin]
        if gap.count(b"\n") > 1 or gap.strip():
            break
        begin = previous.start_byte
        previous = previous.prev_sibling
    return begin


def removal_range(data: bytes, node: Node) -> tuple[int, int]:
    """Диапазон удаления оператора: целые строки, если он занимает их один; с комментариями над ним."""
    begin = _with_leading_comments(data, node)
    end = node.end_byte
    at = line_start(data, begin)
    tail = data.find(b"\n", end)
    tail = len(data) if tail < 0 else tail
    if not data[at:begin].strip() and not data[end:tail].strip():
        finish = min(tail + 1, len(data))
        # пустая строка-разделитель перед удалённым блоком не должна оставаться перед «}» или другой пустой строкой
        if at > 0:
            previous = line_start(data, at - 1)
            following = data[finish:].lstrip(b" \t")
            if not data[previous : at - 1].strip() and (
                following.startswith(b"}") or following.startswith(b"\n")
            ):
                at = previous
        return at, finish
    return begin, end


# --- replace_code ----------------------------------------------------------------------------------------------------


def op_replace_code(ed: Editor, op: dict[str, Any]) -> None:
    text = str(op["text"])
    if op.get("node_id"):
        file, start, end = ed.span(op["node_id"])
    elif op.get("range"):
        r = op["range"]
        file = r["file"]
        if file not in ed.files:
            raise not_found(f"файл {file} не найден")
        project = ed.project
        start = offset_of(project, file, r["start_line"], r["start_col"])
        end = offset_of(project, file, r["end_line"], r["end_col"])
    else:
        raise invalid("укажите node_id или range")
    ed.apply([TextEdit(file, start, end, text)], (file, start))


# --- insert_node -----------------------------------------------------------------------------------------------------


def op_insert_node(ed: Editor, op: dict[str, Any]) -> None:
    kind = op["kind"]
    if kind not in templates.INSERTABLE:
        raise unsupported(f"блок «{kind}» нельзя вставить")
    file = op["parent_id"].split("#", 1)[0]
    if file not in ed.files:
        raise not_found(f"узел {op['parent_id']} не найден")
    text = templates.render(kind, op.get("fields") or {}, ed.unit(file))
    edit, focus = insertion_edit(ed, op["parent_id"], op["slot"], int(op["index"]), text)
    ed.apply([edit], (edit.file, focus))


# --- update_node -----------------------------------------------------------------------------------------------------


def _replace_inner(ts: Node, file: str, field_name: str, text: str) -> TextEdit:
    """Подменяет содержимое скобок ``( … )`` условия."""
    holder = ts.child_by_field_name(field_name)
    if holder is None:
        raise unsupported("у оператора нет условия")
    if holder.type == "parenthesized_expression":
        return TextEdit(file, holder.start_byte + 1, holder.end_byte - 1, text)
    return TextEdit(file, holder.start_byte, holder.end_byte, text)


def op_update_node(ed: Editor, op: dict[str, Any]) -> None:
    node_id = op["node_id"]
    fields = op.get("fields") or {}
    flow_node = ed.flow_node(node_id)
    kind = flow_node.kind
    file, start, end = ed.span(node_id)
    ts = ts_node(ed.project, file, start, end)
    data = flow_node.data
    edits: list[TextEdit] = []

    if kind in {"condition", "loop_while", "switch"}:
        field_name = "cond" if kind == "loop_while" else "expr"
        edits.append(
            _replace_inner(ts, file, "condition", templates.expression(fields, field_name))
        )
    elif kind == "loop_for":
        body = ts.child_by_field_name("body")
        header = templates.for_header(
            fields,
            {
                "init": ", ".join(data.get("init", [])),
                "cond": data.get("cond", "") or "",
                "update": ", ".join(data.get("update", [])),
            },
        )
        if body is None:
            raise unsupported("у цикла нет тела")
        edits.append(TextEdit(file, ts.start_byte, body.start_byte, f"for ({header}) "))
    elif kind == "loop_foreach":
        parts = {
            "type": templates.type_name(fields, "type") if "type" in fields else None,
            "name": templates.identifier(fields, "name") if "name" in fields else None,
            "iterable": templates.expression(fields, "iterable") if "iterable" in fields else None,
        }
        for field_name, ts_field in (("type", "type"), ("name", "name"), ("iterable", "value")):
            if parts[field_name] is not None:
                target = ts.child_by_field_name(ts_field)
                if target is None:
                    raise unsupported("заголовок цикла не разобран")
                edits.append(
                    TextEdit(file, target.start_byte, target.end_byte, parts[field_name] or "")
                )
    elif kind == "var_decl":
        names = data.get("names") or []
        merged = {
            "type": data.get("type"),
            "name": names[0] if names else None,
            "value": (data.get("declarators") or [{}])[0].get("init"),
            **fields,
        }
        if len(names) > 1:
            if set(fields) - {"type"}:
                raise unsupported("в объявлении несколько переменных: меняется только тип")
            type_node = ts.child_by_field_name("type")
            if type_node is None:
                raise unsupported("тип объявления не найден")
            edits.append(
                TextEdit(
                    file,
                    type_node.start_byte,
                    type_node.end_byte,
                    templates.type_name(merged, "type"),
                )
            )
        else:
            edits.append(
                TextEdit(
                    file, start, end, templates.var_text(merged, list(data.get("modifiers") or []))
                )
            )
    elif kind == "assign":
        merged = {
            "target": data.get("target"),
            "op": data.get("op") or "=",
            "value": data.get("value"),
            **fields,
        }
        edits.append(TextEdit(file, start, end, templates.assign_text(merged)))
    elif kind in {"operation", "return", "throw", "yield"}:
        keyword = {"return": "return", "throw": "throw", "yield": "yield"}.get(kind)
        value = templates.expression(fields, "expr", required=kind != "return")
        text = (
            f"{keyword} {value};"
            if keyword and value
            else (f"{keyword};" if keyword else f"{value};")
        )
        edits.append(TextEdit(file, start, end, text))
    elif kind in {"break", "continue"}:
        name = templates.label(fields)
        edits.append(TextEdit(file, start, end, f"{kind} {name};" if name else f"{kind};"))
    elif kind == "catch":
        param = cst.first_child_of_type(ts, "catch_formal_parameter")
        if param is None:
            raise unsupported("параметр catch не найден")
        exc = templates.type_name(
            fields, "exception_type", ", ".join(data.get("types") or ["Exception"])
        )
        name = templates.identifier(fields, "name", data.get("name") or "e")
        edits.append(TextEdit(file, param.start_byte, param.end_byte, f"{exc} {name}"))
    elif kind == "code":
        edits.append(TextEdit(file, start, end, templates.text_field(fields, "text")))
    else:
        raise unsupported(f"блок «{kind}» не редактируется полями; используйте replace_code")
    ed.apply(edits, (file, start))


# --- delete_node / unwrap --------------------------------------------------------------------------------------------


def _owner_block(ts: Node) -> Node | None:
    return ts.parent if ts.parent is not None and ts.parent.type == "block" else None


def dedent_body(data: bytes, body: Node, base: str, unit: str) -> str:
    """Содержимое блока без фигурных скобок, сдвинутое на уровень ``base``."""
    statements = statements_of(body)
    if not statements:
        return ""
    first = _with_leading_comments(data, statements[0])
    text = data[line_start(data, first) : statements[-1].end_byte].decode("utf-8")
    lines = text.split("\n")
    current = line_indent(data, statements[0].start_byte)
    result = []
    for line in lines:
        result.append(
            base + line[len(current) :] if line.startswith(current) else base + line.lstrip()
        )
    del unit
    return "\n".join(result)


def op_unwrap(ed: Editor, node_id: str, *, require_single_branch: bool = True) -> None:
    flow_node = ed.flow_node(node_id)
    file, start, end = ed.span(node_id)
    ts = ts_node(ed.project, file, start, end)
    body: Node | None
    if ts.type == "if_statement":
        if ts.child_by_field_name("alternative") is not None and require_single_branch:
            raise precondition("у условия есть ветка else: сначала удалите её")
        body = ts.child_by_field_name("consequence")
    elif ts.type in {"try_statement", "try_with_resources_statement"}:
        if (
            cst.children_of_type(ts, "catch_clause") or cst.children_of_type(ts, "finally_clause")
        ) and require_single_branch:
            raise precondition("у try есть catch/finally: сначала удалите их")
        if ts.child_by_field_name("resources") is not None:
            raise precondition("у try есть ресурсы: развернуть нельзя")
        body = ts.child_by_field_name("body")
    elif ts.type in _BODY_OWNERS or ts.type == "block":
        body = ts if ts.type == "block" else ts.child_by_field_name("body")
    else:
        raise unsupported(f"блок «{flow_node.kind}» нельзя развернуть")
    if body is None:
        raise unsupported("у блока нет тела")
    data = ed.bytes_of(file)
    unit = ed.unit(file)
    base = line_indent(data, ts.start_byte)
    if body.type == "block":
        text = dedent_body(data, body, base, unit)
    else:
        text = base + data[body.start_byte : body.end_byte].decode("utf-8")
    begin, finish = removal_range(data, ts)
    if begin == ts.start_byte:  # оператор делит строку с другим кодом
        text = text.lstrip()
    replacement = text + ("\n" if finish > ts.end_byte else "")
    ed.apply([TextEdit(file, begin, finish, replacement)], (file, begin))


def op_delete_node(ed: Editor, op: dict[str, Any]) -> None:
    node_id = op["node_id"]
    if op.get("mode") == "unwrap":
        op_unwrap(ed, node_id)
        return
    flow_node = ed.flow_node(node_id)
    if flow_node.kind == "method":
        raise unsupported("метод удаляется операцией delete_member")
    file, start, end = ed.span(node_id)
    ts = ts_node(ed.project, file, start, end)
    data = ed.bytes_of(file)
    if flow_node.kind in {"catch", "finally"}:
        _remove_clause(ed, ts, file, data)
        return
    if _owner_block(ts) is None:
        raise precondition("оператор нельзя удалить: он единственный в теле без фигурных скобок")
    begin, finish = removal_range(data, ts)
    ed.apply([TextEdit(file, begin, finish, "")], (file, begin))


def _remove_clause(ed: Editor, clause: Node, file: str, data: bytes) -> None:
    previous = clause.prev_sibling
    begin = previous.end_byte if previous is not None else clause.start_byte
    ed.apply([TextEdit(file, begin, clause.end_byte, "")], (file, begin))


# --- duplicate / wrap -------------------------------------------------------------------------------------------------


def op_duplicate_node(ed: Editor, op: dict[str, Any]) -> None:
    node_id = op["node_id"]
    flow_node = ed.flow_node(node_id)
    if flow_node.kind in {"var_decl", "method", "catch", "finally", "case", "local_type"}:
        raise precondition(
            f"блок «{flow_node.kind}» нельзя дублировать: получится повторное объявление"
        )
    file, start, end = ed.span(node_id)
    ts = ts_node(ed.project, file, start, end)
    if _owner_block(ts) is None:
        raise precondition("копию можно вставить только рядом с оператором в блоке")
    data = ed.bytes_of(file)
    indent = line_indent(data, ts.start_byte)
    text = data[ts.start_byte : ts.end_byte].decode("utf-8")
    ed.apply(
        [TextEdit(file, ts.end_byte, ts.end_byte, "\n" + indent + text)],
        (file, ts.end_byte + 1 + len(indent)),
    )


def op_wrap_node(ed: Editor, op: dict[str, Any]) -> None:
    ids = list(op["node_ids"])
    spans = [ed.span(i) for i in ids]
    file = spans[0][0]
    if any(s[0] != file for s in spans):
        raise precondition("оборачиваемые операторы лежат в разных файлах")
    project = ed.project
    nodes = [ts_node(project, f, s, e) for f, s, e in spans]
    parent = _owner_block(nodes[0])
    if parent is None or any(n.parent is None or n.parent.id != parent.id for n in nodes):
        raise precondition("оборачивать можно только соседние операторы одного блока")
    siblings = statements_of(parent)
    positions = sorted(next(i for i, s in enumerate(siblings) if s.id == n.id) for n in nodes)
    if positions != list(range(positions[0], positions[0] + len(positions))):
        raise precondition("операторы должны идти подряд")
    first, last = siblings[positions[0]], siblings[positions[-1]]
    data = ed.bytes_of(file)
    unit = ed.unit(file)
    # объявленные внутри переменные пропадут из области видимости оборачиваемого блока
    declared = {
        cst.text_of(project.parsed(file), d.child_by_field_name("name"))
        for s in siblings[positions[0] : positions[-1] + 1]
        for d in cst.walk(s)
        if d.type == "variable_declarator" and s.type == "local_variable_declaration"
    }
    later = {
        project.parsed(file).text(n)
        for s in siblings[positions[-1] + 1 :]
        for n in cst.walk(s)
        if n.type == "identifier"
    }
    clash = sorted(declared & later)
    if clash:
        raise precondition(
            f"переменные {', '.join(clash)} используются ниже и пропадут из области видимости"
        )
    wrapper = op["wrapper"]
    fields = op.get("fields") or {}
    if wrapper == "if":
        head, tail = f"if ({templates.expression(fields, 'expr')}) {{", "}"
    elif wrapper == "while":
        head, tail = f"while ({templates.expression(fields, 'cond')}) {{", "}"
    elif wrapper == "for":
        head, tail = f"for ({templates.for_header(fields)}) {{", "}"
    elif wrapper == "try":
        exc = templates.type_name(fields, "exception_type", "Exception")
        name = templates.identifier(fields, "name", "e")
        head, tail = "try {", f"}} catch ({exc} {name}) {{\n}}"
    elif wrapper == "block":
        head, tail = "{", "}"
    else:
        raise unsupported(f"неизвестная обёртка «{wrapper}»")
    begin = _with_leading_comments(data, first)
    at = line_start(data, begin)
    base = line_indent(data, first.start_byte)
    if data[at:begin].strip():
        raise precondition("оператор стоит на одной строке с другим кодом")
    block = data[at : last.end_byte].decode("utf-8")
    inner = "\n".join(unit + line if line.strip() else line for line in block.split("\n"))
    text = f"{base}{head}\n{inner}\n{indented(tail, base)}"
    ed.apply([TextEdit(file, at, last.end_byte, text)], (file, at + len(base)))


# --- ветви --------------------------------------------------------------------------------------------------------------


def op_add_branch(ed: Editor, op: dict[str, Any]) -> None:
    node_id = op["node_id"]
    branch = op["branch"]
    fields = op.get("fields") or {}
    flow_node = ed.flow_node(node_id)
    file, start, end = ed.span(node_id)
    ts = ts_node(ed.project, file, start, end)
    data = ed.bytes_of(file)
    base = line_indent(data, ts.start_byte)

    if ts.type == "if_statement" and branch in {"else", "else_if"}:
        last = ts
        while True:
            alternative = last.child_by_field_name("alternative")
            if alternative is not None and alternative.type == "if_statement":
                last = alternative
            else:
                break
        consequence = last.child_by_field_name("consequence")
        if consequence is None:
            raise unsupported("у условия нет ветки then")
        if branch == "else":
            if last.child_by_field_name("alternative") is not None:
                raise precondition("ветка else уже есть")
            text = " else {\n" + base + "}"
        else:
            text = f" else if ({templates.expression(fields, 'expr')}) {{\n{base}}}"
        if consequence.type != "block":
            raise precondition("ветка then без фигурных скобок: сначала вставьте в неё блок")
        ed.apply(
            [TextEdit(file, consequence.end_byte, consequence.end_byte, text)],
            (file, consequence.end_byte),
        )
        return

    if ts.type in {"try_statement", "try_with_resources_statement"} and branch in {
        "catch",
        "finally",
    }:
        catches = cst.children_of_type(ts, "catch_clause")
        finally_clause = cst.first_child_of_type(ts, "finally_clause")
        if branch == "finally":
            if finally_clause is not None:
                raise precondition("блок finally уже есть")
            anchor = catches[-1] if catches else ts.child_by_field_name("body")
            text = " finally {\n" + base + "}"
        else:
            exc = templates.type_name(fields, "exception_type", "Exception")
            name = templates.identifier(fields, "name", "e")
            anchor = catches[-1] if catches else ts.child_by_field_name("body")
            text = f" catch ({exc} {name}) {{\n{base}}}"
        if anchor is None:
            raise unsupported("тело try не найдено")
        ed.apply([TextEdit(file, anchor.end_byte, anchor.end_byte, text)], (file, anchor.end_byte))
        return

    if ts.type in {"switch_statement", "switch_expression"} and branch in {"case", "default"}:
        body = ts.child_by_field_name("body")
        if body is None:
            raise unsupported("тело switch не найдено")
        arrow = (flow_node.data.get("style") or "classic") == "arrow"
        unit = ed.unit(file)
        label = "default" if branch == "default" else f"case {templates.expression(fields, 'expr')}"
        if (
            branch == "default"
            and "default" in data[ts.start_byte : ts.end_byte].decode("utf-8").split()
        ):
            raise precondition("ветка default уже есть")
        inner = base + unit
        text = (
            f"\n{inner}{label} -> {{\n{inner}}}"
            if arrow
            else f"\n{inner}{label}:\n{inner}{unit}break;"
        )
        close = body.end_byte - 1
        prefix = data[:close].rstrip()
        ed.apply([TextEdit(file, len(prefix), len(prefix), text)], (file, len(prefix) + 1))
        return

    raise unsupported(f"ветку «{branch}» нельзя добавить к блоку «{flow_node.kind}»")


def op_remove_branch(ed: Editor, op: dict[str, Any]) -> None:
    branch_id = op["branch_id"]
    parent_id = op["node_id"]
    if not branch_id.startswith(parent_id):
        raise precondition("ветка не принадлежит узлу")
    tail = branch_id[len(parent_id) :].lstrip("/")
    file, start, end = ed.span(parent_id)
    ts = ts_node(ed.project, file, start, end)
    data = ed.bytes_of(file)
    if tail == "else" and ts.type == "if_statement":
        alternative = ts.child_by_field_name("alternative")
        consequence = ts.child_by_field_name("consequence")
        if alternative is None or consequence is None:
            raise not_found("ветка else не найдена")
        ed.apply(
            [TextEdit(file, consequence.end_byte, alternative.end_byte, "")],
            (file, consequence.end_byte),
        )
        return
    match = re.fullmatch(r"catch\[(\d+)\]", tail)
    if match:
        clauses = cst.children_of_type(ts, "catch_clause")
        index = int(match.group(1))
        if index >= len(clauses):
            raise not_found("ветка catch не найдена")
        if (
            len(clauses) == 1
            and cst.first_child_of_type(ts, "finally_clause") is None
            and ts.child_by_field_name("resources") is None
        ):
            raise precondition("нельзя удалить единственный catch у try без finally")
        _remove_clause(ed, clauses[index], file, data)
        return
    if tail == "finally":
        clause = cst.first_child_of_type(ts, "finally_clause")
        if clause is None:
            raise not_found("блок finally не найден")
        if (
            not cst.children_of_type(ts, "catch_clause")
            and ts.child_by_field_name("resources") is None
        ):
            raise precondition("нельзя удалить finally у try без catch")
        _remove_clause(ed, clause, file, data)
        return
    raise unsupported(f"ветку «{tail}» удалить нельзя")


# --- move ----------------------------------------------------------------------------------------------------------------


def op_move_node(ed: Editor, op: dict[str, Any]) -> None:
    node_id = op["node_id"]
    flow_node = ed.flow_node(node_id)
    if flow_node.kind in {"method", "catch", "finally", "case", "local_type"}:
        raise precondition(f"блок «{flow_node.kind}» перемещать нельзя")
    file, start, end = ed.span(node_id)
    target_file = op["new_parent_id"].split("#", 1)[0]
    if target_file != file:
        raise unsupported("перемещение между файлами не поддерживается")
    parent_start, parent_end = ed.span(op["new_parent_id"])[1:]
    if start <= parent_start and parent_end <= end:
        raise precondition("нельзя переместить блок внутрь самого себя")
    ts = ts_node(ed.project, file, start, end)
    if _owner_block(ts) is None:
        raise precondition("перемещать можно операторы внутри блока")
    data = ed.bytes_of(file)
    text = data[ts.start_byte : ts.end_byte].decode("utf-8")
    edit, _ = insertion_edit(ed, op["new_parent_id"], op["slot"], int(op["index"]), text)
    begin, finish = removal_range(data, ts)
    if not (edit.end <= begin or edit.start >= finish):
        raise precondition("место вставки совпадает с перемещаемым блоком")
    ed.apply([edit, TextEdit(file, begin, finish, "")], None)


Handler = Callable[[Editor, dict[str, Any]], None]

HANDLERS: dict[str, Handler] = {
    "replace_code": op_replace_code,
    "insert_node": op_insert_node,
    "update_node": op_update_node,
    "delete_node": op_delete_node,
    "duplicate_node": op_duplicate_node,
    "wrap_node": op_wrap_node,
    "unwrap_node": lambda ed, op: op_unwrap(ed, op["node_id"]),
    "add_branch": op_add_branch,
    "remove_branch": op_remove_branch,
    "move_node": op_move_node,
}


def check_fragment_kind(kind: str, text: str) -> str:
    return check_fragment(kind, text, "text")


__all__ = ["HANDLERS", "EditError", "Editor", "EditResult", "check_fragment_kind"]
