"""Приёмка S6 (метка `jdk`): I5 «два прогона дают одно и то же», нагрузка gate на 15 сценариях, детерминизм генератора."""

from __future__ import annotations

import time

import pytest

from tests.integration.conftest import wait_run
from tests.integration.test_generate_api import CLASSIFIER, generate, scenarios

pytestmark = pytest.mark.jdk

SAMPLES = [
    "demo-orders",
    "analyzer-pairs",
    "inventory",
    "text-analyzer",
    "graph-paths",
    "expression-calculator",
]


def url(project, suffix: str) -> str:
    return f"/api/projects/{project['id']}{suffix}"


def run_kind(client, project, kind: str) -> dict:
    response = client.post(url(project, "/runs"), json={"kind": kind})
    assert response.status_code == 202, response.text
    return wait_run(client, response.json()["run_id"], 600)


@pytest.mark.slow
@pytest.mark.parametrize("sample", SAMPLES)
def test_i5_two_runs_of_every_scenario_are_identical(client, sample):
    project = client.post("/api/projects", json={"sample_id": sample}).json()
    assert run_kind(client, project, "baseline")["status"] == "done"
    first = run_kind(client, project, "test")
    second = run_kind(client, project, "test")
    assert first["items"], sample

    def key(info: dict) -> dict:
        return {i["name"]: (i["verdict"], i["stdout"], i["exit_code"]) for i in info["items"]}

    assert key(first) == key(second)
    assert all(verdict == "PASS" for verdict, _, _ in key(first).values()), sample


@pytest.mark.slow
def test_gate_on_fifteen_scenarios_fits_the_budget(client):
    project = client.post("/api/projects", json={"sample_id": "demo-orders"}).json()
    existing = client.get(url(project, "/scenarios")).json()["scenarios"]
    assert len(existing) == 11
    for index in range(4):
        client.post(
            url(project, "/scenarios"),
            json={"name": f"extra-{index}", "stdin": "list\nstats\nreport\nquit\n" * (index + 1)},
        )
    assert len(client.get(url(project, "/scenarios")).json()["scenarios"]) == 15
    assert run_kind(client, project, "baseline")["status"] == "done"
    started = time.perf_counter()
    gate = run_kind(client, project, "gate")
    elapsed = time.perf_counter() - started
    assert gate["summary"]["version_status"] == "passed", gate["summary"]
    assert gate["summary"]["total"] >= 15
    assert elapsed <= 20, f"gate на 15 сценариях занял {elapsed:.1f} с"


def test_generator_gives_the_same_scenarios_for_the_same_program(client, make_project):
    outcomes = []
    for index in range(2):
        project = make_project({"Main.java": CLASSIFIER}, name=f"twin-{index}")
        info = generate(client, project, runs=80, seconds=60)
        assert info["status"] == "done", info["summary"]
        outcomes.append(
            (
                info["summary"]["data"]["coverage_after"],
                sorted((s["name"], s["stdin"]) for s in scenarios(client, project)),
            )
        )
    assert outcomes[0] == outcomes[1]
