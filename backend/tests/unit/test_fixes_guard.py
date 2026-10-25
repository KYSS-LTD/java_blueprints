"""REF-03 (ранний возврат вместо вложенности): условие в конце цикла или метода void → защитное условие."""

from __future__ import annotations

import pytest

from engine.analysis.analyzer import analyze
from engine.java.project import JavaProject
from engine.transform.base import apply_proposal
from engine.transform.fixes import propose

DEEP = """\
import java.util.*;

class A {
    int n;

    void run(List<Integer> xs) {
        for (int x : xs) {
            if (x > 0) {
                for (int y : xs) {
                    if (y > x) {
                        n++;
                    }
                }
            }
        }
    }
}
"""


def finding(project: JavaProject):
    found = [f for f in analyze(project).findings if f.rule_id == "MAINT-03"]
    assert found, "вложенность не найдена"
    return found[0]


def fixed(source: str) -> str:
    project = JavaProject({"A.java": source})
    proposal = propose(project, finding(project))
    assert proposal is not None and proposal.available and proposal.behavior_preserving
    after = apply_proposal(project, proposal)["A.java"]
    assert not JavaProject({"A.java": after}).syntax_issues()
    return after


def refusal(source: str) -> str:
    project = JavaProject({"A.java": source})
    proposal = propose(project, finding(project))
    assert proposal is not None and not proposal.available
    return " ".join(c.reason or "" for c in proposal.failed())


def test_a_condition_at_the_end_of_a_loop_becomes_a_guard_with_continue():
    after = fixed(DEEP)
    assert (
        "            if (x <= 0) {\n                continue;\n            }\n"
        "            for (int y : xs) {\n"
    ) in after
    # вложенность стала на единицу меньше, замечание исчезло
    assert not [
        f for f in analyze(JavaProject({"A.java": after})).findings if f.rule_id == "MAINT-03"
    ]


def test_both_branches_are_kept_and_the_shallower_one_becomes_the_guard():
    source = DEEP.replace(
        "                        n++;\n                    }\n                }\n            }\n",
        "                        n++;\n                    }\n                }\n            } else {\n                n--;\n            }\n",
    )
    after = fixed(source)
    assert (
        "            if (x <= 0) {\n                n--;\n                continue;\n            }\n"
        in after
    )
    assert "            for (int y : xs) {" in after


def test_the_deeper_branch_stays_when_the_else_is_the_big_one():
    source = """\
import java.util.*;

class A {
    int n;

    void run(List<Integer> xs) {
        for (int x : xs) {
            if (x > 0) {
                n--;
            } else {
                for (int y : xs) {
                    for (int z : xs) {
                        if (z > y) {
                            n++;
                        }
                    }
                }
            }
        }
    }
}
"""
    after = fixed(source)
    # условие не меняется, выход идёт после короткой ветки
    assert (
        "            if (x > 0) {\n                n--;\n                continue;\n            }\n"
        in after
    )
    assert "            for (int y : xs) {\n                for (int z : xs) {" in after


def test_a_branch_that_already_ends_abruptly_gets_no_second_exit():
    source = DEEP.replace(
        "                        n++;\n                    }\n                }\n            }\n",
        "                        n++;\n                    }\n                }\n            } else {\n                n--;\n                continue;\n            }\n",
    )
    after = fixed(source)
    assert "n--;\n                continue;\n            }\n" in after
    assert after.count("continue;") == 1


def test_a_void_method_tail_uses_return():
    source = """\
class A {
    int n;

    void run(int a, int b) {
        n = a;
        if (a > 0) {
            if (b > 0) {
                while (n < 10) {
                    if (n % 2 == 0) {
                        n += 3;
                    } else {
                        n++;
                    }
                }
            }
        }
    }
}
"""
    after = fixed(source)
    assert "        if (a <= 0) {\n            return;\n        }\n" in after


def test_a_method_that_returns_a_value_without_loops_is_refused():
    source = """class A {
    int run(int a, int b) {
        if (a > 0) {
            if (b > 0) {
                if (a > b) {
                    if (a > 5) {
                        return 1;
                    }
                }
            }
        }
        return a;
    }
}
"""
    assert "конце" in refusal(source)


def test_an_else_if_chain_is_refused():
    source = DEEP.replace(
        "                        n++;\n                    }\n                }\n            }\n",
        "                        n++;\n                    }\n                }\n            } else if (x < -5) {\n                n--;\n            }\n",
    )
    assert "else if" in refusal(source)


@pytest.mark.parametrize(
    ("condition", "negated"),
    [
        ("flag", "!flag"),
        ("!flag", "flag"),
        ("a == b", "a != b"),
        ("a != b", "a == b"),
        ("a < b", "a >= b"),
        ("a >= b", "a < b"),
        ("flag && a > b", "!(flag && a > b)"),
        ("x.isEmpty()", "!x.isEmpty()"),
    ],
)
def test_conditions_are_negated_without_changing_meaning(condition, negated):
    source = f"""\
import java.util.*;

class A {{
    int n;

    void run(List<Integer> xs, boolean flag, int a, int b, List<Integer> x) {{
        for (int v : xs) {{
            if ({condition}) {{
                for (int y : xs) {{
                    for (int z : xs) {{
                        if (z > y) {{
                            n++;
                        }}
                    }}
                }}
            }}
        }}
    }}
}}
"""
    assert f"            if ({negated}) {{\n                continue;" in fixed(source)


def test_a_double_comparison_is_wrapped_not_flipped_because_of_nan():
    source = DEEP.replace("int n;", "int n;\n    double limit;").replace("x > 0", "limit > 0.5")
    after = fixed(source)
    assert "if (!(limit > 0.5)) {" in after


def test_a_comment_on_the_line_of_the_opening_brace_stays_a_comment():
    source = DEEP.replace(
        "            if (x > 0) {\n",
        "            if (x > 0) {\n",
    ).replace(
        "                        n++;\n                    }\n                }\n            }\n",
        "                        n++;\n                    }\n                }\n            } else { // нечётные пропускаем\n"
        "                // отдельная строка\n                n--;\n            }\n",
    )
    after = fixed(source)
    assert (
        "            if (x <= 0) {\n                // нечётные пропускаем\n                // отдельная строка\n"
        "                n--;\n                continue;\n            }\n"
    ) in after
