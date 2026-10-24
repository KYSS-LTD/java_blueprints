"""Генератор JSON-моков для frontend (S1.2): `frontend/src/mocks/fixtures/*.json`.

    cd backend && uv run python ../scripts/gen_mock_fixtures.py          # перезаписать фикстуры
    cd backend && uv run python ../scripts/gen_mock_fixtures.py --check  # только сверить с диском

Фикстуры строятся из `samples/demo-orders/Main.java` настоящим движком (`engine.java.project`):
структура, дерево и блок-схемы всех методов — те же, что отдаст backend после S3. Находки, метрики,
сценарии и запуски — подготовленные данные демо. Каждая фикстура перед записью проверяется
Pydantic-схемой контракта (`app.schemas`), так что моки не расходятся с OpenAPI.
"""

from __future__ import annotations

import argparse
import dataclasses
import json
import re
import sys
from pathlib import Path
from typing import Any

REPO = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(REPO / "backend"))

from pydantic import TypeAdapter  # noqa: E402

from app.schemas.analysis import AnalysisResponse, MetricsResponse  # noqa: E402
from app.schemas.graph import FlowGraph, ProjectTree, StructureGraph  # noqa: E402
from app.schemas.health import HealthResponse  # noqa: E402
from app.schemas.ops import Palette  # noqa: E402
from app.schemas.project import (  # noqa: E402
    FileContent,
    FileList,
    ProjectDetail,
    ProjectList,
    SampleList,
    VersionList,
)
from app.schemas.transform import (  # noqa: E402
    DecomposePlan,
    ExtractMethodPreview,
    ExtractMethodTarget,
    FixList,
)
from app.services import decompose as decompose_service  # noqa: E402
from app.services import extract as extract_service  # noqa: E402
from engine.transform.decompose.plan import build_plan  # noqa: E402
from app.schemas.verify import BuildResult, RunEvent, RunInfo, RunList, ScenarioList  # noqa: E402
from engine.graph.model import ApiRange  # noqa: E402
from engine.java.project import JavaProject  # noqa: E402

SOURCE_PATH = REPO / "samples" / "demo-orders" / "Main.java"
OUT = REPO / "frontend" / "src" / "mocks" / "fixtures"
FILE = "Main.java"
NOW = "2026-10-24T08:00:00Z"

COMPLEXITY = {
    "mergeDuplicates": "O(n·m)",
    "distinctCustomers": "O(n·m)",
    "buildCsv": "O(n²)",
    "findOrderById": "O(n)",
}
CONTENT_HASH = "mock-demo-orders-v3"
LOOPS = {"loop_foreach", "loop_for", "loop_while"}


class Source:
    """Текст демо-монолита и проект движка; диапазоны членов — через разметку строк движка."""

    def __init__(self) -> None:
        self.text = SOURCE_PATH.read_text(encoding="utf-8")
        self.project = JavaProject({FILE: self.text})
        self.parsed = self.project.parsed(FILE)
        assert not self.project.syntax_issues()

    def rng(self, item: dict) -> dict:
        span = self.parsed.lines.range(item["start_byte"], item["end_byte"])
        return dataclasses.asdict(ApiRange.of(FILE, span))

    def txt(self, item: dict) -> str:
        return self.parsed.source[item["start_byte"] : item["end_byte"]].decode("utf-8")


def collect_members(src: Source) -> list[dict]:
    return [
        {
            "id": m.id,
            "type": m.type_path,
            "kind": m.kind,
            "name": m.name,
            "key": m.key,
            "params": [p.type for p in m.params],
            "start_byte": m.start_byte,
            "end_byte": m.end_byte,
        }
        for m in src.project.members()
    ]


def member_id(m: dict) -> str:
    return m["id"]


def stamp(graph: dict) -> dict:
    graph["version_id"] = 3
    graph["content_hash"] = CONTENT_HASH
    return graph


def build_structure(src: Source) -> dict:
    return stamp(dataclasses.asdict(src.project.structure("methods")))


def build_tree(src: Source) -> dict:
    tree = dataclasses.asdict(src.project.tree())
    tree["version_id"] = 3
    return tree


def build_flows(src: Source) -> dict[str, dict]:
    """Блок-схемы всех методов, конструкторов и инициализаторов по id члена."""
    return {g.method_id: stamp(dataclasses.asdict(g)) for g in src.project.flows()}


def apply_findings(structure: dict, tree: dict, flows: dict[str, dict], findings: dict) -> None:
    """Накладывает на графы находки (бейджи, счётчики) и оценки сложности демо."""
    by_method: dict[str, dict[str, int]] = {}
    for finding in findings["findings"]:
        method_id = finding["location"]["method_id"]
        if method_id:
            counts = by_method.setdefault(method_id, {"high": 0, "medium": 0, "low": 0})
            counts[finding["severity"]] += 1
        node_id = finding["location"]["node_id"]
        method_flow = flows.get(method_id or "")
        if node_id and method_flow and finding["category"] == "PERF":
            for node in method_flow["nodes"]:
                if node["id"] == node_id:
                    node["badges"].append(
                        {"kind": "defect", "label": finding["rule_id"], "ref": finding["id"]}
                    )
    complexity_by_id: dict[str, str] = {}
    for node in structure["nodes"]:
        if node["node_type"] == "member" and node["name"] in COMPLEXITY and node["parent_id"].endswith("#Main"):
            node["complexity"] = COMPLEXITY[node["name"]]
            complexity_by_id[node["id"]] = node["complexity"]
        counts = by_method.get(node["id"])
        if counts:
            node["findings_count"] = sum(counts.values())

    def tree_pass(items: list[dict]) -> None:
        for item in items:
            counts = by_method.get(item["node_id"] or "")
            if counts:
                item["findings"] = counts
            if item["node_id"] in complexity_by_id:
                item["complexity"] = complexity_by_id[item["node_id"]]
            tree_pass(item["children"])

    tree_pass(tree["roots"])
    for method_id, complexity in complexity_by_id.items():
        flow = flows[method_id]
        flow["nodes"][0]["complexity"] = complexity
        for node in flow["nodes"]:
            if node["kind"] in LOOPS and node["parent_id"] is None:
                node["complexity"] = complexity


def find_line(src: Source, needle: str, start: int = 0) -> int:
    for i, line in enumerate(src.text.splitlines(), 1):
        if i > start and needle in line:
            return i
    raise KeyError(needle)


def method_range(members: list[dict], src: Source, name: str) -> dict:
    return src.rng(next(m for m in members if m["type"] == "Main" and m["name"] == name))


def build_findings(src: Source, members: list[dict], flows: dict[str, dict]) -> dict:
    def loc(path_id: str, flow_name: str) -> dict:
        node = next(n for n in flows[flow_name]["nodes"] if n["id"].endswith(path_id))
        return {
            "file": FILE,
            "start_line": node["range"]["start_line"],
            "end_line": node["range"]["end_line"],
            "node_id": node["id"],
            "method_id": flows[flow_name]["method_id"],
        }

    def mid(name: str) -> str:
        return flows[name]["method_id"] if name in flows else member_id(
            next(m for m in members if m["type"] == "Main" and m["name"] == name)
        )

    vip_line = find_line(src, "order.getCustomer() == VIP_CUSTOMER")
    catch_line = find_line(src, "catch (OrderParseException e) {", find_line(src, "private List<Order> readBatch"))
    report = method_range(members, src, "printReport")
    pre_ok = lambda name: {"name": name, "passed": True, "reason": None}  # noqa: E731
    findings = [
        {
            "id": f"PERF-01:{loc('foreach[0]', 'mergeDuplicates')['node_id']}",
            "rule_id": "PERF-01",
            "severity": "high",
            "category": "PERF",
            "title": "Вложенный цикл — O(n·m)",
            "location": loc("foreach[0]", "mergeDuplicates"),
            "explanation": "Внешний цикл по orders для каждого заказа обходит список merged целиком в поисках пары (клиент, товар, статус). Число сравнений растёт как n·m, где m — число различных пар.",
            "defect": "Повторный линейный поиск по уже собранному списку",
            "risk": "На 8000 заказов метод занимает секунды и растёт квадратично",
            "recommendation": "Индексировать merged по ключу (клиент, товар, статус) в LinkedHashMap",
            "complexity": {
                "current": "O(n·m)",
                "after": "O(n)",
                "confidence": "exact",
                "contributions": [
                    {"node_id": loc("foreach[0]", "mergeDuplicates")["node_id"], "factor": "n", "why": "цикл по всем заказам"},
                    {"node_id": loc("foreach[0]/foreach[0]", "mergeDuplicates")["node_id"], "factor": "m", "why": "поиск по списку merged"},
                ],
                "reason": None,
            },
            "fix": {
                "fix_id": "OPT-01",
                "available": True,
                "behavior_preserving": True,
                "preconditions": [
                    pre_ok("Внутренний цикл — поиск совпадения по ключу"),
                    pre_ok("Тело зависит только от пары"),
                    pre_ok("Порядок сохраняется (LinkedHashMap)"),
                ],
            },
            "evidence": {
                "static": [
                    {"kind": "loop", "text": "for (Order candidate : orders)", "node_id": loc("foreach[0]", "mergeDuplicates")["node_id"]},
                    {"kind": "loop", "text": "for (Order existing : merged)", "node_id": loc("foreach[0]/foreach[0]", "mergeDuplicates")["node_id"]},
                ],
                "dynamic": None,
            },
        },
        {
            "id": f"PERF-02:{loc('foreach[0]', 'distinctCustomers')['node_id']}",
            "rule_id": "PERF-02",
            "severity": "high",
            "category": "PERF",
            "title": "contains по ArrayList в цикле — O(n·m)",
            "location": loc("foreach[0]", "distinctCustomers"),
            "explanation": "result.contains(...) выполняет линейный поиск по ArrayList на каждой итерации цикла по заказам.",
            "defect": "Список используется как множество",
            "risk": "Квадратичный рост при увеличении числа клиентов",
            "recommendation": "Хранить встреченных клиентов в HashSet, список оставить для порядка",
            "complexity": {"current": "O(n·m)", "after": "O(n)", "confidence": "exact",
                           "contributions": [{"node_id": loc("foreach[0]", "distinctCustomers")["node_id"], "factor": "n", "why": "цикл по заказам"}],
                           "reason": None},
            "fix": {"fix_id": "OPT-02", "available": True, "behavior_preserving": True,
                    "preconditions": [pre_ok("Список не изменяется иначе как добавлением"), pre_ok("String имеет корректные equals/hashCode")]},
            "evidence": {"static": [{"kind": "call", "text": "result.contains(order.getCustomer())", "node_id": loc("foreach[0]/if[0]", "distinctCustomers")["node_id"]}], "dynamic": None},
        },
        {
            "id": f"PERF-03:{loc('foreach[0]', 'buildCsv')['node_id']}",
            "rule_id": "PERF-03",
            "severity": "medium",
            "category": "PERF",
            "title": "Конкатенация строк в цикле — O(n²)",
            "location": loc("foreach[0]", "buildCsv"),
            "explanation": "Переменная csv типа String накапливается оператором += в цикле: каждый шаг копирует всю строку.",
            "defect": "Копирование строки на каждой итерации",
            "risk": "Время и память растут квадратично с числом заказов",
            "recommendation": "Накапливать в StringBuilder и вызвать toString() после цикла",
            "complexity": {"current": "O(n²)", "after": "O(n)", "confidence": "exact",
                           "contributions": [{"node_id": loc("foreach[0]", "buildCsv")["node_id"], "factor": "n", "why": "копирование растущей строки"}],
                           "reason": None},
            "fix": {"fix_id": "OPT-03", "available": True, "behavior_preserving": True,
                    "preconditions": [pre_ok("Переменная локальная и типа String"), pre_ok("Используется только для накопления")]},
            "evidence": {"static": [{"kind": "assign", "text": "csv += …", "node_id": loc("foreach[0]/assign[0]", "buildCsv")["node_id"]}], "dynamic": None},
        },
        {
            "id": f"BUG-01:{FILE}#Main.discountFor(Order):{vip_line}",
            "rule_id": "BUG-01",
            "severity": "high",
            "category": "BUG",
            "title": "Сравнение строк по ==",
            "location": {"file": FILE, "start_line": vip_line, "end_line": vip_line, "node_id": None, "method_id": member_id(next(m for m in members if m["name"] == "discountFor"))},
            "explanation": "order.getCustomer() == VIP_CUSTOMER сравнивает ссылки, а не содержимое: имя клиента приходит из ввода и никогда не совпадает с константой.",
            "defect": "Скидка VIP-клиенту никогда не применяется",
            "risk": "Тихая потеря скидки",
            "recommendation": "Заменить на VIP_CUSTOMER.equals(order.getCustomer())",
            "complexity": None,
            "fix": {"fix_id": "FIX-01", "available": True, "behavior_preserving": False,
                    "preconditions": [pre_ok("Оба операнда типа String")]},
            "evidence": {"static": [{"kind": "literal", "text": "== VIP_CUSTOMER", "node_id": None}], "dynamic": None},
        },
        {
            "id": f"BUG-02:{FILE}#Main.readBatch(Scanner, int):{catch_line}",
            "rule_id": "BUG-02",
            "severity": "medium",
            "category": "BUG",
            "title": "Пустой catch",
            "location": {"file": FILE, "start_line": catch_line, "end_line": catch_line + 1, "node_id": None, "method_id": member_id(next(m for m in members if m["name"] == "readBatch"))},
            "explanation": "Блок catch (OrderParseException e) пуст: битые строки пакета молча пропускаются и не попадают в счётчик ошибок.",
            "defect": "Исключение проглатывается",
            "risk": "Ошибки ввода остаются незамеченными",
            "recommendation": "Вывести сообщение в stderr и увеличить errorCount",
            "complexity": None,
            "fix": {"fix_id": "FIX-02", "available": True, "behavior_preserving": False,
                    "preconditions": [pre_ok("Тело catch пусто")]},
            "evidence": {"static": [{"kind": "catch", "text": "catch (OrderParseException e) { }", "node_id": None}], "dynamic": None},
        },
        {
            "id": f"MAINT-01:{member_id(next(m for m in members if m['name'] == 'printReport'))}",
            "rule_id": "MAINT-01",
            "severity": "low",
            "category": "MAINT",
            "title": "Длинный метод",
            "location": {"file": FILE, "start_line": report["start_line"], "end_line": report["end_line"], "node_id": None, "method_id": member_id(next(m for m in members if m["name"] == "printReport"))},
            "explanation": f"Метод printReport занимает {report['end_line'] - report['start_line'] + 1} строк при пороге 30.",
            "defect": "Метод делает много независимых шагов",
            "risk": "Сложно читать и менять",
            "recommendation": "Выделить разделы отчёта в отдельные методы",
            "complexity": None,
            "fix": {"fix_id": "REF-01", "available": False, "behavior_preserving": True,
                    "preconditions": [{"name": "Выделяемый фрагмент не меняет несколько переменных", "passed": False, "reason": "Требуется выбор фрагмента (этап S9)"}]},
            "evidence": {"static": [{"kind": "metric", "text": "LOC > 30", "node_id": None}], "dynamic": None},
        },
        {
            "id": f"MAINT-05:{FILE}#Main",
            "rule_id": "MAINT-05",
            "severity": "medium",
            "category": "MAINT",
            "title": "God-class: разбор, анализ, преобразования и вывод в одном классе",
            "location": {"file": FILE, "start_line": find_line(src, "public class Main {"), "end_line": find_line(src, "interface Describable") - 2, "node_id": f"{FILE}#Main", "method_id": None},
            "explanation": "Класс Main содержит более 40 методов четырёх ролей (parse, analyze, optimize, print) и общее состояние.",
            "defect": "Одна ответственность на всё",
            "risk": "Любое изменение затрагивает весь файл",
            "recommendation": "Разбить на Parser, Analyzer, Optimizer, Printer",
            "complexity": None,
            "fix": {"fix_id": "REF-04", "available": False, "behavior_preserving": True,
                    "preconditions": [{"name": "Кластеры членов найдены", "passed": True, "reason": None}]},
            "evidence": {"static": [{"kind": "metric", "text": "методов > 20", "node_id": None}], "dynamic": None},
        },
    ]
    return {
        "version_id": 3,
        "findings": findings,
        "summary": {
            "total": len(findings),
            "by_severity": {"high": 3, "medium": 3, "low": 1},
            "by_category": {"PERF": 3, "BUG": 2, "MAINT": 2},
            "fixable": 5,
        },
        "complexity": {"current": "O(n²)", "optimized": "O(n)"},
        "metrics": build_metrics(src, members)["metrics"],
    }


def cyclomatic(text: str) -> int:
    code = re.sub(r'"(?:[^"\\\n]|\\.)*"', '""', text)
    code = re.sub(r"//[^\n]*", "", code)
    return 1 + len(re.findall(r"\b(?:if|for|while|case|catch)\b|&&|\|\||\?", code))


def build_metrics(src: Source, members: list[dict]) -> dict:
    method_metrics = []
    for m in members:
        if m["kind"] != "method":
            continue
        rng = src.rng(m)
        text = src.txt(m)
        method_metrics.append(
            {
                "method_id": member_id(m),
                "name": m["name"],
                "file": FILE,
                "lines": rng["end_line"] - rng["start_line"] + 1,
                "cyclomatic": cyclomatic(text),
                "cognitive": cyclomatic(text) + text.count("{") // 6,
                "max_nesting": max(1, text.count("{") // 5),
                "params": len(m["params"]),
                "complexity": COMPLEXITY.get(m["name"]),
            }
        )
    by_type: dict[str, list[dict]] = {}
    for m in members:
        by_type.setdefault(m["type"], []).append(m)
    type_metrics = []
    for name, ms in by_type.items():
        lines = sum(src.rng(m)["end_line"] - src.rng(m)["start_line"] + 1 for m in ms)
        type_metrics.append(
            {
                "type_id": f"{FILE}#{name}",
                "name": name,
                "file": FILE,
                "lines": lines,
                "methods": sum(1 for m in ms if m["kind"] == "method"),
                "fields": sum(1 for m in ms if m["kind"] == "field"),
                "lcom4": 4.0 if name == "Main" else 1.0,
            }
        )
    cyc = [m["cyclomatic"] for m in method_metrics]
    total_lines = len(src.text.splitlines())
    return {
        "version_id": 3,
        "metrics": {
            "totals": {
                "lines": total_lines,
                "code_lines": sum(1 for line in src.text.splitlines() if line.strip() and not line.strip().startswith(("//", "*", "/*"))),
                "files": 1,
                "types": len(by_type),
                "methods": len(method_metrics),
                "cyclomatic_avg": round(sum(cyc) / len(cyc), 2),
                "cyclomatic_max": max(cyc),
                "duplication_percent": 6.5,
                "lcom4_avg": 1.5,
            },
            "methods": method_metrics,
            "types": type_metrics,
        },
        "compare_to_version_id": None,
        "compare_to": None,
    }


def build_scenarios() -> dict:
    scenarios = []
    folder = REPO / "samples" / "demo-orders" / "scenarios"
    for i, path in enumerate(sorted(folder.glob("*.json")), 1):
        raw = json.loads(path.read_text(encoding="utf-8"))
        stdin = raw["stdin"]
        if len(stdin) > 1200:
            stdin = "\n".join(stdin.splitlines()[:12]) + "\n"  # в моке большой ввод усечён
        scenarios.append(
            {
                "id": i,
                "name": raw["name"],
                "description": raw["description"],
                "stdin": stdin,
                "args": [],
                "source": "manual",
                "size_factor": None,
                "enabled": True,
                "stability": "stable",
                "masks": [],
                "baseline": {"state": "recorded", "version_id": 1, "duration_ms": 90 + i * 7},
                "created_at": NOW,
            }
        )
    return {"scenarios": scenarios}


def build_runs(scenarios: dict) -> tuple[dict, dict, dict, list[dict]]:
    items_ok = [
        {
            "scenario_id": s["id"],
            "name": s["name"],
            "verdict": "PASS",
            "stdout": "",
            "stderr": "",
            "exit_code": 1 if s["name"] == "uncaught-exception" else 0,
            "duration_ms": 80 + s["id"] * 9,
            "baseline_ms": 90 + s["id"] * 7,
            "diff": None,
            "hits": None,
        }
        for s in scenarios["scenarios"]
    ]
    total = len(items_ok)
    run7 = {
        "id": 7,
        "kind": "test",
        "status": "done",
        "version_id": 3,
        "started_at": NOW,
        "finished_at": "2026-10-24T08:00:04Z",
        "summary": {"passed": total, "total": total, "coverage": 0.87, "version_status": "passed", "message": None, "data": None},
        "items": items_ok,
    }
    failing = {
        "scenario_id": 3,
        "name": scenarios["scenarios"][2]["name"],
        "verdict": "FAIL",
        "stdout": "",
        "stderr": "",
        "exit_code": 0,
        "duration_ms": 120,
        "baseline_ms": 105,
        "diff": {
            "stdout": {
                "expected": "Сумма (без отменённых): 3743.50\n",
                "actual": "Сумма (без отменённых): 3743.00\n",
                "unified": "@@ -1 +1 @@\n-Сумма (без отменённых): 3743.50\n+Сумма (без отменённых): 3743.00\n",
            },
            "stderr": None,
            "exit_code": None,
            "exception": None,
        },
        "hits": None,
    }
    items_fail = [failing if i["scenario_id"] == 3 else i for i in items_ok]
    run8 = {
        "id": 8,
        "kind": "test",
        "status": "done",
        "version_id": 4,
        "started_at": "2026-10-24T08:05:00Z",
        "finished_at": "2026-10-24T08:05:05Z",
        "summary": {"passed": total - 1, "total": total, "coverage": 0.87, "version_status": "failed", "message": "Поведение отличается в 1 сценарии", "data": None},
        "items": items_fail,
    }
    events: list[dict] = [
        {"event": "status", "data": {"status": "queued"}},
        {"event": "status", "data": {"status": "running"}},
        {"event": "gate", "data": {"stage": "syntax", "status": "passed", "details": None}},
        {"event": "log", "data": {"stream": "build", "text": "javac --release 21 -encoding UTF-8 -g Main.java"}},
        {"event": "gate", "data": {"stage": "compile", "status": "passed", "details": None}},
        {"event": "gate", "data": {"stage": "scenarios", "status": "running", "details": None}},
    ]
    for done, item in enumerate(items_fail, 1):
        events.append(
            {
                "event": "item",
                "data": {
                    "scenario_id": item["scenario_id"],
                    "name": item["name"],
                    "verdict": item["verdict"],
                    "duration_ms": item["duration_ms"],
                    "baseline_ms": item["baseline_ms"],
                    "diff": item["diff"],
                },
            }
        )
        events.append({"event": "progress", "data": {"done": done, "total": total}})
    events += [
        {"event": "gate", "data": {"stage": "scenarios", "status": "failed", "details": "1 из %d сценариев отличается" % total}},
        {"event": "result", "data": {"summary": run8["summary"]}},
        {"event": "status", "data": {"status": "done"}},
        {"event": "done", "data": {}},
    ]
    runs = {"runs": [{**run8, "items": []}, {**run7, "items": []}]}
    return run7, run8, runs, events


def build_static() -> dict[str, Any]:
    ok_pre = lambda n: {"name": n, "passed": True, "reason": None}  # noqa: E731
    return {
        "health": {"status": "ok", "version": "0.1.0-mock", "javac": "21.0.4", "java": "21.0.4", "db": "ok", "problems": []},
        "samples": {
            "samples": [
                {"id": "demo-orders", "name": "demo-orders", "description": "Учёт заказов магазина: монолит ≈1000 строк", "lines": 995},
                {"id": "analyzer-pairs", "name": "analyzer-pairs", "description": "Вложенные циклы и merge (слайд 5)", "lines": 80},
            ]
        },
        "projects": {
            "projects": [
                {"id": 1, "name": "demo-orders", "created_at": NOW, "updated_at": NOW, "head_version_id": 3, "main_class": "Main", "files_count": 1},
            ]
        },
        "project": {
            "id": 1,
            "name": "demo-orders",
            "created_at": NOW,
            "updated_at": NOW,
            "main_class": "Main",
            "main_class_candidates": ["Main"],
            "head_version_id": 3,
            "original_version_id": 1,
            "baseline_version_id": 1,
            "settings": {
                "run_timeout_s": 10, "compile_timeout_s": 30, "output_limit_bytes": 1048576, "jvm_xmx": "256m",
                "locale": "en_US", "timezone": "UTC", "rule_thresholds": {"MAINT-01": 30.0}, "gate_min_scenarios": 3, "gate_min_coverage": 0.6,
            },
            "gate": {"status": "passed", "passed": 11, "total": 11, "coverage": 0.87, "run_id": 7},
        },
        "versions": {
            "head_version_id": 3,
            "versions": [
                {"id": 3, "number": 3, "parent_id": 2, "origin": "optimize", "description": "Оптимизация O(n·m) → O(n)", "created_at": "2026-10-24T08:20:00Z", "gate_status": "passed", "status": "active", "content_hash": "mock-demo-orders-v3"},
                {"id": 2, "number": 2, "parent_id": 1, "origin": "graph_op", "description": "Добавить проверку на null", "created_at": "2026-10-24T08:10:00Z", "gate_status": "passed", "status": "active", "content_hash": "mock-demo-orders-v2"},
                {"id": 1, "number": 1, "parent_id": None, "origin": "upload", "description": "Оригинал", "created_at": NOW, "gate_status": "passed", "status": "active", "content_hash": "mock-demo-orders-v1"},
            ],
        },
        "files": {"version_id": 3, "files": [{"path": FILE, "lines": 995, "size": 0}]},
        "build": {"ok": True, "diagnostics": [], "duration_ms": 1240, "cached": False},
        "palette": {
            "blocks": [
                {"kind": "var_decl", "title": "Переменная", "group": "basic", "color": "blue", "icon": "variable",
                 "fields": [{"name": "type", "label": "Тип", "type": "type", "required": True, "default": "int", "options": []},
                            {"name": "name", "label": "Имя", "type": "identifier", "required": True, "default": None, "options": []},
                            {"name": "value", "label": "Значение", "type": "expression", "required": False, "default": None, "options": []}],
                 "template": "int count = 0;"},
                {"kind": "condition", "title": "Условие", "group": "control", "color": "orange", "icon": "git-branch",
                 "fields": [{"name": "expr", "label": "Выражение", "type": "expression", "required": True, "default": None, "options": []},
                            {"name": "add_else", "label": "Добавить else", "type": "boolean", "required": False, "default": False, "options": []}],
                 "template": "if (cond) { }"},
                {"kind": "loop_foreach", "title": "Цикл (for each)", "group": "control", "color": "teal", "icon": "repeat",
                 "fields": [{"name": "type", "label": "Тип", "type": "type", "required": True, "default": None, "options": []},
                            {"name": "name", "label": "Имя", "type": "identifier", "required": True, "default": None, "options": []},
                            {"name": "iterable", "label": "Коллекция", "type": "expression", "required": True, "default": None, "options": []}],
                 "template": "for (Order o : orders) { }"},
                {"kind": "operation", "title": "Операция", "group": "basic", "color": "teal", "icon": "terminal",
                 "fields": [{"name": "expr", "label": "Выражение", "type": "expression", "required": True, "default": None, "options": []}],
                 "template": "System.out.println(\"…\");"},
                {"kind": "return", "title": "Возврат", "group": "basic", "color": "green", "icon": "corner-down-left",
                 "fields": [{"name": "expr", "label": "Значение", "type": "expression", "required": False, "default": None, "options": []}],
                 "template": "return result;"},
            ]
        },
    }


VALIDATORS: dict[str, Any] = {
    "health": HealthResponse,
    "samples": SampleList,
    "projects": ProjectList,
    "project": ProjectDetail,
    "versions": VersionList,
    "files": FileList,
    "fixes": FixList,
    "decompose-plan": DecomposePlan,
    "extract-preview": ExtractMethodPreview,
    "build": BuildResult,
    "palette": Palette,
    "tree": ProjectTree,
    "structure": StructureGraph,
    "findings": AnalysisResponse,
    "metrics": MetricsResponse,
    "scenarios": ScenarioList,
    "runs": RunList,
    "run-7": RunInfo,
    "run-8": RunInfo,
    "file-main": FileContent,
}


STALE: list[str] = []
CHECK = False


def write(name: str, data: Any) -> None:
    if name == "flows":
        TypeAdapter(dict[str, FlowGraph]).validate_python(data)
    elif name == "run-8-events":
        TypeAdapter(list[RunEvent]).validate_python(data)
    elif VALIDATORS.get(name) is not None:
        VALIDATORS[name].model_validate(data)
    # блок-схемы всех методов — одной строкой: файл большой и руками его не читают
    if name == "flows":
        text = json.dumps(data, ensure_ascii=False, separators=(",", ":")) + "\n"
    else:
        text = json.dumps(data, ensure_ascii=False, indent=2) + "\n"
    target = OUT / f"{name}.json"
    if CHECK:
        if not target.exists() or target.read_bytes().decode("utf-8") != text:
            STALE.append(target.name)
        return
    OUT.mkdir(parents=True, exist_ok=True)
    target.write_text(text, encoding="utf-8", newline="\n")


def main() -> int:
    global CHECK
    if hasattr(sys.stdout, "reconfigure"):
        sys.stdout.reconfigure(encoding="utf-8", errors="replace")
    parser = argparse.ArgumentParser(description="Генератор JSON-моков frontend")
    parser.add_argument("--check", action="store_true", help="не писать, а сверить с файлами")
    CHECK = parser.parse_args().check
    src = Source()
    members = collect_members(src)
    structure = build_structure(src)
    tree = build_tree(src)
    flows_by_id = build_flows(src)
    flows = {
        name: flows_by_id[member_id(next(m for m in members if m["type"] == "Main" and m["name"] == name))]
        for name in ("mergeDuplicates", "distinctCustomers", "buildCsv", "findOrderById")
    }

    for name, data in build_static().items():
        write(name, data)
    findings = build_findings(src, members, flows)
    apply_findings(structure, tree, flows_by_id, findings)
    write("tree", tree)
    write("structure", structure)
    write("flows", flows_by_id)
    write("findings", findings)
    titles = {
        "OPT-01": "Индексация по ключу вместо вложенного поиска",
        "OPT-02": "contains в цикле → HashSet",
        "OPT-03": "Конкатенация → StringBuilder",
        "FIX-01": "== → equals",
        "FIX-02": "Заглушка вместо пустого catch",
        "REF-01": "Выделить метод",
        "REF-04": "Декомпозиция монолита",
    }
    write(
        "fixes",
        {
            "version_id": 3,
            "fixes": [
                {
                    "finding_id": f["id"],
                    "fix_id": f["fix"]["fix_id"],
                    "title": titles[f["fix"]["fix_id"]],
                    "behavior_preserving": f["fix"]["behavior_preserving"],
                    "available": f["fix"]["available"],
                }
                for f in findings["findings"]
                if f["fix"]
            ],
        },
    )
    write("metrics", build_metrics(src, members))
    # выделение метода: два подряд идущих оператора тела цикла `run`; декомпозиция — план настоящего движка
    target = ExtractMethodTarget(
        node_ids=[f"{FILE}#Main.run(Scanner)/while[0]/assign[0]", f"{FILE}#Main.run(Scanner)/while[0]/op[0]"]
    )
    write("extract-preview", extract_service.preview(src.project, target).model_dump(mode="json", by_alias=True))
    write(
        "decompose-plan",
        decompose_service.to_schema(build_plan(src.project)).model_dump(mode="json", by_alias=True),
    )
    scenarios = build_scenarios()
    write("scenarios", scenarios)
    run7, run8, runs, events = build_runs(scenarios)
    write("runs", runs)
    write("run-7", run7)
    write("run-8", run8)
    write("run-8-events", events)
    write(
        "file-main",
        {"path": FILE, "version_id": 3, "content": src.text, "syntax_errors": []},
    )
    if CHECK:
        if STALE:
            print("устарели: " + ", ".join(sorted(STALE)))
            print("обновить: cd backend && uv run python ../scripts/gen_mock_fixtures.py")
            return 1
        print("фикстуры актуальны")
        return 0
    print(f"fixtures written to {OUT.relative_to(REPO)}: {len(list(OUT.glob('*.json')))} files")
    return 0


if __name__ == "__main__":
    sys.exit(main())
