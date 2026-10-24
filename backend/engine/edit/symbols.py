"""Переименование символов и правка членов классов (S5.6)."""

from __future__ import annotations

import re
from typing import Any

from tree_sitter import Node

from engine.edit import templates
from engine.edit.core import (
    KEYWORDS,
    TextEdit,
    check_fragment,
    invalid,
    line_indent,
    line_start,
    not_found,
    precondition,
    ts_node,
    unsupported,
)
from engine.edit.operations import Editor, removal_range
from engine.java import cst
from engine.java.project import FLOWABLE, JavaProject
from engine.java.symbols import Member, TypeDecl

_NOT_REFERENCES = {
    "scoped_identifier",
    "scoped_type_identifier",
    "type_parameter",
    "package_declaration",
}


def _is_name_position(parent: Node, node: Node) -> str | None:
    """Роль идентификатора в родителе: ``call``/``field`` (имя после точки) или ``None`` (переменная)."""
    if parent.type == "method_invocation":
        name = parent.child_by_field_name("name")
        return "call" if name is not None and name.id == node.id else None
    if parent.type == "field_access":
        field_node = parent.child_by_field_name("field")
        return "field" if field_node is not None and field_node.id == node.id else None
    return None


def _check_new_name(name: str) -> str:
    cleaned = check_fragment("identifier", name, "new_name")
    if cleaned in KEYWORDS:
        raise invalid(f"«{cleaned}» — ключевое слово", "new_name")
    return cleaned


def _member_nodes(project: JavaProject, member: Member) -> tuple[str, Node | None]:
    file = project.file_of(member)
    return file, project.context(member)[1]


# --- локальные переменные и параметры ---------------------------------------------------------------------------


def _rename_local(ed: Editor, member_id: str, name: str, new_name: str) -> list[TextEdit]:
    project = ed.project
    member = project.member(member_id)
    if member is None or member.kind not in FLOWABLE:
        raise not_found(f"метод {member_id} не найден")
    ctx, declaration = project.context(member)
    file = project.file_of(member)
    if declaration is None or name not in ctx.scope:
        raise not_found(f"переменная «{name}» не найдена в {member_id}")
    if new_name in ctx.scope:
        raise precondition(f"имя «{new_name}» уже занято в методе")
    parsed = project.parsed(file)
    edits = []
    for node in cst.walk(declaration):
        if node.type != "identifier" or parsed.text(node) != name or node.parent is None:
            continue
        if node.parent.type in _NOT_REFERENCES or _is_name_position(node.parent, node) is not None:
            continue
        edits.append(TextEdit(file, node.start_byte, node.end_byte, new_name))
    return edits


# --- поля ---------------------------------------------------------------------------------------------------------


def _rename_field(ed: Editor, field: Member, new_name: str) -> list[TextEdit]:
    project = ed.project
    if field.role == "component":
        raise unsupported(
            "компонент record переименовывается вместе с аксессором: используйте replace_code"
        )
    owner = next(
        (t for t in project.types() if field.id.startswith(t.id + ".") and field in t.members), None
    )
    if owner is None:
        raise not_found(f"тип поля {field.id} не найден")
    if any(m.name == new_name and m.kind == "field" for m in owner.members):
        raise precondition(f"в {owner.path} уже есть поле «{new_name}»")
    edits: list[TextEdit] = []
    for member in project.members():
        file, declaration = _member_nodes(project, member)
        if declaration is None:
            continue
        ctx = project.context(member)[0]
        parsed = ctx.parsed
        for node in cst.walk(declaration):
            if node.type != "identifier" or parsed.text(node) != field.name or node.parent is None:
                continue
            parent = node.parent
            if member.id == field.id and parent.type == "variable_declarator":
                edits.append(TextEdit(file, node.start_byte, node.end_byte, new_name))
                continue
            role = _is_name_position(parent, node)
            if role == "call" or parent.type in _NOT_REFERENCES:
                continue
            if role == "field":
                target = parent.child_by_field_name("object")
                resolved = _field_of(ctx, target, field.name)
                if resolved is not None and resolved.id == field.id:
                    edits.append(TextEdit(file, node.start_byte, node.end_byte, new_name))
                continue
            if field.name in ctx.scope and member.kind != "field":
                continue
            resolved_field = ctx.table.field_type(ctx.owner, field.name)
            if resolved_field is not None and resolved_field[0].id == field.id:
                edits.append(TextEdit(file, node.start_byte, node.end_byte, new_name))
    return edits


def _field_of(ctx: Any, target: Node | None, name: str) -> Member | None:
    if target is None:
        return None
    if target.type == "this":
        found = ctx.table.field_type(ctx.owner, name)
        return found[0] if found else None
    owner_name = ctx.expr_type(target)
    if owner_name is None and target.type == "identifier":
        owner_name = ctx.parsed.text(target)
    decl = ctx.table.type_named(owner_name, ctx.owner) if owner_name else None
    found = ctx.table.field_type(decl, name) if decl is not None else None
    return found[0] if found else None


# --- методы -----------------------------------------------------------------------------------------------------


def _method_group(project: JavaProject, method: Member) -> list[Member]:
    """Метод и его переопределения/переопределяемые в иерархии проекта (одинаковые имя и параметры)."""
    table = project.table
    owner = next(t for t in project.types() if method in t.members)
    related = {owner.id: owner}
    for other in (*table.supertypes(owner), *table.subtypes(owner)):
        related[other.id] = other
    group = [method]
    for decl in related.values():
        for candidate in decl.members:
            if (
                candidate.kind == "method"
                and candidate.id != method.id
                and candidate.name == method.name
                and [p.type for p in candidate.params] == [p.type for p in method.params]
            ):
                group.append(candidate)
    return group


def _rename_method(ed: Editor, method: Member, new_name: str) -> list[TextEdit]:
    project = ed.project
    if method.kind != "method":
        raise unsupported("переименовывается только метод")
    if method.name == "main" and method.is_static:
        raise precondition("метод main переименовывать нельзя: это точка входа")
    group = _method_group(project, method)
    group_ids = {m.id for m in group}
    for candidate in group:
        owner = next(t for t in project.types() if candidate in t.members)
        if any(
            m.kind == "method"
            and m.name == new_name
            and [p.type for p in m.params] == [p.type for p in candidate.params]
            for m in owner.members
        ):
            raise precondition(f"в {owner.path} уже есть метод «{new_name}» с такими параметрами")
    if "Override" in " ".join(method.annotations) and len(group) == 1:
        raise precondition("метод переопределяет метод библиотеки: переименование сломает его")
    edits: list[TextEdit] = []
    for member in project.members():
        file, declaration = _member_nodes(project, member)
        if declaration is None:
            continue
        ctx = project.context(member)[0]
        if member.id in group_ids and member.kind == "method":
            name_node = declaration.child_by_field_name("name")
            if name_node is not None:
                edits.append(TextEdit(file, name_node.start_byte, name_node.end_byte, new_name))
        for node in cst.walk(declaration):
            if node.type == "method_invocation":
                name_node = node.child_by_field_name("name")
                if name_node is None or ctx.parsed.text(name_node) != method.name:
                    continue
                if any(t.id in group_ids for t in ctx.resolve_call(node)):
                    edits.append(TextEdit(file, name_node.start_byte, name_node.end_byte, new_name))
            elif node.type == "method_reference":
                identifiers = [c for c in node.children if c.type == "identifier"]
                if (
                    identifiers
                    and ctx.parsed.text(identifiers[-1]) == method.name
                    and len(identifiers) >= 1
                ):
                    qualifier = node.children[0]
                    owner_name = ctx.expr_type(qualifier) or (
                        ctx.parsed.text(qualifier) if qualifier.type == "identifier" else None
                    )
                    decl = ctx.table.type_named(owner_name, ctx.owner) if owner_name else None
                    if decl is not None and any(m in decl.members for m in group):
                        last = identifiers[-1]
                        edits.append(TextEdit(file, last.start_byte, last.end_byte, new_name))
    return _unique(edits)


def _unique(edits: list[TextEdit]) -> list[TextEdit]:
    seen: set[tuple[str, int, int]] = set()
    result = []
    for edit in edits:
        key = (edit.file, edit.start, edit.end)
        if key not in seen:
            seen.add(key)
            result.append(edit)
    return result


# --- типы ---------------------------------------------------------------------------------------------------------


def _rename_type(ed: Editor, decl: TypeDecl, new_name: str) -> list[TextEdit]:
    project = ed.project
    if "public" in decl.modifiers and decl.parent_path is None:
        raise precondition("публичный тип совпадает с именем файла: переименуйте файл и тип вместе")
    same = [t for t in project.types() if t.name == decl.name]
    if len(same) > 1:
        raise precondition(
            f"в проекте несколько типов с именем «{decl.name}»: однозначно переименовать нельзя"
        )
    if any(t.name == new_name for t in project.types()):
        raise precondition(f"тип «{new_name}» уже существует")
    edits: list[TextEdit] = []
    for file in project.files:
        parsed = project.parsed(file)
        for node in cst.walk(parsed.root):
            if node.type not in {"type_identifier", "identifier"} or parsed.text(node) != decl.name:
                continue
            parent = node.parent
            if parent is None or parent.type in {"package_declaration"}:
                continue
            if node.type == "identifier":
                if (
                    parent.type == "constructor_declaration"
                    and parent.child_by_field_name("name") is not None
                ):
                    if file == decl.id.split("#", 1)[0]:
                        edits.append(TextEdit(file, node.start_byte, node.end_byte, new_name))
                    continue
                static_use = (
                    parent.type in {"field_access", "method_invocation", "method_reference"}
                    and parent.children[0].id == node.id
                )
                if not static_use:
                    continue
            edits.append(TextEdit(file, node.start_byte, node.end_byte, new_name))
    return _unique(edits)


def op_rename_symbol(ed: Editor, op: dict[str, Any]) -> None:
    symbol_id = op["symbol_id"]
    new_name = _check_new_name(op["new_name"])
    project = ed.project
    edits: list[TextEdit]
    if "/var:" in symbol_id or "/param:" in symbol_id:
        member_id, _, tail = symbol_id.partition("/")
        name = tail.split(":", 1)[1]
        edits = _rename_local(ed, member_id, name, new_name)
    else:
        member = project.member(symbol_id)
        decl = next((t for t in project.types() if t.id == symbol_id), None)
        if member is not None and member.kind == "field":
            edits = _rename_field(ed, member, new_name)
        elif member is not None and member.kind == "method":
            edits = _rename_method(ed, member, new_name)
        elif decl is not None:
            edits = _rename_type(ed, decl, new_name)
            # объявление типа: имя в самом объявлении
            file = symbol_id.split("#", 1)[0]
            node = ts_node(project, file, decl.start_byte, decl.end_byte)
            name_node = node.child_by_field_name("name")
            if name_node is not None:
                edits.append(TextEdit(file, name_node.start_byte, name_node.end_byte, new_name))
            edits = _unique(edits)
        else:
            raise not_found(f"символ {symbol_id} не найден или не переименовывается")
    if not edits:
        raise not_found(f"использований символа {symbol_id} не найдено")
    ed.apply(edits, None)


# --- члены -----------------------------------------------------------------------------------------------------------


def _modifier_text(fields: dict[str, Any], default: str) -> str:
    raw = fields.get("modifiers")
    if raw is None:
        return default
    items = raw if isinstance(raw, list) else str(raw).split()
    allowed = {
        "public",
        "protected",
        "private",
        "static",
        "final",
        "abstract",
        "synchronized",
        "default",
    }
    bad = [m for m in items if m not in allowed]
    if bad:
        raise invalid(f"неизвестные модификаторы: {', '.join(bad)}", "modifiers")
    return " ".join(items)


def _params_text(fields: dict[str, Any]) -> str:
    params = fields.get("params") or []
    parts = []
    for item in params:
        if not isinstance(item, dict):
            raise invalid("параметр должен быть {type, name}", "params")
        parts.append(
            f"{check_fragment('type', str(item.get('type', '')), 'params')} {check_fragment('identifier', str(item.get('name', '')), 'params')}"
        )
    return ", ".join(parts)


def _throws_text(fields: dict[str, Any]) -> str:
    throws = fields.get("throws") or []
    items = [check_fragment("type", str(t), "throws") for t in throws]
    return f" throws {', '.join(items)}" if items else ""


def _type_body(
    project: JavaProject, type_id: str, *, bare_enum: bool = False
) -> tuple[str, TypeDecl, Node]:
    decl = next((t for t in project.types() if t.id == type_id), None)
    if decl is None:
        raise not_found(f"тип {type_id} не найден")
    file = type_id.split("#", 1)[0]
    node = ts_node(project, file, decl.start_byte, decl.end_byte)
    body = node.child_by_field_name("body")
    if body is None:
        raise unsupported("у типа нет тела")
    if (
        not bare_enum
        and body.type == "enum_body"
        and cst.first_child_of_type(body, "enum_body_declarations") is None
    ):
        raise unsupported("в enum без секции членов добавить член нельзя")
    return file, decl, body


def _add_enum_constant(ed: Editor, op: dict[str, Any]) -> None:
    fields = op.get("fields") or {}
    project = ed.project
    file, decl, body = _type_body(project, op["type_id"], bare_enum=True)
    if decl.kind != "enum":
        raise unsupported("константу можно добавить только в enum")
    name = templates.identifier(fields, "name")
    if name in decl.constants:
        raise precondition(f"константа «{name}» уже есть в {decl.name}")
    args = [check_fragment("expression", str(a), "args") for a in fields.get("args") or []]
    text = name + (f"({', '.join(args)})" if args else "")
    data = ed.bytes_of(file)
    base = line_indent(data, body.start_byte)
    indent = base + ed.unit(file)
    constants = cst.children_of_type(body, "enum_constant")
    if constants:
        last = constants[-1]
        follower = last.next_sibling
        multiline = (
            b"\n" in data[body.start_byte : constants[0].start_byte]
            or len(constants) > 1
            and (b"\n" in data[constants[0].end_byte : last.start_byte])
        )
        separator = ",\n" + indent if multiline else ", "
        if follower is not None and follower.type == ",":
            edit = TextEdit(
                file, follower.end_byte, follower.end_byte, separator.lstrip(",") + text + ","
            )
        else:
            edit = TextEdit(file, last.end_byte, last.end_byte, separator + text)
    else:
        declarations = cst.first_child_of_type(body, "enum_body_declarations")
        if declarations is not None:
            edit = TextEdit(file, declarations.start_byte, declarations.start_byte, text)
        else:
            close = body.end_byte - 1
            edit = TextEdit(file, body.start_byte + 1, close, "\n" + indent + text + "\n" + base)
    ed.apply([edit], (file, edit.start))


def _add_record_component(ed: Editor, op: dict[str, Any]) -> None:
    fields = op.get("fields") or {}
    project = ed.project
    file, decl, _ = _type_body(project, op["type_id"])
    if decl.kind != "record":
        raise unsupported("компонент можно добавить только в record")
    name = templates.identifier(fields, "name")
    if any(c.name == name for c in decl.components):
        raise precondition(f"компонент «{name}» уже есть в {decl.name}")
    component = f"{templates.type_name(fields, 'type')} {name}"
    node = ts_node(project, file, decl.start_byte, decl.end_byte)
    header = node.child_by_field_name("parameters")
    if header is None:
        raise unsupported("у record нет списка компонентов")
    close = header.end_byte - 1
    existing = decl.components
    edit = TextEdit(file, close, close, (", " if existing else "") + component)
    ed.apply([edit], (file, close))


def op_add_member(ed: Editor, op: dict[str, Any]) -> None:
    if op["kind"] == "enum_constant":
        _add_enum_constant(ed, op)
        return
    if op["kind"] == "record_component":
        _add_record_component(ed, op)
        return
    fields = op.get("fields") or {}
    project = ed.project
    file, decl, body = _type_body(project, op["type_id"])
    data = ed.bytes_of(file)
    unit = ed.unit(file)
    indent = line_indent(data, body.start_byte) + unit
    kind = op["kind"]
    abstract_body = decl.kind == "interface"
    if kind == "field":
        modifiers = _modifier_text(fields, "private")
        value = templates.expression(fields, "value", required=False)
        head = f"{modifiers} " if modifiers else ""
        text = (
            f"{head}{templates.type_name(fields, 'type')} {templates.identifier(fields, 'name')}"
            + (f" = {value};" if value else ";")
        )
    elif kind == "method":
        modifiers = _modifier_text(fields, "" if abstract_body else "public")
        head = f"{modifiers} " if modifiers else ""
        ret = templates.type_name(fields, "return_type", "void")
        signature = f"{head}{ret} {templates.identifier(fields, 'name')}({_params_text(fields)}){_throws_text(fields)}"
        text = signature + (";" if abstract_body or "abstract" in modifiers else " {\n}")
    elif kind == "constructor":
        modifiers = _modifier_text(fields, "public")
        head = f"{modifiers} " if modifiers else ""
        text = f"{head}{decl.name}({_params_text(fields)}){_throws_text(fields)} {{\n}}"
    else:
        raise unsupported(f"член «{kind}» добавить нельзя")
    members = [m for m in decl.members if project.file_of(m) == file]
    present = [m for m in members if m.kind == "field"]
    newline = "\n"
    block = newline.join(indent + line if line else line for line in text.split(newline))
    if kind == "field" and present:
        anchor, insertion = present[-1].end_byte, newline + block
    elif kind == "field":
        anchor, insertion = body.start_byte + 1, newline + block
        if members or b"\n" not in data[body.start_byte : body.end_byte]:
            insertion += newline + (line_indent(data, body.start_byte) if not members else "")
    else:
        close = body.end_byte - 1
        anchor = len(data[:close].rstrip())
        insertion = (newline * 2 if members else newline) + block
        if b"\n" not in data[anchor:close]:  # ``class A {}`` в одну строку
            insertion += newline + line_indent(data, body.start_byte)
    ed.apply([TextEdit(file, anchor, anchor, insertion)], (file, anchor))


def op_update_member(ed: Editor, op: dict[str, Any]) -> None:
    fields = dict(op.get("fields") or {})
    project = ed.project
    member = project.member(op["member_id"])
    if member is None:
        raise not_found(f"член {op['member_id']} не найден")
    member_id = member.id
    new_name = fields.pop("name", None)
    if new_name and new_name != member.name:
        op_rename_symbol(ed, {"symbol_id": member_id, "new_name": new_name})
        member_id = member_id.replace(f".{member.name}", f".{_check_new_name(str(new_name))}", 1)
        project = ed.project
        member = project.member(member_id)
        if member is None:
            raise not_found("член после переименования не найден")
    if not fields:
        return
    file = project.file_of(member)
    ts = ts_node(project, file, member.start_byte, member.end_byte)
    edits: list[TextEdit] = []
    if "modifiers" in fields:
        keywords = _modifier_text(fields, "")
        modifiers = cst.first_child_of_type(ts, "modifiers")
        annotations = (
            " ".join(
                project.parsed(file).text(c) for c in modifiers.children if "annotation" in c.type
            )
            if modifiers is not None
            else ""
        )
        text = " ".join(part for part in (annotations, keywords) if part)
        if modifiers is not None:
            edits.append(TextEdit(file, modifiers.start_byte, modifiers.end_byte, text))
        elif text:
            first = ts.children[0]
            edits.append(TextEdit(file, first.start_byte, first.start_byte, text + " "))
    if "type" in fields or "return_type" in fields:
        key = "return_type" if "return_type" in fields else "type"
        type_node = ts.child_by_field_name("type")
        if type_node is None:
            raise unsupported("тип члена не найден")
        edits.append(
            TextEdit(
                file, type_node.start_byte, type_node.end_byte, templates.type_name(fields, key)
            )
        )
    if "params" in fields:
        holder = ts.child_by_field_name("parameters")
        if holder is None:
            raise unsupported("у члена нет списка параметров")
        edits.append(
            TextEdit(file, holder.start_byte, holder.end_byte, f"({_params_text(fields)})")
        )
    if "throws" in fields:
        throws = cst.first_child_of_type(ts, "throws")
        text = _throws_text(fields)
        if throws is not None:
            edits.append(
                TextEdit(
                    file,
                    throws.start_byte - (1 if text == "" else 0),
                    throws.end_byte,
                    text.lstrip() if text else "",
                )
            )
        elif text:
            holder = ts.child_by_field_name("parameters")
            if holder is None:
                raise unsupported("у члена нет списка параметров")
            edits.append(TextEdit(file, holder.end_byte, holder.end_byte, text))
    if edits:
        ed.apply(edits, (file, member.start_byte))


def _delete_constant(ed: Editor, member: Member) -> None:
    project = ed.project
    file = project.file_of(member)
    node = ts_node(project, file, member.start_byte, member.end_byte)
    data = ed.bytes_of(file)
    follower = node.next_sibling
    if follower is not None and follower.type == ",":
        end = follower.end_byte
        while end < len(data) and data[end : end + 1] in (b" ", b"\t"):
            end += 1
        if data[end : end + 1] == b"\n":
            end += 1
            begin = line_start(data, node.start_byte)
            if data[begin : node.start_byte].strip():
                begin = node.start_byte
        else:
            begin = node.start_byte
        edit = TextEdit(file, begin, end, "")
    else:
        before = node.prev_sibling
        if before is not None and before.type == ",":
            edit = TextEdit(file, before.start_byte, node.end_byte, "")
        else:
            edit = TextEdit(file, node.start_byte, node.end_byte, "")
    ed.apply([edit], (file, node.start_byte))


def _delete_component(ed: Editor, member: Member) -> None:
    project = ed.project
    file = project.file_of(member)
    decl = next(t for t in project.types() if member in t.members)
    node = ts_node(project, file, decl.start_byte, decl.end_byte)
    header = node.child_by_field_name("parameters")
    if header is None:
        raise unsupported("у record нет списка компонентов")
    parameters = [c for c in header.children if c.type == "formal_parameter"]
    target = next(
        (
            p
            for p in parameters
            if (name := p.child_by_field_name("name")) is not None
            and project.parsed(file).text(name) == member.name
        ),
        None,
    )
    if target is None:
        raise not_found(f"компонент {member.name} не найден")
    index = parameters.index(target)
    if len(parameters) == 1:
        edit = TextEdit(file, target.start_byte, target.end_byte, "")
    elif index == len(parameters) - 1:
        edit = TextEdit(file, parameters[index - 1].end_byte, target.end_byte, "")
    else:
        edit = TextEdit(file, target.start_byte, parameters[index + 1].start_byte, "")
    ed.apply([edit], (file, target.start_byte))


def op_delete_member(ed: Editor, op: dict[str, Any]) -> None:
    project = ed.project
    member = project.member(op["member_id"])
    if member is None:
        raise not_found(f"член {op['member_id']} не найден")
    if member.role in {"constant", "component"}:
        owner = next(t for t in project.types() if member in t.members)
        users = []
        for other in project.members():
            if other.id == member.id or other.kind not in FLOWABLE:
                continue
            used = project.call_counts(other)[member.id] or project.use_counts(other)[0][member.id]
            if used:
                users.append(other.key)
        if member.role == "component":
            created = sum(
                len(re.findall(rf"\bnew\s+{re.escape(owner.name)}\s*\(", text))
                for text in project.files.values()
            )
            if created:
                users.append(f"new {owner.name}(…) × {created}")
        if users:
            shown = ", ".join(users[:3]) + ("…" if len(users) > 3 else "")
            raise precondition(f"«{member.name}» используется в: {shown}")
        (_delete_constant if member.role == "constant" else _delete_component)(ed, member)
        return
    users = []
    for other in project.members():
        if other.id == member.id or other.kind not in FLOWABLE:
            continue
        used = project.call_counts(other)[member.id] or project.use_counts(other)[0][member.id]
        if used:
            users.append(other.key)
    if users:
        shown = ", ".join(users[:3]) + ("…" if len(users) > 3 else "")
        raise precondition(f"«{member.name}» используется в: {shown}")
    file = project.file_of(member)
    ts = ts_node(project, file, member.start_byte, member.end_byte)
    data = ed.bytes_of(file)
    begin, finish = removal_range(data, ts)
    ed.apply([TextEdit(file, begin, finish, "")], (file, begin))
