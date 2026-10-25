"""Модель стоимости Big O (S7.1): таблица «фрагмент → сложность» и цепочка вкладов (docs/05 § 3)."""

from __future__ import annotations

import pytest

from engine.analysis.complexity import EXP, LOGN, N2, NLOGN, ONE, Cost, N
from engine.analysis.cost_model import CostModel
from engine.java.project import JavaProject


def estimate(body: str, members: str = "", imports: str = "") -> tuple[Cost, str]:
    """Стоимость метода ``run`` из фрагмента; возвращает также уверенность."""
    source = f"{imports}\nimport java.util.*;\nimport java.util.stream.*;\nclass A {{\n{members}\n{body}\n}}\n"
    project = JavaProject({"A.java": source})
    found = CostModel(project).method(next(m.id for m in project.members() if m.name == "run"))
    assert found is not None
    return found.cost, "heuristic" if found.result.notes else "exact"


def method(body: str, signature: str = "void run(List<Integer> list, int n)") -> str:
    return f"{signature} {{\n{body}\n}}"


CASES: list[tuple[str, str, Cost]] = [
    ("пусто", "int x = 1; x++;", ONE),
    ("цикл до n", "for (int i = 0; i < n; i++) { sum(i); }", N),
    ("цикл до константы", "for (int i = 0; i < 10; i++) { n++; }", ONE),
    ("цикл до константы-имени", "final int K = 5; for (int i = 0; i < K; i++) { n++; }", ONE),
    ("цикл по размеру списка", "for (int i = 0; i < list.size(); i++) { n++; }", N),
    ("for each по списку", "for (int x : list) { n += x; }", N),
    ("for each по литералу", "for (int x : List.of(1, 2, 3)) { n += x; }", ONE),
    ("вложенные циклы", "for (int i = 0; i < n; i++) { for (int j = 0; j < n; j++) { n++; } }", N2),
    (
        "треугольный цикл",
        "for (int i = 0; i < n; i++) { for (int j = i + 1; j < n; j++) { n++; } }",
        N2,
    ),
    (
        "последовательные циклы",
        "for (int i = 0; i < n; i++) { n++; } for (int j = 0; j < n; j++) { n--; }",
        N,
    ),
    ("удвоение счётчика", "for (int i = 1; i < n; i *= 2) { n++; }", LOGN),
    ("деление счётчика", "for (int i = n; i > 0; i /= 2) { n++; }", LOGN),
    ("шаг k", "for (int i = 0; i < n; i += 2) { n++; }", N),
    ("n log n", "for (int i = 0; i < n; i++) { for (int j = 1; j < n; j *= 2) { n++; } }", NLOGN),
    (
        "while hasNext",
        "Iterator<Integer> it = list.iterator(); while (it.hasNext()) { it.next(); }",
        N,
    ),
    ("while со счётчиком", "int i = 0; while (i < n) { i++; }", N),
    (
        "двоичный поиск",
        "int lo = 0, hi = n; while (lo <= hi) { int mid = (lo + hi) / 2; if (mid > 3) hi = mid - 1; else lo = mid + 1; }",
        LOGN,
    ),
    ("if берёт максимум", "if (n > 0) { for (int i = 0; i < n; i++) { n++; } } else { n = 1; }", N),
    (
        "switch берёт максимум",
        "switch (n) { case 1: n = 2; break; case 2: for (int i = 0; i < n; i++) { n++; } break; default: break; }",
        N,
    ),
    (
        "try/catch/finally",
        "try { for (int i = 0; i < n; i++) { n++; } } catch (Exception e) { n = 0; } finally { n++; }",
        N,
    ),
    ("sort", "Collections.sort(list);", NLOGN),
    ("sort в цикле", "for (int i = 0; i < n; i++) { Collections.sort(list); }", Cost(False, 2, 1)),
    ("contains по ArrayList в цикле", "for (int x : list) { if (list.contains(x)) { n++; } }", N2),
    (
        "contains по HashSet в цикле",
        "Set<Integer> s = new HashSet<>(list); for (int x : list) { if (s.contains(x)) { n++; } }",
        N,
    ),
    (
        "get по LinkedList в цикле",
        "LinkedList<Integer> l = new LinkedList<>(list); for (int i = 0; i < l.size(); i++) { n += l.get(i); }",
        N2,
    ),
    (
        "TreeMap в цикле",
        "TreeMap<Integer, Integer> m = new TreeMap<>(); for (int x : list) { m.put(x, 1); }",
        NLOGN,
    ),
    (
        "HashMap в цикле",
        "Map<Integer, Integer> m = new HashMap<>(); for (int x : list) { m.merge(x, 1, Integer::sum); }",
        N,
    ),
    ("stream", "list.stream().filter(x -> x > 1).map(x -> x * 2).collect(Collectors.toList());", N),
    ("stream sorted", "list.stream().sorted().collect(Collectors.toList());", NLOGN),
    ("forEach с телом O(1)", "list.forEach(x -> System.out.println(x));", N),
    (
        "forEach с contains в лямбде",
        "list.forEach(x -> { if (list.contains(x)) { System.out.println(x); } });",
        N2,
    ),
    ("removeIf", "list.removeIf(x -> x > 3);", N),
    ("конкатенация строк", 'String s = ""; for (int i = 0; i < n; i++) { s += i; }', N),
    ("println — O(1)", "System.out.println(n);", ONE),
    ("копирование коллекции", "List<Integer> copy = new ArrayList<>(list);", N),
    (
        "цикл по массиву",
        "int[] a = new int[n]; for (int i = 0; i < a.length; i++) { a[i] = i; }",
        N,
    ),
    (
        "цикл по литеральному массиву",
        "int[] a = {1, 2, 3}; for (int i = 0; i < a.length; i++) { n += a[i]; }",
        ONE,
    ),
]


@pytest.mark.parametrize(("title", "body", "expected"), CASES, ids=[c[0] for c in CASES])
def test_fragment_complexity(title: str, body: str, expected: Cost):
    cost, _ = estimate(method(body))
    assert cost == expected, f"{title}: {cost.format()} вместо {expected.format()}"


def test_call_to_project_method_inherits_its_cost():
    helper = "int sum(List<Integer> l) { int s = 0; for (int x : l) { s += x; } return s; }"
    cost, _ = estimate(method("for (int i = 0; i < n; i++) { sum(list); }"), helper)
    assert cost == N2
    cost, _ = estimate(method("sum(list);"), helper)
    assert cost == N


def test_recursion_patterns():
    linear = "int f(int n) { if (n <= 1) return 1; return n * f(n - 1); }"
    fib = "int f(int n) { if (n < 2) return n; return f(n - 1) + f(n - 2); }"
    halving = "int f(int n) { if (n <= 1) return 0; return 1 + f(n / 2); }"
    for text, expected in ((linear, N), (fib, EXP), (halving, LOGN)):
        project = JavaProject({"A.java": f"class A {{ {text} }}"})
        found = CostModel(project).method("A.java#A.f(int)")
        assert found is not None and found.cost == expected, (text, found.cost.format())
        assert found.recursion


def test_exact_and_heuristic_confidence():
    _, exact = estimate(method("for (int i = 0; i < n; i++) { n--; }"))
    assert exact == "exact"
    _, heuristic = estimate(
        method("int i = 0; while (n > 0) { n = n - 1; i = i + 1; foo(); }"), "void foo() {}"
    )
    assert heuristic == "heuristic"


def test_contributions_explain_the_nested_loop():
    source = """class A {
    void run(List<Integer> items) {
        for (int a : items) {
            for (int b : items) {
                if (a == b) { System.out.println(a); }
            }
        }
    }
}"""
    project = JavaProject({"A.java": source})
    found = CostModel(project).method("A.java#A.run(List<Integer>)")
    assert found is not None and found.cost == N2
    chain = found.result.chain
    assert [c.factor for c in chain] == ["n", "n"]
    assert [c.node_id for c in chain] == [
        "A.java#A.run(List<Integer>)/foreach[0]",
        "A.java#A.run(List<Integer>)/foreach[0]/foreach[0]",
    ]
    assert "items" in chain[0].why and "n элементов" in chain[0].why
    assert found.node_costs["A.java#A.run(List<Integer>)/foreach[0]"].cost == N2
    assert found.node_costs["A.java#A.run(List<Integer>)/foreach[0]/foreach[0]"].cost == N


def test_cost_formatting():
    assert ONE.format() == "O(1)"
    assert N.format() == "O(n)"
    assert N2.format() == "O(n²)"
    assert NLOGN.format() == "O(n log n)"
    assert LOGN.format() == "O(log n)"
    assert Cost(False, 2, 1).format() == "O(n² log n)"
    assert EXP.format() == "O(2ⁿ)"
    assert N * N == N2 and N + N2 == N2 and EXP * N == EXP and max(N, NLOGN) == NLOGN


# --- амортизация: общий курсор, общий счётчик, обход дерева, итерируемое один раз -----------------------------------

PARSER = """
class A {
    private final List<String> tokens = new ArrayList<>();
    private int pos;

    private String peek() { return pos < tokens.size() ? tokens.get(pos) : ""; }
    private String next() { return tokens.get(pos++); }

    int run() { return parseSum(); }

    private int parseSum() {
        int left = parseProduct();
        while (peek().equals("+")) {
            next();
            left += parseProduct();
        }
        return left;
    }

    private int parseProduct() {
        int left = parseUnary();
        while (peek().equals("*")) {
            next();
            left *= parseUnary();
        }
        return left;
    }

    private int parseUnary() {
        if (peek().equals("-")) {
            next();
            return -parseUnary();
        }
        if (peek().equals("(")) {
            next();
            int inner = parseSum();
            next();
            return inner;
        }
        return Integer.parseInt(next());
    }
}
"""


def program_cost(source: str) -> tuple[Cost, str]:
    project = JavaProject({"A.java": "import java.util.*;\n" + source})
    found = CostModel(project).method(next(m.id for m in project.members() if m.name == "run"))
    assert found is not None
    return found.cost, "heuristic" if found.result.notes else "exact"


def test_a_recursive_descent_parser_costs_one_pass_over_the_tokens():
    # циклы разных уровней забирают токены из одного курсора: вместе они делают не больше n шагов
    cost, confidence = program_cost(PARSER)
    assert cost == N
    assert confidence == "heuristic"


def test_a_tokenizer_whose_inner_loop_moves_the_outer_index_is_linear():
    cost, _ = estimate(
        method(
            "int i = 0; int count = 0;\n"
            "while (i < n) {\n"
            "    if (list.get(i) > 0) {\n"
            "        while (i < n && list.get(i) > 0) { i++; }\n"
            "        count++;\n"
            "    } else { i++; }\n"
            "}"
        )
    )
    assert cost == N


def test_recursion_over_the_children_of_a_node_visits_every_node_once():
    cost, _ = program_cost(
        """
class A {
    static class Node { List<Node> children = new ArrayList<>(); int value; }
    int run() { return total(new Node()); }
    private int total(Node node) {
        int sum = node.value;
        for (Node child : node.children) {
            sum += total(child);
        }
        return sum;
    }
}
"""
    )
    assert cost == N


def test_the_iterable_of_a_for_each_is_evaluated_once_not_per_iteration():
    cost, _ = estimate(
        method("for (int x : sorted(list)) { n += x; }"),
        members="List<Integer> sorted(List<Integer> items) { List<Integer> copy = new ArrayList<>(items); Collections.sort(copy); return copy; }",
    )
    assert cost == NLOGN


def test_loops_over_independent_counters_still_multiply():
    # у внутреннего цикла свой счётчик: это настоящее вложение, а не общий индекс
    cost, _ = estimate(
        method("int i = 0; while (i < n) { int j = 0; while (j < n) { j++; } i++; }")
    )
    assert cost == N2
