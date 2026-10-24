"""`scripts/verify_samples.py` (S6.4, метка `jdk`): корпус через API и сводный отчёт."""

from __future__ import annotations

import json
import subprocess
import sys
from pathlib import Path

import pytest

pytestmark = [pytest.mark.jdk, pytest.mark.slow]

SCRIPT = Path(__file__).resolve().parents[3] / "scripts" / "verify_samples.py"


def run_script(out: Path, *args: str) -> subprocess.CompletedProcess[str]:
    return subprocess.run(
        [sys.executable, "-I", str(SCRIPT), "--out", str(out), *args],
        capture_output=True,
        text=True,
        encoding="utf-8",
        timeout=300,
        check=False,
    )


def test_report_for_selected_samples(tmp_path):
    done = run_script(tmp_path, "--only", "analyzer-pairs", "--only", "demo-orders")
    assert done.returncode == 0, done.stdout + done.stderr
    rows = json.loads((tmp_path / "verify_samples.json").read_text(encoding="utf-8"))
    assert [r["sample"] for r in rows] == ["demo-orders", "analyzer-pairs"]
    for row in rows:
        assert row["gate"] == "passed" and row["passed"] == row["total"] > 0
        assert row["syntax_errors"] == 0 and row["coverage"] > 0.8
    text = (tmp_path / "verify_samples.md").read_text(encoding="utf-8")
    assert "| demo-orders | 995 | 11 | ✓ 11/11 |" in text
    assert "Все примеры зелёные." in text


def test_unknown_sample_is_an_error(tmp_path):
    done = run_script(tmp_path, "--only", "no-such-sample")
    assert done.returncode == 2
