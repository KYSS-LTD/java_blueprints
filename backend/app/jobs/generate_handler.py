"""Запуск ``generate``: генерация сценариев по покрытию (S6.9, docs/07 § 7).

Версия собирается в инструментированную копию; поиск (``engine.verify.generator``) подбирает ввод,
дающий новые блоки и исходы условий. Выбранные входы сохраняются как сценарии ``generated``, для них
записывается эталон (двойной прогон проверяет устойчивость; нестабильные удаляются).
"""

from __future__ import annotations

import dataclasses
from concurrent.futures import ThreadPoolExecutor
from typing import Any

from sqlmodel import Session, col, delete, select

from app.db.models import Baseline, Project, RunStatus
from app.db.models import Scenario as ScenarioRow
from app.db.session import get_engine
from app.jobs.handlers import build_version, cancelled
from app.jobs.run_manager import Handler, JobContext, Outcome
from app.jobs.trace_handlers import prepare_traced, run_traced
from app.jobs.verify_handlers import load_scenarios, record_baselines, require_main
from app.schemas import verify as schemas
from engine.java.project import get_project
from engine.runtime.trace import features
from engine.verify.generator.input_schema import infer
from engine.verify.generator.inputs import TestInput
from engine.verify.generator.search import Budget, Chosen, Execution, search

DEFAULT_RUNS = 200
DEFAULT_SECONDS = 60.0

_TITLES = {
    "empty": ("пустой ввод", "Программа получает пустой ввод."),
    "single": ("один элемент", "Минимальный осмысленный ввод по схеме."),
    "multi": ("несколько элементов", "Ввод средней величины по схеме."),
    "boundary": ("граничные значения", "Нулевые, отрицательные и предельные значения."),
    "invalid": ("неверный формат", "Ввод, который программа не должна принимать."),
    "args": ("аргументы", "Аргументы командной строки."),
    "mutant": ("мутация", "Мутация найденного ввода, открывшая новый путь."),
    "existing": ("копия", "Повторяет имеющийся сценарий."),
}


def _executor(ctx: JobContext, built: Any, traced: Any, main: str):  # type: ignore[no-untyped-def]
    instrumented = traced.build.instrumented
    workers = max(1, ctx.settings.max_parallel_runs)

    def one(item: TestInput) -> Execution:
        if ctx.cancel.is_set():
            return Execution(frozenset(), ok=False)
        result, hits = run_traced(ctx, traced, main, item.stdin, list(item.args))
        ok = result.status in {"ok", "exception"}
        exception = None
        if result.exception is not None:
            exception = f"{result.exception.type}: {result.exception.message or ''}".strip()
        return Execution(features(instrumented, hits), ok, result.exit_code, exception)

    def run(batch: Any) -> list[Execution]:
        with ThreadPoolExecutor(max_workers=workers, thread_name_prefix="jb-generate") as pool:
            return list(pool.map(one, batch))

    return run


def _name(taken: set[str], origin: str, counter: dict[str, int]) -> str:
    while True:
        counter[origin] = counter.get(origin, 0) + 1
        name = f"gen_{origin}_{counter[origin]}"
        if name not in taken:
            taken.add(name)
            return name


def _describe(chosen: Chosen, origin: str) -> str:
    title, text = _TITLES.get(origin, _TITLES["mutant"])
    gained = len(chosen.new)
    extra = f" Новое покрытие: {gained} признаков." if gained else ""
    crash = f" Завершается с кодом {chosen.exit_code}." if chosen.exit_code not in (0, None) else ""
    return f"{title.capitalize()}. {text}{extra}{crash}"


def _store(ctx: JobContext, selected: list[Chosen]) -> list[tuple[int, str, Chosen]]:
    """Сохраняет выбранные входы сценариями; возвращает ``(id, имя, выбор)``."""
    created: list[tuple[int, str, Chosen]] = []
    with Session(get_engine()) as session:
        taken = {
            row.name
            for row in session.exec(
                select(ScenarioRow).where(ScenarioRow.project_id == ctx.project_id)
            ).all()
        }
        counter: dict[str, int] = {}
        for chosen in selected:
            origin = chosen.input.origin if chosen.input.origin in _TITLES else "mutant"
            name = _name(taken, origin, counter)
            row = ScenarioRow(
                project_id=ctx.project_id,
                name=name,
                description=_describe(chosen, origin),
                stdin=chosen.input.stdin,
                args=list(chosen.input.args),
                source="generated",
            )
            session.add(row)
            session.flush()
            created.append((row.id or 0, name, chosen))
        session.commit()
    return created


def _drop_unstable(ids: list[int]) -> set[int]:
    """Сценарии, не прошедшие проверку устойчивости, удаляются вместе с остатками эталона."""
    with Session(get_engine()) as session:
        rows = session.exec(select(ScenarioRow).where(col(ScenarioRow.id).in_(ids))).all()
        dropped = {r.id or 0 for r in rows if r.stability == "nondeterministic"}
        for scenario_id in dropped:
            session.exec(delete(Baseline).where(Baseline.scenario_id == scenario_id))  # type: ignore[call-overload]
            row = session.get(ScenarioRow, scenario_id)
            if row is not None:
                session.delete(row)
        session.commit()
    return dropped


def run_generate(ctx: JobContext) -> Outcome:
    """Поиск сценариев с обратной связью по покрытию; детерминирован по версии (seed из хеша)."""
    main = require_main(ctx)
    built, failure = build_version(ctx)
    if built is None:
        return failure or cancelled()
    traced, failure = prepare_traced(ctx, built)
    if traced is None:
        return failure or cancelled()

    project = get_project(built.files)
    hints = infer(project, main)
    with Session(get_engine()) as session:
        existing = [
            TestInput(s.stdin, s.args, "existing")
            for s in load_scenarios(session, ctx.project_id, None)
        ]
        owner = session.get(Project, ctx.project_id)
        is_reference = owner is not None and owner.baseline_version_id == ctx.version_id

    ctx.log(
        "info",
        f"Генерация: схема ввода {'выведена' if hints.inferred else 'не выведена, запасной путь'}, "
        f"имеющихся сценариев: {len(existing)}",
    )
    budget = Budget(runs=DEFAULT_RUNS, seconds=DEFAULT_SECONDS)
    progress: list[int] = [0]

    def on_progress(done: int, total: int, covered: int) -> None:
        progress[0] = covered
        ctx.emit(
            schemas.ProgressEvent(data=schemas.ProgressData(done=min(done, total), total=total))
        )

    result = search(
        hints,
        _executor(ctx, built, traced, main),
        seed=int(built.content_hash[:8], 16),
        budget=budget,
        existing=existing,
        on_progress=on_progress,
        cancelled=ctx.cancel.is_set,
    )
    if ctx.cancel.is_set():
        return cancelled()

    probe_ids = {p.node_id for p in traced.build.instrumented.probes}
    total = len(probe_ids)
    before = len(probe_ids & result.baseline)
    after = len(probe_ids & result.covered)
    created = _store(ctx, result.selected)

    warnings: list[str] = []
    dropped: set[int] = set()
    if created and is_reference:
        ids = [scenario_id for scenario_id, _, _ in created]
        ctx.log("info", f"Эталон для {len(ids)} новых сценариев")
        baseline_ctx = dataclasses.replace(
            ctx, request=schemas.RunRequest(kind="baseline", scenario_ids=ids)
        )
        outcome = record_baselines(baseline_ctx)
        if outcome.status != RunStatus.DONE.value:
            warnings.append(f"эталон не записан: {outcome.summary.message}")
        else:
            dropped = _drop_unstable(ids)
            if dropped:
                warnings.append(f"удалено нестабильных сценариев: {len(dropped)}")
    elif created:
        warnings.append("эталон не записан: версия не является эталонной")

    kept = [(i, n, c) for i, n, c in created if i not in dropped]
    with Session(get_engine()) as session:
        owner = session.get(Project, ctx.project_id)
        minimum = int((owner.settings or {}).get("gate_min_scenarios", 3)) if owner else 3
        total_now = len(load_scenarios(session, ctx.project_id, None))
    if total_now < minimum:
        warnings.append(
            f"для gate нужно не меньше {minimum} сценариев, сейчас {total_now}: добавьте свои или снизьте порог"
        )
    items = [
        schemas.RunItem(scenario_id=i, name=n, verdict="PASS", duration_ms=0) for i, n, _ in kept
    ]
    for item in items:
        ctx.emit(
            schemas.ItemEvent(
                data=schemas.ItemData(
                    scenario_id=item.scenario_id, name=item.name, verdict="PASS", duration_ms=0
                )
            )
        )
    ratio = round(after / total, 4) if total else 0.0
    message = f"Сгенерировано сценариев: {len(kept)}; покрытие блоков {before} → {after} из {total}"
    summary = schemas.RunSummary(
        passed=len(kept),
        total=len(kept),
        coverage=ratio,
        message=message,
        data={
            "scenario_ids": [i for i, _, _ in kept],
            "coverage_before": round(before / total, 4) if total else 0.0,
            "coverage_after": ratio,
            "runs": result.runs,
            "elapsed_s": round(result.elapsed_s, 2),
            "stop_reason": result.stop_reason,
            "warnings": warnings,
            "schema_inferred": hints.inferred,
        },
    )
    return Outcome(RunStatus.DONE.value, summary, items)


def generate_handlers() -> dict[str, Handler]:
    return {"generate": run_generate}
