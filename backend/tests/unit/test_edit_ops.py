"""Операции над графом → правки Java-текста (S5.2–S5.7): golden-тесты «до → после», откат и ошибки."""

from __future__ import annotations

import difflib
from pathlib import Path

import pytest

from engine.edit.core import EditError, check_fragment, indent_unit
from engine.edit.pipeline import apply_ops
from engine.java.project import JavaProject

SAMPLES = Path(__file__).resolve().parents[3] / "samples"

SRC = """import java.util.*;

public class A {
    int total;
    private final List<String> names = new ArrayList<>();

    int run(List<Integer> xs, int n) {
        int sum = 0;
        for (int x : xs) {
            sum += x;
        }
        if (sum > n) {
            System.out.println("big");
        } else if (sum == 0) {
            System.out.println("zero");
        }
        // итог
        System.out.println(sum);
        return sum;
    }

    int twice(int v) {
        return v * 2;
    }

    int caller() {
        return twice(total) + twice(1);
    }
}
"""
RUN = "A.java#A.run(List<Integer>, int)"


def edit(ops: list[dict], source: str = SRC) -> str:
    return apply_ops({"A.java": source}, ops).files["A.java"]


def fails(ops: list[dict], source: str = SRC) -> EditError:
    with pytest.raises(EditError) as caught:
        apply_ops({"A.java": source}, ops)
    return caught.value


def lines_changed(before: str, after: str) -> tuple[list[str], list[str]]:
    """Удалённые и добавленные строки (построчный diff, повторы строк различаются)."""
    removed, added = [], []
    for line in difflib.ndiff(before.splitlines(), after.splitlines()):
        if line.startswith("- "):
            removed.append(line[2:])
        elif line.startswith("+ "):
            added.append(line[2:])
    return removed, added


# --- insert_node -----------------------------------------------------------------------------------------


@pytest.mark.parametrize(
    ("kind", "fields", "expected"),
    [
        ("var_decl", {"type": "int", "name": "count", "value": "0"}, ["        int count = 0;"]),
        ("var_decl", {"type": "String", "name": "s"}, ["        String s;"]),
        ("assign", {"target": "sum", "op": "+=", "value": "10"}, ["        sum += 10;"]),
        ("assign", {"target": "sum", "op": "++"}, ["        sum++;"]),
        ("operation", {"expr": 'System.out.println("hi")'}, ['        System.out.println("hi");']),
        ("return", {"expr": "sum"}, ["        return sum;"]),
        ("return", {}, ["        return;"]),
        (
            "throw",
            {"expr": 'new IllegalStateException("x")'},
            ['        throw new IllegalStateException("x");'],
        ),
        ("break", {}, ["        break;"]),
        ("continue", {"label": "outer"}, ["        continue outer;"]),
        ("condition", {"expr": "sum > 1"}, ["        if (sum > 1) {", "        }"]),
        (
            "condition",
            {"expr": "sum > 1", "add_else": True},
            ["        if (sum > 1) {", "        } else {", "        }"],
        ),
        (
            "loop_for",
            {"init": "int i = 0", "cond": "i < n", "update": "i++"},
            ["        for (int i = 0; i < n; i++) {", "        }"],
        ),
        (
            "loop_foreach",
            {"type": "String", "name": "s", "iterable": "names"},
            ["        for (String s : names) {", "        }"],
        ),
        ("loop_while", {"cond": "n > 0"}, ["        while (n > 0) {", "        }"]),
        (
            "loop_while",
            {"cond": "n > 0", "do_while": True},
            ["        do {", "        } while (n > 0);"],
        ),
        ("try", {}, ["        try {", "        } catch (Exception e) {", "        }"]),
        (
            "try",
            {"exception_type": "IOException", "name": "ex", "add_finally": True},
            [
                "        try {",
                "        } catch (IOException ex) {",
                "        } finally {",
                "        }",
            ],
        ),
        ("code", {"text": "int raw = 1;"}, ["        int raw = 1;"]),
    ],
)
def test_insert_node_renders_the_template_with_the_file_indent(kind, fields, expected):
    after = edit(
        [
            {
                "op": "insert_node",
                "parent_id": RUN,
                "slot": "body",
                "index": 1,
                "kind": kind,
                "fields": fields,
            }
        ]
    )
    removed, added = lines_changed(SRC, after)
    assert removed == []
    assert added == expected


def test_insert_at_start_end_and_into_nested_slots():
    first = edit(
        [
            {
                "op": "insert_node",
                "parent_id": RUN,
                "slot": "body",
                "index": 0,
                "kind": "operation",
                "fields": {"expr": "init()"},
            }
        ]
    )
    assert first.split("\n")[7] == "        init();"
    last = edit(
        [
            {
                "op": "insert_node",
                "parent_id": RUN,
                "slot": "body",
                "index": 99,
                "kind": "operation",
                "fields": {"expr": "done()"},
            }
        ]
    )
    assert "        return sum;\n        done();\n    }" in last
    inner = edit(
        [
            {
                "op": "insert_node",
                "parent_id": f"{RUN}/foreach[0]",
                "slot": "body",
                "index": 1,
                "kind": "operation",
                "fields": {"expr": "log(x)"},
            }
        ]
    )
    assert "            sum += x;\n            log(x);\n        }" in inner
    then = edit(
        [
            {
                "op": "insert_node",
                "parent_id": f"{RUN}/if[0]",
                "slot": "then",
                "index": 0,
                "kind": "operation",
                "fields": {"expr": "mark()"},
            }
        ]
    )
    assert (
        '        if (sum > n) {\n            mark();\n            System.out.println("big");'
        in then
    )


def test_insert_into_an_empty_block_and_a_braceless_body():
    empty = "class A {\n    void f(int n) {\n        if (n > 0) {\n        }\n    }\n}\n"
    out = edit(
        [
            {
                "op": "insert_node",
                "parent_id": "A.java#A.f(int)/if[0]",
                "slot": "then",
                "index": 0,
                "kind": "operation",
                "fields": {"expr": "g()"},
            }
        ],
        empty,
    )
    assert "        if (n > 0) {\n            g();\n        }" in out
    braceless = "class A {\n    void f(int n) {\n        if (n > 0)\n            h();\n    }\n}\n"
    out = edit(
        [
            {
                "op": "insert_node",
                "parent_id": "A.java#A.f(int)/if[0]",
                "slot": "then",
                "index": 1,
                "kind": "operation",
                "fields": {"expr": "g()"},
            }
        ],
        braceless,
    )
    assert "if (n > 0) {\n            h();\n            g();\n        }" in out


def test_javadoc_continuation_lines_do_not_shrink_the_indent_unit():
    source = "/**\n * Описание.\n */\nclass A {\n    void f() {\n        g();\n    }\n}\n"
    assert indent_unit(source) == "    "


def test_insert_uses_tabs_when_the_file_is_tab_indented():
    source = SRC.replace("    ", "\t")
    assert indent_unit(source) == "\t"
    out = edit(
        [
            {
                "op": "insert_node",
                "parent_id": RUN,
                "slot": "body",
                "index": 1,
                "kind": "operation",
                "fields": {"expr": "f()"},
            }
        ],
        source,
    )
    assert "\t\tf();\n" in out


def test_insert_validation_errors_name_the_field():
    cases = [
        ({"type": "int", "name": "1bad"}, "name"),
        ({"type": "in t", "name": "ok"}, "type"),
        ({"type": "int", "name": "ok", "value": "1 +"}, "value"),
        ({"type": "int", "name": "class"}, "name"),
        ({"type": "int"}, "name"),
    ]
    for fields, field in cases:
        error = fails(
            [
                {
                    "op": "insert_node",
                    "parent_id": RUN,
                    "slot": "body",
                    "index": 0,
                    "kind": "var_decl",
                    "fields": fields,
                }
            ]
        )
        assert (error.code, error.field) == ("INVALID_FRAGMENT", field), fields


def test_expression_injection_is_rejected():
    for text in ("1); foo(", "a; b", "x) + (y", "}"):
        error = fails(
            [
                {
                    "op": "insert_node",
                    "parent_id": RUN,
                    "slot": "body",
                    "index": 0,
                    "kind": "operation",
                    "fields": {"expr": text},
                }
            ]
        )
        assert error.code == "INVALID_FRAGMENT", text


# --- update_node -----------------------------------------------------------------------------------------


@pytest.mark.parametrize(
    ("node", "fields", "old", "new"),
    [
        ("/if[0]", {"expr": "sum >= n"}, "if (sum > n) {", "if (sum >= n) {"),
        (
            "/foreach[0]",
            {"iterable": "xs.subList(0, 1)"},
            "for (int x : xs) {",
            "for (int x : xs.subList(0, 1)) {",
        ),
        ("/foreach[0]", {"name": "v"}, "for (int x : xs) {", "for (int v : xs) {"),
        ("/foreach[0]", {"type": "long"}, "for (int x : xs) {", "for (long x : xs) {"),
        ("/foreach[0]/assign[0]", {"op": "-="}, "            sum += x;", "            sum -= x;"),
        (
            "/foreach[0]/assign[0]",
            {"target": "total", "value": "x * 2"},
            "            sum += x;",
            "            total += x * 2;",
        ),
        ("/var[0]", {"value": "42"}, "        int sum = 0;", "        int sum = 42;"),
        (
            "/var[0]",
            {"name": "acc", "type": "long"},
            "        int sum = 0;",
            "        long acc = 0;",
        ),
        ("/return[0]", {"expr": "sum + 1"}, "        return sum;", "        return sum + 1;"),
        (
            "/op[0]",
            {"expr": "System.out.println(sum + 1)"},
            "        System.out.println(sum);",
            "        System.out.println(sum + 1);",
        ),
    ],
)
def test_update_node_changes_only_the_edited_part(node, fields, old, new):
    after = edit([{"op": "update_node", "node_id": RUN + node, "fields": fields}])
    removed, added = lines_changed(SRC, after)
    assert [r.strip() for r in removed] == [old.strip()]
    assert [a.strip() for a in added] == [new.strip()]


def test_update_for_header_keeps_unspecified_parts():
    source = "class A {\n    void f(int n) {\n        for (int i = 0; i < n; i++) {\n            n--;\n        }\n    }\n}\n"
    out = edit(
        [{"op": "update_node", "node_id": "A.java#A.f(int)/for[0]", "fields": {"cond": "i <= n"}}],
        source,
    )
    assert "for (int i = 0; i <= n; i++) {" in out
    out = edit(
        [
            {
                "op": "update_node",
                "node_id": "A.java#A.f(int)/for[0]",
                "fields": {"update": "i += 2"},
            }
        ],
        source,
    )
    assert "for (int i = 0; i < n; i += 2) {" in out


def test_update_while_switch_and_catch_headers():
    source = (
        "class A {\n    void f(int n) {\n        while (n > 0) {\n            n--;\n        }\n"
        "        switch (n) {\n            case 1:\n                break;\n            default:\n                break;\n        }\n"
        "        try {\n            n++;\n        } catch (RuntimeException e) {\n            n = 0;\n        }\n    }\n}\n"
    )
    out = edit(
        [
            {
                "op": "update_node",
                "node_id": "A.java#A.f(int)/while[0]",
                "fields": {"cond": "n > 1"},
            },
            {
                "op": "update_node",
                "node_id": "A.java#A.f(int)/switch[0]",
                "fields": {"expr": "n % 3"},
            },
            {
                "op": "update_node",
                "node_id": "A.java#A.f(int)/try[0]/catch[0]",
                "fields": {"exception_type": "Exception", "name": "ex"},
            },
        ],
        source,
    )
    assert (
        "while (n > 1) {" in out and "switch (n % 3) {" in out and "catch (Exception ex) {" in out
    )


def test_update_errors():
    node = RUN + "/if[0]"
    assert (
        fails([{"op": "update_node", "node_id": node, "fields": {"expr": "sum >"}}]).field == "expr"
    )
    assert fails([{"op": "update_node", "node_id": node, "fields": {}}]).field == "expr"
    assert (
        fails([{"op": "update_node", "node_id": RUN + "/if[9]", "fields": {"expr": "x"}}]).code
        == "NOT_FOUND"
    )
    assert (
        fails([{"op": "update_node", "node_id": RUN, "fields": {"expr": "x"}}]).code
        == "UNSUPPORTED"
    )
    error = fails(
        [{"op": "update_node", "node_id": RUN + "/foreach[0]/assign[0]", "fields": {"op": "**"}}]
    )
    assert (error.code, error.field) == ("INVALID_FRAGMENT", "op")


# --- delete / unwrap / duplicate ------------------------------------------------------------------------------


def test_delete_node_removes_whole_lines_with_leading_comment():
    after = edit([{"op": "delete_node", "node_id": RUN + "/op[0]"}])
    assert "// итог" not in after and after.count("System.out.println(sum);") == 0
    removed, added = lines_changed(SRC, after)
    assert added == [] and removed == ["        // итог", "        System.out.println(sum);"]


def test_delete_container_with_body_and_unwrap_keep_the_rest():
    after = edit([{"op": "delete_node", "node_id": RUN + "/foreach[0]"}])
    assert "for (int x : xs)" not in after and "sum += x" not in after
    unwrapped = edit([{"op": "delete_node", "node_id": RUN + "/foreach[0]", "mode": "unwrap"}])
    assert "        int sum = 0;\n        sum += x;\n        if (sum > n) {" in unwrapped
    via_op = edit([{"op": "unwrap_node", "node_id": RUN + "/foreach[0]"}])
    assert via_op == unwrapped


def test_unwrap_refuses_conditions_with_else_branches():
    assert fails([{"op": "unwrap_node", "node_id": RUN + "/if[0]"}]).code == "PRECONDITION_FAILED"


def test_duplicate_node_copies_next_to_the_original():
    after = edit([{"op": "duplicate_node", "node_id": RUN + "/op[0]"}])
    assert after.count("        System.out.println(sum);\n        System.out.println(sum);") == 1
    assert (
        fails([{"op": "duplicate_node", "node_id": RUN + "/var[0]"}]).code == "PRECONDITION_FAILED"
    )


# --- wrap / branches / move -------------------------------------------------------------------------------------


def test_wrap_consecutive_statements_in_if_try_and_block():
    ids = [RUN + "/op[0]", RUN + "/return[0]"]
    wrapped = edit(
        [{"op": "wrap_node", "node_ids": ids, "wrapper": "if", "fields": {"expr": "sum > 0"}}]
    )
    assert (
        "        if (sum > 0) {\n            // итог\n            System.out.println(sum);\n            return sum;\n        }\n    }"
        in wrapped
    )
    tried = edit([{"op": "wrap_node", "node_ids": ids[:1], "wrapper": "try"}])
    assert (
        "        try {\n            // итог\n            System.out.println(sum);\n        } catch (Exception e) {\n        }"
        in tried
    )
    block = edit([{"op": "wrap_node", "node_ids": ids[:1], "wrapper": "block"}])
    assert (
        "        {\n            // итог\n            System.out.println(sum);\n        }" in block
    )
    loop = edit(
        [
            {
                "op": "wrap_node",
                "node_ids": ids[:1],
                "wrapper": "while",
                "fields": {"cond": "n-- > 0"},
            }
        ]
    )
    assert "        while (n-- > 0) {" in loop


def test_wrap_rejects_gaps_and_scope_breaking():
    assert (
        fails(
            [{"op": "wrap_node", "node_ids": [RUN + "/var[0]", RUN + "/op[0]"], "wrapper": "block"}]
        ).code
        == "PRECONDITION_FAILED"
    )
    error = fails([{"op": "wrap_node", "node_ids": [RUN + "/var[0]"], "wrapper": "block"}])
    assert error.code == "PRECONDITION_FAILED" and "sum" in error.message


def test_add_branch_to_if_try_and_switch():
    with_else_if = edit(
        [
            {
                "op": "add_branch",
                "node_id": RUN + "/if[0]",
                "branch": "else_if",
                "fields": {"expr": "sum < 0"},
            }
        ]
    )
    assert (
        '        } else if (sum == 0) {\n            System.out.println("zero");\n        } else if (sum < 0) {\n        }'
        in with_else_if
    )
    with_else = edit([{"op": "add_branch", "node_id": RUN + "/if[0]", "branch": "else"}])
    assert '            System.out.println("zero");\n        } else {\n        }' in with_else
    assert (
        fails([{"op": "add_branch", "node_id": RUN + "/if[0]", "branch": "else"}], with_else).code
        == "PRECONDITION_FAILED"
    )

    source = (
        "class A {\n    void f(int n) {\n        try {\n            n++;\n        } catch (RuntimeException e) {\n            n = 0;\n        }\n"
        "        switch (n) {\n            case 1:\n                break;\n        }\n    }\n}\n"
    )
    out = edit(
        [
            {
                "op": "add_branch",
                "node_id": "A.java#A.f(int)/try[0]",
                "branch": "catch",
                "fields": {"exception_type": "Error", "name": "err"},
            },
            {"op": "add_branch", "node_id": "A.java#A.f(int)/try[0]", "branch": "finally"},
            {
                "op": "add_branch",
                "node_id": "A.java#A.f(int)/switch[0]",
                "branch": "case",
                "fields": {"expr": "2"},
            },
            {"op": "add_branch", "node_id": "A.java#A.f(int)/switch[0]", "branch": "default"},
        ],
        source,
    )
    assert "} catch (Error err) {\n        } finally {\n        }" in out
    assert (
        "            case 2:\n                break;\n            default:\n                break;\n        }"
        in out
    )


def test_remove_branch():
    source = (
        "class A {\n    void f(int n) {\n        if (n > 0) {\n            n++;\n        } else {\n            n--;\n        }\n"
        "        try {\n            n++;\n        } catch (RuntimeException e) {\n            n = 0;\n        } catch (Error e) {\n            n = 1;\n        } finally {\n            n = 2;\n        }\n    }\n}\n"
    )
    out = edit(
        [
            {
                "op": "remove_branch",
                "node_id": "A.java#A.f(int)/if[0]",
                "branch_id": "A.java#A.f(int)/if[0]/else",
            }
        ],
        source,
    )
    assert "n--" not in out and "} else" not in out
    out = edit(
        [
            {
                "op": "remove_branch",
                "node_id": "A.java#A.f(int)/try[0]",
                "branch_id": "A.java#A.f(int)/try[0]/catch[1]",
            }
        ],
        source,
    )
    assert "catch (Error e)" not in out and "catch (RuntimeException e)" in out
    out = edit(
        [
            {
                "op": "remove_branch",
                "node_id": "A.java#A.f(int)/try[0]",
                "branch_id": "A.java#A.f(int)/try[0]/finally",
            }
        ],
        source,
    )
    assert "finally" not in out


def test_move_node_between_slots_keeps_the_text():
    ops = [
        {
            "op": "move_node",
            "node_id": RUN + "/op[0]",
            "new_parent_id": f"{RUN}/foreach[0]",
            "slot": "body",
            "index": 0,
        }
    ]
    after = edit(ops)
    assert (
        "        for (int x : xs) {\n            System.out.println(sum);\n            sum += x;"
        in after
    )
    assert after.count("System.out.println(sum);") == 1
    assert (
        fails(
            [
                {
                    "op": "move_node",
                    "node_id": f"{RUN}/foreach[0]",
                    "new_parent_id": f"{RUN}/foreach[0]",
                    "slot": "body",
                    "index": 0,
                }
            ]
        ).code
        == "PRECONDITION_FAILED"
    )


# --- replace_code и откат -------------------------------------------------------------------------------------------


def test_replace_code_by_node_and_range():
    out = edit([{"op": "replace_code", "node_id": RUN + "/return[0]", "text": "return -1;"}])
    assert "        return -1;" in out
    rng = {"file": "A.java", "start_line": 8, "start_col": 9, "end_line": 8, "end_col": 24}
    out = edit([{"op": "replace_code", "range": rng, "text": "int sum = 5;"}])
    assert "        int sum = 5;" in out
    assert fails([{"op": "replace_code", "text": "x"}]).code == "INVALID_FRAGMENT"


def test_broken_result_is_rejected_and_nothing_changes():
    error = fails([{"op": "replace_code", "node_id": RUN + "/return[0]", "text": "return ;;; }}"}])
    assert error.code == "INVALID_FRAGMENT" and error.line is not None
    batch = fails(
        [
            {
                "op": "insert_node",
                "parent_id": RUN,
                "slot": "body",
                "index": 0,
                "kind": "operation",
                "fields": {"expr": "ok()"},
            },
            {"op": "replace_code", "node_id": RUN + "/return[1]", "text": "return ((;"},
        ]
    )
    assert batch.op_index == 1


def test_identity_replacement_never_changes_a_byte():
    """I2: замена любого блока его же текстом ничего не меняет (на демонстрационном монолите)."""
    source = (SAMPLES / "demo-orders" / "Main.java").read_text(encoding="utf-8")
    project = JavaProject({"Main.java": source})
    checked = 0
    for graph in list(project.flows())[:25]:
        for node in graph.nodes:
            if node.id == graph.method_id or node.kind in {"case", "finally", "catch"}:
                continue
            r = node.range
            original = _slice(source, r.start_line, r.start_col, r.end_line, r.end_col)
            result = apply_ops(
                {"Main.java": source},
                [{"op": "replace_code", "node_id": node.id, "text": original}],
            )
            assert result.files["Main.java"] == source, node.id
            checked += 1
    assert checked > 100


def _slice(source: str, line1: int, col1: int, line2: int, col2: int) -> str:
    lines = source.split("\n")

    def offset(line: int, col: int) -> int:
        text = lines[line - 1]
        utf16 = 0
        for i, ch in enumerate(text):
            if utf16 >= col - 1:
                return sum(len(x) + 1 for x in lines[: line - 1]) + i
            utf16 += 2 if ord(ch) > 0xFFFF else 1
        return sum(len(x) + 1 for x in lines[: line - 1]) + len(text)

    return source[offset(line1, col1) : offset(line2, col2)]


# --- id_map и фокус ----------------------------------------------------------------------------------------------


def test_focus_points_at_the_new_node_and_id_map_covers_shifted_siblings():
    result = apply_ops(
        {"A.java": SRC},
        [
            {
                "op": "insert_node",
                "parent_id": RUN,
                "slot": "body",
                "index": 0,
                "kind": "operation",
                "fields": {"expr": "init()"},
            }
        ],
    )
    assert result.focus_node_id == RUN + "/op[0]"
    assert result.id_map[RUN + "/op[0]"] == RUN + "/op[1]"  # прежний println(sum) сдвинулся
    assert result.changed == ["A.java"]


# --- члены -----------------------------------------------------------------------------------------------------------


def test_add_members():
    out = edit(
        [
            {
                "op": "add_member",
                "type_id": "A.java#A",
                "kind": "field",
                "fields": {"type": "int", "name": "limit", "value": "5"},
            },
            {
                "op": "add_member",
                "type_id": "A.java#A",
                "kind": "method",
                "fields": {
                    "return_type": "int",
                    "name": "limitOf",
                    "params": [{"type": "int", "name": "k"}],
                    "throws": ["java.io.IOException"],
                },
            },
            {
                "op": "add_member",
                "type_id": "A.java#A",
                "kind": "constructor",
                "fields": {"params": [{"type": "int", "name": "t"}]},
            },
        ]
    )
    assert (
        "    private final List<String> names = new ArrayList<>();\n    private int limit = 5;\n"
        in out
    )
    assert "    public int limitOf(int k) throws java.io.IOException {\n    }" in out
    assert "    public A(int t) {\n    }\n}" in out


def test_add_member_to_an_empty_one_line_class_and_errors():
    out = edit(
        [
            {
                "op": "add_member",
                "type_id": "A.java#A",
                "kind": "field",
                "fields": {"type": "int", "name": "x"},
            }
        ],
        "class A {}\n",
    )
    assert out == "class A {\n    private int x;\n}\n"
    assert (
        fails(
            [
                {
                    "op": "add_member",
                    "type_id": "A.java#Nope",
                    "kind": "field",
                    "fields": {"type": "int", "name": "x"},
                }
            ]
        ).code
        == "NOT_FOUND"
    )
    assert (
        fails(
            [
                {
                    "op": "add_member",
                    "type_id": "A.java#A",
                    "kind": "field",
                    "fields": {"type": "int", "name": "x", "modifiers": ["weird"]},
                }
            ]
        ).field
        == "modifiers"
    )


def test_update_and_delete_member():
    out = edit(
        [
            {
                "op": "update_member",
                "member_id": "A.java#A.twice(int)",
                "fields": {
                    "modifiers": ["private"],
                    "return_type": "long",
                    "params": [{"type": "long", "name": "w"}],
                },
            }
        ]
    )
    assert "    private long twice(long w) {" in out
    renamed = edit(
        [{"op": "update_member", "member_id": "A.java#A.twice(int)", "fields": {"name": "dbl"}}]
    )
    assert (
        "int dbl(int v)" in renamed and "dbl(total) + dbl(1)" in renamed and "twice" not in renamed
    )
    assert (
        fails([{"op": "delete_member", "member_id": "A.java#A.twice(int)"}]).code
        == "PRECONDITION_FAILED"
    )
    deleted = edit([{"op": "delete_member", "member_id": "A.java#A.caller()"}])
    assert "caller" not in deleted and deleted.rstrip().endswith("}\n}".rstrip())
    unused_field = edit([{"op": "delete_member", "member_id": "A.java#A.names"}])
    assert "names" not in unused_field


# --- переименование -------------------------------------------------------------------------------------------------------


def test_rename_local_parameter_and_field_respecting_scopes():
    local = edit([{"op": "rename_symbol", "symbol_id": RUN + "/var:sum", "new_name": "acc"}])
    assert local.count("acc") == 6 and "sum" not in local
    param = edit([{"op": "rename_symbol", "symbol_id": RUN + "/param:n", "new_name": "limit"}])
    assert (
        "int run(List<Integer> xs, int limit)" in param
        and "if (acc" not in param
        and "sum > limit" in param
    )
    field = edit([{"op": "rename_symbol", "symbol_id": "A.java#A.total", "new_name": "grand"}])
    assert "int grand;" in field and "twice(grand)" in field and "total" not in field


def test_rename_field_skips_shadowing_locals_and_this_access():
    source = (
        "class A {\n    int count;\n    void f(int count) {\n        this.count = count;\n    }\n"
        "    int g() {\n        return count;\n    }\n}\n"
    )
    out = edit([{"op": "rename_symbol", "symbol_id": "A.java#A.count", "new_name": "size"}], source)
    assert "int size;" in out and "this.size = count;" in out and "return size;" in out
    assert "void f(int count)" in out


def test_rename_method_updates_calls_overrides_and_other_files():
    files = {
        "Base.java": "class Base {\n    int area() {\n        return 0;\n    }\n}\n",
        "Sq.java": "class Sq extends Base {\n    @Override\n    int area() {\n        return 4;\n    }\n}\n",
        "Main.java": "class Main {\n    int use(Base b, Sq s) {\n        return b.area() + s.area();\n    }\n}\n",
    }
    result = apply_ops(
        files,
        [{"op": "rename_symbol", "symbol_id": "Base.java#Base.area()", "new_name": "surface"}],
    )
    assert "area" not in "".join(result.files.values())
    assert "b.surface() + s.surface()" in result.files["Main.java"]
    assert result.changed == ["Base.java", "Main.java", "Sq.java"]


def test_rename_type_updates_all_references():
    files = {
        "Item.java": "class Item {\n    Item() {\n    }\n}\n",
        "Main.java": "import java.util.*;\nclass Main {\n    List<Item> items = new ArrayList<Item>();\n    Item make() {\n        return new Item();\n    }\n}\n",
    }
    result = apply_ops(
        files, [{"op": "rename_symbol", "symbol_id": "Item.java#Item", "new_name": "Entry"}]
    )
    assert "Item" not in "".join(result.files.values())
    assert "class Entry {\n    Entry() {" in result.files["Item.java"]
    assert "List<Entry> items = new ArrayList<Entry>();" in result.files["Main.java"]
    public = {"Pub.java": "public class Pub {\n}\n"}
    with pytest.raises(EditError) as caught:
        apply_ops(public, [{"op": "rename_symbol", "symbol_id": "Pub.java#Pub", "new_name": "Q"}])
    assert caught.value.code == "PRECONDITION_FAILED"


def test_rename_conflicts_and_bad_names():
    assert (
        fails([{"op": "rename_symbol", "symbol_id": RUN + "/var:sum", "new_name": "n"}]).code
        == "PRECONDITION_FAILED"
    )
    assert (
        fails([{"op": "rename_symbol", "symbol_id": RUN + "/var:sum", "new_name": "for"}]).field
        == "new_name"
    )
    assert (
        fails([{"op": "rename_symbol", "symbol_id": RUN + "/var:sum", "new_name": "a b"}]).field
        == "new_name"
    )
    assert (
        fails([{"op": "rename_symbol", "symbol_id": "A.java#A.nope", "new_name": "x"}]).code
        == "NOT_FOUND"
    )
    assert (
        fails([{"op": "rename_symbol", "symbol_id": "A.java#A.total", "new_name": "names"}]).code
        == "PRECONDITION_FAILED"
    )
    main = "class M {\n    public static void main(String[] a) {\n    }\n}\n"
    assert (
        fails(
            [{"op": "rename_symbol", "symbol_id": "A.java#A.main(String[])", "new_name": "start"}],
            main.replace("M", "A"),
        ).code
        == "PRECONDITION_FAILED"
    )


# --- проверка фрагментов ---------------------------------------------------------------------------------------------------


def test_check_fragment_kinds():
    assert check_fragment("expression", " a + b ", "x") == "a + b"
    assert check_fragment("type", "Map<String, List<Integer>>", "x") == "Map<String, List<Integer>>"
    assert check_fragment("type", "int[]", "x") == "int[]"
    assert check_fragment("identifier", "счётчик", "x") == "счётчик"
    for kind, text in (
        ("expression", ""),
        ("expression", "1 +"),
        ("type", "int int"),
        ("identifier", "9a"),
        ("identifier", "new"),
    ):
        with pytest.raises(EditError):
            check_fragment(kind, text, "x")


# --- S5 (остаток): ветки switch, константы enum, компоненты record ----------------------------------------------

SWITCH_SRC = (
    "class A {\n    void f(int n) {\n        switch (n) {\n            case 1:\n"
    '                System.out.println("one");\n                break;\n            case 2:\n'
    "            case 3:\n                n++;\n                break;\n        }\n    }\n}\n"
)
CASE_1 = "A.java#A.f(int)/switch[0]/case[0]"


def test_insert_into_switch_case_groups():
    inserted = edit(
        [
            {
                "op": "insert_node",
                "parent_id": CASE_1,
                "slot": "body",
                "index": 1,
                "kind": "operation",
                "fields": {"expr": "n++"},
            }
        ],
        SWITCH_SRC,
    )
    assert (
        '                System.out.println("one");\n                n++;\n                break;'
        in inserted
    )
    last = edit(
        [
            {
                "op": "insert_node",
                "parent_id": CASE_1,
                "slot": "body",
                "index": 5,
                "kind": "operation",
                "fields": {"expr": "n--"},
            }
        ],
        SWITCH_SRC,
    )
    assert "                break;\n                n--;" in last
    labels_only = edit(
        [
            {
                "op": "insert_node",
                "parent_id": "A.java#A.f(int)/switch[0]/case[1]",
                "slot": "body",
                "index": 0,
                "kind": "operation",
                "fields": {"expr": "n += 2"},
            }
        ],
        SWITCH_SRC,
    )
    assert "            case 2:\n                n += 2;\n            case 3:" in labels_only
    assert not JavaProject({"A.java": inserted}).syntax_issues()


ENUM_SRC = (
    "class A {\n    enum Kind {\n        SMALL,\n        BIG(5);\n\n        private final int w;\n"
    "        Kind() { this(0); }\n        Kind(int w) { this.w = w; }\n    }\n\n"
    "    record Pair(int a, String b) {}\n\n    enum Empty {}\n    enum Inline { X, Y }\n}\n"
)


def test_add_and_delete_enum_constants():
    added = edit(
        [
            {
                "op": "add_member",
                "type_id": "A.java#A.Kind",
                "kind": "enum_constant",
                "fields": {"name": "HUGE", "args": ["9"]},
            }
        ],
        ENUM_SRC,
    )
    assert "        BIG(5),\n        HUGE(9);" in added
    inline = edit(
        [
            {
                "op": "add_member",
                "type_id": "A.java#A.Inline",
                "kind": "enum_constant",
                "fields": {"name": "Z"},
            }
        ],
        ENUM_SRC,
    )
    assert "enum Inline { X, Y, Z }" in inline
    empty = edit(
        [
            {
                "op": "add_member",
                "type_id": "A.java#A.Empty",
                "kind": "enum_constant",
                "fields": {"name": "ONLY"},
            }
        ],
        ENUM_SRC,
    )
    assert "ONLY" in empty and not JavaProject({"A.java": empty}).syntax_issues()
    assert (
        fails(
            [
                {
                    "op": "add_member",
                    "type_id": "A.java#A.Kind",
                    "kind": "enum_constant",
                    "fields": {"name": "BIG"},
                }
            ],
            ENUM_SRC,
        ).code
        == "PRECONDITION_FAILED"
    )
    removed = edit([{"op": "delete_member", "member_id": "A.java#A.Kind.SMALL"}], ENUM_SRC)
    assert "enum Kind {\n        BIG(5);" in removed
    last = edit([{"op": "delete_member", "member_id": "A.java#A.Inline.Y"}], ENUM_SRC)
    assert "enum Inline { X }" in last


def test_add_and_delete_record_components():
    added = edit(
        [
            {
                "op": "add_member",
                "type_id": "A.java#A.Pair",
                "kind": "record_component",
                "fields": {"type": "long", "name": "c"},
            }
        ],
        ENUM_SRC,
    )
    assert "record Pair(int a, String b, long c) {}" in added
    removed = edit([{"op": "delete_member", "member_id": "A.java#A.Pair.a"}], ENUM_SRC)
    assert "record Pair(String b) {}" in removed
    tail = edit([{"op": "delete_member", "member_id": "A.java#A.Pair.b"}], ENUM_SRC)
    assert "record Pair(int a) {}" in tail
    used = ENUM_SRC.replace(
        "enum Empty {}", 'enum Empty {}\n    Object make() { return new Pair(1, "x"); }'
    )
    assert (
        fails([{"op": "delete_member", "member_id": "A.java#A.Pair.a"}], used).code
        == "PRECONDITION_FAILED"
    )
