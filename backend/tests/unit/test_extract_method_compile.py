"""Свойство S9.2 (метка `jdk`): допустимое выделение метода даёт код, который компилируется."""

from __future__ import annotations

import pytest

from engine.edit.core import apply_text_edits
from engine.java.project import JavaProject
from engine.runtime.compiler import compile_workspace
from engine.runtime.workspace import materialize
from tests.unit.test_extract_method import candidate_plans

pytestmark = pytest.mark.jdk


@pytest.mark.parametrize(
    "directory", ["demo-orders", "monoliths/inventory", "monoliths/graph-paths"]
)
def test_extracted_fragments_compile(directory, tmp_path):
    plans = candidate_plans(directory, 14)
    assert plans
    failures = []
    for index, (project, result) in enumerate(plans):
        updated = apply_text_edits(project.files, result.edits)
        compiled = compile_workspace(
            materialize(updated, tmp_path / directory.replace("/", "_"), f"x{index}")
        )
        if not compiled.ok:
            errors = [
                f"{d.file}:{d.line}: {d.message}"
                for d in compiled.diagnostics
                if d.severity == "error"
            ][:2]
            failures.append((result.member_id, result.name, errors))
    assert not failures, failures


def test_extraction_result_keeps_the_class_structure(tmp_path):
    project, result = candidate_plans("demo-orders", 3)[0]
    new = JavaProject(apply_text_edits(project.files, result.edits))
    assert result.name in {m.name for m in new.members()}
