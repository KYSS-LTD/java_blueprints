"""Автоисправления OPT-02/03/06, FIX-01/02 (S8): правки, предусловия, идемпотентность."""

from __future__ import annotations

import pytest

from engine.analysis.analyzer import analyze
from engine.java.project import JavaProject
from engine.transform.base import apply_proposal
from engine.transform.fixes import propose


def project_of(body: str, members: str = "", imports: str = "import java.util.*;") -> JavaProject:
    source = f"{imports}\nclass A {{\n{members}\n    void run(List<String> items, int n) {{\n{body}\n    }}\n}}\n"
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


# --- OPT-03 ---------------------------------------------------------------------------------------------------


def test_opt_03_replaces_concatenation_with_a_builder():
    project = project_of(
        '        String text = "";\n'
        "        for (String s : items) {\n"
        '            text += s + ",";\n'
        "        }\n"
        "        System.out.println(text);"
    )
    after = fixed(project, "PERF-03")
    assert (
        "        StringBuilder textBuilder = new StringBuilder(text);\n        for (String s : items) {"
        in after
    )
    assert '            textBuilder.append(s + ",");' in after
    assert "        text = textBuilder.toString();\n        System.out.println(text);" in after
    assert not [
        f for f in analyze(JavaProject({"A.java": after})).findings if f.rule_id == "PERF-03"
    ]


def test_opt_03_handles_the_assignment_form_and_keeps_operand_order():
    project = project_of(
        '        String r = "x";\n'
        "        for (int i = 0; i < n; i++) {\n"
        '            r = r + i + ";";\n'
        "        }\n"
        "        System.out.println(r);"
    )
    after = fixed(project, "PERF-03")
    assert '            rBuilder.append(i).append(";");' in after
    assert "new StringBuilder(r);" in after


def test_opt_03_refuses_when_the_string_is_read_in_the_loop_or_not_a_literal():
    read_in_loop = project_of(
        '        String t = "";\n'
        "        for (String s : items) {\n"
        "            t += s;\n"
        "            if (t.length() > 5) { n++; }\n"
        "        }\n"
        "        System.out.println(t);"
    )
    assert "читается в цикле" in refusal(read_in_loop, "PERF-03")
    not_literal = project_of(
        "        String t = items.get(0);\n"
        "        for (String s : items) {\n"
        "            t += s;\n"
        "        }\n"
        "        System.out.println(t);"
    )
    assert "литералом" in refusal(not_literal, "PERF-03")
    used_in_catch = project_of(
        '        String t = "";\n'
        "        try {\n"
        "            for (String s : items) {\n"
        "                t += s;\n"
        "            }\n"
        "        } catch (RuntimeException e) {\n"
        "            System.out.println(t);\n"
        "        }"
    )
    assert "catch/finally" in refusal(used_in_catch, "PERF-03") or "в том же блоке" in refusal(
        used_in_catch, "PERF-03"
    )


# --- OPT-02 ---------------------------------------------------------------------------------------------------


def test_opt_02_adds_a_parallel_hash_set_and_keeps_the_list():
    project = project_of(
        "        List<String> seen = new ArrayList<>();\n"
        "        for (String s : items) {\n"
        "            if (!seen.contains(s)) {\n"
        "                seen.add(s);\n"
        "            }\n"
        "        }\n"
        "        System.out.println(seen);"
    )
    after = fixed(project, "PERF-02")
    assert (
        "        List<String> seen = new ArrayList<>();\n        Set<String> seenSet = new HashSet<>(seen);"
        in after
    )
    assert "if (!seenSet.contains(s)) {" in after
    assert "seen.add(s); seenSet.add(s);" in after
    assert "import java.util.*;" in after  # java.util.* уже покрывает Set и HashSet
    assert not [
        f for f in analyze(JavaProject({"A.java": after})).findings if f.rule_id == "PERF-02"
    ]


def test_opt_02_adds_imports_when_only_specific_classes_are_imported():
    project = project_of(
        "        List<String> seen = new ArrayList<>();\n"
        "        for (String s : items) {\n"
        "            if (!seen.contains(s)) { seen.add(s); }\n"
        "        }",
        imports="import java.util.List;\nimport java.util.ArrayList;",
    )
    after = fixed(project, "PERF-02")
    assert "import java.util.HashSet;\nimport java.util.Set;\n" in after


def test_opt_02_refuses_unsafe_list_usage_and_unhashable_elements():
    mutated = project_of(
        "        List<String> seen = new ArrayList<>();\n"
        "        for (String s : items) {\n"
        "            if (!seen.contains(s)) { seen.add(s); }\n"
        "            seen.remove(0);\n"
        "        }"
    )
    assert "remove" in refusal(mutated, "PERF-02")
    passed_on = project_of(
        "        List<String> seen = new ArrayList<>();\n"
        "        for (String s : items) {\n"
        "            if (!seen.contains(s)) { seen.add(s); }\n"
        "        }\n"
        "        consume(seen);",
        "    void consume(List<String> l) { }",
    )
    assert "передаётся" in refusal(passed_on, "PERF-02")
    unhashable = project_of(
        "        List<Box> seen = new ArrayList<>();\n"
        "        for (String s : items) {\n"
        "            if (!seen.contains(new Box())) { seen.add(new Box()); }\n"
        "        }",
        "    static class Box { }",
    )
    assert "equals/hashCode" in refusal(unhashable, "PERF-02")
    as_expression = project_of(
        "        List<String> seen = new ArrayList<>();\n"
        "        for (String s : items) {\n"
        "            if (!seen.contains(s) && seen.add(s)) { n++; }\n"
        "        }"
    )
    assert "add" in refusal(as_expression, "PERF-02")


# --- OPT-06 ---------------------------------------------------------------------------------------------------


def test_opt_06_turns_linked_list_into_array_list():
    project = project_of(
        "        LinkedList<String> l = new LinkedList<>(items);\n"
        "        for (int i = 0; i < l.size(); i++) {\n"
        "            n += l.get(i).length();\n"
        "        }"
    )
    after = fixed(project, "PERF-06")
    assert "ArrayList<String> l = new ArrayList<>(items);" in after and "LinkedList" not in after
    assert not [
        f for f in analyze(JavaProject({"A.java": after})).findings if f.rule_id == "PERF-06"
    ]


def test_opt_06_refuses_deque_methods_and_front_insert():
    deque = project_of(
        "        LinkedList<String> l = new LinkedList<>(items);\n"
        '        l.addFirst("x");\n'
        "        for (int i = 0; i < l.size(); i++) {\n"
        "            n += l.get(i).length();\n"
        "        }"
    )
    assert "addFirst" in refusal(deque, "PERF-06")
    front = project_of(
        "        ArrayList<String> a = new ArrayList<>();\n"
        "        for (String s : items) { a.add(0, s); }"
    )
    assert "ArrayDeque" in refusal(front, "PERF-06")


# --- FIX-01 ---------------------------------------------------------------------------------------------------


def test_fix_01_uses_equals_and_puts_literals_first():
    project = project_of(
        '        if (items.get(0) == "a") { n++; }\n'
        "        if (items.get(1) != items.get(2)) { n--; }"
    )
    first = fixed(project, "BUG-01", 0)
    assert 'if ("a".equals(items.get(0))) { n++; }' in first
    second = fixed(project, "BUG-01", 1)
    assert "if (!items.get(1).equals(items.get(2))) { n--; }" in second
    proposal = propose(project, finding_of(project, "BUG-01"))
    assert proposal is not None and proposal.behavior_preserving is False


def test_fix_01_parenthesises_complex_operands():
    project = project_of('        if ((items.get(0) + "x") == items.get(1)) { n++; }')
    after = fixed(project, "BUG-01")
    assert 'if ((items.get(0) + "x").equals(items.get(1))) { n++; }' in after


# --- FIX-02 ---------------------------------------------------------------------------------------------------


def test_fix_02_reports_the_swallowed_exception():
    project = project_of(
        "        try {\n"
        "            n = Integer.parseInt(items.get(0));\n"
        "        } catch (NumberFormatException e) {\n"
        "        }"
    )
    after = fixed(project, "BUG-02")
    assert (
        "} catch (NumberFormatException e) {\n            System.err.println(e); // TODO: обработать исключение\n        }"
        in after
    )
    assert not [
        f for f in analyze(JavaProject({"A.java": after})).findings if f.rule_id == "BUG-02"
    ]


def test_fix_02_keeps_existing_comments():
    project = project_of(
        "        try {\n"
        "            n = 1;\n"
        "        } catch (RuntimeException ex) {\n"
        "            // ignore\n"
        "        }"
    )
    after = fixed(project, "BUG-02")
    assert "// ignore\n            System.err.println(ex);" in after


def test_fix_02_does_not_comment_out_the_brace_of_a_one_line_block():
    project = project_of(
        "        try {\n            n = 1;\n        } catch (RuntimeException ex) { /* ignore */ }"
    )
    after = fixed(project, "BUG-02")
    assert (
        "catch (RuntimeException ex) { /* ignore */\n"
        "            System.err.println(ex); // TODO: обработать исключение\n"
        "        }"
    ) in after
    assert not JavaProject({"A.java": after}).syntax_issues()


# --- доступность в находках ------------------------------------------------------------------------------------


def test_findings_carry_fix_availability_with_reasons():
    project = project_of(
        '        String text = "";\n'
        "        for (String s : items) { text += s; }\n"
        "        System.out.println(text);\n"
        "        for (String a : items) { for (String b : items) { if (a.equals(b)) { n++; } } }"
    )
    by_rule = {f.rule_id: f for f in analyze(project).findings}
    opt3 = by_rule["PERF-03"].fix
    assert opt3 is not None and (opt3.fix_id, opt3.available, opt3.behavior_preserving) == (
        "OPT-03",
        True,
        True,
    )
    assert all(p.passed for p in opt3.preconditions)
    opt1 = by_rule["PERF-01"].fix
    assert opt1 is not None and opt1.fix_id == "OPT-01" and opt1.available is False
    assert opt1.preconditions[0].reason
    assert analyze(project).summary.fixable == 1


def test_applying_a_proposal_to_a_changed_project_is_rejected_when_syntax_breaks():
    project = project_of("        n++;")
    from engine.edit.core import EditError, TextEdit
    from engine.transform.base import FixProposal

    broken = FixProposal("X", "x", "x", True, edits=[TextEdit("A.java", 0, 5, "}}}}}")])
    with pytest.raises(EditError):
        apply_proposal(project, broken)
