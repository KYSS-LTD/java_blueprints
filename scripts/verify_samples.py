#!/usr/bin/env python3
"""Прогон корпуса ``samples/`` через API инструмента и сводный отчёт (S6.4, docs/07 § 5).

Для каждого примера (``GET /api/samples``): создаётся проект, записывается эталон, запускается gate,
считается анализ (находки, сложность). Отчёт — ``reports/verify_samples.md`` и ``.json``; код выхода
1, если gate любого примера не зелёный. Используется в CI и перед сдачей.

    python scripts/verify_samples.py                  # все примеры
    python scripts/verify_samples.py --only demo-orders --java-home /path/to/jdk
    python scripts/verify_samples.py --out reports    # каталог отчётов
"""

from __future__ import annotations

import argparse
import json
import os
import shutil
import sys
import tempfile
import time
from pathlib import Path
from typing import Any

ROOT = Path(__file__).resolve().parents[1]
BACKEND = ROOT / "backend"


def run_and_wait(client: Any, project_id: int, kind: str, timeout_s: float = 600.0, **body: Any) -> dict[str, Any]:
    response = client.post(f"/api/projects/{project_id}/runs", json={"kind": kind, **body})
    response.raise_for_status()
    run_id = response.json()["run_id"]
    deadline = time.monotonic() + timeout_s
    while True:
        info = client.get(f"/api/runs/{run_id}").json()
        if info["status"] not in {"queued", "running"}:
            return info
        if time.monotonic() > deadline:
            raise TimeoutError(f"запуск {run_id} ({kind}) не завершился за {timeout_s} с")
        time.sleep(0.2)


def check_sample(client: Any, sample: dict[str, Any]) -> dict[str, Any]:
    started = time.monotonic()
    created = client.post("/api/projects", json={"sample_id": sample["id"]})
    created.raise_for_status()
    project = created.json()
    pid = project["id"]
    row: dict[str, Any] = {
        "sample": sample["id"],
        "lines": sample["lines"],
        "files": len(project["files"]),
        "syntax_errors": len(project["syntax_errors"]),
        "main_class": project["main_class"],
    }
    scenarios = client.get(f"/api/projects/{pid}/scenarios").json()["scenarios"]
    row["scenarios"] = len(scenarios)
    baseline = run_and_wait(client, pid, "baseline")
    row["baseline"] = baseline["summary"].get("message")
    row["warnings"] = (baseline["summary"].get("data") or {}).get("warnings", [])
    gate = run_and_wait(client, pid, "gate")
    summary = gate["summary"]
    row.update(
        gate=summary.get("version_status"),
        gate_message=summary.get("message"),
        passed=summary.get("passed"),
        total=summary.get("total"),
        coverage=summary.get("coverage"),
    )
    analysis = client.get(f"/api/projects/{pid}/analysis").json()
    row.update(
        complexity=analysis["complexity"]["current"],
        optimized=analysis["complexity"]["optimized"],
        findings=analysis["summary"]["total"],
        by_severity=analysis["summary"]["by_severity"],
        fixable=analysis["summary"]["fixable"],
    )
    row["seconds"] = round(time.monotonic() - started, 1)
    return row


def markdown(rows: list[dict[str, Any]]) -> str:
    lines = [
        "# Прогон корпуса через API",
        "",
        "| Пример | Строк | Сценариев | Gate | Покрытие | Сложность | Находок (исправимых) | Время |",
        "|---|---|---|---|---|---|---|---|",
    ]
    for r in rows:
        mark = {"passed": "✓", "failed": "✗", "unknown": "?"}.get(r.get("gate"), "?")
        coverage = f"{r['coverage'] * 100:.0f}%" if r.get("coverage") is not None else "—"
        complexity = r["complexity"] + (f" → {r['optimized']}" if r.get("optimized") else "")
        lines.append(
            f"| {r['sample']} | {r['lines']} | {r['scenarios']} | {mark} {r.get('passed')}/{r.get('total')} "
            f"| {coverage} | {complexity} | {r['findings']} ({r['fixable']}) | {r['seconds']} с |"
        )
    problems = [r for r in rows if r.get("gate") != "passed" or r["syntax_errors"]]
    lines += ["", "## Итог", ""]
    lines.append("Все примеры зелёные." if not problems else f"Проблемных примеров: {len(problems)}.")
    for r in problems:
        lines.append(f"- {r['sample']}: gate {r.get('gate')} — {r.get('gate_message')}")
    warnings = [(r["sample"], w) for r in rows for w in r.get("warnings", [])]
    if warnings:
        lines += ["", "## Предупреждения", ""] + [f"- {name}: {text}" for name, text in warnings]
    return "\n".join(lines) + "\n"


def main() -> int:
    parser = argparse.ArgumentParser(description=__doc__, formatter_class=argparse.RawDescriptionHelpFormatter)
    parser.add_argument("--only", action="append", default=[], help="только эти примеры (можно несколько раз)")
    parser.add_argument("--java-home", help="каталог JDK (иначе javac/java из PATH)")
    parser.add_argument("--out", default=str(ROOT / "reports"), help="куда писать отчёты")
    args = parser.parse_args()

    if args.java_home:
        os.environ["JB_JAVA_HOME"] = args.java_home
    os.environ.setdefault("JB_LOG_LEVEL", "ERROR")
    data_dir = tempfile.mkdtemp(prefix="jb-verify-samples-")
    os.environ["JB_DATA_DIR"] = data_dir
    sys.path.insert(0, str(BACKEND))
    from fastapi.testclient import TestClient

    from app.db.session import get_engine
    from app.main import create_app

    rows: list[dict[str, Any]] = []
    try:
        with TestClient(create_app()) as client:
            samples = client.get("/api/samples").json()["samples"]
            if args.only:
                samples = [s for s in samples if s["id"] in args.only]
            if not samples:
                print("нет примеров для проверки", file=sys.stderr)
                return 2
            for sample in samples:
                row = check_sample(client, sample)
                rows.append(row)
                print(
                    f"{row['sample']:<22} gate={row.get('gate')} {row.get('passed')}/{row.get('total')} "
                    f"покрытие={row.get('coverage')} находок={row['findings']} {row['seconds']} с"
                )
    finally:
        get_engine().dispose()  # на Windows файл БД держит открытым пул соединений
        shutil.rmtree(data_dir, ignore_errors=True)

    out = Path(args.out)
    out.mkdir(parents=True, exist_ok=True)
    (out / "verify_samples.md").write_text(markdown(rows), encoding="utf-8", newline="\n")
    (out / "verify_samples.json").write_text(
        json.dumps(rows, ensure_ascii=False, indent=2) + "\n", encoding="utf-8", newline="\n"
    )
    failed = [r for r in rows if r.get("gate") != "passed" or r["syntax_errors"]]
    print(f"отчёт: {out / 'verify_samples.md'}")
    return 1 if failed else 0


if __name__ == "__main__":
    sys.exit(main())
