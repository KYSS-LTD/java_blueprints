#!/usr/bin/env python3
"""Комплект сдачи (S10.5, docs/12 § 3): отчёты проверок и архив репозитория в ``dist/``.

Шаги: тесты backend и frontend (JUnit XML в ``reports/``), прогон корпуса (`verify_samples.py`), баг-баш
(`bug_bash.py`), бюджеты производительности (`perf_budget.py`), сводка ``reports/SUMMARY.md`` по чек-листу
и архив ``dist/java-blueprints-submission.zip`` (файлы, отслеживаемые git, плюс ``reports/``).

    python scripts/submit.py                    # полный комплект (долго: несколько минут)
    python scripts/submit.py --skip-tests       # только отчёты корпуса и архив
    python scripts/submit.py --java-home /path/to/jdk
"""

from __future__ import annotations

import argparse
import os
import shutil
import subprocess
import sys
import zipfile
from pathlib import Path
from xml.etree import ElementTree

ROOT = Path(__file__).resolve().parents[1]
REPORTS = ROOT / "reports"
DIST = ROOT / "dist"

REQUIRED = [
    "README.md",
    "SOLUTION.md",
    "docker-compose.yml",
    "docs/README.md",
    "docs/13-acceptance.md",
    "samples/demo-orders/Main.java",
]


def run(title: str, command: list[str], cwd: Path, env: dict[str, str] | None = None) -> bool:
    print(f"\n=== {title}\n$ {' '.join(command)}", flush=True)
    result = subprocess.run(command, cwd=cwd, env={**os.environ, **(env or {})})
    ok = result.returncode == 0
    print(f"--- {title}: {'готово' if ok else f'код выхода {result.returncode}'}", flush=True)
    return ok


# тесты, которым по замыслу нужен JDK 21+: на более раннем JDK их падение не считается провалом комплекта
JDK21_ONLY = {"test_detect_jdk_finds_supported_version", "test_health_ok_with_installed_jdk"}


def only_expected_failures(path: Path) -> bool:
    if not path.exists():
        return False
    failed = {
        case.get("name", "")
        for case in ElementTree.parse(path).getroot().iter("testcase")
        if case.find("failure") is not None or case.find("error") is not None
    }
    return failed <= JDK21_ONLY


def jdk_sources(java_home: str | None) -> bool:
    homes = [java_home, os.environ.get("JB_JAVA_HOME"), os.environ.get("JAVA_HOME")]
    homes += [str(p) for p in (Path.home() / ".jdks").glob("*")]
    return any(h and (Path(h) / "lib" / "src.zip").is_file() for h in homes)


def pnpm() -> list[str]:
    found = shutil.which("pnpm")
    if found:
        return [found]
    corepack = shutil.which("corepack")  # на Windows это corepack.CMD
    return [corepack or "corepack", "pnpm"]


def junit_counts(path: Path) -> str:
    """Короткая сводка из JUnit XML: ``прошло/всего``; ``нет файла`` — если отчёта нет."""
    if not path.exists():
        return "нет файла"
    root = ElementTree.parse(path).getroot()
    suites = [root] if root.tag == "testsuite" else list(root.iter("testsuite"))
    total = sum(int(s.get("tests", 0)) for s in suites)
    bad = sum(int(s.get("failures", 0)) + int(s.get("errors", 0)) for s in suites)
    skipped = sum(int(s.get("skipped", 0)) for s in suites)
    return f"{total - bad - skipped} из {total}" + (f", пропущено {skipped}" if skipped else "")


def summary(steps: dict[str, bool]) -> str:
    lines = ["# Комплект сдачи", "", "Сводка по чек-листу [docs/12 § 3](../docs/12-demo-and-submission.md).", ""]
    lines += ["| Пункт | Состояние |", "|---|---|"]
    for name in REQUIRED:
        lines.append(f"| `{name}` | {'есть' if (ROOT / name).exists() else 'НЕТ'} |")
    lines += ["", "| Проверка | Результат |", "|---|---|"]
    for title, ok in steps.items():
        lines.append(f"| {title} | {'✓' if ok else '✗'} |")
    lines += [
        "",
        f"- pytest: {junit_counts(REPORTS / 'pytest-report.xml')} (`reports/pytest-report.xml`)",
        f"- vitest: {junit_counts(REPORTS / 'vitest-report.xml')} (`reports/vitest-report.xml`)",
        f"- playwright: {junit_counts(REPORTS / 'playwright-report.xml')} (`reports/playwright-report.xml`; "
        "запуск: `cd frontend && pnpm e2e` при поднятом приложении)",
        "- корпус через API: `reports/verify_samples.md`, `reports/verify_samples.json`",
        "- баг-баш: `reports/bug_bash.md`",
        "- бюджеты производительности: `reports/perf_budget.md`",
        "- прочность на чужом коде (исходники JDK): `reports/jdk_sweep.md`",
        "",
        "Два теста backend требуют JDK 21+ и на более раннем JDK падают по замыслу (`test_jdk.py`, `/health`).",
    ]
    return "\n".join(lines) + "\n"


def archive() -> Path:
    DIST.mkdir(exist_ok=True)
    target = DIST / "java-blueprints-submission.zip"
    tracked = subprocess.run(
        ["git", "ls-files"], cwd=ROOT, capture_output=True, text=True, encoding="utf-8", check=True
    ).stdout.splitlines()
    extra = [str(p.relative_to(ROOT)).replace("\\", "/") for p in REPORTS.glob("*") if p.is_file()]
    with zipfile.ZipFile(target, "w", zipfile.ZIP_DEFLATED) as bundle:
        for name in sorted({*tracked, *extra}):
            path = ROOT / name
            if path.is_file():
                bundle.write(path, f"java-blueprints/{name}")
    return target


def main() -> int:
    for stream in (sys.stdout, sys.stderr):  # Windows: вывод в файл иначе идёт в кодовой странице консоли
        stream.reconfigure(encoding="utf-8", errors="replace")
    parser = argparse.ArgumentParser(description=__doc__, formatter_class=argparse.RawDescriptionHelpFormatter)
    parser.add_argument("--skip-tests", action="store_true", help="не гонять pytest и vitest")
    parser.add_argument("--skip-pytest", action="store_true", help="не гонять только pytest (отчёт взять прежний)")
    parser.add_argument("--java-home", help="каталог JDK для тестов и скриптов")
    args = parser.parse_args()

    REPORTS.mkdir(exist_ok=True)
    env: dict[str, str] = {"PYTHONIOENCODING": "utf-8"}
    java_args: list[str] = []
    if args.java_home:
        env["JB_JAVA_HOME"] = args.java_home
        env["PATH"] = str(Path(args.java_home) / "bin") + os.pathsep + os.environ.get("PATH", "")
        java_args = ["--java-home", args.java_home]
    steps: dict[str, bool] = {}
    uv = ["uv", "run", "python"]

    if not args.skip_tests:
        if args.skip_pytest:
            print("pytest пропущен: используется прежний reports/pytest-report.xml")
        else:
            run(
                "pytest",
                ["uv", "run", "pytest", "-q", "-p", "no:cacheprovider", f"--junitxml={REPORTS / 'pytest-report.xml'}"],
                ROOT / "backend",
                env,
            )
        steps["pytest (backend)"] = only_expected_failures(REPORTS / "pytest-report.xml")
        steps["vitest (frontend)"] = run(
            "vitest",
            [*pnpm(), "exec", "vitest", "run", "--reporter=junit", f"--outputFile={REPORTS / 'vitest-report.xml'}"],
            ROOT / "frontend",
            {**env, "COREPACK_ENABLE_DOWNLOAD_PROMPT": "0"},
        )
    steps["verify_samples"] = run(
        "прогон корпуса", [*uv, str(ROOT / "scripts" / "verify_samples.py"), *java_args], ROOT / "backend", env
    )
    steps["bug_bash"] = run("баг-баш", [*uv, str(ROOT / "scripts" / "bug_bash.py"), *java_args], ROOT / "backend", env)
    steps["perf_budget"] = run(
        "бюджеты", [*uv, str(ROOT / "scripts" / "perf_budget.py"), *java_args], ROOT / "backend", env
    )

    if jdk_sources(args.java_home):  # нужен src.zip JDK; без него шаг пропускается
        steps["jdk_sweep"] = run(
            "прочность на чужом коде", [*uv, str(ROOT / "scripts" / "jdk_sweep.py")], ROOT / "backend", env
        )
    else:
        print("src.zip JDK не найден: проверка на чужом коде пропущена")

    (REPORTS / "SUMMARY.md").write_text(summary(steps), encoding="utf-8", newline="\n")
    target = archive()
    print(f"\nкомплект: {target} ({target.stat().st_size // 1024} КБ); сводка: {REPORTS / 'SUMMARY.md'}")
    return 0 if all(steps.values()) and all((ROOT / n).exists() for n in REQUIRED) else 1


if __name__ == "__main__":
    sys.exit(main())
