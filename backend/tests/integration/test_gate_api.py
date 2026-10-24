"""Эталон, сравнение и gate на реальном JDK (S6.2–S6.3, метка `jdk`): «записать эталон → изменить
код → запустить тесты → красный/зелёный» (контрольная точка M1)."""

from __future__ import annotations

import pytest

from tests.integration.conftest import read_sse, wait_run

pytestmark = pytest.mark.jdk

CALC = """\
import java.util.*;

public class Main {
    static int add(int a, int b) {
        return a + b;
    }

    public static void main(String[] args) {
        Scanner in = new Scanner(System.in);
        while (in.hasNextInt()) {
            int x = in.nextInt();
            System.out.println(add(x, 10));
        }
        if (args.length > 0) {
            System.out.println("args=" + String.join(",", args));
        }
    }
}
"""


def add_scenarios(client, project, count: int = 3) -> list[dict]:
    specs = [
        {"name": "one", "stdin": "1\n"},
        {"name": "two", "stdin": "2 3\n"},
        {"name": "empty", "stdin": ""},
        {"name": "args", "stdin": "", "args": ["a", "b"]},
    ][:count]
    return [
        client.post(f"/api/projects/{project['id']}/scenarios", json=spec).json() for spec in specs
    ]


def start(client, project, kind: str, **body) -> dict:
    response = client.post(f"/api/projects/{project['id']}/runs", json={"kind": kind, **body})
    assert response.status_code == 202, response.text
    return wait_run(client, response.json()["run_id"], 120)


def items(info: dict) -> dict[str, dict]:
    return {i["name"]: i for i in info["items"]}


def edit(client, project, content: str, base: int) -> dict:
    response = client.put(
        f"/api/projects/{project['id']}/files/Main.java",
        json={"base_version_id": base, "content": content},
    )
    assert response.status_code == 200, response.text
    return response.json()


def version_gate(client, project, version_id: int) -> str:
    versions = client.get(f"/api/projects/{project['id']}/versions").json()["versions"]
    return next(v["gate_status"] for v in versions if v["id"] == version_id)


def scenario_states(client, project) -> dict[str, dict]:
    listed = client.get(f"/api/projects/{project['id']}/scenarios").json()["scenarios"]
    return {s["name"]: s for s in listed}


@pytest.fixture
def calc(make_project):
    return make_project({"Main.java": CALC}, name="calc")


# --- эталон ----------------------------------------------------------------------------------------------


def test_baseline_records_outputs_for_every_enabled_scenario(client, calc):
    add_scenarios(client, calc, 4)
    info = start(client, calc, "baseline")
    assert info["status"] == "done"
    assert info["version_id"] == calc["original_version_id"]
    assert (info["summary"]["passed"], info["summary"]["total"]) == (4, 4)
    assert all(i["verdict"] == "PASS" for i in info["items"])
    assert items(info)["one"]["stdout"] == "11"
    assert items(info)["args"]["stdout"] == "args=a,b"

    states = scenario_states(client, calc)
    assert {s["baseline"]["state"] for s in states.values()} == {"recorded"}
    assert {s["stability"] for s in states.values()} == {"stable"}
    assert all(s["baseline"]["version_id"] == calc["original_version_id"] for s in states.values())

    detail = client.get(f"/api/projects/{calc['id']}").json()
    assert detail["baseline_version_id"] == calc["original_version_id"]


def test_baseline_without_scenarios_or_with_compile_errors_fails(client, make_project):
    empty = make_project({"Main.java": CALC}, name="no-scenarios")
    info = start(client, empty, "baseline")
    assert info["status"] == "failed"
    assert "сценариев" in info["summary"]["message"]

    broken = make_project({"Main.java": CALC.replace("a + b", "a +")}, name="broken")
    add_scenarios(client, broken, 3)
    info = start(client, broken, "baseline")
    assert info["status"] == "failed"
    assert info["summary"]["data"]["stage"] == "compile"


def test_disabled_scenarios_are_skipped_and_subset_can_be_chosen(client, calc):
    one, two, empty = add_scenarios(client, calc, 3)
    client.put(f"/api/projects/{calc['id']}/scenarios/{empty['id']}", json={"enabled": False})
    info = start(client, calc, "baseline")
    assert {i["name"] for i in info["items"]} == {"one", "two"}

    only_two = start(client, calc, "baseline", scenario_ids=[two["id"]])
    assert [i["name"] for i in only_two["items"]] == ["two"]
    assert one["id"] != two["id"]


def test_unstable_output_is_masked_and_truly_random_output_is_excluded(client, make_project):
    program = """\
public class Main {
    public static void main(String[] args) {
        String mode = args.length > 0 ? args[0] : "";
        if (mode.equals("uuid")) {
            System.out.println("id " + java.util.UUID.randomUUID());
        } else if (mode.equals("chaos")) {
            System.out.println("x".repeat((int) (System.nanoTime() % 100003) + 1));
        } else {
            System.out.println("stable");
        }
    }
}
"""
    project = make_project({"Main.java": program}, name="unstable")
    for name, args in (("plain", []), ("uuid", ["uuid"]), ("chaos", ["chaos"])):
        client.post(f"/api/projects/{project['id']}/scenarios", json={"name": name, "args": args})
    info = start(client, project, "baseline")
    assert info["status"] == "done"
    assert info["summary"]["data"]["stability"] == {
        "plain": "stable",
        "uuid": "masked",
        "chaos": "nondeterministic",
    }
    assert items(info)["chaos"]["verdict"] == "ERROR"
    assert (info["summary"]["passed"], info["summary"]["total"]) == (2, 3)
    assert any("uuid" in w for w in info["summary"]["data"]["warnings"])

    states = scenario_states(client, project)
    assert states["uuid"]["masks"], "маска сохранена в сценарии"
    assert states["chaos"]["baseline"]["state"] == "none"

    tested = start(client, project, "test")  # замаскированный проходит, нестабильный пропущен
    assert tested["summary"]["version_status"] == "unknown"  # сценариев меньше порога
    assert {i["name"]: i["verdict"] for i in tested["items"]} == {"plain": "PASS", "uuid": "PASS"}
    assert any("chaos" in w for w in tested["summary"]["data"]["warnings"])


def test_expected_output_mismatch_is_a_warning_not_an_error(client, make_project):
    project = make_project({"Main.java": CALC}, name="expect")
    client.post(
        f"/api/projects/{project['id']}/scenarios/import",
        json={
            "scenarios": [
                {"name": "ok", "stdin": "1\n", "expected_stdout": "11\n", "expected_exit_code": 0},
                {"name": "bad", "stdin": "1\n", "expected_stdout": "12\n"},
            ]
        },
    )
    info = start(client, project, "baseline")
    assert info["status"] == "done"
    warnings = info["summary"]["data"]["warnings"]
    assert any("bad" in w and "expected_stdout" in w for w in warnings)
    assert not any(w.startswith("ok:") for w in warnings)


# --- тесты ---------------------------------------------------------------------------------------------------


def test_unchanged_version_passes_and_changed_behaviour_is_caught_with_a_diff(client, calc):
    add_scenarios(client, calc, 4)
    start(client, calc, "baseline")

    green = start(client, calc, "test")
    assert green["status"] == "done"
    assert green["summary"]["version_status"] == "passed"
    assert (green["summary"]["passed"], green["summary"]["total"]) == (4, 4)
    assert green["summary"]["message"] == "Все тесты пройдены — 4 из 4"
    assert all(i["baseline_ms"] is not None for i in green["items"])

    harmless = CALC.replace("return a + b;", "int sum = a + b;\n        return sum;")
    change = edit(client, calc, harmless, calc["head_version_id"])
    assert start(client, calc, "test")["summary"]["version_status"] == "passed"

    mutated = CALC.replace("return a + b;", "return a - b;")  # негативный контроль (docs/07 § 10)
    edit(client, calc, mutated, change["version"]["id"])
    red = start(client, calc, "test")
    assert red["summary"]["version_status"] == "failed"
    assert (red["summary"]["passed"], red["summary"]["total"]) == (
        2,
        4,
    )  # args и empty не затронуты
    one = items(red)["one"]
    assert one["verdict"] == "FAIL"
    assert one["diff"]["stdout"]["expected"] == "11"
    assert one["diff"]["stdout"]["actual"] == "-9"
    assert "-11" in one["diff"]["stdout"]["unified"] and "+-9" in one["diff"]["stdout"]["unified"]
    assert one["diff"]["exit_code"] is None
    assert items(red)["empty"]["verdict"] == "PASS"


def test_exception_and_exit_code_changes_are_caught(client, calc):
    add_scenarios(client, calc, 3)
    start(client, calc, "baseline")
    crashing = CALC.replace(
        "return a + b;",
        'if (a == 2) throw new IllegalStateException("boom");\n        return a + b;',
    )
    edit(client, calc, crashing, calc["head_version_id"])
    red = start(client, calc, "test")
    two = items(red)["two"]
    assert two["verdict"] == "FAIL"
    assert two["diff"]["exit_code"] == {"expected": 0, "actual": 1}
    assert two["diff"]["exception"]["actual"] == "java.lang.IllegalStateException: boom"


def test_slower_than_limit_is_a_timeout(client, calc):
    add_scenarios(client, calc, 3)
    start(client, calc, "baseline")
    client.patch(f"/api/projects/{calc['id']}", json={"settings": {"run_timeout_s": 1}})
    hanging = CALC.replace("return a + b;", "while (true) { }")
    edit(client, calc, hanging, calc["head_version_id"])
    red = start(client, calc, "test")
    assert items(red)["one"]["verdict"] == "TIMEOUT"
    assert red["summary"]["version_status"] == "failed"


def test_test_run_needs_a_baseline_and_a_compilable_version(client, calc):
    add_scenarios(client, calc, 3)
    no_baseline = start(client, calc, "test")
    assert no_baseline["status"] == "failed"
    assert "эталон" in no_baseline["summary"]["message"]

    start(client, calc, "baseline")
    edit(client, calc, CALC.replace("a + b", "a +"), calc["head_version_id"])
    broken = start(client, calc, "test")
    assert broken["status"] == "failed"
    assert broken["summary"]["data"]["stage"] == "compile"


def test_editing_a_scenario_requires_a_new_baseline(client, calc):
    one, *_ = add_scenarios(client, calc, 3)
    start(client, calc, "baseline")
    client.put(f"/api/projects/{calc['id']}/scenarios/{one['id']}", json={"stdin": "5\n"})
    tested = start(client, calc, "test")
    assert any("one: нет эталона" in w for w in tested["summary"]["data"]["warnings"])
    assert {i["name"] for i in tested["items"]} == {"two", "empty"}


def test_baseline_of_a_newer_version_makes_old_baselines_stale(client, calc):
    add_scenarios(client, calc, 3)
    start(client, calc, "baseline")
    changed = edit(client, calc, CALC.replace("a + b", "a + b + 0"), calc["head_version_id"])
    accepted = start(client, calc, "baseline", version_id=changed["version"]["id"])
    assert accepted["version_id"] == changed["version"]["id"]
    assert (
        client.get(f"/api/projects/{calc['id']}").json()["baseline_version_id"]
        == changed["version"]["id"]
    )
    states = scenario_states(client, calc)
    assert {s["baseline"]["version_id"] for s in states.values()} == {changed["version"]["id"]}


# --- gate ------------------------------------------------------------------------------------------------------


def gate_stages(client, run_id: int) -> list[tuple[str, str]]:
    events = read_sse(client, run_id)
    return [(e["data"]["stage"], e["data"]["status"]) for e in events if e["event"] == "gate"]


def test_gate_is_green_on_the_original_and_red_after_a_regression(client, calc):
    add_scenarios(client, calc, 4)
    start(client, calc, "baseline")

    green = start(client, calc, "gate")
    assert green["status"] == "done"
    assert green["summary"]["version_status"] == "passed"
    assert gate_stages(client, green["id"]) == [
        ("syntax", "running"),
        ("syntax", "passed"),
        ("compile", "running"),
        ("compile", "passed"),
        ("scenarios", "running"),
        ("scenarios", "passed"),
        ("coverage", "running"),
        ("coverage", "passed"),
    ]
    assert version_gate(client, calc, calc["head_version_id"]) == "passed"
    detail = client.get(f"/api/projects/{calc['id']}").json()
    assert detail["gate"]["status"] == "passed"
    assert detail["gate"]["run_id"] == green["id"]

    regression = edit(client, calc, CALC.replace("a + b", "a * b"), calc["head_version_id"])
    new_head = regression["version"]["id"]
    assert version_gate(client, calc, new_head) == "unknown"  # новая версия ещё не проверена
    red = start(client, calc, "gate")
    assert red["summary"]["version_status"] == "failed"
    assert gate_stages(client, red["id"])[-1] == ("scenarios", "failed")
    assert version_gate(client, calc, new_head) == "failed"
    assert (
        version_gate(client, calc, calc["head_version_id"]) == "passed"
    )  # у оригинала свой статус


def test_gate_stops_at_syntax_and_compile_errors(client, calc):
    add_scenarios(client, calc, 3)
    start(client, calc, "baseline")

    syntax = edit(client, calc, CALC.replace("a + b;", "a + b"), calc["head_version_id"])
    red = start(client, calc, "gate")
    assert red["summary"]["version_status"] == "failed"
    assert gate_stages(client, red["id"]) == [("syntax", "running"), ("syntax", "failed")]
    assert version_gate(client, calc, syntax["version"]["id"]) == "failed"

    compile_error = CALC.replace("return a + b;", 'return "text";')
    fixed = edit(client, calc, compile_error, syntax["version"]["id"])
    red = start(client, calc, "gate")
    assert red["summary"]["version_status"] == "failed"
    assert gate_stages(client, red["id"])[-1] == ("compile", "failed")
    assert version_gate(client, calc, fixed["version"]["id"]) == "failed"


def test_gate_without_enough_scenarios_cannot_claim_success(client, calc):
    add_scenarios(client, calc, 2)  # порог по умолчанию — 3
    start(client, calc, "baseline")
    info = start(client, calc, "gate")
    assert info["summary"]["version_status"] == "unknown"
    assert "недостаточно" in info["summary"]["message"]
    assert version_gate(client, calc, calc["head_version_id"]) == "unknown"

    client.patch(f"/api/projects/{calc['id']}", json={"settings": {"gate_min_scenarios": 2}})
    assert start(client, calc, "gate")["summary"]["version_status"] == "passed"


def test_gate_without_baseline_fails_with_a_hint(client, calc):
    add_scenarios(client, calc, 3)
    info = start(client, calc, "gate")
    assert info["status"] == "failed"
    assert "эталон" in info["summary"]["message"]


def test_events_of_a_test_run_carry_items_progress_and_result(client, calc):
    add_scenarios(client, calc, 3)
    start(client, calc, "baseline")
    info = start(client, calc, "test")
    events = read_sse(client, info["id"])
    kinds = [e["event"] for e in events]
    assert kinds.count("item") == 3
    assert [e["data"] for e in events if e["event"] == "progress"][-1] == {"done": 3, "total": 3}
    result = next(e for e in events if e["event"] == "result")
    assert result["data"]["summary"]["version_status"] == "passed"
    assert kinds[-1] == "done"


@pytest.mark.slow
def test_demo_orders_baseline_and_gate_on_the_full_corpus(client, demo_orders):
    baseline = start(client, demo_orders, "baseline")
    assert baseline["status"] == "done", baseline["summary"]
    assert baseline["summary"]["total"] == 11
    warnings = baseline["summary"]["data"]["warnings"]
    assert not any("expected_stdout" in w for w in warnings), warnings
    assert baseline["summary"]["passed"] == 11

    gate = start(client, demo_orders, "gate")
    assert gate["summary"]["version_status"] == "passed", gate["summary"]
    assert gate["summary"]["passed"] == 11
    assert all(i["verdict"] == "PASS" for i in gate["items"])


WIDE = """\
import java.util.*;

public class Main {
    public static void main(String[] args) {
        Scanner in = new Scanner(System.in);
        String cmd = in.hasNext() ? in.next() : "";
        if (cmd.equals("a")) {
            System.out.println("A");
        } else if (cmd.equals("b")) {
            System.out.println("B");
        } else if (cmd.equals("c")) {
            System.out.println("C");
        } else if (cmd.equals("d")) {
            System.out.println("D");
        } else {
            System.out.println("?");
        }
    }
}
"""


def test_low_coverage_keeps_the_gate_from_claiming_success(client, make_project):
    project = make_project({"Main.java": WIDE}, name="wide")
    for index in range(3):  # три сценария, все ходят одной веткой
        client.post(
            f"/api/projects/{project['id']}/scenarios",
            json={"name": f"same{index}", "stdin": "a\n"},
        )
    assert start(client, project, "baseline")["status"] == "done"

    info = start(client, project, "gate")
    assert info["summary"]["version_status"] == "unknown", info["summary"]
    assert info["summary"]["coverage"] < 0.6
    assert "покрытие" in info["summary"]["message"] and "ниже порога" in info["summary"]["message"]
    assert gate_stages(client, info["id"])[-2:] == [("coverage", "running"), ("coverage", "failed")]
    assert version_gate(client, project, project["head_version_id"]) == "unknown"

    client.patch(f"/api/projects/{project['id']}", json={"settings": {"gate_min_coverage": 0.2}})
    relaxed = start(client, project, "gate")
    assert relaxed["summary"]["version_status"] == "passed"
    assert gate_stages(client, relaxed["id"])[-1] == ("coverage", "passed")
    assert relaxed["summary"]["data"]["coverage_min"] == 0.2


def test_red_scenarios_skip_the_coverage_stage(client, calc):
    add_scenarios(client, calc, 4)
    start(client, calc, "baseline")
    edit(client, calc, CALC.replace("a + b", "a * b"), calc["head_version_id"])
    red = start(client, calc, "gate")
    assert red["summary"]["version_status"] == "failed"
    assert all(stage != "coverage" for stage, _ in gate_stages(client, red["id"]))
