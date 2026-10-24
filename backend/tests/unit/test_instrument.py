"""Инструментирование и трассировка (S6.6): структура вставок без JDK, счётчики и идентичность
поведения на реальном JDK (метка `jdk`)."""

from __future__ import annotations

import json
from pathlib import Path

import pytest

from engine.java.project import JavaProject
from engine.runtime.compiler import compile_workspace
from engine.runtime.instrument import TRACE_FILE, instrument
from engine.runtime.runner import run_java
from engine.runtime.trace import (
    collect,
    coverage_of,
    features,
    prepare_trace,
)
from engine.runtime.workspace import materialize
from tests.unit.engine_helpers import project_of

SAMPLES = Path(__file__).resolve().parents[3] / "samples"


def sample_files(name: str) -> dict[str, str]:
    root = SAMPLES / name
    return {
        p.relative_to(root).as_posix(): p.read_text(encoding="utf-8") for p in root.rglob("*.java")
    }


def probe_of(result, suffix: str):
    found = [p for p in result.probes if p.node_id.endswith(suffix)]
    assert len(found) == 1, (suffix, [p.node_id for p in result.probes])
    return found[0]


def program(body: str, extra: str = "") -> str:
    return (
        "import java.util.*;\n"
        "public class Main {\n"
        f"    {extra}\n"
        "    public static void main(String[] args) {\n"
        f"{body}\n"
        "    }\n"
        "}\n"
    )


# --- структура вставок (без JDK) ----------------------------------------------------------------------


def test_line_numbers_do_not_change_and_the_trace_class_is_added():
    source = program("        int x = 1;\n        if (x > 0) {\n            x++;\n        }\n")
    result = instrument(project_of(source))
    assert TRACE_FILE in result.files
    assert result.files["Main.java"].count("\n") == source.count("\n")
    assert "jbtrace.JbTrace.hit(" in result.files["Main.java"]


def test_every_statement_gets_its_own_slot_and_conditions_get_two():
    source = program("        int x = 1;\n        if (x > 0) {\n            x++;\n        }\n")
    result = instrument(project_of(source))
    kinds = {p.kind: p for p in result.probes}
    assert kinds["var_decl"].mode == "exec"
    assert kinds["condition"].mode == "cond"
    assert kinds["assign"].mode == "exec"
    slots = [p.slot for p in result.probes]
    assert len(slots) == len(set(slots))
    assert "cond(" in result.files["Main.java"]


def test_single_statement_bodies_are_wrapped_in_blocks():
    source = program(
        "        int x = 0;\n        if (x == 0) x++; else x--;\n        for (int i = 0; i < 3; i++) x += i;\n"
    )
    text = instrument(project_of(source)).files["Main.java"]
    assert "if (jbtrace.JbTrace.cond(" in text
    assert "{ jbtrace.JbTrace.hit(" in text
    assert text.count("{") == text.count("}")


def test_pattern_variables_keep_the_condition_unwrapped():
    source = program(
        '        Object o = "s";\n        if (o instanceof String s && s.length() > 0) {\n'
        "            System.out.println(s);\n        }\n"
    )
    result = instrument(project_of(source))
    node = probe_of(result, "/if[0]")
    assert (
        node.mode == "exec"
    )  # условие с шаблоном нельзя оборачивать: переменная s потеряла бы область видимости


def test_loop_counts_iterations_and_entries_separately():
    source = program(
        "        for (int i = 0; i < 3; i++) {\n            System.out.println(i);\n        }\n"
    )
    result = instrument(project_of(source))
    loop = probe_of(result, "/for[0]")
    assert loop.mode == "loop"
    assert loop.entry_slot is not None
    assert loop.entry_slot != loop.slot


def test_this_call_stays_the_first_statement_of_a_constructor():
    source = (
        "public class Main {\n"
        "    int v;\n"
        "    Main() { this(1); }\n"
        "    Main(int v) { this.v = v; }\n"
        "    public static void main(String[] args) { new Main(); }\n"
        "}\n"
    )
    text = instrument(project_of(source)).files["Main.java"]
    assert "Main() { this(1); jbtrace" in text


def test_expression_lambdas_become_blocks_with_return_or_statement():
    source = program(
        "        List<Integer> xs = new ArrayList<>(List.of(1, 2, 3));\n"
        "        xs.forEach(x -> System.out.println(x));\n"
        "        int s = xs.stream().mapToInt(x -> x * 2).sum();\n"
        "        System.out.println(s);\n"
    )
    result = instrument(project_of(source))
    text = result.files["Main.java"]
    assert "x -> { jbtrace.JbTrace.hit(" in text
    assert "return x * 2; }" in text
    assert not any(v.startswith("лямбда") for v in result.untracked.values())


def test_lambdas_with_unknown_result_type_are_left_alone():
    source = program(
        "        var r = (java.util.function.Supplier<String>) () -> String.valueOf(1);\n"
    )
    result = instrument(project_of(source))
    assert any("лямбда" in reason or "возвращает" in reason for reason in result.untracked.values())


def test_arrow_cases_of_switch_expression_use_yield():
    source = program(
        "        int x = args.length;\n"
        "        int r = switch (x) { case 0 -> 5; case 1 -> throw new IllegalStateException(); default -> 7; };\n"
        "        System.out.println(r);\n"
    )
    result = instrument(project_of(source))
    text = result.files["Main.java"]
    assert "yield 5;" in text
    assert not result.untracked or "стрелка" not in " ".join(result.untracked.values())


def test_files_with_syntax_errors_are_not_instrumented():
    broken = "public class Main { void f( { }"
    result = instrument(JavaProject({"Main.java": broken}))
    assert result.files["Main.java"] == broken
    assert result.skipped_files == []  # граф ошибочного файла не строится, пробы пусты
    assert result.probes == [] or all(p.node_id for p in result.probes)


# --- сквозные проверки (JDK) -------------------------------------------------------------------------------


@pytest.mark.jdk
def test_counters_reflect_loops_branches_and_calls(tmp_path):
    source = program(
        "        Scanner in = new Scanner(System.in);\n"
        "        int n = in.nextInt();\n"
        "        int even = 0;\n"
        "        for (int i = 0; i < n; i++) {\n"
        "            if (i % 2 == 0) {\n"
        "                even++;\n"
        "            }\n"
        "        }\n"
        "        System.out.println(even + helper(n));\n",
        extra="static int helper(int k) { return k * 2; }",
    )
    project = project_of(source)
    traced = prepare_trace(project, tmp_path / "builds")
    result = run_java(traced.workspace, "Main", stdin="5\n", trace_limit=1_000_000)
    assert result.stdout == "13\n"
    hits = collect(traced.instrumented, result.trace)

    assert hits.counts[probe_of(traced.instrumented, "/for[0]").node_id] == 5
    assert hits.entries[probe_of(traced.instrumented, "/for[0]").node_id] == 1
    condition = probe_of(traced.instrumented, "/for[0]/if[0]").node_id
    assert hits.counts[condition] == 5
    assert hits.branches[condition] == (3, 2)
    assert hits.counts[probe_of(traced.instrumented, "/for[0]/if[0]/then/assign[0]").node_id] == 3
    assert hits.counts[probe_of(traced.instrumented, "Main.helper(int)").node_id] == 1
    assert not hits.saturated


@pytest.mark.jdk
def test_counters_saturate_at_the_limit_and_flag_it(tmp_path):
    source = program(
        "        long s = 0;\n        for (int i = 0; i < 1000; i++) {\n            s += i;\n        }\n        System.out.println(s);\n"
    )
    traced = prepare_trace(project_of(source), tmp_path / "builds")
    result = run_java(traced.workspace, "Main", trace_limit=100)
    assert result.stdout == "499500\n"  # поведение программы не меняется
    hits = collect(traced.instrumented, result.trace)
    assert hits.saturated
    assert max(hits.counts.values()) <= 100


@pytest.mark.jdk
def test_features_distinguish_branch_outcomes(tmp_path):
    source = program(
        "        int x = args.length;\n        if (x > 0) {\n            x++;\n        }\n        System.out.println(x);\n"
    )
    traced = prepare_trace(project_of(source), tmp_path / "builds")
    only_false = collect(
        traced.instrumented, run_java(traced.workspace, "Main", trace_limit=1000).trace
    )
    with_arg = collect(
        traced.instrumented, run_java(traced.workspace, "Main", args=["a"], trace_limit=1000).trace
    )
    condition = probe_of(traced.instrumented, "/if[0]").node_id
    assert f"{condition}:F" in features(traced.instrumented, only_false)
    assert f"{condition}:T" not in features(traced.instrumented, only_false)
    assert f"{condition}:T" in features(traced.instrumented, with_arg)
    coverage = coverage_of(traced.instrumented, [only_false])
    assert 0 < coverage.ratio < 1
    assert coverage_of(traced.instrumented, [only_false, with_arg]).ratio == 1.0


@pytest.mark.jdk
def test_uncaught_exception_still_writes_counters(tmp_path):
    source = program(
        '        System.out.println("start");\n        throw new IllegalStateException("boom");\n'
    )
    traced = prepare_trace(project_of(source), tmp_path / "builds")
    result = run_java(traced.workspace, "Main", trace_limit=1000)
    assert result.exit_code == 1
    assert (
        result.exception is not None and result.exception.type == "java.lang.IllegalStateException"
    )
    hits = collect(traced.instrumented, result.trace)
    assert hits.counts[probe_of(traced.instrumented, "/op[0]").node_id] == 1
    assert hits.counts[probe_of(traced.instrumented, "/throw[0]").node_id] == 1


@pytest.mark.jdk
@pytest.mark.parametrize(
    "sample",
    [
        "demo-orders",
        "monoliths/text-analyzer",
        "monoliths/graph-paths",
        "scope/b3-lambda",
        "scope/b1-enum",
        "scope/01-control",
    ],
)
def test_instrumented_program_behaves_exactly_like_the_original(sample, tmp_path):
    root = SAMPLES / sample
    files = sample_files(sample)
    project = JavaProject(files)
    main = project.main_classes()[0]
    original = materialize(files, tmp_path / "orig", "orig")
    assert compile_workspace(original).ok
    traced = prepare_trace(project, tmp_path / "traced")
    scenarios = sorted((root / "scenarios").glob("*.json"))
    assert scenarios
    for path in scenarios[:5]:
        data = json.loads(path.read_text(encoding="utf-8"))
        before = run_java(original, main, stdin=data.get("stdin", ""), args=data.get("args", []))
        after = run_java(
            traced.workspace,
            main,
            stdin=data.get("stdin", ""),
            args=data.get("args", []),
            trace_limit=5_000_000,
        )
        assert (after.stdout, after.exit_code) == (before.stdout, before.exit_code), path.name
        assert (after.exception and after.exception.type) == (
            before.exception and before.exception.type
        )
        assert after.trace is not None


@pytest.mark.jdk
@pytest.mark.slow
def test_whole_corpus_compiles_instrumented(tmp_path):
    failures = []
    for group in ("monoliths", "scope"):
        for directory in sorted((SAMPLES / group).iterdir()):
            if not directory.is_dir():
                continue
            files = sample_files(f"{group}/{directory.name}")
            if not files:
                continue
            project = JavaProject(files)
            if not compile_workspace(materialize(files, tmp_path / "orig", directory.name)).ok:
                continue
            traced = prepare_trace(project, tmp_path / "traced")
            if not (traced.instrumented.conditions and traced.instrumented.lambdas):
                failures.append(directory.name)
    assert not failures, f"упрощённая инструментация понадобилась для: {failures}"
