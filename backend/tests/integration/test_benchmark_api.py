"""Бенчмарк и «Проверить» у находок (S6.10, S7.7; метка `jdk`): показатель роста k на фикстурах."""

from __future__ import annotations

from urllib.parse import quote

import pytest

from tests.integration.conftest import read_sse, wait_run

pytestmark = pytest.mark.jdk

READ_ARRAY = """\
import java.util.*;

public class Main {
    public static void main(String[] args) {
        Scanner in = new Scanner(System.in);
        int n = in.nextInt();
        int[] a = new int[n];
        for (int i = 0; i < n; i++) {
            a[i] = in.nextInt();
        }
%s
    }
}
"""
QUADRATIC = READ_ARRAY % (
    "        long same = 0;\n"
    "        for (int i = 0; i < n; i++) {\n"
    "            for (int j = 0; j < n; j++) {\n"
    "                if (a[i] == a[j]) {\n"
    "                    same++;\n"
    "                }\n"
    "            }\n"
    "        }\n"
    "        System.out.println(same);"
)
LINEAR = READ_ARRAY % (
    "        long sum = 0;\n"
    "        for (int i = 0; i < n; i++) {\n"
    "            sum += a[i];\n"
    "        }\n"
    "        System.out.println(sum);"
)
CONTAINS = """\
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
SIZES = [1000, 2000, 4000, 8000]


def url(project, suffix: str) -> str:
    return f"/api/projects/{project['id']}{suffix}"


def benchmark(client, project, **body) -> dict:
    response = client.post(
        url(project, "/runs"), json={"kind": "benchmark", "sizes": SIZES, **body}
    )
    assert response.status_code == 202, response.text
    return wait_run(client, response.json()["run_id"], 240)


def verify(client, project, finding_id: str) -> dict:
    response = client.post(url(project, f"/findings/{quote(finding_id, safe='/')}/verify"))
    assert response.status_code == 202, response.text
    return wait_run(client, response.json()["run_id"], 240)


def test_quadratic_program_has_k_close_to_two(client, make_project):
    info = benchmark(client, make_project({"Main.java": QUADRATIC}, name="q"))
    assert info["status"] == "done", info["summary"]
    data = info["summary"]["data"]
    assert 1.7 <= data["growth_k"] <= 2.3, data
    assert "O(n²)" in info["summary"]["message"]
    assert [s["size"] for s in data["samples"]][: len(SIZES)] == SIZES  # дальше — удвоения
    assert all(s["hits"] for s in data["samples"])
    assert data["samples"][-1]["hits"] > 10 * data["samples"][0]["hits"]


def test_linear_program_has_k_close_to_one(client, make_project):
    info = benchmark(client, make_project({"Main.java": LINEAR}, name="l"))
    data = info["summary"]["data"]
    assert 0.8 <= data["growth_k"] <= 1.25, data
    assert "O(n)" in info["summary"]["message"] and data["basis"] == "hits"


def test_events_and_scaled_template_scenarios(client, make_project):
    project = make_project({"Main.java": LINEAR}, name="tpl")
    client.post(
        url(project, "/scenarios/import"),
        json={"scenarios": [{"name": "tpl", "stdin": "${N}\n" + "1 " * 0 + "1\n" * 0}]},
    )
    info = benchmark(client, project)
    events = read_sse(client, info["id"])
    kinds = [e["event"] for e in events]
    assert kinds.count("item") >= len(SIZES) and "progress" in kinds and kinds[-1] == "done"


def test_program_that_ignores_input_gets_an_honest_warning(client, make_project):
    program = (
        "import java.util.*;\npublic class Main { public static void main(String[] a) {\n"
        "Scanner in = new Scanner(System.in);\nint n = in.nextInt();\nSystem.out.println(n);\n} }\n"
    )
    info = benchmark(client, make_project({"Main.java": program}, name="flat"))
    warnings = info["summary"]["data"]["warnings"]
    assert any("не влияет" in w for w in warnings), info["summary"]


def test_program_without_scalable_input_asks_for_a_template(client, make_project):
    program = (
        'public class Main { public static void main(String[] a) { System.out.println("x"); } }'
    )
    info = benchmark(client, make_project({"Main.java": program}, name="none"))
    assert info["status"] == "failed" and "${N}" in info["summary"]["message"]


def test_verify_confirms_a_nested_loop_finding(client, make_project):
    project = make_project({"Main.java": QUADRATIC}, name="verify")
    findings = client.get(url(project, "/findings")).json()["findings"]
    perf = next(f for f in findings if f["rule_id"] == "PERF-01")
    info = verify(client, project, perf["id"])
    assert info["status"] == "done", info["summary"]
    data = info["summary"]["data"]
    assert data["finding_id"] == perf["id"] and 1.6 <= data["growth_k"] <= 2.4
    assert "статически O(n²)" in info["summary"]["message"]
    assert data["warnings"] == [] or all("расходится" not in w for w in data["warnings"])

    card = client.get(url(project, f"/findings/{quote(perf['id'], safe='/')}")).json()
    dynamic = card["evidence"]["dynamic"]
    assert dynamic["run_id"] == info["id"] and 1.6 <= dynamic["growth_k"] <= 2.4
    assert [s["size"] for s in dynamic["samples"]][:4] == [1000, 2000, 4000, 8000]
    assert dynamic["verdict"].startswith("k ≈")


def test_verify_measures_jdk_call_cost_by_time(client, make_project):
    project = make_project({"Main.java": CONTAINS}, name="contains")
    findings = client.get(url(project, "/findings")).json()["findings"]
    perf = next(f for f in findings if f["rule_id"] == "PERF-02")
    # замер времени чувствителен к нагрузке машины: допускаем до трёх попыток
    for _ in range(3):
        data = verify(client, project, perf["id"])["summary"]["data"]
        if data["basis"] == "time" and 1.3 <= data["growth_k"] <= 2.8:
            break
    assert data["basis"] == "time" and 1.3 <= data["growth_k"] <= 2.8, data


def test_verify_of_a_defect_reports_reachability_honestly(client, make_project):
    program = """\
public class Main {
    public static void main(String[] args) {
        String a = new String("x");
        String b = args.length > 0 ? args[0] : new String("x");
        if (a == b) {
            System.out.println("same");
        }
    }
}
"""
    project = make_project({"Main.java": program}, name="bug")
    bug = next(
        f
        for f in client.get(url(project, "/findings")).json()["findings"]
        if f["rule_id"] == "BUG-01"
    )
    first = verify(client, project, bug["id"])
    assert "не подтверждено" in first["summary"]["message"]  # сценариев нет
    client.post(url(project, "/scenarios"), json={"name": "plain"})
    second = verify(client, project, bug["id"])
    assert second["summary"]["data"]["reached_by"] == 1


def test_verify_unknown_finding_is_404(client, make_project):
    project = make_project({"Main.java": LINEAR}, name="x")
    response = client.post(url(project, "/findings/PERF-01:Nope/verify"))
    assert response.status_code == 404
