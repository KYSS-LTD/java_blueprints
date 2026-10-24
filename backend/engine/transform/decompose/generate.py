"""Генерация кода по плану декомпозиции (S9.7, docs/06 § 6.1 этап F).

Стратегия «ядро хранит состояние»: поля, конструкторы и ``main`` остаются в исходном классе (ядре);
методы кластера переезжают в новый класс, который получает ссылку ``core`` на ядро. Обращения из
перенесённого кода к полям и методам ядра квалифицируются (``core.x``, ``Main.x``), обращения к
перенесённым методам — через поле ядра (``parser.parse()``) или имя класса (``Parser.parse()``).
Члены пишутся как текст — с комментариями и аннотациями; правки точечные, остальной текст не меняется.
"""

from __future__ import annotations

from dataclasses import dataclass, field

from tree_sitter import Node

from engine.edit.core import (
    TextEdit,
    apply_text_edits,
    check_syntax,
    indent_unit,
    ts_node,
    valid_identifier,
)
from engine.edit.operations import _with_leading_comments, removal_range
from engine.java import cst
from engine.java.project import FLOWABLE, JavaProject, get_project
from engine.java.symbols import Member, TypeDecl
from engine.transform.decompose.plan import Plan, inner_classes, pinned


class DecomposeError(Exception):
    """План нельзя применить; ``message`` — причина для пользователя."""

    def __init__(self, message: str) -> None:
        super().__init__(message)
        self.message = message


@dataclass(slots=True)
class Generated:
    files: dict[str, str]
    changed: list[str]
    moves: dict[str, str] = field(default_factory=dict)  # id члена → новый класс
    warnings: list[str] = field(default_factory=list)


def _lower_first(name: str) -> str:
    return name[:1].lower() + name[1:]


def validate(project: JavaProject, plan: Plan) -> TypeDecl:
    """Проверка целостности плана; возвращает декомпозируемый тип."""
    decl = next((t for t in project.types() if t.id == plan.type_id), None)
    if decl is None:
        raise DecomposeError(f"класс {plan.type_id} не найден")
    ids = {m.id for m in decl.members}
    seen: dict[str, str] = {}
    cores = [c for c in plan.classes if c.role == "core"]
    if len(cores) != 1:
        raise DecomposeError("в плане должен быть ровно один класс-ядро")
    if cores[0].name != decl.name:
        raise DecomposeError(f"ядро должно называться {decl.name}")
    existing = {t.name for t in project.types()}
    names: set[str] = set()
    detachable = {t.id for t in project.symbols(decl.id.split("#", 1)[0]).types if t.id != decl.id}
    for cls in plan.classes:
        if cls.role == "model":  # тип выносится в свой файл целиком
            for type_id in cls.members:
                if type_id not in detachable:
                    raise DecomposeError(f"тип {type_id} нельзя вынести в отдельный файл")
            continue
        if cls.role != "core":
            if not valid_identifier(cls.name):
                raise DecomposeError(f"«{cls.name}» не годится как имя класса")
            if cls.name in existing or cls.name in names:
                raise DecomposeError(f"имя класса «{cls.name}» уже занято")
            names.add(cls.name)
        for member_id in cls.members:
            if member_id not in ids:
                raise DecomposeError(f"член {member_id} не принадлежит {decl.name}")
            if member_id in seen:
                raise DecomposeError(f"член {member_id} указан в двух классах")
            seen[member_id] = cls.name
    missing = ids - set(seen)
    if missing:
        raise DecomposeError(f"в плане не хватает членов: {', '.join(sorted(missing)[:3])}")
    stay = pinned(project, decl)
    for member_id, reason in stay.items():
        if seen[member_id] != decl.name:
            raise DecomposeError(f"член {member_id} нельзя переносить: {reason}")
    if any(not c.members for c in plan.classes if c.role not in {"core", "model"}):
        raise DecomposeError("в плане есть класс без методов")
    return decl


@dataclass(slots=True)
class _Edit:
    start: int
    end: int
    text: str


class _Rewriter:
    """Переписывание обращений к членам монолита по плану."""

    def __init__(self, project: JavaProject, plan: Plan, decl: TypeDecl) -> None:
        self.project = project
        self.decl = decl
        self.core = decl.name
        self.home = {m_id: c.name for c in plan.classes for m_id in c.members}
        self.members = {m.id: m for m in decl.members}
        self.cluster_field = self._field_names(plan)
        self.expose: set[str] = set()
        self.nested = {t.name: t for t in decl.nested if t.name not in inner_classes(decl)}
        self.expose_types: set[str] = set()
        self.warnings: list[str] = []

    def _field_names(self, plan: Plan) -> dict[str, str]:
        used = {m.name for m in self.decl.members if m.kind == "field"}
        names: dict[str, str] = {}
        for cls in plan.classes:
            if cls.role == "core":
                continue
            candidate, index = _lower_first(cls.name), 2
            while candidate in used:
                candidate = f"{_lower_first(cls.name)}{index}"
                index += 1
            used.add(candidate)
            names[cls.name] = candidate
        return names

    def path(self, caller: str, target: str, static: bool) -> str:
        if caller == target:
            return ""
        if static:
            return f"{target}."
        parts = ["core"] if caller != self.core else []
        if target != self.core:
            parts.append(self.cluster_field[target])
        return "".join(f"{p}." for p in parts)

    def rewrites(self, member: Member, caller: str | None) -> list[_Edit]:
        """Правки текста тела члена; ``caller`` — класс, где член окажется (``None`` — вне монолита)."""
        file = self.project.file_of(member)
        ctx, declaration = self.project.context(member)
        if declaration is None:
            return []
        parsed = ctx.parsed
        source = self.project.files[file].encode("utf-8")
        text = lambda n: source[n.start_byte : n.end_byte].decode("utf-8")  # noqa: E731
        edits: list[_Edit] = []
        for node in cst.walk(declaration):
            if node.type == "method_invocation":
                edits.extend(self._call(node, ctx, caller, text, member))
            elif node.type == "field_access" and caller not in (None, self.core):
                edits.extend(self._field_access(node, ctx, text))
            elif node.type == "identifier" and caller not in (None, self.core):
                edits.extend(self._identifier(node, ctx, text, member))
            elif node.type == "type_identifier" and caller not in (None, self.core):
                edits.extend(self._nested_type(node, text))
            elif node.type == "this" and caller not in (None, self.core):
                parent = node.parent
                qualified = parent is not None and (
                    (parent.type == "field_access" and parent.child_by_field_name("object") == node)
                    or (
                        parent.type == "method_invocation"
                        and parent.child_by_field_name("object") == node
                    )
                )
                if not qualified:
                    edits.append(_Edit(node.start_byte, node.end_byte, "core"))
        del parsed
        return edits

    def _call(self, node: Node, ctx, caller, text, member) -> list[_Edit]:  # type: ignore[no-untyped-def]
        targets = [t for t in ctx.resolve_call(node) if t.id in self.home]
        if not targets:
            return []
        homes = {self.home[t.id] for t in targets}
        if len(homes) > 1:
            raise DecomposeError(f"вызов {text(node)[:40]} разрешается в методы разных классов")
        target_home = next(iter(homes))
        static = all(t.is_static for t in targets)
        name_node = node.child_by_field_name("name")
        receiver = node.child_by_field_name("object")
        assert name_node is not None
        if target_home != caller:
            self.expose.update(t.id for t in targets)
        if receiver is None:
            if caller is None:
                return []
            prefix = self.path(caller, target_home, static)
            return [_Edit(name_node.start_byte, name_node.start_byte, prefix)] if prefix else []
        if receiver.type == "this":
            if caller is None:
                return []
            prefix = self.path(caller, target_home, static)
            if prefix == "":
                return []
            return [_Edit(receiver.start_byte, name_node.start_byte, prefix)]
        if receiver.type == "identifier" and text(receiver) == self.decl.name and static:
            if target_home != self.core:
                return [_Edit(receiver.start_byte, receiver.end_byte, target_home)]
            return []
        if not static and ctx.expr_type(receiver) == self.decl.name and target_home != self.core:
            return [
                _Edit(receiver.end_byte, receiver.end_byte, "." + self.cluster_field[target_home])
            ]
        return []

    def _field_access(self, node: Node, ctx, text) -> list[_Edit]:  # type: ignore[no-untyped-def]
        receiver = node.child_by_field_name("object")
        field_node = node.child_by_field_name("field")
        if receiver is None or field_node is None or receiver.type != "this":
            return []
        found = ctx.table.field_type(ctx.owner, text(field_node))
        if found is None or found[0].id not in self.home:
            return []
        self.expose.add(found[0].id)
        return [_Edit(receiver.start_byte, receiver.end_byte, "core")]

    def _nested_type(self, node: Node, text) -> list[_Edit]:  # type: ignore[no-untyped-def]
        """Вложенный тип монолита (``Kind``) в перенесённом коде пишется как ``Main.Kind``."""
        parent = node.parent
        name = text(node)
        if name not in self.nested or (
            parent is not None and parent.type == "scoped_type_identifier"
        ):
            return []
        self.expose_types.add(self.nested[name].id)
        return [_Edit(node.start_byte, node.start_byte, f"{self.decl.name}.")]

    def _identifier(self, node: Node, ctx, text, member) -> list[_Edit]:  # type: ignore[no-untyped-def]
        parent = node.parent
        if parent is None:
            return []
        if parent.type == "method_invocation" and parent.child_by_field_name("name") == node:
            return []
        if parent.type == "field_access" and parent.child_by_field_name("field") == node:
            return []
        if parent.type in {
            "variable_declarator",
            "formal_parameter",
            "spread_parameter",
            "catch_formal_parameter",
            "scoped_identifier",
            "labeled_statement",
            "break_statement",
            "continue_statement",
        }:
            if parent.type == "variable_declarator" and parent.child_by_field_name("name") == node:
                return []
            if parent.type != "variable_declarator":
                return []
        if parent.type == "field_access" and parent.child_by_field_name("object") == node:
            # ``Main.CONST`` и ``obj.f``: имя типа или значение, не поле монолита
            pass
        name = text(node)
        if name in ctx.scope:
            return []
        if (
            name in self.nested
            and parent.type in {"field_access", "method_invocation"}
            and parent.child_by_field_name("object") == node
        ):
            self.expose_types.add(self.nested[name].id)
            return [_Edit(node.start_byte, node.start_byte, f"{self.decl.name}.")]
        found = ctx.table.field_type(ctx.owner, name)
        if found is None or found[0].id not in self.home:
            return []
        field_member = found[0]
        self.expose.add(field_member.id)
        prefix = f"{self.decl.name}." if field_member.is_static else "core."
        return [_Edit(node.start_byte, node.end_byte, prefix + name)]


def _remove_private(member_node: Node, source: bytes, base: int) -> list[_Edit]:
    modifiers = cst.first_child_of_type(member_node, "modifiers")
    if modifiers is None:
        return []
    for child in modifiers.children:
        if child.type == "private":
            end = child.end_byte + (1 if source[child.end_byte : child.end_byte + 1] == b" " else 0)
            return [_Edit(child.start_byte, end, "")]
    del base
    return []


def _indent(source: bytes, node: Node) -> str:
    start = source.rfind(b"\n", 0, node.start_byte) + 1
    return source[start : node.start_byte].decode("utf-8")


def _apply(source: str, base: int, edits: list[_Edit]) -> str:
    """Применяет правки с абсолютными смещениями к фрагменту, начинающемуся в байте ``base``."""
    data = bytearray(source.encode("utf-8"))
    for edit in sorted(edits, key=lambda e: (e.start, e.end), reverse=True):
        data[edit.start - base : edit.end - base] = edit.text.encode("utf-8")
    return data.decode("utf-8")


def generate(project: JavaProject, plan: Plan) -> Generated:
    decl = validate(project, plan)
    file = decl.id.split("#", 1)[0]
    source_text = project.files[file]
    source = source_text.encode("utf-8")
    unit = indent_unit(source_text)
    rewriter = _Rewriter(project, plan, decl)
    home = rewriter.home
    moved_classes = [c for c in plan.classes if c.role not in {"core", "model"}]
    type_moves = [c for c in plan.classes if c.role == "model"]
    moved_ids = {m for c in moved_classes for m in c.members}
    node_of = {m.id: ts_node(project, file, m.start_byte, m.end_byte) for m in decl.members}

    # 1. правки тел всех членов монолита и внешних вызывающих
    body_edits: dict[str, list[_Edit]] = {
        m.id: rewriter.rewrites(m, home[m.id])
        for m in decl.members
        if m.kind in {*FLOWABLE, "field"}
    }
    outside: dict[str, list[_Edit]] = {}
    for other in project.members():
        if other.id in home or other.kind not in {*FLOWABLE, "field"}:
            continue
        edits = rewriter.rewrites(other, None)
        if edits:
            outside.setdefault(project.file_of(other), []).extend(edits)

    imports = [
        source[n.start_byte : n.end_byte].decode("utf-8")
        for n in project.parsed(file).root.children
        if n.type == "import_declaration"
    ]
    package = next(
        (
            source[n.start_byte : n.end_byte].decode("utf-8")
            for n in project.parsed(file).root.children
            if n.type == "package_declaration"
        ),
        None,
    )

    # 2. новые файлы
    files = dict(project.files)
    for cls in moved_classes:
        chunks: list[str] = []
        for member_id in cls.members:
            node = node_of[member_id]
            begin = _with_leading_comments(source, node)
            fragment = source[begin : node.end_byte].decode("utf-8")
            edits = list(body_edits.get(member_id, [])) + _remove_private(node, source, begin)
            text = _apply(
                fragment, begin, [e for e in edits if begin <= e.start and e.end <= node.end_byte]
            )
            chunks.append(_indent(source, node) + text)
        header = (f"{package}\n\n" if package else "") + (
            "\n".join(imports) + "\n\n" if imports else ""
        )
        constructor = (
            f"{unit}{cls.name}({decl.name} core) {{\n{unit}{unit}this.core = core;\n{unit}}}"
        )
        body = f"{unit}private final {decl.name} core;\n\n{constructor}\n\n" + "\n\n".join(chunks)
        files[cls.file] = f"{header}class {cls.name} {{\n{body}\n}}\n"

    # 2б. типы, вынесенные в отдельные файлы
    file_edits: list[TextEdit] = []
    removals: list[tuple[int, int]] = []
    header = (f"{package}\n\n" if package else "") + (
        "\n".join(imports) + "\n\n" if imports else ""
    )
    for cls in type_moves:
        for type_id in cls.members:
            type_decl = next(t for t in project.symbols(file).types if t.id == type_id)
            type_node = ts_node(project, file, type_decl.start_byte, type_decl.end_byte)
            begin = _with_leading_comments(source, type_node)
            fragment = source[begin : type_node.end_byte].decode("utf-8")
            inner = [
                e for e in outside.get(file, []) if begin <= e.start and e.end <= type_node.end_byte
            ]
            files[cls.file] = header + _apply(fragment, begin, inner) + "\n"
            start, finish = removal_range(source, type_node)
            removals.append((start, finish))
            file_edits.append(TextEdit(file, start, finish, ""))

    # 3. исходный файл: убрать перенесённое, переписать обращения, добавить поля кластеров
    for member in decl.members:
        node = node_of[member.id]
        if member.id in moved_ids:
            begin, finish = removal_range(source, node)
            removals.append((begin, finish))
            file_edits.append(TextEdit(file, begin, finish, ""))
        else:
            for edit in body_edits.get(member.id, []):
                file_edits.append(TextEdit(file, edit.start, edit.end, edit.text))
    for edit in outside.get(file, []):
        if not any(a <= edit.start < b for a, b in removals):
            file_edits.append(TextEdit(file, edit.start, edit.end, edit.text))
    for member_id in rewriter.expose:
        exposed = rewriter.members.get(member_id)
        if exposed is not None and member_id not in moved_ids and home[member_id] == decl.name:
            for edit in _remove_private(node_of[member_id], source, 0):
                file_edits.append(TextEdit(file, edit.start, edit.end, edit.text))
    for nested in decl.nested:
        if nested.id in rewriter.expose_types:  # вложенный тип виден из новых классов
            spans = [(nested.start_byte, nested.end_byte)]
            spans.extend((m.start_byte, m.end_byte) for m in nested.members)
            for begin, finish in spans:
                for edit in _remove_private(ts_node(project, file, begin, finish), source, 0):
                    file_edits.append(TextEdit(file, edit.start, edit.end, edit.text))
    decl_node = ts_node(project, file, decl.start_byte, decl.end_byte)
    class_body = decl_node.child_by_field_name("body")
    if class_body is None:
        raise DecomposeError("у класса нет тела")
    fields = "".join(
        f"\n{unit}final {c.name} {rewriter.cluster_field[c.name]} = new {c.name}(this);"
        for c in moved_classes
    )
    insert_at = class_body.start_byte + 1
    file_edits.append(TextEdit(file, insert_at, insert_at, fields + "\n"))

    for path, edits in outside.items():
        if path == file:
            continue
        files[path] = apply_text_edits(
            files, [TextEdit(path, e.start, e.end, e.text) for e in edits]
        )[path]
    try:
        files[file] = apply_text_edits({file: source_text}, _dedupe(file_edits))[file]
    except Exception as exc:  # noqa: BLE001 - пересекающиеся правки означают нераспознанный случай
        raise DecomposeError(f"не удалось безопасно переписать код: {exc}") from exc

    result_project = get_project(files)
    try:
        check_syntax(project, result_project, [file, *[c.file for c in moved_classes]])
    except Exception as exc:  # noqa: BLE001
        raise DecomposeError(str(exc)) from exc
    changed = sorted(p for p in files if files[p] != project.files.get(p))
    moves = {m_id: home[m_id] for m_id in moved_ids}
    warnings = list(rewriter.warnings)
    return Generated(files, changed, moves, warnings)


def _dedupe(edits: list[TextEdit]) -> list[TextEdit]:
    seen: set[tuple[int, int, str]] = set()
    result = []
    for edit in edits:
        key = (edit.start, edit.end, edit.text)
        if key not in seen:
            seen.add(key)
            result.append(edit)
    return result


__all__ = ["DecomposeError", "Generated", "generate", "validate"]
