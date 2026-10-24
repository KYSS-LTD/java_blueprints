"""Декомпозиция монолита через API (S9.5–S9.10, I9; метка `jdk`): план, предпросмотр, gate."""

from __future__ import annotations

import copy

import pytest

from tests.integration.conftest import wait_run
from tests.unit.test_decompose import INHERITED, NESTED

pytestmark = pytest.mark.jdk

MONOLITHS = ["demo-orders", "inventory", "text-analyzer", "graph-paths", "expression-calculator"]


def url(project, suffix: str) -> str:
    return f"/api/projects/{project['id']}{suffix}"


def make(client, sample: str) -> dict:
    sample_id = sample
    response = client.post("/api/projects", json={"sample_id": sample_id})
    assert response.status_code == 201, response.text
    return response.json()


def plan_of(client, project, **options) -> dict:
    response = client.post(url(project, "/decompose/plan"), json={"options": options or None})
    assert response.status_code == 200, response.text
    return response.json()


def head(client, project) -> dict:
    versions = client.get(url(project, "/versions")).json()
    return next(v for v in versions["versions"] if v["id"] == versions["head_version_id"])


def run_kind(client, project, kind: str, **body) -> dict:
    response = client.post(url(project, "/runs"), json={"kind": kind, **body})
    return wait_run(client, response.json()["run_id"], 300)


def decompose(client, project, plan: dict):
    return client.post(
        url(project, "/decompose/apply"),
        json={"base_version_id": head(client, project)["id"], "plan": plan},
    )


def test_plan_has_a_core_and_at_least_three_new_classes_for_demo_orders(client):
    project = make(client, "demo-orders")
    plan = plan_of(client, project)
    core = [c for c in plan["classes"] if c["role"] == "core"]
    moved = [c for c in plan["classes"] if c["role"] not in {"core", "model"}]
    assert [c["name"] for c in core] == ["Main"] and core[0]["file"] == "Main.java"
    assert len(moved) >= 3
    assert all(c["file"] == f"{c['name']}.java" and c["members"] and c["lines"] > 0 for c in moved)
    assert all(c["reason"] for c in plan["classes"])
    assert plan["metrics"]["after"]["lines"] * 2 <= plan["metrics"]["before"]["lines"]
    members = [m for c in plan["classes"] if c["role"] != "model" for m in c["members"]]
    assert len(members) == len(set(members))  # каждый член ровно в одном классе
    assert {e["from"] for e in plan["edges"]} <= {c["name"] for c in plan["classes"]}


def test_plan_is_deterministic_and_respects_options(client):
    first, second = make(client, "demo-orders"), make(client, "demo-orders")
    assert plan_of(client, first) == plan_of(client, second)
    two = plan_of(client, first, max_classes=2)
    assert len([c for c in two["classes"] if c["role"] not in {"core", "model"}]) <= 2
    assert plan_of(client, first, seed=1) == plan_of(client, second, seed=1)


def test_preview_shows_new_files_and_does_not_save(client):
    project = make(client, "demo-orders")
    plan = plan_of(client, project)
    response = client.post(
        url(project, "/decompose/preview"),
        json={"base_version_id": project["head_version_id"], "plan": plan},
    )
    assert response.status_code == 200, response.text
    body = response.json()
    status = {d["path"]: d["status"] for d in body["diff"]}
    assert status["Main.java"] == "modified"
    assert all(status[c["file"]] == "added" for c in plan["classes"] if c["role"] != "core")
    assert not any("не компилируется" in w for w in body["warnings"]), body["warnings"]
    assert len(client.get(url(project, "/versions")).json()["versions"]) == 1


def test_apply_makes_a_gated_candidate_with_symbol_moves(client):
    project = make(client, "demo-orders")
    assert run_kind(client, project, "baseline")["summary"]["passed"] == 11
    plan = plan_of(client, project)
    response = decompose(client, project, plan)
    assert response.status_code == 202, response.text
    info = wait_run(client, response.json()["run_id"], 300)
    assert info["summary"]["version_status"] == "passed", info[
        "summary"
    ]  # A6: все сценарии зелёные
    assert info["summary"]["passed"] == 11 and info["summary"]["data"]["candidate"] == "promoted"

    new_head = head(client, project)
    assert (new_head["origin"], new_head["status"]) == ("decompose", "active")
    files = {f["path"]: f for f in client.get(url(project, "/files")).json()["files"]}
    assert (
        files["Main.java"]["lines"] * 2 <= project["files"][0]["lines"]
    )  # Main.java уменьшился вдвое
    assert {c["file"] for c in plan["classes"]} <= set(files)
    detail = client.get(url(project, f"/versions/{new_head['id']}")).json()
    assert len(detail["files"]) == len(files)
    build = client.post(url(project, "/build"), json={}).json()
    assert build["ok"], build["diagnostics"]


def test_applying_the_same_plan_to_equal_projects_gives_identical_files(client):
    first, second = make(client, "demo-orders"), make(client, "demo-orders")
    for project in (first, second):
        run_kind(client, project, "baseline")
        info = wait_run(
            client, decompose(client, project, plan_of(client, project)).json()["run_id"], 120
        )
        assert info["summary"]["version_status"] == "passed"
    texts = [
        {
            f["path"]: client.get(url(p, f"/files/{f['path']}")).json()["content"]
            for f in client.get(url(p, "/files")).json()["files"]
        }
        for p in (first, second)
    ]
    assert texts[0] == texts[1]


def test_plan_can_be_edited_before_applying(client):
    project = make(client, "demo-orders")
    plan = plan_of(client, project)
    edited = copy.deepcopy(plan)
    moved = [c for c in edited["classes"] if c["role"] not in {"core", "model"}]
    moved[0]["name"], moved[0]["file"] = "OrderRules", "OrderRules.java"
    for edge in edited["edges"]:
        edge["from"] = "OrderRules" if edge["from"] == plan["classes"][1]["name"] else edge["from"]
    response = client.post(
        url(project, "/decompose/preview"),
        json={"base_version_id": project["head_version_id"], "plan": edited},
    )
    assert response.status_code == 200, response.text
    assert any(d["path"] == "OrderRules.java" for d in response.json()["diff"])
    # метод переехал из одного нового класса в другой — результат остаётся рабочим
    method = moved[0]["members"][0]
    moved[1]["members"].append(method)
    moved[0]["members"].remove(method)
    again = client.post(
        url(project, "/decompose/preview"),
        json={"base_version_id": project["head_version_id"], "plan": edited},
    )
    assert again.status_code == 200 and not any(
        "не компилируется" in w for w in again.json()["warnings"]
    ), again.text


def test_rescore_recounts_a_plan_after_a_method_moves(client):
    project = make(client, "demo-orders")
    plan = plan_of(client, project)
    base = project["head_version_id"]
    rescore = lambda p: client.post(  # noqa: E731
        url(project, "/decompose/rescore"), json={"base_version_id": base, "plan": p}
    )

    untouched = rescore(plan)
    assert untouched.status_code == 200, untouched.text
    assert untouched.json() == plan  # план из `plan` уже пересчитан: правок нет, ответ тот же

    edited = copy.deepcopy(plan)
    first, second = [c for c in edited["classes"] if c["role"] not in {"core", "model"}][:2]
    method = first["members"].pop(0)
    second["members"].append(method)
    body = rescore(edited).json()
    lines = {c["name"]: c["lines"] for c in body["classes"]}
    before = {c["name"]: c["lines"] for c in plan["classes"]}
    assert lines[first["name"]] < before[first["name"]]
    assert lines[second["name"]] > before[second["name"]]
    assert body["edges"] != plan["edges"]

    stale = copy.deepcopy(edited)
    stale["warnings"] = ["устаревшее"]
    assert "устаревшее" not in rescore(stale).json()["warnings"]

    # предпросмотр возвращает тот же пересчитанный план
    preview = client.post(
        url(project, "/decompose/preview"), json={"base_version_id": base, "plan": edited}
    )
    assert preview.status_code == 200, preview.text
    assert preview.json()["plan"] == body

    taken = copy.deepcopy(edited)
    taken["classes"][1]["name"] = "Order"  # имя существующего типа
    assert "занято" in rescore(taken).json()["error"]["message"]
    stolen = copy.deepcopy(plan)
    entry = next(m for m in plan["pinned"] if m.endswith(".main(String[])"))
    stolen["classes"][0]["members"].remove(entry)
    stolen["classes"][1]["members"].append(entry)
    assert rescore(stolen).status_code == 422

    no_core = copy.deepcopy(plan)
    no_core["classes"] = [c for c in no_core["classes"] if c["role"] != "core"]
    assert rescore(no_core).status_code == 400
    assert (
        client.post(
            url(project, "/decompose/rescore"), json={"base_version_id": 999, "plan": plan}
        ).status_code
        == 409
    )


def test_invalid_plans_are_refused_with_a_reason(client):
    project = make(client, "demo-orders")
    plan = plan_of(client, project)
    preview = lambda p: client.post(  # noqa: E731
        url(project, "/decompose/preview"),
        json={"base_version_id": project["head_version_id"], "plan": p},
    )

    taken = copy.deepcopy(plan)
    next(c for c in taken["classes"] if c["role"] == "analyzer")["name"] = "Order"
    assert "занято" in preview(taken).json()["error"]["message"]

    bad_name = copy.deepcopy(plan)
    next(c for c in bad_name["classes"] if c["role"] == "analyzer")["name"] = "1bad"
    assert preview(bad_name).status_code == 422

    pinned = copy.deepcopy(plan)
    core = next(c for c in pinned["classes"] if c["role"] == "core")
    stolen = next(m for m in core["members"] if m.endswith(".main(String[])"))
    core["members"].remove(stolen)
    next(c for c in pinned["classes"] if c["role"] == "analyzer")["members"].append(stolen)
    assert "нельзя переносить" in preview(pinned).json()["error"]["message"]

    missing = copy.deepcopy(plan)
    next(c for c in missing["classes"] if c["role"] == "analyzer")["members"].pop()
    assert "не хватает" in preview(missing).json()["error"]["message"]

    no_core = copy.deepcopy(plan)
    no_core["classes"] = [c for c in no_core["classes"] if c["role"] != "core"]
    assert preview(no_core).status_code == 400

    stale = client.post(
        url(project, "/decompose/apply"), json={"base_version_id": 999, "plan": plan}
    )
    assert stale.status_code == 409


@pytest.mark.slow
@pytest.mark.parametrize("sample", MONOLITHS)
def test_every_monolith_decomposes_into_working_code(client, sample):
    """I9: `javac` проходит, все сценарии зелёные, ≥ 3 новых класса, файл ядра уменьшился вдвое."""
    project = make(client, sample)
    baseline = run_kind(client, project, "baseline")
    total = baseline["summary"]["passed"]
    assert total >= 9
    plan = plan_of(client, project)
    moved = [c for c in plan["classes"] if c["role"] not in {"core", "model"}]
    assert len(moved) >= 3, [c["name"] for c in plan["classes"]]
    before_lines = next(f["lines"] for f in project["files"] if f["path"] == "Main.java")

    info = wait_run(client, decompose(client, project, plan).json()["run_id"], 300)
    assert info["summary"]["version_status"] == "passed", (sample, info["summary"])
    assert info["summary"]["passed"] == total
    lines = {f["path"]: f["lines"] for f in client.get(url(project, "/files")).json()["files"]}
    assert lines["Main.java"] * 2 <= before_lines
    assert all(c["file"] in lines for c in plan["classes"])


@pytest.mark.parametrize("source", [NESTED, INHERITED], ids=["nested-types", "inheritance"])
def test_nested_types_and_inheritance_still_compile(client, make_project, source):
    project = make_project({"Main.java": source}, name="tricky")
    plan = plan_of(client, project)
    assert any(c["role"] not in {"core", "model"} for c in plan["classes"])
    response = client.post(
        url(project, "/decompose/preview"),
        json={"base_version_id": project["head_version_id"], "plan": plan},
    )
    assert response.status_code == 200, response.text
    body = response.json()
    assert not any("не компилируется" in w for w in body["warnings"]), body["warnings"]
    assert any(d["status"] == "added" for d in body["diff"])
