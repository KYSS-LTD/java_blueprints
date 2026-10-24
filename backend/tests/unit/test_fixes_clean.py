"""Исправления FIX-03/07/09/10 и CLEAN-01/02/03 (S8.5): правки, отказы по предусловиям, идемпотентность."""

from __future__ import annotations

from engine.analysis.analyzer import analyze
from engine.java.project import JavaProject
from engine.transform.base import apply_proposal
from engine.transform.fixes import propose


def project_of(
    body: str,
    members: str = "",
    params: str = "List<String> items, int n",
    throws: str = "",
) -> JavaProject:
    source = (
        "import java.util.*;\nimport java.io.*;\n"
        f"class A {{\n{members}\n    void run({params}){throws} {{\n{body}\n    }}\n}}\n"
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


def refusal(project: JavaProject, rule: str, index: int = 0) -> str:
    proposal = propose(project, finding_of(project, rule, index))
    assert proposal is not None and not proposal.available
    return " ".join(c.reason or "" for c in proposal.failed())


def rule_ids(text: str) -> set[str]:
    return {f.rule_id for f in analyze(JavaProject({"A.java": text})).findings}


def behaviour(project: JavaProject, rule: str) -> bool:
    proposal = propose(project, finding_of(project, rule))
    assert proposal is not None
    return proposal.behavior_preserving


# --- FIX-07 --------------------------------------------------------------------------------------------------


def test_fix_07_assigns_the_discarded_result():
    project = project_of(
        "        String text = items.get(0);\n        text.trim();\n        System.out.println(text);"
    )
    after = fixed(project, "BUG-07")
    assert "        text = text.trim();" in after and "BUG-07" not in rule_ids(after)
    assert behaviour(project, "BUG-07") is False


def test_fix_07_refuses_final_variables():
    project = project_of(
        "        final String text = items.get(0);\n        text.trim();\n        System.out.println(text);"
    )
    assert "final" in refusal(project, "BUG-07")


# --- FIX-09 --------------------------------------------------------------------------------------------------


def test_fix_09_turns_removal_in_the_loop_into_remove_if():
    project = project_of(
        "        for (String s : items) {\n"
        "            if (s.length() > n) {\n"
        "                items.remove(s);\n"
        "            }\n"
        "        }"
    )
    after = fixed(project, "BUG-09")
    assert "        items.removeIf(s -> s.length() > n);" in after
    assert "BUG-09" not in rule_ids(after)


def test_fix_09_refuses_other_loop_bodies_and_primitive_elements():
    busy = project_of(
        "        for (String s : items) {\n"
        "            System.out.println(s);\n"
        "            if (s.length() > n) {\n"
        "                items.remove(s);\n"
        "            }\n"
        "        }"
    )
    assert "теле цикла" in refusal(busy, "BUG-09")
    primitive = project_of(
        "        for (int v : numbers) {\n"
        "            if (v > n) {\n"
        "                numbers.remove(v);\n"
        "            }\n"
        "        }",
        params="List<Integer> numbers, int n",
    )
    assert "индексу" in refusal(primitive, "BUG-09")


# --- FIX-10 --------------------------------------------------------------------------------------------------


SWITCH = (
    "        switch (n) {\n"
    "            case 1:\n"
    '                System.out.println("one");\n'
    "            case 2:\n"
    '                System.out.println("two");\n'
    "                break;\n"
    "        }"
)


def test_fix_10_adds_default_and_break_for_fall_through():
    after = fixed(project_of(SWITCH), "BUG-10")
    assert (
        '                System.out.println("one");\n                break;\n            case 2:'
        in after
    )
    assert "            default:\n                break;\n        }" in after
    assert "BUG-10" not in rule_ids(after)


def test_fix_10_flags_behaviour_change():
    assert behaviour(project_of(SWITCH), "BUG-10") is False


# --- FIX-03 --------------------------------------------------------------------------------------------------


READER = (
    '        BufferedReader in = new BufferedReader(new FileReader("data.txt"));\n'
    "        String line = in.readLine();\n"
    "        System.out.println(line);"
)


def test_fix_03_wraps_the_rest_of_the_block_in_try_with_resources():
    after = fixed(project_of(READER, throws=" throws IOException"), "BUG-03")
    assert 'try (BufferedReader in = new BufferedReader(new FileReader("data.txt"))) {' in after
    assert (
        "            String line = in.readLine();\n            System.out.println(line);\n        }"
        in after
    )
    assert "BUG-03" not in rule_ids(after)


def test_fix_03_refuses_when_close_exception_is_not_handled():
    assert "IOException" in refusal(project_of(READER), "BUG-03")


# --- CLEAN-02 ------------------------------------------------------------------------------------------------


def test_clean_02_removes_an_unused_private_method_and_local_variable():
    members = "    private int helper(int x) {\n        return x * 2;\n    }\n"
    project = project_of("        n++;", members)
    after = fixed(project, "MAINT-07")
    assert "helper" not in after and "MAINT-07" not in rule_ids(after)
    local = project_of("        int unused = 5;\n        n++;")
    after_local = fixed(local, "MAINT-07")
    assert "unused" not in after_local


def test_clean_02_keeps_the_call_of_an_unused_initialiser():
    project = project_of("        int ignored = items.size();\n        n++;")
    after = fixed(project, "MAINT-07")
    assert "        items.size();" in after and "ignored" not in after


# --- CLEAN-01 ------------------------------------------------------------------------------------------------


def test_clean_01_extracts_a_repeated_number_into_a_constant():
    body = "        if (n > 100) {\n            n = 100;\n        }\n        System.out.println(n * 100);"
    after = fixed(project_of(body), "MAINT-06")
    assert "    private static final int VALUE_100 = 100;" in after
    assert "if (n > VALUE_100)" in after and "n = VALUE_100;" in after
    assert "MAINT-06" not in rule_ids(after)


def test_clean_01_handles_strings():
    body = '        System.out.println("something happened");\n        System.out.println("something happened");\n        System.out.println("something happened");'
    after = fixed(project_of(body), "MAINT-06")
    assert '    private static final String TEXT_1 = "something happened";' in after
    assert after.count("TEXT_1") == 4


# --- CLEAN-03 ------------------------------------------------------------------------------------------------


def test_clean_03_renames_by_convention_everywhere():
    members = "    int Bad_Method(int x) {\n        return x + 1;\n    }\n"
    project = project_of("        n = Bad_Method(n) + Bad_Method(2);", members)
    after = fixed(project, "MAINT-10")
    assert "int badMethod(int x)" in after
    assert "n = badMethod(n) + badMethod(2);" in after
    assert "Bad_Method" not in after and "MAINT-10" not in rule_ids(after)


def test_clean_03_refuses_taken_names():
    members = "    int Bad_Method(int x) {\n        return x;\n    }\n    int badMethod() {\n        return 1;\n    }\n"
    project = project_of("        n = Bad_Method(n);", members)
    assert "занято" in refusal(project, "MAINT-10")
