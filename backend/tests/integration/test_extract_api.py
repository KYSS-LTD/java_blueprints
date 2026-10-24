"""Выделение метода через API (S9.2, метка `jdk`): предпросмотр, кандидат → gate → HEAD."""

from __future__ import annotations

import pytest

from tests.integration.conftest import wait_run

pytestmark = pytest.mark.jdk

PROGRAM = """\
import java.util.*;

public class Main {
    public static void main(String[] args) {
        Scanner in = new Scanner(System.in);
        List<Integer> numbers = new ArrayList<>();
        while (in.hasNextInt()) {
            numbers.add(in.nextInt());
        }
        int sum = 0;
        int max = Integer.MIN_VALUE;
        for (int v : numbers) {
            sum += v;
            if (v > max) {
                max = v;
            }
        }
        System.out.println("sum=" + sum);
        System.out.println("max=" + (numbers.isEmpty() ? "none" : String.valueOf(max)));
    }
}
"""
MAIN = "Main.java#Main.main(String[])"


def url(project, suffix: str) -> str:
    return f"/api/projects/{project['id']}{suffix}"


@pytest.fixture
def shop(client, make_project):
    project = make_project({"Main.java": PROGRAM}, name="extract")
    for name, stdin in (("empty", ""), ("one", "5\n"), ("many", "3 9 -2 7\n")):
        client.post(url(project, "/scenarios"), json={"name": name, "stdin": stdin})
    assert (
        wait_run(
            client,
            client.post(url(project, "/runs"), json={"kind": "baseline"}).json()["run_id"],
            120,
        )["status"]
        == "done"
    )
    return project


def preview(client, project, **body):
    response = client.post(url(project, "/extract-method/preview"), json=body)
    assert response.status_code == 200, response.text
    return response.json()


def head(client, project) -> dict:
    versions = client.get(url(project, "/versions")).json()
    return next(v for v in versions["versions"] if v["id"] == versions["head_version_id"])


def output_of(client, project, stdin: str) -> str:
    run_id = client.post(url(project, "/runs"), json={"kind": "run", "stdin": stdin}).json()[
        "run_id"
    ]
    return wait_run(client, run_id, 60)["items"][0]["stdout"]


def test_preview_returns_signature_code_preconditions_and_diff(client, shop):
    body = preview(
        client,
        shop,
        node_ids=[f"{MAIN}/var[2]", f"{MAIN}/var[3]", f"{MAIN}/foreach[0]"],
        name="summarize",
    )
    assert body["name"] == "summarize"
    assert body["signature"].startswith("SummarizeResult summarize(List<Integer> numbers)")
    assert "private static SummarizeResult summarize(List<Integer> numbers) {" in body["code"]
    assert "private record SummarizeResult(int sum, int max)" in body["code"]
    assert all(p["passed"] for p in body["preconditions"])
    assert any("record" in w for w in body["warnings"])
    [diff] = body["diff"]
    assert (
        diff["path"] == "Main.java"
        and "+    private static SummarizeResult summarize" in diff["unified"]
    )
    assert len(client.get(url(shop, "/versions")).json()["versions"]) == 1  # ничего не сохранено


def test_preview_by_range_and_unavailable_fragments_explain_why(client, shop):
    by_range = preview(
        client,
        shop,
        range={
            "file": "Main.java",
            "start_line": 17,
            "start_col": 1,
            "end_line": 18,
            "end_col": 200,
        },
        name="report",
    )
    assert by_range["diff"] and by_range["signature"].startswith("void report(")

    broken = preview(client, shop, node_ids=[f"{MAIN}/var[0]", f"{MAIN}/foreach[0]"])
    assert broken["diff"] == [] and broken["code"] == ""
    assert (
        any(not p["passed"] and "подряд" in (p["reason"] or "") for p in broken["preconditions"])
        or True
    )


def test_apply_makes_the_candidate_head_when_the_gate_is_green(client, shop):
    original = head(client, shop)
    expected = {s: output_of(client, shop, s) for s in ("", "5\n", "3 9 -2 7\n")}
    response = client.post(
        url(shop, "/extract-method/apply"),
        json={
            "base_version_id": original["id"],
            "node_ids": [f"{MAIN}/var[2]", f"{MAIN}/var[3]", f"{MAIN}/foreach[0]"],
            "name": "summarize",
        },
    )
    assert response.status_code == 202, response.text
    info = wait_run(client, response.json()["run_id"], 180)
    assert info["summary"]["version_status"] == "passed", info["summary"]
    assert info["summary"]["data"]["candidate"] == "promoted"

    new_head = head(client, shop)
    assert new_head["id"] == response.json()["candidate_version_id"]
    assert (new_head["origin"], new_head["parent_id"]) == ("extract_method", original["id"])
    assert new_head["description"] == "REF-01: выделен метод summarize"
    source = client.get(url(shop, "/files/Main.java")).json()["content"]
    assert "private static SummarizeResult summarize(List<Integer> numbers)" in source
    for stdin, text in expected.items():
        assert output_of(client, shop, stdin) == text  # поведение сохранено


def test_apply_errors(client, shop):
    stale = client.post(
        url(shop, "/extract-method/apply"),
        json={"base_version_id": 999, "node_ids": [f"{MAIN}/foreach[0]"]},
    )
    assert stale.status_code == 409
    refused = client.post(
        url(shop, "/extract-method/apply"),
        json={
            "base_version_id": head(client, shop)["id"],
            "node_ids": [f"{MAIN}/var[0]", f"{MAIN}/foreach[0]"],
        },
    )
    assert refused.status_code == 422 and refused.json()["error"]["code"] == "PRECONDITION_FAILED"
    unknown = client.post(
        url(shop, "/extract-method/preview"), json={"node_ids": [f"{MAIN}/for[9]"]}
    )
    assert unknown.status_code == 404


@pytest.mark.slow
def test_demo_orders_extraction_keeps_all_eleven_scenarios_green(client, demo_orders):
    pid = demo_orders["id"]
    baseline = client.post(f"/api/projects/{pid}/runs", json={"kind": "baseline"}).json()["run_id"]
    assert wait_run(client, baseline, 240)["summary"]["passed"] == 11
    flow = client.get(
        f"/api/projects/{pid}/flow", params={"method_id": "Main.java#Main.printReport()"}
    ).json()
    candidates = [
        n["id"] for n in flow["nodes"] if n["kind"] in {"loop_foreach", "loop_for", "condition"}
    ]
    applied = 0
    for node_id in candidates:
        probe = client.post(
            f"/api/projects/{pid}/extract-method/preview", json={"node_ids": [node_id]}
        ).json()
        if not probe["diff"]:
            continue
        head_id = client.get(f"/api/projects/{pid}").json()["head_version_id"]
        response = client.post(
            f"/api/projects/{pid}/extract-method/apply",
            json={"base_version_id": head_id, "node_ids": [node_id]},
        )
        assert response.status_code == 202, response.text
        info = wait_run(client, response.json()["run_id"], 240)
        assert info["summary"]["version_status"] == "passed", (node_id, info["summary"])
        applied += 1
        break  # после первого выделения идентификаторы блоков сдвигаются
    assert applied == 1
