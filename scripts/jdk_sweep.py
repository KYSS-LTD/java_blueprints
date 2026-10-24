#!/usr/bin/env python3
"""Прочность движка на чужом коде (S10.2): исходники JDK из ``src.zip`` проходят весь конвейер без падений.

Для каждого файла (самые крупные из выбранных пакетов, по одному файлу как проект): блок-схемы **всех** методов
и их инварианты (id уникальны, все блоки достижимы, ни один оператор не потерян, нет ошибок в блоках) →
анализ → предпросмотр **каждого** доступного исправления и проверка, что результат разбирается →
замена блоков их же текстом (инвариант I2: ни один байт не меняется). Результат — ``reports/jdk_sweep.md``;
код выхода 1, если найдено нарушение.

    python scripts/jdk_sweep.py                       # 120 крупнейших файлов java.base: util, lang, io, text, time
    python scripts/jdk_sweep.py --limit 200 --prefix java.base/java/util/,java.desktop/javax/swing/
    python scripts/jdk_sweep.py --src /path/to/src.zip
"""

from __future__ import annotations

import argparse
import os
import sys
import time
import zipfile
from collections import Counter
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
BACKEND = ROOT / "backend"


def find_sources(explicit: str | None) -> Path:
    candidates = [Path(explicit)] if explicit else []
    for variable in ("JB_JAVA_HOME", "JAVA_HOME"):
        if os.environ.get(variable):
            candidates.append(Path(os.environ[variable]) / "lib" / "src.zip")
    candidates += [p / "lib" / "src.zip" for p in sorted((Path.home() / ".jdks").glob("*"))]
    for path in candidates:
        if path.is_file():
            return path
    raise SystemExit("не найден src.zip JDK: укажите --src или JAVA_HOME")


def pick(archive: zipfile.ZipFile, prefixes: list[str], limit: int) -> dict[str, str]:
    names = [
        n
        for n in archive.namelist()
        if n.endswith(".java")
        and any(n.startswith(p) and n.count("/") == p.count("/") for p in prefixes)
    ]
    names = sorted(names, key=lambda n: -archive.getinfo(n).file_size)[:limit]
    return {n: archive.read(n).decode("utf-8", errors="replace").replace("\r\n", "\n") for n in names}


def main() -> int:
    for stream in (sys.stdout, sys.stderr):
        stream.reconfigure(encoding="utf-8", errors="replace")
    parser = argparse.ArgumentParser(description=__doc__, formatter_class=argparse.RawDescriptionHelpFormatter)
    parser.add_argument("--src", help="путь к src.zip (по умолчанию из JAVA_HOME / ~/.jdks)")
    parser.add_argument("--limit", type=int, default=120, help="сколько крупнейших файлов взять")
    parser.add_argument("--prefix",
        default="java.base/java/util/,java.base/java/lang/,java.base/java/io/,java.base/java/text/,java.base/java/time/",
        help="пакеты через запятую",)
    parser.add_argument("--identity", type=int, default=6, help="методов на файл для проверки I2")
    parser.add_argument("--out", default=str(ROOT / "reports"))
    args = parser.parse_args()

    sys.path.insert(0, str(BACKEND))
    from engine.analysis.analyzer import analyze
    from engine.edit.pipeline import apply_ops
    from engine.java import cst
    from engine.java.project import JavaProject
    from engine.transform.base import apply_proposal
    from engine.transform.fixes import propose
    from tests.unit.test_corpus_invariants import STATEMENT_TYPES, reachable
    from tests.unit.test_edit_ops import _slice

    source_zip = find_sources(args.src)
    with zipfile.ZipFile(source_zip) as archive:
        texts = pick(archive, args.prefix.split(","), args.limit)
    print(f"{len(texts)} файлов из {source_zip}", flush=True)

    problems: Counter[str] = Counter()
    examples: dict[str, str] = {}
    findings: Counter[str] = Counter()
    previews: Counter[str] = Counter()
    fallbacks = 0
    methods = lines = identity = 0
    timings: list[tuple[float, str, int]] = []

    def fail(kind: str, where: str) -> None:
        problems[kind] += 1
        examples.setdefault(kind, where)

    for name, text in texts.items():
        short = name.rsplit("/", 1)[-1]
        started = time.perf_counter()
        lines += text.count("\n")
        project = JavaProject({short: text})
        if project.syntax_issues():
            fail("исходник не разбирается", short)
            continue
        flows = []
        for member in project.members():
            if member.kind not in {"method", "constructor", "initializer"} or not member.has_body:
                continue
            methods += 1
            where = f"{short}: {member.key}"
            try:
                graph = project.flow(member.id)
            except Exception as error:  # noqa: BLE001 - любая ошибка движка — дефект
                fail(f"блок-схема: {type(error).__name__}", where)
                continue
            if graph is None:
                fail("блок-схема не построена", where)
                continue
            flows.append((member, graph))
            ids = {n.id for n in graph.nodes}
            if len(ids) != len(graph.nodes):
                fail("дубли id блоков", where)
            if reachable(graph) != ids:
                fail("недостижимые блоки", where)
            fallbacks += sum(1 for n in graph.nodes if n.kind == "code")
            if any(n.errors for n in graph.nodes):
                fail("ошибки в блоках", where)
            parsed = project.parsed(short)
            declaration = cst.find_node(parsed, member.start_byte, member.end_byte, frozenset({member.decl_type}))
            if declaration is None:
                fail("объявление метода не найдено", where)
                continue
            boxes = [(n.range.start_line, n.range.start_col, n.range.end_line, n.range.end_col) for n in graph.nodes]
            for statement in cst.walk(declaration):
                if statement.type not in STATEMENT_TYPES:
                    continue
                span = parsed.range_of(statement)
                box = (span.start.line, span.start.column, span.end.line, span.end.column)
                if not any(b[:2] <= box[:2] and box[2:] <= b[2:] for b in boxes):
                    fail(f"оператор потерян: {statement.type}", f"{where}, строка {box[0]}")
        try:
            result = analyze(project)
        except Exception as error:  # noqa: BLE001
            fail(f"анализ: {type(error).__name__}", short)
            continue
        for finding in result.findings:
            findings[finding.rule_id] += 1
            where = f"{short}: {finding.rule_id} строка {finding.location.start_line}"
            try:
                proposal = propose(project, finding)
                if proposal is None:
                    continue
                if not proposal.available:
                    previews["отказ с причиной"] += 1
                    continue
                updated = apply_proposal(project, proposal)
                previews["применимо"] += 1
                if JavaProject(updated).syntax_issues():
                    fail(f"исправление {proposal.fix_id} ломает синтаксис", where)
            except Exception as error:  # noqa: BLE001
                fail(f"исправление {finding.rule_id}: {type(error).__name__}", f"{where}: {str(error)[:80]}")
        for _member, graph in flows[: args.identity]:
            for node in graph.nodes:
                if node.id == graph.method_id or node.kind in {"case", "finally", "catch"}:
                    continue
                r = node.range
                original = _slice(text, r.start_line, r.start_col, r.end_line, r.end_col)
                try:
                    changed = apply_ops({short: text}, [{"op": "replace_code", "node_id": node.id, "text": original}])
                except Exception as error:  # noqa: BLE001
                    fail(f"I2: {type(error).__name__}", f"{short}: {node.id}")
                    continue
                identity += 1
                if changed.files[short] != text:
                    fail("I2: байты изменились", f"{short}: {node.id}")
        timings.append((time.perf_counter() - started, short, text.count("\n")))

    timings.sort(reverse=True)
    out = Path(args.out)
    out.mkdir(exist_ok=True)
    report = [
        "# Прочность на чужом коде (`scripts/jdk_sweep.py`)",
        "",
        f"Источник: `{source_zip.name}`, пакеты `{args.prefix}`; файлов {len(texts)}, строк {lines}, методов {methods}.",
        "",
        "| Проверка | Результат |",
        "|---|---|",
        f"| блок-схемы методов, инварианты (id, достижимость, ни один оператор не потерян) | {methods} методов, запасных блоков «Код»: {fallbacks} |",
        f"| анализ | {sum(findings.values())} находок по {len(findings)} правилам, падений нет |",
        f"| предпросмотр исправлений | применимо {previews['применимо']}, отказ с причиной {previews['отказ с причиной']}; результат всегда разбирается |",
        f"| I2: замена блока его же текстом | {identity} замен, байты не менялись |",
        f"| нарушений | {sum(problems.values())} |",
        "",
        "Самые долгие файлы (разбор + блок-схемы + анализ + все предпросмотры + I2): "
        + ", ".join(f"`{n}` {t:.1f} с ({size} строк)" for t, n, size in timings[:5]),
    ]
    if problems:
        report += ["", "## Нарушения", "", "| Вид | Сколько | Пример |", "|---|---|---|"]
        report += [f"| {k} | {v} | `{examples[k]}` |" for k, v in problems.most_common()]
    (out / "jdk_sweep.md").write_text("\n".join(report) + "\n", encoding="utf-8", newline="\n")
    print("\n".join(report))
    return 1 if problems else 0


if __name__ == "__main__":
    sys.exit(main())
