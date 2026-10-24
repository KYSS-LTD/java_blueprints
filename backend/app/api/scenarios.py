"""Сценарии (docs/08-api.md § 8.1). CRUD, импорт и экспорт — S6.1; генерация и схема ввода — S6.8–S6.9."""

from __future__ import annotations

import dataclasses

from fastapi import APIRouter, Response
from fastapi.concurrency import run_in_threadpool

from app.api.common import ERROR_RESPONSES
from app.deps import ProjectDep, RunManagerDep, SessionDep
from app.errors import bad_request
from app.schemas.verify import (
    InputSchema,
    RunRef,
    RunRequest,
    Scenario,
    ScenarioCreate,
    ScenarioExport,
    ScenarioImport,
    ScenarioImportResult,
    ScenarioList,
    ScenarioUpdate,
)
from app.services import graphs
from app.services import scenarios as service
from app.services import versions as versions_service
from engine.verify.generator.input_schema import infer

router = APIRouter(tags=["scenarios"], responses=ERROR_RESPONSES)


@router.get("/projects/{project_id}/scenarios", summary="Сценарии со статусом эталона")
def list_scenarios(project: ProjectDep, session: SessionDep) -> ScenarioList:
    return ScenarioList(scenarios=service.listing(session, project))


@router.post("/projects/{project_id}/scenarios", status_code=201, summary="Создать сценарий")
def create_scenario(project: ProjectDep, session: SessionDep, payload: ScenarioCreate) -> Scenario:
    return service.create(session, project, payload)


@router.post(
    "/projects/{project_id}/scenarios/import",
    summary="Импорт сценариев",
    description='Формат `{"scenarios": [...]}`; пары `*.in` + `*.out` frontend собирает в этот же формат.',
)
def import_scenarios(
    project: ProjectDep, session: SessionDep, payload: ScenarioImport
) -> ScenarioImportResult:
    return service.import_items(session, project, payload)


@router.get("/projects/{project_id}/scenarios/export", summary="Экспорт всех сценариев в JSON")
def export_scenarios(project: ProjectDep, session: SessionDep) -> ScenarioExport:
    return service.export(session, project)


@router.post(
    "/projects/{project_id}/scenarios/generate",
    status_code=202,
    summary="Сгенерировать сценарии",
    description="Запуск `kind=generate`; результат — сценарии с `source=generated`.",
)
async def generate_scenarios(
    project: ProjectDep,
    session: SessionDep,
    manager: RunManagerDep,
    version_id: int | None = None,
) -> RunRef:
    target = version_id
    if target is None:  # сценарии нужны эталонной версии: на ней записывается их эталон
        target = project.baseline_version_id
    version = await run_in_threadpool(versions_service.resolve_version, session, project, target)
    if project.main_class is None:
        raise bad_request("у проекта нет главного класса: выберите его в настройках проекта")
    run_id = await manager.submit(
        project, RunRequest(kind="generate", version_id=version.id), version.id or 0
    )
    return RunRef(run_id=run_id)


@router.put(
    "/projects/{project_id}/scenarios/{scenario_id}",
    summary="Изменить сценарий (сбрасывает эталон)",
    description="Меняются `stdin`, `args` или маски — эталон сценария удаляется.",
)
def update_scenario(
    project: ProjectDep, session: SessionDep, scenario_id: int, payload: ScenarioUpdate
) -> Scenario:
    return service.update(session, project, scenario_id, payload)


@router.delete(
    "/projects/{project_id}/scenarios/{scenario_id}",
    status_code=204,
    response_class=Response,
    summary="Удалить сценарий",
)
def delete_scenario(project: ProjectDep, session: SessionDep, scenario_id: int) -> None:
    service.remove(session, project, scenario_id)


@router.get(
    "/projects/{project_id}/input-schema",
    summary="Выведенная схема ввода",
    description="Для отладки и отображения: как генератор понял формат ввода программы.",
)
def get_input_schema(
    project: ProjectDep, session: SessionDep, version_id: int | None = None
) -> InputSchema:
    version = versions_service.resolve_version(session, project, version_id)
    java = graphs.java_project(session, version)
    hints = infer(java, project.main_class)
    return InputSchema.model_validate(
        {"inferred": hints.inferred, "root": [dataclasses.asdict(node) for node in hints.schema]}
    )
