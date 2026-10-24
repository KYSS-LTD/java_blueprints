"""Анализ (docs/08-api.md § 6, S7): находки, сложность, метрики. «Проверить» — вместе с бенчмарком (S6B)."""

from __future__ import annotations

from fastapi import APIRouter
from fastapi.concurrency import run_in_threadpool

from app.api.common import ERROR_RESPONSES
from app.deps import ProjectDep, RunManagerDep, SessionDep, SettingsDep
from app.schemas.analysis import (
    AnalysisResponse,
    Category,
    ComplexityResponse,
    Finding,
    FindingsResponse,
    MetricsResponse,
    Severity,
)
from app.schemas.verify import RunRef, RunRequest
from app.services import analysis as service
from app.services import graphs
from app.services import versions as versions_service

router = APIRouter(tags=["analysis"], responses=ERROR_RESPONSES)


@router.get("/projects/{project_id}/analysis", summary="Полный результат анализа версии")
def get_analysis(
    project: ProjectDep, session: SessionDep, settings: SettingsDep, version_id: int | None = None
) -> AnalysisResponse:
    version = versions_service.resolve_version(session, project, version_id)
    java = graphs.java_project(session, version)
    return service.response(java, project, settings, version)


@router.get("/projects/{project_id}/findings", summary="Находки с фильтрами")
def list_findings(
    project: ProjectDep,
    session: SessionDep,
    settings: SettingsDep,
    version_id: int | None = None,
    severity: Severity | None = None,
    category: Category | None = None,
    file: str | None = None,
    fixable: bool | None = None,
) -> FindingsResponse:
    version = versions_service.resolve_version(session, project, version_id)
    java = graphs.java_project(session, version)
    found = service.findings(
        java, project, settings, severity=severity, category=category, file=file, fixable=fixable
    )
    return FindingsResponse(version_id=version.id or 0, findings=found)


@router.post(
    "/projects/{project_id}/findings/{finding_id:path}/verify",
    status_code=202,
    summary="Динамическая проверка находки",
    description="Запускает `kind=verify` (бенчмарк или трассировка); дальше — SSE запуска.",
)
async def verify_finding(
    project: ProjectDep,
    session: SessionDep,
    settings: SettingsDep,
    manager: RunManagerDep,
    finding_id: str,
) -> RunRef:
    def prepare() -> int:
        version = versions_service.resolve_version(session, project, None)
        service.finding(graphs.java_project(session, version), project, settings, finding_id)  # 404
        return version.id or 0

    version_id = await run_in_threadpool(prepare)
    request = RunRequest(kind="verify", finding_id=finding_id, version_id=version_id)
    return RunRef(run_id=await manager.submit(project, request, version_id))


@router.get(
    "/projects/{project_id}/findings/{finding_id:path}",
    summary="Карточка находки",
    description="Объяснение, вклады в сложность, доказательства, исправление.",
)
def get_finding(
    project: ProjectDep,
    session: SessionDep,
    settings: SettingsDep,
    finding_id: str,
    version_id: int | None = None,
) -> Finding:
    version = versions_service.resolve_version(session, project, version_id)
    java = graphs.java_project(session, version)
    card = service.finding(java, project, settings, finding_id)
    card.evidence.dynamic = service.dynamic_evidence(session, project, version, finding_id)
    return card


@router.get(
    "/projects/{project_id}/complexity",
    summary="Оценка сложности узла или фрагмента",
    description="Задаётся `node_id` либо `file` + `start_line` + `end_line`; ответ с цепочкой вкладов.",
)
def get_complexity(
    project: ProjectDep,
    session: SessionDep,
    settings: SettingsDep,
    version_id: int | None = None,
    node_id: str | None = None,
    file: str | None = None,
    start_line: int | None = None,
    end_line: int | None = None,
) -> ComplexityResponse:
    version = versions_service.resolve_version(session, project, version_id)
    java = graphs.java_project(session, version)
    return service.complexity(
        java,
        project,
        settings,
        node_id=node_id,
        file=file,
        start_line=start_line,
        end_line=end_line,
    )


@router.get("/projects/{project_id}/metrics", summary="Метрики версии")
def get_metrics(
    project: ProjectDep,
    session: SessionDep,
    settings: SettingsDep,
    version_id: int | None = None,
    compare_to: int | None = None,
) -> MetricsResponse:
    version = versions_service.resolve_version(session, project, version_id)
    current = service.metrics(graphs.java_project(session, version), project, settings)
    other = None
    if compare_to is not None:
        base = versions_service.get_version(session, project, compare_to)
        other = service.metrics(graphs.java_project(session, base), project, settings)
    return MetricsResponse(
        version_id=version.id or 0,
        metrics=current,
        compare_to_version_id=compare_to,
        compare_to=other,
    )
