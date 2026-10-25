#!/usr/bin/env python3
"""Дифференциальная проверка исправлений (инвариант I8): исправление не меняет поведение программы.

Для каждого примера с сценариями: берётся каждая доступная правка (находка → предпросмотр) **по отдельности**,
правка применяется к исходной версии, результат собирается ``javac`` и прогоняется на всех сценариях; вывод и код
выхода сравниваются с оригиналом. Для исправлений с пометкой «поведение сохраняется» любое расхождение — дефект;
для остальных (`FIX-*`, `OPT-01`) расхождения только считаются. Результат — ``reports/fix_differential.md``,
код выхода 1, если найден дефект.

    python scripts/fix_differential.py                       # demo-orders и монолиты
    python scripts/fix_differential.py --only inventory
    python scripts/fix_differential.py --java-home /path/to/jdk
"""

from __future__ import annotations

import argparse
import json
import sys
import tempfile
import time
from concurrent.futures import ThreadPoolExecutor
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
BACKEND = ROOT / "backend"
SAMPLES = ROOT / "samples"


def corpus() -> list[Path]:
    wanted = [SAMPLES / "demo-orders", SAMPLES / "analyzer-pairs", *sorted((SAMPLES / "monoliths").iterdir())]
    return [p for p in wanted if p.is_dir() and (p / "scenarios").is_dir()]


def main() -> int:
    for stream in (sys.stdout, sys.stderr):
        stream.reconfigure(encoding="utf-8", errors="replace")
    parser = argparse.ArgumentParser(description=__doc__, formatter_class=argparse.RawDescriptionHelpFormatter)
    parser.add_argument("--only", help="подстрока пути примера")
    parser.add_argument("--java-home", help="каталог JDK")
    parser.add_argument("--out", default=str(ROOT / "reports"))
    parser.add_argument("--jobs", type=int, default=4, help="сколько сценариев гнать параллельно")
    args = parser.parse_args()

    sys.path.insert(0, str(BACKEND))
    from engine.analysis.analyzer import analyze
    from engine.java.project import JavaProject
    from engine.runtime.compiler import compile_workspace
    from engine.runtime.runner import RunLimits, run_java
    from engine.runtime.workspace import materialize
    from engine.transform.base import apply_proposal
    from engine.transform.fixes import propose

    java_home = Path(args.java_home) if args.java_home else None
    builds = Path(tempfile.mkdtemp(prefix="jb-diff-"))
    limits = RunLimits(timeout_s=15)

    def run_all(files: dict[str, str], scenarios: list[dict], key: str) -> tuple[bool, list]:
        workspace = materialize(files, builds, key)
        built = compile_workspace(workspace, java_home)
        if not built.ok:
            return False, [built.log[:300]]

        def one(scenario: dict):
            ran = run_java(
                workspace, "Main", stdin=scenario["stdin"], args=scenario.get("args", []), java_home=java_home, limits=limits
            )
            return ran.status, ran.stdout, ran.stderr.splitlines()[0] if ran.stderr else "", ran.exit_code

        with ThreadPoolExecutor(args.jobs) as pool:
            return True, list(pool.map(one, scenarios))

    started = time.monotonic()
    rows: list[str] = []
    defects: list[str] = []
    total = same = changed = 0
    for sample in corpus():
        name = sample.relative_to(SAMPLES).as_posix()
        if args.only and args.only not in name:
            continue
        files = {
            p.relative_to(sample).as_posix(): p.read_text(encoding="utf-8")
            for p in sorted(sample.rglob("*.java"))
            if "scenarios" not in p.relative_to(sample).parts
        }
        scenarios = [json.loads(p.read_text(encoding="utf-8")) for p in sorted((sample / "scenarios").glob("*.json"))]
        project = JavaProject(files)
        ok, baseline = run_all(files, scenarios, "base-" + name.replace("/", "-"))
        if not ok:
            defects.append(f"{name}: оригинал не собирается: {baseline[0]}")
            continue
        seen: set[tuple[str, str]] = set()
        counts = {"preserving": 0, "changing": 0}
        for finding in analyze(project).findings:
            proposal = propose(project, finding)
            if proposal is None or not proposal.available:
                continue
            try:
                after = apply_proposal(project, proposal)
            except Exception as error:  # noqa: BLE001
                defects.append(f"{name}: {proposal.fix_id} в {finding.location.method_id}: применение упало: {error}")
                continue
            digest = (proposal.fix_id, json.dumps(after, sort_keys=True))
            if digest in seen:
                continue
            seen.add(digest)
            total += 1
            key = f"{name.replace('/', '-')}-{len(seen)}"
            built, outputs = run_all(after, scenarios, key)
            where = f"{(finding.location.method_id or finding.location.node_id or finding.rule_id).split('#')[-1]} (строка {finding.location.start_line})"
            if not built:
                defects.append(f"{name}: {proposal.fix_id} {where}: результат не собирается: {outputs[0]}")
                continue
            diffs = [s["name"] for s, a, b in zip(scenarios, baseline, outputs, strict=True) if a != b]
            if not diffs:
                same += 1
                counts["preserving" if proposal.behavior_preserving else "changing"] += 1
                continue
            changed += 1
            if proposal.behavior_preserving:
                defects.append(f"{name}: {proposal.fix_id} {where} меняет вывод в сценариях: {', '.join(diffs[:4])}")
            else:
                counts["changing"] += 1
        rows.append(f"| {name} | {len(seen)} | {counts['preserving']} | {counts['changing']} | {len(scenarios)} |")
        print(f"{name}: исправлений {len(seen)}", flush=True)

    out = Path(args.out)
    out.mkdir(exist_ok=True)
    report = [
        "# Дифференциальная проверка исправлений (I8)",
        "",
        "Каждая доступная правка применяется отдельно к исходной версии, результат собирается `javac` и гоняется на всех "
        "сценариях примера; вывод и код выхода сравниваются с оригиналом.",
        "",
        "| Пример | Правок | Вывод совпал | Вывод отличается (ожидаемо: `FIX-*`, `OPT-01`) | Сценариев |",
        "|---|---|---|---|---|",
        *rows,
        "",
        f"Правок всего {total}: вывод совпал у {same}, отличается у {changed}; время {time.monotonic() - started:.0f} с.",
        "",
        "## Итог",
        "",
        "Расхождений у исправлений с пометкой «поведение сохраняется» нет." if not defects else "Найдены дефекты:",
        *[f"- {d}" for d in defects],
    ]
    (out / "fix_differential.md").write_text("\n".join(report) + "\n", encoding="utf-8", newline="\n")
    print("\n".join(report[-6:]))
    return 1 if defects else 0


if __name__ == "__main__":
    sys.exit(main())
