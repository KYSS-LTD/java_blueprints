"""Автоисправления через API (S8): предпросмотр, кандидат → gate → HEAD, accept/reject, пакетная
оптимизация. Нужен JDK (gate запускает программу)."""

from __future__ import annotations

from urllib.parse import quote

import pytest

from tests.integration.conftest import read_sse, wait_run

pytestmark = pytest.mark.jdk

PROGRAM = """\
import java.util.*;

public class Main {
    public static void main(String[] args) {
        Scanner in = new Scanner(System.in);
        List<String> names = new ArrayList<>();
        while (in.hasNext()) {
            names.add(in.next());
        }
        List<String> seen = new ArrayList<>();
        String csv = "";
        for (String name : names) {
            if (!seen.contains(name)) {
                seen.add(name);
                csv += name + ";";
            }
        }
        System.out.println(csv);
        System.out.println(seen.size());
        if (args.length > 0 && args[0] == "x") {
            System.out.println("same");
        }
    }
}
"""


def url(project, suffix: str) -> str:
    return f"/api/projects/{project['id']}{suffix}"


def run_kind(client, project, kind: str, **body) -> dict:
    response = client.post(url(project, "/runs"), json={"kind": kind, **body})
    assert response.status_code == 202, response.text
    return wait_run(client, response.json()["run_id"], 120)


@pytest.fixture
def shop(client, make_project):
    project = make_project({"Main.java": PROGRAM}, name="fixes")
    for name, stdin, args in (
        ("dups", "b a b c a\n", []),
        ("empty", "", []),
        ("same-arg", "z z\n", ["x"]),
    ):
        client.post(url(project, "/scenarios"), json={"name": name, "stdin": stdin, "args": args})
    assert run_kind(client, project, "baseline")["status"] == "done"
    return project


def fixes(client, project) -> dict[str, dict]:
    listed = client.get(url(project, "/fixes")).json()["fixes"]
    return {f["fix_id"]: f for f in listed}


def head(client, project) -> dict:
    versions = client.get(url(project, "/versions")).json()
    return next(v for v in versions["versions"] if v["id"] == versions["head_version_id"])


def stdout_of(client, project, stdin: str, args: list[str] | None = None) -> str:
    return run_kind(client, project, "run", stdin=stdin, args=args or [])["items"][0]["stdout"]


def test_fixes_list_shows_availability_and_behaviour_flag(client, shop):
    listed = fixes(client, shop)
    assert set(listed) == {"OPT-02", "OPT-03", "FIX-01"}
    assert all(f["available"] for f in listed.values())
    assert (listed["OPT-03"]["behavior_preserving"], listed["FIX-01"]["behavior_preserving"]) == (
        True,
        False,
    )
    findings = client.get(url(shop, "/analysis")).json()
    assert findings["summary"]["fixable"] == 3


def test_preview_shows_diff_effect_and_preconditions_without_saving(client, shop):
    finding = listed_id(client, shop, "OPT-03")
    response = client.post(url(shop, f"/fixes/{quote(finding, safe=chr(47))}/preview"), json={})
    assert response.status_code == 200, response.text
    body = response.json()
    assert body["fix_id"] == "OPT-03" and body["behavior_preserving"] is True
    # в цикле остаётся ещё и contains по списку (OPT-02): класс сложности цикла пока не меняется
    assert body["effect"]["complexity_before"] == "O(n²)"
    assert body["effect"]["complexity_after"] == "O(n²)"
    assert all(p["passed"] for p in body["preconditions"])
    [diff] = body["diff"]
    assert "+        StringBuilder csvBuilder = new StringBuilder(csv);" in diff["unified"]
    assert len(client.get(url(shop, "/versions")).json()["versions"]) == 1


def listed_id(client, project, fix_id: str) -> str:
    return fixes(client, project)[fix_id]["finding_id"]


def test_unavailable_fix_is_a_precondition_failure_with_reasons(client, shop):
    findings = client.get(url(shop, "/analysis")).json()["findings"]
    # PERF-01 в программе нет; провоцируем отказ, изменив код так, чтобы исправление стало небезопасным
    unsafe = PROGRAM.replace(
        'csv += name + ";";', 'csv += name + ";"; if (csv.length() > 99) { n(); }'
    ).replace("public static void main", "static void n() { }\n    public static void main")
    client.put(
        url(shop, "/files/Main.java"),
        json={"base_version_id": head(client, shop)["id"], "content": unsafe},
    )
    finding = next(
        f
        for f in client.get(url(shop, "/analysis")).json()["findings"]
        if f["rule_id"] == "PERF-03"
    )
    assert findings and finding["fix"]["available"] is False
    response = client.post(
        url(shop, f"/fixes/{quote(finding['id'], safe=chr(47))}/preview"), json={}
    )
    assert response.status_code == 422
    error = response.json()["error"]
    assert error["code"] == "PRECONDITION_FAILED"
    assert any(not p["passed"] and p["reason"] for p in error["details"]["preconditions"])


def test_apply_creates_a_candidate_and_green_gate_makes_it_head(client, shop):
    original = head(client, shop)
    expected = stdout_of(client, shop, "b a b c a\n")
    finding = listed_id(client, shop, "OPT-03")
    response = client.post(
        url(shop, f"/fixes/{quote(finding, safe=chr(47))}/apply"),
        json={"base_version_id": original["id"]},
    )
    assert response.status_code == 202, response.text
    candidate_id, run_id = response.json()["candidate_version_id"], response.json()["run_id"]
    assert candidate_id != original["id"]

    info = wait_run(client, run_id, 120)
    assert info["status"] == "done" and info["summary"]["version_status"] == "passed"
    assert info["summary"]["data"]["candidate"] == "promoted"
    new_head = head(client, shop)
    assert new_head["id"] == candidate_id and new_head["status"] == "active"
    assert (new_head["origin"], new_head["parent_id"]) == ("optimize", original["id"])
    assert new_head["description"] == "OPT-03: Накопление строки через StringBuilder"
    assert new_head["gate_status"] == "passed"
    assert stdout_of(client, shop, "b a b c a\n") == expected  # поведение сохранено
    assert "OPT-03" not in fixes(client, shop)  # находка исправлена


def test_stale_base_version_is_409(client, shop):
    finding = listed_id(client, shop, "OPT-03")
    response = client.post(
        url(shop, f"/fixes/{quote(finding, safe=chr(47))}/apply"), json={"base_version_id": 999}
    )
    assert response.status_code == 409
    assert response.json()["error"]["code"] == "VERSION_CONFLICT"


def test_behaviour_changing_fix_stays_a_candidate_until_accepted(client, shop):
    original = head(client, shop)
    finding = listed_id(client, shop, "FIX-01")
    response = client.post(
        url(shop, f"/fixes/{quote(finding, safe=chr(47))}/apply"),
        json={"base_version_id": original["id"]},
    )
    info = wait_run(client, response.json()["run_id"], 120)
    assert (
        info["summary"]["version_status"] == "failed"
    )  # сценарий «same-arg» теперь печатает «same»
    failed = next(i for i in info["items"] if i["verdict"] == "FAIL")
    assert failed["name"] == "same-arg" and "same" in failed["diff"]["stdout"]["actual"]
    assert info["summary"]["data"]["candidate"] == "candidate"
    assert head(client, shop)["id"] == original["id"]  # HEAD прежний

    candidate_id = response.json()["candidate_version_id"]
    accepted = client.post(url(shop, f"/versions/{candidate_id}/accept"))
    assert accepted.status_code == 200, accepted.text
    assert accepted.json()["head_version_id"] == candidate_id
    assert head(client, shop)["id"] == candidate_id
    assert (
        client.post(url(shop, f"/versions/{candidate_id}/accept")).status_code == 422
    )  # уже не кандидат


def test_reject_leaves_head_and_blocks_further_decisions(client, shop):
    original = head(client, shop)
    finding = listed_id(client, shop, "FIX-01")
    response = client.post(
        url(shop, f"/fixes/{quote(finding, safe=chr(47))}/apply"),
        json={"base_version_id": original["id"]},
    )
    wait_run(client, response.json()["run_id"], 120)
    candidate_id = response.json()["candidate_version_id"]
    rejected = client.post(url(shop, f"/versions/{candidate_id}/reject"))
    assert rejected.status_code == 200 and rejected.json()["version"]["status"] == "rejected"
    assert head(client, shop)["id"] == original["id"]
    assert client.post(url(shop, f"/versions/{candidate_id}/accept")).status_code == 422
    assert client.post(url(shop, "/versions/999/reject")).status_code == 404


def test_optimize_plan_lists_only_behaviour_preserving_steps(client, shop):
    plan = client.post(url(shop, "/optimize/plan"), json={}).json()
    assert {s["fix_id"] for s in plan["steps"]} == {"OPT-02", "OPT-03"}
    assert (
        plan["effect"]["complexity_before"] == "O(n²)"
        and plan["effect"]["complexity_after"] == "O(n)"
    )
    assert all(s["behavior_preserving"] for s in plan["steps"])


def test_optimize_applies_steps_in_order_and_every_head_passed_the_gate(client, shop):
    expected = {stdin: stdout_of(client, shop, stdin) for stdin in ("b a b c a\n", "")}
    plan = client.post(url(shop, "/optimize/plan"), json={}).json()
    ids = [s["step_id"] for s in plan["steps"]]
    response = client.post(
        url(shop, "/optimize/apply"),
        json={"base_version_id": head(client, shop)["id"], "step_ids": ids},
    )
    assert response.status_code == 202, response.text
    run_id = response.json()["run_id"]
    info = wait_run(client, run_id, 240)
    assert info["status"] == "done"
    assert info["summary"]["passed"] == len(ids), info["summary"]
    assert info["summary"]["data"]["skipped"] == {}

    events = read_sse(client, run_id)
    steps = [(e["data"]["step_id"], e["data"]["status"]) for e in events if e["event"] == "step"]
    assert steps == [(i, s) for i in ids for s in ("applying", "passed")]

    versions = client.get(url(shop, "/versions")).json()["versions"]
    promoted = [v for v in versions if v["origin"] == "optimize" and v["status"] == "active"]
    assert len(promoted) == len(ids) and all(v["gate_status"] == "passed" for v in promoted)
    assert head(client, shop)["gate_status"] == "passed"
    for stdin, text in expected.items():
        assert stdout_of(client, shop, stdin) == text
    assert fixes(client, shop).keys() == {
        "FIX-01"
    }  # остались только исправления, меняющие поведение


def test_optimize_skips_a_step_whose_finding_disappeared(client, shop):
    plan = client.post(url(shop, "/optimize/plan"), json={}).json()
    step = plan["steps"][0]["step_id"]
    info = wait_run(
        client,
        client.post(
            url(shop, "/optimize/apply"),
            json={"base_version_id": head(client, shop)["id"], "step_ids": [step, step]},
        ).json()["run_id"],
        240,
    )
    assert info["summary"]["passed"] == 1
    assert "пропала" in info["summary"]["data"]["skipped"][step]


@pytest.mark.slow
def test_demo_orders_optimisation_keeps_every_scenario_green(client, demo_orders):
    """I8: все предложенные OPT сохраняют поведение монолита — gate на 11 сценариях остаётся зелёным."""
    pid = demo_orders["id"]
    assert run_kind(client, demo_orders, "baseline")["summary"]["passed"] == 11
    plan = client.post(url(demo_orders, "/optimize/plan"), json={}).json()
    ids = [s["step_id"] for s in plan["steps"]]
    assert {s["fix_id"] for s in plan["steps"]} == {"OPT-02", "OPT-03", "OPT-09"}

    response = client.post(
        url(demo_orders, "/optimize/apply"),
        json={"base_version_id": demo_orders["head_version_id"], "step_ids": ids},
    )
    info = wait_run(client, response.json()["run_id"], 300)
    assert info["status"] == "done" and info["summary"]["passed"] >= 4, info["summary"]
    gate = run_kind(client, demo_orders, "gate")
    assert gate["summary"]["version_status"] == "passed" and gate["summary"]["passed"] == 11

    remaining = {
        f["rule_id"] for f in client.get(f"/api/projects/{pid}/findings").json()["findings"]
    }
    assert not {"PERF-02", "PERF-03", "PERF-09"} & remaining
    build = client.post(f"/api/projects/{pid}/build", json={}).json()
    assert build["ok"], build["diagnostics"]


DEDUPE = """\
import java.util.*;

public class Main {
    public static void main(String[] args) {
        Scanner in = new Scanner(System.in);
        int n = in.nextInt();
        List<Integer> seen = new ArrayList<>();
        for (int i = 0; i < n; i++) {
            int v = in.nextInt();
            if (!seen.contains(v)) {
                seen.add(v);
            }
        }
        System.out.println(seen.size());
    }
}
"""


def test_optimize_reports_the_measured_speedup(client, make_project):
    """S8.7: до/после на масштабируемом вводе — ускорение и показатели роста."""
    project = make_project({"Main.java": DEDUPE}, name="dedupe")
    for name, stdin in (("one", "1\n5\n"), ("dups", "4\n1 2 1 2\n"), ("zero", "0\n")):
        client.post(url(project, "/scenarios"), json={"name": name, "stdin": stdin})
    assert run_kind(client, project, "baseline")["status"] == "done"
    plan = client.post(url(project, "/optimize/plan"), json={}).json()
    assert [s["fix_id"] for s in plan["steps"]] == ["OPT-02"]
    response = client.post(
        url(project, "/optimize/apply"),
        json={
            "base_version_id": head(client, project)["id"],
            "step_ids": [plan["steps"][0]["step_id"]],
        },
    )
    info = wait_run(client, response.json()["run_id"], 300)
    assert info["status"] == "done" and info["summary"]["passed"] == 1, info["summary"]
    measured = info["summary"]["data"]["measured"]
    assert "error" not in measured, measured
    assert measured["speedup"] >= 1.3, measured  # время чувствительно к нагрузке машины
    assert measured["before_k"] > measured["after_k"] + 0.2, measured
    assert "ускорение до" in info["summary"]["message"]
    assert {s["size"] for s in measured["samples"]} >= {1000, 8000}
