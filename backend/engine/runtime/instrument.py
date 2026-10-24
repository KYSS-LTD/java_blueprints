"""Инструментирование Java для трассировки и покрытия (S6.6, docs/07 § 6.1).

Исходный код проекта не меняется: инструментированная копия существует только на время сборки.
В текст вставляются вызовы ``jbtrace.JbTrace.hit(n)`` перед операторами, в начало тел циклов,
``catch``/``finally``, лямбд и веток ``switch`` и обёртка ``jbtrace.JbTrace.cond(n, условие)`` вокруг
условий ``if``. Все вставки однострочные, поэтому номера строк копии совпадают с исходными, а порядок
вычислений программы не меняется.

Каждому блоку блок-схемы (``FlowNode``) соответствует проба (``Probe``): номера слотов счётчиков
и вид пробы (``exec`` — число выполнений, ``cond`` — вычисления и истинные исходы, ``loop`` —
итерации тела и входы в цикл). Блоки, которые нельзя инструментировать без риска сломать
компиляцию (тело лямбды-выражения, стрелка ``switch``-выражения, анонимный класс), попадают в
``untracked`` и не учитываются в покрытии.
"""

from __future__ import annotations

from dataclasses import dataclass, field
from pathlib import Path

from tree_sitter import Node

from engine.graph.model import ApiRange, FlowGraph, FlowNode
from engine.graph.ranges import Position
from engine.java import cst
from engine.java.project import JavaProject
from engine.java.ts import ParsedFile

TRACE_PACKAGE = "jbtrace"
TRACE_FILE = "jbtrace/JbTrace.java"
INSTRUMENT_VERSION = 1
_CALL = "jbtrace.JbTrace"
_STATEMENT_LISTS = frozenset({"block", "constructor_body", "switch_block_statement_group"})
_WRAP_PARENTS = frozenset(
    {"if_statement", "for_statement", "enhanced_for_statement", "while_statement", "do_statement"}
)
_STATEMENT_EXPRESSIONS = frozenset(
    {
        "method_invocation",
        "assignment_expression",
        "update_expression",
        "object_creation_expression",
    }
)
# результат лямбды по имени метода, в который она передана, и по типу переменной
_VOID = "void"
_VALUE = "value"
_METHOD_RESULTS: dict[str, str] = {
    **dict.fromkeys(
        ["forEach", "forEachOrdered", "ifPresent", "ifPresentOrElse", "peek", "forEachRemaining"],
        _VOID,
    ),
    **dict.fromkeys(
        [
            "map",
            "mapToInt",
            "mapToLong",
            "mapToDouble",
            "mapToObj",
            "flatMap",
            "filter",
            "sorted",
            "anyMatch",
            "allMatch",
            "noneMatch",
            "reduce",
            "removeIf",
            "sort",
            "replaceAll",
            "computeIfAbsent",
            "computeIfPresent",
            "compute",
            "merge",
            "comparing",
            "comparingInt",
            "comparingLong",
            "comparingDouble",
            "thenComparing",
            "thenComparingInt",
            "thenComparingLong",
            "thenComparingDouble",
            "groupingBy",
            "partitioningBy",
            "toMap",
            "mapping",
            "summingInt",
            "summingLong",
            "summingDouble",
            "averagingInt",
            "averagingLong",
            "averagingDouble",
            "takeWhile",
            "dropWhile",
            "iterate",
            "generate",
            "orElseGet",
            "orElseThrow",
            "max",
            "min",
            "collectingAndThen",
        ],
        _VALUE,
    ),
}
_TYPE_RESULTS: dict[str, str] = {
    **dict.fromkeys(
        ["Runnable", "Consumer", "BiConsumer", "IntConsumer", "LongConsumer", "DoubleConsumer"],
        _VOID,
    ),
    **dict.fromkeys(
        [
            "Supplier",
            "Function",
            "BiFunction",
            "Predicate",
            "BiPredicate",
            "Comparator",
            "UnaryOperator",
            "BinaryOperator",
            "IntFunction",
            "IntPredicate",
            "IntUnaryOperator",
            "IntBinaryOperator",
            "ToIntFunction",
            "ToLongFunction",
            "ToDoubleFunction",
            "IntSupplier",
            "BooleanSupplier",
            "DoubleSupplier",
            "Callable",
        ],
        _VALUE,
    ),
}
_LOOPS = frozenset({"loop_for", "loop_foreach", "loop_while"})
_PLAIN = frozenset(
    {
        "var_decl",
        "assign",
        "operation",
        "throw",
        "return",
        "break",
        "continue",
        "yield",
        "assert",
        "sync",
        "code",
        "labeled",
        "try",
    }
)


def trace_source() -> str:
    """Текст ``JbTrace.java`` (лежит рядом с модулем)."""
    return (Path(__file__).parent / "java" / "JbTrace.java").read_text(encoding="utf-8")


@dataclass(frozen=True, slots=True)
class Probe:
    """Счётчики одного блока: ``exec`` — ``slot``; ``cond`` — ``slot`` (вычисления), ``slot+1``
    (истинные); ``loop`` — ``slot`` (итерации тела) и ``entry_slot`` (входы в цикл)."""

    node_id: str
    kind: str
    mode: str  # exec | cond | loop
    slot: int
    entry_slot: int | None = None


@dataclass(slots=True)
class Instrumented:
    files: dict[str, str]
    probes: list[Probe]
    untracked: dict[str, str]
    slots: int
    conditions: bool
    lambdas: bool = True
    skipped_files: list[str] = field(default_factory=list)


@dataclass(slots=True)
class _Site:
    """Оператор, перед которым нужно вставить вызовы (и, возможно, обернуть его в блок).

    ``wrap`` — обернуть в ``{ … }``; ``value`` — тело стрелки ``switch``-выражения, которое после
    обёртки нужно вернуть через ``yield``.
    """

    start: int
    end: int
    wrap: bool = False
    value: bool = False
    hits: list[int] = field(default_factory=list)


class _Edits:
    """Вставки текста по байтовым смещениям; порядок внутри позиции задаёт приоритет."""

    def __init__(self) -> None:
        self._pieces: dict[int, list[tuple[int, int, str]]] = {}
        self._seq = 0

    def add(self, pos: int, text: str, priority: int = 1) -> None:
        self._seq += 1
        self._pieces.setdefault(pos, []).append((priority, self._seq, text))

    def render(self, source: bytes) -> str:
        out = bytearray()
        last = 0
        for pos in sorted(self._pieces):
            out += source[last:pos]
            for _, _, text in sorted(self._pieces[pos]):
                out += text.encode("utf-8")
            last = pos
        out += source[last:]
        return out.decode("utf-8")


def _hit(slot: int) -> str:
    return f"{_CALL}.hit({slot});"


class _Slots:
    def __init__(self) -> None:
        self.count = 0

    def take(self, size: int = 1) -> int:
        first = self.count
        self.count += size
        return first


class _FileInstrumenter:
    def __init__(
        self,
        parsed: ParsedFile,
        slots: _Slots,
        probes: list[Probe],
        untracked: dict[str, str],
        conditions: bool,
        lambdas: bool,
    ) -> None:
        self.parsed = parsed
        self.slots = slots
        self.probes = probes
        self.untracked = untracked
        self.conditions = conditions
        self.lambdas = lambdas
        self.edits = _Edits()
        self.sites: dict[tuple[int, int], _Site] = {}
        self.label_slots: dict[int, int] = {}

    # --- поиск узлов и мест вставки ----------------------------------------------------------

    def cst_node(self, r: ApiRange) -> Node | None:
        lines = self.parsed.lines
        start = lines.offset(Position(r.start_line, r.start_col))
        end = lines.offset(Position(r.end_line, r.end_col))
        node: Node | None = self.parsed.root.descendant_for_byte_range(start, end)
        best: Node | None = None
        while node is not None:
            if node.start_byte == start and node.end_byte == end:
                best = node
            elif best is not None:
                break
            node = node.parent
        return best

    @staticmethod
    def _is_statement_switch(rule: Node) -> bool:
        block = rule.parent
        switch = block.parent if block is not None else None
        parent = switch.parent if switch is not None else None
        return parent is not None and (
            parent.type in _STATEMENT_LISTS
            or parent.type in _WRAP_PARENTS
            or parent.type == "labeled_statement"
        )

    def placement(self, node: Node) -> str:
        """``list`` — перед оператором, ``wrap`` — обернуть в ``{ … }``, ``yield`` — обернуть и
        вернуть значение (стрелка ``switch``-выражения), ``skip`` — нельзя."""
        parent = node.parent
        if parent is None:
            return "skip"
        if parent.type in _STATEMENT_LISTS:
            return "list"
        if parent.type in _WRAP_PARENTS:
            return "wrap" if node.type != "block" else "skip"
        if parent.type == "switch_rule":
            if node.type == "throw_statement":
                return "wrap"
            if node.type == "expression_statement":
                return "wrap" if self._is_statement_switch(parent) else "yield"
        return "skip"

    def site(self, node: Node) -> _Site:
        key = (node.start_byte, node.end_byte)
        if key not in self.sites:
            self.sites[key] = _Site(node.start_byte, node.end_byte)
        return self.sites[key]

    def statement_hit(self, node: Node, slot: int) -> bool:
        if node.type == "explicit_constructor_invocation":
            # this(...)/super(...) должен остаться первым оператором: счётчик ставится после него
            self.edits.add(node.end_byte, f" {_hit(slot)}")
            return True
        place = self.placement(node)
        if place == "skip":
            return False
        site = self.site(node)
        site.hits.append(slot)
        site.wrap = site.wrap or place in {"wrap", "yield"}
        site.value = site.value or place == "yield"
        return True

    def block_start_hit(self, block: Node, slot: int) -> bool:
        if block.type not in {"block", "constructor_body"}:
            return False
        self.edits.add(block.start_byte + 1, f" {_hit(slot)}")
        return True

    # --- блоки графа -----------------------------------------------------------------------------

    def probe(
        self,
        flow: FlowNode,
        mode: str,
        slot: int,
        entry: int | None = None,
    ) -> None:
        self.probes.append(Probe(flow.id, flow.kind, mode, slot, entry))

    def skip(self, flow: FlowNode, reason: str) -> None:
        self.untracked[flow.id] = reason

    def instrument(self, graph: FlowGraph) -> None:
        for flow in graph.nodes:
            node = self.cst_node(flow.range)
            if node is None:
                self.skip(flow, "не найден узел синтаксического дерева")
                continue
            kind = flow.kind
            if kind == "method":
                self.method(flow, node)
            elif kind in _PLAIN:
                self.plain(flow, node)
            elif kind == "condition":
                self.condition(flow, node)
            elif kind in _LOOPS:
                self.loop(flow, node)
            elif kind == "switch":
                self.plain(flow, node, "switch как выражение")
            elif kind == "case":
                self.case(flow, node)
            elif kind == "catch":
                self.block_node(flow, node.child_by_field_name("body"), "тело catch не найдено")
            elif kind == "finally":
                self.block_node(flow, cst.first_child_of_type(node, "block"), "finally без блока")
            elif kind == "lambda":
                self.lambda_block(flow, node)
            elif kind == "local_type":
                if node.type in cst.TYPE_DECLARATIONS:
                    self.plain(flow, node)
                else:
                    self.skip(flow, "анонимный класс")
            else:
                self.skip(flow, f"вид блока {kind} не инструментируется")

    def method(self, flow: FlowNode, decl: Node) -> None:
        body = decl.child_by_field_name("body")
        if body is None and decl.type == "static_initializer":
            body = cst.first_child_of_type(decl, "block")
        if body is None and decl.type == "block":
            body = decl
        if body is None or body.type not in {"block", "constructor_body"}:
            return  # абстрактный или интерфейсный метод: тела нет
        slot = self.slots.take()
        named = cst.named_children(body)
        if named and named[0].type == "explicit_constructor_invocation":
            self.edits.add(named[0].end_byte, f" {_hit(slot)}")
        else:
            self.block_start_hit(body, slot)
        self.probe(flow, "exec", slot)

    def plain(
        self, flow: FlowNode, node: Node, reason: str = "оператор вне списка операторов"
    ) -> None:
        slot = self.slots.take()
        if not self.statement_hit(node, slot):
            self.skip(flow, reason)
            return
        if flow.kind == "labeled":
            self.label_slots[node.start_byte] = slot
        self.probe(flow, "exec", slot)

    @staticmethod
    def _has_pattern(expr: Node) -> bool:
        for node in cst.walk(expr):
            if node.type == "instanceof_expression" and (
                node.child_by_field_name("name") is not None
                or node.child_by_field_name("pattern") is not None
            ):
                return True
        return False

    def condition(self, flow: FlowNode, node: Node) -> None:
        parens = node.child_by_field_name("condition")
        named = cst.named_children(parens) if parens is not None else []
        expr = named[0] if named else None
        if self.conditions and expr is not None and not self._has_pattern(expr):
            slot = self.slots.take(2)
            # открывающая вставка раньше закрывающей при совпадении позиций вложенных условий
            self.edits.add(expr.start_byte, f"{_CALL}.cond({slot}, ", 3)
            self.edits.add(expr.end_byte, ")", 0)
            self.probe(flow, "cond", slot)
            return
        self.plain(flow, node, "условие нельзя обернуть")

    def loop(self, flow: FlowNode, node: Node) -> None:
        parent = node.parent
        entry: int | None
        if parent is not None and parent.type == "labeled_statement":
            entry = self.label_slots.get(parent.start_byte)
        else:
            entry = self.slots.take()
            if not self.statement_hit(node, entry):
                entry = None
        body = node.child_by_field_name("body")
        iterations = self.slots.take()
        placed = False
        if body is not None:
            if body.type == "block":
                placed = self.block_start_hit(body, iterations)
            elif body.type not in {";", "empty_statement"}:
                placed = self.statement_hit(body, iterations)
        if placed:
            self.probe(flow, "loop", iterations, entry)
        elif entry is not None:
            self.probe(flow, "exec", entry)
        else:
            self.skip(flow, "цикл вне списка операторов")

    def case(self, flow: FlowNode, node: Node) -> None:
        slot = self.slots.take()
        if node.type == "switch_block_statement_group":
            colon = cst.first_child_of_type(node, ":")
            if colon is None:
                self.skip(flow, "ветка без двоеточия")
                return
            self.edits.add(colon.end_byte, f" {_hit(slot)}")
            self.probe(flow, "exec", slot)
            return
        body = next(
            (c for c in reversed(cst.named_children(node)) if c.type != "switch_label"), None
        )
        if body is None:
            self.skip(flow, "ветка без тела")
        elif body.type == "block":
            self.block_start_hit(body, slot)
            self.probe(flow, "exec", slot)
        elif self.statement_hit(body, slot):
            self.probe(flow, "exec", slot)
        else:
            self.skip(flow, "стрелка switch-выражения")

    def block_node(self, flow: FlowNode, block: Node | None, reason: str) -> None:
        slot = self.slots.take()
        if block is not None and self.block_start_hit(block, slot):
            self.probe(flow, "exec", slot)
        else:
            self.skip(flow, reason)

    def lambda_block(self, flow: FlowNode, node: Node) -> None:
        body = node.child_by_field_name("body")
        if body is None:
            self.skip(flow, "лямбда без тела")
        elif body.type == "block":
            self.block_node(flow, body, "тело лямбды")
        else:
            self.lambda_expression(flow, node, body)

    def lambda_expression(self, flow: FlowNode, lam: Node, body: Node) -> None:
        """``x -> выражение`` превращается в блок: ``return`` для значения, оператор для ``void``.

        Выражение, которое не может быть оператором (``x * 2``), всегда значение. Вызов метода
        может быть и тем и другим: тип берётся из имени метода или типа переменной (``forEach`` —
        ``void``, ``map`` — значение); если по ним не понять — лямбда не инструментируется.
        """
        if not self.lambdas:
            self.skip(flow, "лямбда-выражение без блока")
            return
        kind = self.lambda_result(lam, body)
        if kind is None:
            self.skip(flow, "неизвестно, возвращает ли лямбда значение")
            return
        slot = self.slots.take()
        head = f"{{ {_hit(slot)} " + ("return " if kind == _VALUE else "")
        self.edits.add(body.start_byte, head, 0)
        self.edits.add(body.end_byte, "; }", 2)
        self.probe(flow, "exec", slot)

    def lambda_result(self, lam: Node, body: Node) -> str | None:
        if body.type not in _STATEMENT_EXPRESSIONS:
            return _VALUE
        parent = lam.parent
        if parent is None:
            return None
        if parent.type == "argument_list" and parent.parent is not None:
            call = parent.parent
            name = ""
            if call.type == "method_invocation":
                name = cst.text_of(self.parsed, call.child_by_field_name("name"))
            elif call.type == "object_creation_expression":
                name = cst.text_of(self.parsed, call.child_by_field_name("type"))
            return _METHOD_RESULTS.get(name)
        if parent.type == "variable_declarator" and parent.parent is not None:
            declared = parent.parent.child_by_field_name("type")
            if declared is not None:
                text = cst.squash(cst.text_of(self.parsed, declared))
                return _TYPE_RESULTS.get(text.split("<", 1)[0].rsplit(".", 1)[-1])
        return None

    # --- результат ----------------------------------------------------------------------------------

    def render(self) -> str:
        for site in self.sites.values():
            if site.wrap:
                self.edits.add(site.start, "{ ", 0)
            self.edits.add(site.start, " ".join(_hit(slot) for slot in site.hits) + " ", 1)
            if site.value:
                self.edits.add(site.start, "yield ", 1)
            if site.wrap:
                self.edits.add(site.end, " }", 2)
        return self.edits.render(self.parsed.source)


def instrument(
    project: JavaProject, *, conditions: bool = True, lambdas: bool = True
) -> Instrumented:
    """Инструментированная копия всех файлов проекта и таблица проб.

    ``conditions=False`` — запасной вариант без обёрток ``cond``: они ломают компиляцию, если
    условие вводит переменную, определённую только по ходу вычисления (``a && (y = f()) > 0``).
    ``lambdas=False`` — без превращения лямбд-выражений в блоки (если по имени метода неверно
    угадано, возвращает ли лямбда значение).
    """
    slots = _Slots()
    probes: list[Probe] = []
    untracked: dict[str, str] = {}
    by_file: dict[str, _FileInstrumenter] = {}
    skipped: list[str] = []
    for graph in project.flows():
        path = graph.file
        if project.parsed(path).issues:
            if path not in skipped:
                skipped.append(path)
            continue
        if any(node.errors for node in graph.nodes):
            for node in graph.nodes:
                untracked[node.id] = "блок-схема метода построена с ошибкой"
            continue
        worker = by_file.get(path)
        if worker is None:
            worker = _FileInstrumenter(
                project.parsed(path), slots, probes, untracked, conditions, lambdas
            )
            by_file[path] = worker
        worker.instrument(graph)
    files: dict[str, str] = dict(project.files)
    for path, worker in by_file.items():
        files[path] = worker.render()
    files[TRACE_FILE] = trace_source()
    return Instrumented(files, probes, untracked, slots.count, conditions, lambdas, skipped)
