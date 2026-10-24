"""Метод-поиск по ключу в поле-коллекции (PERF-13, OPT-13): общее распознавание формы для правила и исправления.

Форма метода (без ``static``, один параметр — ключ)::

    T findById(K id) {
        for (T item : items) {          // items — private-поле, List<T>
            if (item.getId() == id) {   // или item.getId().equals(id), id.equals(item.getId())
                return item;
            }
        }
        return null;
    }
"""

from __future__ import annotations

import re
from dataclasses import dataclass

from tree_sitter import Node

from engine.java import cst
from engine.java.project import JavaProject
from engine.java.semantic import SymbolTable
from engine.java.symbols import Member, base_name

LIST_TYPES = frozenset({"List", "ArrayList", "LinkedList"})
PRIMITIVE_KEYS = frozenset({"int", "long", "short", "byte", "char", "boolean"})
STRING_KEYS = frozenset({"String", "Integer", "Long", "Short", "Byte", "Character", "Boolean"})

_COMMENTS = ("line_comment", "block_comment")


@dataclass(frozen=True)
class Lookup:
    """Распознанный метод-поиск."""

    method: Member
    field: Member  # поле-коллекция
    element_type: str
    variable: str  # переменная цикла
    parameter: str  # имя параметра-ключа
    key: str  # выражение ключа через переменную цикла: ``item.getId()``
    chain: list[tuple[str, bool]]  # то же выражение как цепочка (имя, это вызов)
    key_type: str
    equals_form: bool  # сравнение через ``equals`` (иначе ``==``)
    loop: Node
    body: Node  # тело метода


def key_chain(text: str, variable: str) -> list[tuple[str, bool]] | None:
    """Цепочка ``v.id()`` / ``v.owner.name()`` как список (имя, это вызов) или ``None``."""
    match = re.fullmatch(rf"{re.escape(variable)}((?:\.\w+(?:\(\))?)+)", text.strip())
    if match is None:
        return None
    return [
        (part.rstrip("()"), part.endswith("()")) for part in match.group(1).strip(".").split(".")
    ]


def chain_members(
    table: SymbolTable, type_name: str, chain: list[tuple[str, bool]]
) -> list[Member] | None:
    """Поля и геттеры цепочки ключа по порядку; ``None`` — какое-то звено не найдено."""
    current = type_name.split("<")[0].strip()
    found: list[Member] = []
    for name, is_call in chain:
        decl = table.type_named(current)
        if decl is None:
            return None
        member = None
        for owner in (decl, *table.supertypes(decl)):
            member = next(
                (
                    m
                    for m in owner.members
                    if m.name == name
                    and (m.kind == "method" and not m.params if is_call else m.kind == "field")
                ),
                None,
            )
            if member is not None:
                break
        if member is None or not member.type_text:
            return None
        found.append(member)
        current = member.type_text.split("<")[0].strip()
    return found


def unwrap(node: Node) -> Node:
    while node.type == "parenthesized_expression" and cst.named_children(node):
        node = cst.named_children(node)[0]
    return node


def statements(block: Node | None) -> list[Node]:
    if block is None:
        return []
    items = cst.named_children(block) if block.type == "block" else [block]
    return [n for n in items if n.type not in _COMMENTS]


def match_lookup(project: JavaProject, member: Member) -> Lookup | None:
    """Форма метода-поиска или ``None``, если метод устроен иначе."""
    if member.kind != "method" or member.is_static or len(member.params) != 1:
        return None
    if member.params[0].varargs or not member.type_text:
        return None
    context, declaration = project.context(member)
    body = declaration.child_by_field_name("body") if declaration is not None else None
    if body is None:
        return None
    parsed = context.parsed

    def text(node: Node | None) -> str:
        return cst.text_of(parsed, node)

    steps = statements(body)
    if len(steps) != 2 or steps[0].type != "enhanced_for_statement":
        return None
    final = steps[1]
    if final.type != "return_statement" or text(final).replace(" ", "") != "returnnull;":
        return None
    loop = steps[0]
    source = loop.child_by_field_name("value")
    variable = text(loop.child_by_field_name("name"))
    element_type = text(loop.child_by_field_name("type"))
    parameter = member.params[0].name
    if source is None or source.type != "identifier" or element_type in {"", "var"}:
        return None
    name = text(source)
    if name in context.scope or name == parameter:
        return None  # локальная переменная или параметр, а не поле
    found = context.table.field_type(context.owner, name)
    if found is None:
        return None
    field, owner = found
    if (
        field.is_static
        or "private" not in field.modifiers
        or owner.id != context.owner.id
        or base_name(field.type_text or "") not in LIST_TYPES
    ):
        return None
    argument = re.search(r"<\s*(.+)\s*>\s*$", field.type_text or "")
    if argument is None or argument.group(1).strip() != element_type:
        return None
    if base_name(member.type_text) != base_name(element_type):
        return None
    branch = steps_of_loop(loop)
    if branch is None or branch.type != "if_statement" or branch.child_by_field_name("alternative"):
        return None
    taken = statements(branch.child_by_field_name("consequence"))
    if len(taken) != 1 or taken[0].type != "return_statement":
        return None
    if text(taken[0]).replace(" ", "") != f"return{variable};":
        return None
    condition = branch.child_by_field_name("condition")
    if condition is None:
        return None
    test = unwrap(condition)
    equals_form = False
    if test.type == "binary_expression" and text(test.child_by_field_name("operator")) == "==":
        left, right = test.child_by_field_name("left"), test.child_by_field_name("right")
        sides = [text(left), text(right)]
    elif test.type == "method_invocation" and text(test.child_by_field_name("name")) == "equals":
        holder = test.child_by_field_name("arguments")
        args = cst.named_children(holder) if holder is not None else []
        receiver = test.child_by_field_name("object")
        if receiver is None or len(args) != 1:
            return None
        sides = [text(receiver), text(args[0])]
        equals_form = True
    else:
        return None
    if parameter not in sides or sides.count(parameter) != 1:
        return None
    key = sides[1] if sides[0] == parameter else sides[0]
    chain = key_chain(key, variable)
    if not chain:
        return None
    members = chain_members(context.table, element_type, chain)
    if members is None:
        return None
    key_type = base_name(members[-1].type_text or "")
    declaration_of_key = context.table.type_named(key_type)
    is_enum = declaration_of_key is not None and declaration_of_key.kind == "enum"
    allowed = STRING_KEYS if equals_form else PRIMITIVE_KEYS
    if not (is_enum or key_type in allowed):
        return None
    parameter_type = member.params[0].type
    if parameter_type != key_type and not (is_enum and parameter_type == key_type):
        return None
    return Lookup(
        method=member,
        field=field,
        element_type=element_type,
        variable=variable,
        parameter=parameter,
        key=key,
        chain=chain,
        key_type=key_type,
        equals_form=equals_form,
        loop=loop,
        body=body,
    )


def steps_of_loop(loop: Node) -> Node | None:
    """Единственный оператор тела цикла."""
    items = statements(loop.child_by_field_name("body"))
    return items[0] if len(items) == 1 else None
