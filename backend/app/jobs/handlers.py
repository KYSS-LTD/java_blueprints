"""Обработчики видов запуска. Сейчас: ``run`` (S3.6). Остальные виды появляются на этапе S6."""

from __future__ import annotations

import threading
from dataclasses import dataclass

from sqlmodel import Session

from app.db.models import RunStatus
from app.db.session import get_engine
from app.errors import bad_request, payload_too_large
from app.jobs.run_manager import Handler, JobContext, Outcome
from app.schemas import verify as schemas
from app.services import builds, importing
from app.services import versions as versions_service
from engine.java.project import get_project
from engine.runtime.compiler import CompileResult
from engine.runtime.runner import RunLimits, RunResult, run_java
from engine.runtime.workspace import Workspace

CHUNK = 8192
MAX_BUILD_LOG = 20_000


def _chunks(text: str) -> list[str]:
    return [text[i : i + CHUNK] for i in range(0, len(text), CHUNK)]


def verdict_of(result: RunResult) -> str:
    if result.status == "timeout":
        return "TIMEOUT"
    if result.status == "ok":
        return "PASS" if result.exit_code == 0 else "FAIL"
    if result.status == "exception":
        return "FAIL"
    return "ERROR"


_SLOTS_LOCK = threading.Lock()
_SLOTS: dict[int, threading.BoundedSemaphore] = {}


def jvm_slots(limit: int) -> threading.BoundedSemaphore:
    """Общий для процесса лимит одновременных JVM (``JB_MAX_PARALLEL_RUNS``, docs/08 § 11)."""
    with _SLOTS_LOCK:
        if limit not in _SLOTS:
            _SLOTS[limit] = threading.BoundedSemaphore(limit)
        return _SLOTS[limit]


@dataclass(slots=True)
class Built:
    files: dict[str, str]
    content_hash: str
    workspace: Workspace
    compiled: CompileResult


def limits_of(ctx: JobContext) -> RunLimits:
    config = ctx.project_settings
    return RunLimits(
        timeout_s=config.get("run_timeout_s", ctx.settings.run_timeout_s),
        output_limit_bytes=config.get("output_limit_bytes", ctx.settings.output_limit_bytes),
        xmx=config.get("jvm_xmx", ctx.settings.jvm_xmx),
    )


def run_in_slot(
    ctx: JobContext,
    built: Built,
    main_class: str,
    *,
    stdin: str,
    args: list[str],
    trace_limit: int | None = None,
    limits: RunLimits | None = None,
) -> RunResult:
    """Одно выполнение программы под общим лимитом параллельных JVM.

    ``trace_limit`` включает сбор счётчиков (сборка должна быть инструментированной);
    ``limits`` заменяет лимиты проекта (бенчмарк даёт больше времени).
    """
    config = ctx.project_settings
    with jvm_slots(ctx.settings.max_parallel_runs):
        return run_java(
            built.workspace,
            main_class,
            stdin=stdin,
            args=args,
            java_home=ctx.settings.java_home,
            limits=limits or limits_of(ctx),
            locale=config.get("locale", "en_US"),
            timezone=config.get("timezone", "UTC"),
            cancel=ctx.cancel,
            trace_limit=trace_limit,
        )


def cancelled() -> Outcome:
    return Outcome(RunStatus.CANCELLED.value, schemas.RunSummary(message="отменён"))


def build_version(
    ctx: JobContext, version_id: int | None = None
) -> tuple[Built | None, Outcome | None]:
    """Сборка версии запуска: либо ``Built``, либо готовый итог (отмена или ошибки компиляции)."""
    target = version_id or ctx.version_id
    with Session(get_engine()) as session:
        version = versions_service.get_version_by_id(session, target)
        files = versions_service.version_files(session, target)
        content_hash = version.content_hash

    ctx.log("info", "Сборка…")
    workspace, compiled = builds.prepare(ctx.settings, files, content_hash)
    if compiled.log.strip():
        ctx.log("build", compiled.log.strip()[:MAX_BUILD_LOG])
    if ctx.cancel.is_set():
        return None, cancelled()
    if not compiled.ok:
        errors = [d for d in compiled.diagnostics if d.severity == "error"]
        message = f"Ошибки компиляции: {len(errors)}"
        ctx.emit(schemas.ErrorEvent(data=schemas.ErrorData(code="COMPILE_ERROR", message=message)))
        diagnostics = builds.diagnostics(compiled, get_project(files))
        return None, Outcome(
            RunStatus.FAILED.value,
            schemas.RunSummary(
                message=message,
                data={
                    "stage": "compile",
                    "diagnostics": [d.model_dump(mode="json") for d in diagnostics],
                },
            ),
        )
    return Built(files, content_hash, workspace, compiled), None


def run_program(ctx: JobContext) -> Outcome:
    """Компиляция и одно выполнение главного класса с ``stdin``/``args`` из запроса."""
    request = ctx.request
    if request.stdin is not None and len(request.stdin.encode("utf-8")) > importing.MAX_FILE_BYTES:
        raise payload_too_large("stdin больше 2 МБ")
    if ctx.main_class is None:
        raise bad_request("у проекта нет главного класса: выберите его в настройках проекта")

    built, failure = build_version(ctx)
    if failure is not None or built is None:
        return failure or cancelled()

    limits = limits_of(ctx)
    ctx.log("info", f"Запуск {ctx.main_class}")
    result = run_in_slot(
        ctx, built, ctx.main_class, stdin=request.stdin or "", args=request.args or []
    )
    for part in _chunks(result.stdout):
        ctx.log("stdout", part)
    for part in _chunks(result.stderr):
        ctx.log("stderr", part)

    if result.status == "cancelled":
        return cancelled()
    verdict = verdict_of(result)
    item = schemas.RunItem(
        name=ctx.main_class,
        verdict=verdict,  # type: ignore[arg-type]
        stdout=result.stdout,
        stderr=result.stderr,
        exit_code=result.exit_code,
        duration_ms=result.duration_ms,
    )
    ctx.emit(
        schemas.ItemEvent(
            data=schemas.ItemData(
                name=item.name, verdict=item.verdict, duration_ms=item.duration_ms
            )
        )
    )
    details = {
        "status": result.status,
        "exit_code": result.exit_code,
        "duration_ms": result.duration_ms,
        "truncated": result.truncated,
        "exception": (
            {
                "type": result.exception.type,
                "message": result.exception.message,
                "frames": result.exception.frames,
            }
            if result.exception
            else None
        ),
    }
    status = (
        RunStatus.ERROR.value
        if result.status in {"internal", "output_limit"}
        else RunStatus.DONE.value
    )
    message = {
        "ok": f"Код завершения {result.exit_code}",
        "exception": f"Исключение {result.exception.type}" if result.exception else "Исключение",
        "timeout": f"Превышено время выполнения ({limits.timeout_s} с)",
        "output_limit": "Превышен лимит вывода",
        "internal": result.stderr or "Внутренняя ошибка запуска",
    }.get(result.status, result.status)
    summary = schemas.RunSummary(
        passed=1 if verdict == "PASS" else 0, total=1, message=message, data=details
    )
    return Outcome(status, summary, [item])


def default_handlers() -> dict[str, Handler]:
    return {"run": run_program}
