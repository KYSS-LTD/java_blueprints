"""Генерация сценариев (S6.9, метка `jdk`): поиск по покрытию, имена, эталон и устойчивость."""

from __future__ import annotations

import pytest

from tests.integration.conftest import read_sse, wait_run

pytestmark = pytest.mark.jdk

CLASSIFIER = """\
import java.util.*;

public class Main {
    public static void main(String[] args) {
        Scanner in = new Scanner(System.in);
        int n = in.hasNextInt() ? in.nextInt() : 0;
        int sum = 0;
        for (int i = 0; i < n && in.hasNextInt(); i++) {
            int v = in.nextInt();
            if (v < 0) {
                System.out.println("negative " + v);
            } else if (v == 0) {
                System.out.println("zero");
            } else if (v > 100) {
                System.out.println("big");
            } else {
                sum += v;
            }
        }
        System.out.println(n == 0 ? "empty" : "sum=" + sum);
    }
}
"""


def url(project, suffix: str) -> str:
    return f"/api/projects/{project['id']}{suffix}"


def generate(client, project, **params) -> dict:
    response = client.post(url(project, "/scenarios/generate"), params=params)
    assert response.status_code == 202, response.text
    return wait_run(client, response.json()["run_id"], 240)


def scenarios(client, project) -> list[dict]:
    return client.get(url(project, "/scenarios")).json()["scenarios"]


@pytest.fixture
def classifier(make_project):
    return make_project({"Main.java": CLASSIFIER}, name="classifier")


def test_generation_creates_named_generated_scenarios_with_baselines(client, classifier):
    info = generate(client, classifier)
    assert info["status"] == "done", info["summary"]
    summary = info["summary"]
    assert summary["data"]["coverage_after"] > summary["data"]["coverage_before"] == 0.0
    assert summary["data"]["coverage_after"] >= 0.9
    assert summary["data"]["runs"] <= 200 and summary["data"]["schema_inferred"] is True

    created = scenarios(client, classifier)
    assert len(created) == summary["passed"] >= 1
    assert [s["id"] for s in created] == summary["data"]["scenario_ids"]
    for scenario in created:
        assert scenario["source"] == "generated"
        assert scenario["name"].startswith("gen_") and scenario["description"]
        assert scenario["baseline"]["state"] == "recorded"
        assert scenario["stability"] in {"stable", "masked"}
    assert len({s["name"] for s in created}) == len(created)
    assert {i["name"] for i in info["items"]} == {s["name"] for s in created}


def test_generated_set_makes_a_green_gate_and_covers_the_blocks(client, classifier):
    generate(client, classifier)
    client.patch(url(classifier, ""), json={"settings": {"gate_min_scenarios": 1}})
    gate = client.post(url(classifier, "/runs"), json={"kind": "gate"}).json()["run_id"]
    info = wait_run(client, gate, 240)
    assert info["summary"]["version_status"] == "passed", info["summary"]
    trace = client.post(url(classifier, "/runs"), json={"kind": "trace"}).json()["run_id"]
    assert wait_run(client, trace, 240)["summary"]["coverage"] >= 0.9


def test_generation_is_deterministic_for_equal_sources(client, make_project):
    first, second = (make_project({"Main.java": CLASSIFIER}, name=n) for n in ("a", "b"))
    generate(client, first)
    generate(client, second)
    key = lambda p: [(s["name"], s["stdin"], s["args"]) for s in scenarios(client, p)]  # noqa: E731
    assert key(first) == key(second)


def test_existing_scenarios_are_kept_and_not_duplicated(client, classifier):
    client.post(url(classifier, "/scenarios"), json={"name": "mine", "stdin": "0\n"})
    generate(client, classifier)
    after = scenarios(client, classifier)
    assert after[0]["name"] == "mine" and after[0]["source"] == "manual"
    stdins = [s["stdin"] for s in after]
    assert len(stdins) == len(set(stdins))
    again = generate(client, classifier)  # повторный запуск: нового покрытия нет
    assert again["summary"]["passed"] == 0
    assert len(scenarios(client, classifier)) == len(after)


def test_progress_and_items_are_streamed(client, classifier):
    info = generate(client, classifier)
    events = read_sse(client, info["id"])
    progress = [e["data"] for e in events if e["event"] == "progress"]
    assert any(p["total"] == 200 for p in progress)  # поиск; затем эталон новых сценариев
    assert all(p["done"] <= p["total"] for p in progress)
    names = {e["data"]["name"] for e in events if e["event"] == "item"}  # и записи эталона, и итог
    assert names == {i["name"] for i in info["items"]}
    assert events[-1]["event"] == "done"


def test_program_without_input_gets_an_empty_run_scenario(client, make_project):
    project = make_project(
        {
            "Main.java": 'public class Main { public static void main(String[] a) { System.out.println("hi"); } }'
        }
    )
    info = generate(client, project)
    assert info["status"] == "done"
    assert info["summary"]["data"]["schema_inferred"] is False
    assert [s["stdin"] for s in scenarios(client, project)] in ([""], [])


def test_generation_needs_a_main_class(client, make_project):
    project = make_project({"A.java": "class A {}"})
    response = client.post(url(project, "/scenarios/generate"))
    assert response.status_code == 400


def test_candidate_version_gets_scenarios_without_an_overwritten_baseline(client, classifier):
    changed = client.put(
        url(classifier, "/files/Main.java"),
        json={"base_version_id": classifier["head_version_id"], "content": CLASSIFIER + "\n"},
    ).json()["version"]["id"]
    info = generate(client, classifier, version_id=changed)
    assert info["status"] == "done"
    assert any("не является эталонной" in w for w in info["summary"]["data"]["warnings"])
    assert all(s["baseline"]["state"] == "none" for s in scenarios(client, classifier))


@pytest.mark.slow
def test_demo_orders_reaches_high_coverage_within_the_budget(client, make_project):
    files = {
        p.name: p.read_text(encoding="utf-8")
        for p in __import__("pathlib")
        .Path(__file__)
        .resolve()
        .parents[3]
        .joinpath("samples", "demo-orders")
        .glob("*.java")
    }
    project = make_project(files, name="demo")
    info = generate(client, project)
    assert info["status"] == "done"
    assert info["summary"]["data"]["coverage_after"] >= 0.8, info["summary"]["data"]
    assert info["summary"]["data"]["elapsed_s"] <= 75
