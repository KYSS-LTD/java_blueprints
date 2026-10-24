"""Запуски, SSE и сборка (docs/08-api.md § 8.2–8.3, § 9). Запуск ``run`` и сборка — S3;
остальные виды запусков (gate, эталон, отчёты) — этап S6."""

from __future__ import annotations

from typing import Any, Literal

from fastapi import APIRouter, Header, Response
from fastapi.concurrency import run_in_threadpool

from app.api.common import ERROR_JSON_RESPONSE, ERROR_RESPONSES, SseResponse
from app.deps import ProjectDep, RunManagerDep, SessionDep, SettingsDep
from app.errors import bad_request, not_implemented
from app.schemas.verify import (
    BuildRequest,
    BuildResult,
    RunEvent,
    RunInfo,
    RunList,
    RunRef,
    RunRequest,
)
from app.services import builds, reports
from app.services import runs as runs_service
from app.services import versions as versions_service

router = APIRouter(tags=["runs"], responses=ERROR_RESPONSES)

_REPORT_TYPES: dict[int | str, dict[str, Any]] = {
    200: {
        "description": "Отчёт в выбранном формате",
        "content": {
            "application/json": {"schema": {"type": "object"}},
            "text/markdown": {"schema": {"type": "string"}},
            "application/xml": {"schema": {"type": "string"}},
        },
    }
}


@router.post(
    "/projects/{project_id}/runs",
    status_code=202,
    summary="Запустить задачу",
    description=(
        "`kind`: `run`, `trace`, `baseline`, `test`, `gate`, `optimize`, `generate`, `benchmark`, "
        "`verify`. Ответ `202`; ход выполнения — `GET /runs/{run_id}/events`. `429 BUSY` — слишком "
        "много параллельных запусков. `trace` — «Дебаг»: свой ввод (`stdin`/`args`) или сценарии "
        "(`scenario_ids`, без полей — все включённые); счётчики блоков — в `items[].hits`, покрытие "
        "и исходы условий — в `summary`. `generate`, `benchmark`, `verify` пока отвечают `501`."
    ),
)
async def create_run(
    project: ProjectDep, session: SessionDep, manager: RunManagerDep, payload: RunRequest
) -> RunRef:
    if payload.kind not in manager.handlers:
        raise not_implemented("S6")
    if payload.kind == "run" and project.main_class is None:
        raise bad_request("у проекта нет главного класса: выберите его в настройках проекта")
    # эталон по умолчанию пишется на эталонной версии проекта, остальное — на HEAD
    target = payload.version_id
    if payload.kind == "baseline" and target is None:
        target = project.baseline_version_id
    version = await run_in_threadpool(versions_service.resolve_version, session, project, target)
    return RunRef(run_id=await manager.submit(project, payload, version.id or 0))


@router.get("/projects/{project_id}/runs", summary="История запусков")
def list_runs(project: ProjectDep, session: SessionDep) -> RunList:
    return RunList(runs=runs_service.history(session, project))


@router.get("/runs/{run_id}", summary="Статус и итог запуска")
def get_run(run_id: int, session: SessionDep) -> RunInfo:
    return runs_service.info(session, runs_service.get_or_404(session, run_id))


@router.get(
    "/runs/{run_id}/events",
    summary="SSE-поток запуска",
    description=(
        "`text/event-stream`: события `status`, `log`, `gate`, `item`, `step`, `progress`, "
        "`result`, `error`, `done` (каждое — `event:` + `data:` JSON). Поздние подписчики "
        "получают replay с начала (с заголовком `Last-Event-ID` — после указанного события); "
        "heartbeat-комментарий каждые 15 секунд."
    ),
    response_class=SseResponse,
    responses={
        200: {"model": RunEvent, "description": "Поток событий запуска"},
        **ERROR_JSON_RESPONSE,
    },
)
async def run_events(
    run_id: int,
    session: SessionDep,
    manager: RunManagerDep,
    last_event_id: str | None = Header(default=None),
) -> Any:
    run = await run_in_threadpool(runs_service.get_or_404, session, run_id)
    start = int(last_event_id) + 1 if last_event_id and last_event_id.isdigit() else 0
    if run_id in manager.states:
        return SseResponse(manager.stream(run_id, start), ping=15)
    # Запуска нет в памяти (перезапуск сервера, вытеснение): события восстанавливаются из БД.
    info = await run_in_threadpool(runs_service.info, session, run)
    stored = manager.stored(runs_service.replay_events(info), start)

    async def replay() -> Any:
        for event in stored:
            yield event

    return SseResponse(replay(), ping=15)


@router.post("/runs/{run_id}/cancel", summary="Отменить запуск")
async def cancel_run(run_id: int, session: SessionDep, manager: RunManagerDep) -> RunInfo:
    run = await run_in_threadpool(runs_service.get_or_404, session, run_id)
    if manager.cancel(run_id):
        await manager.wait_finished(run_id)  # итог в ответе: статус уже `cancelled`
        session.refresh(run)
    return await run_in_threadpool(runs_service.info, session, run)


@router.get(
    "/runs/{run_id}/report",
    summary="Отчёт запуска для скачивания",
    response_class=Response,
    responses=_REPORT_TYPES,
)
def get_run_report(
    run_id: int, session: SessionDep, format: Literal["json", "md", "junit"] = "json"
) -> Any:
    run = runs_service.get_or_404(session, run_id)
    body, media_type, filename = reports.run_report(session, run, format)
    return Response(
        body,
        media_type=media_type,
        headers={"Content-Disposition": f'attachment; filename="{filename}"'},
    )


@router.post(
    "/projects/{project_id}/build",
    summary="Синхронная сборка версии",
    description="Компиляция HEAD или `version_id`; кеш по хешу. Для быстрых ошибок в маркерах Monaco.",
)
def build_project(
    project: ProjectDep, session: SessionDep, settings: SettingsDep, payload: BuildRequest
) -> BuildResult:
    version = versions_service.resolve_version(session, project, payload.version_id)
    files = versions_service.version_files(session, version.id or 0)
    return builds.build(settings, files, version.content_hash)
