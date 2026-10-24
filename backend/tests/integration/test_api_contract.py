"""Контракт API (S1.1): все эндпоинты docs/08-api.md описаны в OpenAPI, заглушки отвечают 501."""

from __future__ import annotations

import json
import re
from pathlib import Path

import pytest
from pydantic import TypeAdapter

from app.main import create_app
from app.schemas.ops import GraphOp

REPO_ROOT = Path(__file__).resolve().parents[3]

# (метод, путь) из docs/08-api.md; параметры пути нормализованы до ``{}``.
EXPECTED_ENDPOINTS = {
    ("GET", "/api/health"),
    ("GET", "/api/samples"),
    ("POST", "/api/projects"),
    ("GET", "/api/projects"),
    ("GET", "/api/projects/{}"),
    ("PATCH", "/api/projects/{}"),
    ("DELETE", "/api/projects/{}"),
    ("GET", "/api/projects/{}/versions"),
    ("GET", "/api/projects/{}/versions/{}"),
    ("POST", "/api/projects/{}/versions/{}/restore"),
    ("POST", "/api/projects/{}/undo"),
    ("POST", "/api/projects/{}/redo"),
    ("GET", "/api/projects/{}/files"),
    ("GET", "/api/projects/{}/files/{}"),
    ("PUT", "/api/projects/{}/files/{}"),
    ("POST", "/api/projects/{}/files"),
    ("DELETE", "/api/projects/{}/files/{}"),
    ("GET", "/api/projects/{}/diff"),
    ("POST", "/api/projects/{}/parse"),
    ("GET", "/api/projects/{}/structure"),
    ("GET", "/api/projects/{}/flow"),
    ("GET", "/api/projects/{}/tree"),
    ("GET", "/api/projects/{}/nodes/{}"),
    ("POST", "/api/projects/{}/nodes/lookup"),
    ("GET", "/api/projects/{}/locate"),
    ("POST", "/api/projects/{}/ops"),
    ("POST", "/api/projects/{}/ops/validate"),
    ("GET", "/api/projects/{}/palette"),
    ("POST", "/api/projects/{}/validate/expression"),
    ("GET", "/api/projects/{}/analysis"),
    ("GET", "/api/projects/{}/findings"),
    ("GET", "/api/projects/{}/findings/{}"),
    ("GET", "/api/projects/{}/complexity"),
    ("GET", "/api/projects/{}/metrics"),
    ("POST", "/api/projects/{}/findings/{}/verify"),
    ("GET", "/api/projects/{}/fixes"),
    ("POST", "/api/projects/{}/fixes/{}/preview"),
    ("POST", "/api/projects/{}/fixes/{}/apply"),
    ("POST", "/api/projects/{}/optimize/plan"),
    ("POST", "/api/projects/{}/optimize/apply"),
    ("POST", "/api/projects/{}/extract-method/preview"),
    ("POST", "/api/projects/{}/extract-method/apply"),
    ("POST", "/api/projects/{}/decompose/plan"),
    ("POST", "/api/projects/{}/decompose/rescore"),
    ("POST", "/api/projects/{}/decompose/preview"),
    ("POST", "/api/projects/{}/decompose/apply"),
    ("POST", "/api/projects/{}/versions/{}/accept"),
    ("POST", "/api/projects/{}/versions/{}/reject"),
    ("GET", "/api/projects/{}/scenarios"),
    ("POST", "/api/projects/{}/scenarios"),
    ("PUT", "/api/projects/{}/scenarios/{}"),
    ("DELETE", "/api/projects/{}/scenarios/{}"),
    ("POST", "/api/projects/{}/scenarios/import"),
    ("GET", "/api/projects/{}/scenarios/export"),
    ("POST", "/api/projects/{}/scenarios/generate"),
    ("GET", "/api/projects/{}/input-schema"),
    ("POST", "/api/projects/{}/runs"),
    ("GET", "/api/runs/{}"),
    ("GET", "/api/runs/{}/events"),
    ("POST", "/api/runs/{}/cancel"),
    ("GET", "/api/projects/{}/runs"),
    ("GET", "/api/runs/{}/report"),
    ("POST", "/api/projects/{}/build"),
    ("GET", "/api/projects/{}/export/source"),
    ("GET", "/api/projects/{}/export/report"),
    ("GET", "/api/projects/{}/export/bundle"),
}

SSE_EVENTS = {"status", "log", "gate", "item", "step", "progress", "result", "error", "done"}


@pytest.fixture(scope="module")
def schema() -> dict:
    return create_app().openapi()


def _endpoints(schema: dict) -> set[tuple[str, str]]:
    found = set()
    for path, item in schema["paths"].items():
        for method in item:
            found.add((method.upper(), re.sub(r"\{[^}]+\}", "{}", path)))
    return found


def test_all_documented_endpoints_are_in_openapi(schema):
    found = _endpoints(schema)
    assert EXPECTED_ENDPOINTS - found == set(), "эндпоинты из docs/08-api.md отсутствуют"
    assert found - EXPECTED_ENDPOINTS == set(), "эндпоинты не описаны в docs/08-api.md"


def test_operation_ids_are_unique_function_names(schema):
    ids = [op["operationId"] for item in schema["paths"].values() for op in item.values()]
    assert len(ids) == len(set(ids))
    assert all(re.fullmatch(r"[a-z_]+", op_id) for op_id in ids), ids


def test_errors_use_single_envelope(schema):
    schemas = schema["components"]["schemas"]
    assert "HTTPValidationError" not in schemas
    for path, item in schema["paths"].items():
        for method, op in item.items():
            default = op["responses"].get("default")
            assert default, f"{method.upper()} {path}: нет описания ошибки"
            ref = default["content"]["application/json"]["schema"]["$ref"]
            assert ref.endswith("/ErrorResponse")
            assert "422" not in op["responses"], f"{method.upper()} {path}"


def test_sse_endpoint_describes_all_events(schema):
    content = schema["paths"]["/api/runs/{run_id}/events"]["get"]["responses"]["200"]["content"]
    assert list(content) == ["text/event-stream"]
    variants = content["text/event-stream"]["schema"]["oneOf"]
    names = {ref["$ref"].rsplit("/", 1)[-1] for ref in variants}
    assert names == {f"{event.capitalize()}Event" for event in SSE_EVENTS}
    for name in names:
        event_field = schema["components"]["schemas"][name]["properties"]["event"]
        assert event_field["const"] == name.removesuffix("Event").lower()


def test_project_create_accepts_json_and_multipart(schema):
    content = schema["paths"]["/api/projects"]["post"]["requestBody"]["content"]
    assert set(content) == {"application/json", "multipart/form-data"}


def test_requests_do_not_get_input_output_suffixes(schema):
    names = schema["components"]["schemas"]
    assert not [n for n in names if n.endswith(("-Input", "-Output"))]


def test_response_fields_with_defaults_are_required(schema):
    flow_node = schema["components"]["schemas"]["FlowNode"]
    assert {"badges", "data", "errors"} <= set(flow_node["required"])


ALL_OPS = [
    {"op": "insert_node", "parent_id": "A#B.m()", "slot": "body", "index": 0, "kind": "return"},
    {"op": "update_node", "node_id": "A#B.m()/if[0]", "fields": {"expr": "x > 0"}},
    {"op": "delete_node", "node_id": "A#B.m()/if[0]", "mode": "unwrap"},
    {"op": "move_node", "node_id": "a", "new_parent_id": "b", "slot": "body", "index": 1},
    {"op": "duplicate_node", "node_id": "a"},
    {"op": "wrap_node", "node_ids": ["a", "b"], "wrapper": "try"},
    {"op": "unwrap_node", "node_id": "a"},
    {"op": "add_branch", "node_id": "a", "branch": "else_if", "fields": {"expr": "x"}},
    {"op": "remove_branch", "node_id": "a", "branch_id": "a/else"},
    {"op": "rename_symbol", "symbol_id": "A#B.m()/var:x", "new_name": "y"},
    {"op": "add_member", "type_id": "A#B", "kind": "method", "fields": {"name": "f"}},
    {"op": "update_member", "member_id": "A#B.m()", "fields": {"name": "g"}},
    {"op": "delete_member", "member_id": "A#B.m()"},
    {"op": "replace_code", "node_id": "a", "text": "int x = 1;"},
]


def test_graph_ops_cover_04_section_5_2():
    names = {op["op"] for op in ALL_OPS}
    assert len(names) == 14
    adapter = TypeAdapter(GraphOp)
    for raw in ALL_OPS:
        assert adapter.validate_python(raw).op == raw["op"]


def test_unknown_op_is_rejected_with_bad_request(client):
    created = client.post(
        "/api/projects",
        json={"name": "t", "files": [{"path": "A.java", "content": "class A {}"}]},
    ).json()
    body = {"base_version_id": 1, "ops": [{"op": "explode", "node_id": "a"}]}
    response = client.post(f"/api/projects/{created['id']}/ops", json=body)
    assert response.status_code == 400
    assert response.json()["error"]["code"] == "BAD_REQUEST"


def test_no_endpoint_answers_501_any_more(client):
    """Все эндпоинты docs/08-api.md реализованы: `501 NOT_IMPLEMENTED` больше не встречается."""
    schema = client.get("/api/openapi.json").json()
    assert "NOT_IMPLEMENTED" not in str(schema)
    for path in REPO_ROOT.joinpath("backend", "app", "api").glob("*.py"):
        assert "not_implemented(" not in path.read_text(encoding="utf-8"), path.name


def test_stub_validates_request_before_answering(client):
    response = client.get("/api/projects/not-a-number")
    assert response.status_code == 400
    assert response.json()["error"]["code"] == "BAD_REQUEST"


def test_committed_openapi_is_current(schema):
    """``frontend/openapi.json`` — результат ``app.export_openapi``; CI проверяет и ``schema.d.ts``."""
    committed = REPO_ROOT / "frontend" / "openapi.json"
    if not committed.exists():
        pytest.skip("нет frontend/openapi.json")
    assert json.loads(committed.read_text(encoding="utf-8")) == schema, (
        "Схема API изменилась: uv run python -m app.export_openapi ../frontend/openapi.json "
        "&& cd ../frontend && pnpm gen:api"
    )
