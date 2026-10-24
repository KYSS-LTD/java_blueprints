"""План декомпозиции монолита: граф зависимостей членов, кластеризация, роли и имена (S9.5–S9.6, docs/06 § 6).

Единица декомпозиции — методы класса-монолита. Поля, конструкторы, ``main``, инициализаторы и методы,
переопределяющие или реализующие чужие, остаются в «ядре» (исходном классе). Остальные методы
группируются по связности: вызовы (2), общие поля (по Жаккару до 3), общий глагол в имени (1).
Кластеризация — Louvain (networkx) с фиксированным ``seed``, результат детерминирован.
"""

from __future__ import annotations

import re
from collections import Counter, defaultdict
from collections.abc import Iterable
from dataclasses import dataclass, field

import networkx as nx

from engine.java.project import FLOWABLE, JavaProject
from engine.java.symbols import Member, TypeDecl, base_name

DEFAULT_SEED = 42
DEFAULT_MAX_CLASSES = 6
DEFAULT_MIN_MEMBERS = 2

ROLES: dict[str, tuple[str, ...]] = {
    "parser": ("parse", "read", "tokenize", "load", "scan", "input", "split", "decode", "fetch"),
    "analyzer": (
        "analyze",
        "calc",
        "count",
        "check",
        "validate",
        "find",
        "detect",
        "compute",
        "evaluate",
        "total",
        "sum",
        "average",
        "median",
        "top",
        "filter",
        "group",
        "merge",
        "distinct",
        "search",
        "match",
        "verify",
        "score",
    ),
    "optimizer": (
        "optimize",
        "transform",
        "rewrite",
        "simplify",
        "fold",
        "reduce",
        "normalize",
        "compress",
        "dedupe",
        "purge",
        "clean",
    ),
    "printer": (
        "print",
        "format",
        "render",
        "write",
        "report",
        "show",
        "output",
        "display",
        "csv",
        "describe",
        "dump",
        "list",
        "log",
    ),
}
ROLE_CLASS = {
    "parser": "Parser",
    "analyzer": "Analyzer",
    "optimizer": "Optimizer",
    "printer": "Printer",
    "model": "Model",
    "util": "Utils",
}


@dataclass(slots=True)
class PlanClass:
    name: str
    role: str  # parser | analyzer | optimizer | printer | model | util | core
    file: str
    members: list[str]
    lines: int
    reason: str


@dataclass(slots=True)
class PlanEdge:
    from_: str
    to: str
    calls: int
    fields: int


@dataclass(slots=True)
class Stat:
    lines: int
    lcom4: float


@dataclass(slots=True)
class Plan:
    type_id: str
    classes: list[PlanClass]
    edges: list[PlanEdge] = field(default_factory=list)
    warnings: list[str] = field(default_factory=list)
    before: Stat = field(default_factory=lambda: Stat(0, 0.0))
    after: Stat = field(default_factory=lambda: Stat(0, 0.0))

    def core(self) -> PlanClass:
        return next(c for c in self.classes if c.role == "core")


def words(name: str) -> list[str]:
    return [w.lower() for w in re.findall(r"[A-Z]?[a-z0-9]+|[A-Z]+(?![a-z])", name)]


def first_word(name: str) -> str:
    parts = words(name)
    return parts[0] if parts else name.lower()


def monolith(project: JavaProject) -> TypeDecl | None:
    """Класс для декомпозиции: тот, что держит ``main``; иначе самый большой по числу методов."""
    classes = [t for t in project.types() if t.kind == "class" and t.parent_path is None]
    if not classes:
        return None
    mains = {m.split(".")[-1] for m in project.main_classes()}
    with_main = [t for t in classes if t.name in mains]
    pool = with_main or classes
    return max(
        pool,
        key=lambda t: (sum(1 for m in t.members if m.kind == "method"), t.end_byte - t.start_byte),
    )


def owner_of(project: JavaProject, member_id: str) -> TypeDecl | None:
    return next((t for t in project.types() if any(m.id == member_id for m in t.members)), None)


def _overrides(project: JavaProject, decl: TypeDecl, method: Member) -> bool:
    if any(a.endswith("Override") for a in method.annotations):
        return True
    arity = len(method.params)
    for parent in project.table.supertypes(decl):
        if any(
            m.kind == "method" and m.name == method.name and len(m.params) == arity
            for m in parent.members
        ):
            return True
    return False


def pinned(project: JavaProject, decl: TypeDecl) -> dict[str, str]:
    """Члены, которые остаются в ядре, и причина."""
    result: dict[str, str] = {}
    for member in decl.members:
        if member.kind == "field":
            result[member.id] = "состояние остаётся в ядре"
        elif member.kind in {"constructor", "initializer"}:
            result[member.id] = "конструкторы и инициализаторы остаются в ядре"
        elif member.kind == "method":
            if member.name == "main" and member.is_static:
                result[member.id] = "точка входа"
            elif _overrides(project, decl, member):
                result[member.id] = "переопределяет или реализует чужой метод"
    # методы, на которые ссылаются как ``Тип::метод``: правила переписывания их не охватывают
    for path, text in project.files.items():
        parsed = project.parsed(path)
        for match in re.finditer(r"(?:\bthis|\b" + re.escape(decl.name) + r")::(\w+)", text):
            for member in decl.members:
                if member.kind == "method" and member.name == match.group(1):
                    result.setdefault(member.id, "используется как ссылка на метод")
        del parsed
    return result


def build_graph(project: JavaProject, decl: TypeDecl, movable: list[Member]) -> nx.Graph:
    graph: nx.Graph = nx.Graph()
    ids = {m.id for m in movable}
    fields_of: dict[str, set[str]] = {}
    for method in movable:
        graph.add_node(method.id)
        used, _ = project.use_counts(method)
        fields_of[method.id] = set(used)

    def bump(a: str, b: str, weight: float) -> None:
        if a == b:
            return
        if graph.has_edge(a, b):
            graph[a][b]["weight"] += weight
        else:
            graph.add_edge(a, b, weight=weight)

    for method in movable:
        for callee in project.call_counts(method):
            if callee in ids:
                bump(method.id, callee, 2.0)
    ordered = sorted(movable, key=lambda m: m.id)
    for i, first in enumerate(ordered):
        for second in ordered[i + 1 :]:
            shared = fields_of[first.id] & fields_of[second.id]
            union = fields_of[first.id] | fields_of[second.id]
            if shared and union:
                bump(first.id, second.id, 3.0 * len(shared) / len(union))
            if len(first_word(first.name)) >= 3 and first_word(first.name) == first_word(
                second.name
            ):
                bump(first.id, second.id, 1.0)
    return graph


def communities(graph: nx.Graph, seed: int) -> list[list[str]]:
    if graph.number_of_nodes() == 0:
        return []
    found = nx.community.louvain_communities(graph, weight="weight", seed=seed, resolution=1.0)
    ordered = [sorted(c) for c in found]
    ordered.sort(key=lambda c: (-len(c), c[0]))
    return ordered


def _weight_between(graph: nx.Graph, a: Iterable[str], b: Iterable[str]) -> float:
    right = set(b)
    return float(sum(graph[u][v]["weight"] for u in a for v in graph.adj[u] if v in right))


def merge_small(
    graph: nx.Graph, groups: list[list[str]], min_members: int, max_groups: int
) -> list[list[str]]:
    """Слияние слишком малых кластеров с самым связанным и ограничение числа кластеров."""
    groups = [list(g) for g in groups]

    def merge_into_best(index: int) -> None:
        source = groups[index]
        others = [i for i in range(len(groups)) if i != index]
        best = max(
            others, key=lambda i: (_weight_between(graph, source, groups[i]), len(groups[i]), -i)
        )
        groups[best] = sorted(groups[best] + source)
        del groups[index]

    while len(groups) > 1:
        smallest = min(range(len(groups)), key=lambda i: (len(groups[i]), -i))
        if len(groups[smallest]) >= min_members and len(groups) <= max_groups:
            break
        merge_into_best(smallest)
    groups.sort(key=lambda c: (-len(c), c[0]))
    return groups


def role_of(names: list[str]) -> tuple[str, Counter[str]]:
    votes: Counter[str] = Counter()
    for name in names:
        for word in words(name):
            for role, lexicon in ROLES.items():
                if word in lexicon:
                    votes[role] += 1
                    break
    if not votes:
        return "util", votes
    top = votes.most_common()
    best = max(top, key=lambda item: (item[1], -list(ROLES).index(item[0])))
    return best[0], votes


def dominant_noun(names: list[str]) -> str:
    nouns: Counter[str] = Counter()
    for name in names:
        parts = words(name)
        for word in parts[1:]:
            if len(word) >= 3 and not any(word in lexicon for lexicon in ROLES.values()):
                nouns[word] += 1
    return nouns.most_common(1)[0][0].capitalize() if nouns else ""


def name_classes(groups: list[list[Member]], taken: set[str]) -> list[tuple[str, str, str]]:
    """``(имя, роль, причина)`` для каждой группы; имена уникальны и не совпадают с существующими типами."""
    results: list[tuple[str, str, str]] = []
    used = set(taken)
    for members in groups:
        names = [m.name for m in members]
        role, votes = role_of(names)
        noun = dominant_noun(names)
        base = ROLE_CLASS[role]
        if role == "util" and noun:
            base = f"{noun}Utils"
        candidate = base
        if candidate in used and noun and role != "util":
            candidate = f"{noun}{base}"
        index = 2
        while candidate in used:
            candidate = f"{base}{index}"
            index += 1
        used.add(candidate)
        sample = ", ".join(sorted(set(names))[:4])
        reason = f"{len(members)} методов ({sample}…)" + (
            f"; роль по глаголам: {role}" if votes else ""
        )
        results.append((candidate, role, reason))
    return results


def lines_of(project: JavaProject, members: Iterable[Member]) -> int:
    total = 0
    for member in members:
        span = project.parsed(project.file_of(member)).lines.range(
            member.start_byte, member.end_byte
        )
        total += span.end.line - span.start.line + 1
    return total


def lcom4_of(project: JavaProject, members: list[Member], all_fields: set[str]) -> float:
    """Число компонент связности «методы — поля, вызовы внутри набора» (как в ``analysis.metrics``)."""
    methods = [m for m in members if m.kind == "method" and not m.is_static]
    if not methods:
        return 0.0
    parent: dict[str, str] = {}

    def find(x: str) -> str:
        parent.setdefault(x, x)
        while parent[x] != x:
            parent[x] = parent[parent[x]]
            x = parent[x]
        return x

    ids = {m.id for m in methods}
    for method in methods:
        find(method.id)
        for field_id in project.use_counts(method)[0]:
            if field_id in all_fields:
                parent[find(method.id)] = find(field_id)
        for callee in project.call_counts(method):
            if callee in ids:
                parent[find(method.id)] = find(callee)
    return float(len({find(m.id) for m in methods}))


def build_plan(
    project: JavaProject,
    type_id: str | None = None,
    *,
    max_classes: int | None = None,
    min_members: int | None = None,
    seed: int | None = None,
) -> Plan:
    decl = (
        next((t for t in project.types() if t.id == type_id), None)
        if type_id
        else monolith(project)
    )
    if decl is None:
        raise LookupError("класс для декомпозиции не найден")
    limit = max_classes or DEFAULT_MAX_CLASSES
    smallest = min_members or DEFAULT_MIN_MEMBERS
    stay = pinned(project, decl)
    movable = [m for m in decl.members if m.kind == "method" and m.id not in stay]
    file = decl.id.split("#", 1)[0]
    plan_classes: list[PlanClass] = []
    plan = Plan(decl.id, plan_classes)
    members_by_id = {m.id: m for m in decl.members}
    fields = {m.id for m in decl.members if m.kind == "field"}
    plan.before = Stat(
        lines_of(project, decl.members) + 2, lcom4_of(project, list(decl.members), fields)
    )

    graph = build_graph(project, decl, movable)
    groups = merge_small(
        graph, communities(graph, seed if seed is not None else DEFAULT_SEED), smallest, limit
    )
    if (
        len(groups) < 2 and len(movable) >= 4
    ):  # кластеризация не нашла структуры: делим по ролям глаголов
        by_role: dict[str, list[str]] = defaultdict(list)
        for method in movable:
            by_role[role_of([method.name])[0]].append(method.id)
        groups = merge_small(graph, [sorted(v) for v in by_role.values()], smallest, limit)

    # ядро оставляет кластер, к которому относятся методы, вызываемые из main
    main = next((m for m in decl.members if m.name == "main" and m.is_static), None)
    entries = set(project.call_counts(main)) if main is not None else set()
    core_group: list[str] = []
    if (
        len(groups) >= 4
    ):  # при малом числе кластеров выносим всё: иначе будет меньше трёх новых классов
        scored = [
            (sum(1 for m in g if m in entries), _weight_between(graph, g, entries), len(g), i)
            for i, g in enumerate(groups)
        ]
        best = max(scored)
        if best[0] > 0:
            core_group = groups.pop(best[3])
    moved = [[members_by_id[i] for i in g] for g in groups]
    labels = name_classes(moved, {t.name for t in project.types()} | {decl.name})

    core_members = [m for m in decl.members if m.id in stay] + [
        members_by_id[i] for i in core_group
    ]
    core_members.sort(key=lambda m: m.start_byte)
    plan_classes.append(
        PlanClass(
            decl.name,
            "core",
            file,
            [m.id for m in core_members],
            lines_of(project, core_members) + 4,
            "поля, конструкторы, точка входа и оркестрация",
        )
    )
    for (name, role, reason), members in zip(labels, moved, strict=True):
        members.sort(key=lambda m: m.start_byte)
        plan_classes.append(
            PlanClass(
                name,
                role,
                f"{name}.java",
                [m.id for m in members],
                lines_of(project, members) + 8,
                reason,
            )
        )

    owner = {m_id: c.name for c in plan_classes for m_id in c.members}
    for source in plan_classes:
        calls: Counter[str] = Counter()
        used_fields: dict[str, set[str]] = defaultdict(set)
        for member_id in source.members:
            member = members_by_id[member_id]
            if member.kind not in FLOWABLE:
                continue
            for callee, count in project.call_counts(member).items():
                target = owner.get(callee)
                if target and target != source.name:
                    calls[target] += count
            for field_id in project.use_counts(member)[0]:
                target = owner.get(field_id)
                if target and target != source.name:
                    used_fields[target].add(field_id)
        for target in sorted(set(calls) | set(used_fields)):
            plan.edges.append(
                PlanEdge(source.name, target, calls[target], len(used_fields[target]))
            )
    _warn(plan)
    after = [lcom4_of(project, [members_by_id[i] for i in c.members], fields) for c in plan_classes]
    shown = [v for v in after if v > 0]
    plan.after = Stat(
        max((c.lines for c in plan_classes if c.role == "core"), default=0),
        round(sum(shown) / len(shown), 2) if shown else 0.0,
    )
    return plan


def _warn(plan: Plan) -> None:
    core = plan.core().name
    pairs = {(e.from_, e.to) for e in plan.edges if core not in (e.from_, e.to)}
    for first, second in sorted(pairs):
        if (second, first) in pairs and first < second:
            plan.warnings.append(
                f"циклическая зависимость между {first} и {second}: рассмотрите объединение"
            )
    for edge in plan.edges:
        if edge.fields >= 5:
            plan.warnings.append(f"{edge.from_} использует {edge.fields} полей класса {edge.to}")


__all__ = ["Plan", "PlanClass", "PlanEdge", "Stat", "base_name", "build_plan", "monolith"]
