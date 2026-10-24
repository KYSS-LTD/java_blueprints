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


# --- OPT-08 --------------------------------------------------------------------------------------------------

FIB = (
    "    int fib(int k) {\n"
    "        if (k < 2) {\n"
    "            return k;\n"
    "        }\n"
    "        return fib(k - 1) + fib(k - 2);\n"
    "    }"
)


def test_opt_08_wraps_a_pure_recursive_function_in_a_cache():
    after = fixed(project_of("n++;", FIB), "PERF-08")
    assert "    private final Map<Integer, Integer> fibMemo = new HashMap<>();" in after
    assert "        Integer cached = fibMemo.get(k);" in after
    assert "        int result = fibCompute(k);\n        fibMemo.put(k, result);" in after
    assert "    private int fibCompute(int k) {" in after
    assert "return fib(k - 1) + fib(k - 2);" in after  # рекурсия идёт через обёртку с кэшем
    assert "PERF-08" not in rule_ids(after)


def test_opt_08_static_methods_get_a_static_cache_and_keep_visibility():
    source = (
        "import java.util.*;\n"
        "public class A {\n"
        "    public static long fib(int k) {\n"
        "        return k < 2 ? k : fib(k - 1) + fib(k - 2);\n"
        "    }\n"
        "}\n"
    )
    project = JavaProject({"A.java": source})
    after = fixed(project, "PERF-08")
    assert "    private static final Map<Integer, Long> fibMemo = new HashMap<>();" in after
    assert "    public static long fib(int k) {" in after
    assert "    private static long fibCompute(int k) {" in after


def test_opt_08_refuses_impure_functions():
    printing = FIB.replace("if (k < 2) {", 'System.out.println("fib");\n        if (k < 2) {')
    assert "System" in refusal(project_of("n++;", printing), "PERF-08")
    stateful = "    int calls;\n" + FIB.replace(
        "return fib(k - 1)", "calls++;\n        return fib(k - 1)"
    )
    assert "не локальную" in refusal(project_of("n++;", stateful), "PERF-08")
    reads_field = "    int base = 1;\n" + FIB.replace("return k;", "return k + base;")
    assert "изменяемое поле" in refusal(project_of("n++;", reads_field), "PERF-08")


# --- OPT-11 --------------------------------------------------------------------------------------------------


def test_opt_11_swaps_sorted_and_filter():
    body = "        List<String> r = items.stream().sorted().filter(s -> s.length() > 2).collect(Collectors.toList());"
    project = project_of(body)
    after = fixed(project, "PERF-11")
    assert "items.stream().filter(s -> s.length() > 2).sorted().collect(" in after
    assert "PERF-11" not in rule_ids(after)


def test_opt_11_drops_needless_collect_and_stream():
    source = (
        "import java.util.*;\nimport java.util.stream.*;\nclass A {\n"
        "    long run(List<String> items) {\n"
        "        return items.stream().map(String::trim).collect(Collectors.toList()).stream().filter(s -> !s.isEmpty()).count();\n"
        "    }\n}\n"
    )
    after = fixed(JavaProject({"A.java": source}), "PERF-11")
    assert "items.stream().map(String::trim).filter(s -> !s.isEmpty()).count()" in after


def test_opt_11_replaces_stream_count_with_size():
    source = (
        "import java.util.*;\nimport java.util.stream.*;\nclass A {\n"
        "    long run(List<String> items) {\n"
        "        return items.stream().count();\n"
        "    }\n}\n"
    )
    after = fixed(JavaProject({"A.java": source}), "PERF-11")
    assert "return items.size();" in after


def test_opt_11_refuses_a_filter_with_side_effects():
    source = (
        "import java.util.*;\nimport java.util.stream.*;\nclass A {\n"
        "    void run(List<String> items) {\n"
        "        items.stream().sorted().filter(s -> { System.out.println(s); return true; }).count();\n"
        "    }\n}\n"
    )
    assert "предикате" in refusal(JavaProject({"A.java": source}), "PERF-11")


# --- OPT-12 --------------------------------------------------------------------------------------------------


def test_opt_12_turns_the_wrapper_accumulator_into_a_primitive():
    project = project_of(
        "        Integer total = 0;\n"
        "        for (String s : items) {\n"
        "            total += s.length();\n"
        "        }\n"
        "        System.out.println(total);"
    )
    after = fixed(project, "PERF-12")
    assert "        int total = 0;" in after and "PERF-12" not in rule_ids(after)


def test_opt_12_refuses_when_the_variable_is_used_as_an_object():
    equals = project_of(
        "        Integer total = 0;\n"
        "        for (String s : items) {\n"
        "            total += s.length();\n"
        "        }\n"
        "        System.out.println(total.equals(3));"
    )
    assert "метод" in refusal(equals, "PERF-12")
    identity = project_of(
        "        Integer other = items.size();\n"
        "        Integer total = 0;\n"
        "        for (String s : items) {\n"
        "            total += s.length();\n"
        "        }\n"
        "        System.out.println(total == other);"
    )
    assert "==" in refusal(identity, "PERF-12")


# --- OPT-01 --------------------------------------------------------------------------------------------------

ITEMS = """\
import java.util.*;

class A {
    static class Item {
        private final String id;
        private final int weight;
        private int merged;

        Item(String id, int weight) {
            this.id = id;
            this.weight = weight;
        }

        String id() {
            return id;
        }

        int weight() {
            return weight;
        }

        void absorb(int amount) {
            merged += amount;
        }
    }

    static void merge(Item a, Item b) {
        a.absorb(b.weight());
    }

    static void analyze(List<Item> items) {
        for (Item a : items) {
            for (Item b : items) {
                if (a.id().equals(b.id())) {
                    merge(a, b);
                }
            }
        }
    }
}
"""


def test_opt_01_indexes_the_nested_search_by_key():
    project = JavaProject({"A.java": ITEMS})
    finding = finding_of(project, "PERF-01")
    proposal = propose(project, finding)
    assert proposal is not None and proposal.available and not proposal.behavior_preserving
    after = apply_proposal(project, proposal)["A.java"]
    assert "Map<String, List<Item>> itemsById = new HashMap<>();" in after
    assert "itemsById.computeIfAbsent(b.id(), key -> new ArrayList<>()).add(b);" in after
    assert (
        "for (Item b : itemsById.getOrDefault(a.id(), List.of())) {\n                merge(a, b);"
        in after
    )
    assert "PERF-01" not in rule_ids(after)


def test_opt_01_refuses_unhashable_keys_and_mutating_bodies():
    unhashable = ITEMS.replace(
        "String id() {\n            return id;", "Object id() {\n            return id;"
    )
    proposal = propose(
        JavaProject({"A.java": unhashable}),
        finding_of(JavaProject({"A.java": unhashable}), "PERF-01"),
    )
    assert proposal is not None and not proposal.available
    assert "хеширование" in " ".join(c.reason or "" for c in proposal.failed())
    mutating = ITEMS.replace("merge(a, b);", "merge(a, b);\n                    items.remove(b);")
    project = JavaProject({"A.java": mutating})
    reasons = " ".join(
        c.reason or "" for c in propose(project, finding_of(project, "PERF-01")).failed()
    )
    assert "изменяется items" in reasons
