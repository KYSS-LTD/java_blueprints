"""Автоисправления OPT-04, OPT-05, OPT-07 (S8.3): правки, отказы по предусловиям, идемпотентность."""

from __future__ import annotations

from engine.analysis.analyzer import analyze
from engine.java.project import JavaProject
from engine.transform.base import apply_proposal
from engine.transform.fixes import propose


def project_of(
    body: str, members: str = "", params: str = "List<String> items, int n"
) -> JavaProject:
    source = (
        "import java.util.*;\n"
        f"class A {{\n{members}\n    void run({params}) {{\n{body}\n    }}\n}}\n"
    )
    return JavaProject({"A.java": source})


def finding_of(project: JavaProject, rule: str, index: int = 0):
    return [f for f in analyze(project).findings if f.rule_id == rule][index]


def fixed(project: JavaProject, rule: str, index: int = 0) -> str:
    finding = finding_of(project, rule, index)
    proposal = propose(project, finding)
    assert proposal is not None and proposal.available, (
        [c.reason for c in proposal.failed()] if proposal else None
    )
    return apply_proposal(project, proposal)["A.java"]


def refusal(project: JavaProject, rule: str) -> str:
    proposal = propose(project, finding_of(project, rule))
    assert proposal is not None and not proposal.available
    return " ".join(c.reason or "" for c in proposal.failed())


def rule_ids(text: str) -> set[str]:
    return {f.rule_id for f in analyze(JavaProject({"A.java": text})).findings}


# --- OPT-05 --------------------------------------------------------------------------------------------------


def test_opt_05_hoists_a_string_call_out_of_the_loop():
    project = project_of(
        "        String text = items.get(0);\n"
        "        for (int i = 0; i < n; i++) {\n"
        "            n += text.toLowerCase().length();\n"
        "        }"
    )
    after = fixed(project, "PERF-05")
    assert (
        "        String lower = text.toLowerCase();\n        for (int i = 0; i < n; i++) {" in after
    )
    assert "            n += lower.length();" in after
    assert "PERF-05" not in rule_ids(after)


def test_opt_05_array_results_only_when_read_only():
    project = project_of(
        "        String text = items.get(0);\n"
        "        for (int i = 0; i < n; i++) {\n"
        "            n += text.toCharArray().length;\n"
        "        }"
    )
    after = fixed(project, "PERF-05")
    assert (
        "        char[] letters = text.toCharArray();" in after and "n += letters.length;" in after
    )
    mutated = project_of(
        "        String text = items.get(0);\n"
        "        for (int i = 0; i < n; i++) {\n"
        "            char[] copy = text.toCharArray();\n"
        "            copy[0] = 'x';\n"
        "            n += copy.length;\n"
        "        }"
    )
    assert "массив" in refusal(mutated, "PERF-05")


def test_opt_05_collections_max_takes_the_element_type_from_the_declaration():
    project = project_of(
        "        for (int i = 0; i < n; i++) {\n"
        "            n += Collections.max(numbers) + i;\n"
        "        }",
        params="List<Integer> numbers, int n",
    )
    after = fixed(project, "PERF-05")
    assert "        Integer largest = Collections.max(numbers);" in after
    assert "n += largest + i;" in after


# --- OPT-04 --------------------------------------------------------------------------------------------------


def test_opt_04_moves_the_sort_before_the_loop():
    project = project_of(
        "        for (int i = 0; i < n; i++) {\n"
        "            Collections.sort(items);\n"
        "            System.out.println(items.get(0) + i);\n"
        "        }"
    )
    after = fixed(project, "PERF-04")
    assert "        Collections.sort(items);\n        for (int i = 0; i < n; i++) {" in after
    assert after.count("Collections.sort(items)") == 1
    assert "PERF-04" not in rule_ids(after)


def test_opt_04_refuses_when_the_collection_changes_or_is_used_after():
    changing = project_of(
        "        for (int i = 0; i < n; i++) {\n"
        '            items.add("x");\n'
        "            Collections.sort(items);\n"
        "        }"
    )
    assert "изменяется" in refusal(changing, "PERF-04")
    used_after = project_of(
        "        for (int i = 0; i < n; i++) {\n"
        "            Collections.sort(items);\n"
        "        }\n"
        "        System.out.println(items);"
    )
    assert "после цикла" in refusal(used_after, "PERF-04")
    read_before = project_of(
        "        for (int i = 0; i < n; i++) {\n"
        "            n += items.size();\n"
        "            Collections.sort(items);\n"
        "        }"
    )
    assert "до sort" in refusal(read_before, "PERF-04")


# --- OPT-07 --------------------------------------------------------------------------------------------------


def test_opt_07_stores_the_repeated_expression_in_a_variable():
    project = project_of(
        "        double a = Math.sqrt(n) + 1;\n"
        "        double b = Math.sqrt(n) * 2;\n"
        "        System.out.println(a + b);"
    )
    after = fixed(project, "PERF-07")
    assert "        double root = Math.sqrt(n);\n        double a = root + 1;" in after
    assert "        double b = root * 2;" in after
    assert "PERF-07" not in rule_ids(after)


def test_opt_07_refuses_conditional_first_evaluation():
    project = project_of(
        "        double a = n > 0 ? Math.sqrt(n) : 0;\n"
        "        double b = Math.sqrt(n) * 2;\n"
        "        System.out.println(a + b);"
    )
    assert "условной" in refusal(project, "PERF-07")


def test_opt_07_handles_a_repeat_inside_a_nested_block():
    project = project_of(
        "        int k = Integer.parseInt(items.get(0));\n"
        "        int m = Integer.parseInt(items.get(0)) + 1;\n"
        "        if (k > m) {\n"
        "            System.out.println(Integer.parseInt(items.get(0)));\n"
        "        }"
    )
    after = fixed(project, "PERF-07")
    assert after.count("Integer.parseInt(items.get(0))") == 1
    assert "System.out.println(number);" in after
