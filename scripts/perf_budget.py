#!/usr/bin/env python3
"""Бюджеты производительности на ``samples/demo-orders`` (S10.4, docs/10 § 3.6).

Замеряется через API (как работает интерфейс): импорт, разбор и граф, операция над графом, анализ,
холодная компиляция, gate на 15 сценариях. Результат — ``reports/perf_budget.md``; код выхода 1, если
какой-либо бюджет превышен.

    python scripts/perf_budget.py [--java-home /path/to/jdk] [--out reports]
"""

from __future__ import annotations

import argparse
import json
import os
import shutil
import statistics
import sys
import tempfile
import time
from pathlib import Path
from typing import Any

ROOT = Path(__file__).resolve().parents[1]
BACKEND = ROOT / "backend"
DEMO = ROOT / "samples" / "demo-orders"

# (название, бюджет, секунд)
BUDGETS = {
    "import": ("Импорт проекта (1000 строк)", 2.0),
    "parse": ("Разбор + граф всех методов, 1000 строк", 0.3),
    "operation": ("Операция над графом (вставка блока)", 0.5),
    "analysis": ("Анализ (холодный)", 1.0),
    "build": ("Компиляция demo-orders (холодная)", 3.0),
    "gate": ("Gate на 15 сценариях", 20.0),
}


def timed(action: Any, repeat: int = 1) -> float:
    samples = []
    for _ in range(repeat):
        started = time.perf_counter()
        action()
        samples.append(time.perf_counter() - started)
    return statistics.median(samples)


def wait_run(client: Any, run_id: int, timeout_s: float = 300.0) -> dict[str, Any]:
    deadline = time.monotonic() + timeout_s
    while True:
        info = client.get(f"/api/runs/{run_id}").json()
        if info["status"] not in {"queued", "running"}:
            return info
        if time.monotonic() > deadline:
            raise TimeoutError(f"запуск {run_id} не завершился")
        time.sleep(0.1)


def measure(client: Any, java_home: str | None) -> dict[str, float]:
    sys.path.insert(0, str(BACKEND))
    from engine.java.project import JavaProject

    files = [
        {"path": p.relative_to(DEMO).as_posix(), "content": p.read_text(encoding="utf-8")}
        for p in sorted(DEMO.rglob("*.java"))
    ]
    scenarios = [json.loads(p.read_text(encoding="utf-8")) for p in sorted((DEMO / "scenarios").glob("*.json"))]
    result: dict[str, float] = {}

    # чистый движок: разбор и блок-схемы всех методов (без HTTP и без кеша)
    def parse_all() -> None:
        project = JavaProject({f["path"]: f["content"] for f in files})
        for graph in project.flows():
            assert graph.nodes
        list(project.types())

    result["parse"] = timed(parse_all, repeat=5)

    holder: dict[str, Any] = {}

    def create() -> None:
        response = client.post("/api/projects", json={"name": "perf", "files": files})
        response.raise_for_status()
        holder["project"] = response.json()

    result["import"] = timed(create)
    project = holder["project"]
    pid = project["id"]
    client.post(f"/api/projects/{pid}/scenarios/import", json={"scenarios": scenarios}).raise_for_status()

    # операция над графом: вставка блока в начало метода run(Scanner)
    method = next(
        m
        for root in client.get(f"/api/projects/{pid}/tree").json()["roots"]
        for m in _methods(root)
        if m["name"] == "printReport()"
    )
    head = project["head_version_id"]

    def operate() -> None:
        nonlocal head
        response = client.post(
            f"/api/projects/{pid}/ops",
            json={
                "base_version_id": head,
                "ops": [
                    {
                        "op": "insert_node",
                        "parent_id": method["method_id"],
                        "slot": "body",
                        "index": 0,
                        "kind": "operation",
                        "fields": {"expr": "System.out.print(\"\")"},
                    }
                ],
            },
        )
        response.raise_for_status()
        head = response.json()["version"]["id"]

    result["operation"] = timed(operate, repeat=3)

    result["analysis"] = timed(lambda: client.get(f"/api/projects/{pid}/analysis").raise_for_status())

    # свежий проект без кешей сборки: холодная компиляция
    cold = client.post("/api/projects", json={"name": "perf-cold", "files": files}).json()
    result["build"] = timed(lambda: client.post(f"/api/projects/{cold['id']}/build", json={}).raise_for_status())

    # gate на 15 сценариях: 11 из корпуса и 4 дополнительных
    gate_project = client.post("/api/projects", json={"name": "perf-gate", "files": files}).json()
    gid = gate_project["id"]
    extra = [
        {"name": f"extra-{i}", "stdin": "list\nstats\nreport\nquit\n" * (i + 1), "args": []} for i in range(4)
    ]
    client.post(f"/api/projects/{gid}/scenarios/import", json={"scenarios": [*scenarios, *extra]}).raise_for_status()
    base = client.post(f"/api/projects/{gid}/runs", json={"kind": "baseline"}).json()["run_id"]
    wait_run(client, base)

    def gate() -> None:
        run_id = client.post(f"/api/projects/{gid}/runs", json={"kind": "gate"}).json()["run_id"]
        info = wait_run(client, run_id)
        assert info["summary"]["version_status"] == "passed", info["summary"]

    result["gate"] = timed(gate)
    return result


def _methods(node: dict[str, Any]) -> list[dict[str, Any]]:
    found = [node] if node.get("method_id") else []
    for child in node.get("children") or []:
        found.extend(_methods(child))
    return found


def markdown(result: dict[str, float], machine: str) -> str:
    lines = [
        "# Бюджеты производительности",
        "",
        f"Машина: {machine}. Пример: `samples/demo-orders` (995 строк, 11 сценариев). Медиана по замерам.",
        "",
        "| Метрика | Бюджет | Замер | Итог |",
        "|---|---|---|---|",
    ]
    for key, (title, budget) in BUDGETS.items():
        value = result.get(key)
        if value is None:
            lines.append(f"| {title} | ≤ {budget} с | — | — |")
            continue
        mark = "✓" if value <= budget else "✗"
        lines.append(f"| {title} | ≤ {budget} с | {value:.3f} с | {mark} |")
    return "\n".join(lines) + "\n"


def main() -> int:
    for stream in (sys.stdout, sys.stderr):  # Windows: вывод в файл иначе идёт в кодовой странице консоли
        stream.reconfigure(encoding="utf-8", errors="replace")
    parser = argparse.ArgumentParser(description=__doc__, formatter_class=argparse.RawDescriptionHelpFormatter)
    parser.add_argument("--java-home", help="каталог JDK (иначе javac/java из PATH)")
    parser.add_argument("--out", default=str(ROOT / "reports"), help="куда писать отчёт")
    args = parser.parse_args()
    if args.java_home:
        os.environ["JB_JAVA_HOME"] = args.java_home
    os.environ.setdefault("JB_LOG_LEVEL", "ERROR")
    data_dir = tempfile.mkdtemp(prefix="jb-perf-")
    os.environ["JB_DATA_DIR"] = data_dir
    sys.path.insert(0, str(BACKEND))
    from fastapi.testclient import TestClient

    from app.db.session import get_engine
    from app.main import create_app

    try:
        with TestClient(create_app()) as client:
            result = measure(client, args.java_home)
    finally:
        get_engine().dispose()
        shutil.rmtree(data_dir, ignore_errors=True)

    import platform

    machine = f"{platform.system()} {platform.release()}, Python {platform.python_version()}, {os.cpu_count()} CPU"
    out = Path(args.out)
    out.mkdir(parents=True, exist_ok=True)
    (out / "perf_budget.md").write_text(markdown(result, machine), encoding="utf-8", newline="\n")
    for key, (title, budget) in BUDGETS.items():
        print(f"{title:<45} {result[key]:.3f} с (бюджет {budget})")
    over = [k for k, (_, budget) in BUDGETS.items() if result[k] > budget]
    return 1 if over else 0


if __name__ == "__main__":
    sys.exit(main())
