"""Запуск ``trace``: трассировка («Дебаг») и покрытие блоков (S6.7, docs/07 § 6).

Версия собирается в инструментированную копию (``JbTrace``), программа запускается на своём вводе
или на сценариях, счётчики пересчитываются в блоки графа. Результат оценочный: вердикт
«поведение сохранилось» дают только чистые сборки gate.
"""

from __future__ import annotations

from dataclasses import dataclass
from typing import Any

from sqlmodel import Session

from app.db.models import RunStatus
from app.db.session import get_engine
from app.errors import payload_too_large
from app.jobs.handlers import Built, build_version, cancelled, run_in_slot, verdict_of
from app.jobs.run_manager import Handler, JobContext, Outcome
from app.jobs.verify_handlers import ScenarioData, load_scenarios, require_main, run_parallel
from app.schemas import verify as schemas
from app.services import builds, importing
from engine.runtime.runner import RunResult
from engine.runtime.trace import (
    DEFAULT_TRACE_LIMIT,
    Coverage,
    TraceBuild,
    TraceHits,
    TraceUnavailable,
    collect,
    coverage_of,
)

SINGLE_RUN = "run"


@dataclass(slots=True)
class Traced:
    """Инструментированная сборка версии и её связь с исходной."""

    build: TraceBuild
    built: Built


def trace_limit_of(ctx: JobContext) -> int:
    return int(ctx.project_settings.get("trace_limit", DEFAULT_TRACE_LIMIT))


def prepare_traced(ctx: JobContext, built: Built) -> tuple[Traced | None, Outcome | None]:
    """Инструментированная копия исходной сборки либо готовый итог с причиной отказа."""
    ctx.log("info", "Инструментирование…")
    try:
        trace_build = builds.prepare_trace(ctx.settings, built.files)
    except TraceUnavailable as exc:
        errors = [d for d in exc.compiled.diagnostics if d.severity == "error"]
        message = "Инструментированная копия не компилируется"
        details = "; ".join(f"{d.file}:{d.line}: {d.message}" for d in errors[:3])
        ctx.log("build", exc.compiled.log.strip()[:2000])
        ctx.emit(
            schemas.ErrorEvent(data=schemas.ErrorData(code="TRACE_UNAVAILABLE", message=message))
        )
        return None, Outcome(
            RunStatus.FAILED.value,
            schemas.RunSummary(message=message, data={"stage": "instrument", "details": details}),
        )
    traced = Built(built.files, built.content_hash, trace_build.workspace, trace_build.compiled)
    return Traced(trace_build, traced), None


def run_traced(
    ctx: JobContext, traced: Traced, main: str, stdin: str, args: list[str]
) -> tuple[RunResult, TraceHits]:
    result = run_in_slot(
        ctx, traced.built, main, stdin=stdin, args=args, trace_limit=trace_limit_of(ctx)
    )
    return result, collect(traced.build.instrumented, result.trace)


def summary_of(
    traced: Traced, coverage: Coverage, hits: dict[str, TraceHits], message: str
) -> schemas.RunSummary:
    instrumented = traced.build.instrumented
    details: dict[str, Any] = {
        key: {
            "entries": h.entries,
            "branches": {node: {"true": t, "false": f} for node, (t, f) in h.branches.items()},
            "saturated": h.saturated,
        }
        for key, h in hits.items()
    }
    return schemas.RunSummary(
        coverage=round(coverage.ratio, 4),
        message=message,
        data={
            "covered": len(coverage.covered),
            "total": coverage.total,
            "uncovered": list(coverage.uncovered),
            "untracked": dict(instrumented.untracked),
            "details": details,
            "instrumentation": {
                "conditions": instrumented.conditions,
                "lambdas": instrumented.lambdas,
            },
        },
    )


def _item(
    name: str, scenario: ScenarioData | None, result: RunResult, h: TraceHits
) -> schemas.RunItem:
    return schemas.RunItem(
        scenario_id=scenario.id if scenario else None,
        name=name,
        verdict=verdict_of(result),  # type: ignore[arg-type]
        stdout=result.stdout,
        stderr=result.stderr,
        exit_code=result.exit_code,
        duration_ms=result.duration_ms,
        hits=h.counts,
    )


def run_trace(ctx: JobContext) -> Outcome:
    """Своя команда (``stdin``/``args``) или сценарии (``scenario_ids``; пусто — все включённые)."""
    request = ctx.request
    custom = request.scenario_ids is None and (
        request.stdin is not None or request.args is not None
    )
    if request.stdin is not None and len(request.stdin.encode("utf-8")) > importing.MAX_FILE_BYTES:
        raise payload_too_large("stdin больше 2 МБ")
    main = require_main(ctx)
    built, failure = build_version(ctx)
    if built is None:
        return failure or cancelled()
    traced, failure = prepare_traced(ctx, built)
    if traced is None:
        return failure or cancelled()

    items: list[schemas.RunItem] = []
    hits: dict[str, TraceHits] = {}
    if custom:
        ctx.log("info", f"Трассировка {main}")
        result, h = run_traced(ctx, traced, main, request.stdin or "", request.args or [])
        for text in (result.stdout, result.stderr):
            for i in range(0, len(text), 8192):
                ctx.log("stdout" if text is result.stdout else "stderr", text[i : i + 8192])
        if result.status == "cancelled":
            return cancelled()
        items.append(_item(main, None, result, h))
        hits[SINGLE_RUN] = h
    else:
        with Session(get_engine()) as session:
            scenarios = load_scenarios(session, ctx.project_id, request.scenario_ids or None)
        if not scenarios:
            return Outcome(
                RunStatus.FAILED.value, schemas.RunSummary(message="нет включённых сценариев")
            )
        ctx.log("info", f"Трассировка: {len(scenarios)} сценариев")
        collected: dict[int, tuple[RunResult, TraceHits]] = {}

        def work(scenario: ScenarioData) -> tuple[RunResult, TraceHits]:
            return run_traced(ctx, traced, main, scenario.stdin, list(scenario.args))

        def done(scenario: ScenarioData, outcome: tuple[RunResult, TraceHits]) -> None:
            collected[scenario.id] = outcome
            ctx.emit(
                schemas.ItemEvent(
                    data=schemas.ItemData(
                        scenario_id=scenario.id,
                        name=scenario.name,
                        verdict=verdict_of(outcome[0]),  # type: ignore[arg-type]
                        duration_ms=outcome[0].duration_ms,
                    )
                )
            )

        if not run_parallel(ctx, scenarios, work, done):
            return cancelled()
        for scenario in scenarios:
            result, h = collected[scenario.id]
            items.append(_item(scenario.name, scenario, result, h))
            hits[str(scenario.id)] = h

    coverage = coverage_of(traced.build.instrumented, hits.values())
    saturated = any(h.saturated for h in hits.values())
    message = f"Покрытие блоков: {len(coverage.covered)} из {coverage.total}"
    if saturated:
        message += " (счётчики достигли лимита)"
    summary = summary_of(traced, coverage, hits, message)
    summary.passed = sum(1 for i in items if i.verdict == "PASS")
    summary.total = len(items)
    return Outcome(RunStatus.DONE.value, summary, items)


def trace_handlers() -> dict[str, Handler]:
    return {"trace": run_trace}


__all__ = ["Traced", "prepare_traced", "run_traced", "trace_handlers"]
