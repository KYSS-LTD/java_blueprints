"""Общее для дифференциальных проверок (`fix_differential.py`, `extract_differential.py`): корпус и прогон сценариев."""

from __future__ import annotations

import json
import sys
import tempfile
from concurrent.futures import ThreadPoolExecutor
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
SAMPLES = ROOT / "samples"
sys.path.insert(0, str(ROOT / "backend"))


def corpus() -> list[Path]:
    """Примеры со сценариями: демонстрационный монолит, ``analyzer-pairs`` и монолиты."""
    wanted = [SAMPLES / "demo-orders", SAMPLES / "analyzer-pairs", *sorted((SAMPLES / "monoliths").iterdir())]
    return [p for p in wanted if p.is_dir() and (p / "scenarios").is_dir()]


def load(sample: Path) -> tuple[dict[str, str], list[dict]]:
    files = {
        p.relative_to(sample).as_posix(): p.read_text(encoding="utf-8")
        for p in sorted(sample.rglob("*.java"))
        if "scenarios" not in p.relative_to(sample).parts
    }
    scenarios = [json.loads(p.read_text(encoding="utf-8")) for p in sorted((sample / "scenarios").glob("*.json"))]
    return files, scenarios


class Runner:
    """Собирает версию проекта и гоняет на ней сценарии; результат — вывод и код выхода каждого."""

    def __init__(self, java_home: Path | None, jobs: int = 4, timeout_s: int = 15) -> None:
        from engine.runtime.runner import RunLimits

        self.java_home = java_home
        self.jobs = jobs
        self.limits = RunLimits(timeout_s=timeout_s)
        self.builds = Path(tempfile.mkdtemp(prefix="jb-diff-"))

    def run_all(self, files: dict[str, str], scenarios: list[dict], key: str) -> tuple[bool, list]:
        from engine.runtime.compiler import compile_workspace
        from engine.runtime.runner import run_java
        from engine.runtime.workspace import materialize

        workspace = materialize(files, self.builds, key)
        built = compile_workspace(workspace, self.java_home)
        if not built.ok:
            errors = [d for d in built.diagnostics if d.severity == "error"][:2]
            return False, ["; ".join(f"{d.file}:{d.line}: {d.message}" for d in errors) or built.log[:300]]

        def one(scenario: dict):
            ran = run_java(
                workspace,
                "Main",
                stdin=scenario["stdin"],
                args=scenario.get("args", []),
                java_home=self.java_home,
                limits=self.limits,
            )
            return ran.status, ran.stdout, ran.stderr.splitlines()[0] if ran.stderr else "", ran.exit_code

        with ThreadPoolExecutor(self.jobs) as pool:
            return True, list(pool.map(one, scenarios))
