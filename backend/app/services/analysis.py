"""Анализ версии для API: движок → схемы, фильтры, обогащение графов сложностью и находками (S7)."""

from __future__ import annotations

import dataclasses
import logging
from collections import Counter
from typing import Any

from sqlmodel import Session, col, select

from app.config import Settings
from app.db.models import Project, Run, Version
from app.errors import bad_request, not_found
from app.schemas import analysis as schemas
from app.schemas.common import SourceRange
from app.schemas.graph import Badge, FlowGraph, ProjectTree, StructureGraph, TreeNode
from app.services import graphs
from app.services import projects as projects_service
from engine.analysis import analyzer
from engine.analysis.model import AnalysisResult
from engine.java.project import JavaProject

log = logging.getLogger("app.analysis")

LOOP_KINDS = {"loop_for", "loop_foreach", "loop_while"}


def thresholds(project: Project, settings: Settings) -> dict[str, float]:
    configured = projects_service.project_settings(project, settings).rule_thresholds
    return {k: float(v) for k, v in configured.items()}


def run(java: JavaProject, project: Project, settings: Settings) -> AnalysisResult:
    return analyzer.analyze(java, thresholds(project, settings))


def _dump(value: Any) -> Any:
    return dataclasses.asdict(value)


def response(
    java: JavaProject, project: Project, settings: Settings, version: Version
) -> schemas.AnalysisResponse:
    result = run(java, project, settings)
    return schemas.AnalysisResponse.model_validate({**_dump(result), "version_id": version.id or 0})


def findings(
    java: JavaProject,
    project: Project,
    settings: Settings,
    *,
    severity: str | None = None,
    category: str | None = None,
    file: str | None = None,
    fixable: bool | None = None,
) -> list[schemas.Finding]:
    result = run(java, project, settings)
    chosen = [
        f
        for f in result.findings
        if (severity is None or f.severity == severity)
        and (category is None or f.category == category)
        and (file is None or f.location.file == file)
        and (fixable is None or (f.fix is not None and f.fix.available) == fixable)
    ]
    return [schemas.Finding.model_validate(_dump(f)) for f in chosen]


def finding(
    java: JavaProject, project: Project, settings: Settings, finding_id: str
) -> schemas.Finding:
    for item in run(java, project, settings).findings:
        if item.id == finding_id:
            return schemas.Finding.model_validate(_dump(item))
    raise not_found(f"находка {finding_id} не найдена", finding_id=finding_id)


def dynamic_evidence(
    session: Session, project: Project, version: Version, finding_id: str
) -> schemas.DynamicEvidence | None:
    """Результат последней завершённой проверки находки на этой версии (``kind=verify``)."""
    query = (
        select(Run)
        .where(
            Run.project_id == project.id,
            Run.version_id == version.id,
            Run.kind == "verify",
            Run.status == "done",
        )
        .order_by(col(Run.id).desc())
        .limit(30)
    )
    for run in session.exec(query).all():
        data = (run.summary or {}).get("data") or {}
        if data.get("finding_id") == finding_id and data.get("samples"):
            return schemas.DynamicEvidence.model_validate(
                {
                    "run_id": run.id,
                    "samples": data["samples"],
                    "growth_k": data.get("growth_k"),
                    "verdict": data.get("verdict"),
                }
            )
    return None


def metrics(java: JavaProject, project: Project, settings: Settings) -> schemas.Metrics:
    return schemas.Metrics.model_validate(_dump(run(java, project, settings).metrics))


def complexity(
    java: JavaProject,
    project: Project,
    settings: Settings,
    *,
    node_id: str | None,
    file: str | None,
    start_line: int | None,
    end_line: int | None,
) -> schemas.ComplexityResponse:
    analysis = run(java, project, settings)
    if node_id is not None:
        details = graphs.node_details(java, node_id)  # 404, если узла нет
        estimate = analyzer.estimate_node(java, analysis, node_id)
        if estimate is None:
            raise not_found(f"для узла {node_id} нет оценки сложности", node_id=node_id)
        return schemas.ComplexityResponse(
            node_id=node_id,
            range=details.range,
            estimate=schemas.ComplexityEstimate.model_validate(_dump(estimate)),
        )
    if file is None or start_line is None or end_line is None:
        raise bad_request("укажите node_id либо file, start_line и end_line")
    if start_line < 1 or end_line < start_line:
        raise bad_request("неверный диапазон строк", start_line=start_line, end_line=end_line)
    if file not in java.files:
        raise not_found(f"файл {file} не найден", path=file)
    estimate = analyzer.estimate_range(java, analysis, file, start_line, end_line)
    assert estimate is not None
    return schemas.ComplexityResponse(
        range=SourceRange(
            file=file, start_line=start_line, start_col=1, end_line=end_line, end_col=1
        ),
        estimate=schemas.ComplexityEstimate.model_validate(_dump(estimate)),
    )


# --- обогащение графов ---------------------------------------------------------------------------------------


def _findings_by_node(result: AnalysisResult) -> dict[str, list[Any]]:
    by_node: dict[str, list[Any]] = {}
    for item in result.findings:
        if item.location.node_id:
            by_node.setdefault(item.location.node_id, []).append(item)
    return by_node


def enrich_flow(
    model: FlowGraph, java: JavaProject, project: Project, settings: Settings
) -> FlowGraph:
    """Сложность на циклах и методе, бейджи ⚠ на узлах с находками. Ошибки анализа граф не ломают."""
    try:
        result = run(java, project, settings)
        method = analyzer.cost_model(java).method(model.method_id)
        by_node = _findings_by_node(result)
    except Exception:  # noqa: BLE001 - диагностика вторична по отношению к графу
        log.warning("flow enrichment failed", exc_info=True)
        return model
    for node in model.nodes:
        if node.id == model.method_id and method is not None:
            node.complexity = method.cost.format()
        elif node.kind in LOOP_KINDS and method is not None and node.id in method.node_costs:
            node.complexity = method.node_costs[node.id].cost.format()
        for item in by_node.get(node.id, []):
            node.badges.append(Badge(kind="defect", label=item.rule_id, ref=item.id))
    return model


def enrich_structure(
    model: StructureGraph, java: JavaProject, project: Project, settings: Settings
) -> StructureGraph:
    try:
        result = run(java, project, settings)
        cost_model = analyzer.cost_model(java)
    except Exception:  # noqa: BLE001
        log.warning("structure enrichment failed", exc_info=True)
        return model
    counts: Counter[str] = Counter()
    for item in result.findings:
        key = item.location.method_id or item.location.node_id
        if key:
            counts[key] += 1
    for node in model.nodes:
        node.findings_count = counts.get(node.id, 0)
        if node.method_id:
            found = cost_model.method(node.method_id)
            if found is not None:
                node.complexity = found.cost.format()
    return model


def enrich_tree(
    model: ProjectTree, java: JavaProject, project: Project, settings: Settings
) -> ProjectTree:
    try:
        result = run(java, project, settings)
        cost_model = analyzer.cost_model(java)
    except Exception:  # noqa: BLE001
        log.warning("tree enrichment failed", exc_info=True)
        return model
    own: dict[str, Counter[str]] = {}
    for item in result.findings:
        key = item.location.method_id or item.location.node_id
        if key:
            own.setdefault(key, Counter())[item.severity] += 1

    def visit(node: TreeNode) -> Counter[str]:
        total: Counter[str] = Counter(own.get(node.node_id or "", Counter()))
        if node.method_id:
            found = cost_model.method(node.method_id)
            if found is not None:
                node.complexity = found.cost.format()
        for child in node.children:
            total += visit(child)
        node.findings.high = total["high"]
        node.findings.medium = total["medium"]
        node.findings.low = total["low"]
        return total

    for root in model.roots:
        visit(root)
    return model
