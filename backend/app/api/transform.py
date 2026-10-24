"""Преобразования (docs/08-api.md § 7): исправления и оптимизация — S8; выделение метода и
декомпозиция — S9 (заглушки контракта)."""

from __future__ import annotations

from fastapi import APIRouter
from fastapi.concurrency import run_in_threadpool

from app.api.common import ERROR_RESPONSES
from app.deps import ProjectDep, RunManagerDep, SessionDep, SettingsDep
from app.schemas.transform import (
    ApplyResult,
    CandidateDecision,
    DecomposeEdit,
    DecomposePlan,
    DecomposePlanRequest,
    DecomposePreview,
    Effect,
    ExtractMethodApply,
    ExtractMethodPreview,
    ExtractMethodRequest,
    FixApply,
    FixList,
    FixPreview,
    FixPreviewRequest,
    OptimizeApply,
    OptimizePlan,
    OptimizePlanRequest,
    OptimizeStep,
)
from app.schemas.verify import RunRef, RunRequest
from app.services import analysis as analysis_service
from app.services import decompose as decompose_service
from app.services import extract as extract_service
from app.services import fixes as service
from app.services import graphs
from app.services import versions as versions_service
from engine.transform.fixes import propose

router = APIRouter(tags=["transform"], responses=ERROR_RESPONSES)

_APPLY_NOTE = (
    "Создаётся версия-кандидат и запускается gate (`syntax → compile → scenarios`). "
    "Зелёный gate делает кандидата HEAD; красный — отклоняет."
)


@router.get("/projects/{project_id}/fixes", summary="Доступные исправления версии")
def list_fixes(
    project: ProjectDep, session: SessionDep, settings: SettingsDep, version_id: int | None = None
) -> FixList:
    version = versions_service.resolve_version(session, project, version_id)
    return service.listing(graphs.java_project(session, version), project, settings, version)


@router.post(
    "/projects/{project_id}/fixes/{finding_id:path}/preview",
    summary="Предпросмотр исправления",
    description="Diff, ожидаемый эффект и предусловия. `422 PRECONDITION_FAILED`, если не выполнены.",
)
def preview_fix(
    project: ProjectDep,
    session: SessionDep,
    settings: SettingsDep,
    finding_id: str,
    payload: FixPreviewRequest,
) -> FixPreview:
    version = versions_service.resolve_version(session, project, payload.version_id)
    return service.preview(graphs.java_project(session, version), project, settings, finding_id)


@router.post(
    "/projects/{project_id}/fixes/{finding_id:path}/apply",
    status_code=202,
    summary="Применить исправление",
    description=_APPLY_NOTE,
)
async def apply_fix(
    project: ProjectDep,
    session: SessionDep,
    settings: SettingsDep,
    manager: RunManagerDep,
    finding_id: str,
    payload: FixApply,
) -> ApplyResult:
    def prepare() -> int:
        versions_service.require_head(project, payload.base_version_id)
        head = versions_service.head_version(session, project)
        java = graphs.java_project(session, head)
        finding = service.find(java, project, settings, finding_id)
        proposal = service.proposal_for(java, finding)
        files = service.new_files(java, proposal)
        return service.create_candidate(session, project, head, proposal, files, finding.id).id or 0

    candidate_id = await run_in_threadpool(prepare)
    run_id = await manager.submit(
        project, RunRequest(kind="gate", version_id=candidate_id), candidate_id
    )
    return ApplyResult(candidate_version_id=candidate_id, run_id=run_id)


@router.post("/projects/{project_id}/optimize/plan", summary="План пакетной оптимизации")
def plan_optimize(
    project: ProjectDep, session: SessionDep, settings: SettingsDep, payload: OptimizePlanRequest
) -> OptimizePlan:
    version = versions_service.resolve_version(session, project, payload.version_id)
    java = graphs.java_project(session, version)
    analysis = analysis_service.run(java, project, settings)
    steps: list[OptimizeStep] = []
    taken: list[tuple[str, int, int]] = []
    for item in analysis.findings:
        if item.fix is None or not item.fix.available or not item.fix.behavior_preserving:
            continue
        place = item.location
        if any(
            f == place.file and not (place.end_line < a or place.start_line > b)
            for f, a, b in taken
        ):
            continue  # пересекающиеся диапазоны исправляются по одному
        taken.append((place.file, place.start_line, place.end_line))
        proposal = propose(java, item)
        complexity = item.complexity
        steps.append(
            OptimizeStep(
                step_id=item.id,
                fix_id=item.fix.fix_id,
                finding_id=item.id,
                title=proposal.title if proposal is not None else item.fix.fix_id,
                effect=Effect(
                    complexity_before=complexity.current if complexity else None,
                    complexity_after=complexity.after if complexity else None,
                ),
                behavior_preserving=True,
            )
        )
    return OptimizePlan(
        version_id=version.id or 0,
        steps=steps,
        effect=Effect(
            complexity_before=analysis.complexity.current,
            complexity_after=analysis.complexity.optimized,
            notes="оценка по модели Big O; измеренный эффект — после бенчмарка",
        ),
    )


@router.post(
    "/projects/{project_id}/optimize/apply",
    status_code=202,
    summary="Применить шаги оптимизации по очереди",
    description="Прогресс шагов приходит в SSE запуска событиями `step`.",
)
async def apply_optimize(
    project: ProjectDep, session: SessionDep, manager: RunManagerDep, payload: OptimizeApply
) -> RunRef:
    def prepare() -> int:
        versions_service.require_head(project, payload.base_version_id)
        return versions_service.head_version(session, project).id or 0

    head_id = await run_in_threadpool(prepare)
    request = RunRequest(kind="optimize", version_id=head_id, step_ids=payload.step_ids)
    return RunRef(run_id=await manager.submit(project, request, head_id))


@router.post(
    "/projects/{project_id}/extract-method/preview",
    summary="Предпросмотр выделения метода",
    description=(
        "Фрагмент задаётся `node_ids` (подряд идущие блоки одного блока схемы) либо `range`. "
        "Ответ содержит имя, сигнатуру, код нового метода, предусловия и diff; если выделить нельзя, "
        "`preconditions` объясняют почему, а `diff` пуст."
    ),
)
def preview_extract_method(
    project: ProjectDep, session: SessionDep, payload: ExtractMethodRequest
) -> ExtractMethodPreview:
    version = versions_service.resolve_version(session, project, payload.version_id)
    return extract_service.preview(graphs.java_project(session, version), payload)


@router.post(
    "/projects/{project_id}/extract-method/apply",
    status_code=202,
    summary="Выделить метод",
    description=_APPLY_NOTE,
)
async def apply_extract_method(
    project: ProjectDep, session: SessionDep, manager: RunManagerDep, payload: ExtractMethodApply
) -> ApplyResult:
    def prepare() -> int:
        versions_service.require_head(project, payload.base_version_id)
        head = versions_service.head_version(session, project)
        java = graphs.java_project(session, head)
        return extract_service.candidate(session, project, head, java, payload).id or 0

    candidate_id = await run_in_threadpool(prepare)
    run_id = await manager.submit(
        project, RunRequest(kind="gate", version_id=candidate_id), candidate_id
    )
    return ApplyResult(candidate_version_id=candidate_id, run_id=run_id)


@router.post(
    "/projects/{project_id}/decompose/plan",
    summary="План декомпозиции монолита",
    description=(
        "Класс с `main` (иначе самый крупный) разбивается на ядро и 2–6 новых классов по связности "
        "методов (Louvain, фиксированный seed — результат детерминирован). Поля, конструкторы и точка "
        "входа остаются в ядре; прочие типы файла выносятся в отдельные файлы."
    ),
)
def plan_decompose(
    project: ProjectDep, session: SessionDep, payload: DecomposePlanRequest
) -> DecomposePlan:
    version = versions_service.resolve_version(session, project, payload.version_id)
    return decompose_service.make_plan(graphs.java_project(session, version), payload.options)


@router.post(
    "/projects/{project_id}/decompose/preview",
    summary="Предпросмотр декомпозиции по отредактированному плану",
    description=(
        "Применяет план к HEAD без сохранения: diff (новые файлы — `added`) и предупреждения, "
        "в том числе о том, что результат не компилируется."
    ),
)
def preview_decompose(
    project: ProjectDep, session: SessionDep, settings: SettingsDep, payload: DecomposeEdit
) -> DecomposePreview:
    versions_service.require_head(project, payload.base_version_id)
    head = versions_service.head_version(session, project)
    return decompose_service.preview(graphs.java_project(session, head), settings, payload.plan)


@router.post(
    "/projects/{project_id}/decompose/apply",
    status_code=202,
    summary="Применить декомпозицию",
    description=_APPLY_NOTE + " В `Version.meta` записывается `symbol_moves` (член → новый класс).",
)
async def apply_decompose(
    project: ProjectDep, session: SessionDep, manager: RunManagerDep, payload: DecomposeEdit
) -> ApplyResult:
    def prepare() -> int:
        versions_service.require_head(project, payload.base_version_id)
        head = versions_service.head_version(session, project)
        java = graphs.java_project(session, head)
        return decompose_service.candidate(session, project, head, java, payload.plan).id or 0

    candidate_id = await run_in_threadpool(prepare)
    run_id = await manager.submit(
        project, RunRequest(kind="gate", version_id=candidate_id), candidate_id
    )
    return ApplyResult(candidate_version_id=candidate_id, run_id=run_id)


@router.post(
    "/projects/{project_id}/versions/{version_id}/accept",
    summary="Подтвердить кандидата после красного gate",
    description=(
        "Для `behavior_preserving = false`: кандидат становится HEAD. Эталоны сценариев остаются "
        "прежними: после подтверждения расхождений их нужно записать заново (`kind=baseline`)."
    ),
)
def accept_candidate(
    project: ProjectDep, session: SessionDep, version_id: int
) -> CandidateDecision:
    return service.accept(session, project, version_id)


@router.post("/projects/{project_id}/versions/{version_id}/reject", summary="Отклонить кандидата")
def reject_candidate(
    project: ProjectDep, session: SessionDep, version_id: int
) -> CandidateDecision:
    return service.reject(session, project, version_id)
