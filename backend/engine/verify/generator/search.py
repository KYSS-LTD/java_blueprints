"""Поиск сценариев с обратной связью по покрытию (S6.9, docs/07 § 7.3).

Детерминированный «серый ящик»: начальный набор (seed) и мутации запускаются на инструментированной
сборке, кандидат сохраняется, если покрывает блоки или исходы условий, которых ещё не было
(жадное покрытие множества). Затем набор сжимается: остаются сценарии, дающие новое покрытие.
Исполнение запусков — за вызывающим (``Executor``), поэтому поиск не знает ни про JVM, ни про БД.
"""

from __future__ import annotations

import random
import time
from collections.abc import Callable, Sequence
from dataclasses import dataclass, field

from engine.verify.generator.input_schema import InputHints
from engine.verify.generator.inputs import Mutator, TestInput, seed_inputs, split_lines


@dataclass(frozen=True, slots=True)
class Execution:
    """Итог одного запуска кандидата: признаки покрытия и как он завершился."""

    features: frozenset[str]
    ok: bool = True  # False — таймаут, переполнение вывода или сбой запуска: кандидат отбрасывается
    exit_code: int | None = 0
    exception: str | None = None


Executor = Callable[[Sequence[TestInput]], list[Execution]]


@dataclass(frozen=True, slots=True)
class Budget:
    runs: int = 300
    seconds: float = 60.0
    batch: int = 8
    stall_rounds: int = 10
    max_scenarios: int = 25


@dataclass(slots=True)
class Chosen:
    input: TestInput
    features: frozenset[str]
    new: frozenset[str]
    exit_code: int | None = 0
    exception: str | None = None


@dataclass(slots=True)
class SearchResult:
    selected: list[Chosen]
    covered: frozenset[str]
    baseline: frozenset[str]  # покрытие уже существовавших сценариев
    runs: int
    elapsed_s: float
    stop_reason: str
    accepted: int = 0
    log: list[str] = field(default_factory=list)


def outcome_features(execution: Execution) -> frozenset[str]:
    """Признаки покрытия плюс исход: падение с другим исключением — тоже новое поведение."""
    extra = set(execution.features)
    if execution.exception:
        extra.add(f"exc:{execution.exception}")
    if execution.exit_code not in (0, None):
        extra.add(f"exit:{execution.exit_code}")
    return frozenset(extra)


def search(
    hints: InputHints,
    executor: Executor,
    *,
    seed: int,
    budget: Budget = Budget(),  # noqa: B008 - неизменяемый dataclass
    existing: Sequence[TestInput] = (),
    templates: Sequence[str] = (),
    on_progress: Callable[[int, int, int], None] | None = None,
    cancelled: Callable[[], bool] = lambda: False,
) -> SearchResult:
    rng = random.Random(seed)
    started = time.monotonic()
    log: list[str] = []
    runs = 0
    covered: set[str] = set()
    corpus: list[TestInput] = []
    chosen: list[Chosen] = []
    seen: set[tuple[str, tuple[str, ...]]] = set()

    def run(batch: list[TestInput]) -> list[Execution]:
        nonlocal runs
        results = executor(batch)
        runs += len(batch)
        if on_progress is not None:
            on_progress(runs, budget.runs, len(covered))
        return results

    # 1. уже существующие сценарии: их покрытие считается данным
    base_lines: list[str] = []
    if existing:
        for item, result in zip(existing, run(list(existing)), strict=True):
            covered |= outcome_features(result)
            corpus.append(item)
            seen.add(item.key)
            base_lines.extend(split_lines(item.stdin))
    baseline = frozenset(covered)

    mutator = Mutator(hints, [*templates, *base_lines], rng)

    def consider(batch: list[TestInput], results: list[Execution]) -> bool:
        progress = False
        for item, result in zip(batch, results, strict=True):
            if not result.ok:
                continue
            features = outcome_features(result)
            fresh = features - covered
            if fresh:
                covered.update(features)
                corpus.append(item)
                chosen.append(
                    Chosen(item, features, frozenset(fresh), result.exit_code, result.exception)
                )
                progress = True
        return progress

    # 2. seed-набор
    seeds = [s for s in seed_inputs(hints, [], rng) if s.key not in seen]
    for item in seeds:
        seen.add(item.key)
    stop = "бюджет запусков исчерпан"
    for start in range(0, len(seeds), budget.batch):
        if runs >= budget.runs or cancelled():
            break
        batch = seeds[start : start + budget.batch]
        consider(batch, run(batch))
    log.append(f"seed: {len(chosen)} полезных из {min(len(seeds), runs)}")

    # 3. мутации
    stalled = 0
    while runs < budget.runs:
        if cancelled():
            stop = "отменено"
            break
        if time.monotonic() - started > budget.seconds:
            stop = "время вышло"
            break
        if stalled >= budget.stall_rounds:
            stop = "новое покрытие не находится"
            break
        pool = corpus or [TestInput("", (), "empty")]
        batch = []
        attempts = 0
        while len(batch) < min(budget.batch, budget.runs - runs) and attempts < 60:
            attempts += 1
            recent = chosen[-8:]
            parent = rng.choice(recent).input if recent and rng.random() < 0.5 else rng.choice(pool)
            other = rng.choice(pool) if len(pool) > 1 else None
            candidate = mutator.mutate(parent, other)
            if candidate.key in seen:
                continue
            seen.add(candidate.key)
            batch.append(candidate)
        if not batch:
            stop = "новых кандидатов нет"
            break
        stalled = 0 if consider(batch, run(batch)) else stalled + 1

    elapsed = time.monotonic() - started
    selected = minimize(chosen, baseline, budget.max_scenarios)
    return SearchResult(
        selected=selected,
        covered=frozenset(covered),
        baseline=baseline,
        runs=runs,
        elapsed_s=elapsed,
        stop_reason=stop,
        accepted=len(chosen),
        log=log,
    )


def minimize(chosen: Sequence[Chosen], baseline: frozenset[str], limit: int) -> list[Chosen]:
    """Жадное сжатие: каждый шаг берёт сценарий с наибольшим числом ещё не покрытых признаков."""
    remaining = list(chosen)
    have = set(baseline)
    result: list[Chosen] = []
    while remaining and len(result) < limit:
        best = max(remaining, key=lambda c: (len(c.features - have), -remaining.index(c)))
        gain = best.features - have
        if not gain:
            break
        result.append(
            Chosen(best.input, best.features, frozenset(gain), best.exit_code, best.exception)
        )
        have |= best.features
        remaining.remove(best)
    return result
