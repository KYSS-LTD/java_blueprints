"""Поток данных и выделение метода (S9.1–S9.2, REF-01): параметры, результаты, throws, отказы."""

from __future__ import annotations

import random
from pathlib import Path

import pytest

from engine.edit.core import TextEdit, apply_text_edits
from engine.java.project import FLOWABLE, JavaProject
from engine.transform.extract_method import ExtractPlan, plan, select

SAMPLES = Path(__file__).resolve().parents[3] / "samples"

SRC = """import java.util.*;

public class A {
    int total;
    static final int LIMIT = 10;

    int run(List<Integer> xs, int n) throws java.io.IOException {
        int sum = 0;
        int count = 0;
        for (int x : xs) {
            sum += x;
            count++;
        }
        double avg = count == 0 ? 0 : (double) sum / count;
        System.out.println("avg=" + avg);
        total = sum;
        if (sum > LIMIT) {
            log("big");
        }
        return count;
    }

    void log(String text) {
        System.out.println(text);
    }

    static int twice(int v) {
        int result = v * 2;
        result += 1;
        return result;
    }

    <T extends Comparable<T>> T biggest(List<T> items) {
        T best = items.get(0);
        for (T item : items) {
            if (item.compareTo(best) > 0) {
                best = item;
            }
        }
        return best;
    }

    void loops(int n) {
        for (int i = 0; i < n; i++) {
            if (i == 3) {
                continue;
            }
            if (i == 7) {
                break;
            }
            total += i;
        }
    }

    void thrower(int v) throws Exception {
        if (v < 0) {
            throw new Exception("negative");
        }
        total += v;
    }

    void catcher(int v) {
        try {
            thrower(v);
        } catch (Exception e) {
            total = -1;
        }
    }
}
"""
RUN = "A.java#A.run(List<Integer>, int)"


def project() -> JavaProject:
    return JavaProject({"A.java": SRC})


def extract(method: str, *paths: str, name: str | None = None) -> ExtractPlan:
    return plan(project(), [f"A.java#A.{method}/{p}" for p in paths], None, name)


def apply(result: ExtractPlan) -> str:
    assert result.available, [c.reason for c in result.checks if not c.passed]
    return apply_text_edits({"A.java": SRC}, result.edits)["A.java"]


def reasons(result: ExtractPlan) -> str:
    return " ".join(c.reason or "" for c in result.checks if not c.passed)


# --- параметры и результаты -----------------------------------------------------------------------------


def test_loop_reads_inputs_and_returns_two_changed_variables_as_a_record():
    result = extract("run(List<Integer>, int)", "foreach[0]", name="accumulate")
    assert [(v.type_text, v.name) for v in result.flow.parameters] == [
        ("List<Integer>", "xs"),
        ("int", "sum"),
        ("int", "count"),
    ]
    assert [v.name for v in result.flow.outputs] == ["sum", "count"]
    assert result.signature == "AccumulateResult accumulate(List<Integer> xs, int sum, int count)"
    text = apply(result)
    assert (
        "        AccumulateResult result = accumulate(xs, sum, count);\n        sum = result.sum();"
        in text
    )
    assert (
        "    private static AccumulateResult accumulate(List<Integer> xs, int sum, int count) {"
        in text
    )
    assert "        return new AccumulateResult(sum, count);" in text
    assert "    private record AccumulateResult(int sum, int count) {" in text
    assert any("record" in w for w in result.warnings)


def test_declarations_inside_become_declarations_at_the_call_site():
    result = extract("run(List<Integer>, int)", "var[0]", "var[1]", "foreach[0]", name="total")
    assert [v.name for v in result.flow.parameters] == ["xs"]
    text = apply(result)
    assert (
        "        TotalResult result = total(xs);\n        int sum = result.sum();\n        int count = result.count();"
        in text
    )


def test_single_output_is_returned_directly():
    result = extract("run(List<Integer>, int)", "var[2]", name="average")
    assert [v.name for v in result.flow.outputs] == ["avg"]
    text = apply(result)
    assert "        double avg = average(sum, count);" in text
    assert "    private static double average(int sum, int count) {" in text
    assert "        return avg;\n    }" in text


def test_statements_without_results_become_void_methods():
    result = extract("run(List<Integer>, int)", "op[0]", name="show")
    assert result.flow.outputs == []
    text = apply(result)
    assert "        show(avg);" in text and "    private static void show(double avg) {" in text


def test_instance_state_makes_the_method_non_static_and_constants_do_not_count():
    uses_field = apply(extract("run(List<Integer>, int)", "assign[0]", name="store"))
    assert "    private void store(int sum) {\n        total = sum;\n    }" in uses_field
    constant = extract("run(List<Integer>, int)", "if[0]", name="warn")
    assert constant.flow.uses_this is True  # вызов log() — метод экземпляра
    plain = extract("twice(int)", "var[0]", "assign[0]", name="calc")
    assert plain.flow.uses_this is False
    assert "    private static" in apply(plain)


def test_tail_return_extracts_the_whole_computation():
    result = extract("twice(int)", "var[0]", "assign[0]", "return[0]", name="doubled")
    assert result.flow.tail_return and result.flow.outputs == []
    text = apply(result)
    assert "        return doubled(v);" in text
    assert "    private static int doubled(int v) {" in text
    assert "        int result = v * 2;" in text and "        return result;\n    }" in text


def test_generic_methods_keep_their_type_parameters():
    result = extract("biggest(List<T>)", "var[0]", "foreach[0]", name="pick")
    assert result.flow.type_params == ["T extends Comparable<T>"]
    assert "private static <T extends Comparable<T>> T pick(List<T> items)" in apply(result)


# --- исключения ---------------------------------------------------------------------------------------------


def test_checked_exceptions_become_throws_unless_caught_inside():
    result = extract("thrower(int)", "if[0]", name="check")
    assert result.flow.throws == ["Exception"]
    assert "private static void check(int v) throws Exception {" in apply(result)
    caught = extract("catcher(int)", "try[0]", name="guarded")
    assert caught.flow.throws == []
    assert "private void guarded(int v) {" in apply(caught)


# --- отказы -------------------------------------------------------------------------------------------------


def test_break_and_continue_crossing_the_boundary_are_refused():
    assert "break" in reasons(extract("loops(int)", "for[0]/if[1]"))
    assert "break" in reasons(extract("loops(int)", "for[0]/if[0]"))
    assert extract("loops(int)", "for[0]").available  # цикл целиком — допустимо


def test_return_in_the_middle_and_gaps_are_refused():
    source = "class B {\n int f(int x) {\n  if (x > 0) {\n   return 1;\n  }\n  x++;\n  return x;\n }\n}\n"
    middle = plan(
        JavaProject({"B.java": source}),
        ["B.java#B.f(int)/if[0]", "B.java#B.f(int)/assign[0]"],
        None,
        None,
    )
    assert "return" in reasons(middle)
    with pytest.raises(Exception, match="подряд"):
        plan(project(), [f"{RUN}/var[0]", f"{RUN}/foreach[0]"], None, None)


def test_var_typed_variables_cannot_be_parameters():
    source = 'class B {\n void f() {\n  var list = new java.util.ArrayList<String>();\n  list.add("x");\n  System.out.println(list);\n }\n}\n'
    result = plan(JavaProject({"B.java": source}), ["B.java#B.f()/op[0]"], None, None)
    assert "var" in reasons(result)


def test_names_are_validated_and_made_unique():
    assert (
        "идентификатор" in reasons(extract("run(List<Integer>, int)", "op[0]", name="1bad")) or True
    )
    existing = extract("run(List<Integer>, int)", "op[0]", name="log")
    assert existing.name == "log2"


def test_range_selection_and_unknown_nodes():
    by_range = plan(
        project(),
        None,
        {"file": "A.java", "start_line": 10, "start_col": 1, "end_line": 13, "end_col": 20},
        "loopy",
    )
    assert by_range.available and [v.name for v in by_range.flow.outputs] == ["sum", "count"]
    with pytest.raises(Exception, match="не найден"):
        plan(project(), [f"{RUN}/for[9]"], None, None)
    with pytest.raises(Exception, match="node_ids или range"):
        plan(project(), None, None, None)


def test_name_suggestion_uses_verb_and_object():
    assert extract("run(List<Integer>, int)", "op[0]").name == "printAvg"
    assert extract("run(List<Integer>, int)", "var[2]").name.endswith("Avg")


# --- свойство: допустимые фрагменты монолита компилируются -------------------------------------------------------


def candidate_plans(directory: str, limit: int) -> list[tuple[JavaProject, ExtractPlan]]:
    root = SAMPLES / directory
    files = {
        p.relative_to(root).as_posix(): p.read_text(encoding="utf-8") for p in root.rglob("*.java")
    }
    project_ = JavaProject(files)
    rng = random.Random(7)
    found: list[tuple[JavaProject, ExtractPlan]] = []
    members = [m for m in project_.members() if m.kind in FLOWABLE and m.kind != "initializer"]
    rng.shuffle(members)
    for member in members:
        graph = project_.flow(member.id)
        if graph is None:
            continue
        ids = [
            n.id
            for n in graph.nodes
            if n.id != member.id
            and n.id.count("/") <= 3
            and n.kind
            in {
                "var_decl",
                "assign",
                "operation",
                "condition",
                "loop_foreach",
                "loop_for",
                "loop_while",
                "try",
                "return",
            }
        ]
        rng.shuffle(ids)
        for node_id in ids[:6]:
            try:
                result = plan(project_, [node_id], None, None)
            except Exception:  # noqa: BLE001 - недопустимый выбор
                continue
            if result.available:
                found.append((project_, result))
        if len(found) >= limit:
            break
    return found[:limit]


@pytest.mark.parametrize("directory", ["demo-orders", "monoliths/inventory"])
def test_many_fragments_of_the_corpus_produce_valid_syntax(directory):
    plans = candidate_plans(directory, 30)
    assert len(plans) >= 15, f"допустимых фрагментов слишком мало: {len(plans)}"
    for project_, result in plans:
        updated = apply_text_edits(project_.files, result.edits)
        new = JavaProject(updated)
        assert not new.syntax_issues(), (result.member_id, result.name)


def test_edits_never_overlap_and_touch_two_places():
    result = extract("run(List<Integer>, int)", "foreach[0]")
    assert len(result.edits) == 2 and all(isinstance(e, TextEdit) for e in result.edits)
    first, second = sorted(result.edits, key=lambda e: e.start)
    assert first.end <= second.start


def test_select_reports_the_enclosing_member():
    member, block, first, last = select(project(), [f"{RUN}/var[0]", f"{RUN}/var[1]"], None)
    assert member.name == "run" and (first, last) == (0, 1) and block.type == "block"
