"""Остальные правила каталога (S7.6): срабатывание и «чистый» пример для каждого."""

from __future__ import annotations

from engine.analysis.analyzer import analyze
from engine.java.project import JavaProject


def source(body: str, members: str = "", params: str = "List<String> items, int n") -> JavaProject:
    text = (
        "import java.util.*;\nimport java.io.*;\nimport java.util.stream.*;\n"
        f"class A {{\n{members}\n    void run({params}) {{\n{body}\n    }}\n}}\n"
    )
    return JavaProject({"A.java": text})


def rules(project: JavaProject) -> dict[str, list]:
    found: dict[str, list] = {}
    for finding in analyze(project).findings:
        found.setdefault(finding.rule_id, []).append(finding)
    return found


def has(rule: str, body: str, members: str = "", params: str = "List<String> items, int n"):
    return rule in rules(source(body, members, params))


# --- PERF-05 -------------------------------------------------------------------------------------------------


def test_perf_05_invariant_call_in_a_loop():
    body = "        String text = items.get(0);\n        for (int i = 0; i < n; i++) {\n            char[] letters = text.toCharArray();\n            n += letters.length;\n        }"
    [finding] = rules(source(body))["PERF-05"]
    assert "text.toCharArray()" in finding.title or "text.toCharArray()" in finding.explanation
    assert finding.severity == "low"


def test_perf_05_ignores_changing_receivers_and_constant_loops():
    clean = [
        "        for (String s : items) {\n            char[] letters = s.toCharArray();\n            n += letters.length;\n        }",
        "        String text = items.get(0);\n        for (int i = 0; i < n; i++) {\n            text = text + i;\n            n += text.toLowerCase().length();\n        }",
        "        String text = items.get(0);\n        for (int i = 0; i < 3; i++) {\n            n += text.toCharArray().length;\n        }",
    ]
    for body in clean:
        assert not has("PERF-05", body), body


# --- PERF-07 -------------------------------------------------------------------------------------------------


def test_perf_07_repeated_costly_expression():
    body = "        double a = Math.sqrt(n) + 1;\n        double b = Math.sqrt(n) * 2;\n        System.out.println(a + b);"
    [finding] = rules(source(body))["PERF-07"]
    assert "Math.sqrt(n)" in finding.explanation


def test_perf_07_ignores_changed_arguments_and_branches():
    clean = [
        "        double a = Math.sqrt(n) + 1;\n        n++;\n        double b = Math.sqrt(n) * 2;\n        System.out.println(a + b);",
        "        double a;\n        if (n > 0) {\n            a = Math.sqrt(n);\n        } else {\n            a = Math.sqrt(n) + 1;\n        }\n        System.out.println(a);",
        "        double a = Math.sqrt(n) + 1;\n        System.out.println(a);",
    ]
    for body in clean:
        assert not has("PERF-07", body), body


# --- PERF-09 -------------------------------------------------------------------------------------------------


MANUAL_COUNT = """\
        List<String> keys = new ArrayList<>();
        List<Integer> counts = new ArrayList<>();
        for (String word : items) {
            boolean found = false;
            for (int i = 0; i < keys.size(); i++) {
                if (keys.get(i).equals(word)) {
                    counts.set(i, counts.get(i) + 1);
                    found = true;
                }
            }
            if (!found) {
                keys.add(word);
                counts.add(1);
            }
        }"""


def test_perf_09_manual_frequency_counting():
    [finding] = rules(source(MANUAL_COUNT))["PERF-09"]
    assert finding.complexity is not None
    assert finding.complexity.current == "O(n²)" and finding.complexity.after == "O(n)"


def test_perf_09_ignores_counting_in_a_map():
    clean = "        Map<String, Integer> counts = new HashMap<>();\n        for (String word : items) {\n            counts.merge(word, 1, Integer::sum);\n        }"
    assert not has("PERF-09", clean)


# --- PERF-10 -------------------------------------------------------------------------------------------------


def test_perf_10_output_inside_a_loop_over_the_input():
    body = "        for (String item : items) {\n            System.out.println(item);\n        }"
    [finding] = rules(source(body))["PERF-10"]
    assert "System.out.println(item)" in finding.evidence.static[0].text


def test_perf_10_ignores_constant_loops_and_printing_methods():
    assert not has(
        "PERF-10",
        "        for (int i = 0; i < 3; i++) {\n            System.out.println(i);\n        }",
    )
    printing = "    void printAll(List<String> items) {\n        for (String item : items) {\n            System.out.println(item);\n        }\n    }"
    assert "PERF-10" not in rules(source("n++;", printing))


# --- PERF-11 -------------------------------------------------------------------------------------------------


def test_perf_11_sorted_before_filter_and_needless_collect():
    cases = {
        "sorted": "        List<String> r = items.stream().sorted().filter(s -> s.length() > 2).collect(Collectors.toList());",
        "collect": "        long k = items.stream().map(String::trim).collect(Collectors.toList()).stream().count();",
        "count": "        long k = items.stream().count();",
    }
    for name, body in cases.items():
        assert has("PERF-11", body), name


def test_perf_11_ignores_a_good_pipeline():
    body = "        List<String> r = items.stream().filter(s -> s.length() > 2).sorted().collect(Collectors.toList());"
    assert not has("PERF-11", body)


# --- PERF-12 -------------------------------------------------------------------------------------------------


def test_perf_12_wrapper_accumulator_in_a_hot_loop():
    body = "        Integer total = 0;\n        for (String s : items) {\n            total += s.length();\n        }\n        System.out.println(total);"
    [finding] = rules(source(body))["PERF-12"]
    assert "`int`" in finding.recommendation


def test_perf_12_ignores_primitives_and_loop_locals():
    clean = [
        "        int total = 0;\n        for (String s : items) {\n            total += s.length();\n        }",
        "        for (String s : items) {\n            Integer local = 0;\n            local += s.length();\n        }",
    ]
    for body in clean:
        assert not has("PERF-12", body), body


# --- BUG-03 --------------------------------------------------------------------------------------------------


def test_bug_03_resource_never_closed():
    body = '        BufferedReader in = new BufferedReader(new FileReader("data.txt"));\n        System.out.println(n);'
    [finding] = rules(source(body, params="int n"))["BUG-03"]
    assert "BufferedReader" in finding.title


def test_bug_03_ignores_closed_system_in_and_escaping_resources():
    clean = [
        "        Scanner in = new Scanner(System.in);\n        System.out.println(in.nextInt());",
        '        Scanner in = new Scanner("x y");\n        System.out.println(in.next());\n        in.close();',
        '        try (Scanner in = new Scanner("x y")) {\n            System.out.println(in.next());\n        }',
    ]
    for body in clean:
        assert not has("BUG-03", body, params="int n"), body
    escaping = (
        '    Scanner open() {\n        Scanner in = new Scanner("x");\n        return in;\n    }'
    )
    assert "BUG-03" not in rules(source("n++;", escaping))


# --- BUG-04 --------------------------------------------------------------------------------------------------


def test_bug_04_division_by_a_parameter_or_input():
    [finding] = rules(source("        System.out.println(100 / n);"))["BUG-04"]
    assert "n" in finding.explanation
    parsed = "        int d = Integer.parseInt(items.get(0));\n        System.out.println(10 % d);"
    assert has("BUG-04", parsed)


def test_bug_04_ignores_guarded_and_floating_division():
    clean = [
        "        if (n != 0) {\n            System.out.println(100 / n);\n        }",
        "        double x = 5;\n        System.out.println(x / n);",
        "        System.out.println(100 / 4);",
        "        try {\n            System.out.println(100 / n);\n        } catch (ArithmeticException e) {\n            System.out.println(e);\n        }",
    ]
    for body in clean:
        assert not has("BUG-04", body), body


# --- BUG-05 --------------------------------------------------------------------------------------------------


def test_bug_05_map_get_dereferenced_without_check():
    body = '        Map<String, List<String>> index = new HashMap<>();\n        index.get(items.get(0)).add("x");'
    [finding] = rules(source(body))["BUG-05"]
    assert "index.get" in finding.explanation


def test_bug_05_ignores_guarded_access():
    clean = [
        '        Map<String, List<String>> index = new HashMap<>();\n        if (index.containsKey("a")) {\n            index.get("a").add("x");\n        }',
        '        Map<String, List<String>> index = new HashMap<>();\n        if (index.get("a") != null) {\n            index.get("a").add("x");\n        }',
        '        Map<String, List<String>> index = new HashMap<>();\n        index.computeIfAbsent("a", k -> new ArrayList<>()).add("x");',
    ]
    for body in clean:
        assert not has("BUG-05", body), body


# --- BUG-06 --------------------------------------------------------------------------------------------------


def test_bug_06_mutating_an_immutable_or_fixed_size_collection():
    for init, call in (
        ('List.of("a", "b")', 'fixed.add("c");'),
        ("Collections.unmodifiableList(items)", "fixed.remove(0);"),
        ('Arrays.asList("a", "b")', 'fixed.add("c");'),
    ):
        body = f"        List<String> fixed = {init};\n        {call}"
        assert has("BUG-06", body), body


def test_bug_06_ignores_copies_and_allowed_changes():
    clean = [
        '        List<String> mutable = new ArrayList<>(List.of("a"));\n        mutable.add("b");',
        '        List<String> fixed = Arrays.asList("a", "b");\n        fixed.set(0, "c");',
        '        List<String> fixed = List.of("a");\n        fixed = new ArrayList<>(fixed);\n        fixed.add("b");',
    ]
    for body in clean:
        assert not has("BUG-06", body), body


# --- BUG-08 --------------------------------------------------------------------------------------------------


def test_bug_08_broad_catch_that_ignores_the_exception():
    body = '        try {\n            n = Integer.parseInt(items.get(0));\n        } catch (Exception e) {\n            System.out.println("error");\n        }'
    [finding] = rules(source(body))["BUG-08"]
    assert "Exception" in finding.title


def test_bug_08_ignores_used_rethrown_specific_and_empty_catches():
    clean = [
        '        try {\n            n = Integer.parseInt("1");\n        } catch (Exception e) {\n            System.out.println(e.getMessage());\n        }',
        '        try {\n            n = Integer.parseInt("1");\n        } catch (Exception e) {\n            throw new IllegalStateException("x");\n        }',
        '        try {\n            n = Integer.parseInt("1");\n        } catch (NumberFormatException e) {\n            System.out.println("bad");\n        }',
    ]
    for body in clean:
        assert not has("BUG-08", body), body
    empty = '        try {\n            n = Integer.parseInt("1");\n        } catch (Exception e) {\n        }'
    assert has("BUG-02", empty) and not has("BUG-08", empty)


# --- BUG-11 --------------------------------------------------------------------------------------------------


def test_bug_11_format_does_not_match_the_arguments():
    cases = [
        '        double x = 1.5;\n        System.out.printf("%d%n", x);',
        '        int k = 3;\n        System.out.println(String.format("%f", k));',
        '        System.out.printf("%s %s%n", "a");',
        '        String s = "a";\n        System.out.printf("%c", s);',
    ]
    for body in cases:
        assert has("BUG-11", body), body


def test_bug_11_ignores_matching_formats():
    clean = [
        '        double x = 1.5;\n        int k = 3;\n        System.out.printf("%.2f %d %s%n", x, k, "z");',
        '        System.out.printf("100%% done%n");',
        '        long big = 4L;\n        System.out.println(String.format("%5d|%-5s", big, "ab"));',
    ]
    for body in clean:
        assert not has("BUG-11", body), body


# --- MAINT-08 ------------------------------------------------------------------------------------------------


def test_maint_08_input_computation_and_output_in_one_method():
    lines = "\n".join(f"        total += in.nextInt() * {i};" for i in range(1, 22))
    body = f"        Scanner in = new Scanner(System.in);\n        int total = 0;\n{lines}\n        for (int i = 0; i < n; i++) {{\n            total += i;\n        }}\n        System.out.println(total);"
    [finding] = rules(source(body))["MAINT-08"]
    assert finding.severity == "low" and "REF-01" in finding.recommendation


def test_maint_08_ignores_short_methods_and_single_responsibility():
    short = "        Scanner in = new Scanner(System.in);\n        int total = in.nextInt() + in.nextInt();\n        System.out.println(total);"
    assert not has("MAINT-08", short)
    no_input = "\n".join(f"        total += {i} * n;" for i in range(1, 25))
    long_pure = f"        int total = 0;\n{no_input}\n        System.out.println(total);"
    assert not has("MAINT-08", long_pure)
