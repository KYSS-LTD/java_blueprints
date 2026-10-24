"""Запуск ``trace``: счётчики по блокам графа и покрытие (S6.7, метка `jdk`)."""

from __future__ import annotations

import pytest

from tests.integration.conftest import read_sse
from tests.integration.test_gate_api import CALC, add_scenarios, items, start

pytestmark = pytest.mark.jdk


@pytest.fixture
def calc(make_project):
    return make_project({"Main.java": CALC}, name="calc")


def flow_nodes(client, project, method: str = "Main.main(String[])") -> dict[str, dict]:
    response = client.get(
        f"/api/projects/{project['id']}/flow",
        params={"method_id": f"Main.java#{method}"},
    )
    assert response.status_code == 200, response.text
    return {n["id"]: n for n in response.json()["nodes"]}


def test_trace_of_a_custom_input_counts_loop_iterations(client, calc):
    info = start(client, calc, "trace", stdin="1 2 3\n")
    assert info["status"] == "done"
    [item] = info["items"]
    assert item["stdout"] == "11\n12\n13\n" or item["stdout"].replace("\r", "") == "11\n12\n13\n"
    nodes = flow_nodes(client, calc)
    assert set(item["hits"]) <= set(nodes) | set(flow_nodes(client, calc, "Main.add(int, int)"))
    loop = next(n["id"] for n in nodes.values() if n["kind"] == "loop_while")
    assert item["hits"][loop] == 3
    summary = info["summary"]
    assert 0 < summary["coverage"] < 1
    assert summary["data"]["total"] > summary["data"]["covered"]
    assert summary["data"]["details"]["run"]["entries"][loop] == 1
    # блок, которого касаются только аргументы командной строки, остался невыполненным
    assert any("args" in nodes[n]["code"] for n in summary["data"]["uncovered"] if n in nodes)


def test_trace_of_scenarios_gives_one_item_per_scenario_and_merged_coverage(client, calc):
    scenarios = add_scenarios(client, calc, 4)
    info = start(client, calc, "trace")
    assert info["status"] == "done"
    assert {i["name"] for i in info["items"]} == {s["name"] for s in scenarios}
    assert all(i["hits"] for i in info["items"])
    assert info["summary"]["coverage"] == 1.0  # сценарий с аргументами докрывает последнюю ветку
    only_one = start(client, calc, "trace", scenario_ids=[scenarios[0]["id"]])
    assert [i["name"] for i in only_one["items"]] == ["one"]
    assert only_one["summary"]["coverage"] < 1.0


def test_trace_reports_progress_and_items_over_sse(client, calc):
    add_scenarios(client, calc, 3)
    run = client.post(f"/api/projects/{calc['id']}/runs", json={"kind": "trace"}).json()["run_id"]
    kinds = [e["event"] for e in read_sse(client, run)]
    assert kinds[0] == "status" and kinds[-1] == "done"
    assert kinds.count("item") == 3
    assert "progress" in kinds and "result" in kinds


def test_trace_of_a_program_that_does_not_compile_reports_the_compile_stage(client, make_project):
    project = make_project({"Main.java": CALC.replace("a + b", "a +")}, name="broken")
    info = start(client, project, "trace", stdin="")
    assert info["status"] == "failed"
    assert info["summary"]["data"]["stage"] == "compile"


def test_trace_does_not_touch_the_project_files(client, calc):
    before = client.get(f"/api/projects/{calc['id']}/files/Main.java").json()["content"]
    start(client, calc, "trace", stdin="5\n")
    after = client.get(f"/api/projects/{calc['id']}/files/Main.java").json()["content"]
    assert before == after == CALC
    versions = client.get(f"/api/projects/{calc['id']}/versions").json()["versions"]
    assert len(versions) == 1


def test_trace_on_the_demo_project_marks_the_command_loop(client, demo_orders):
    info = start(client, demo_orders, "trace", stdin="add 1 Иванов Хлеб 2 45.5\nlist\nquit\n")
    assert info["status"] == "done"
    [item] = info["items"]
    assert item["verdict"] == "PASS"
    assert len(item["hits"]) > 500
    assert any(count > 0 for count in item["hits"].values())
    assert items(info)  # имя элемента — главный класс
