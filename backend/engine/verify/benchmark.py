"""Эмпирический рост: масштабируемый ввод, показатель роста ``k`` и его трактовка (S6.10, docs/07 § 8).

Время работы небольших программ тонет в запуске JVM, поэтому основной показатель строится по
счётчикам выполнения узлов (детерминированным), а по времени — как дополнительный.
"""

from __future__ import annotations

import math
import random
from collections.abc import Sequence
from dataclasses import dataclass, replace

from engine.verify.generator.input_schema import InputHints, SchemaNode
from engine.verify.generator.inputs import (
    _TERMINATORS,
    Pools,
    _count_refs,
    _from_schema,
    _join,
    join_lines,
)

DEFAULT_SIZES = (1000, 2000, 4000, 8000)
TEMPLATE = "${N}"
_STOPPING = _TERMINATORS | {"boom", "crash", "fail", "error", "throw", "panic", "abort"}

# (верхняя граница k, класс): k меньше границы — этот класс
_CLASSES: tuple[tuple[float, str], ...] = (
    (0.35, "O(1)"),
    (1.07, "O(n)"),
    (1.45, "O(n log n)"),
    (2.4, "O(n²)"),
    (3.4, "O(n³)"),
)


@dataclass(frozen=True, slots=True)
class Sample:
    size: int
    time_ms: float
    hits: int | None = None
    timed_out: bool = False


def growth_exponent(points: Sequence[tuple[float, float]], last: int = 3) -> float | None:
    """Наклон прямой ``log y ~ k·log x`` (МНК) по последним ``last`` точкам; ``None`` — данных мало."""
    usable = [(x, y) for x, y in points if x > 0 and y > 0]
    usable = usable[-last:]
    if len(usable) < 2:
        return None
    xs = [math.log(x) for x, _ in usable]
    ys = [math.log(y) for _, y in usable]
    mean_x, mean_y = sum(xs) / len(xs), sum(ys) / len(ys)
    denominator = sum((x - mean_x) ** 2 for x in xs)
    if denominator == 0:
        return None
    return sum((x - mean_x) * (y - mean_y) for x, y in zip(xs, ys, strict=True)) / denominator


def complexity_class(k: float) -> str:
    for bound, name in _CLASSES:
        if k < bound:
            return name
    return f"O(n^{round(k)})"


def verdict_text(k: float | None) -> str | None:
    if k is None:
        return None
    return f"k ≈ {k:.2f} → ~{complexity_class(k)}"


def degree_of(estimate: str) -> float | None:
    """Степень роста из записи ``O(n²)`` (для сравнения с измеренным ``k``)."""
    if "2ⁿ" in estimate:
        return None
    if "n" not in estimate:
        return 0.0
    for symbol, power in (("⁵", 5), ("⁴", 4), ("³", 3), ("²", 2)):
        if symbol in estimate:
            return float(power) + (0.15 if "log" in estimate else 0.0)
    return 1.0 + (0.15 if "log" in estimate else 0.0)


def disagreement(k: float, predicted: str) -> str | None:
    """Предупреждение, если измеренный рост расходится со статической оценкой больше чем на 0,5."""
    expected = degree_of(predicted)
    if expected is None or abs(k - expected) <= 0.5:
        return None
    return f"измеренный рост (k ≈ {k:.2f}) расходится со статической оценкой {predicted}"


def sized(template: str, size: int) -> str:
    return template.replace(TEMPLATE, str(size))


# --- масштабируемый ввод по схеме ---------------------------------------------------------------------------


def _without_stopping(nodes: list[SchemaNode]) -> list[SchemaNode]:
    """Копия схемы без команд выхода и аварийных: после них остальной ввод не читается."""
    result = []
    for node in nodes:
        labels = [label for label in node.labels if label.lower() not in _STOPPING]
        result.append(replace(node, labels=labels, children=_without_stopping(node.children)))
    return result


def _command_lines(
    node: SchemaNode, size: int, pools: Pools, rng: random.Random, delimiter: str
) -> list[str]:
    """``size`` команд по кругу; первый аргумент — порядковый номер (идентификаторы не повторяются)."""
    labels = node.labels or ["add"]
    lines = []
    for index in range(size):
        label = labels[index % len(labels)]
        arguments = [str(index + 1), *(pools.token(rng) for _ in range(rng.randint(1, 4)))]
        lines.append(_join([label, *arguments], delimiter))
    return lines


def _distinct(kind: str | None, _pools: Pools, rng: random.Random) -> str:
    """Значения токенов для замеров: почти все различны (худший случай для поиска дублей)."""
    if kind in {"int", "long"}:
        return str(rng.randrange(10**9))
    if kind == "double":
        return f"{rng.random() * 1000:.2f}"
    if kind == "word":
        return f"w{rng.randrange(10**9)}"
    return f"text w{rng.randrange(10**9)}"


def scaled_stdin(hints: InputHints, size: int, seed: int) -> str:
    """Ввод, управляемый размером ``size``; детерминирован по ``seed``."""
    rng = random.Random(seed)
    pools = Pools(hints)
    schema = _without_stopping(hints.schema)
    counts = _count_refs(schema)
    if any(node.kind == "command_loop" for node in schema):
        lines: list[str] = []
        for node in schema:
            if node.kind == "command_loop":
                lines.extend(_command_lines(node, size, pools, rng, hints.delimiter))
            else:
                lines.extend(_from_schema([node], pools, rng, 1, counts, hints.delimiter))
        return join_lines(lines)
    return join_lines(_from_schema(schema, pools, rng, size, counts, hints.delimiter, _distinct))
