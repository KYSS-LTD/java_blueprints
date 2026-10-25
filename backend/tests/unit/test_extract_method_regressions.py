"""Дефекты «Выделить в метод», найденные дифференциальной проверкой на корпусе (scripts/extract_differential.py)."""

from __future__ import annotations

from engine.edit.core import apply_text_edits
from engine.java.project import JavaProject
from engine.transform.extract_method import ExtractPlan, plan

SRC = """import java.util.*;

public class A {
    int total;

    void report(List<String> xs) {
        if (xs.isEmpty()) {
            System.out.println("empty");
            return;
        }
        for (String x : xs) {
            System.out.println(x);
        }
    }

    int parse(String text) {
        try {
            return Integer.parseInt(text);
        } catch (NumberFormatException e) {
            throw new IllegalArgumentException("bad: " + text);
        }
    }

    String busiest(List<String> names) {
        String best = "";
        int bestLength = -1;
        for (String name : names) {
            if (name.length() > bestLength) {
                best = name;
                bestLength = name.length();
            }
        }
        return best;
    }

    int counting(List<String> names) {
        int seen = 0;
        for (String name : names) {
            seen++;
            if (name.isEmpty()) {
                total += 1;
            }
        }
        return total;
    }

    void twoLoops(int n) {
        for (int i = 0; i < n; i++) {
            total += i;
        }
        for (int i = 0; i < n; i++) {
            total -= i;
        }
    }
}
"""


def extract(method: str, *paths: str) -> ExtractPlan:
    return plan(JavaProject({"A.java": SRC}), [f"A.java#A.{method}/{p}" for p in paths], None, None)


def applied(result: ExtractPlan) -> str:
    assert result.available, [c.reason for c in result.checks if not c.passed]
    text = apply_text_edits({"A.java": SRC}, result.edits)["A.java"]
    assert not JavaProject({"A.java": text}).syntax_issues()
    return text


def test_a_void_return_at_the_end_of_the_fragment_keeps_the_exit_after_the_call():
    result = extract("report(List<String>)", "if[0]/then/op[0]", "if[0]/then/return[0]")
    text = applied(result)
    # `return вызов();` в методе void не собирается, а выход из метода должен остаться
    assert f"            {result.name}();\n            return;\n" in text
    assert f"private static void {result.name}()" in text


def test_a_fragment_that_always_throws_is_refused_in_a_method_with_a_result():
    result = extract("parse(String)", "try[0]/catch[0]/throw[0]")
    assert not result.available
    assert "броском исключения" in " ".join(c.reason or "" for c in result.checks if not c.passed)


def test_variables_that_live_on_to_the_next_iteration_are_results():
    result = extract("busiest(List<String>)", "foreach[0]/if[0]")
    assert {v.name for v in result.flow.outputs} == {"best", "bestLength"}
    text = applied(result)
    assert "Result" in text  # два результата — вспомогательный record


def test_a_counter_changed_only_inside_the_loop_is_still_a_result():
    result = extract("counting(List<String>)", "foreach[0]/assign[0]")
    assert [v.name for v in result.flow.outputs] == ["seen"]


def test_a_loop_variable_is_not_confused_with_a_later_variable_of_the_same_name():
    result = extract("twoLoops(int)", "for[0]")
    assert result.flow.outputs == []
    assert [v.name for v in result.flow.parameters] == ["n"]
    assert "i = " not in applied(result).split("twoLoops")[1].split("for (int i")[0]
