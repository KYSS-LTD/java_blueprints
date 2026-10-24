"""Пакетная оптимизация (S8.6): шаги по очереди — «кандидат → gate → HEAD или пропуск»."""

from __future__ import annotations

import dataclasses
from typing import Any

from sqlmodel import Session

from app.db.models import Project, RunStatus
from app.db.session import get_engine
from app.jobs.benchmark_handler import run_benchmark
from app.jobs.handlers import cancelled
from app.jobs.run_manager import Handler, JobContext, Outcome, RunState
from app.jobs.verify_handlers import run_gate
from app.schemas import verify as schemas
from app.services import analysis as analysis_service
from app.services import fixes as fixes_service
from app.services import versions as versions_service
from engine.analysis import analyzer
from engine.edit.core import EditError
from engine.java.project import get_project
from engine.transform.base import apply_proposal
from engine.transform.fixes import propose


def _step(
    ctx: JobContext, step_id: str, title: str, status: str, reason: str | None = None
) -> None:
    ctx.emit(
        schemas.StepEvent(
            data=schemas.StepData(step_id=step_id, title=title, status=status, reason=reason)  # type: ignore[arg-type]
        )
    )


def _apply_step(ctx: JobContext, finding_id: str) -> tuple[str, str | None]:
    """Применяет одно исправление к HEAD; возвращает ``(заголовок, причина пропуска или None)``."""
    with Session(get_engine()) as session:
        project = session.get(Project, ctx.project_id)
        assert project is not None
        head = versions_service.head_version(session, project)
        java = get_project(versions_service.version_files(session, head.id or 0))
        thresholds = analysis_service.thresholds(project, ctx.settings)
        finding = next(
            (f for f in analyzer.analyze(java, thresholds).findings if f.id == finding_id), None
        )
        if finding is None:
            return finding_id, "находка пропала после предыдущих шагов"
        proposal = propose(java, finding)
        if proposal is None:
            return finding_id, "для находки нет автоисправления"
        if not proposal.available:
            failed = proposal.failed()
            return proposal.title, (
                failed[0].reason if failed else None
            ) or "предусловия не выполнены"
        if not proposal.behavior_preserving:
            return proposal.title, "исправление меняет поведение: применяйте его отдельно"
        try:
            files = apply_proposal(java, proposal)
        except EditError as error:
            return proposal.title, error.message
        candidate = fixes_service.create_candidate(
            session, project, head, proposal, files, finding.id
        )
        candidate_id = candidate.id or 0
        title = proposal.title

    ctx.state.candidate = None
    gate = run_gate(
        dataclasses.replace(ctx, version_id=candidate_id, request=schemas.RunRequest(kind="gate"))
    )
    if ctx.state.candidate == "promoted":
        return title, None
    return title, gate.summary.message or "gate не пройден"


def _benchmark(ctx: JobContext, version_id: int) -> dict[str, Any]:
    """Бенчмарк версии без событий в поток оптимизации; ``error`` — размер ввода масштабировать нечем."""
    quiet = RunState(ctx.state.run_id, "benchmark")
    quiet.cancel = ctx.cancel
    outcome = run_benchmark(
        dataclasses.replace(
            ctx, state=quiet, version_id=version_id, request=schemas.RunRequest(kind="benchmark")
        )
    )
    data = outcome.summary.data or {}
    if outcome.status != RunStatus.DONE.value or not data.get("samples"):
        return {"error": outcome.summary.message or "бенчмарк не выполнен"}
    return data


def speedup(before: dict[str, Any], after: dict[str, Any]) -> dict[str, Any]:
    """Измеренное ускорение на наибольшем общем размере: по времени, если оно заметно, иначе по счётчикам."""
    if "error" in before or "error" in after:
        return {"error": before.get("error") or after.get("error")}
    old = {s["size"]: s for s in before["samples"]}
    new = {s["size"]: s for s in after["samples"]}
    common = sorted(set(old) & set(new))
    result: dict[str, Any] = {
        "before_k": before.get("growth_k"),
        "after_k": after.get("growth_k"),
        "samples": [{"size": n, "before": old[n], "after": new[n]} for n in common],
        "speedup": None,
    }
    for size in reversed(common):
        slow, fast = old[size]["time_ms"], new[size]["time_ms"]
        if slow >= 20 and fast > 0:
            result.update(speedup=round(slow / fast, 2), size=size, basis="time")
            break
    else:
        for size in reversed(common):
            slow, fast = old[size].get("hits"), new[size].get("hits")
            if slow and fast:
                result.update(speedup=round(slow / fast, 2), size=size, basis="hits")
                break
    return result


def run_optimize(ctx: JobContext) -> Outcome:
    """Исправления из ``step_ids`` применяются последовательно; каждый HEAD прошёл gate."""
    steps = ctx.request.step_ids or []
    applied: list[str] = []
    skipped: dict[str, str] = {}
    with Session(get_engine()) as session:
        project = session.get(Project, ctx.project_id)
        assert project is not None
        before_id = versions_service.head_version(session, project).id or 0
    ctx.log("info", "Замер до оптимизации…")
    before = _benchmark(ctx, before_id) if steps and ctx.main_class else {"error": "нет шагов"}
    for index, finding_id in enumerate(steps):
        if ctx.cancel.is_set():
            return cancelled()
        _step(ctx, finding_id, finding_id, "applying")
        title, reason = _apply_step(ctx, finding_id)
        if reason is None:
            applied.append(finding_id)
            _step(ctx, finding_id, title, "passed")
        else:
            skipped[finding_id] = reason
            _step(ctx, finding_id, title, "skipped", reason)
        ctx.emit(schemas.ProgressEvent(data=schemas.ProgressData(done=index + 1, total=len(steps))))

    with Session(get_engine()) as session:
        project = session.get(Project, ctx.project_id)
        assert project is not None
        head = versions_service.head_version(session, project)
        java = get_project(versions_service.version_files(session, head.id or 0))
        after = analyzer.analyze(java, analysis_service.thresholds(project, ctx.settings))
        head_id, gate_status = head.id or 0, head.gate_status
    measured: dict[str, Any] = {"error": "ничего не применено"}
    if applied and head_id != before_id:
        ctx.log("info", "Замер после оптимизации…")
        measured = speedup(before, _benchmark(ctx, head_id))
    message = f"Применено шагов: {len(applied)} из {len(steps)}"
    if measured.get("speedup"):
        message += f"; ускорение до {measured['speedup']}× (N={measured['size']})"
    summary = schemas.RunSummary(
        passed=len(applied),
        total=len(steps),
        version_status=gate_status if gate_status in {"passed", "failed"} else "unknown",  # type: ignore[arg-type]
        message=message,
        data={
            "measured": measured,
            "applied": applied,
            "skipped": skipped,
            "head_version_id": head_id,
            "complexity_after": after.complexity.current,
        },
    )
    return Outcome(RunStatus.DONE.value, summary)


def optimize_handlers() -> dict[str, Handler]:
    return {"optimize": run_optimize}
