"""I6 (docs/10): ``duplicate`` → ``delete`` и ``wrap`` → ``unwrap`` возвращают исходный текст — для блоков корпуса."""

from __future__ import annotations

from pathlib import Path

import pytest

from engine.edit.core import EditError
from engine.edit.pipeline import apply_ops
from engine.java.project import JavaProject

SAMPLES = Path(__file__).resolve().parents[3] / "samples"
CORPUS = [
    "demo-orders",
    "monoliths/inventory",
    "monoliths/graph-paths",
    "monoliths/text-analyzer",
    "monoliths/library",
]
STATEMENTS = {
    "operation",
    "assign",
    "return",
    "condition",
    "loop_foreach",
    "loop_for",
    "loop_while",
}


def load(directory: str) -> dict[str, str]:
    root = SAMPLES / directory
    return {
        p.relative_to(root).as_posix(): p.read_text(encoding="utf-8") for p in root.rglob("*.java")
    }


@pytest.mark.parametrize("directory", CORPUS)
def test_duplicate_then_delete_restores_the_text(directory):
    files = load(directory)
    project = JavaProject(files)
    checked = 0
    for graph in project.flows():
        for node in graph.nodes:
            if node.kind not in {"operation", "assign"} or node.id == graph.method_id:
                continue
            try:
                copied = apply_ops(files, [{"op": "duplicate_node", "node_id": node.id}])
            except EditError:
                continue  # оператор в слоте, куда копию не поставить (например, тело без блока)
            assert copied.focus_node_id and copied.focus_node_id != node.id
            restored = apply_ops(
                copied.files, [{"op": "delete_node", "node_id": copied.focus_node_id}]
            )
            assert restored.files == files, node.id
            checked += 1
            if checked >= 40:
                return
    assert checked >= 10


@pytest.mark.parametrize("directory", CORPUS)
def test_wrap_then_unwrap_restores_the_text(directory):
    files = load(directory)
    project = JavaProject(files)
    checked = 0
    for graph in project.flows():
        for node in graph.nodes:
            if node.kind not in {"operation", "assign"} or node.id == graph.method_id:
                continue
            ops = [
                {
                    "op": "wrap_node",
                    "node_ids": [node.id],
                    "wrapper": "if",
                    "fields": {"expr": "true"},
                }
            ]
            try:
                wrapped = apply_ops(files, ops)
            except EditError:
                continue
            assert wrapped.focus_node_id
            restored = apply_ops(
                wrapped.files, [{"op": "unwrap_node", "node_id": wrapped.focus_node_id}]
            )
            assert restored.files == files, node.id
            checked += 1
            if checked >= 40:
                return
    assert checked >= 10
