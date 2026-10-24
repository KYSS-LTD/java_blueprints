"""Основа правок: ``TextEdit``, применение, проверка синтаксиса, отступы, поиск узлов CST (S5.2)."""

from __future__ import annotations

import re
from collections.abc import Iterable, Mapping
from dataclasses import dataclass

from tree_sitter import Node

from engine.graph.ranges import Position
from engine.java.project import JavaProject, get_project
from engine.java.ts import parse

KEYWORDS = frozenset(
    [
        "abstract",
        "assert",
        "boolean",
        "break",
        "byte",
        "case",
        "catch",
        "char",
        "class",
        "const",
        "continue",
        "default",
        "do",
        "double",
        "else",
        "enum",
        "extends",
        "final",
        "finally",
        "float",
        "for",
        "goto",
        "if",
        "implements",
        "import",
        "instanceof",
        "int",
        "interface",
        "long",
        "native",
        "new",
        "package",
        "private",
        "protected",
        "public",
        "return",
        "short",
        "static",
        "strictfp",
        "super",
        "switch",
        "synchronized",
        "this",
        "throw",
        "throws",
        "transient",
        "try",
        "void",
        "volatile",
        "while",
        "true",
        "false",
        "null",
    ]
)
_IDENTIFIER = re.compile(r"^[^\W\d][\w$]*$|^\$[\w$]*$")


class EditError(Exception):
    """Ожидаемая ошибка операции: код из docs/08-api.md § 5 и место в форме или коде."""

    def __init__(
        self,
        code: str,
        message: str,
        *,
        field: str | None = None,
        line: int | None = None,
        column: int | None = None,
        op_index: int = 0,
    ) -> None:
        super().__init__(message)
        self.code = code
        self.message = message
        self.field = field
        self.line = line
        self.column = column
        self.op_index = op_index


def invalid(message: str, field: str | None = None, **where: int) -> EditError:
    return EditError("INVALID_FRAGMENT", message, field=field, **where)


def precondition(message: str) -> EditError:
    return EditError("PRECONDITION_FAILED", message)


def not_found(message: str) -> EditError:
    return EditError("NOT_FOUND", message)


def unsupported(message: str) -> EditError:
    return EditError("UNSUPPORTED", message)


@dataclass(frozen=True, slots=True)
class TextEdit:
    """Замена байтов ``[start, end)`` файла текстом."""

    file: str
    start: int
    end: int
    text: str


def apply_text_edits(files: Mapping[str, str], edits: Iterable[TextEdit]) -> dict[str, str]:
    """Применяет правки к копии файлов в порядке убывания смещений; пересечения запрещены."""
    result = dict(files)
    by_file: dict[str, list[TextEdit]] = {}
    for edit in edits:
        by_file.setdefault(edit.file, []).append(edit)
    for path, items in by_file.items():
        data = result[path].encode("utf-8")
        ordered = sorted(items, key=lambda e: (e.start, e.end), reverse=True)
        for earlier, later in zip(ordered[1:], ordered, strict=False):
            if earlier.end > later.start:
                raise precondition(f"правки в {path} пересекаются")
        for edit in ordered:
            if not 0 <= edit.start <= edit.end <= len(data):
                raise precondition(f"неверный диапазон правки в {path}")
            data = data[: edit.start] + edit.text.encode("utf-8") + data[edit.end :]
        result[path] = data.decode("utf-8")
    return result


def syntax_error_count(project: JavaProject, path: str) -> int:
    return len(project.parsed(path).issues) if path in project.files else 0


def check_syntax(before: JavaProject, after: JavaProject, changed: Iterable[str]) -> None:
    """После правки в изменённых файлах не должно прибавиться ``ERROR``/``MISSING``."""
    for path in changed:
        if path not in after.files:
            continue
        issues = after.parsed(path).issues
        if len(issues) > syntax_error_count(before, path):
            first = issues[0]
            raise invalid(
                f"после правки файл {path} не разбирается: {first.message}",
                line=first.range.start.line,
                column=first.range.start.column,
            )


# --- отступы -------------------------------------------------------------------------------------


def line_start(data: bytes, offset: int) -> int:
    return data.rfind(b"\n", 0, offset) + 1


def line_indent(data: bytes, offset: int) -> str:
    start = line_start(data, offset)
    match = re.match(rb"[ \t]*", data[start:])
    return match.group(0).decode() if match else ""


def indent_unit(source: str) -> str:
    """Единица отступа файла: таб или ширина по минимальному положительному отступу."""
    widths: list[int] = []
    tabs = 0
    for line in source.split("\n"):
        stripped = line.lstrip(" \t")
        # продолжение блочного комментария (` * текст`) выровнено на пробел и не показывает отступ кода
        if not stripped or stripped.startswith("*"):
            continue
        lead = line[: len(line) - len(stripped)]
        if lead.startswith("\t"):
            tabs += 1
        elif lead:
            widths.append(len(lead))
    if tabs > len(widths):
        return "\t"
    width = min(widths, default=4)
    return " " * (width if 1 <= width <= 8 else 4)


def indented(text: str, prefix: str) -> str:
    """Добавляет ``prefix`` к каждой непустой строке многострочного текста."""
    return "\n".join(prefix + line if line else line for line in text.split("\n"))


# --- узлы CST ---------------------------------------------------------------------------------------


def offset_of(project: JavaProject, path: str, line: int, column: int) -> int:
    return project.parsed(path).lines.offset(Position(line, column))


def node_span(project: JavaProject, node_id: str) -> tuple[str, int, int]:
    """Файл и байтовый диапазон узла графа (метода, блока схемы, члена, типа)."""
    from engine.graph.ids import split_node_id

    member_id, path = split_node_id(node_id)
    file = member_id.split("#", 1)[0]
    if file not in project.files:
        raise not_found(f"файл узла {node_id} не найден")
    if not path:
        member = project.member(member_id)
        if member is not None:
            return file, member.start_byte, member.end_byte
        for decl in project.types():
            if decl.id == node_id:
                return file, decl.start_byte, decl.end_byte
        raise not_found(f"узел {node_id} не найден")
    graph = project.flow(member_id)
    flow_node = next((n for n in graph.nodes if n.id == node_id), None) if graph else None
    if flow_node is None:
        raise not_found(f"узел {node_id} не найден")
    span = flow_node.range
    return (
        file,
        offset_of(project, file, span.start_line, span.start_col),
        offset_of(project, file, span.end_line, span.end_col),
    )


def ts_node(project: JavaProject, file: str, start: int, end: int) -> Node:
    """Узел CST ровно с диапазоном ``[start, end)`` (самый крупный из совпадающих)."""
    node = project.parsed(file).root.descendant_for_byte_range(start, end)
    if node is None:
        raise not_found("узел кода не найден")
    while (
        node.parent is not None and node.parent.start_byte == start and node.parent.end_byte == end
    ):
        node = node.parent
    if node.start_byte != start or node.end_byte != end:
        raise not_found("узел блок-схемы не совпал с узлом кода")
    return node


def reparse(files: Mapping[str, str]) -> JavaProject:
    return get_project(files)


def valid_identifier(name: str) -> bool:
    return bool(_IDENTIFIER.match(name)) and name not in KEYWORDS


# --- проверка фрагментов ---------------------------------------------------------------------------------


def check_fragment(kind: str, text: str, field: str) -> str:
    """Проверяет выражение, тип или идентификатор и возвращает очищенный текст."""
    cleaned = text.strip()
    if not cleaned:
        raise invalid("поле не должно быть пустым", field)
    if kind == "identifier":
        if not valid_identifier(cleaned):
            raise invalid(f"«{cleaned}» не годится как идентификатор", field)
        return cleaned
    if kind == "expression":
        prefix, suffix = "class _V { void _m() { var _x = (", "\n); } }"
    elif kind == "type":
        prefix, suffix = "class _V { void _m() { ", " _x; } }"
    else:
        raise unsupported(f"неизвестный вид фрагмента: {kind}")
    source = prefix + cleaned + suffix
    parsed = parse(source)
    start = len(prefix.encode("utf-8"))
    end = start + len(cleaned.encode("utf-8"))
    if parsed.issues:
        first = parsed.issues[0]
        raise invalid(
            f"{'выражение' if kind == 'expression' else 'тип'} не разбирается: {first.message}",
            field,
        )
    node = parsed.root.descendant_for_byte_range(start, end)
    # фрагмент целиком — один узел нужного вида, а не несколько операторов с закрывающими скобками
    while (
        node is not None
        and node.parent is not None
        and node.parent.start_byte >= start
        and node.parent.end_byte <= end
    ):
        node = node.parent
    if node is None or node.start_byte != start or node.end_byte != end:
        raise invalid(
            f"{'выражение' if kind == 'expression' else 'тип'} не разбирается как одно целое", field
        )
    return cleaned
