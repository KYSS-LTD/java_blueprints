#!/usr/bin/env python3
"""Дифференциальная проверка «Выделить в метод» (REF-01, инвариант I8): выделение не меняет поведение.

Для каждого примера со сценариями выбирается набор фрагментов (один оператор или 2–3 подряд идущих оператора одного
слота, случайно, но воспроизводимо), для каждого строится выделение; если оно допустимо, оно применяется к исходной
версии, результат собирается ``javac`` и гоняется на всех сценариях; вывод и код выхода сравниваются с оригиналом.
Любое расхождение или ошибка сборки — дефект. Результат — ``reports/extract_differential.md``.

    python scripts/extract_differential.py                  # по 25 фрагментов на пример
    python scripts/extract_differential.py --per-sample 10 --only inventory
"""

from __future__ import annotations

import argparse
import random
import sys
import time
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent))
from _differential import ROOT, SAMPLES, Runner, corpus, load  # noqa: E402

NOT_STATEMENTS = {"method", "external", "catch", "finally", "case"}


def fragments(project, member_id: str) -> list[list[str]]:
    """Одиночные операторы и 2–3 подряд идущих оператора одного слота метода (по идентификаторам блок-схемы)."""
    graph = project.flow(member_id)
    if graph is None:
        return []
    groups: dict[str, list] = {}
    for node in graph.nodes:
        if node.kind in NOT_STATEMENTS or node.id == member_id or "/" not in node.id:
            continue
        groups.setdefault(node.id.rsplit("/", 1)[0], []).append(node)
    found: list[list[str]] = []
    for nodes in groups.values():
        nodes.sort(key=lambda n: (n.range.start_line, n.range.start_col))
        for size in (1, 2, 3):
            for index in range(len(nodes) - size + 1):
                found.append([n.id for n in nodes[index : index + size]])
    return found


def main() -> int:
    for stream in (sys.stdout, sys.stderr):
        stream.reconfigure(encoding="utf-8", errors="replace")
    parser = argparse.ArgumentParser(description=__doc__, formatter_class=argparse.RawDescriptionHelpFormatter)
    parser.add_argument("--only", help="подстрока пути примера")
    parser.add_argument("--per-sample", type=int, default=25, help="допустимых выделений на пример")
    parser.add_argument("--seed", type=int, default=11)
    parser.add_argument("--java-home", help="каталог JDK")
    parser.add_argument("--out", default=str(ROOT / "reports"))
    args = parser.parse_args()

    from engine.edit.core import apply_text_edits
    from engine.java.project import JavaProject
    from engine.transform.extract_method import plan

    runner = Runner(Path(args.java_home) if args.java_home else None)
    started = time.monotonic()
    rows: list[str] = []
    defects: list[str] = []
    total = same = 0
    for sample in corpus():
        name = sample.relative_to(SAMPLES).as_posix()
        if args.only and args.only not in name:
            continue
        files, scenarios = load(sample)
        project = JavaProject(files)
        ok, baseline = runner.run_all(files, scenarios, "base-" + name.replace("/", "-"))
        if not ok:
            defects.append(f"{name}: оригинал не собирается: {baseline[0]}")
            continue
        candidates = [
            ids
            for member in project.members()
            if member.kind in {"method", "constructor"} and member.has_body
            for ids in fragments(project, member.id)
        ]
        random.Random(args.seed).shuffle(candidates)
        checked = refused = 0
        for ids in candidates:
            if checked >= args.per_sample:
                break
            try:
                result = plan(project, ids, None, None)
            except Exception:  # noqa: BLE001 - недопустимый выбор
                refused += 1
                continue
            if not result.available:
                refused += 1
                continue
            checked += 1
            total += 1
            after = apply_text_edits(project.files, result.edits)
            where = f"{ids[0].split('#')[-1]}…{len(ids)} оп."
            built, outputs = runner.run_all(after, scenarios, f"{name.replace('/', '-')}-{checked}")
            if not built:
                defects.append(f"{name}: выделение {where} → {result.name}: результат не собирается: {outputs[0]}")
                continue
            diffs = [s["name"] for s, a, b in zip(scenarios, baseline, outputs, strict=True) if a != b]
            if diffs:
                defects.append(f"{name}: выделение {where} → {result.name} меняет вывод в сценариях: {', '.join(diffs[:4])}")
            else:
                same += 1
        rows.append(f"| {name} | {len(candidates)} | {refused} | {checked} | {len(scenarios)} |")
        print(f"{name}: проверено {checked}, отказов {refused}", flush=True)

    out = Path(args.out)
    out.mkdir(exist_ok=True)
    report = [
        "# Дифференциальная проверка «Выделить в метод» (REF-01, I8)",
        "",
        "Случайные (seed 11) фрагменты — один оператор или 2–3 подряд идущих — выделяются в метод, результат собирается "
        "`javac` и гоняется на всех сценариях примера; вывод и код выхода сравниваются с оригиналом.",
        "",
        "| Пример | Фрагментов в выборке | Отказов (с причиной) | Выделено и проверено | Сценариев |",
        "|---|---|---|---|---|",
        *rows,
        "",
        f"Проверено выделений: {total}, вывод совпал у {same}; время {time.monotonic() - started:.0f} с.",
        "",
        "## Итог",
        "",
        "Расхождений и ошибок сборки нет." if not defects else "Найдены дефекты:",
        *[f"- {d}" for d in defects],
    ]
    (out / "extract_differential.md").write_text("\n".join(report) + "\n", encoding="utf-8", newline="\n")
    print("\n".join(report[-6:]))
    return 1 if defects else 0


if __name__ == "__main__":
    sys.exit(main())
