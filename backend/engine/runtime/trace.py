"""Сборка инструментированной копии и разбор счётчиков трассировки (S6.6–S6.7, docs/07 § 6)."""

from __future__ import annotations

from collections.abc import Iterable
from dataclasses import dataclass, field
from pathlib import Path
from typing import Any

from engine.java.project import JavaProject
from engine.runtime.compiler import CompileResult, compile_workspace
from engine.runtime.instrument import INSTRUMENT_VERSION, Instrumented, Probe, instrument
from engine.runtime.workspace import Workspace, materialize

DEFAULT_TRACE_LIMIT = 5_000_000


class TraceUnavailable(Exception):
    """Инструментированная копия не собралась ни полной, ни упрощённой."""

    def __init__(self, compiled: CompileResult) -> None:
        super().__init__("инструментированная копия не компилируется")
        self.compiled = compiled


@dataclass(slots=True)
class TraceBuild:
    workspace: Workspace
    instrumented: Instrumented
    compiled: CompileResult


def prepare_trace(
    project: JavaProject,
    builds_dir: Path,
    *,
    java_home: Path | None = None,
    jdk_major: int | None = None,
    timeout_s: int = 30,
) -> TraceBuild:
    """Инструментированная сборка в ``builds/<hash>-t<версия>…``; кеш по хешу версии.

    Сначала полная (с обёртками условий и блоками вместо лямбд-выражений); если она не
    компилируется — без лямбд, затем только со счётчиками перед операторами.
    """
    last: CompileResult | None = None
    for conditions, lambdas in ((True, True), (True, False), (False, False)):
        instrumented = instrument(project, conditions=conditions, lambdas=lambdas)
        key = (
            f"{project.content_hash}-t{INSTRUMENT_VERSION}"
            f"{'c' if conditions else 'b'}{'l' if lambdas else 'n'}"
        )
        workspace = materialize(instrumented.files, builds_dir, key)
        last = compile_workspace(workspace, java_home, jdk_major, timeout_s)
        if last.ok:
            return TraceBuild(workspace, instrumented, last)
    assert last is not None
    raise TraceUnavailable(last)


@dataclass(slots=True)
class TraceHits:
    """Счётчики одного запуска, пересчитанные из слотов в блоки графа.

    ``counts`` — основной счётчик блока: выполнения оператора, вычисления условия, итерации тела
    цикла. ``entries`` — входы в цикл; ``branches`` — исходы условия ``(истина, ложь)``.
    """

    counts: dict[str, int] = field(default_factory=dict)
    entries: dict[str, int] = field(default_factory=dict)
    branches: dict[str, tuple[int, int]] = field(default_factory=dict)
    saturated: bool = False


def _slot(counts: dict[str, Any], slot: int) -> int:
    return int(counts.get(str(slot), 0))


def collect(instrumented: Instrumented, raw: dict[str, Any] | None) -> TraceHits:
    """Счётчики из ``hits.json``; ``None`` (файла нет) — ни один блок не выполнялся."""
    counts: dict[str, Any] = (raw or {}).get("counts", {})
    hits = TraceHits(saturated=bool((raw or {}).get("saturated", False)))
    for probe in instrumented.probes:
        value = _slot(counts, probe.slot)
        hits.counts[probe.node_id] = value
        if probe.mode == "cond":
            truthy = _slot(counts, probe.slot + 1)
            hits.branches[probe.node_id] = (truthy, max(value - truthy, 0))
        elif probe.mode == "loop" and probe.entry_slot is not None:
            hits.entries[probe.node_id] = _slot(counts, probe.entry_slot)
    return hits


def reached(probe: Probe, hits: TraceHits) -> bool:
    """Блок достигнут: оператор выполнялся, условие вычислялось, в цикл входили."""
    if probe.mode == "loop" and probe.node_id in hits.entries:
        return hits.entries[probe.node_id] > 0
    return hits.counts.get(probe.node_id, 0) > 0


def features(instrumented: Instrumented, hits: TraceHits) -> frozenset[str]:
    """Что покрыл запуск, с точностью до исходов: ``узел``, ``узел:T``/``узел:F``, ``узел:iter``.

    По этим признакам генератор сценариев решает, даёт ли кандидат что-то новое.
    """
    found: set[str] = set()
    for probe in instrumented.probes:
        if reached(probe, hits):
            found.add(probe.node_id)
        if probe.mode == "cond":
            truthy, falsy = hits.branches.get(probe.node_id, (0, 0))
            if truthy:
                found.add(f"{probe.node_id}:T")
            if falsy:
                found.add(f"{probe.node_id}:F")
        elif probe.mode == "loop" and hits.counts.get(probe.node_id, 0) > 0:
            found.add(f"{probe.node_id}:iter")
    return frozenset(found)


@dataclass(frozen=True, slots=True)
class Coverage:
    covered: frozenset[str]
    total: int
    uncovered: tuple[str, ...]

    @property
    def ratio(self) -> float:
        return len(self.covered) / self.total if self.total else 0.0


def coverage_of(instrumented: Instrumented, runs: Iterable[TraceHits]) -> Coverage:
    """Покрытие блоков набором запусков: доля достигнутых блоков среди инструментированных."""
    covered: set[str] = set()
    for hits in runs:
        covered.update(p.node_id for p in instrumented.probes if reached(p, hits))
    ordered = [p.node_id for p in instrumented.probes]
    return Coverage(
        frozenset(covered),
        len(ordered),
        tuple(node for node in ordered if node not in covered),
    )
