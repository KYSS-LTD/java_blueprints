"""Модель стоимости Big O для методов и произвольных узлов (S7.1, docs/05 § 3).

Стоимость считается обходом CST тела метода: цикл умножает стоимость тела на число итераций
(``bound_cost``), ветвление берёт максимум, последовательность — доминирующее слагаемое, вызов
метода проекта — его стоимость (мемоизация), вызов JDK — запись каталога. Результат содержит цепочку
вкладов: из каких узлов сложилась оценка. Узлы CST сопоставляются с узлами блок-схемы по позиции.
"""

from __future__ import annotations

import re
from dataclasses import dataclass, field

from tree_sitter import Node

from engine.analysis import jdk_costs
from engine.analysis.complexity import EXP, LOGN, ONE, Cost, N
from engine.java import cst
from engine.java.project import FLOWABLE, JavaProject
from engine.java.symbols import Member, base_name

_INT_LITERALS = {
    "decimal_integer_literal",
    "hex_integer_literal",
    "octal_integer_literal",
    "binary_integer_literal",
}
_CONSTANT_NAME = re.compile(r"^[A-Z][A-Z0-9_]+$")
_LINEAR_UPDATE = re.compile(r"^(?:\+\+\s*\w+|\w+\s*\+\+|--\s*\w+|\w+\s*--|\w+\s*[+-]=\s*\d+)$")
_LOG_UPDATE = re.compile(r"(?:\*=|/=|<<=|>>=|>>>=)|=\s*\w+\s*[*/]\s*\d+|=\s*\w+\s*(?:<<|>>)\s*\d+")
_SOURCE_READ = re.compile(r"\.(?:hasNext\w*|readLine|isEmpty|poll\w*|peek\w*|read|ready)\s*\(")
# Цикл по общему курсору (разбор токенов): условие смотрит вперёд, тело забирает токен. Все такие циклы вложенных
# вызовов вместе делают не больше n шагов, поэтому внутри одного цикла по курсору остальные стоят O(1).
_CURSOR_LOOK = re.compile(
    r"(?<![.\w])(?:peek|lookahead|current|check|match|atEnd|isAtEnd|hasMore|more)\w*\s*\("
)
_CURSOR_TAKE = re.compile(
    r"(?<![.\w])(?:next|advance|consume|nextToken|eat|take|skip)\w*\s*\(|\b(?:pos|position|index|idx|cursor|current)\s*(?:\+\+|\+=)|(?:\+\+)\s*(?:pos|position|index|idx|cursor|current)\b"
)
_HALVING = re.compile(r"/\s*2\b|>>\s*1\b|>>>\s*1\b|/=\s*2\b|\*=\s*2\b|<<=\s*1\b")
_STREAM_STEPS = frozenset(
    {
        "stream",
        "parallelStream",
        "chars",
        "boxed",
        "filter",
        "map",
        "mapToInt",
        "mapToObj",
        "mapToLong",
        "mapToDouble",
        "sorted",
        "distinct",
        "limit",
        "skip",
        "flatMap",
        "peek",
        "range",
        "rangeClosed",
        "of",
        "iterate",
        "generate",
    }
)
_PER_ELEMENT = frozenset(
    {
        "forEach",
        "removeIf",
        "replaceAll",
        "map",
        "filter",
        "mapToInt",
        "mapToObj",
        "mapToLong",
        "mapToDouble",
        "flatMap",
        "anyMatch",
        "allMatch",
        "noneMatch",
        "peek",
        "reduce",
    }
)
_STREAM_STATIC = frozenset({"Arrays", "IntStream", "LongStream", "Stream", "Collections"})


@dataclass(frozen=True, slots=True)
class Contribution:
    node_id: str
    factor: str
    why: str


@dataclass(frozen=True, slots=True)
class CostResult:
    """Стоимость вместе с цепочкой вкладов и пометками об эвристиках."""

    cost: Cost = ONE
    chain: tuple[Contribution, ...] = ()
    notes: frozenset[str] = frozenset()

    def then(self, other: CostResult) -> CostResult:
        """Последовательное выполнение: цепочка берётся у доминирующей части."""
        notes = self.notes | other.notes
        if other.cost > self.cost:
            return CostResult(other.cost, other.chain, notes)
        return CostResult(self.cost, self.chain, notes)

    def scaled(
        self, factor: Cost, contribution: Contribution | None, notes: frozenset[str] = frozenset()
    ) -> CostResult:
        chain = (contribution, *self.chain) if contribution is not None else self.chain
        return CostResult(factor * self.cost, chain, self.notes | notes)


ZERO = CostResult()


@dataclass(slots=True)
class MethodCost:
    result: CostResult
    node_costs: dict[str, CostResult] = field(default_factory=dict)
    recursion: str | None = None
    loops: dict[str, Cost] = field(default_factory=dict)  # id цикла → число итераций
    flow_ids: dict[tuple[int, int], str] = field(
        default_factory=dict
    )  # позиция → id узла блок-схемы

    @property
    def cost(self) -> Cost:
        return self.result.cost


class CostModel:
    """Стоимость методов проекта; один экземпляр на ``JavaProject``."""

    def __init__(self, project: JavaProject, reduce: frozenset[str] = frozenset()) -> None:
        """``reduce`` — методы, у которых оценивается результат оптимизации: степень ``n`` ниже на 1,
        что распространяется на вызывающих (сценарий «после исправления находок»)."""
        self.project = project
        self.reduce = reduce
        self._done: dict[str, MethodCost] = {}
        self._absorbed: dict[
            str, MethodCost
        ] = {}  # стоимость метода, вызванного внутри цикла по общему курсору
        self._stack: list[tuple[str, bool]] = []

    def method(self, member_id: str, absorbed: bool = False) -> MethodCost | None:
        memo = self._absorbed if absorbed else self._done
        done = memo.get(member_id)
        if done is not None:
            return done
        member = self.project.member(member_id)
        if member is None or member.kind not in FLOWABLE:
            return None
        if (member_id, absorbed) in self._stack:
            return MethodCost(ZERO)  # взаимная рекурсия: вклад учтён у начавшего вычисление
        self._stack.append((member_id, absorbed))
        try:
            computed = _Walker(self, member, absorbed).run()
        finally:
            self._stack.pop()
        if (
            member_id in self.reduce
            and computed.result.cost.n >= 2
            and not computed.result.cost.exp
        ):
            lowered = Cost(False, computed.result.cost.n - 1, computed.result.cost.log)
            computed.result = CostResult(lowered, computed.result.chain, computed.result.notes)
        memo[member_id] = computed
        return computed

    def program_cost(self) -> Cost:
        """Максимум по методам проекта: «текущая» сложность программы."""
        best = ONE
        for member in self.project.members():
            found = self.method(member.id) if member.kind in FLOWABLE else None
            if found is not None:
                best = max(best, found.cost)
        return best


class _Walker:
    """Обход одного метода."""

    def __init__(self, model: CostModel, member: Member, absorbed: bool = False) -> None:
        self.model = model
        self.member = member
        # метод вызван внутри цикла по общему курсору: его собственные такие циклы и рекурсия по токенам — O(1)
        self.absorbed = absorbed
        self.inside_cursor = absorbed
        project = model.project
        self.ctx, self.declaration = project.context(member)
        self.parsed = self.ctx.parsed
        graph = project.flow(member.id)
        self.flow_ids: dict[tuple[int, int], str] = {}
        if graph is not None:
            for flow_node in graph.nodes:
                if flow_node.id != member.id:
                    self.flow_ids[(flow_node.range.start_line, flow_node.range.start_col)] = (
                        flow_node.id
                    )
        self.node_costs: dict[str, CostResult] = {}
        self.loops: dict[str, Cost] = {}
        self.self_calls: list[str] = []
        self.descents = (
            0  # из self_calls: сколько вызовов идут по элементу текущего for-each (обход дерева)
        )
        self.counters: list[str] = []  # счётчики охватывающих циклов while
        self.foreach_vars: list[str] = []  # переменные охватывающих for-each
        self.pending_counter: str | None = None
        self.constants: set[str] = set()

    # --- вход -------------------------------------------------------------------------------

    def run(self) -> MethodCost:
        declaration = self.declaration
        if declaration is None:
            return MethodCost(CostResult(notes=frozenset({"узел объявления не найден"})))
        body = declaration.child_by_field_name("body") or cst.first_child_of_type(
            declaration, "block"
        )
        if body is None:
            return MethodCost(ZERO)
        self._collect_constants(body)
        work = self.stmt(body)
        recursion = None
        if self.self_calls:
            work, recursion = self._recursive(work)
        return MethodCost(work, self.node_costs, recursion, self.loops, self.flow_ids)

    def _recursive(self, work: CostResult) -> tuple[CostResult, str]:
        calls = self.self_calls
        halving = any(_HALVING.search(a) for a in calls)
        decreasing = any(re.search(r"-\s*\d|--", a) for a in calls)
        where = self.member.id
        if halving:
            if len(calls) == 1:
                cost = LOGN if work.cost.is_constant else work.cost
            else:
                cost = N if work.cost.is_constant else work.cost * LOGN
            why = "рекурсия с делением аргумента пополам"
            notes: frozenset[str] = frozenset()
        elif decreasing and len(calls) >= 2:
            cost, why, notes = (
                EXP,
                "две и более рекурсивные ветви с уменьшением на константу",
                frozenset(),
            )
        elif decreasing:
            cost, why, notes = (
                work.cost * N,
                "рекурсия с уменьшением аргумента на константу",
                frozenset(),
            )
        elif self.descents == len(calls):
            cost = (
                work.cost
            )  # обход дерева: у каждого узла рекурсия по его потомкам, узлов в сумме n
            why = "рекурсия по элементам цикла: каждый узел посещается один раз"
            notes = frozenset({"обход дерева: узлы считаются в сумме"})
        elif _CURSOR_TAKE.search(self.text(self.declaration)):
            cost = work.cost  # каждый шаг рекурсии забирает токен: суммарно не больше n шагов, они учтены у цикла-хозяина
            why = "рекурсия по общему курсору: шаги учтены у внешнего цикла"
            notes = frozenset({"рекурсия по курсору: стоимость шагов отнесена к внешнему циклу"})
        else:
            cost = work.cost * N
            why = "рекурсия без распознанного шага"
            notes = frozenset({"рекурсия без распознанного шага: принято O(n)"})
        contribution = Contribution(where, cost.factor(), why)
        return CostResult(cost, (contribution, *work.chain), work.notes | notes), why

    # --- константы ------------------------------------------------------------------------------

    def _collect_constants(self, body: Node) -> None:
        reassigned: set[str] = set()
        for node in cst.walk(body):
            if node.type == "local_variable_declaration":
                for declarator in cst.children_of_type(node, "variable_declarator"):
                    name = self.text(declarator.child_by_field_name("name"))
                    value = declarator.child_by_field_name("value")
                    if value is not None and self.literal_size(value, depth=0):
                        self.constants.add(name)
            elif node.type == "assignment_expression":
                reassigned.add(self.text(node.child_by_field_name("left")))
            elif node.type == "update_expression":
                reassigned.update(self.text(c) for c in node.children if c.type == "identifier")
        self.constants -= reassigned

    # --- вспомогательное ----------------------------------------------------------------------------

    def text(self, node: Node | None) -> str:
        return cst.text_of(self.parsed, node)

    def fid(self, node: Node) -> str:
        span = self.parsed.range_of(node)
        return self.flow_ids.get((span.start.line, span.start.column), self.member.id)

    def literal_size(self, node: Node | None, depth: int = 0) -> bool:
        """Размер выражения (число, массив, список, строка) — константа?"""
        if node is None or depth > 6:
            return False
        kind = node.type
        if kind in _INT_LITERALS or kind in {
            "string_literal",
            "array_initializer",
            "character_literal",
        }:
            return True
        if kind == "parenthesized_expression":
            inner = cst.named_children(node)
            return bool(inner) and self.literal_size(inner[0], depth + 1)
        if kind == "identifier":
            name = self.text(node)
            return name in self.constants or bool(_CONSTANT_NAME.match(name))
        if kind == "array_creation_expression":
            if node.child_by_field_name("value") is not None:
                return True
            dims = cst.children_of_type(node, "dimensions_expr")
            return bool(dims) and all(
                self.literal_size(c, depth + 1) for d in dims for c in cst.named_children(d)
            )
        if kind == "binary_expression":
            return self.literal_size(
                node.child_by_field_name("left"), depth + 1
            ) and self.literal_size(node.child_by_field_name("right"), depth + 1)
        if kind == "method_invocation":
            owner = self.text(node.child_by_field_name("object"))
            name = self.text(node.child_by_field_name("name"))
            arguments = node.child_by_field_name("arguments")
            args = cst.named_children(arguments) if arguments is not None else []
            if owner in {"List", "Set", "Map"} and name in {"of", "entry"}:
                return all(self.literal_size(a, depth + 1) for a in args)
            if owner == "Arrays" and name == "asList":
                return len(args) != 1 and all(self.literal_size(a, depth + 1) for a in args)
        return False

    def bound_cost(self, node: Node | None) -> Cost:
        """``ONE`` для константной границы/размера, иначе ``N``."""
        if node is None:
            return N
        kind = node.type
        if kind == "field_access" and self.text(node.child_by_field_name("field")) == "length":
            return ONE if self.literal_size(node.child_by_field_name("object")) else N
        if kind == "method_invocation" and self.text(node.child_by_field_name("name")) in {
            "size",
            "length",
        }:
            return ONE if self.literal_size(node.child_by_field_name("object")) else N
        if kind == "parenthesized_expression":
            inner = cst.named_children(node)
            return self.bound_cost(inner[0]) if inner else N
        if kind == "binary_expression":
            return max(
                self.bound_cost(node.child_by_field_name("left")),
                self.bound_cost(node.child_by_field_name("right")),
            )
        return ONE if self.literal_size(node) else N

    # --- операторы ------------------------------------------------------------------------------------

    def stmt(self, node: Node) -> CostResult:
        handler = getattr(self, f"_s_{node.type}", None)
        result = handler(node) if handler is not None else self.expr(node)
        key = self.fid(node)
        if key != self.member.id and key not in self.node_costs:
            self.node_costs[key] = result
        return result

    def seq(self, nodes: list[Node]) -> CostResult:
        total = ZERO
        for child in nodes:
            total = total.then(self.stmt(child))
        return total

    def _s_block(self, node: Node) -> CostResult:
        return self.seq(cst.named_children(node))

    def _s_if_statement(self, node: Node) -> CostResult:
        result = self.expr(node.child_by_field_name("condition"))
        for field_name in ("consequence", "alternative"):
            branch = node.child_by_field_name(field_name)
            if branch is not None:
                result = result.then(self.stmt(branch))
        return result

    def _cases(self, node: Node) -> CostResult:
        result = self.expr(node.child_by_field_name("condition"))
        body = node.child_by_field_name("body")
        if body is not None:
            for case in cst.named_children(body):
                result = result.then(self.seq(cst.named_children(case)))
        return result

    _s_switch_statement = _cases
    _s_switch_expression = _cases

    def _s_try_statement(self, node: Node) -> CostResult:
        result = ZERO
        resources = node.child_by_field_name("resources")
        if resources is not None:
            result = self.expr(resources)
        body = node.child_by_field_name("body")
        if body is not None:
            result = result.then(self.stmt(body))
        for child in cst.children_of_type(node, "catch_clause"):
            handler = child.child_by_field_name("body")
            if handler is not None:
                result = result.then(self.stmt(handler))
        for child in cst.children_of_type(node, "finally_clause"):
            result = result.then(self.seq(cst.named_children(child)))
        return result

    _s_try_with_resources_statement = _s_try_statement

    def _s_synchronized_statement(self, node: Node) -> CostResult:
        body = node.child_by_field_name("body")
        return self.stmt(body) if body is not None else ZERO

    def _s_labeled_statement(self, node: Node) -> CostResult:
        return self.seq(cst.named_children(node)[1:])

    def _s_local_class_declaration(self, node: Node) -> CostResult:
        return ZERO

    # --- циклы -------------------------------------------------------------------------------------------

    def _loop(
        self,
        node: Node,
        iterations: tuple[Cost, str, bool],
        body: Node | None,
        extras: list[Node | None],
        cursor: bool = False,
        once: list[Node | None] | None = None,
    ) -> CostResult:
        """``extras`` вычисляются на каждой итерации (условие, шаг), ``once`` — один раз (инициализация, итерируемое)."""
        factor, why, exact = iterations
        outside = self.inside_cursor
        if cursor:
            if outside:
                factor, why, exact = (
                    ONE,
                    "цикл по общему курсору внутри другого: шаги учтены у внешнего",
                    True,
                )
            self.inside_cursor = True
        inner = self.stmt(body) if body is not None else ZERO
        for extra in extras:
            if extra is not None:
                inner = inner.then(self.expr(extra))
        self.inside_cursor = outside
        self.loops[self.fid(node)] = factor
        contribution = (
            None if factor.is_constant else Contribution(self.fid(node), factor.factor(), why)
        )
        notes = frozenset() if exact else frozenset({f"число итераций оценено эвристически: {why}"})
        result = inner.scaled(factor, contribution, notes)
        for extra in once or []:
            if extra is not None:
                result = result.then(self.expr(extra))
        return result

    def _s_for_statement(self, node: Node) -> CostResult:
        cond = node.child_by_field_name("condition")
        updates = node.children_by_field_name("update")
        init = node.children_by_field_name("init")
        extras: list[Node | None] = [cond, *updates]
        return self._loop(
            node,
            self._for_iterations(cond, updates, init),
            node.child_by_field_name("body"),
            extras,
            once=[*init],
        )

    def _for_iterations(
        self, cond: Node | None, updates: list[Node], init: list[Node]
    ) -> tuple[Cost, str, bool]:
        if cond is None:
            return N, "цикл без условия (принято n)", False
        var = ""
        for item in init:
            if item.type == "local_variable_declaration":
                declarators = cst.children_of_type(item, "variable_declarator")
                if declarators:
                    var = self.text(declarators[0].child_by_field_name("name"))
            elif item.type == "assignment_expression":
                var = self.text(item.child_by_field_name("left"))
        left, right = cond.child_by_field_name("left"), cond.child_by_field_name("right")
        bound = right
        if (
            var
            and cond.type == "binary_expression"
            and left is not None
            and right is not None
            and self._mentions(right, var)
            and not self._mentions(left, var)
        ):
            bound = left
        update_text = " ".join(self.text(u) for u in updates)
        shown = self.text(bound) or "условию"
        if updates and any(_LOG_UPDATE.search(self.text(u)) for u in updates):
            return LOGN, f"цикл с умножением/делением счётчика {var or ''}".strip(), True
        cost = self.bound_cost(bound)
        if updates and all(_LINEAR_UPDATE.match(self.text(u).strip()) for u in updates):
            return cost, f"цикл до {shown} ({'n итераций' if cost == N else 'константа'})", True
        return cost, f"цикл до {shown} (шаг «{update_text}» не распознан)", False

    def _mentions(self, node: Node, name: str) -> bool:
        return any(n.type == "identifier" and self.text(n) == name for n in cst.walk(node))

    def _s_enhanced_for_statement(self, node: Node) -> CostResult:
        value = node.child_by_field_name("value")
        cost, why, exact = self._iterable(value)
        self.foreach_vars.append(self.text(node.child_by_field_name("name")))
        try:
            return self._loop(
                node, (cost, why, exact), node.child_by_field_name("body"), [], once=[value]
            )
        finally:
            self.foreach_vars.pop()

    def _iterable(self, value: Node | None) -> tuple[Cost, str, bool]:
        shown = self.text(value) or "коллекции"
        if value is None:
            return N, "цикл по коллекции", False
        if self.literal_size(value):
            return ONE, f"цикл по {shown} (константный размер)", True
        if value.type == "method_invocation":
            name = self.text(value.child_by_field_name("name"))
            receiver = value.child_by_field_name("object")
            if name in {"entrySet", "keySet", "values", "toCharArray", "split", "chars", "stream"}:
                cost = ONE if self.literal_size(receiver) else N
                return (
                    cost,
                    f"цикл по {shown} ({'n элементов' if cost == N else 'константа'})",
                    True,
                )
            return N, f"цикл по результату {shown} (принято n)", False
        return N, f"цикл по {shown} (n элементов)", True

    def _is_cursor_loop(self, cond: Node | None, body: Node | None, exact: bool) -> bool:
        """``while (peek() …) { … next() … }``: цикл по общему курсору, а не по коллекции или счётчику."""
        if exact or cond is None or body is None:
            return False
        return bool(_CURSOR_LOOK.search(self.text(cond)) and _CURSOR_TAKE.search(self.text(body)))

    def _while(self, node: Node, cond: Node | None, body: Node | None) -> CostResult:
        self.pending_counter = None
        iterations = self._while_iterations(cond, body)
        cursor = self._is_cursor_loop(cond, body, iterations[2])
        counter = self.pending_counter
        self.pending_counter = None
        if counter is not None:
            self.counters.append(counter)
        try:
            return self._loop(node, iterations, body, [cond], cursor)
        finally:
            if counter is not None:
                self.counters.pop()

    def _s_while_statement(self, node: Node) -> CostResult:
        return self._while(
            node, node.child_by_field_name("condition"), node.child_by_field_name("body")
        )

    def _s_do_statement(self, node: Node) -> CostResult:
        return self._while(
            node, node.child_by_field_name("condition"), node.child_by_field_name("body")
        )

    def _and_terms(self, node: Node | None) -> list[Node]:
        """Слагаемые условия ``a && b && c`` (скобки раскрываются)."""
        if node is None:
            return []
        if node.type == "parenthesized_expression" and cst.named_children(node):
            return self._and_terms(cst.named_children(node)[0])
        if (
            node.type == "binary_expression"
            and self.text(node.child_by_field_name("operator")) == "&&"
        ):
            left, right = node.child_by_field_name("left"), node.child_by_field_name("right")
            return [*self._and_terms(left), *self._and_terms(right)]
        return [node]

    def _while_iterations(self, cond: Node | None, body: Node | None) -> tuple[Cost, str, bool]:
        cond_text = self.text(cond)
        body_text = self.text(body)
        if _SOURCE_READ.search(cond_text):
            return N, f"цикл по источнику данных ({cond_text})", True
        inner = (
            cst.named_children(cond)[0]
            if cond is not None
            and cond.type == "parenthesized_expression"
            and cst.named_children(cond)
            else cond
        )
        for term in self._and_terms(inner):
            if term.type != "binary_expression":
                continue
            op = self.text(term.child_by_field_name("operator"))
            if op not in {"<", "<=", ">", ">=", "!="}:
                continue
            if _HALVING.search(body_text) or re.search(r"\bmid\b", body_text):
                return LOGN, f"цикл с делением диапазона ({cond_text})", True
            names = {self.text(n) for n in cst.walk(term) if n.type == "identifier"}
            for name in names:
                if re.search(
                    rf"\b{re.escape(name)}\s*(?:\+\+|--|[+-]=\s*\d+)|(?:\+\+|--)\s*{re.escape(name)}\b",
                    body_text,
                ):
                    bound = term.child_by_field_name("right")
                    left = term.child_by_field_name("left")
                    if bound is not None and left is not None and self._mentions(bound, name):
                        bound = left
                    if name in self.counters:
                        return (
                            ONE,
                            f"цикл двигает счётчик {name} внешнего цикла: шаги учтены у него",
                            True,
                        )
                    self.pending_counter = name
                    return (
                        self.bound_cost(bound),
                        f"цикл со счётчиком {name} ({cond_text})",
                        True,
                    )
        return N, f"цикл while ({cond_text}) без распознанного шаблона (принято n)", False

    # --- выражения ------------------------------------------------------------------------------------------

    def expr(self, node: Node | None) -> CostResult:
        """Стоимость вычисления выражения: вызовы внутри; лямбды считает вызывающий потребитель."""
        if node is None:
            return ZERO
        total = ZERO
        stack = [node]
        while stack:
            current = stack.pop()
            kind = current.type
            if kind == "lambda_expression":
                continue
            if kind == "method_invocation":
                total = total.then(self.call(current))
            elif kind == "object_creation_expression":
                total = total.then(self.creation(current))
            elif kind in {"switch_expression"} and current is not node:
                total = total.then(self._cases(current))
                continue
            stack.extend(current.children)
        return total

    def _args(self, call: Node) -> list[Node]:
        arguments = call.child_by_field_name("arguments")
        return cst.named_children(arguments) if arguments is not None else []

    def _lambda_cost(self, lam: Node) -> CostResult:
        body = lam.child_by_field_name("body")
        if body is None:
            return ZERO
        return self.stmt(body) if body.type == "block" else self.expr(body)

    def _owner(self, receiver: Node | None) -> tuple[str | None, bool]:
        """``(тип получателя, статический ли вызов)``."""
        if receiver is None:
            return None, False
        if receiver.type == "identifier":
            name = self.text(receiver)
            unknown = (
                name not in self.ctx.scope
                and self.model.project.table.field_type(self.ctx.owner, name) is None
            )
            if unknown and name[:1].isupper():
                return name, True
        if receiver.type == "field_access" and self.text(receiver) == "System.out":
            return "PrintStream", False
        if receiver.type == "method_invocation":
            step = self.text(receiver.child_by_field_name("name"))
            inner_owner = self.text(receiver.child_by_field_name("object"))
            own = bool(
                self.ctx.resolve_call(receiver)
            )  # свой метод с именем шага (peek, filter, map…), а не Stream
            if (
                not own
                and step in _STREAM_STEPS
                and (inner_owner in _STREAM_STATIC or step != "of")
            ):
                return "Stream", False
            if step in {"entrySet", "keySet", "values"}:
                return "Set", False
        return self.ctx.expr_type(receiver), False

    def call(self, node: Node) -> CostResult:
        name = self.text(node.child_by_field_name("name"))
        args = self._args(node)
        targets = self.ctx.resolve_call(node)
        node_id = self.fid(node)
        lambdas = [a for a in args if a.type == "lambda_expression"]

        if targets:
            if any(t.id == self.member.id for t in targets):
                self.self_calls.append(" ".join(self.text(a) for a in args))
                names = {self.text(n) for a in args for n in cst.walk(a) if n.type == "identifier"}
                if names & set(self.foreach_vars):
                    self.descents += 1
                return ZERO
            best = ZERO
            for target in targets:
                found = self.model.method(target.id, self.inside_cursor)
                if found is None:
                    continue
                part = found.result
                if part.cost > best.cost:
                    chain = part.chain[:4]
                    best = CostResult(
                        part.cost,
                        (
                            Contribution(
                                node_id,
                                part.cost.factor(),
                                f"вызов {name}() — {part.cost.format()}",
                            ),
                            *chain,
                        )
                        if not part.cost.is_constant
                        else (),
                        part.notes,
                    )
            return best

        owner, static = self._owner(node.child_by_field_name("object"))
        base = jdk_costs.call_cost(owner, name, static=static)
        cost = base if base is not None else ONE
        contribution = None
        if not cost.is_constant:
            contribution = Contribution(
                node_id,
                cost.factor(),
                f"{owner}.{name}() — {cost.format()}" if owner else f"{name}() — {cost.format()}",
            )
        result = CostResult(cost, (contribution,) if contribution else ())
        if lambdas and name in _PER_ELEMENT:
            body = ZERO
            for lam in lambdas:
                body = body.then(self._lambda_cost(lam))
            if not body.cost.is_constant:
                result = result.then(
                    body.scaled(
                        N,
                        Contribution(
                            node_id,
                            N.factor(),
                            f"лямбда в {name}() вызывается для каждого элемента",
                        ),
                    )
                )
        return result

    def creation(self, node: Node) -> CostResult:
        targets = self.ctx.resolve_creation(node)
        if targets:
            best = ZERO
            for target in targets:
                found = self.model.method(target.id)
                if found is not None:
                    best = best.then(found.result)
            return best
        type_name = base_name(self.text(node.child_by_field_name("type")))
        args = self._args(node)
        if jdk_costs.is_collection(type_name) and len(args) == 1:
            arg_type = self.ctx.expr_type(args[0])
            if jdk_costs.is_collection(arg_type):
                cost = N
                return CostResult(
                    cost,
                    (
                        Contribution(
                            self.fid(node), "n", f"копирование коллекции в new {type_name}(…)"
                        ),
                    ),
                )
        return ZERO
