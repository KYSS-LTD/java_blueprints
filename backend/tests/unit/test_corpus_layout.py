"""Раскладка и содержание корпуса `samples/` (S1.3–S1.6, S1.9). Не требует JDK.

Компиляцию и определённость вывода проверяет `scripts/verify_corpus.py` (джоб CI с JDK 21).
"""

from __future__ import annotations

import json
import re
from collections.abc import Iterator
from pathlib import Path
from typing import get_args

import pytest
from tree_sitter import Node

from app.schemas.graph import NodeKind
from app.schemas.verify import ScenarioImportItem
from engine.java.ts import parse

REPO = Path(__file__).resolve().parents[3]
SAMPLES = REPO / "samples"
SCOPE = SAMPLES / "scope"
SCENARIO_KEYS = set(ScenarioImportItem.model_fields)
SECTIONS = {"01": "1", "02": "2", "03": "3", "04": "4"}


def _scope_ids_from_docs() -> set[str]:
    text = (REPO / "docs" / "04-code-graph-model.md").read_text(encoding="utf-8")
    return set(re.findall(r"`scope/([0-9a-z][0-9a-z-]*)`", text))


def _fixtures() -> list[Path]:
    return sorted(p for p in SCOPE.iterdir() if p.is_dir())


def _samples() -> list[Path]:
    """Каталоги с `scenarios/` — примеры, которые компилируются и запускаются."""
    return sorted(p.parent for p in SAMPLES.rglob("scenarios") if p.is_dir())


def _java_files(root: Path) -> list[Path]:
    return sorted(root.rglob("*.java"))


def _walk(node: Node) -> Iterator[Node]:
    yield node
    for child in node.children:
        yield from _walk(child)


# --- Матрица scope (04 § 9) ---------------------------------------------------------------


def test_scope_table_has_all_expected_rows():
    ids = _scope_ids_from_docs()
    assert len(ids) == 38, sorted(ids)
    assert {i for i in ids if i.startswith("0")} == {i for i in ids if i[0] == "0"}
    assert {"01-classes", "02-output", "03-list", "04-uncaught", "b4-stream"} <= ids


def test_every_table_row_has_a_fixture():
    expected = _scope_ids_from_docs()
    present = {p.name for p in _fixtures()}
    assert expected - present == set(), "нет фикстур для строк таблицы 04 § 9"
    assert present - expected == set(), "фикстуры, которых нет в таблице 04 § 9"


@pytest.mark.parametrize("fixture", _fixtures(), ids=lambda p: p.name)
def test_fixture_files(fixture: Path):
    assert (fixture / "Main.java").is_file()
    meta = json.loads((fixture / "expected_kinds.json").read_text(encoding="utf-8"))
    assert set(meta) == {"description", "section", "kinds"}
    assert meta["description"].strip()
    prefix = fixture.name.split("-")[0]
    assert meta["section"] == SECTIONS.get(prefix, "bonus")
    allowed = set(get_args(NodeKind))
    assert meta["kinds"] and set(meta["kinds"]) <= allowed, set(meta["kinds"]) - allowed
    assert len(meta["kinds"]) == len(set(meta["kinds"]))


def test_scope_readme_lists_every_fixture():
    readme = (SCOPE / "README.md").read_text(encoding="utf-8")
    for fixture in _fixtures():
        assert f"`{fixture.name}`" in readme, fixture.name


# --- Сценарии ----------------------------------------------------------------------------------


def _min_scenarios(sample: Path) -> int:
    if sample.name == "demo-orders":
        return 8
    return 2 if SCOPE in sample.parents else 3


@pytest.mark.parametrize("sample", _samples(), ids=lambda p: p.relative_to(SAMPLES).as_posix())
def test_scenarios_are_valid_import_items(sample: Path):
    files = sorted((sample / "scenarios").glob("*.json"))
    assert len(files) >= _min_scenarios(sample)
    for path in files:
        raw = json.loads(path.read_text(encoding="utf-8"))
        assert set(raw) <= SCENARIO_KEYS, f"{path.name}: лишние поля {set(raw) - SCENARIO_KEYS}"
        item = ScenarioImportItem.model_validate(raw)
        assert item.name == path.stem
        assert item.description
        assert isinstance(raw["stdin"], str)
        assert "expected_stdout" in raw, f"{path.name}: нет expected_stdout (--update-golden)"


@pytest.mark.parametrize("sample", _samples(), ids=lambda p: p.relative_to(SAMPLES).as_posix())
def test_sample_scenario_names_are_unique_and_inputs_differ(sample: Path):
    inputs = [
        json.loads(p.read_text(encoding="utf-8"))["stdin"]
        for p in sorted((sample / "scenarios").glob("*.json"))
    ]
    assert len(inputs) == len(set(inputs)), "два сценария с одинаковым вводом"


def test_corpus_has_stable_line_endings_and_utf8():
    for path in _java_files(SAMPLES):
        data = path.read_bytes()
        assert b"\r" not in data, f"{path}: CRLF (корпус хранится с LF)"
        assert not data.startswith(b"\xef\xbb\xbf"), f"{path}: BOM"
        data.decode("utf-8")


# --- Разбор tree-sitter (I1) --------------------------------------------------------------


@pytest.mark.parametrize(
    "path",
    [p for p in _java_files(SAMPLES) if "broken" not in p.parts],
    ids=lambda p: p.relative_to(SAMPLES).as_posix(),
)
def test_all_valid_samples_parse_without_errors(path: Path):
    parsed = parse(path.read_text(encoding="utf-8"))
    assert parsed.ok, [(i.kind, i.range) for i in parsed.issues]


@pytest.mark.parametrize("path", sorted((SAMPLES / "broken").glob("*.java")), ids=lambda p: p.name)
def test_broken_files_match_their_declared_kind(path: Path):
    header = path.read_text(encoding="utf-8").splitlines()[0]
    parsed = parse(path.read_text(encoding="utf-8"))
    if header == "// broken: syntax":
        assert parsed.issues, "tree-sitter не заметил синтаксической ошибки"
        assert any(n.type == "method_declaration" for n in _walk(parsed.root)), "разбор развалился"
    else:
        assert header == "// broken: semantic"
        assert parsed.ok, "семантическая ошибка не должна ломать разбор"


def test_broken_readme_lists_every_file():
    readme = (SAMPLES / "broken" / "README.md").read_text(encoding="utf-8")
    for path in (SAMPLES / "broken").glob("*.java"):
        assert path.name in readme


# --- Демо-монолит (S1.3) ---------------------------------------------------------------------

DEMO = SAMPLES / "demo-orders" / "Main.java"


def _demo() -> str:
    return DEMO.read_text(encoding="utf-8")


def test_demo_orders_is_about_a_thousand_lines():
    assert 900 <= len(_demo().splitlines()) <= 1100


def test_demo_orders_contains_planted_defects_and_features():
    text = _demo()
    # Дефекты и «оптимизируемые» участки, на которых строятся S7–S9 (docs/10 § 2).
    assert re.search(r"for \(Order \w+ : orders\) \{\s+boolean found = false;\s+for \(Order", text)
    assert "!result.contains(order.getCustomer())" in text  # contains по ArrayList в цикле
    assert "csv += order.getId()" in text  # конкатенация строк в цикле
    assert "order.getCustomer() == VIP_CUSTOMER" in text  # == для строк
    assert re.search(r"catch \(OrderParseException e\) \{\n\s*\}", text)  # пустой catch
    # Бонусный scope и собственные исключения.
    for needle in (
        "record CustomerTotal",
        "enum OrderStatus",
        "static <T> List<T> topN",
        "class OrderParseException extends Exception",
        ".stream()",
        "->",
        "System.out.printf",
        "StandardCharsets.UTF_8",
        "yield ",
    ):
        assert needle in text, needle
    assert re.search(r"[А-Яа-яЁё]", text)


def _structure(path: Path) -> tuple[list[tuple[str, int]], list[str]]:
    """Методы файла (имя, число строк) и имена методов класса Main.

    Диапазоны берутся из ``range_of`` (байты → строки), а ``Node`` не хранятся: ``start_point``/
    ``end_point`` на удержанных узлах роняют py-tree-sitter 0.26 на Python 3.14 (см. docs/03 § 8).
    """
    parsed = parse(path.read_text(encoding="utf-8"))
    spans: list[tuple[str, int]] = []
    main_methods: list[str] = []
    for node in _walk(parsed.root):
        if node.type == "method_declaration":
            span = parsed.range_of(node)
            name = parsed.text(node.child_by_field_name("name"))
            spans.append((name, span.end.line - span.start.line + 1))
        elif (
            node.type == "class_declaration"
            and parsed.text(node.child_by_field_name("name")) == "Main"
        ):
            body = node.child_by_field_name("body")
            main_methods = [
                parsed.text(member.child_by_field_name("name"))
                for member in body.children
                if member.type == "method_declaration"
            ]
    return spans, main_methods


def _demo_structure() -> tuple[list[tuple[str, int]], list[str]]:
    return _structure(DEMO)


def test_demo_orders_has_long_method_clones_and_god_class():
    spans, main_methods = _demo_structure()
    assert max(lines for _, lines in spans) >= 40
    names = {name for name, _ in spans}
    assert {
        "checkCustomer",
        "checkProduct",
        "totalForCustomer",
        "totalForProduct",
        "printByCustomer",
        "printByProduct",
    } <= names  # клоны

    roles = {"parse": 0, "analyze": 0, "optimize": 0, "print": 0}
    for name in main_methods:
        if name.startswith(("parse", "tokenize", "read", "normalize", "require")):
            roles["parse"] += 1
        elif name.startswith(
            ("total", "average", "median", "percentile", "top", "distinct", "find", "count")
        ):
            roles["analyze"] += 1
        elif name.startswith(("merge", "build", "apply", "discount", "round")):
            roles["optimize"] += 1
        elif name.startswith(("print", "format", "pad", "repeat", "bar")):
            roles["print"] += 1
    assert all(count >= 2 for count in roles.values()), roles
    assert len(main_methods) >= 40


def test_demo_orders_scenario_set_covers_required_cases():
    names = {p.stem for p in (DEMO.parent / "scenarios").glob("*.json")}
    required = {
        "empty-input",
        "single-order",
        "many-orders",
        "bad-format",
        "uncaught-exception",
        "cyrillic",
        "boundary-values",
        "large-input",
    }
    assert required <= names
    crash = json.loads(
        (DEMO.parent / "scenarios" / "uncaught-exception.json").read_text(encoding="utf-8")
    )
    assert crash["expected_exit_code"] == 1


def test_analyzer_pairs_has_the_slide_5_fragment():
    """Фрагмент слайда 5 кейса: вложенный перебор одной коллекции и merge(a, b)."""
    text = (SAMPLES / "analyzer-pairs" / "Analyzer.java").read_text(encoding="utf-8")
    slide = re.compile(
        r"for \(Item a : items\) \{\s+for \(Item b : items\) \{\s+"
        r"if \(a\.id\(\)\.equals\(b\.id\(\)\)\) \{\s+merge\(a, b\);\s+\}\s+\}\s+\}"
    )
    assert slide.search(text)
    assert "findPairs" in text


# --- Дополнительные монолиты (S1.7) ------------------------------------------------------------

MONOLITHS = SAMPLES / "monoliths"

# Заведомые дефекты и «оптимизируемые» участки (docs/10 § 2): подстрока исходника на каждый.
PLANTED = {
    "text-analyzer": {
        "вложенный поиск": "for (String kept : result)",
        "contains в цикле": "!unique.contains(word)",
        "конкатенация в цикле": "rebuilt += token",
        "== для строк": "word.toLowerCase() == CONJUNCTION",
        "клоны": ("countVowels", "countConsonants", "countDigits"),
    },
    "inventory": {
        "вложенный поиск": "for (Product existing : merged)",
        "contains в цикле": "!result.contains(product.getCategory())",
        "конкатенация в цикле": "csv += product.getSku()",
        "== для строк": "product.getCategory() == FOOD",
        "клоны": ("checkSku", "checkName", "checkCategory"),
    },
    "expression-calculator": {
        "вложенный поиск": "for (ExprCount entry : result)",
        "contains в цикле": "!names.contains(node.getText())",
        "конкатенация в цикле": 'result += item + " "',
        "== для строк": "name.toLowerCase() == PI_NAME",
        "клоны": ("parseComparison", "parseAdditive", "parseMultiplicative"),
    },
    "graph-paths": {
        "вложенный поиск": "for (Edge existing : merged)",
        "contains в цикле": "visited.contains(start)",
        "конкатенация в цикле": 'text += " -> "',
        "== для строк": "name == HUB_NAME",
        "клоны": ("countIsolated", "countLeaves", "countHubs"),
    },
    "library": {
        "вложенный поиск": "for (Item existing : merged)",
        "contains в цикле": "!authors.contains(author)",
        "конкатенация в цикле": "csv += item.getId()",
        "== для строк": "a.getTitle() == b.getTitle()",
        "клоны": ("checkTitle", "checkAuthor", "checkReaderName"),
    },
}


def _monolith_dirs() -> list[Path]:
    return sorted(p for p in MONOLITHS.iterdir() if p.is_dir())


def test_there_are_at_least_four_monoliths_for_distinct_topics():
    names = {p.name for p in _monolith_dirs()}
    assert set(PLANTED) == names, "каждому монолиту нужна запись в PLANTED"
    assert len(names) >= 4


@pytest.mark.parametrize("sample", _monolith_dirs(), ids=lambda p: p.name)
def test_monolith_has_planted_defects_and_scope_features(sample: Path):
    text = (sample / "Main.java").read_text(encoding="utf-8")
    assert 600 <= len(text.splitlines()) <= 1200
    planted = PLANTED[sample.name]
    for title, needle in planted.items():
        if title == "клоны":
            continue
        assert needle in text, f"{sample.name}: нет дефекта «{title}»"
    assert re.search(r"catch \(\w+ e\) \{\s*\}", text), "нет пустого catch"
    for needle in (
        "record ",
        "enum ",
        "static <T",
        ".stream()",
        "System.out.printf",
        "StandardCharsets.UTF_8",
        "extends Exception",
        "extends RuntimeException",
        "default:",
        "implements",
    ):
        if needle == "implements" and sample.name != "inventory":
            continue
        assert needle in text, f"{sample.name}: нет «{needle}»"
    assert re.search(r"[A-Za-z_)]\s->\s[A-Za-z_(]", text), "нет лямбды"
    assert re.search(r"[А-Яа-яЁё]", text)


@pytest.mark.parametrize("sample", _monolith_dirs(), ids=lambda p: p.name)
def test_monolith_structure_long_method_clones_and_god_class(sample: Path):
    spans, main_methods = _structure(sample / "Main.java")
    assert max(lines for _, lines in spans) >= 40, "нет метода длиннее 40 строк"
    assert len(main_methods) >= 40, f"в Main только {len(main_methods)} методов"
    names = {name for name, _ in spans}
    assert set(PLANTED[sample.name]["клоны"]) <= names
    sizes = dict(spans)
    clone_sizes = [sizes[name] for name in PLANTED[sample.name]["клоны"]]
    assert min(clone_sizes) >= 6, clone_sizes
    assert max(clone_sizes) - min(clone_sizes) <= 3, f"клоны слишком разные: {clone_sizes}"


@pytest.mark.parametrize("sample", _monolith_dirs(), ids=lambda p: p.name)
def test_monolith_scenarios_cover_required_cases(sample: Path):
    folder = sample / "scenarios"
    scenarios = {p.stem: json.loads(p.read_text(encoding="utf-8")) for p in folder.glob("*.json")}
    assert len(scenarios) >= 8
    assert {
        "empty-input",
        "bad-format",
        "uncaught-exception",
        "boundary-values",
        "large-input",
    } <= set(scenarios)
    assert scenarios["empty-input"]["stdin"] == ""
    assert scenarios["uncaught-exception"]["expected_exit_code"] == 1
    assert all(
        "expected_exit_code" not in s for n, s in scenarios.items() if n != "uncaught-exception"
    )
    assert len(scenarios["large-input"]["stdin"].splitlines()) >= 250
    assert any(re.search(r"[А-Яа-яЁё]", s["stdin"]) for s in scenarios.values())


@pytest.mark.parametrize("sample", _monolith_dirs(), ids=lambda p: p.name)
def test_monolith_readme_is_one_line(sample: Path):
    lines = [
        line
        for line in (sample / "README.md").read_text(encoding="utf-8").splitlines()
        if line.strip()
    ]
    assert len(lines) == 1


def test_monoliths_readme_lists_every_monolith():
    readme = (MONOLITHS / "README.md").read_text(encoding="utf-8")
    for sample in _monolith_dirs():
        assert f"`{sample.name}/`" in readme
