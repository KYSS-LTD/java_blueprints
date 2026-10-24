"""Запуски проверки поведения: ``baseline``, ``test``, ``gate`` (S6.3, docs/07 § 4 и § 8)."""

from __future__ import annotations

from collections.abc import Callable
from concurrent.futures import Future, ThreadPoolExecutor, as_completed
from dataclasses import dataclass
from datetime import UTC, datetime
from typing import Any

from sqlmodel import Session, col, delete, select

from app.db.models import Baseline, GateStatus, Project, RunStatus, Version, VersionStatus
from app.db.models import Scenario as ScenarioRow
from app.db.session import get_engine
from app.errors import bad_request
from app.jobs.handlers import Built, build_version, cancelled, run_in_slot
from app.jobs.run_manager import Handler, JobContext, Outcome
from app.schemas import verify as schemas
from app.services import versions as versions_service
from engine.java.project import get_project
from engine.runtime.runner import RunResult
from engine.verify import compare
from engine.verify.compare import Difference, Observed
from engine.verify.normalize import Mask, normalize_stdout


@dataclass(frozen=True, slots=True)
class ScenarioData:
    """Снимок сценария для потока-исполнителя (ORM-объекты между потоками не передаются)."""

    id: int
    name: str
    stdin: str
    args: tuple[str, ...]
    masks: tuple[Mask, ...]
    stability: str
    expected_stdout: str | None
    expected_exit_code: int | None


@dataclass(frozen=True, slots=True)
class StoredBaseline:
    stdout: str
    stderr: str
    exit_code: int | None
    exception: str | None
    duration_ms: int

    def observed(self) -> Observed:
        return Observed(self.stdout, self.stderr, self.exit_code, self.exception, self.duration_ms)


def scenario_data(row: ScenarioRow) -> ScenarioData:
    return ScenarioData(
        id=row.id or 0,
        name=row.name,
        stdin=row.stdin,
        args=tuple(row.args or []),
        masks=tuple(Mask(m["pattern"], m["replacement"]) for m in row.masks or []),
        stability=row.stability,
        expected_stdout=row.expected_stdout,
        expected_exit_code=row.expected_exit_code,
    )


def load_scenarios(session: Session, project_id: int, only: list[int] | None) -> list[ScenarioData]:
    query = (
        select(ScenarioRow)
        .where(ScenarioRow.project_id == project_id, col(ScenarioRow.enabled).is_(True))
        .order_by(col(ScenarioRow.id))
    )
    rows = session.exec(query).all()
    if only is not None:
        wanted = set(only)
        rows = [r for r in rows if r.id in wanted]
    return [scenario_data(r) for r in rows]


def load_baselines(session: Session, project_id: int) -> dict[int, StoredBaseline]:
    found = session.exec(select(Baseline).where(Baseline.project_id == project_id)).all()
    return {
        b.scenario_id: StoredBaseline(
            b.stdout,
            b.stderr,
            b.exit_code,
            (b.exception or {}).get("summary"),
            b.duration_ms or 0,
        )
        for b in found
    }


def _diff_schema(diff: Difference | None) -> schemas.BehaviorDiff | None:
    if diff is None:
        return None
    return schemas.BehaviorDiff(
        stdout=(
            schemas.TextChange(
                expected=diff.stdout.expected,
                actual=diff.stdout.actual,
                unified=diff.stdout.unified,
            )
            if diff.stdout
            else None
        ),
        stderr=(
            schemas.TextChange(
                expected=diff.stderr.expected,
                actual=diff.stderr.actual,
                unified=diff.stderr.unified,
            )
            if diff.stderr
            else None
        ),
        exit_code=(
            schemas.ExitCodeChange(
                expected=diff.exit_code.expected,  # type: ignore[arg-type]
                actual=diff.exit_code.actual,  # type: ignore[arg-type]
            )
            if diff.exit_code
            else None
        ),
        exception=(
            schemas.ExceptionChange(
                expected=diff.exception.expected,  # type: ignore[arg-type]
                actual=diff.exception.actual,  # type: ignore[arg-type]
            )
            if diff.exception
            else None
        ),
    )


def require_main(ctx: JobContext) -> str:
    if ctx.main_class is None:
        raise bad_request("у проекта нет главного класса: выберите его в настройках проекта")
    return ctx.main_class


def run_parallel[T](
    ctx: JobContext,
    scenarios: list[ScenarioData],
    work: Callable[[ScenarioData], T],
    on_done: Callable[[ScenarioData, T], None],
) -> bool:
    """Сценарии выполняются в пуле потоков; результаты обрабатываются по мере готовности.

    Возвращает ``False``, если запуск отменили. Порядок вызовов ``on_done`` — порядок завершения.
    """
    total = len(scenarios)
    done = 0
    workers = max(1, min(ctx.settings.max_parallel_runs, total))
    with ThreadPoolExecutor(max_workers=workers, thread_name_prefix="jb-scenario") as pool:
        futures: dict[Future[T], ScenarioData] = {pool.submit(work, s): s for s in scenarios}
        for future in as_completed(futures):
            on_done(futures[future], future.result())
            done += 1
            ctx.emit(schemas.ProgressEvent(data=schemas.ProgressData(done=done, total=total)))
            if ctx.cancel.is_set():
                for pending in futures:
                    pending.cancel()
                return False
    return True


# --- baseline ---------------------------------------------------------------------------------------


def record_baselines(ctx: JobContext) -> Outcome:
    """Эталон: каждый включённый сценарий дважды на эталонной версии (docs/07 § 3.3, § 4.1)."""
    main = require_main(ctx)
    with Session(get_engine()) as session:
        scenarios = load_scenarios(session, ctx.project_id, ctx.request.scenario_ids)
    if not scenarios:
        return Outcome(
            RunStatus.FAILED.value, schemas.RunSummary(message="нет включённых сценариев")
        )
    built, failure = build_version(ctx)
    if built is None:
        return failure or cancelled()

    ctx.log("info", f"Запись эталона: {len(scenarios)} сценариев, каждый запускается дважды")
    results: dict[int, compare.Classification] = {}
    items: list[schemas.RunItem] = []
    warnings: list[str] = []

    def work(scenario: ScenarioData) -> compare.Classification:
        runs: list[RunResult] = []
        for _ in range(2):
            runs.append(
                run_in_slot(ctx, built, main, stdin=scenario.stdin, args=list(scenario.args))
            )
            if ctx.cancel.is_set():
                break
        if len(runs) < 2:
            runs.append(runs[0])
        return compare.classify(runs[0], runs[1], scenario.masks)

    def done(scenario: ScenarioData, result: compare.Classification) -> None:
        results[scenario.id] = result
        usable = result.stability != "nondeterministic"
        warnings.extend(f"{scenario.name}: {w}" for w in result.warnings)
        expected = scenario.expected_stdout
        if usable and expected is not None:
            masks = tuple(result.masks)
            if normalize_stdout(expected, masks) != result.baseline.stdout:
                warnings.append(
                    f"{scenario.name}: вывод не совпадает с ожидаемым (expected_stdout)"
                )
        if usable and scenario.expected_exit_code not in (None, result.baseline.exit_code):
            warnings.append(f"{scenario.name}: код выхода не совпадает с ожидаемым")
        verdict = "PASS" if usable else "ERROR"
        item = schemas.RunItem(
            scenario_id=scenario.id,
            name=scenario.name,
            verdict=verdict,
            stdout=result.baseline.stdout,
            stderr=result.baseline.stderr,
            exit_code=result.baseline.exit_code,
            duration_ms=result.baseline.duration_ms,
        )
        items.append(item)
        ctx.emit(
            schemas.ItemEvent(
                data=schemas.ItemData(
                    scenario_id=scenario.id,
                    name=scenario.name,
                    verdict=verdict,
                    duration_ms=result.baseline.duration_ms,
                )
            )
        )

    if not run_parallel(ctx, scenarios, work, done):
        return cancelled()

    order = {s.id: i for i, s in enumerate(scenarios)}
    items.sort(key=lambda item: order.get(item.scenario_id or 0, 0))
    recorded = _store_baselines(ctx, scenarios, results)
    for text in warnings:
        ctx.log("info", f"Предупреждение: {text}")
    excluded = sum(1 for r in results.values() if r.stability == "nondeterministic")
    message = f"Эталон записан: {recorded} из {len(scenarios)}"
    if excluded:
        message += f"; исключено нестабильных: {excluded}"
    summary = schemas.RunSummary(
        passed=recorded,
        total=len(scenarios),
        message=message,
        data={
            "warnings": warnings,
            "baseline_version_id": ctx.version_id,
            "stability": {s.name: results[s.id].stability for s in scenarios},
        },
    )
    return Outcome(RunStatus.DONE.value, summary, items)


def _store_baselines(
    ctx: JobContext, scenarios: list[ScenarioData], results: dict[int, compare.Classification]
) -> int:
    recorded = 0
    with Session(get_engine()) as session:
        for scenario in scenarios:
            result = results[scenario.id]
            session.exec(delete(Baseline).where(Baseline.scenario_id == scenario.id))  # type: ignore[call-overload]
            row = session.get(ScenarioRow, scenario.id)
            if row is None:
                continue
            row.stability = result.stability
            row.masks = [{"pattern": m.pattern, "replacement": m.replacement} for m in result.masks]
            session.add(row)
            if result.stability == "nondeterministic":
                continue
            base = result.baseline
            session.add(
                Baseline(
                    project_id=ctx.project_id,
                    scenario_id=scenario.id,
                    version_id=ctx.version_id,
                    stdout=base.stdout,
                    stderr=base.stderr,
                    exit_code=base.exit_code,
                    exception={"summary": base.exception} if base.exception else None,
                    duration_ms=base.duration_ms,
                )
            )
            recorded += 1
        project = session.get(Project, ctx.project_id)
        if project is not None:
            project.baseline_version_id = ctx.version_id
            project.updated_at = datetime.now(UTC)
            session.add(project)
        session.commit()
    return recorded


# --- test -----------------------------------------------------------------------------------------------


@dataclass(slots=True)
class ScenarioRun:
    """Итог прогона сценариев на версии."""

    items: list[schemas.RunItem]
    skipped: list[str]
    passed: int
    total: int


def run_scenarios(ctx: JobContext, built: Built) -> ScenarioRun | None:
    """Прогон включённых сценариев с эталоном; ``None`` — запуск отменён."""
    main = require_main(ctx)
    with Session(get_engine()) as session:
        scenarios = load_scenarios(session, ctx.project_id, ctx.request.scenario_ids)
        stored = load_baselines(session, ctx.project_id)
    skipped: list[str] = []
    runnable: list[ScenarioData] = []
    for scenario in scenarios:
        if scenario.stability == "nondeterministic":
            skipped.append(f"{scenario.name}: нестабильный сценарий исключён из проверки")
        elif scenario.id not in stored:
            skipped.append(f"{scenario.name}: нет эталона")
        else:
            runnable.append(scenario)

    items: list[schemas.RunItem] = []

    def work(scenario: ScenarioData) -> tuple[RunResult, Observed]:
        result = run_in_slot(ctx, built, main, stdin=scenario.stdin, args=list(scenario.args))
        return result, compare.observe(result, scenario.masks)

    def done(scenario: ScenarioData, outcome: tuple[RunResult, Observed]) -> None:
        result, actual = outcome
        expected = stored[scenario.id].observed()
        verdict, diff = compare.verdict(result, expected, actual)
        item = schemas.RunItem(
            scenario_id=scenario.id,
            name=scenario.name,
            verdict=verdict,  # type: ignore[arg-type]
            stdout=result.stdout,
            stderr=result.stderr,
            exit_code=result.exit_code,
            duration_ms=result.duration_ms,
            baseline_ms=expected.duration_ms,
            diff=_diff_schema(diff),
        )
        items.append(item)
        ctx.emit(
            schemas.ItemEvent(
                data=schemas.ItemData(
                    scenario_id=scenario.id,
                    name=scenario.name,
                    verdict=verdict,  # type: ignore[arg-type]
                    duration_ms=result.duration_ms,
                    baseline_ms=expected.duration_ms,
                    diff=item.diff,
                )
            )
        )

    if runnable and not run_parallel(ctx, runnable, work, done):
        return None
    order = {s.id: i for i, s in enumerate(runnable)}
    items.sort(key=lambda item: order.get(item.scenario_id or 0, 0))
    passed = sum(1 for i in items if i.verdict == "PASS")
    return ScenarioRun(items, skipped, passed, len(items))


def _verdict_summary(
    ctx: JobContext, result: ScenarioRun, extra: dict[str, Any]
) -> schemas.RunSummary:
    with Session(get_engine()) as session:
        project = session.get(Project, ctx.project_id)
        minimum = int((project.settings or {}).get("gate_min_scenarios", 3)) if project else 3
        baseline_version = project.baseline_version_id if project else None
    failed = result.passed < result.total
    if failed:
        status = "failed"
        message = f"Поведение отличается: {result.passed} из {result.total}"
    elif result.total < minimum:
        status = "unknown"
        message = f"Сценариев недостаточно: {result.total} из {minimum} необходимых"
    else:
        status = "passed"
        message = f"Все тесты пройдены — {result.passed} из {result.total}"
    return schemas.RunSummary(
        passed=result.passed,
        total=result.total,
        message=message,
        version_status=status,  # type: ignore[arg-type]
        data={"warnings": result.skipped, "baseline_version_id": baseline_version, **extra},
    )


def run_tests(ctx: JobContext) -> Outcome:
    """Сравнение версии с эталоном по всем включённым сценариям."""
    built, failure = build_version(ctx)
    if built is None:
        return failure or cancelled()
    result = run_scenarios(ctx, built)
    if result is None:
        return cancelled()
    if result.total == 0:
        return Outcome(
            RunStatus.FAILED.value,
            schemas.RunSummary(
                message="нет сценариев с эталоном: запишите эталон",
                data={"warnings": result.skipped},
            ),
        )
    return Outcome(RunStatus.DONE.value, _verdict_summary(ctx, result, {}), result.items)


# --- gate ------------------------------------------------------------------------------------------------


def _stage(ctx: JobContext, stage: str, status: str, details: str | None = None) -> None:
    ctx.emit(
        schemas.GateEvent(
            data=schemas.GateData(stage=stage, status=status, details=details)  # type: ignore[arg-type]
        )
    )


def _set_gate(ctx: JobContext, status: str) -> None:
    with Session(get_engine()) as session:
        version = session.get(Version, ctx.version_id)
        if version is None:
            return
        version.gate_status = status
        version.gate_run_id = ctx.state.run_id
        session.add(version)
        session.commit()
        if version.status == VersionStatus.CANDIDATE.value:
            ctx.state.candidate = _resolve_candidate(session, version, status)


def _resolve_candidate(session: Session, version: Version, gate_status: str) -> str:
    """Судьба версии-кандидата после gate: зелёный — становится HEAD, красный — отклоняется (S8)."""
    project = session.get(Project, version.project_id)
    if project is None:
        return "candidate"
    if gate_status == GateStatus.PASSED.value:
        if project.head_version_id != version.parent_id:
            return "stale"  # HEAD успел измениться: подтверждать вручную
        version.status = VersionStatus.ACTIVE.value
        project.head_version_id = version.id
        project.updated_at = datetime.now(UTC)
        session.add_all([version, project])
        session.commit()
        return "promoted"
    if gate_status == GateStatus.FAILED.value and (version.meta or {}).get(
        "behavior_preserving", True
    ):
        version.status = VersionStatus.REJECTED.value
        session.add(version)
        session.commit()
        return "rejected"
    return "candidate"


def run_gate(ctx: JobContext) -> Outcome:
    """Gate: ``syntax → compile → scenarios``; результат записывается в ``gate_status`` версии."""
    outcome = _gate_stages(ctx)
    if ctx.state.candidate is not None:
        outcome.summary.data = {**(outcome.summary.data or {}), "candidate": ctx.state.candidate}
    return outcome


def _gate_stages(ctx: JobContext) -> Outcome:
    with Session(get_engine()) as session:
        files = versions_service.version_files(session, ctx.version_id)

    _stage(ctx, "syntax", "running")
    issues = get_project(files).syntax_issues()
    if issues:
        details = "; ".join(f"{path}: {len(found)}" for path, found in issues.items())
        _stage(ctx, "syntax", "failed", details)
        _set_gate(ctx, GateStatus.FAILED.value)
        return Outcome(
            RunStatus.DONE.value,
            schemas.RunSummary(
                message=f"Синтаксические ошибки: {details}",
                version_status="failed",
                data={"stage": "syntax"},
            ),
        )
    _stage(ctx, "syntax", "passed")

    _stage(ctx, "compile", "running")
    built, failure = build_version(ctx)
    if built is None:
        if failure is not None and failure.status == RunStatus.FAILED.value:
            _stage(ctx, "compile", "failed", failure.summary.message)
            failure.status = RunStatus.DONE.value
            failure.summary.version_status = "failed"
            _set_gate(ctx, GateStatus.FAILED.value)
        return failure or cancelled()
    _stage(ctx, "compile", "passed")

    _stage(ctx, "scenarios", "running")
    result = run_scenarios(ctx, built)
    if result is None:
        return cancelled()
    if result.total == 0:
        _stage(ctx, "scenarios", "failed", "нет сценариев с эталоном")
        return Outcome(
            RunStatus.FAILED.value,
            schemas.RunSummary(
                message="нет сценариев с эталоном: запишите эталон",
                version_status="unknown",
                data={"warnings": result.skipped},
            ),
        )
    summary = _verdict_summary(ctx, result, {"stage": "scenarios"})
    _stage(
        ctx,
        "scenarios",
        "passed" if result.passed == result.total else "failed",
        f"{result.passed} из {result.total}",
    )
    if summary.version_status == "passed":
        _coverage_stage(ctx, built, result, summary)
    gate_status = {
        "passed": GateStatus.PASSED,
        "failed": GateStatus.FAILED,
        "unknown": GateStatus.UNKNOWN,
    }[summary.version_status or "unknown"]
    _set_gate(ctx, gate_status.value)
    return Outcome(RunStatus.DONE.value, summary, result.items)


def _coverage_stage(
    ctx: JobContext, built: Built, result: ScenarioRun, summary: schemas.RunSummary
) -> None:
    """Покрытие блоков сценариями gate (docs/07 § 8). Низкое покрытие не краснит gate, но не даёт
    заявить «поведение сохранено»: статус версии становится «не проверено»."""
    from app.jobs.trace_handlers import prepare_traced, run_traced
    from engine.runtime.trace import coverage_of

    _stage(ctx, "coverage", "running")
    with Session(get_engine()) as session:
        project = session.get(Project, ctx.project_id)
        minimum = float((project.settings or {}).get("gate_min_coverage", 0.6)) if project else 0.6
        scenarios = [
            s
            for s in load_scenarios(session, ctx.project_id, ctx.request.scenario_ids)
            if s.name in {i.name for i in result.items}
        ]
    traced, _ = prepare_traced(ctx, built)
    if traced is None:
        _stage(
            ctx,
            "coverage",
            "passed",
            "покрытие не измерено: инструментированная копия не собралась",
        )
        return
    main = require_main(ctx)
    hits = []

    def work(scenario: ScenarioData):  # type: ignore[no-untyped-def]
        return run_traced(ctx, traced, main, scenario.stdin, list(scenario.args))[1]

    run_parallel(ctx, scenarios, work, lambda _scenario, h: hits.append(h))
    coverage = coverage_of(traced.build.instrumented, hits)
    summary.coverage = round(coverage.ratio, 4)
    data = dict(summary.data or {})
    data.update(
        coverage_total=coverage.total, coverage_covered=len(coverage.covered), coverage_min=minimum
    )
    summary.data = data
    percent = f"{coverage.ratio * 100:.0f}%"
    if coverage.ratio + 1e-9 < minimum:
        _stage(ctx, "coverage", "failed", f"{percent} ниже порога {minimum * 100:.0f}%")
        summary.version_status = "unknown"
        summary.message = (
            f"Все тесты пройдены, но покрытие {percent} ниже порога {minimum * 100:.0f}%: "
            "поведение подтверждено не полностью"
        )
    else:
        _stage(ctx, "coverage", "passed", f"{percent} блоков")


def verify_handlers() -> dict[str, Handler]:
    return {"baseline": record_baselines, "test": run_tests, "gate": run_gate}
