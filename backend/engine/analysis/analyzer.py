"""Анализ версии: метрики, находки, сложность программы и оценка произвольного узла (S7.2, S7.4)."""

from __future__ import annotations

import re
from collections import Counter
from collections.abc import Mapping
from weakref import WeakKeyDictionary

from engine.analysis.complexity import Cost
from engine.analysis.cost_model import CostModel, CostResult
from engine.analysis.metrics import MetricsResult, compute_metrics
from engine.analysis.model import (
    AnalysisResult,
    AnalysisSummary,
    ComplexityEstimate,
    ComplexityPair,
    Finding,
    FixAvailability,
    Precondition,
)
from engine.analysis.rules import RULES, AnalysisContext, estimate_of
from engine.analysis.rules_extra import RULES_EXTRA
from engine.analysis.rules_more import RULES_MORE
from engine.graph.ids import split_node_id
from engine.java.project import FLOWABLE, JavaProject

_SEVERITY_ORDER = {"high": 0, "medium": 1, "low": 2}
_POWERS = {"²": 2, "³": 3, "⁴": 4, "⁵": 5}
_MODELS: WeakKeyDictionary[JavaProject, CostModel] = WeakKeyDictionary()
_METRICS: WeakKeyDictionary[JavaProject, MetricsResult] = WeakKeyDictionary()
_RESULTS: WeakKeyDictionary[JavaProject, dict[tuple[tuple[str, float], ...], AnalysisResult]] = (
    WeakKeyDictionary()
)


def cost_model(project: JavaProject) -> CostModel:
    model = _MODELS.get(project)
    if model is None:
        model = _MODELS[project] = CostModel(project)
    return model


def metrics_result(project: JavaProject) -> MetricsResult:
    found = _METRICS.get(project)
    if found is None:
        found = _METRICS[project] = compute_metrics(project, cost_model(project))
    return found


def _degree(text: str) -> float:
    """Порядок роста из строки ``O(n²)`` для сортировки находок."""
    if "2ⁿ" in text:
        return 99.0
    match = re.search(r"n([²³⁴⁵])?", text)
    power = 0 if match is None else _POWERS.get(match.group(1) or "", 1)
    return power + (0.5 if "log" in text else 0.0)


def _sort_key(finding: Finding) -> tuple[int, float, str, int, str]:
    degree = _degree(finding.complexity.current) if finding.complexity else 0.0
    return (
        _SEVERITY_ORDER[finding.severity],
        -degree,
        finding.location.file,
        finding.location.start_line,
        finding.id,
    )


def _optimized(project: JavaProject, findings: list[Finding]) -> Cost | None:
    """Оценка «после оптимизаций»: у методов с PERF-находками степень ``n`` снижается на 1."""
    improved = frozenset(
        f.location.method_id
        for f in findings
        if f.category == "PERF"
        and f.complexity is not None
        and f.complexity.after
        and f.location.method_id
    )
    return CostModel(project, improved).program_cost()


def _attach_fixes(project: JavaProject, findings: list[Finding]) -> None:
    """Для находок с исправлением: доступно ли оно и какие предусловия не выполнены."""
    from engine.transform.fixes import propose

    for finding in findings:
        proposal = propose(project, finding)
        if proposal is None:
            continue
        finding.fix = FixAvailability(
            fix_id=proposal.fix_id,
            available=proposal.available,
            behavior_preserving=proposal.behavior_preserving,
            preconditions=[Precondition(c.name, c.passed, c.reason) for c in proposal.checks],
        )


def analyze(project: JavaProject, thresholds: Mapping[str, float] | None = None) -> AnalysisResult:
    """Находки, метрики и сложность программы; результат кешируется на проекте (по порогам)."""
    key = tuple(sorted((thresholds or {}).items()))
    cache = _RESULTS.setdefault(project, {})
    cached = cache.get(key)
    if cached is not None:
        return cached

    model = cost_model(project)
    metrics = metrics_result(project)
    context = AnalysisContext(project, model, metrics, dict(thresholds or {}))
    findings: list[Finding] = []
    for rule in (*RULES, *RULES_EXTRA, *RULES_MORE):
        findings.extend(rule(context))
    findings.sort(key=_sort_key)
    _attach_fixes(project, findings)

    current = model.program_cost()
    optimized = _optimized(project, findings)
    summary = AnalysisSummary(
        total=len(findings),
        by_severity=dict(Counter(f.severity for f in findings)),
        by_category=dict(Counter(f.category for f in findings)),
        fixable=sum(1 for f in findings if f.fix is not None and f.fix.available),
    )
    result = AnalysisResult(
        findings=findings,
        summary=summary,
        complexity=ComplexityPair(
            current.format(),
            optimized.format() if optimized is not None and optimized != current else None,
        ),
        metrics=metrics.metrics,
    )
    cache[key] = result
    return result


# --- оценка узла и фрагмента ------------------------------------------------------------------------------------


def _after_for(analysis: AnalysisResult, node_id: str) -> str | None:
    for finding in analysis.findings:
        estimate = finding.complexity
        if estimate is not None and estimate.after and finding.location.node_id == node_id:
            return estimate.after
    return None


def estimate_node(
    project: JavaProject, analysis: AnalysisResult, node_id: str
) -> ComplexityEstimate | None:
    """Сложность метода, цикла или блока по id узла; ``None`` — такого узла нет."""
    member_id, path = split_node_id(node_id)
    model = cost_model(project)
    found = model.method(member_id)
    if found is None:
        return None
    result = found.result if not path else found.node_costs.get(node_id)
    if result is None:
        return None
    estimate = estimate_of(result)
    estimate.after = _after_for(analysis, node_id)
    return estimate


def estimate_range(
    project: JavaProject, analysis: AnalysisResult, file: str, start_line: int, end_line: int
) -> ComplexityEstimate | None:
    """Сложность выделенных строк: доминирующая из самых внешних блоков внутри диапазона."""
    if file not in project.files:
        return None
    model = cost_model(project)
    best: CostResult | None = None
    for member in project.members():
        if member.kind not in FLOWABLE or project.file_of(member) != file:
            continue
        graph = project.flow(member.id)
        found = model.method(member.id)
        if graph is None or found is None:
            continue
        inside = {
            n.id: n
            for n in graph.nodes
            if n.id != member.id
            and n.range.start_line >= start_line
            and n.range.end_line <= end_line
        }
        for node_id, node in inside.items():
            if node.parent_id in inside:
                continue  # вложенный блок уже входит в стоимость внешнего
            part = found.node_costs.get(node_id)
            if part is None:
                continue
            best = part if best is None else best.then(part)
    return estimate_of(best if best is not None else CostResult())
