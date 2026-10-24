"""Остальные правила каталога (S7.6): PERF-05, 07, 09, 10, 11, 12; BUG-03, 04, 05, 06, 08, 11; MAINT-08.

Правила эвристические: срабатывают на явных паттернах и молчат, если рядом есть проверка или изменение,
которое делает вывод неочевидным. Находки без автоисправления дают рекомендацию (docs/05 § 5).
"""

from __future__ import annotations

import re
from collections import defaultdict

from tree_sitter import Node

from engine.analysis.complexity import N
from engine.analysis.cost_model import CostResult
from engine.analysis.model import (
    ComplexityEstimate,
    Contribution,
    Evidence,
    EvidenceItem,
    Finding,
    FindingLocation,
)
from engine.analysis.rules import (
    AnalysisContext,
    MemberView,
    _loop_finding,
    _members,
    _node_cost,
    estimate_of,
    reduced,
)
from engine.java import cst
from engine.java.symbols import base_name

_LOOP_TYPES = {"for_statement", "enhanced_for_statement", "while_statement", "do_statement"}
_STATEMENT_PARENTS = {"block", "switch_block", "switch_block_statement_group", "constructor_body"}
_LITERALS = {
    "decimal_integer_literal",
    "hex_integer_literal",
    "octal_integer_literal",
    "binary_integer_literal",
    "decimal_floating_point_literal",
    "string_literal",
    "character_literal",
    "true",
    "false",
    "null_literal",
}
_INTEGERS = frozenset({"int", "long", "short", "byte", "Integer", "Long", "Short", "Byte"})
_FLOATING = frozenset({"double", "float", "Double", "Float"})
_WRAPPERS = frozenset({"Integer", "Long", "Double", "Float", "Short", "Byte", "Character"})
_MAPS = frozenset(
    {
        "Map",
        "HashMap",
        "TreeMap",
        "LinkedHashMap",
        "SortedMap",
        "NavigableMap",
        "ConcurrentHashMap",
        "EnumMap",
        "Hashtable",
    }
)
_STRING_COSTLY = frozenset(
    {"split", "toCharArray", "getBytes", "toLowerCase", "toUpperCase", "trim", "strip", "chars"}
)
_COSTLY_STATIC = {
    ("Math", "pow"),
    ("Math", "sqrt"),
    ("Math", "log"),
    ("Math", "log10"),
    ("Math", "exp"),
    ("Math", "sin"),
    ("Math", "cos"),
    ("Math", "tan"),
    ("Math", "atan2"),
    ("Math", "hypot"),
    ("Integer", "parseInt"),
    ("Double", "parseDouble"),
    ("Long", "parseLong"),
    ("String", "format"),
    ("Collections", "max"),
    ("Collections", "min"),
    ("Collections", "frequency"),
}
_RESOURCES = frozenset(
    {
        "Scanner",
        "BufferedReader",
        "FileReader",
        "InputStreamReader",
        "FileInputStream",
        "FileOutputStream",
        "FileWriter",
        "BufferedWriter",
        "PrintWriter",
        "ObjectInputStream",
        "ObjectOutputStream",
        "RandomAccessFile",
    }
)
_IMMUTABLE_INIT = re.compile(
    r"^(?:(?:List|Set|Map)\.(?:of|copyOf|ofEntries)\s*\("
    r"|Collections\.(?:unmodifiable\w+|empty\w+|singleton\w*)\s*\("
    r"|.*\.stream\(\).*\.toList\(\)$)",
    re.DOTALL,
)
_FIXED_SIZE_INIT = re.compile(r"^Arrays\.asList\s*\(")
_ALL_MUTATORS = frozenset(
    {
        "add",
        "addAll",
        "remove",
        "removeAll",
        "removeIf",
        "retainAll",
        "clear",
        "put",
        "putAll",
        "putIfAbsent",
        "replaceAll",
        "sort",
        "set",
        "merge",
        "compute",
        "computeIfAbsent",
        "addFirst",
        "addLast",
    }
)
_STRUCTURAL_MUTATORS = frozenset(
    {
        "add",
        "addAll",
        "remove",
        "removeAll",
        "removeIf",
        "retainAll",
        "clear",
        "addFirst",
        "addLast",
    }
)
_INPUT_CALLS = frozenset(
    {
        "nextInt",
        "nextLine",
        "next",
        "nextDouble",
        "nextLong",
        "nextBoolean",
        "hasNext",
        "hasNextInt",
        "hasNextLine",
        "readLine",
        "read",
    }
)
_PRINT_CALLS = frozenset({"print", "println", "printf", "format"})
_SPECIFIER = re.compile(r"%(\d+\$)?[-#+ 0,(]*\d*(?:\.\d+)?([a-zA-Z%])")
_FORMAT_ALLOWED = {
    "d": _INTEGERS | {"BigInteger"},
    "x": _INTEGERS | {"BigInteger"},
    "X": _INTEGERS | {"BigInteger"},
    "o": _INTEGERS | {"BigInteger"},
    "f": {"double", "float", "Double", "Float", "BigDecimal"},
    "e": {"double", "float", "Double", "Float", "BigDecimal"},
    "E": {"double", "float", "Double", "Float", "BigDecimal"},
    "g": {"double", "float", "Double", "Float", "BigDecimal"},
    "c": {"char", "Character", "int", "Integer", "byte", "Byte", "short", "Short"},
}


# --- общие помощники ---------------------------------------------------------------------------------------------


def _anchor(view: MemberView, node: Node) -> Node:
    """Оператор блока, в котором лежит узел (для строк и узла блок-схемы находки)."""
    current = node
    while current.parent is not None and current.parent.type not in _STATEMENT_PARENTS:
        if view.declaration is not None and current.parent.id == view.declaration.id:
            break
        current = current.parent
    return current


def _finding(
    ctx: AnalysisContext,
    view: MemberView,
    rule_id: str,
    severity: str,
    category: str,
    title: str,
    node: Node,
    texts: tuple[str, str, str, str],
    evidence: list[EvidenceItem],
    complexity: ComplexityEstimate | None = None,
) -> Finding:
    anchor = _anchor(view, node)
    start, end = view.report_lines(anchor)
    explanation, defect, risk, recommendation = texts
    return Finding(
        id=ctx.unique_id(rule_id, view.fid(anchor)),
        rule_id=rule_id,
        severity=severity,
        category=category,
        title=title,
        location=FindingLocation(view.file, start, end, view.fid(anchor), view.member.id),
        explanation=explanation,
        defect=defect,
        risk=risk,
        recommendation=recommendation,
        evidence=Evidence(static=evidence),
        complexity=complexity,
    )


def _constant_estimate(
    view: MemberView, node: Node, result: CostResult | None, why: str
) -> ComplexityEstimate:
    """Оценка для находки, которая меняет только константу: класс сложности «до» и «после» один."""
    result = result or CostResult()
    estimate = estimate_of(result, result.cost)
    if not estimate.contributions:
        estimate.contributions.append(Contribution(view.fid(node), result.cost.factor(), why))
    return estimate


def _body_of(ctx: AnalysisContext) -> list[tuple[MemberView, Node]]:
    result = []
    for member in _members(ctx):
        view = MemberView(ctx.project, ctx.model, member)
        body = view.body()
        if body is not None:
            result.append((view, body))
    return result


def _calls(root: Node) -> list[Node]:
    return [n for n in cst.walk(root) if n.type == "method_invocation"]


def _arguments(call: Node) -> list[Node]:
    holder = call.child_by_field_name("arguments")
    return cst.named_children(holder) if holder is not None else []


def _writes(view: MemberView, name: str, scope: Node) -> bool:
    """Переменная присваивается или меняется (``++``, ``+=``) внутри ``scope``."""
    for node in cst.walk(scope):
        if node.type == "assignment_expression":
            left = node.child_by_field_name("left")
            if left is not None and left.type == "identifier" and view.text(left) == name:
                return True
        elif node.type == "update_expression" and any(
            c.type == "identifier" and view.text(c) == name for c in node.children
        ):
            return True
    return False


def _innermost_loop(view: MemberView, node: Node) -> Node | None:
    child, parent = node, node.parent
    while parent is not None and (view.declaration is None or parent.id != view.declaration.id):
        if parent.type in _LOOP_TYPES and not MemberView._evaluated_once(parent, child):
            return parent
        if parent.type == "lambda_expression":
            return None
        child, parent = parent, parent.parent
    return None


def _squash(text: str) -> str:
    return re.sub(r"\s+", " ", text).strip()


def _identifiers(view: MemberView, node: Node | None) -> list[str]:
    if node is None:
        return []
    return [view.text(n) for n in cst.walk(node) if n.type == "identifier"]


def _hot_loop(view: MemberView, loop: Node | None) -> bool:
    return loop is not None and not view.loop_info(loop).factor.is_constant


# --- PERF-05: инвариант в цикле ------------------------------------------------------------------------------------


def rule_perf_05(ctx: AnalysisContext) -> list[Finding]:
    found: list[Finding] = []
    for view, body in _body_of(ctx):
        for call in _calls(body):
            name = view.text(call.child_by_field_name("name"))
            receiver = call.child_by_field_name("object")
            if receiver is None or receiver.type != "identifier":
                continue
            owner = view.text(receiver)
            string_call = name in _STRING_COSTLY and view.type_of_name(owner) == "String"
            static_call = (owner, name) in {("Collections", "max"), ("Collections", "min")}
            if not (string_call or static_call):
                continue
            loop = _innermost_loop(view, call)
            if not _hot_loop(view, loop):
                continue
            assert loop is not None
            names = {owner} if string_call else set()
            args = _arguments(call)
            for arg in args:
                names.update(_identifiers(view, arg))
            if static_call:
                names.discard("Collections")
            if not names or any(n not in view.context.scope for n in names):
                continue
            if any(
                _writes(view, n, loop)
                or view.declared_inside(n, loop)
                or view.variable_of(loop) == n
                for n in names
            ):
                continue
            if any(
                m.type == "method_invocation"
                and view.text(m.child_by_field_name("object")) in names
                and view.text(m.child_by_field_name("name")) in {"add", "remove", "clear", "put"}
                for m in _calls(loop)
            ):
                continue
            text = view.text(call)
            loops = view.enclosing_loops(call)
            if not any(not lp.factor.is_constant for lp in loops):
                continue
            source = view.loop_info(loop).source
            found.append(
                _loop_finding(
                    ctx,
                    view,
                    "PERF-05",
                    "low",
                    "Инвариант вычисляется в цикле",
                    _anchor(view, call),
                    loops,
                    (
                        f"`{text}` не зависит от переменных цикла по {source} и пересчитывается на каждой итерации.",
                        "Неизменяемое значение вычисляется заново на каждой итерации.",
                        "Лишняя работа пропорциональна числу итераций, а результат всегда один и тот же.",
                        f"Вынести `{text}` в переменную перед циклом: итог — {{after}}.",
                    ),
                    [
                        EvidenceItem("call", text, view.fid(call)),
                        EvidenceItem("loop", f"цикл по {source}", view.loop_info(loop).node_id),
                    ],
                    (N, f"`{text}` выполняется на каждой итерации"),
                )
            )
    return found


# --- PERF-07: повторное вычисление --------------------------------------------------------------------------------


def _costly(view: MemberView, call: Node) -> bool:
    name = view.text(call.child_by_field_name("name"))
    receiver = call.child_by_field_name("object")
    if receiver is None:
        return False
    owner = view.text(receiver)
    if (owner, name) in _COSTLY_STATIC:
        return True
    return (
        name in _STRING_COSTLY
        and receiver.type == "identifier"
        and view.type_of_name(owner) == "String"
    )


def rule_perf_07(ctx: AnalysisContext) -> list[Finding]:
    found: list[Finding] = []
    for view, body in _body_of(ctx):
        groups: dict[str, list[Node]] = defaultdict(list)
        for call in _calls(body):
            if _costly(view, call) and not any(
                p.type == "method_invocation" and p.child_by_field_name("object") == call
                for p in [call.parent]
                if p is not None
            ):
                groups[_squash(view.text(call))].append(call)
        for text, calls in groups.items():
            if len(calls) < 2:
                continue
            involved = {
                n
                for n in _identifiers(view, calls[0])
                if n in view.context.scope or view.project_field_type(n) is not None
            }
            if any(_writes(view, n, body) for n in involved):
                continue
            first_block = _anchor(view, calls[0]).parent
            repeat = next(
                (
                    c
                    for c in calls[1:]
                    if first_block is not None
                    and c.start_byte > calls[0].end_byte
                    and first_block.start_byte <= c.start_byte
                    and c.end_byte <= first_block.end_byte
                ),
                None,
            )
            if repeat is None:
                continue
            found.append(
                _finding(
                    ctx,
                    view,
                    "PERF-07",
                    "low",
                    "PERF",
                    "Повторное вычисление",
                    repeat,
                    (
                        f"`{text}` вычисляется {len(calls)} раза с одинаковыми аргументами, а значения в них не меняются.",
                        "Одно и то же значение считается несколько раз.",
                        "Лишняя работа; при изменении формулы её легко поправить только в одном месте.",
                        f"Сохранить результат `{text}` в локальную переменную и использовать её.",
                    ),
                    [EvidenceItem("call", text, view.fid(c)) for c in calls[:3]],
                    _constant_estimate(
                        view,
                        repeat,
                        view.cost.result if view.cost else None,
                        "повторное вычисление",
                    ),
                )
            )
    return found


# --- PERF-09: ручной подсчёт частот или группировка -----------------------------------------------------------------


def _source_name(view: MemberView, loop: Node) -> str | None:
    """Имя коллекции или массива, по которым идёт цикл."""
    if loop.type == "enhanced_for_statement":
        value = loop.child_by_field_name("value")
        return view.text(value) if value is not None and value.type == "identifier" else None
    condition = loop.child_by_field_name("condition")
    if condition is None:
        return None
    for node in cst.walk(condition):
        if (
            node.type == "method_invocation"
            and view.text(node.child_by_field_name("name")) == "size"
        ):
            receiver = node.child_by_field_name("object")
            if receiver is not None and receiver.type == "identifier":
                return view.text(receiver)
        if node.type == "field_access" and view.text(node.child_by_field_name("field")) == "length":
            receiver = node.child_by_field_name("object")
            if receiver is not None and receiver.type == "identifier":
                return view.text(receiver)
    return None


def _counts(view: MemberView, loop: Node) -> bool:
    for node in cst.walk(loop):
        if node.type == "update_expression":
            return True
        if node.type == "assignment_expression" and view.text(
            node.child_by_field_name("operator")
        ) in {
            "+=",
            "-=",
        }:
            return True
        if node.type == "method_invocation" and view.text(node.child_by_field_name("name")) in {
            "set",
            "put",
            "merge",
        }:
            return True
    return False


def _compares(view: MemberView, loop: Node) -> bool:
    for node in cst.walk(loop):
        if node.type == "method_invocation" and view.text(node.child_by_field_name("name")) in {
            "equals",
            "equalsIgnoreCase",
        }:
            return True
        if (
            node.type == "binary_expression"
            and view.text(node.child_by_field_name("operator")) == "=="
        ):
            return True
    return False


def rule_perf_09(ctx: AnalysisContext) -> list[Finding]:
    found: list[Finding] = []
    for view, body in _body_of(ctx):
        for outer in (n for n in cst.walk(body) if n.type in _LOOP_TYPES):
            outer_body = outer.child_by_field_name("body")
            if outer_body is None or not _hot_loop(view, outer):
                continue
            info = view.loop_info(outer)
            for inner in (n for n in cst.walk(outer_body) if n.type in _LOOP_TYPES):
                store = _source_name(view, inner)
                if store is None or not _counts(view, inner) or not _compares(view, inner):
                    continue
                grows = any(
                    c.type == "method_invocation"
                    and view.text(c.child_by_field_name("object")) == store
                    and view.text(c.child_by_field_name("name"))
                    in {"add", "put", "offer", "addLast"}
                    and not (inner.start_byte <= c.start_byte < inner.end_byte)
                    for c in _calls(outer_body)
                )
                if not grows:
                    continue
                node_cost = _node_cost(view, info.node_id)
                complexity = (
                    estimate_of(node_cost, reduced(node_cost.cost))
                    if node_cost is not None
                    else None
                )
                found.append(
                    _finding(
                        ctx,
                        view,
                        "PERF-09",
                        "medium",
                        "PERF",
                        "Ручной подсчёт частот",
                        outer,
                        (
                            f"Для каждого элемента цикла по {info.source} заново просматривается `{store}`, "
                            "чтобы найти ключ и увеличить его счётчик: это группировка, написанная вручную.",
                            "Ручной вложенный перебор там, где есть готовая хеш-таблица.",
                            "Время растёт квадратично с числом различных ключей.",
                            "Хранить счётчики в `Map` и обновлять через `map.merge(key, 1, Integer::sum)` "
                            "или `Collectors.groupingBy`.",
                        ),
                        [
                            EvidenceItem("loop", f"цикл по {info.source}", info.node_id),
                            EvidenceItem("loop", f"внутренний просмотр {store}", view.fid(inner)),
                        ],
                        complexity,
                    )
                )
                break
    return found


# --- PERF-10: вывод в цикле ------------------------------------------------------------------------------------------


def rule_perf_10(ctx: AnalysisContext) -> list[Finding]:
    found: list[Finding] = []
    for view, body in _body_of(ctx):
        if view.member.name.lower().startswith(_OUTPUT_METHODS):
            continue  # метод для того и написан, чтобы печатать
        by_loop: dict[int, tuple[Node, list[Node]]] = {}
        for call in _calls(body):
            receiver = call.child_by_field_name("object")
            if (
                receiver is None
                or view.text(receiver) != "System.out"
                or view.text(call.child_by_field_name("name")) not in _PRINT_CALLS
            ):
                continue
            loop = _innermost_loop(view, call)
            if _hot_loop(view, loop):
                assert loop is not None
                by_loop.setdefault(loop.id, (loop, []))[1].append(call)
        for loop, calls in by_loop.values():
            info = view.loop_info(loop)
            found.append(
                _finding(
                    ctx,
                    view,
                    "PERF-10",
                    "low",
                    "PERF",
                    "Вывод в цикле",
                    loop,
                    (
                        f"Цикл по {info.source} печатает в `System.out` на каждой итерации ({len(calls)} вызов(ов) в теле).",
                        "Построчный вывод без буфера.",
                        "Каждый вызов обращается к потоку вывода: на больших данных вывод доминирует по времени.",
                        "Накопить текст в `StringBuilder` и вывести один раз после цикла "
                        "(если порядок вывода не смешивается с вводом и `System.err`).",
                    ),
                    [EvidenceItem("call", view.text(c), view.fid(c)) for c in calls[:3]]
                    + [EvidenceItem("loop", f"цикл по {info.source}", info.node_id)],
                    _constant_estimate(
                        view, loop, _node_cost(view, info.node_id), "вывод на каждой итерации"
                    ),
                )
            )
    return found


# --- PERF-11: лишняя работа в стриме ------------------------------------------------------------------------------


def _chain(view: MemberView, call: Node) -> list[str]:
    """Имена вызовов цепочки от первого к последнему."""
    names: list[str] = []
    node: Node | None = call
    while node is not None and node.type == "method_invocation":
        names.append(view.text(node.child_by_field_name("name")))
        node = node.child_by_field_name("object")
    names.reverse()
    return names


def rule_perf_11(ctx: AnalysisContext) -> list[Finding]:
    found: list[Finding] = []
    for view, body in _body_of(ctx):
        for call in _calls(body):
            parent = call.parent
            if (
                parent is not None
                and parent.type == "method_invocation"
                and parent.child_by_field_name("object") == call
            ):
                continue  # берём только внешний вызов цепочки
            names = _chain(view, call)
            if not ({"stream", "parallelStream"} & set(names)):
                continue
            problem = None
            if "sorted" in names:
                after = names[names.index("sorted") + 1 :]
                if "filter" in after:
                    problem = (
                        "`sorted` стоит до `filter`: сортируются и те элементы, которые затем отбрасываются.",
                        "Переставить `filter` перед `sorted`.",
                        "Сортировка до фильтрации",
                    )
            if problem is None:
                for index, name in enumerate(names[:-1]):
                    if name in {"collect", "toList"} and names[index + 1] in {
                        "stream",
                        "parallelStream",
                    }:
                        problem = (
                            "Результат `collect` снова превращается в стрим: промежуточная коллекция лишняя.",
                            "Продолжить цепочку без `collect` и `stream`.",
                            "Лишнее создание коллекции в стриме",
                        )
                        break
            if problem is None and names[-2:] == ["stream", "count"]:
                problem = (
                    "`stream().count()` по коллекции создаёт стрим ради длины.",
                    "Использовать `size()`.",
                    "Лишний стрим ради подсчёта",
                )
            if problem is None:
                continue
            explanation, recommendation, title = problem
            found.append(
                _finding(
                    ctx,
                    view,
                    "PERF-11",
                    "low",
                    "PERF",
                    title,
                    call,
                    (
                        explanation,
                        "Лишняя работа в цепочке Stream API.",
                        "Константный множитель времени и памяти; на больших данных заметен.",
                        recommendation,
                    ),
                    [EvidenceItem("stream", " → ".join(names), view.fid(call))],
                    _constant_estimate(
                        view, call, view.cost.result if view.cost else None, "лишний этап стрима"
                    ),
                )
            )
    return found


# --- PERF-12: боксирование в горячем цикле -------------------------------------------------------------------------


def rule_perf_12(ctx: AnalysisContext) -> list[Finding]:
    found: list[Finding] = []
    for view, body in _body_of(ctx):
        seen: set[tuple[str, int]] = set()
        for node in cst.walk(body):
            if node.type == "assignment_expression":
                left = node.child_by_field_name("left")
                operator = view.text(node.child_by_field_name("operator"))
                if left is None or left.type != "identifier" or operator == "=":
                    continue
                name = view.text(left)
            elif node.type == "update_expression":
                inner = next((c for c in node.children if c.type == "identifier"), None)
                if inner is None:
                    continue
                name = view.text(inner)
            else:
                continue
            declared = base_name(view.context.scope.get(name, ""))
            loop = _innermost_loop(view, node)
            if declared not in _WRAPPERS or not _hot_loop(view, loop):
                continue
            assert loop is not None
            if view.declared_inside(name, loop) or (name, loop.id) in seen:
                continue
            seen.add((name, loop.id))
            primitive = {
                "Integer": "int",
                "Long": "long",
                "Double": "double",
                "Float": "float",
                "Short": "short",
                "Byte": "byte",
                "Character": "char",
            }[declared]
            found.append(
                _finding(
                    ctx,
                    view,
                    "PERF-12",
                    "low",
                    "PERF",
                    "Боксирование в цикле",
                    node,
                    (
                        f"`{name}` имеет тип `{declared}`: каждое изменение в цикле по {view.loop_info(loop).source} "
                        "распаковывает и снова упаковывает значение (создаёт объект).",
                        "Накопление в обёртке вместо примитива.",
                        "Лишние выделения памяти и нагрузка на сборщик мусора в горячем цикле.",
                        f"Объявить `{name}` как `{primitive}`.",
                    ),
                    [EvidenceItem("variable", f"{declared} {name}", view.fid(node))],
                    _constant_estimate(
                        view,
                        node,
                        _node_cost(view, view.loop_info(loop).node_id),
                        "боксирование на каждой итерации",
                    ),
                )
            )
    return found


# --- BUG-03: ресурс не закрывается ---------------------------------------------------------------------------------


def _escapes(view: MemberView, name: str, body: Node, declaration: Node) -> bool:
    """Переменная передаётся дальше: возвращается, кладётся в поле или передаётся аргументом."""
    for ref in view.references_of(name, body):
        parent = ref.parent
        if parent is None or parent.id == declaration.id:
            continue
        if parent.type == "return_statement":
            return True
        if parent.type == "argument_list":
            return True
        if parent.type == "assignment_expression" and parent.child_by_field_name("right") == ref:
            return True
    return False


def rule_bug_03(ctx: AnalysisContext) -> list[Finding]:
    found: list[Finding] = []
    for view, body in _body_of(ctx):
        for declaration in (n for n in cst.walk(body) if n.type == "local_variable_declaration"):
            for declarator in cst.children_of_type(declaration, "variable_declarator"):
                value = declarator.child_by_field_name("value")
                if value is None or value.type != "object_creation_expression":
                    continue
                type_name = base_name(view.text(value.child_by_field_name("type")))
                if type_name not in _RESOURCES or "System.in" in view.text(value):
                    continue
                name = view.text(declarator.child_by_field_name("name"))
                closed = any(
                    view.text(c.child_by_field_name("name")) == "close"
                    and view.text(c.child_by_field_name("object")) == name
                    for c in _calls(body)
                )
                if closed or _escapes(view, name, body, declaration):
                    continue
                found.append(
                    _finding(
                        ctx,
                        view,
                        "BUG-03",
                        "medium",
                        "BUG",
                        f"Ресурс {type_name} не закрывается",
                        declaration,
                        (
                            f"`{name}` ({type_name}) открывается в методе и нигде не закрывается: "
                            "нет `close()` и `try`-с-ресурсами.",
                            "Утечка ресурса.",
                            "Файловые дескрипторы и буферы остаются занятыми до сборки мусора; данные записи могут не сброситься.",
                            f"Открыть `{name}` в `try (…)` — тогда `close()` вызовется автоматически "
                            "(поток `System.in` закрывать не нужно).",
                        ),
                        [EvidenceItem("resource", f"{type_name} {name}", view.fid(declaration))],
                    )
                )
    return found


# --- BUG-04: возможное деление на ноль -------------------------------------------------------------------------------

_NUMBER_INPUT = re.compile(
    r"\b(?:nextInt|nextLong|parseInt|parseLong|valueOf|readInt)\s*\(", re.ASCII
)


def _guarded(view: MemberView, body: Node, name: str, before: int) -> bool:
    for node in cst.walk(body):
        if node.start_byte >= before:
            continue
        if node.type in {"if_statement", "while_statement", "ternary_expression", "do_statement"}:
            condition = node.child_by_field_name("condition")
            if condition is not None and name in _identifiers(view, condition):
                return True
        if node.type in {"assert_statement"} and name in _identifiers(view, node):
            return True
    return False


def _in_arithmetic_try(view: MemberView, node: Node) -> bool:
    parent = node.parent
    while parent is not None:
        if parent.type == "try_statement":
            caught = [view.text(c) for c in cst.walk(parent) if c.type == "catch_type"]
            return any("ArithmeticException" in t or t == "Exception" for t in caught)
        parent = parent.parent
    return False


def rule_bug_04(ctx: AnalysisContext) -> list[Finding]:
    found: list[Finding] = []
    for view, body in _body_of(ctx):
        params = {p.name for p in view.member.params}
        for node in cst.walk(body):
            if node.type == "binary_expression":
                operator = view.text(node.child_by_field_name("operator"))
                divisor = node.child_by_field_name("right")
            elif node.type == "assignment_expression":
                operator = view.text(node.child_by_field_name("operator"))
                divisor = node.child_by_field_name("right")
                operator = operator[:-1] if operator in {"/=", "%="} else ""
            else:
                continue
            if operator not in {"/", "%"} or divisor is None or divisor.type != "identifier":
                continue
            name = view.text(divisor)
            declared = base_name(view.context.scope.get(name, ""))
            if declared not in _INTEGERS:
                continue
            dividend = node.child_by_field_name("left")
            if base_name(view.context.expr_type(dividend) or "") in _FLOATING:
                continue  # вещественное деление на ноль не бросает исключение
            from_input = name in params
            if not from_input:
                text = _declaration_text(view, body, name)
                from_input = bool(text and _NUMBER_INPUT.search(text))
            if not from_input or _guarded(view, body, name, node.start_byte):
                continue
            if _writes(view, name, body) or _in_arithmetic_try(view, node):
                continue
            found.append(
                _finding(
                    ctx,
                    view,
                    "BUG-04",
                    "medium",
                    "BUG",
                    "Деление на возможный ноль",
                    node,
                    (
                        f"`{view.text(node)}`: делитель `{name}` приходит из параметра или ввода, "
                        "и до деления нет проверки на ноль.",
                        "Целочисленное деление без проверки делителя.",
                        "При нуле программа упадёт с `ArithmeticException`.",
                        f"Проверить `{name} != 0` до деления либо обработать `ArithmeticException`.",
                    ),
                    [EvidenceItem("expression", view.text(node), view.fid(node))],
                )
            )
    return found


def _declaration_text(view: MemberView, body: Node, name: str) -> str | None:
    for node in cst.walk(body):
        if (
            node.type == "variable_declarator"
            and view.text(node.child_by_field_name("name")) == name
        ):
            value = node.child_by_field_name("value")
            return view.text(value) if value is not None else None
    return None


# --- BUG-05: разыменование возможного null -------------------------------------------------------------------------


def _null_guard(view: MemberView, call: Node, owner: str) -> bool:
    parent = call.parent
    while parent is not None:
        if parent.type in {"if_statement", "ternary_expression", "while_statement"}:
            condition = parent.child_by_field_name("condition")
            text = view.text(condition)
            if owner in text and ("null" in text or "containsKey" in text):
                return True
        if (
            parent.type == "binary_expression"
            and view.text(parent.child_by_field_name("operator")) == "&&"
        ):
            text = view.text(parent.child_by_field_name("left"))
            if owner in text and ("null" in text or "containsKey" in text):
                return True
        parent = parent.parent
    return False


def rule_bug_05(ctx: AnalysisContext) -> list[Finding]:
    found: list[Finding] = []
    for view, body in _body_of(ctx):
        text_of_body = view.text(body)
        for call in _calls(body):
            inner = call.child_by_field_name("object")
            if inner is None or inner.type != "method_invocation":
                continue
            if view.text(inner.child_by_field_name("name")) != "get":
                continue
            holder = inner.child_by_field_name("object")
            if holder is None or holder.type != "identifier":
                continue
            owner = view.text(holder)
            kind = view.type_of_name(owner)
            if kind not in _MAPS:
                continue
            if f"{owner}.containsKey(" in text_of_body or _null_guard(view, call, owner):
                continue
            found.append(
                _finding(
                    ctx,
                    view,
                    "BUG-05",
                    "medium",
                    "BUG",
                    "Разыменование возможного null",
                    call,
                    (
                        f"`{view.text(inner)}` вернёт `null`, если ключа нет в `{owner}`, "
                        f"и сразу же вызывается `.{view.text(call.child_by_field_name('name'))}(…)`.",
                        "Результат `Map.get` используется без проверки.",
                        "Для отсутствующего ключа программа упадёт с `NullPointerException`.",
                        f"Проверить `{owner}.containsKey(…)`, взять `getOrDefault(…)` или `computeIfAbsent(…)`.",
                    ),
                    [EvidenceItem("call", view.text(call), view.fid(call))],
                )
            )
    return found


# --- BUG-06: изменение неизменяемой коллекции ----------------------------------------------------------------------


def rule_bug_06(ctx: AnalysisContext) -> list[Finding]:
    found: list[Finding] = []
    for view, body in _body_of(ctx):
        for declarator in (n for n in cst.walk(body) if n.type == "variable_declarator"):
            value = declarator.child_by_field_name("value")
            if value is None:
                continue
            init = _squash(view.text(value))
            fixed = bool(_FIXED_SIZE_INIT.match(init))
            if not (fixed or _IMMUTABLE_INIT.match(init)):
                continue
            name = view.text(declarator.child_by_field_name("name"))
            if _reassigned(view, name, body):
                continue
            mutators = _STRUCTURAL_MUTATORS if fixed else _ALL_MUTATORS
            for call in _calls(body):
                if (
                    view.text(call.child_by_field_name("object")) == name
                    and view.text(call.child_by_field_name("name")) in mutators
                    and call.start_byte > declarator.end_byte
                ):
                    what = "фиксированного размера" if fixed else "неизменяемой"
                    found.append(
                        _finding(
                            ctx,
                            view,
                            "BUG-06",
                            "high",
                            "BUG",
                            "Изменение неизменяемой коллекции",
                            call,
                            (
                                f"`{name}` создана как {what} коллекция (`{init[:40]}`), а `{view.text(call)}` её меняет.",
                                "Попытка изменить коллекцию, которая этого не позволяет.",
                                "Вызов бросит `UnsupportedOperationException`.",
                                f"Создать изменяемую копию: `new ArrayList<>({name})` / `new HashMap<>({name})`.",
                            ),
                            [
                                EvidenceItem(
                                    "declaration", f"{name} = {init[:60]}", view.fid(declarator)
                                ),
                                EvidenceItem("call", view.text(call), view.fid(call)),
                            ],
                        )
                    )
                    break
    return found


def _reassigned(view: MemberView, name: str, body: Node) -> bool:
    for node in cst.walk(body):
        if node.type == "assignment_expression":
            left = node.child_by_field_name("left")
            if left is not None and left.type == "identifier" and view.text(left) == name:
                return True
    return False


# --- BUG-08: проглоченное исключение ------------------------------------------------------------------------------


def rule_bug_08(ctx: AnalysisContext) -> list[Finding]:
    found: list[Finding] = []
    for view, body in _body_of(ctx):
        for clause in (n for n in cst.walk(body) if n.type == "catch_clause"):
            parameter = cst.first_child_of_type(clause, "catch_formal_parameter")
            handler = clause.child_by_field_name("body")
            if parameter is None or handler is None:
                continue
            caught = view.text(cst.first_child_of_type(parameter, "catch_type"))
            if caught not in {"Exception", "Throwable"}:
                continue
            if not cst.named_children(handler):
                continue  # пустой блок — BUG-02
            name = view.text(parameter.child_by_field_name("name"))
            used = bool(view.references_of(name, handler))
            rethrows = any(n.type == "throw_statement" for n in cst.walk(handler))
            if used or rethrows:
                continue
            found.append(
                _finding(
                    ctx,
                    view,
                    "BUG-08",
                    "medium",
                    "BUG",
                    f"Исключение {caught} проглочено",
                    clause,
                    (
                        f"`catch ({caught} {name})` не использует исключение и не пробрасывает его дальше.",
                        "Широкий перехват без обработки причины.",
                        "Любая ошибка, включая непредвиденные, маскируется: причину сбоя не узнать.",
                        "Ловить конкретный тип, записывать причину (`"
                        + name
                        + ".getMessage()`) или пробросить исключение.",
                    ),
                    [EvidenceItem("catch", f"catch ({caught} {name})", view.fid(clause))],
                )
            )
    return found


# --- BUG-11: формат не соответствует аргументам ------------------------------------------------------------------


def _format_call(view: MemberView, call: Node) -> tuple[Node, list[Node]] | None:
    """Строка формата и аргументы вызова ``printf``/``String.format``/``formatted``."""
    name = view.text(call.child_by_field_name("name"))
    receiver = call.child_by_field_name("object")
    if receiver is None:
        return None
    args = _arguments(call)
    owner = view.text(receiver)
    if name == "formatted" and receiver.type == "string_literal":
        return receiver, args
    streams = {"System.out", "System.err"}
    if (name == "printf" and owner in streams) or (
        name == "format" and owner in streams | {"String"}
    ):
        if len(args) > 1 and args[0].type != "string_literal":
            args = args[1:]  # первый аргумент — Locale
        if args and args[0].type == "string_literal":
            return args[0], args[1:]
    return None


def rule_bug_11(ctx: AnalysisContext) -> list[Finding]:
    found: list[Finding] = []
    for view, body in _body_of(ctx):
        for call in _calls(body):
            parts = _format_call(view, call)
            if parts is None:
                continue
            literal, args = parts
            specifiers = [
                m
                for m in _SPECIFIER.finditer(view.text(literal)[1:-1])
                if m.group(2) not in {"n", "%"}
            ]
            if any(m.group(1) for m in specifiers):
                continue  # явные индексы аргументов не разбираем
            if len(args) == 1 and (view.context.expr_type(args[0]) or "").endswith("[]"):
                continue
            problem = None
            if len(specifiers) > len(args):
                problem = f"в формате {len(specifiers)} спецификатор(ов), а аргументов {len(args)}"
            else:
                for spec, arg in zip(specifiers, args, strict=False):
                    kind = view.context.expr_type(arg)
                    allowed = _FORMAT_ALLOWED.get(spec.group(2))
                    if kind is None or allowed is None or kind in allowed:
                        continue
                    problem = f"`%{spec.group(2)}` не подходит к `{view.text(arg)}` типа `{kind}`"
                    break
                else:
                    if len(args) > len(specifiers):
                        problem = (
                            f"аргументов {len(args)} больше, чем спецификаторов {len(specifiers)}"
                        )
            if problem is None:
                continue
            found.append(
                _finding(
                    ctx,
                    view,
                    "BUG-11",
                    "high",
                    "BUG",
                    "Формат не соответствует аргументам",
                    call,
                    (
                        f"`{view.text(call)[:70]}`: {problem}.",
                        "Спецификаторы формата не совпадают с аргументами.",
                        "Вызов бросит `IllegalFormatException` или напечатает не то, что задумано.",
                        "Согласовать спецификаторы с типами: `%d` — целые, `%f` — вещественные, `%s` — любые.",
                    ),
                    [EvidenceItem("call", view.text(call)[:80], view.fid(call))],
                )
            )
    return found


# --- MAINT-08: смешение ввода, вычислений и вывода -----------------------------------------------------------------

_MIXED_MIN_LINES = 20
_OUTPUT_METHODS = (
    "print",
    "show",
    "report",
    "dump",
    "display",
    "write",
    "render",
    "describe",
    "list",
    "log",
    "out",
)


def rule_maint_08(ctx: AnalysisContext) -> list[Finding]:
    found: list[Finding] = []
    lines_of = {m.method_id: m.lines for m in ctx.metrics.metrics.methods}
    for view, body in _body_of(ctx):
        if lines_of.get(view.member.id, 0) < _MIXED_MIN_LINES:
            continue
        reads = prints = None
        for call in _calls(body):
            name = view.text(call.child_by_field_name("name"))
            receiver = view.text(call.child_by_field_name("object"))
            if reads is None and name in _INPUT_CALLS and receiver != "System.out":
                reads = call
            if prints is None and receiver == "System.out" and name in _PRINT_CALLS:
                prints = call
        loops = [n for n in cst.walk(body) if n.type in _LOOP_TYPES]
        arithmetic = sum(
            1
            for n in cst.walk(body)
            if n.type == "binary_expression"
            and view.text(n.child_by_field_name("operator")) in {"+", "-", "*", "/", "%"}
        )
        if reads is None or prints is None or not (loops or arithmetic >= 4):
            continue
        start, end = view.lines(view.declaration) if view.declaration is not None else (0, 0)
        found.append(
            Finding(
                id=ctx.unique_id("MAINT-08", view.member.id),
                rule_id="MAINT-08",
                severity="low",
                category="MAINT",
                title=f"Смешение ввода, вычислений и вывода в {view.member.name}()",
                location=FindingLocation(view.file, start, end, view.member.id, view.member.id),
                explanation=(
                    f"{view.member.name}() читает ввод (строка {view.lines(reads)[0]}), вычисляет "
                    f"({'цикл' if loops else 'арифметика'}) и печатает результат (строка {view.lines(prints)[0]}) в одном теле."
                ),
                defect="Метод совмещает несколько ответственностей.",
                risk="Такой метод трудно тестировать и переиспользовать: чтение и печать нельзя отделить от расчёта.",
                recommendation="Выделить чтение, расчёт и вывод в отдельные методы (REF-01 «Выделить метод»).",
                evidence=Evidence(
                    static=[
                        EvidenceItem("input", view.text(reads)[:60], view.fid(reads)),
                        EvidenceItem("output", view.text(prints)[:60], view.fid(prints)),
                    ]
                ),
            )
        )
    return found


RULES_MORE = (
    rule_perf_05,
    rule_perf_07,
    rule_perf_09,
    rule_perf_10,
    rule_perf_11,
    rule_perf_12,
    rule_bug_03,
    rule_bug_04,
    rule_bug_05,
    rule_bug_06,
    rule_bug_08,
    rule_bug_11,
    rule_maint_08,
)
