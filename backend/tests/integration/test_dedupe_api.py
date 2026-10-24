"""Замена клонов общим методом через API (S9.3, REF-02, метка `jdk`): предпросмотр, кандидат, gate."""

from __future__ import annotations

from urllib.parse import quote

import pytest

from tests.integration.conftest import wait_run

pytestmark = pytest.mark.jdk


def url(project, suffix: str) -> str:
    return f"/api/projects/{project['id']}{suffix}"


def make(client, sample: str) -> dict:
    response = client.post("/api/projects", json={"sample_id": sample})
    assert response.status_code == 201, response.text
    return response.json()


def clone_findings(client, project) -> list[dict]:
    findings = client.get(url(project, "/analysis")).json()["findings"]
    return [f for f in findings if f["rule_id"] == "MAINT-02"]


def finding_path(finding_id: str) -> str:
    return quote(finding_id, safe=chr(47))


def head(client, project) -> dict:
    versions = client.get(url(project, "/versions")).json()
    return next(v for v in versions["versions"] if v["id"] == versions["head_version_id"])


def baseline(client, project) -> dict:
    run_id = client.post(url(project, "/runs"), json={"kind": "baseline"}).json()["run_id"]
    return wait_run(client, run_id, 300)


def available(client, project) -> list[dict]:
    return [f for f in clone_findings(client, project) if f["fix"] and f["fix"]["available"]]


def test_every_clone_finding_carries_a_fix_with_an_answer(client):
    project = make(client, "demo-orders")
    findings = clone_findings(client, project)
    assert len(findings) == 4
    assert all(f["fix"]["fix_id"] == "REF-02" for f in findings)
    ready = [f for f in findings if f["fix"]["available"]]
    refused = [f for f in findings if not f["fix"]["available"]]
    assert [f["location"]["method_id"] for f in ready] == ["Main.java#Main.checkCustomer(Order)"]
    reasons = " ".join(
        p["reason"] or "" for f in refused for p in f["fix"]["preconditions"] if not p["passed"]
    )
    assert "getCustomer" in reasons and "return" in reasons
    listed = {f["finding_id"]: f for f in client.get(url(project, "/fixes")).json()["fixes"]}
    assert listed[ready[0]["id"]]["fix_id"] == "REF-02" and listed[ready[0]["id"]]["available"]


def test_preview_shows_the_common_method_and_both_calls(client):
    project = make(client, "demo-orders")
    [finding] = available(client, project)
    response = client.post(url(project, f"/fixes/{finding_path(finding['id'])}/preview"), json={})
    assert response.status_code == 200, response.text
    body = response.json()
    assert body["fix_id"] == "REF-02" and body["behavior_preserving"] is True
    assert all(p["passed"] for p in body["preconditions"])
    [diff] = body["diff"]
    added = [line for line in diff["unified"].splitlines() if line.startswith("+")]
    assert any(
        "private static void " in line and "String value, Order order" in line for line in added
    )
    assert sum('"заказ " + order.getId() + message' in line for line in added) == 3
    assert len(client.get(url(project, "/versions")).json()["versions"]) == 1


def test_a_clone_that_differs_in_method_names_is_a_precondition_failure(client):
    project = make(client, "demo-orders")
    refused = next(
        f
        for f in clone_findings(client, project)
        if f["location"]["method_id"] == "Main.java#Main.totalForCustomer(String)"
    )
    response = client.post(url(project, f"/fixes/{finding_path(refused['id'])}/preview"), json={})
    assert response.status_code == 422
    error = response.json()["error"]
    assert error["code"] == "PRECONDITION_FAILED" and "getCustomer" in error["message"]


def test_apply_makes_a_gated_candidate_and_the_finding_disappears(client):
    project = make(client, "demo-orders")
    total = baseline(client, project)["summary"]["passed"]
    assert total == 11
    [finding] = available(client, project)
    original = head(client, project)
    response = client.post(
        url(project, f"/fixes/{finding_path(finding['id'])}/apply"),
        json={"base_version_id": original["id"]},
    )
    assert response.status_code == 202, response.text
    info = wait_run(client, response.json()["run_id"], 300)
    assert info["summary"]["version_status"] == "passed", info["summary"]
    assert info["summary"]["passed"] == total and info["summary"]["data"]["candidate"] == "promoted"

    new_head = head(client, project)
    assert (new_head["origin"], new_head["parent_id"]) == ("extract_method", original["id"])
    assert new_head["description"].startswith("REF-02: ")
    text = client.get(url(project, "/files/Main.java")).json()["content"]
    assert text.count('"заказ " + order.getId() + message') == 3  # тело общего метода — один раз
    assert text.count("MAX_NAME_LENGTH") <= 2
    assert not any(
        f["location"]["method_id"] == "Main.java#Main.checkCustomer(Order)"
        for f in clone_findings(client, project)
    )
    assert client.post(url(project, "/build"), json={}).json()["ok"]


def test_inventory_clone_is_replaced_and_scenarios_stay_green(client):
    project = make(client, "inventory")
    total = baseline(client, project)["summary"]["passed"]
    [finding] = available(client, project)
    response = client.post(
        url(project, f"/fixes/{finding_path(finding['id'])}/apply"),
        json={"base_version_id": head(client, project)["id"]},
    )
    info = wait_run(client, response.json()["run_id"], 300)
    assert info["summary"]["version_status"] == "passed", info["summary"]
    assert info["summary"]["passed"] == total


def test_batch_optimization_does_not_apply_refactorings(client):
    project = make(client, "demo-orders")
    plan = client.post(url(project, "/optimize/plan"), json={}).json()
    assert plan["steps"] and all(step["fix_id"].startswith("OPT") for step in plan["steps"])
