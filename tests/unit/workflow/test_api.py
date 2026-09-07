"""Unit tests for app.workflow.api (spec-08 TC3, AD-10 / AD-02 v2 host registration).

spec-09 TC1 (H4/G7): the registry is injected via ``app.state.workflow_registry``
by the host composition root; the engine module keeps no module-level cache.
"""

from __future__ import annotations

from collections.abc import Generator
from pathlib import Path

import pytest
from fastapi import FastAPI
from fastapi.testclient import TestClient
from slowapi import _rate_limit_exceeded_handler
from slowapi.errors import RateLimitExceeded

from app.core.logging import get_structlog_processors
from app.workflow import api as workflow_api
from app.workflow.cli import build_registry
from app.workflow.logging_conf import redact_processor
from app.workflow.nodes.factory import register_node_type
from tests.unit.workflow.test_cli import _ECHO_YAML, _FAIL_YAML, _FailNode

pytestmark = pytest.mark.unit

_SECOND_YAML = """
workflow_id: alpha_workflow
description: "second workflow for list testing"
entry_point: step_one
nodes:
  - name: step_one
    type: echo
    config:
      output:
        result: step one done
  - name: step_two
    type: echo
    config:
      output:
        result: step two done
edges:
  - source: step_one
    target: step_two
  - source: step_two
    target: END
state_schema:
  input:
    type: str
    description: user input
  result:
    type: str
    description: intermediate result
"""


_DETAIL_YAML = """
workflow_id: detail_test
entry_point: step_a
nodes:
  - name: step_a
    type: echo
    config:
      output:
        result: step a done
  - name: step_b
    type: echo
    config:
      output:
        result: step b done
edges:
  - source: step_a
    target: step_b
  - source: step_b
    target: END
state_schema:
  input:
    type: str
    description: user input
"""


@pytest.fixture()
def client(tmp_path: Path) -> Generator[TestClient, None, None]:
    """Minimal FastAPI app hosting the workflow router with an app.state-injected registry."""
    (tmp_path / "echo_demo.yaml").write_text(_ECHO_YAML, encoding="utf-8")
    (tmp_path / "detail_test.yaml").write_text(_DETAIL_YAML, encoding="utf-8")
    app = FastAPI()
    app.state.limiter = workflow_api.limiter
    app.state.workflow_registry = build_registry(tmp_path)
    app.state.workflow_directory = tmp_path
    app.add_exception_handler(RateLimitExceeded, _rate_limit_exceeded_handler)
    app.include_router(workflow_api.router)
    with TestClient(app) as test_client:
        yield test_client


def test_api_execute_success(client: TestClient) -> None:
    """POST execute returns the host unified envelope {code,message,data} with metadata folded into data."""
    response = client.post("/workflows/echo_demo/execute", json={"input": "hi"})
    assert response.status_code == 200
    envelope = response.json()
    assert envelope["code"] == 200
    assert envelope["message"] == "success"
    assert envelope["data"]["response"] == "hello"
    metadata = envelope["data"]["metadata"]
    assert metadata["workflow_id"] == "echo_demo"
    assert len(metadata["run_id"]) == 32
    assert metadata["duration_ms"] >= 0.0
    assert metadata["node_count"] == 1


def test_api_unknown_workflow_returns_404(client: TestClient) -> None:
    """Unknown workflow_id: 404 host envelope with a redacted message mentioning the id."""
    response = client.post("/workflows/not_exist/execute", json={})
    assert response.status_code == 404
    envelope = response.json()
    assert envelope["code"] == 404
    assert "not_exist" in envelope["message"]
    assert envelope["data"] is None


def test_api_failure_envelope_redacted(client: TestClient) -> None:
    """H6: node exception embedding a dummy secret never leaks via the envelope."""
    register_node_type("fail_leak", _FailNode)
    app_state = client.app.state
    fail_path = Path(app_state.workflow_directory) / "fail_demo.yaml"
    fail_path.write_text(_FAIL_YAML, encoding="utf-8")
    app_state.workflow_registry = build_registry(app_state.workflow_directory)  # rebuild with new definition
    response = client.post("/workflows/fail_demo/execute", json={})
    assert response.status_code == 500
    assert "sk-live-leak-999" not in response.text
    envelope = response.json()
    assert envelope["code"] == 500
    assert envelope["data"] is None


def test_api_missing_registry_injection_returns_500(tmp_path: Path) -> None:
    """G7: without host-injected app.state.workflow_registry the endpoint fails loudly (no implicit cache)."""
    (tmp_path / "echo_demo.yaml").write_text(_ECHO_YAML, encoding="utf-8")
    app = FastAPI()
    app.state.limiter = workflow_api.limiter
    app.add_exception_handler(RateLimitExceeded, _rate_limit_exceeded_handler)
    app.include_router(workflow_api.router)
    with TestClient(app) as test_client:
        response = test_client.post("/workflows/echo_demo/execute", json={})
    assert response.status_code == 500
    assert "workflow_registry" in response.json()["message"]


def test_api_projection_non_list_non_dict_output_wrapped_as_result() -> None:
    """Egress mapping locks the non-dict/non-list branch: data = {"result": output, "metadata": {...}}."""
    metadata = {"workflow_id": "demo", "run_id": "r", "duration_ms": 1.0, "node_count": 1}
    response = workflow_api.ApiResponse(success=True, data="plain string", metadata=metadata)
    content = workflow_api._host_envelope_content(response, 200)
    assert content == {"code": 200, "message": "success", "data": {"result": "plain string", "metadata": metadata}}
    assert workflow_api._project_to_host_envelope(response, 200).status_code == 200


def test_api_projection_list_output_passes_through() -> None:
    """CONTRACT §4.13: list data passes through as ``data`` directly (no result/metadata wrapping)."""
    response = workflow_api.ApiResponse(success=True, data=[{"workflow_id": "a"}, {"workflow_id": "b"}])
    content = workflow_api._host_envelope_content(response, 200)
    assert content == {"code": 200, "message": "success", "data": [{"workflow_id": "a"}, {"workflow_id": "b"}]}


def test_list_workflows_empty(tmp_path: Path) -> None:
    """Empty registry returns data=[], code=200."""
    app = FastAPI()
    app.state.limiter = workflow_api.limiter
    app.state.workflow_registry = build_registry(tmp_path)
    app.add_exception_handler(RateLimitExceeded, _rate_limit_exceeded_handler)
    app.include_router(workflow_api.router)
    with TestClient(app) as test_client:
        response = test_client.get("/workflows")
    assert response.status_code == 200
    envelope = response.json()
    assert envelope["code"] == 200
    assert envelope["message"] == "success"
    assert envelope["data"] == []


def test_list_workflows_returns_summaries_sorted(tmp_path: Path) -> None:
    """Two registered workflows: returns 2 summaries sorted by workflow_id ascending."""
    (tmp_path / "echo_demo.yaml").write_text(_ECHO_YAML, encoding="utf-8")
    (tmp_path / "alpha_workflow.yaml").write_text(_SECOND_YAML, encoding="utf-8")
    app = FastAPI()
    app.state.limiter = workflow_api.limiter
    app.state.workflow_registry = build_registry(tmp_path)
    app.add_exception_handler(RateLimitExceeded, _rate_limit_exceeded_handler)
    app.include_router(workflow_api.router)
    with TestClient(app) as test_client:
        response = test_client.get("/workflows")
    assert response.status_code == 200
    data = response.json()["data"]
    assert len(data) == 2
    assert data[0]["workflow_id"] == "alpha_workflow"
    assert data[0]["node_count"] == 2
    assert data[0]["entry_point"] == "step_one"
    assert data[1]["workflow_id"] == "echo_demo"
    assert data[1]["node_count"] == 1
    assert data[1]["entry_point"] == "greet"


def test_list_workflows_reflects_registry_changes(tmp_path: Path) -> None:
    """H4: no module-level cache; consecutive calls reflect registry changes in real time."""
    (tmp_path / "echo_demo.yaml").write_text(_ECHO_YAML, encoding="utf-8")
    app = FastAPI()
    app.state.limiter = workflow_api.limiter
    app.state.workflow_registry = build_registry(tmp_path)
    app.add_exception_handler(RateLimitExceeded, _rate_limit_exceeded_handler)
    app.include_router(workflow_api.router)
    with TestClient(app) as test_client:
        first = test_client.get("/workflows").json()["data"]
        assert len(first) == 1
        (tmp_path / "alpha_workflow.yaml").write_text(_SECOND_YAML, encoding="utf-8")
        app.state.workflow_registry = build_registry(tmp_path)
        second = test_client.get("/workflows").json()["data"]
        assert len(second) == 2


def test_list_workflows_missing_registry_returns_500(tmp_path: Path) -> None:
    """H4/G7: list endpoint also requires host-injected registry."""
    app = FastAPI()
    app.state.limiter = workflow_api.limiter
    app.add_exception_handler(RateLimitExceeded, _rate_limit_exceeded_handler)
    app.include_router(workflow_api.router)
    with TestClient(app) as test_client:
        response = test_client.get("/workflows")
    assert response.status_code == 500
    assert "workflow_registry" in response.json()["message"]


def test_get_workflow_json_projection(client: TestClient) -> None:
    """GET /workflows/{id} format=json returns definition with key fields, no execution_history."""
    response = client.get("/workflows/detail_test")
    assert response.status_code == 200
    envelope = response.json()
    assert envelope["code"] == 200
    assert envelope["message"] == "success"
    data = envelope["data"]
    assert data["workflow_id"] == "detail_test"
    assert data["entry_point"] == "step_a"
    assert len(data["nodes"]) == 2
    assert "edges" in data
    assert "state_schema" in data
    assert "execution_history" not in data


def test_get_workflow_json_ui_layout_not_present(client: TestClient) -> None:
    """WorkflowDefinition(extra='ignore') drops ui_layout at parse time; projection reflects model reality."""
    response = client.get("/workflows/detail_test")
    data = response.json()["data"]
    assert "ui_layout" not in data


def test_get_workflow_yaml_roundtrip(client: TestClient) -> None:
    """format=yaml: data.yaml_text round-trips back to the same dict as json projection."""
    import yaml  # safe_load only (S16)

    json_resp = client.get("/workflows/detail_test?format=json")
    yaml_resp = client.get("/workflows/detail_test?format=yaml")
    assert yaml_resp.status_code == 200
    yaml_text = yaml_resp.json()["data"]["yaml_text"]
    assert isinstance(yaml_text, str)
    roundtripped = yaml.safe_load(yaml_text)
    json_data = {k: v for k, v in json_resp.json()["data"].items() if k != "metadata"}
    assert roundtripped == json_data


def test_get_workflow_unknown_returns_404(client: TestClient) -> None:
    """Unknown workflow_id: 404 envelope with redacted message, data=null."""
    response = client.get("/workflows/nonexistent_workflow")
    assert response.status_code == 404
    envelope = response.json()
    assert envelope["code"] == 404
    assert "nonexistent_workflow" in envelope["message"]
    assert envelope["data"] is None


def test_get_workflow_missing_registry_returns_500(tmp_path: Path) -> None:
    """H4/G7: detail endpoint also requires host-injected registry."""
    app = FastAPI()
    app.state.limiter = workflow_api.limiter
    app.add_exception_handler(RateLimitExceeded, _rate_limit_exceeded_handler)
    app.include_router(workflow_api.router)
    with TestClient(app) as test_client:
        response = test_client.get("/workflows/any_id")
    assert response.status_code == 500
    assert "workflow_registry" in response.json()["message"]


def test_api_module_uses_only_safe_yaml() -> None:
    """S16: api.py source uses only yaml.safe_dump, no unsafe yaml.dump or yaml.load."""
    import inspect
    import re

    source = inspect.getsource(workflow_api)
    assert "safe_dump" in source
    unsafe_dump = re.findall(r"yaml\.dump\s*\(", source)
    assert not unsafe_dump, "Found unsafe yaml.dump() calls in api.py"
    unsafe_load = re.findall(r"yaml\.load\s*\(", source)
    assert not unsafe_load, "Found unsafe yaml.load() calls in api.py"


def test_host_processor_chain_contains_redact() -> None:
    """AD-02 v2: host composition root registers redact_processor globally."""
    assert redact_processor in get_structlog_processors()
