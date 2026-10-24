"""OPT-04, OPT-05, OPT-07 через API (S8.3, метка `jdk`): кандидат → gate → HEAD, вывод не меняется."""

from __future__ import annotations

from urllib.parse import quote

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
        String label = "Total";
        int n = numbers.size();
        long acc = 0;
        for (int i = 0; i < n; i++) {
            Collections.sort(numbers);
            acc += numbers.get(i) * label.toUpperCase().length();
        }
        long total = acc;
        double a = Math.sqrt(total) + 1;
        double b = Math.sqrt(total) * 2;
        System.out.println(total + " " + a + " " + b);
    }
}
"""
INPUTS = ["3 1 2\n", "", "5\n", "9 8 7 6 5 4 3 2 1\n"]


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
    project = make_project({"Main.java": PROGRAM}, name="more-fixes")
    for index, stdin in enumerate(INPUTS):
        client.post(url(project, "/scenarios"), json={"name": f"s{index}", "stdin": stdin})
    assert run_kind(client, project, "baseline")["status"] == "done"
    return project


def available(client, project) -> dict[str, str]:
    listed = client.get(url(project, "/fixes")).json()["fixes"]
    return {f["fix_id"]: f["finding_id"] for f in listed if f["available"]}


def test_the_three_fixes_are_offered(client, program):
    assert {"OPT-04", "OPT-05", "OPT-07"} <= set(available(client, program))


@pytest.mark.parametrize("fix_id", ["OPT-04", "OPT-05", "OPT-07"])
def test_each_fix_keeps_the_output_and_the_gate_green(client, program, fix_id):
    expected = outputs(client, program)
    finding = available(client, program)[fix_id]
    versions = client.get(url(program, "/versions")).json()
    response = client.post(
        url(program, f"/fixes/{quote(finding, safe=chr(47))}/apply"),
        json={"base_version_id": versions["head_version_id"]},
    )
    assert response.status_code == 202, response.text
    info = wait_run(client, response.json()["run_id"], 180)
    assert info["summary"]["version_status"] == "passed", info["summary"]
    assert info["summary"]["data"]["candidate"] == "promoted"
    assert outputs(client, program) == expected
    assert fix_id not in available(client, program)


def test_batch_optimization_applies_them_in_turn(client, program):
    expected = outputs(client, program)
    plan = client.post(url(program, "/optimize/plan"), json={}).json()
    assert {"OPT-04", "OPT-05", "OPT-07"} <= {s["fix_id"] for s in plan["steps"]}
    versions = client.get(url(program, "/versions")).json()
    response = client.post(
        url(program, "/optimize/apply"),
        json={
            "base_version_id": versions["head_version_id"],
            "step_ids": [s["step_id"] for s in plan["steps"]],
        },
    )
    assert response.status_code == 202, response.text
    info = wait_run(client, response.json()["run_id"], 300)
    assert info["status"] == "done", info
    assert outputs(client, program) == expected


# --- OPT-08, OPT-11, OPT-12 -----------------------------------------------------------------------------------

SECOND = """\
import java.util.*;
import java.util.stream.*;

public class Main {
    static long fib(int k) {
        if (k < 2) {
            return k;
        }
        return fib(k - 1) + fib(k - 2);
    }

    public static void main(String[] args) {
        Scanner in = new Scanner(System.in);
        List<String> words = new ArrayList<>();
        while (in.hasNext()) {
            words.add(in.next());
        }
        Integer total = 0;
        for (String w : words) {
            total += w.length();
        }
        List<String> kept = words.stream().sorted().filter(w -> w.length() > 2).collect(Collectors.toList());
        System.out.println(total + " " + kept + " " + fib(Math.min(total, 25)));
    }
}
"""
SECOND_INPUTS = ["a bb ccc dddd\n", "", "hello world\n", "x\n"]


@pytest.fixture
def second(client, make_project):
    project = make_project({"Main.java": SECOND}, name="more-fixes-2")
    for index, stdin in enumerate(SECOND_INPUTS):
        client.post(url(project, "/scenarios"), json={"name": f"t{index}", "stdin": stdin})
    assert run_kind(client, project, "baseline")["status"] == "done"
    return project


def second_outputs(client, project) -> list[str]:
    return [run_kind(client, project, "run", stdin=s)["items"][0]["stdout"] for s in SECOND_INPUTS]


@pytest.mark.parametrize("fix_id", ["OPT-08", "OPT-11", "OPT-12"])
def test_second_group_keeps_output_and_gate(client, second, fix_id):
    assert fix_id in available(client, second)
    expected = second_outputs(client, second)
    finding = available(client, second)[fix_id]
    versions = client.get(url(second, "/versions")).json()
    response = client.post(
        url(second, f"/fixes/{quote(finding, safe=chr(47))}/apply"),
        json={"base_version_id": versions["head_version_id"]},
    )
    assert response.status_code == 202, response.text
    info = wait_run(client, response.json()["run_id"], 240)
    assert info["summary"]["version_status"] == "passed", info["summary"]
    assert info["summary"]["data"]["candidate"] == "promoted"
    assert second_outputs(client, second) == expected
    assert fix_id not in available(client, second)
