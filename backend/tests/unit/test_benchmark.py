"""Показатель роста и масштабируемый ввод (S6.10) — чистые части без JDK."""

from __future__ import annotations

import math

import pytest

from engine.verify.benchmark import (
    DEFAULT_SIZES,
    complexity_class,
    degree_of,
    disagreement,
    growth_exponent,
    scaled_stdin,
    sized,
    verdict_text,
)
from engine.verify.generator.input_schema import infer
from engine.verify.generator.inputs import split_lines
from tests.unit.engine_helpers import project_of
from tests.unit.test_input_schema import sample


def points(f, sizes=DEFAULT_SIZES):
    return [(n, f(n)) for n in sizes]


def test_growth_exponent_of_known_complexities():
    assert growth_exponent(points(lambda n: 3.0 * n)) == pytest.approx(1.0, abs=1e-9)
    assert growth_exponent(points(lambda n: 0.01 * n**2)) == pytest.approx(2.0, abs=1e-9)
    assert growth_exponent(points(lambda n: 5.0 * n**3)) == pytest.approx(3.0, abs=1e-9)
    assert growth_exponent(points(lambda n: 7.0)) == pytest.approx(0.0, abs=1e-9)
    nlogn = growth_exponent(points(lambda n: n * math.log2(n)))
    assert nlogn is not None and 1.05 < nlogn < 1.25


def test_growth_exponent_uses_only_the_last_points_and_needs_two():
    mixed = [(1000, 5.0), (2000, 5.0), (4000, 20.0), (8000, 80.0), (16000, 320.0)]
    assert growth_exponent(mixed) == pytest.approx(2.0)
    assert growth_exponent([(1000, 1.0)]) is None
    assert growth_exponent([(1000, 0.0), (2000, 0.0)]) is None
    assert growth_exponent([(1000, 1.0), (1000, 2.0)]) is None


def test_classification_and_verdict_text():
    assert [complexity_class(k) for k in (0.0, 1.0, 1.2, 1.97, 3.0, 4.2)] == [
        "O(1)",
        "O(n)",
        "O(n log n)",
        "O(n²)",
        "O(n³)",
        "O(n^4)",
    ]
    assert verdict_text(1.97) == "k ≈ 1.97 → ~O(n²)"
    assert verdict_text(None) is None


def test_degree_of_and_disagreement_with_the_static_estimate():
    assert [degree_of(e) for e in ("O(1)", "O(n)", "O(n²)", "O(n³)", "O(2ⁿ)")] == [
        0.0,
        1.0,
        2.0,
        3.0,
        None,
    ]
    assert degree_of("O(n log n)") == pytest.approx(1.15)
    assert disagreement(1.9, "O(n²)") is None
    assert "расходится" in (disagreement(1.0, "O(n²)") or "")
    assert disagreement(5.0, "O(2ⁿ)") is None


def test_template_substitution():
    assert sized("${N}\nfill ${N}", 1000) == "1000\nfill 1000"


def test_counted_schema_scales_with_the_size_and_is_deterministic():
    project = project_of(
        "import java.util.*;\nclass A {\n    public static void main(String[] args) {\n"
        "        Scanner in = new Scanner(System.in);\n        int n = in.nextInt();\n"
        "        for (int i = 0; i < n; i++) {\n            int v = in.nextInt();\n        }\n    }\n}\n"
    )
    hints = infer(project, "A")
    small, large = scaled_stdin(hints, 10, 1), scaled_stdin(hints, 1000, 1)
    assert split_lines(small)[0] == "10" and len(split_lines(small)) == 11
    assert split_lines(large)[0] == "1000" and len(split_lines(large)) == 1001
    assert scaled_stdin(hints, 1000, 1) == large
    assert scaled_stdin(hints, 1000, 2) != large


def test_command_programs_scale_with_ordinary_commands_only():
    hints = infer(sample("demo-orders"), "Main")
    stdin = scaled_stdin(hints, 300, 7)
    lines = split_lines(stdin)
    assert len(lines) >= 300
    commands = {line.split()[0] for line in lines}
    assert not commands & {"quit", "exit", "boom"}
    assert commands <= set(hints.commands)
    assert lines[0].split()[1] == "1" and lines[10].split()[1] == "11"  # номера возрастают


# --- масштабирование по рабочим сценариям проекта -----------------------------------------------------------------


def test_scaler_repeats_the_most_common_numbered_command_and_keeps_the_queries():
    from engine.verify.benchmark import derive_scaler

    scaler = derive_scaler(
        [
            "add 1 Иванов Хлеб 2 45.5\nadd 2 Орлова Масло 3 640\nstats\nquit\n",
            "add 1 Попов Сыр 1 520\nstats\nboom\nquit\n",
        ]
    )
    assert scaler is not None and scaler.label == "add"
    lines = scaler.stdin(5).splitlines()
    assert lines[:5] == [
        "add 1 Иванов Хлеб 2 45.5",
        "add 2 Орлова Масло 3 640",
        "add 3 Иванов Хлеб 2 45.5",
        "add 4 Орлова Масло 3 640",
        "add 5 Иванов Хлеб 2 45.5",
    ]
    assert lines[5:] == [
        "stats",
        "quit",
    ]  # запросы остаются после роста, аварийные команды отброшены


def test_scaler_needs_a_repeated_numbered_command():
    from engine.verify.benchmark import derive_scaler

    assert derive_scaler(["hello\nworld\n", ""]) is None
    assert derive_scaler(["add 1 x\nquit\n"]) is None  # одна строка — это не повторяемая команда
