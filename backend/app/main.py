"""Точка входа FastAPI: ``uvicorn app.main:app``."""

from __future__ import annotations

import asyncio
import logging
from collections.abc import AsyncIterator
from contextlib import asynccontextmanager

from fastapi import FastAPI
from fastapi.middleware.cors import CORSMiddleware
from sqlmodel import Session

from app import __version__
from app.api import (
    analysis,
    export,
    graph,
    health,
    ops,
    projects,
    runs,
    scenarios,
    transform,
    versions,
)
from app.config import Settings, get_settings
from app.db.models import Project
from app.db.session import get_engine, init_db
from app.errors import register_error_handlers
from app.jobs.registry import all_handlers
from app.jobs.run_manager import RunManager
from app.logging_config import RequestIdMiddleware, configure_logging
from app.openapi import install_openapi_extras
from app.schemas.verify import RunRequest
from app.services.seeding import seed_demo


@asynccontextmanager
async def lifespan(app: FastAPI) -> AsyncIterator[None]:
    settings = get_settings()
    configure_logging(settings.log_level)
    settings.ensure_dirs()
    init_db()
    manager = RunManager(settings, all_handlers())
    app.state.run_manager = manager
    log = logging.getLogger("app")
    interrupted = await asyncio.to_thread(manager.recover)
    if interrupted:
        log.warning("marked interrupted runs", extra={"count": interrupted})
    if settings.seed_demo:
        seeded = await asyncio.to_thread(_seed, settings)
        if seeded is not None and settings.seed_prepare:
            app.state.seed_task = asyncio.create_task(_prepare_demo(manager, seeded, log))
    log.info(
        "started", extra={"data_dir": str(settings.data_dir.resolve()), "version": __version__}
    )
    yield
    task = getattr(app.state, "seed_task", None)
    if task is not None and not task.done():
        task.cancel()
    await manager.shutdown()


def _seed(settings: Settings) -> int | None:
    with Session(get_engine()) as session:
        project = seed_demo(session, settings)
        return project.id if project is not None else None


async def _prepare_demo(manager: RunManager, project_id: int, log: logging.Logger) -> None:
    """Демо-проект «готовый»: эталон по оригиналу и зелёный gate, чтобы значок поведения сразу был ✓."""
    try:
        for kind in ("baseline", "gate"):
            with Session(get_engine()) as session:
                project = session.get(Project, project_id)
                if project is None:
                    return
                version_id = project.head_version_id or 0
            run_id = await manager.submit(project, RunRequest(kind=kind), version_id)  # type: ignore[arg-type]
            await manager.wait_finished(run_id, timeout_s=300.0)
        log.info("demo project is ready", extra={"project_id": project_id})
    except Exception:  # noqa: BLE001 - без JDK демо остаётся без эталона, сервер работает
        log.warning("demo project was not prepared", exc_info=True)


def create_app() -> FastAPI:
    app = FastAPI(
        title="Java Blueprints API",
        version=__version__,
        description="Визуальная среда программирования для Java: код ↔ граф, анализ, проверка поведения.",
        openapi_url="/api/openapi.json",
        docs_url="/api/docs",
        redoc_url=None,
        lifespan=lifespan,
        # operationId = имя функции-обработчика: читаемые имена в сгенерированных TS-типах.
        # Имена обработчиков должны быть уникальны во всём приложении.
        generate_unique_id_function=lambda route: route.name,
    )
    settings = get_settings()
    if settings.cors_origin_list:
        app.add_middleware(
            CORSMiddleware,
            allow_origins=settings.cors_origin_list,
            allow_methods=["*"],
            allow_headers=["*"],
        )
    app.add_middleware(RequestIdMiddleware)
    register_error_handlers(app)
    for module in (
        health,
        projects,
        versions,
        graph,
        ops,
        analysis,
        transform,
        scenarios,
        runs,
        export,
    ):
        app.include_router(module.router, prefix="/api")
    install_openapi_extras(app)
    return app


app = create_app()
