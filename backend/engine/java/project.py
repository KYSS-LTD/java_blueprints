"""Фасад над разбором: файлы проекта → символы, блок-схемы, структура, поиск по позиции.

Всё считается лениво и кешируется по ``content_hash`` (docs/02 § 5.3): для 1000 строк пересчёт
стоит десятки миллисекунд, кеш нужен для частых запросов интерфейса.
"""

from __future__ import annotations

import hashlib
from collections import Counter, OrderedDict
from collections.abc import Callable, Iterator, Mapping
from dataclasses import dataclass
from typing import TypeVar, cast

from tree_sitter import Node

from engine.graph import flow as flow_module
from engine.graph import structure as structure_module
from engine.graph.ids import split_node_id
from engine.graph.model import (
    ApiRange,
    FlowGraph,
    FlowNode,
    ProjectTree,
    StructureGraph,
)
from engine.graph.ranges import Position
from engine.java import cst
from engine.java.semantic import Context, SymbolTable, declared_types
from engine.java.symbols import FileSymbols, Member, TypeDecl, base_name, extract_symbols
from engine.java.ts import ParsedFile, SyntaxIssue, parse

FLOWABLE = {"method", "constructor", "initializer"}
_REFERENCE_PARENTS_TO_SKIP = {
    "variable_declarator",
    "formal_parameter",
    "spread_parameter",
    "catch_formal_parameter",
    "enhanced_for_statement",
    "resource",
    "labeled_statement",
    "break_statement",
    "continue_statement",
}


@dataclass(frozen=True, slots=True)
class Location:
    """Результат ``locate``: узел графа и метод, в котором он лежит."""

    node_id: str
    method_id: str | None


def content_hash_of(files: Mapping[str, str]) -> str:
    digest = hashlib.sha256()
    for path in sorted(files):
        digest.update(path.encode("utf-8"))
        digest.update(b"\0")
        digest.update(files[path].encode("utf-8"))
        digest.update(b"\0")
    return digest.hexdigest()


T = TypeVar("T")


class JavaProject:
    """Набор Java-файлов одной версии проекта."""

    def __init__(self, files: Mapping[str, str]) -> None:
        self.files: dict[str, str] = dict(sorted(files.items()))
        self.content_hash = content_hash_of(self.files)
        self._parsed: dict[str, ParsedFile] = {}
        self._symbols: dict[str, FileSymbols] = {}
        self._table: SymbolTable | None = None
        self._flows: dict[str, FlowGraph] = {}
        self._contexts: dict[str, tuple[Context, Node | None]] = {}
        self._calls: dict[str, Counter[str]] = {}
        self._uses: dict[str, tuple[Counter[str], Counter[str]]] = {}
        self._memo: dict[tuple[str, str], object] = {}

    # --- файлы и символы ---------------------------------------------------------------------

    def parsed(self, path: str) -> ParsedFile:
        if path not in self._parsed:
            self._parsed[path] = parse(self.files[path])
        return self._parsed[path]

    def symbols(self, path: str) -> FileSymbols:
        if path not in self._symbols:
            self._symbols[path] = extract_symbols(self.parsed(path), path)
        return self._symbols[path]

    @property
    def table(self) -> SymbolTable:
        if self._table is None:
            self._table = SymbolTable(self.symbols(path) for path in self.files)
        return self._table

    def memo(self, kind: str, key: str, make: Callable[[], T]) -> T:
        """Результат ``make()``, посчитанный один раз на пару (вид, ключ): файлы версии не меняются."""
        if (kind, key) not in self._memo:
            self._memo[(kind, key)] = make()
        return cast(T, self._memo[(kind, key)])

    def syntax_issues(self) -> dict[str, list[SyntaxIssue]]:
        """Синтаксические ошибки по файлам (только файлы с ошибками)."""
        return {path: self.parsed(path).issues for path in self.files if self.parsed(path).issues}

    def types(self) -> Iterator[TypeDecl]:
        for path in self.files:
            yield from self.symbols(path).all_types()

    def members(self) -> Iterator[Member]:
        for path in self.files:
            yield from self.symbols(path).all_members()

    def member(self, member_id: str) -> Member | None:
        return self.table.members.get(member_id)

    def main_classes(self) -> list[str]:
        found: list[str] = []
        for path in self.files:
            found.extend(self.symbols(path).main_classes)
        return found

    # --- объявления и контекст --------------------------------------------------------------

    def file_of(self, member: Member) -> str:
        return member.id.split("#", 1)[0]

    def context(self, member: Member) -> tuple[Context, Node | None]:
        """Контекст разрешения имён для члена и его узел объявления (``None`` — узел не найден)."""
        cached = self._contexts.get(member.id)
        if cached is not None:
            return cached
        path = self.file_of(member)
        parsed = self.parsed(path)
        owner = self.table.owner_of[member.id]
        declaration = cst.find_node(
            parsed, member.start_byte, member.end_byte, frozenset({member.decl_type})
        )
        scope: dict[str, str] = {}
        if declaration is not None and member.kind != "field":
            body = declaration.child_by_field_name("body") or declaration
            scope = declared_types(parsed, body, [(p.name, p.type) for p in member.params])
        ctx = Context(parsed, self.table, owner, scope)
        self._contexts[member.id] = (ctx, declaration)
        return ctx, declaration

    # --- блок-схемы --------------------------------------------------------------------------

    def flow(self, member_id: str) -> FlowGraph | None:
        """Блок-схема метода, конструктора или инициализатора (``None`` — такого члена нет)."""
        if member_id in self._flows:
            return self._flows[member_id]
        member = self.member(member_id)
        if member is None or member.kind not in FLOWABLE:
            return None
        ctx, declaration = self.context(member)
        path = self.file_of(member)
        graph: FlowGraph
        if declaration is None:
            graph = self._fallback_flow(member, path, "узел объявления не найден")
        else:
            try:
                builder = flow_module.FlowBuilder(
                    self.parsed(path), path, member, declaration, ctx, self.content_hash
                )
                graph = builder.build()
            except Exception as exc:  # noqa: BLE001 - на сломанном коде граф не должен падать
                graph = self._fallback_flow(member, path, f"{type(exc).__name__}: {exc}")
        self._flows[member_id] = graph
        return graph

    def _fallback_flow(self, member: Member, path: str, reason: str) -> FlowGraph:
        parsed = self.parsed(path)
        span = ApiRange.of(path, parsed.lines.range(member.start_byte, member.end_byte))
        root = FlowNode(
            id=member.id,
            kind="method",
            label="Метод",
            code=cst.short(member.key),
            range=span,
            data={"name": member.name, "member_kind": member.kind},
            errors=[reason],
        )
        code = FlowNode(
            id=f"{member.id}/code[0]",
            kind="code",
            label="Код",
            code=cst.short(
                parsed.source[member.start_byte : member.end_byte].decode("utf-8", "replace")
            ),
            range=span,
            parent_id=None,
            data={"text": ""},
            errors=[reason],
        )
        graph = FlowGraph(member.id, member.key, path, [root, code], [], member.id, [code.id])
        graph.content_hash = self.content_hash
        return graph

    def flows(self) -> Iterator[FlowGraph]:
        for member in self.members():
            if member.kind in FLOWABLE:
                graph = self.flow(member.id)
                if graph is not None:
                    yield graph

    # --- связи между членами -------------------------------------------------------------------

    def call_counts(self, member: Member) -> Counter[str]:
        """Сколько раз член вызывает методы и конструкторы проекта (id вызываемого → число)."""
        if member.id in self._calls:
            return self._calls[member.id]
        counts: Counter[str] = Counter()
        if member.kind in FLOWABLE:
            ctx, declaration = self.context(member)
            if declaration is not None:
                for node in cst.walk(declaration):
                    if node.type == "method_invocation":
                        counts.update(m.id for m in ctx.resolve_call(node))
                    elif node.type == "object_creation_expression":
                        counts.update(m.id for m in ctx.resolve_creation(node))
                    elif node.type == "explicit_constructor_invocation":
                        counts.update(m.id for m in ctx.resolve_explicit_constructor(node))
        self._calls[member.id] = counts
        return counts

    def use_counts(self, member: Member) -> tuple[Counter[str], Counter[str]]:
        """``(поля, типы)``, на которые ссылается член: id поля/типа → число упоминаний."""
        if member.id in self._uses:
            return self._uses[member.id]
        fields: Counter[str] = Counter()
        types: Counter[str] = Counter()
        if member.kind in FLOWABLE:
            ctx, declaration = self.context(member)
            if declaration is not None:
                self._count_uses(member, ctx, declaration, fields, types)
        self._uses[member.id] = (fields, types)
        return fields, types

    def _count_uses(
        self,
        member: Member,
        ctx: Context,
        declaration: Node,
        fields: Counter[str],
        types: Counter[str],
    ) -> None:
        table = self.table
        parsed = ctx.parsed
        for node in cst.walk(declaration):
            kind = node.type
            if kind == "type_identifier":
                decl = table.type_named(parsed.text(node), ctx.owner)
                if decl is not None and decl.id != ctx.owner.id:
                    types[decl.id] += 1
            elif kind == "field_access":
                target = node.child_by_field_name("object")
                name = cst.text_of(parsed, node.child_by_field_name("field"))
                found = None
                if target is not None and target.type == "this":
                    found = table.field_type(ctx.owner, name)
                elif target is not None and target.type == "identifier":
                    ident = parsed.text(target)
                    if ident not in ctx.scope:
                        decl = table.type_named(ident, ctx.owner)
                        if decl is not None:
                            found = table.field_type(decl, name)
                if found is not None:
                    fields[found[0].id] += 1
            elif kind == "identifier" and self._is_reference(node):
                name = parsed.text(node)
                if name in ctx.scope:
                    continue
                found = table.field_type(ctx.owner, name)
                if found is not None:
                    fields[found[0].id] += 1
                else:
                    # имя типа в позиции получателя: ``Helper.twice(x)``, ``Mode.A``
                    decl = table.type_named(name, ctx.owner)
                    if decl is not None and decl.id != ctx.owner.id and name[:1].isupper():
                        types[decl.id] += 1

    @staticmethod
    def _is_reference(node: Node) -> bool:
        parent = node.parent
        if parent is None:
            return False
        if parent.type in _REFERENCE_PARENTS_TO_SKIP:
            name_node = parent.child_by_field_name("name")
            return not (name_node is not None and name_node.id == node.id)
        if parent.type == "method_invocation":
            name_node = parent.child_by_field_name("name")
            return not (name_node is not None and name_node.id == node.id)
        if parent.type == "field_access":
            field_node = parent.child_by_field_name("field")
            return not (field_node is not None and field_node.id == node.id)
        return parent.type not in {"type_parameter", "scoped_identifier", "scoped_type_identifier"}

    # --- графы -----------------------------------------------------------------------------

    def structure(self, mode: str = "methods") -> StructureGraph:
        return structure_module.build_structure(self, mode)

    def tree(self) -> ProjectTree:
        return structure_module.build_tree(self)

    # --- позиция в коде --------------------------------------------------------------------------

    def locate(self, file: str, line: int, column: int) -> Location | None:
        """Узел графа под курсором: самый вложенный блок метода или член/тип вне методов."""
        if file not in self.files:
            return None
        parsed = self.parsed(file)
        offset = parsed.lines.offset(Position(line, column))
        best: Member | None = None
        for member in self.symbols(file).all_members():
            if member.start_byte <= offset < member.end_byte and (
                best is None
                or member.end_byte - member.start_byte < best.end_byte - best.start_byte
            ):
                best = member
        if best is None:
            owner: TypeDecl | None = None
            for decl in self.symbols(file).all_types():
                if decl.start_byte <= offset < decl.end_byte and (
                    owner is None
                    or decl.end_byte - decl.start_byte < owner.end_byte - owner.start_byte
                ):
                    owner = decl
            return Location(owner.id, None) if owner is not None else None
        if best.kind not in FLOWABLE:
            return Location(best.id, None)
        graph = self.flow(best.id)
        if graph is None:
            return Location(best.id, best.id)
        inside = [n for n in graph.nodes if n.range.contains(line, column)]
        if not inside:
            return Location(best.id, best.id)
        node = min(inside, key=lambda n: (n.range.size, -len(n.id)))
        return Location(node.id, best.id)

    @staticmethod
    def member_of_node(node_id: str) -> str:
        return split_node_id(node_id)[0]


_CACHE: OrderedDict[str, JavaProject] = OrderedDict()
_CACHE_SIZE = 8


def get_project(files: Mapping[str, str]) -> JavaProject:
    """Проект из кеша по ``content_hash`` (повторные запросы к той же версии не пересчитываются)."""
    key = content_hash_of(files)
    project = _CACHE.get(key)
    if project is None:
        project = JavaProject(files)
        _CACHE[key] = project
        while len(_CACHE) > _CACHE_SIZE:
            _CACHE.popitem(last=False)
    else:
        _CACHE.move_to_end(key)
    return project


__all__ = [
    "FLOWABLE",
    "JavaProject",
    "Location",
    "base_name",
    "content_hash_of",
    "get_project",
]
