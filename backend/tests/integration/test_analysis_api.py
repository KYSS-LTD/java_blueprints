"""Анализ через API (S7.2, S7.4): находки с фильтрами, карточка, сложность узла и диапазона, метрики."""

from __future__ import annotations

from urllib.parse import quote

import pytest

PAIRS = """\
import java.util.*;

public class Analyzer {
    static int analyze(List<String> items) {
        int merged = 0;
        for (String a : items) {
            for (String b : items) {
                if (a.equals(b)) {
                    merged++;
                }
            }
        }
        return merged;
    }

    public static void main(String[] args) {
        String text = "";
        System.out.println(analyze(List.of("a", "b")));
        if (text == "x") {
            System.out.println("same");
        }
    }
}
"""


@pytest.fixture
def pairs(make_project):
    return make_project({"Analyzer.java": PAIRS}, name="pairs")


def url(project, suffix: str) -> str:
    return f"/api/projects/{project['id']}{suffix}"


def test_analysis_returns_findings_summary_complexity_and_metrics(client, pairs):
    body = client.get(url(pairs, "/analysis")).json()
    assert body["version_id"] == pairs["head_version_id"]
    rules = [f["rule_id"] for f in body["findings"]]
    assert rules == ["PERF-01", "BUG-01"]  # важность, затем сложность
    assert body["summary"] == {
        "total": 2,
        "by_severity": {"high": 2},
        "by_category": {"PERF": 1, "BUG": 1},
        "fixable": 1,
    }
    assert body["complexity"] == {"current": "O(n²)", "optimized": "O(n)"}
    totals = body["metrics"]["totals"]
    assert totals["files"] == 1 and totals["methods"] == 2

    perf = body["findings"][0]
    assert perf["location"]["file"] == "Analyzer.java"
    assert perf["complexity"]["current"] == "O(n²)" and perf["complexity"]["after"] == "O(n)"
    assert perf["evidence"] == {"static": perf["evidence"]["static"], "dynamic": None}
    assert perf["fix"]["fix_id"] == "OPT-01" and perf["fix"]["available"] is False
    assert (
        perf["fix"]["preconditions"][0]["passed"] is False
        and perf["fix"]["preconditions"][0]["reason"]
    )
    bug = body["findings"][1]
    assert (bug["fix"]["fix_id"], bug["fix"]["available"], bug["fix"]["behavior_preserving"]) == (
        "FIX-01",
        True,
        False,
    )


def test_findings_filters(client, pairs):
    def ids(**params) -> list[str]:
        response = client.get(url(pairs, "/findings"), params=params)
        assert response.status_code == 200, response.text
        return [f["rule_id"] for f in response.json()["findings"]]

    assert ids() == ["PERF-01", "BUG-01"]
    assert ids(category="BUG") == ["BUG-01"]
    assert ids(severity="low") == []
    assert ids(file="Analyzer.java") == ["PERF-01", "BUG-01"]
    assert ids(file="Other.java") == []
    assert ids(fixable=True) == ["BUG-01"]
    assert ids(fixable=False) == ["PERF-01"]
    assert client.get(url(pairs, "/findings"), params={"severity": "huge"}).status_code == 400


def test_finding_card_by_id_with_special_characters(client, pairs):
    [first, _] = client.get(url(pairs, "/findings")).json()["findings"]
    assert first["id"].startswith("PERF-01:Analyzer.java#Analyzer.analyze(List<String>)/foreach[0]")
    response = client.get(url(pairs, f"/findings/{quote(first['id'], safe='/')}"))
    assert response.status_code == 200, response.text
    card = response.json()
    assert card["id"] == first["id"]
    assert card["explanation"] and card["defect"] and card["risk"] and card["recommendation"]
    assert [c["factor"] for c in card["complexity"]["contributions"]] == ["n", "n"]

    missing = client.get(url(pairs, "/findings/PERF-01:Nope"))
    assert missing.status_code == 404
    assert missing.json()["error"]["code"] == "NOT_FOUND"


def test_complexity_of_node_range_and_errors(client, pairs):
    method = "Analyzer.java#Analyzer.analyze(List<String>)"
    outer = client.get(url(pairs, "/complexity"), params={"node_id": f"{method}/foreach[0]"}).json()
    assert outer["node_id"] == f"{method}/foreach[0]"
    assert outer["estimate"]["current"] == "O(n²)" and outer["estimate"]["after"] == "O(n)"
    assert outer["range"]["start_line"] == 6
    inner = client.get(
        url(pairs, "/complexity"), params={"node_id": f"{method}/foreach[0]/foreach[0]"}
    ).json()
    assert inner["estimate"]["current"] == "O(n)"

    whole = client.get(url(pairs, "/complexity"), params={"node_id": method}).json()
    assert whole["estimate"]["current"] == "O(n²)"

    by_range = client.get(
        url(pairs, "/complexity"), params={"file": "Analyzer.java", "start_line": 6, "end_line": 12}
    ).json()
    assert by_range["estimate"]["current"] == "O(n²)"
    assert by_range["range"] == {
        "file": "Analyzer.java",
        "start_line": 6,
        "start_col": 1,
        "end_line": 12,
        "end_col": 1,
    }
    line_only = client.get(
        url(pairs, "/complexity"), params={"file": "Analyzer.java", "start_line": 5, "end_line": 5}
    ).json()
    assert line_only["estimate"]["current"] == "O(1)"

    assert client.get(url(pairs, "/complexity")).status_code == 400
    assert (
        client.get(
            url(pairs, "/complexity"),
            params={"file": "Analyzer.java", "start_line": 9, "end_line": 3},
        ).status_code
        == 400
    )
    assert (
        client.get(url(pairs, "/complexity"), params={"node_id": "Nope.java#X.y()"}).status_code
        == 404
    )
    assert (
        client.get(
            url(pairs, "/complexity"), params={"file": "Nope.java", "start_line": 1, "end_line": 2}
        ).status_code
        == 404
    )


def test_metrics_and_comparison_with_another_version(client, pairs):
    fixed = PAIRS.replace(
        "for (String b : items) {\n                if (a.equals(b)) {\n                    merged++;\n                }\n            }",
        "merged++;",
    )
    changed = client.put(
        url(pairs, "/files/Analyzer.java"),
        json={"base_version_id": pairs["head_version_id"], "content": fixed},
    ).json()
    response = client.get(
        url(pairs, "/metrics"), params={"compare_to": pairs["original_version_id"]}
    ).json()
    assert response["version_id"] == changed["version"]["id"]
    assert response["compare_to_version_id"] == pairs["original_version_id"]
    after = {m["name"]: m for m in response["metrics"]["methods"]}
    before = {m["name"]: m for m in response["compare_to"]["methods"]}
    assert before["analyze"]["complexity"] == "O(n²)" and after["analyze"]["complexity"] == "O(n)"
    assert before["analyze"]["max_nesting"] == 3 and after["analyze"]["max_nesting"] == 1
    assert (
        response["metrics"]["totals"]["code_lines"] < response["compare_to"]["totals"]["code_lines"]
    )

    plain = client.get(url(pairs, "/metrics")).json()
    assert plain["compare_to"] is None and plain["compare_to_version_id"] is None
    assert client.get(url(pairs, "/metrics"), params={"compare_to": 999}).status_code == 404


def test_thresholds_come_from_project_settings(client, make_project):
    body = "\n".join(f"        n += {i};" for i in range(40))
    project = make_project(
        {"A.java": f"class A {{\n    void run(int n) {{\n{body}\n    }}\n}}\n"}, name="long"
    )
    assert [f["rule_id"] for f in client.get(url(project, "/findings")).json()["findings"]] == [
        "MAINT-01"
    ]
    client.patch(
        url(project, ""), json={"settings": {"rule_thresholds": {"max_method_lines": 100}}}
    )
    assert client.get(url(project, "/findings")).json()["findings"] == []


def test_analysis_follows_the_requested_version(client, pairs):
    clean = "class Analyzer { int f(int x) { return x + 1; } }"
    client.put(
        url(pairs, "/files/Analyzer.java"),
        json={"base_version_id": pairs["head_version_id"], "content": clean},
    )
    assert client.get(url(pairs, "/analysis")).json()["findings"] == []
    old = client.get(
        url(pairs, "/analysis"), params={"version_id": pairs["original_version_id"]}
    ).json()
    assert len(old["findings"]) == 2


def test_graphs_carry_complexity_and_defect_badges(client, pairs):
    method = "Analyzer.java#Analyzer.analyze(List<String>)"
    flow = client.get(url(pairs, "/flow"), params={"method_id": method}).json()
    nodes = {n["id"]: n for n in flow["nodes"]}
    assert nodes[method]["complexity"] == "O(n²)"
    assert nodes[f"{method}/foreach[0]"]["complexity"] == "O(n²)"
    assert nodes[f"{method}/foreach[0]/foreach[0]"]["complexity"] == "O(n)"
    badge = nodes[f"{method}/foreach[0]"]["badges"][-1]
    assert (badge["kind"], badge["label"]) == ("defect", "PERF-01")
    assert badge["ref"].startswith("PERF-01:")
    assert nodes[f"{method}/foreach[0]/foreach[0]/if[0]"]["complexity"] is None

    structure = client.get(url(pairs, "/structure")).json()
    members = {n["id"]: n for n in structure["nodes"]}
    assert members[method]["complexity"] == "O(n²)" and members[method]["findings_count"] == 1
    assert members["Analyzer.java#Analyzer.main(String[])"]["findings_count"] == 1

    tree = client.get(url(pairs, "/tree")).json()

    def walk(nodes):
        for node in nodes:
            yield node
            yield from walk(node["children"])

    by_name = {n["name"]: n for n in walk(tree["roots"])}
    assert by_name["Analyzer.java"]["findings"] == {"high": 2, "medium": 0, "low": 0}
    assert by_name["analyze(List<String>)"]["complexity"] == "O(n²)"
    assert by_name["analyze(List<String>)"]["findings"]["high"] == 1


def test_verify_of_an_unknown_finding_is_404(client, pairs):
    response = client.post(url(pairs, "/findings/PERF-01:x/verify"))
    assert response.status_code == 404
