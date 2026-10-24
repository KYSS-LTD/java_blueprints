"""CLEAN-01/02/03 через API (S8.5, метка `jdk`): кандидат → gate → HEAD, вывод не меняется."""

from __future__ import annotations

from urllib.parse import quote

import pytest

from tests.integration.conftest import wait_run

pytestmark = pytest.mark.jdk

PROGRAM = """\
import java.util.*;

public class Main {
    static int Bad_Name(int x) {
        return x * 100 + 100;
    }

    private static int unusedHelper(int x) {
        return x + 1;
    }

    public static void main(String[] args) {
        Scanner in = new Scanner(System.in);
        List<Integer> numbers = new ArrayList<>();
        while (in.hasNextInt()) {
            numbers.add(in.nextInt());
        }
        int leftover = 7;
        int total = 0;
        for (int v : numbers) {
            if (v > 100) {
                total += 100;
            } else {
                total += Bad_Name(v);
            }
        }
        System.out.println(total);
    }
}
"""
INPUTS = ["1 2 3\n", "", "500 20\n", "100 101\n"]


def url(project, suffix: str) -> str:
    return f"/api/projects/{project['id']}{suffix}"


def run_kind(client, project, kind: str, **body) -> dict:
    response = client.post(url(project, "/runs"), json={"kind": kind, **body})
    assert response.status_code == 202, response.text
    return wait_run(client, response.json()["run_id"], 180)


def outputs(client, project) -> list[str]:
    return [run_kind(client, project, "run", stdin=s)["items"][0]["stdout"] for s in INPUTS]


@pytest.fixture
def program(client, make_project):
    project = make_project({"Main.java": PROGRAM}, name="clean")
    for index, stdin in enumerate(INPUTS):
        client.post(url(project, "/scenarios"), json={"name": f"c{index}", "stdin": stdin})
    assert run_kind(client, project, "baseline")["status"] == "done"
    return project


def fixes(client, project) -> dict[str, list[dict]]:
    found: dict[str, list[dict]] = {}
    for item in client.get(url(project, "/fixes")).json()["fixes"]:
        found.setdefault(item["fix_id"], []).append(item)
    return found


def test_clean_fixes_are_listed_as_behaviour_preserving(client, program):
    listed = fixes(client, program)
    for fix_id in ("CLEAN-01", "CLEAN-02", "CLEAN-03"):
        assert fix_id in listed, sorted(listed)
        assert all(f["behavior_preserving"] and f["available"] for f in listed[fix_id])


@pytest.mark.parametrize("fix_id", ["CLEAN-01", "CLEAN-02", "CLEAN-03"])
def test_each_clean_fix_keeps_the_output_and_the_gate_green(client, program, fix_id):
    expected = outputs(client, program)
    finding = fixes(client, program)[fix_id][0]["finding_id"]
    versions = client.get(url(program, "/versions")).json()
    response = client.post(
        url(program, f"/fixes/{quote(finding, safe=chr(47))}/apply"),
        json={"base_version_id": versions["head_version_id"]},
    )
    assert response.status_code == 202, response.text
    info = wait_run(client, response.json()["run_id"], 240)
    assert info["summary"]["version_status"] == "passed", info["summary"]
    assert info["summary"]["data"]["candidate"] == "promoted"
    assert outputs(client, program) == expected
