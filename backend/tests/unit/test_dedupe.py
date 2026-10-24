"""Клоны → общий метод (S9.3, REF-02): параметры, результаты, отказы, окна короче найденных."""

from __future__ import annotations

from pathlib import Path

import pytest

from engine.analysis.analyzer import metrics_result
from engine.edit.core import apply_text_edits
from engine.java.project import JavaProject
from engine.transform.base import Refusal
from engine.transform.dedupe import DedupePlan, build, clone_places

SAMPLES = Path(__file__).resolve().parents[3] / "samples"

LITERALS = """\
public class A {
    void reportMin(int[] data) {
        int best = data[0];
        for (int v : data) {
            if (v < best) {
                best = v;
            }
        }
        System.out.println("min=" + best);
        System.out.println("count=" + data.length);
    }

    void reportMax(int[] values) {
        int best = values[0];
        for (int v : values) {
            if (v < best) {
                best = v;
            }
        }
        System.out.println("max=" + best);
        System.out.println("count=" + values.length);
    }
}
"""

DECLARED_RESULT = """\
public class A {
    void first(int[] xs) {
        int sum = 0;
        int hits = 0;
        for (int x : xs) {
            if (x > 10) {
                sum += x;
                hits++;
            }
        }
        System.out.println(sum + 1);
    }

    void second(int[] ys) {
        int sum = 0;
        int hits = 0;
        for (int y : ys) {
            if (y > 10) {
                sum += y;
                hits++;
            }
        }
        show(sum);
    }

    void show(int value) {
        System.out.println(value);
    }
}
"""

EXTERNAL_RESULT = """\
public class A {
    int first(int[] xs) {
        int total = 5;
        total = total + 1;
        for (int x : xs) {
            if (x > 3) {
                total += x;
                total -= 1;
                total *= 2;
            }
        }
        System.out.println(total);
        return total;
    }

    int second(int[] ys) {
        int sum = seed();
        sum = sum + 1;
        for (int y : ys) {
            if (y > 3) {
                sum += y;
                sum -= 1;
                sum *= 2;
            }
        }
        show(sum);
        return sum;
    }

    int seed() {
        return 7;
    }

    void show(int value) {
        System.out.println(value);
    }
}
"""

TAIL = """\
public class A {
    int first(int[] xs) {
        int count = 0;
        for (int x : xs) {
            if (x % 2 == 0) {
                count++;
            }
        }
        return count;
    }

    int second(int[] ys) {
        int count = 0;
        for (int y : ys) {
            if (y % 3 == 0) {
                count++;
            }
        }
        return count;
    }

    int third(int[] zs) {
        int count = 0;
        for (int z : zs) {
            if (z % 5 == 0) {
                count++;
            }
        }
        return count;
    }
}
"""

OTHER_METHODS = """\
public class A {
    int first(int[] xs) {
        int best = xs[0];
        for (int x : xs) {
            if (x > 0) {
                best = Math.max(best, x);
            }
        }
        return best;
    }

    int second(int[] ys) {
        int best = ys[0];
        for (int y : ys) {
            if (y > 0) {
                best = Math.min(best, y);
            }
        }
        return best;
    }
}
"""

TRIMMED = """\
public class A {
    void first(int[] xs, String label) {
        String tag = label.trim();
        int n = 0;
        for (int x : xs) {
            if (x > 4) {
                n++;
                System.out.println(x);
            }
        }
        System.out.println(tag + n);
    }

    void second(int[] ys, String label) {
        String tag = label.toUpperCase();
        int n = 0;
        for (int y : ys) {
            if (y > 4) {
                n++;
                System.out.println(y);
            }
        }
        System.out.println(tag + n);
    }
}
"""

MANY_DIFFERENCES = """\npublic class A {
    void first() {
        System.out.println("a1");
        log("a2");
        System.out.println("a3");
        log("a4");
        System.out.println("a5");
        log("a6");
    }

    void second() {
        System.out.println("b1");
        log("b2");
        System.out.println("b3");
        log("b4");
        System.out.println("b5");
        log("b6");
    }

    void log(String text) {
        System.out.println(text);
    }
}
"""

TWO_CLASSES = """\
class First {
    void run(int[] xs) {
        int best = xs[0];
        for (int x : xs) {
            if (x < best) {
                best = x;
            }
        }
        System.out.println(best + 1);
    }
}

class Second {
    void run(int[] xs) {
        int best = xs[0];
        for (int x : xs) {
            if (x < best) {
                best = x;
            }
        }
        System.out.println(best + 1);
    }
}
"""


def clone_plan(
    source: str, index: int = 0, name: str | None = None
) -> tuple[JavaProject, DedupePlan]:
    project = JavaProject({"A.java": source})
    clones = metrics_result(project).clones
    assert clones, "клоны не найдены"
    first = clones[index].first
    places = clone_places(project, first.file, first.start_line, first.end_line)
    return project, build(project, places, name)


def refusal(source: str, index: int = 0) -> str:
    with pytest.raises(Refusal) as error:
        clone_plan(source, index)
    return error.value.check.reason or ""


def applied(project: JavaProject, plan: DedupePlan) -> str:
    updated = apply_text_edits(project.files, plan.edits)
    assert not JavaProject(updated).syntax_issues()
    return updated["A.java"]


# --- параметры -------------------------------------------------------------------------------------------------


def test_variables_and_differing_literals_become_parameters():
    project, plan = clone_plan(LITERALS, name="reportStats")
    assert plan.name == "reportStats"
    assert [(p.type_text, p.name, p.values) for p in plan.params] == [
        ("int[]", "data", ["data", "values"]),
        ("String", "text", ['"min="', '"max="']),
    ]
    text = applied(project, plan)
    assert '        reportStats(data, "min=");' in text
    assert '        reportStats(values, "max=");' in text
    assert "    private static void reportStats(int[] data, String text) {" in text
    assert "        System.out.println(text + best);" in text
    assert '        System.out.println("count=" + data.length);' in text


VARARGS = """public class A {
    int countShort(String... names) {
        int total = 0;
        for (String s : names) {
            if (s.length() > 3) {
                total += s.length();
            }
        }
        System.out.println("short=" + total);
        System.out.println("n=" + names.length);
        return total;
    }

    int countLong(String... items) {
        int total = 0;
        for (String s : items) {
            if (s.length() > 3) {
                total += s.length();
            }
        }
        System.out.println("long=" + total);
        System.out.println("n=" + items.length);
        return total;
    }
}
"""


def test_a_varargs_parameter_keeps_its_element_type():
    project, plan = clone_plan(VARARGS, name="countAll")
    assert [(p.type_text, p.name) for p in plan.params][0] == ("String...", "names")
    text = applied(project, plan)
    assert "countAll(String... names" in text


def test_the_same_literal_stays_in_the_body_and_the_name_is_unique():
    project, plan = clone_plan(LITERALS)
    assert plan.name not in {"reportMin", "reportMax"}
    assert all(p.name != "count" for p in plan.params)
    assert len(plan.occurrences) == 2 and {p.method_id for p in plan.occurrences} == {
        "A.java#A.reportMin(int[])",
        "A.java#A.reportMax(int[])",
    }


def test_all_occurrences_of_a_group_are_replaced():
    project, plan = clone_plan(TAIL)
    assert len(plan.occurrences) == 3
    text = applied(project, plan)
    assert text.count(plan.name + "(") == 4  # три вызова и объявление
    assert text.count("count++") == 1


# --- результаты ------------------------------------------------------------------------------------------------


def test_a_variable_declared_inside_and_used_after_is_returned():
    project, plan = clone_plan(DECLARED_RESULT)
    assert plan.signature.startswith("int ")
    text = applied(project, plan)
    assert f"        int sum = {plan.name}(xs);" in text
    assert f"        int sum = {plan.name}(ys);" in text
    assert "        return sum;\n    }" in text


def test_an_outer_variable_that_changes_is_passed_in_and_assigned_back():
    project, plan = clone_plan(EXTERNAL_RESULT)
    text = applied(project, plan)
    assert plan.signature.startswith("int ") and "int total" in plan.signature
    assert f"        total = {plan.name}(total, xs);" in text
    assert f"        sum = {plan.name}(sum, ys);" in text
    assert "        return total;\n    }" in text


def test_a_tail_return_makes_the_call_a_return():
    project, plan = clone_plan(TAIL)
    text = applied(project, plan)
    assert f"        return {plan.name}(xs, 2);" in text
    assert f"        return {plan.name}(zs, 5);" in text
    assert plan.signature.startswith("int ")


# --- отказы и окна -----------------------------------------------------------------------------------------------


def test_differing_method_names_cannot_be_parameters():
    reason = refusal(OTHER_METHODS)
    assert "max" in reason and "min" in reason


def test_too_many_differences_are_refused():
    assert "не больше 4" in refusal(MANY_DIFFERENCES)


def test_occurrences_in_different_classes_are_not_merged():
    assert "другом классе" in refusal(TWO_CLASSES)


def test_a_shorter_window_is_used_when_the_whole_one_differs_at_the_edge():
    project, plan = clone_plan(TRIMMED)
    first, second = plan.occurrences
    assert first.size < 4 and second.size == first.size
    text = applied(project, plan)
    assert "String tag = label.trim();" in text and "String tag = label.toUpperCase();" in text


# --- свойства ------------------------------------------------------------------------------------------------------


def test_the_plan_is_deterministic():
    _, first = clone_plan(LITERALS)
    _, second = clone_plan(LITERALS)
    assert first.code == second.code and first.edits == second.edits


def test_edits_never_overlap_and_a_name_can_be_chosen():
    project, plan = clone_plan(TAIL, name="countEvery")
    assert plan.name == "countEvery"
    ordered = sorted(plan.edits, key=lambda e: (e.start, e.end))
    assert all(a.end <= b.start for a, b in zip(ordered, ordered[1:], strict=False))


def test_every_clone_in_the_corpus_gives_a_plan_or_a_reason():
    found = 0
    for directory in ("demo-orders", "monoliths/inventory", "monoliths/graph-paths"):
        root = SAMPLES / directory
        files = {
            p.relative_to(root).as_posix(): p.read_text(encoding="utf-8")
            for p in root.rglob("*.java")
        }
        project = JavaProject(files)
        for clone in metrics_result(project).clones:
            places = clone_places(
                project, clone.first.file, clone.first.start_line, clone.first.end_line
            )
            try:
                plan = build(project, places)
            except Refusal as refused:
                assert refused.check.reason
                continue
            found += 1
            updated = apply_text_edits(project.files, plan.edits)
            assert not JavaProject(updated).syntax_issues()
    assert found >= 4
