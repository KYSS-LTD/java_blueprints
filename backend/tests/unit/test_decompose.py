"""Декомпозиция монолита (S9.5–S9.8): план детерминирован, генерация даёт валидный код, отказы объяснены."""

from __future__ import annotations

import copy
from pathlib import Path

import pytest

from engine.java.project import JavaProject
from engine.transform.decompose.generate import DecomposeError, generate, validate
from engine.transform.decompose.plan import Plan, build_plan, monolith, pinned

SAMPLES = Path(__file__).resolve().parents[3] / "samples"

SRC = """import java.util.*;

public class Main {
    private final List<String> lines = new ArrayList<>();
    private int total;
    private int errors;

    public static void main(String[] args) {
        Main app = new Main();
        app.parseInput("1 2 x 4");
        app.sumUp();
        app.printReport();
    }

    void parseInput(String text) {
        for (String token : text.split(" ")) {
            if (isNumber(token)) {
                lines.add(token);
            } else {
                errors++;
            }
        }
    }

    boolean isNumber(String token) {
        for (char c : token.toCharArray()) {
            if (!Character.isDigit(c)) {
                return false;
            }
        }
        return !token.isEmpty();
    }

    void parseExtra(String text) {
        parseInput(text);
        lines.add("0");
    }

    void sumUp() {
        total = 0;
        for (String line : lines) {
            total += Integer.parseInt(line);
        }
    }

    int maxValue() {
        int best = Integer.MIN_VALUE;
        for (String line : lines) {
            best = Math.max(best, Integer.parseInt(line));
        }
        return best;
    }

    double average() {
        return lines.isEmpty() ? 0 : (double) total / lines.size();
    }

    void printReport() {
        System.out.println("total=" + total);
        printLine("max=" + maxValue());
        printLine("avg=" + average());
        printLine("errors=" + errors);
    }

    void printLine(String text) {
        System.out.println(text);
    }

    void printHeader() {
        printLine("== report ==");
    }
}
"""
CORE = "Main.java#Main"

NESTED = SRC.replace(
    "    void printHeader() {",
    """    enum Kind { SMALL, BIG }

    record Pair(int a, int b) {}

    class Counter {
        void inc() { total++; }
    }

    Kind kindOf(int v) {
        return v > 5 ? Kind.BIG : Kind.SMALL;
    }

    void showPair() {
        Pair p = new Pair(total, errors);
        printLine(kindOf(p.a()) + ":" + p.b());
    }

    void bump() {
        Counter c = new Counter();
        c.inc();
        printLine("bumped " + total);
    }

    void printHeader() {""",
)
INHERITED = (
    SRC.replace("public class Main {", "public class Main extends Base {").replace(
        'printLine("== report ==");',
        'printLine("== report ==" + baseValue);\n        hello();',
    )
    + """
class Base {
    protected int baseValue = 5;

    void hello() {
        System.out.println("hi");
    }
}
"""
)


def project() -> JavaProject:
    return JavaProject({"Main.java": SRC})


def plan_for(**options: int) -> Plan:
    return build_plan(project(), None, **options)


def moved(plan: Plan) -> list:
    return [c for c in plan.classes if c.role not in {"core", "model"}]


def snapshot(plan: Plan) -> list[tuple[str, str, tuple[str, ...]]]:
    return [(c.name, c.role, tuple(c.members)) for c in plan.classes]


# --- план -----------------------------------------------------------------------------------------------------


def test_plan_is_deterministic_and_seed_changes_nothing_structural_for_a_clear_split():
    assert snapshot(plan_for()) == snapshot(plan_for())
    assert snapshot(plan_for(seed=3)) == snapshot(plan_for(seed=3))


def test_every_member_lives_in_exactly_one_class_and_the_core_is_main():
    plan = plan_for()
    assert plan.core().name == "Main" and plan.core().file == "Main.java"
    members = [m for c in plan.classes for m in c.members]
    assert len(members) == len(set(members))
    assert f"{CORE}.main(String[])" in plan.core().members
    assert all(c.file == f"{c.name}.java" for c in moved(plan))
    assert plan.after.lines < plan.before.lines


def test_methods_calling_each_other_end_up_together():
    plan = plan_for()
    where = {m: c.name for c in plan.classes for m in c.members}
    parse = f"{CORE}.parseInput(String)"
    assert where[parse] == where[f"{CORE}.isNumber(String)"] == where[f"{CORE}.parseExtra(String)"]
    assert where[f"{CORE}.printReport()"] == where[f"{CORE}.printLine(String)"]


def test_options_limit_the_number_of_new_classes():
    assert len(moved(plan_for(max_classes=2))) <= 2
    assert moved(plan_for(max_classes=1))


def test_unknown_type_is_a_lookup_error():
    with pytest.raises(LookupError):
        build_plan(project(), "Main.java#Nope")


def test_samples_get_a_plan_without_warnings_about_cycles_to_the_core():
    root = SAMPLES / "monoliths" / "inventory"
    files = {
        p.relative_to(root).as_posix(): p.read_text(encoding="utf-8") for p in root.rglob("*.java")
    }
    plan = build_plan(JavaProject(files))
    assert len(moved(plan)) >= 3
    assert snapshot(plan) == snapshot(build_plan(JavaProject(files)))


# --- генерация ------------------------------------------------------------------------------------------------


def test_generation_creates_one_file_per_class_and_keeps_syntax_valid():
    plan = plan_for()
    result = generate(project(), plan)
    assert set(result.files) == {"Main.java", *[c.file for c in moved(plan)]}
    assert result.changed and "Main.java" in result.changed
    assert not JavaProject(result.files).syntax_issues()
    assert set(result.moves) == {m for c in moved(plan) for m in c.members}
    assert all(result.moves[m] == c.name for c in moved(plan) for m in c.members)


def test_moved_methods_reach_core_state_through_a_back_reference():
    result = generate(project(), plan_for())
    text = "\n".join(result.files.values())
    assert "core.lines" in text and "core.errors" in text
    assert "Main core" in text
    main = result.files["Main.java"]
    assert "final List<String> lines = new ArrayList<>();" in main  # поля остаются в ядре
    assert "private final List<String> lines" not in main  # и доступны из новых классов
    assert len(main.splitlines()) < len(SRC.splitlines())


def test_generation_is_repeatable():
    assert generate(project(), plan_for()).files == generate(project(), plan_for()).files


# --- отказы ---------------------------------------------------------------------------------------------------


def refuse(mutate) -> str:
    plan = plan_for()
    broken = copy.deepcopy(plan)
    mutate(broken)
    with pytest.raises(DecomposeError) as error:
        validate(project(), broken)
    return error.value.message


def test_valid_plan_passes_validation():
    assert validate(project(), plan_for()).name == "Main"


def test_taken_and_invalid_names_are_refused():
    assert "занято" in refuse(lambda p: setattr(moved(p)[0], "name", "Main"))
    assert "имя класса" in refuse(lambda p: setattr(moved(p)[0], "name", "1bad"))


def test_a_member_in_two_classes_or_lost_is_refused():
    def twice(plan: Plan) -> None:
        moved(plan)[0].members.append(moved(plan)[1].members[0])

    def lost(plan: Plan) -> None:
        moved(plan)[0].members.pop()

    assert "в двух классах" in refuse(twice)
    assert "не хватает" in refuse(lost)


def test_pinned_members_cannot_be_moved():
    def steal(plan: Plan) -> None:
        main = f"{CORE}.main(String[])"
        plan.core().members.remove(main)
        moved(plan)[0].members.append(main)

    assert "нельзя переносить" in refuse(steal)


def test_a_plan_needs_exactly_one_core_and_no_empty_classes():
    assert "ядро" in refuse(lambda p: p.classes.remove(p.core()))

    def empty(plan: Plan) -> None:
        moved(plan)[0].members.clear()

    message = refuse(empty)
    assert "без методов" in message or "не хватает" in message


# --- вложенные типы и наследование (S9.8) -------------------------------------------------------------------------------


def where_is(plan: Plan, method: str) -> str:
    return next(c.name for c in plan.classes if any(m.endswith(f".{method}") for m in c.members))


def test_methods_using_inner_classes_stay_in_the_core():
    project = JavaProject({"Main.java": NESTED})
    plan = build_plan(project)
    assert where_is(plan, "bump()") == "Main"
    pins = pinned(project, monolith(project))
    assert any("внутренний класс Counter" in reason for reason in pins.values())


def test_static_nested_types_are_written_with_the_core_name_in_new_classes():
    project = JavaProject({"Main.java": NESTED})
    plan = build_plan(project)
    generated = generate(project, plan)
    moved = [c for c in plan.classes if c.role not in {"core", "model"}]
    text = "\n".join(generated.files[c.file] for c in moved)
    assert "Main.Kind" in text or "Main.Pair" in text
    assert "Kind.BIG" not in text.replace("Main.Kind.BIG", "")
    assert not JavaProject(generated.files).syntax_issues()


def test_methods_using_inherited_members_stay_in_the_core():
    project = JavaProject({"Main.java": INHERITED})
    pins = pinned(project, monolith(project))
    reasons = " ".join(pins.values())
    assert "унаследованный метод hello()" in reasons or "унаследованное поле baseValue" in reasons
