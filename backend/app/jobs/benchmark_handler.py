"""Запуски ``benchmark`` и ``verify``: эмпирический рост и динамическая проверка находок (S6.10, S7.7).

Программа запускается на вводе растущего размера (по схеме ввода либо по сценариям ``scaled`` с
подстановкой ``${N}``). Показатель роста ``k`` считается по счётчикам выполнения узлов
(инструментированная сборка, детерминированно) и по времени чистой сборки (1 прогрев + 3 замера,
медиана, за вычетом запуска JVM). Для находок проверяется узел находки и всё, что внутри него.
"""

from __future__ import annotations

import dataclasses
import statistics
from collections.abc import Callable
from typing import Any

from sqlmodel import Session, select

from app.db.models import Project, RunStatus
from app.db.models import Scenario as ScenarioRow
from app.db.session import get_engine
from app.jobs.handlers import Built, build_version, cancelled, limits_of, run_in_slot
from app.jobs.run_manager import Handler, JobContext, Outcome
from app.jobs.trace_handlers import Traced, prepare_traced, trace_limit_of
from app.jobs.verify_handlers import load_scenarios, require_main
from app.schemas import verify as schemas
from app.services import analysis as analysis_service
from app.services import graphs
from app.services import versions as versions_service
from engine.analysis.model import Finding
from engine.java.project import get_project
from engine.runtime.trace import TraceHits, collect
from engine.verify import benchmark as bench
from engine.verify.generator.input_schema import infer

BENCH_TRACE_LIMIT = 2_000_000_000  # у бенчмарка счётчики не должны насыщаться раньше времени
NO_INPUT = "не удалось построить масштабируемый ввод: добавьте сценарий с подстановкой ${N} (source=scaled)"


@dataclasses.dataclass(slots=True)
class Measurement:
    samples: list[bench.Sample]
    startup_ms: float
    warnings: list[str]


def _scaled_templates(ctx: JobContext) -> list[ScenarioRow]:
    with Session(get_engine()) as session:
        rows = session.exec(
            select(ScenarioRow).where(
                ScenarioRow.project_id == ctx.project_id, ScenarioRow.source == "scaled"
            )
        ).all()
    wanted = ctx.request.scenario_ids
    return [
        r
        for r in rows
        if r.enabled and bench.TEMPLATE in r.stdin and (not wanted or r.id in wanted)
    ]


def _target_hits(hits: TraceHits, target: str | None) -> int:
    """Сумма счётчиков узла и всех узлов внутри него; без цели — всех блоков программы."""
    if target is None:
        return sum(hits.counts.values())
    prefix = target + "/"
    return sum(v for node, v in hits.counts.items() if node == target or node.startswith(prefix))


def _long_limits(ctx: JobContext):  # type: ignore[no-untyped-def]
    base = limits_of(ctx)
    return dataclasses.replace(base, timeout_s=max(base.timeout_s * 3, 30))


Make = Callable[[int], tuple[str, list[str]]]
MAX_SAMPLES = 7
SIGNIFICANT_MS = 100  # меньше — время тонет в шуме JIT и запуска JVM
GROW_UNTIL_MS = (
    300  # размер удваивается, пока наибольший замер короче: на больших N видна асимптотика
)


def measure(
    ctx: JobContext,
    built: Built,
    traced: Traced,
    main: str,
    make: Make,
    sizes: list[int],
    target: str | None,
) -> Measurement:
    """По одному запуску на размер для счётчиков и серия замеров времени на чистой сборке.

    Пока время на наибольшем размере незаметно (< 100 мс), размер удваивается (до 7 точек, до
    64 000): так видна и стоимость вызовов JDK, которых счётчики не показывают.
    """
    limits = _long_limits(ctx)
    instrumented = traced.build.instrumented
    warnings: list[str] = []
    startup = min(
        run_in_slot(ctx, built, main, stdin="", args=[], limits=limits).duration_ms
        for _ in range(2)
    )
    samples: list[bench.Sample] = []
    queue = list(sizes)
    planned = len(queue)
    while queue and len(samples) < MAX_SAMPLES:
        if ctx.cancel.is_set():
            break
        size = queue.pop(0)
        stdin, args = make(size)
        result = run_in_slot(
            ctx,
            traced.built,
            main,
            stdin=stdin,
            args=args,
            trace_limit=BENCH_TRACE_LIMIT,
            limits=limits,
        )
        if result.status == "timeout":
            samples.append(bench.Sample(size, float(limits.timeout_s * 1000), None, True))
            warnings.append(f"на размере {size} превышено время: рост дальше не измерялся")
            break
        hits = collect(instrumented, result.trace)
        if hits.saturated and size not in sizes:
            # удвоенный размер уже не помещается в счётчики: остаются точки, где они точны
            warnings.append(
                f"на размере {size} счётчики выполнения переполнились: дальше не растим"
            )
            break
        durations: list[float] = []
        for attempt in range(4):  # прогрев + 3 замера
            timed = run_in_slot(ctx, built, main, stdin=stdin, args=args, limits=limits)
            if timed.status == "timeout":
                break
            if attempt:
                durations.append(float(timed.duration_ms))
        median = statistics.median(durations) if durations else float(limits.timeout_s * 1000)
        counted = None if hits.saturated else _target_hits(hits, target)
        if hits.saturated:
            warnings.append(
                f"счётчики на размере {size} достигли лимита: рост по ним не оценивается"
            )
        samples.append(bench.Sample(size, max(median - startup, 0.0), counted))
        ctx.emit(
            schemas.ProgressEvent(
                data=schemas.ProgressData(done=len(samples), total=max(planned, len(samples)))
            )
        )
        ctx.emit(
            schemas.ItemEvent(
                data=schemas.ItemData(name=f"N={size}", verdict="PASS", duration_ms=int(median))
            )
        )
        # время должно стать заметным и опираться минимум на три точки выше шума: иначе показатель роста
        # по времени не считается и «до» оптимизации выглядит линейным
        visible = sum(1 for s in samples if s.time_ms >= 5)
        if (
            not queue
            and (median - startup < GROW_UNTIL_MS or visible < 3)
            and size < 64_000
            and len(samples) >= 3
        ):
            queue.append(size * 2)
    return Measurement(samples, float(startup), warnings)


def _evidence(run_id: int, m: Measurement) -> dict[str, Any]:
    """Результат в форме ``DynamicEvidence`` (docs/08 § 6) плюс показатели по счётчикам и времени.

    Основной показатель — по счётчикам выполнения узлов (точный для работы самой программы). Время
    берётся, только если оно заметно (≥ 100 мс на наибольшем размере, ≥ 3 точек) и растёт заметно
    быстрее счётчиков: так видна стоимость вызовов JDK (``List.contains``), которых счётчики не
    считают. Меньшие времена тонут в шуме JIT и запуска JVM.
    """
    valid = [s for s in m.samples if not s.timed_out]
    k_hits = bench.growth_exponent([(s.size, float(s.hits)) for s in valid if s.hits])
    timed = [(s.size, s.time_ms) for s in valid if s.time_ms >= 5]
    k_time = bench.growth_exponent(timed, last=2)
    strong = len(timed) >= 3 and max((t for _, t in timed), default=0.0) >= SIGNIFICANT_MS
    k, basis = k_hits, "hits"
    if k_time is not None and strong and (k_hits is None or k_time > k_hits + 0.4):
        k, basis = k_time, "time"
    return {
        "run_id": run_id,
        "samples": [
            {"size": s.size, "time_ms": round(s.time_ms, 1), "hits": s.hits} for s in m.samples
        ],
        "growth_k": round(k, 3) if k is not None else None,
        "growth_hits_k": round(k_hits, 3) if k_hits is not None else None,
        "growth_time_k": round(k_time, 3) if k_time is not None else None,
        "basis": basis,
        "verdict": bench.verdict_text(k),
    }


def _inputs(ctx: JobContext, built: Built, main: str) -> tuple[Make | None, list[int], list[str]]:
    """Генератор ввода по размеру, размеры и причины, по которым масштабировать нечего."""
    sizes = list(ctx.request.sizes or bench.DEFAULT_SIZES)
    templates = _scaled_templates(ctx)
    if templates:
        row = templates[0]

        def from_template(n: int) -> tuple[str, list[str]]:
            return bench.sized(row.stdin, n), [bench.sized(a, n) for a in row.args or []]

        return from_template, sizes, []
    with Session(get_engine()) as session:
        own = [
            s.stdin
            for s in load_scenarios(session, ctx.project_id, None)
            if bench.TEMPLATE not in s.stdin
        ]
    scaler = bench.derive_scaler(own)
    if (
        scaler is not None
    ):  # вход из рабочих сценариев проекта: программа принимает его как корректный
        return lambda n: (scaler.stdin(n), []), sizes, []
    hints = infer(get_project(built.files), main)
    if not hints.inferred or not hints.reads_stdin:
        return None, sizes, [NO_INPUT]
    seed = int(built.content_hash[:8], 16)

    def from_schema(n: int) -> tuple[str, list[str]]:
        return bench.scaled_stdin(hints, n, seed), []

    return from_schema, sizes, []


def run_benchmark(ctx: JobContext) -> Outcome:
    """Рост работы программы с размером ввода (вся программа)."""
    main = require_main(ctx)
    built, failure = build_version(ctx)
    if built is None:
        return failure or cancelled()
    traced, failure = prepare_traced(ctx, built)
    if traced is None:
        return failure or cancelled()
    make, sizes, problems = _inputs(ctx, built, main)
    if make is None:
        return Outcome(RunStatus.FAILED.value, schemas.RunSummary(message=problems[0]))
    measured = measure(ctx, built, traced, main, make, sizes, None)
    if ctx.cancel.is_set():
        return cancelled()
    evidence = _evidence(ctx.state.run_id, measured)
    warnings = list(measured.warnings)
    if evidence["growth_k"] is not None and measured.samples[-1].hits == measured.samples[0].hits:
        warnings.append("размер ввода не влияет на работу программы: схема ввода не масштабируется")
    message = evidence["verdict"] or "рост не измерен: слишком мало точек"
    data = {**evidence, "startup_ms": measured.startup_ms, "warnings": warnings}
    return Outcome(RunStatus.DONE.value, schemas.RunSummary(message=message, data=data))


def _find(ctx: JobContext, finding_id: str) -> Finding | None:
    with Session(get_engine()) as session:
        project = session.get(Project, ctx.project_id)
        assert project is not None
        version = versions_service.get_version_by_id(session, ctx.version_id)
        java = graphs.java_project(session, version)
        return next(
            (
                f
                for f in analysis_service.run(java, project, ctx.settings).findings
                if f.id == finding_id
            ),
            None,
        )


def run_verify(ctx: JobContext) -> Outcome:
    """«Проверить» у находки: PERF — рост на размерах N…8N; остальное — достижимость места сценариями."""
    finding_id = ctx.request.finding_id or ""
    finding = _find(ctx, finding_id)
    if finding is None:
        return Outcome(
            RunStatus.FAILED.value, schemas.RunSummary(message=f"находка {finding_id} не найдена")
        )
    main = require_main(ctx)
    built, failure = build_version(ctx)
    if built is None:
        return failure or cancelled()
    traced, failure = prepare_traced(ctx, built)
    if traced is None:
        return failure or cancelled()
    target = finding.location.node_id
    data: dict[str, Any] = {"finding_id": finding.id, "target": target}

    if finding.category == "PERF":
        make, sizes, problems = _inputs(ctx, built, main)
        if make is None:
            data["warnings"] = problems
            return Outcome(
                RunStatus.DONE.value,
                schemas.RunSummary(
                    message="не подтверждено динамически: " + problems[0], data=data
                ),
            )
        measured = measure(ctx, built, traced, main, make, sizes, target)
        if ctx.cancel.is_set():
            return cancelled()
        evidence = _evidence(ctx.state.run_id, measured)
        warnings = list(measured.warnings)
        k = evidence["growth_k"]
        predicted = finding.complexity.current if finding.complexity else None
        if k is not None and predicted:
            warning = bench.disagreement(k, predicted)
            if warning:
                warnings.append(warning)
        data.update({**evidence, "predicted": predicted, "warnings": warnings})
        message = evidence["verdict"] or "рост не измерен: слишком мало точек"
        if predicted:
            message += f" (статически {predicted})"
        return Outcome(RunStatus.DONE.value, schemas.RunSummary(message=message, data=data))

    # BUG/MAINT: достижимость места находки сценариями проекта
    with Session(get_engine()) as session:
        scenarios = load_scenarios(session, ctx.project_id, None)
    reached = 0
    for scenario in scenarios:
        if ctx.cancel.is_set():
            return cancelled()
        result = run_in_slot(
            ctx,
            traced.built,
            main,
            stdin=scenario.stdin,
            args=list(scenario.args),
            trace_limit=trace_limit_of(ctx),
        )
        hits = collect(traced.build.instrumented, result.trace)
        if _target_hits(hits, target) > 0:
            reached += 1
    data.update({"reached_by": reached, "scenarios": len(scenarios)})
    if reached:
        message = f"Место находки выполняется в {reached} из {len(scenarios)} сценариев"
    else:
        message = "не подтверждено динамически: ни один сценарий не доходит до места находки"
    return Outcome(RunStatus.DONE.value, schemas.RunSummary(message=message, data=data))


def benchmark_handlers() -> dict[str, Handler]:
    return {"benchmark": run_benchmark, "verify": run_verify}
