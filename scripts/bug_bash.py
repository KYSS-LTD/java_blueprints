#!/usr/bin/env python3
"""Баг-баш на корпусе (S10.2): весь корпус ``samples/`` проходит через API так, как это делает интерфейс.

Для каждого примера: импорт → дерево, структура, блок-схемы **всех** методов → анализ, метрики →
предпросмотр каждого доступного исправления → сборка и запуск → экспорт исходников и повторная сборка
``javac`` вне инструмента → (монолиты) план декомпозиции и пакетная оптимизация с gate. Любой ответ ``5xx``,
исключение, пустая блок-схема или код, не собирающийся после экспорта, попадает в отчёт
``reports/bug_bash.md``; код выхода 1, если найдены дефекты.

    python scripts/bug_bash.py                     # весь корпус
    python scripts/bug_bash.py --only demo-orders  # по подстроке пути
    python scripts/bug_bash.py --java-home /path/to/jdk --out reports
"""

from __future__ import annotations

import argparse
import io
import json
import os
import shutil
import subprocess
import sys
import tempfile
import time
import zipfile
from pathlib import Path
from typing import Any
from urllib.parse import quote

ROOT = Path(__file__).resolve().parents[1]
BACKEND = ROOT / "backend"
SAMPLES = ROOT / "samples"
SKIP_DIRS = {"optimizations", "defects"}


def corpus() -> list[Path]:
    """Каталоги примеров: каталог с ``*.java`` непосредственно внутри (без вложенных пакетов)."""
    found = []
    for path in sorted(SAMPLES.rglob("*")):
        if not path.is_dir() or path.name in {"scenarios"} or SKIP_DIRS & set(path.relative_to(SAMPLES).parts):
            continue
        if any(child.suffix == ".java" for child in path.iterdir()):
            found.append(path)
    return found


def read_project(directory: Path) -> tuple[list[dict[str, str]], list[dict[str, Any]]]:
    files = [
        {"path": p.relative_to(directory).as_posix(), "content": p.read_text(encoding="utf-8")}
        for p in sorted(directory.rglob("*.java"))
    ]
    scenarios = [
        json.loads(p.read_text(encoding="utf-8")) for p in sorted((directory / "scenarios").glob("*.json"))
    ]
    return files, scenarios


class Report:
    def __init__(self) -> None:
        self.defects: list[tuple[str, str]] = []
        self.rows: list[dict[str, Any]] = []

    def defect(self, sample: str, text: str) -> None:
        self.defects.append((sample, text))
        print(f"  ДЕФЕКТ: {text}")


def wait_run(client: Any, run_id: int, timeout_s: float = 300.0) -> dict[str, Any]:
    deadline = time.monotonic() + timeout_s
    while True:
        info = client.get(f"/api/runs/{run_id}").json()
        if info["status"] not in {"queued", "running"}:
            return info
        if time.monotonic() > deadline:
            raise TimeoutError(f"запуск {run_id} не завершился за {timeout_s} с")
        time.sleep(0.15)


def run(client: Any, pid: int, kind: str, **body: Any) -> dict[str, Any]:
    response = client.post(f"/api/projects/{pid}/runs", json={"kind": kind, **body})
    if response.status_code >= 500:
        raise RuntimeError(f"POST /runs {kind}: {response.status_code} {response.text[:200]}")
    return wait_run(client, response.json()["run_id"])


def check(client: Any, report: Report, name: str, method: str, url: str, **kwargs: Any) -> Any | None:
    """Запрос; ``5xx`` — дефект. Возвращает ответ при ``2xx``, иначе ``None``."""
    response = client.request(method, url, **kwargs)
    if response.status_code >= 500:
        report.defect(name, f"{method} {url.split('/api', 1)[-1]} → {response.status_code}: {response.text[:160]}")
        return None
    return response if response.status_code < 300 else None


def bash_project(client: Any, report: Report, directory: Path, java_home: Path | None) -> dict[str, Any]:
    name = directory.relative_to(SAMPLES).as_posix()
    started = time.monotonic()
    files, scenarios = read_project(directory)
    broken = name.startswith("broken")
    row: dict[str, Any] = {"sample": name, "files": len(files), "methods": 0, "flows": 0, "fixes": 0}
    created = client.post("/api/projects", json={"name": name, "files": files})
    if created.status_code >= 500:
        report.defect(name, f"создание проекта: {created.status_code} {created.text[:160]}")
        return row
    if created.status_code >= 300:
        row["note"] = f"импорт отклонён ({created.status_code})"
        return row
    project = created.json()
    pid = project["id"]
    if scenarios:
        check(client, report, name, "POST", f"/api/projects/{pid}/scenarios/import", json={"scenarios": scenarios})

    # дерево и блок-схемы всех методов
    tree = check(client, report, name, "GET", f"/api/projects/{pid}/tree")
    methods: list[str] = []

    def walk(nodes: list[dict[str, Any]]) -> None:
        for node in nodes:
            if node.get("method_id"):
                methods.append(node["method_id"])
            walk(node.get("children") or [])

    if tree is not None:
        walk(tree.json().get("roots") or [])
    row["methods"] = len(methods)
    for method_id in methods:
        flow = check(client, report, name, "GET", f"/api/projects/{pid}/flow", params={"method_id": method_id})
        if flow is None:
            row["flows_missing"] = row.get("flows_missing", 0) + 1  # метод без тела или сломанный файл
            continue
        if not flow.json().get("nodes"):
            report.defect(name, f"пустая блок-схема {method_id}")
        row["flows"] += 1
    for mode in ("types", "methods", "deps"):
        check(client, report, name, "GET", f"/api/projects/{pid}/structure", params={"mode": mode})

    # анализ и исправления
    analysis = check(client, report, name, "GET", f"/api/projects/{pid}/analysis")
    check(client, report, name, "GET", f"/api/projects/{pid}/metrics")
    check(client, report, name, "GET", f"/api/projects/{pid}/complexity")
    if analysis is not None:
        findings = analysis.json()["findings"]
        row["findings"] = len(findings)
        row["complexity"] = analysis.json()["complexity"]["current"]
        for finding in findings:
            if finding.get("fix") and finding["fix"]["available"]:
                row["fixes"] += 1
                url = f"/api/projects/{pid}/fixes/{quote(finding['id'], safe='/')}/preview"
                response = client.post(url, json={})
                if response.status_code >= 500:
                    report.defect(name, f"предпросмотр {finding['fix']['fix_id']} для {finding['id']}: {response.status_code}")
                elif response.status_code >= 300:
                    report.defect(
                        name,
                        f"исправление {finding['fix']['fix_id']} объявлено доступным, но предпросмотр дал {response.status_code}",
                    )
                elif not response.json()["diff"]:
                    report.defect(name, f"предпросмотр {finding['fix']['fix_id']} без diff: {finding['id']}")
    if broken:
        return row

    # сборка, запуск, экспорт и повторная сборка вне инструмента
    build = check(client, report, name, "POST", f"/api/projects/{pid}/build", json={})
    if build is not None:
        row["build"] = build.json()["ok"]
        if not build.json()["ok"] and not name.startswith("out-of-scope"):
            report.defect(name, "проект не собирается: " + str(build.json()["diagnostics"][:1]))
    if build is not None and build.json()["ok"]:
        info = run(client, pid, "run", stdin="")
        if info["status"] not in {"done", "failed"}:
            report.defect(name, f"запуск завершился статусом {info['status']}")
        exported = check(client, report, name, "GET", f"/api/projects/{pid}/export/source")
        if exported is not None and java_home is not None:
            row["export_javac"] = recompile(report, name, exported.content, java_home)

    # монолиты: декомпозиция (план и предпросмотр) и пакетная оптимизация с gate
    if "monoliths" in name or name == "demo-orders":
        plan = check(client, report, name, "POST", f"/api/projects/{pid}/decompose/plan", json={})
        if plan is not None:
            row["decompose_classes"] = len(plan.json()["classes"])
            preview = check(
                client,
                report,
                name,
                "POST",
                f"/api/projects/{pid}/decompose/preview",
                json={"base_version_id": project["head_version_id"], "plan": plan.json()},
            )
            if preview is not None and any("не компилируется" in w for w in preview.json()["warnings"]):
                report.defect(name, "результат декомпозиции не компилируется")
    optimize = check(client, report, name, "POST", f"/api/projects/{pid}/optimize/plan", json={})
    if optimize is not None and optimize.json()["steps"] and scenarios:
        run(client, pid, "baseline")
        head = client.get(f"/api/projects/{pid}").json()["head_version_id"]
        steps = [s["step_id"] for s in optimize.json()["steps"]]
        applied = check(
            client, report, name, "POST", f"/api/projects/{pid}/optimize/apply", json={"base_version_id": head, "step_ids": steps}
        )
        if applied is not None:
            info = wait_run(client, applied.json()["run_id"])
            row["optimize"] = info["summary"].get("message")
            if info["status"] != "done":
                report.defect(name, f"пакетная оптимизация: статус {info['status']}")
            gate = run(client, pid, "gate")
            row["gate_after_optimize"] = gate["summary"].get("version_status")
            if gate["summary"].get("version_status") == "failed":
                report.defect(name, "после пакетной оптимизации gate красный: " + str(gate["summary"].get("message")))
    row["seconds"] = round(time.monotonic() - started, 1)
    return row


def recompile(report: Report, name: str, archive: bytes, java_home: Path) -> bool:
    """Распаковать экспорт и собрать ``javac`` из каталога вне инструмента."""
    folder = Path(tempfile.mkdtemp(prefix="jb-bash-export-"))
    try:
        with zipfile.ZipFile(io.BytesIO(archive)) as bundle:
            bundle.extractall(folder)
        sources = [str(p) for p in folder.rglob("*.java")]
        if not sources:
            report.defect(name, "в экспортированном архиве нет .java")
            return False
        javac = java_home / "bin" / ("javac.exe" if os.name == "nt" else "javac")
        result = subprocess.run(
            [str(javac), "-encoding", "UTF-8", "-d", str(folder / "out"), *sources],
            capture_output=True,
            text=True,
            encoding="utf-8",
            errors="replace",
        )
        if result.returncode != 0:
            report.defect(name, "экспорт не собирается javac: " + result.stderr.strip().splitlines()[0][:160])
            return False
        return True
    finally:
        shutil.rmtree(folder, ignore_errors=True)


def markdown(report: Report) -> str:
    lines = [
        "# Баг-баш на корпусе",
        "",
        "| Пример | Методов | Блок-схем | Находок | Исправлений | Сборка | Экспорт javac | Оптимизация | Время |",
        "|---|---|---|---|---|---|---|---|---|",
    ]
    for r in report.rows:
        mark = lambda v: {True: "✓", False: "✗", None: "—"}[v]  # noqa: E731
        lines.append(
            f"| {r['sample']} | {r['methods']} | {r['flows']} | {r.get('findings', '—')} | {r['fixes']} "
            f"| {mark(r.get('build'))} | {mark(r.get('export_javac'))} "
            f"| {r.get('gate_after_optimize', '—')} | {r.get('seconds', '—')} с |"
        )
    lines += ["", "## Итог", ""]
    if not report.defects:
        lines.append(f"Дефектов не найдено: {len(report.rows)} примеров прошли весь путь.")
    else:
        lines.append(f"Найдено дефектов: {len(report.defects)}.")
        lines += [f"- `{sample}`: {text}" for sample, text in report.defects]
    return "\n".join(lines) + "\n"


def main() -> int:
    parser = argparse.ArgumentParser(description=__doc__, formatter_class=argparse.RawDescriptionHelpFormatter)
    parser.add_argument("--only", action="append", default=[], help="подстрока пути примера")
    parser.add_argument("--java-home", help="каталог JDK (иначе javac/java из PATH)")
    parser.add_argument("--out", default=str(ROOT / "reports"), help="куда писать отчёт")
    args = parser.parse_args()

    java_home = Path(args.java_home) if args.java_home else None
    if java_home is None and shutil.which("javac"):
        java_home = Path(shutil.which("javac") or "").resolve().parents[1]
    if args.java_home:
        os.environ["JB_JAVA_HOME"] = args.java_home
    os.environ.setdefault("JB_LOG_LEVEL", "ERROR")
    data_dir = tempfile.mkdtemp(prefix="jb-bug-bash-")
    os.environ["JB_DATA_DIR"] = data_dir
    sys.path.insert(0, str(BACKEND))
    from fastapi.testclient import TestClient

    from app.db.session import get_engine
    from app.main import create_app

    report = Report()
    directories = [d for d in corpus() if not args.only or any(o in d.relative_to(SAMPLES).as_posix() for o in args.only)]
    try:
        with TestClient(create_app(), raise_server_exceptions=False) as client:
            for directory in directories:
                label = directory.relative_to(SAMPLES).as_posix()
                print(label)
                try:
                    report.rows.append(bash_project(client, report, directory, java_home))
                except Exception as error:  # noqa: BLE001 - любой сбой прогона — дефект
                    report.defect(label, f"исключение прогона: {type(error).__name__}: {error}")
    finally:
        get_engine().dispose()
        shutil.rmtree(data_dir, ignore_errors=True)

    out = Path(args.out)
    out.mkdir(parents=True, exist_ok=True)
    (out / "bug_bash.md").write_text(markdown(report), encoding="utf-8", newline="\n")
    print(f"отчёт: {out / 'bug_bash.md'}; дефектов: {len(report.defects)}")
    return 1 if report.defects else 0


if __name__ == "__main__":
    sys.exit(main())
