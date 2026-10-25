"""Замена клонов общим методом (S9.3, REF-02, docs/06 § 5.2).

Клоны — окна из подряд идущих операторов, совпадающие с точностью до идентификаторов и литералов
(``engine.analysis.metrics``). Общий метод строится по первому вхождению; параметрами становятся
локальные переменные, которые читаются из окружения, и литералы, которые различаются между
вхождениями. Различающиеся имена методов, полей и типов передать параметром нельзя: такое вхождение
остаётся как есть, а в отчёт попадает причина.
"""

from __future__ import annotations

import re
from collections import deque
from dataclasses import dataclass, field, replace

from tree_sitter import Node

from engine.analysis.metrics import _LITERALS, CLONE_MIN_STATEMENTS, Place, _statement_count
from engine.edit.core import (
    EditError,
    TextEdit,
    check_fragment,
    indent_unit,
    line_indent,
)
from engine.java import cst
from engine.java.project import JavaProject
from engine.java.symbols import Member
from engine.transform.base import Check, Refusal, refuse
from engine.transform.dataflow import Flow, _is_reference, _resolve, analyze, declarations
from engine.transform.extract_method import _unique_name, suggest_name

MAX_VARYING = 4  # сколько различающихся мест можно превратить в параметры
MAX_PARAMS = 8

NEWLINE = "\n"
_COMMENTS = {"line_comment", "block_comment"}
_DECLARES = {
    "variable_declarator",
    "enhanced_for_statement",
    "catch_formal_parameter",
    "formal_parameter",
    "spread_parameter",
    "lambda_expression",
    "resource",
}
_CONSTANT_CONTEXTS = {"switch_label", "annotation", "marker_annotation"}
_MARKS = {"var": "\x01", "internal": "\x01", "decl": "\x01", "lit": "\x02"}
_LITERAL_NAMES = {
    "int": "value",
    "long": "value",
    "double": "value",
    "float": "value",
    "String": "text",
    "char": "symbol",
    "boolean": "flag",
}


class Skip(Exception):
    """Вхождение нельзя заменить вызовом; ``args[0]`` — причина для отчёта."""


@dataclass(slots=True)
class Slot:
    """Место фрагмента, которое может различаться между клонами."""

    tag: str  # var | internal | decl | lit | fixed
    text: str
    start: int
    end: int
    type_text: str = ""  # var — объявленный тип, lit — тип литерала
    detail: int = -1  # internal — порядковый номер объявления внутри фрагмента
    hint: str = ""  # lit — подсказка для имени параметра


@dataclass(slots=True)
class Occurrence:
    place: Place
    member: Member
    start: int
    end: int
    flow: Flow
    slots: list[Slot]
    skeleton: str
    suggested: str
    output_slot: int | None  # индекс объявления результата, если он объявлен внутри фрагмента


@dataclass(slots=True)
class Param:
    name: str
    type_text: str
    values: list[str]
    slots: list[int]
    kind: str  # var | lit

    @property
    def varying(self) -> bool:
        return len(set(self.values)) > 1


@dataclass(slots=True)
class DedupePlan:
    name: str
    signature: str
    code: str
    edits: list[TextEdit]
    params: list[Param]
    occurrences: list[Place]
    notes: list[str] = field(default_factory=list)


def literal_type(kind: str, text: str) -> str | None:
    if kind.endswith("integer_literal"):
        return "long" if text[-1:] in "lL" else "int"
    if kind == "decimal_floating_point_literal":
        return "float" if text[-1:] in "fF" else "double"
    return {
        "string_literal": "String",
        "character_literal": "char",
        "true": "boolean",
        "false": "boolean",
    }.get(kind)


def _name_hint(node: Node, literal_kind: str) -> str:
    """Подсказка для имени параметра-литерала по окружению: сообщение исключения, формат и т. п."""
    parent = node.parent
    while parent is not None and parent.type != "block":
        if parent.type == "object_creation_expression" and literal_kind == "string_literal":
            made = parent.child_by_field_name("type")
            if (
                made is not None
                and made.text is not None
                and made.text.decode("utf-8").endswith(("Exception", "Error"))
            ):
                return "message"
        if parent.type == "method_invocation":
            return "text" if literal_kind == "string_literal" else ""
        parent = parent.parent
    return ""


def _in_constant_context(node: Node) -> bool:
    parent = node.parent
    while parent is not None and parent.type != "block":
        if parent.type in _CONSTANT_CONTEXTS:
            return True
        parent = parent.parent
    return False


def _locate(project: JavaProject, place: Place) -> tuple[Member, Node, int, int]:
    """Метод, блок и индексы первого и последнего оператора окна клона."""
    member = project.member(place.method_id)
    if member is None or member.kind == "initializer":
        raise Skip("фрагмент лежит не в методе и не в конструкторе")
    declaration = project.context(member)[1]
    body = None
    if declaration is not None:
        body = declaration.child_by_field_name("body") or cst.first_child_of_type(
            declaration, "block"
        )
    if body is None:
        raise Skip("у метода нет тела")
    parsed = project.parsed(place.file)
    for block in cst.walk(body):
        if block.type != "block":
            continue
        statements = cst.named_children(block)
        last = place.first + place.size - 1
        if last >= len(statements):
            continue
        if (
            parsed.range_of(statements[place.first]).start.line == place.start_line
            and parsed.range_of(statements[last]).end.line == place.end_line
        ):
            return member, block, place.first, last
    raise Skip("окно клона не найдено в методе")


def occurrence(project: JavaProject, place: Place, front: int = 0, back: int = 0) -> Occurrence:
    """Вхождение клона без ``front`` первых и ``back`` последних операторов окна."""
    member, block, first, last = _locate(project, place)
    if last - first + 1 != place.size:
        raise Skip("границы фрагмента не совпали с найденным клоном")
    first, last = first + front, last - back
    if last < first:
        raise Skip("в окне не осталось операторов")
    flow = analyze(project, member, block, first, last)
    if flow.problems:
        raise Skip(flow.problems[0])
    if len(flow.outputs) > 1:
        raise Skip(f"фрагмент даёт несколько результатов ({len(flow.outputs)})")
    source = project.files[place.file].encode("utf-8")
    region = cst.named_children(block)[first : last + 1]
    if sum(_statement_count(s) for s in region) < CLONE_MIN_STATEMENTS:
        raise Skip("после сокращения окна фрагмент слишком мал")
    start, end = region[0].start_byte, region[-1].end_byte
    span = project.parsed(place.file).lines.range(start, end)
    place = replace(
        place,
        start_line=span.start.line,
        end_line=span.end.line,
        first=first,
        size=last - first + 1,
    )
    _ctx, method = project.context(member)
    assert method is not None
    decls = declarations(source, method)
    internal = sorted({(d.start, d.name) for d in decls if start <= d.start < end})
    ordinal = {key: index for index, key in enumerate(internal)}

    slots: list[Slot] = []
    comments: list[tuple[int, int]] = []

    def text_of(node: Node) -> str:
        return source[node.start_byte : node.end_byte].decode("utf-8")

    for node in cst.walk(block):
        if node.end_byte <= start or node.start_byte >= end:
            continue
        if node.type in _COMMENTS:
            comments.append((node.start_byte, node.end_byte))
            continue
        if node.start_byte < start or node.end_byte > end:
            continue
        kind = node.type
        if kind in _LITERALS:
            text = text_of(node)
            typed = literal_type(kind, text)
            if typed is None or _in_constant_context(node):
                slots.append(Slot("fixed", text, node.start_byte, node.end_byte))
            else:
                slots.append(
                    Slot(
                        "lit",
                        text,
                        node.start_byte,
                        node.end_byte,
                        typed,
                        -1,
                        _name_hint(node, kind),
                    )
                )
        elif kind == "type_identifier":
            slots.append(Slot("fixed", text_of(node), node.start_byte, node.end_byte))
        elif kind == "identifier":
            text = text_of(node)
            if _is_reference(node):
                decl = _resolve(decls, text, node.start_byte)
                if decl is None:
                    slots.append(Slot("fixed", text, node.start_byte, node.end_byte))
                elif start <= decl.start < end:
                    slots.append(
                        Slot(
                            "internal",
                            text,
                            node.start_byte,
                            node.end_byte,
                            detail=ordinal[(decl.start, decl.name)],
                        )
                    )
                else:
                    slots.append(Slot("var", text, node.start_byte, node.end_byte, decl.type_text))
            else:
                parent = node.parent
                tag = "decl" if parent is not None and parent.type in _DECLARES else "fixed"
                slots.append(Slot(tag, text, node.start_byte, node.end_byte))

    marks = sorted(
        [(s.start, s.end, _MARKS.get(s.tag, "")) for s in slots if s.tag in _MARKS]
        + [(a, b, "") for a, b in comments]
    )
    pieces: list[str] = []
    cursor = start
    for a, b, mark in marks:
        if a < cursor:
            continue
        pieces.append(source[cursor:a].decode("utf-8"))
        pieces.append(mark)
        cursor = b
    pieces.append(source[cursor:end].decode("utf-8"))
    skeleton = re.sub(r"\s+", " ", "".join(pieces)).strip()

    output_slot = None
    if flow.outputs and flow.outputs[0].name in flow.declared_inside:
        name = flow.outputs[0].name
        output_slot = next(
            (i for i, s in enumerate(slots) if s.tag == "decl" and s.text == name), None
        )
    suggested = suggest_name(project, member, region, flow)
    return Occurrence(place, member, start, end, flow, slots, skeleton, suggested, output_slot)


def _compare(base: Occurrence, other: Occurrence) -> str | None:
    """Причина, по которой ``other`` нельзя заменить тем же методом, что ``base``; ``None`` — можно."""
    if other.member.type_path != base.member.type_path:
        return "вхождение лежит в другом классе"
    if len(base.slots) != len(other.slots):
        return "структура фрагментов различается"
    for x, y in zip(base.slots, other.slots, strict=True):
        if x.tag != y.tag:
            return f"«{x.text}» и «{y.text}» играют разные роли (переменная, поле, имя метода)"
        if x.tag == "fixed" and x.text != y.text:
            return f"различаются имена, которые нельзя передать параметром: «{x.text}» и «{y.text}»"
        if x.tag == "internal" and x.detail != y.detail:
            return "локальные переменные внутри фрагментов связаны по-разному"
        if x.tag in {"var", "lit"} and x.type_text != y.type_text:
            return f"типы различаются: {x.type_text} и {y.type_text}"
    if base.skeleton != other.skeleton:
        return "тексты фрагментов различаются не только именами и литералами"
    fa, fb = base.flow, other.flow
    if fa.tail_return != fb.tail_return:
        return "один фрагмент заканчивается на return, другой нет"
    if fa.tail_return and base.member.type_text != other.member.type_text:
        return "возвращаемые типы методов различаются"
    if len(fa.outputs) != len(fb.outputs):
        return "число результатов различается"
    if fa.outputs:
        oa, ob = fa.outputs[0], fb.outputs[0]
        if oa.type_text != ob.type_text:
            return "типы результатов различаются"
        if (oa.name in fa.declared_inside) != (ob.name in fb.declared_inside):
            return "результат объявлен внутри одного фрагмента и снаружи другого"
        if base.output_slot != other.output_slot:
            return "результат объявлен в разных местах"
    if sorted(fa.throws) != sorted(fb.throws):
        return "проверяемые исключения различаются"
    return None


def _where(place: Place) -> str:
    return f"{place.file}:{place.start_line}-{place.end_line}"


def clone_places(project: JavaProject, file: str, start_line: int, end_line: int) -> list[Place]:
    """Все вхождения клона, считая от заданного (оно идёт первым); пересекающиеся отбрасываются."""
    from engine.analysis.analyzer import (
        metrics_result,  # отложенный импорт: analyzer импортирует fixes
    )

    def key(place: Place) -> tuple[str, int, int]:
        return (place.file, place.start_line, place.end_line)

    links: dict[tuple[str, int, int], list[Place]] = {}
    for clone in metrics_result(project).clones:
        links.setdefault(key(clone.first), []).append(clone.second)
        links.setdefault(key(clone.second), []).append(clone.first)
    seed = (file, start_line, end_line)
    places: dict[tuple[str, int, int], Place] = {}
    for clone in metrics_result(project).clones:
        for place in (clone.first, clone.second):
            places.setdefault(key(place), place)
    if seed not in places:
        return []
    seen = {seed}
    queue: deque[tuple[str, int, int]] = deque([seed])
    while queue:
        for other in links.get(queue.popleft(), []):
            if key(other) not in seen:
                seen.add(key(other))
                queue.append(key(other))
    ordered = [places[seed]] + sorted(
        (places[k] for k in seen if k != seed), key=lambda p: (p.file, p.start_line)
    )
    kept: list[Place] = []
    for place in ordered:
        if any(
            p.file == place.file
            and not (place.end_line < p.start_line or place.start_line > p.end_line)
            for p in kept
        ):
            continue
        kept.append(place)
    return kept


def _name_for(base: Occurrence) -> str:
    """Имя общего метода: подсказка выделения метода или «вычислить» + последняя переменная."""
    if base.suggested not in {"process", "extractedBlock"}:
        return base.suggested
    for slot in reversed(base.slots):
        if slot.tag in {"var", "internal"} and slot.text.isidentifier():
            return "compute" + slot.text[:1].upper() + slot.text[1:]
    return base.suggested


def _fresh(wanted: str, taken: set[str]) -> str:
    name, index = wanted, 2
    while name in taken:
        name = f"{wanted}{index}"
        index += 1
    return name


def build(project: JavaProject, places: list[Place], name: str | None = None) -> DedupePlan:
    """План замены клонов; ``places[0]`` — образец общего метода. Не годится — ``Refusal``.

    Окно клона берётся целиком, а если оно не сводится к одному методу (например, первый оператор
    читает разные поля), пробуются окна короче: без первых и последних операторов."""
    if len(places) < 2:
        raise refuse("Клон найден", "нужно как минимум два вхождения")
    size = places[0].size
    failure: Refusal | None = None
    for total in range(0, size - 1):
        for front in range(total + 1):
            try:
                return _build(project, places, name, front, total - front)
            except Refusal as refusal:
                failure = failure or refusal
    assert failure is not None
    raise failure


def _build(
    project: JavaProject, places: list[Place], name: str | None, front: int, back: int
) -> DedupePlan:
    if len(places) < 2:
        raise refuse("Клон найден", "нужно как минимум два вхождения")
    notes: list[str] = []
    try:
        base = occurrence(project, places[0], front, back)
    except Skip as skip:
        raise refuse(
            "Образец клона годится для метода", f"{_where(places[0])}: {skip.args[0]}"
        ) from skip
    group = [base]
    for place in places[1:]:
        try:
            found = occurrence(project, place, front, back)
            reason = _compare(base, found)
        except Skip as skip:
            reason = str(skip.args[0])
            found = None
        if found is not None and reason is None:
            group.append(found)
        else:
            notes.append(f"{_where(place)}: {reason}")
    if len(group) < 2:
        raise refuse(
            "Вхождения совпадают с точностью до имён и литералов", notes[0] if notes else ""
        )

    needs_instance = any(o.flow.uses_this for o in group)
    if needs_instance:
        static = [o for o in group if o.member.is_static]
        if base in static:
            raise refuse(
                "Метод не статический",
                "образец лежит в статическом методе, а фрагмент использует this",
            )
        for o in static:
            group.remove(o)
            notes.append(f"{_where(o.place)}: фрагмент использует this, а метод статический")
        if len(group) < 2:
            raise refuse("Вхождения совпадают с точностью до имён и литералов", notes[0])

    flow = base.flow
    out = flow.outputs[0] if flow.outputs else None
    declared_inside = out is not None and out.name in flow.declared_inside
    returns = (
        (base.member.type_text or "void")
        if flow.tail_return
        else (out.type_text if out else "void")
    )
    used_types = " ".join([returns, *(s.type_text for s in base.slots if s.tag == "var")])
    type_params = [
        declared
        for declared in base.member.type_params
        if re.search(rf"{re.escape(declared.split()[0])}", used_types)
    ]
    for o in list(group[1:]):
        if any(tp not in o.member.type_params for tp in type_params):
            group.remove(o)
            notes.append(f"{_where(o.place)}: типовые параметры метода различаются")
    if len(group) < 2:
        raise refuse("Вхождения совпадают с точностью до имён и литералов", notes[-1])

    owner = next(t for t in project.types() if base.member in t.members)
    used = {s.text for s in base.slots if s.tag in {"fixed", "internal", "decl"}}
    used |= {m.name for m in owner.members if m.kind == "field"}
    params: list[Param] = []
    by_key: dict[tuple[str, tuple[str, ...]], Param] = {}
    taken: set[str] = set()
    for index, slot in enumerate(base.slots):
        if slot.tag not in {"var", "lit"}:
            continue
        values = tuple(o.slots[index].text for o in group)
        if slot.tag == "lit" and len(set(values)) == 1:
            continue  # одинаковый литерал остаётся в теле метода
        key = (slot.tag, values)
        param = by_key.get(key)
        if param is None:
            if slot.tag == "var":
                wanted = slot.text
                pname = wanted if wanted not in taken else _fresh(wanted, taken | used)
            else:
                wanted = slot.hint or _LITERAL_NAMES.get(slot.type_text, "value")
                pname = _fresh(wanted, taken | used)
            taken.add(pname)
            param = by_key[key] = Param(pname, slot.type_text, list(values), [], slot.tag)
            params.append(param)
        param.slots.append(index)
    varying = sum(1 for p in params if p.varying)
    if varying > MAX_VARYING:
        raise refuse(
            "Различий не больше четырёх",
            f"вхождения различаются в {varying} местах, параметром можно сделать не больше {MAX_VARYING}",
        )
    if len(params) > MAX_PARAMS:
        raise refuse(
            "Параметров не больше восьми",
            f"общему методу потребовалось бы {len(params)} параметров",
        )

    result_expr = ""
    if out is not None:
        if declared_inside:
            result_expr = out.name
        else:
            names = tuple(o.flow.outputs[0].name for o in group)
            holder = next((p for p in params if p.kind == "var" and tuple(p.values) == names), None)
            if holder is None:
                raise refuse(
                    "Результат передаётся параметром",
                    f"переменные {', '.join(names)} не соответствуют друг другу",
                )
            result_expr = holder.name

    wanted = name.strip() if name else _name_for(base)
    try:
        wanted = check_fragment("identifier", wanted, "name")
    except EditError as error:
        raise refuse("Имя метода — идентификатор", error.message) from error
    method_name = _unique_name(project, base.member, wanted, len(params))

    file = base.place.file
    source = project.files[file].encode("utf-8")
    unit = indent_unit(project.files[file])
    method_indent = line_indent(source, base.member.start_byte)
    body_indent = line_indent(source, base.start)
    replacements = sorted(
        ((base.slots[i].start, base.slots[i].end, p.name) for p in params for i in p.slots),
        reverse=True,
    )
    body_bytes = bytearray(source[base.start : base.end])
    for a, b, text in replacements:
        body_bytes[a - base.start : b - base.start] = text.encode("utf-8")
    text = body_indent + body_bytes.decode("utf-8")
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
    if out is not None:
        body += f"\n{inner}return {result_expr};"
    modifiers = "private" + ("" if needs_instance else " static")
    generic = f"<{', '.join(type_params)}> " if type_params else ""
    throws = f" throws {', '.join(flow.throws)}" if flow.throws else ""
    signature = f"{method_name}({', '.join(f'{p.type_text} {p.name}' for p in params)})"
    code = f"{method_indent}{modifiers} {generic}{returns} {signature}{throws} {{\n{body}\n{method_indent}}}"

    edits: list[TextEdit] = []
    for k, o in enumerate(group):
        args = ", ".join(p.values[k] for p in params)
        if flow.tail_return:
            call = (
                f"{method_name}({args});\n{line_indent(project.files[o.place.file].encode('utf-8'), o.start)}return;"
                if flow.tail_void
                else f"return {method_name}({args});"
            )
        elif out is not None:
            variable = o.flow.outputs[0]
            prefix = f"{variable.type_text} " if declared_inside else ""
            call = f"{prefix}{variable.name} = {method_name}({args});"
        else:
            call = f"{method_name}({args});"
        edits.append(TextEdit(o.place.file, o.start, o.end, call))
    edits.append(TextEdit(file, base.member.end_byte, base.member.end_byte, "\n\n" + code))
    return DedupePlan(
        method_name,
        f"{returns} {signature}{throws}",
        code,
        edits,
        params,
        [o.place for o in group],
        notes,
    )


def checks_for(result: DedupePlan) -> list[Check]:
    varying = [p for p in result.params if p.varying]
    checks = [
        Check(
            f"Заменяется вхождений: {len(result.occurrences)}",
            True,
            ", ".join(_where(p) for p in result.occurrences),
        ),
        Check("Вхождения совпадают с точностью до имён и литералов", True),
        Check(
            f"Различающихся мест: {len(varying)} (не больше {MAX_VARYING})",
            True,
            ", ".join(f"{p.type_text} {p.name}" for p in varying) or None,
        ),
        Check("Результат и проверяемые исключения согласованы", True),
    ]
    checks.extend(
        Check(f"Пропущено вхождение {note.split(': ', 1)[0]}", True, note.split(": ", 1)[-1])
        for note in result.notes
    )
    return checks
