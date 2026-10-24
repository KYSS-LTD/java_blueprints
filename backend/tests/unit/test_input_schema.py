"""Схема ввода и поиск сценариев (S6.8–S6.9, прототип): чистые части без JDK."""

from __future__ import annotations

import random
from pathlib import Path

from engine.java.project import JavaProject
from engine.verify.generator.input_schema import infer
from engine.verify.generator.inputs import Mutator, TestInput, seed_inputs, split_lines
from engine.verify.generator.search import Budget, Chosen, Execution, minimize, search
from tests.unit.engine_helpers import project_of

SAMPLES = Path(__file__).resolve().parents[3] / "samples"


def sample(name: str) -> JavaProject:
    root = SAMPLES / name
    return JavaProject(
        {
            p.relative_to(root).as_posix(): p.read_text(encoding="utf-8")
            for p in root.rglob("*.java")
        }
    )


def test_scanner_program_gets_tokens_and_a_counted_loop():
    source = """\
import java.util.*;
public class Main {
    public static void main(String[] args) {
        Scanner in = new Scanner(System.in);
        int n = in.nextInt();
        for (int i = 0; i < n; i++) {
            String id = in.next();
            double w = in.nextDouble();
            System.out.println(id + w);
        }
    }
}
"""
    hints = infer(project_of(source))
    assert hints.reads_stdin and hints.inferred
    first, loop = hints.schema
    assert (first.kind, first.type, first.var) == ("token", "int", "n")
    assert (loop.kind, loop.count_ref) == ("repeat", "n")
    assert [c.type for c in loop.children] == ["word", "double"]
    assert hints.read_types == ["int", "word", "double"]


def test_command_loop_collects_labels_from_a_switch_in_another_method():
    hints = infer(sample("demo-orders"))
    [loop] = hints.schema
    assert loop.kind == "command_loop" and loop.count_ref == "EOF"
    assert {"add", "batch", "pay", "quit"} <= set(loop.labels)
    assert "add" in hints.commands and "Иванов" in hints.words
    assert 1000.0 in hints.numbers  # константа MAX_QUANTITY из сравнения


def test_program_without_input_uses_arguments_only():
    source = """\
public class Main {
    public static void main(String[] args) {
        if (args.length > 0) {
            System.out.println(args[0]);
        }
    }
}
"""
    hints = infer(project_of(source))
    assert not hints.reads_stdin and hints.uses_args and not hints.inferred
    seeds = seed_inputs(hints, [], random.Random(1))
    assert any(s.args for s in seeds) and all(s.stdin == "" for s in seeds)


def test_seeds_and_mutations_are_deterministic_for_the_same_seed():
    hints = infer(sample("demo-orders"))
    first = seed_inputs(hints, [], random.Random(7))
    second = seed_inputs(hints, [], random.Random(7))
    assert first == second
    assert any(
        "add 1 1 1 1 1" in s.stdin for s in first
    )  # «универсальный» токен проходит любой разбор

    def mutants(seed: int) -> list[TestInput]:
        rng = random.Random(seed)
        mutator = Mutator(hints, [], rng)
        parent = TestInput("add 1 Иванов Хлеб 2 45.5\nlist\n")
        return [mutator.mutate(parent) for _ in range(20)]

    assert mutants(3) == mutants(3)
    assert mutants(3) != mutants(4)
    assert all(m.stdin == "" or m.stdin.endswith("\n") for m in mutants(3))


def test_search_keeps_only_inputs_with_new_coverage_and_is_repeatable():
    hints = infer(sample("demo-orders"))

    def executor(batch):
        """Покрытие — множество слов команд в вводе: чем больше разных команд, тем больше признаков."""
        return [
            Execution(
                frozenset(f"cmd:{w}" for line in split_lines(item.stdin) for w in line.split()[:1])
            )
            for item in batch
        ]

    def run():
        return search(hints, executor, seed=11, budget=Budget(runs=60, batch=6, stall_rounds=3))

    result, again = run(), run()
    assert [c.input for c in result.selected] == [c.input for c in again.selected]
    assert result.covered == again.covered and result.runs <= 60
    assert 0 < len(result.selected) <= 25
    # каждый оставленный сценарий дал то, чего не было у предыдущих
    seen: set[str] = set()
    for chosen in result.selected:
        assert chosen.new and not (chosen.new & seen)
        seen |= chosen.new


def test_search_counts_existing_scenarios_as_already_covered():
    hints = infer(sample("demo-orders"))
    existing = [TestInput("add 1 a b 2 3.5\n")]

    def executor(batch):
        return [
            Execution(frozenset({"base"} if item.stdin.startswith("add") else {"other"}))
            for item in batch
        ]

    result = search(hints, executor, seed=1, existing=existing, budget=Budget(runs=30, batch=5))
    assert "base" in result.baseline
    assert all("base" not in c.new for c in result.selected)


def test_minimize_drops_inputs_that_add_nothing():
    a = Chosen(TestInput("a"), frozenset({"x", "y"}), frozenset({"x", "y"}))
    b = Chosen(TestInput("b"), frozenset({"y"}), frozenset({"y"}))
    c = Chosen(TestInput("c"), frozenset({"z", "y"}), frozenset({"z"}))
    kept = minimize([a, b, c], frozenset(), limit=10)
    assert [k.input.stdin for k in kept] == ["a", "c"]
    assert minimize([a, b, c], frozenset({"x", "y", "z"}), limit=10) == []
    assert len(minimize([a, c], frozenset(), limit=1)) == 1
