"""Unit tests for app.workflow.api (spec-08 TC3, AD-10 / AD-02 v2 host registration).

spec-09 TC1 (H4/G7): the registry is injected via ``app.state.workflow_registry``
by the host composition root; the engine module keeps no module-level cache.
"""

from __future__ import annotations

import re
from collections.abc import Generator
from pathlib import Path
from typing import Any
from unittest.mock import patch

import pytest
from fastapi import FastAPI
from fastapi.testclient import TestClient
from slowapi import _rate_limit_exceeded_handler
from slowapi.errors import RateLimitExceeded
from sqlmodel import Session as DBSession
from sqlmodel import SQLModel, create_engine
from sqlalchemy.pool import StaticPool

from app.api.v1.agent_assets_common import get_db_session
from app.core.config import settings
from app.core.logging import get_structlog_processors
from app.workflow import api as workflow_api
from app.workflow.cli import build_registry
from app.workflow.logging_conf import redact_processor
from app.workflow.models import EdgeDefinition, NodeDefinition, WorkflowDefinition
from app.workflow.nodes.factory import register_node_type
from tests.unit.workflow.test_cli import _ECHO_YAML, _FAIL_YAML, _PROBE_YAML, _FailNode, _MessageProbeNode

pytestmark = [pytest.mark.unit, pytest.mark.usefixtures("isolated_user_workflow_dir")]


def _add_mock_auth(app: FastAPI, username: str = "test_user") -> None:
    """Inject a mock user into the FastAPI app for testing (spec-19: endpoints require auth)."""
    from unittest.mock import MagicMock

    from app.api.v1.auth import get_current_user

    mock_user = MagicMock()
    mock_user.id = 1
    mock_user.username = username
    mock_user.email = f"{username}@example.com"

    async def mock_get_current_user() -> MagicMock:
        return mock_user

    app.dependency_overrides[get_current_user] = mock_get_current_user


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
ui_layout:
  nodes:
    step_a:
      x: 10
      y: 20
    step_b:
      x: 200
      y: 20
"""

_REDACT_DEMO_YAML = """
workflow_id: secret_demo
entry_point: leaky
nodes:
  - name: leaky
    type: echo
    config:
      output:
        api_key: sk-live-leak-999
        result: ok
edges:
  - source: leaky
    target: END
state_schema:
  input:
    type: str
    description: user input
  api_key:
    type: str
    description: leaked key
  result:
    type: str
    description: result
"""

_LONG_VALUE: str = "x" * 600

_LONG_YAML = f"""
workflow_id: long_demo
entry_point: verbose
nodes:
  - name: verbose
    type: echo
    config:
      output:
        blob: "{_LONG_VALUE}"
edges:
  - source: verbose
    target: END
state_schema:
  input:
    type: str
    description: user input
  blob:
    type: str
    description: long output
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
    _add_mock_auth(app, username="admin_user")

    engine = create_engine(
        "sqlite://",
        connect_args={"check_same_thread": False},
        poolclass=StaticPool,
    )
    SQLModel.metadata.create_all(engine)

    def _override_get_db_session() -> Generator[DBSession, None, None]:
        with DBSession(engine) as session:
            yield session

    app.dependency_overrides[get_db_session] = _override_get_db_session

    with TestClient(app) as test_client:
        yield test_client


def test_api_execute_success(client: TestClient) -> None:
    """POST execute returns the host unified envelope {code,message,data} with metadata folded into data."""
    response = client.post("/workflows/echo_demo/execute", json={"input": "hi"})
    assert response.status_code == 200
    envelope = response.json()
    assert envelope["code"] == 200
    assert envelope["message"] == "success"
    assert envelope["data"]["greet_result"]["response"] == "hello"
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
    assert envelope["data"] is not None
    assert "execution_logs" in envelope["data"]["metadata"]


def test_api_missing_registry_injection_returns_500(tmp_path: Path) -> None:
    """G7: without host-injected app.state.workflow_registry the endpoint fails loudly (no implicit cache)."""
    (tmp_path / "echo_demo.yaml").write_text(_ECHO_YAML, encoding="utf-8")
    app = FastAPI()
    app.state.limiter = workflow_api.limiter
    app.add_exception_handler(RateLimitExceeded, _rate_limit_exceeded_handler)
    app.include_router(workflow_api.router)
    _add_mock_auth(app)
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
    _add_mock_auth(app)
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
    _add_mock_auth(app)
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
    _add_mock_auth(app)
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
    _add_mock_auth(app)
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


def test_get_workflow_json_ui_layout_preserved(client: TestClient) -> None:
    """D4: ui_layout annotation is a declared field and survives model_dump projection."""
    response = client.get("/workflows/detail_test")
    data = response.json()["data"]
    assert "ui_layout" in data
    assert data["ui_layout"]["nodes"]["step_a"] == {"x": 10, "y": 20}
    assert data["ui_layout"]["nodes"]["step_b"] == {"x": 200, "y": 20}


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
    _add_mock_auth(app)
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


def test_execute_embeds_execution_logs(client: TestClient) -> None:
    """spec-04: successful execute embeds execution_logs in metadata with all 7 fields."""
    response = client.post("/workflows/echo_demo/execute", json={"input": "hi"})
    assert response.status_code == 200
    metadata = response.json()["data"]["metadata"]
    logs = metadata.get("execution_logs")
    assert logs is not None, "execution_logs must be present in success metadata"
    assert len(logs) == 1
    log_entry = logs[0]
    for field in ("node_name", "node_type", "timestamp", "input_data", "output_data", "execution_time_ms", "error"):
        assert field in log_entry, f"execution log missing required field: {field}"
    assert isinstance(log_entry["timestamp"], str)
    assert log_entry["node_name"] == "greet"


def test_execute_logs_redact_secrets(tmp_path: Path) -> None:
    """H6: execution_logs redact secret-looking keys (api_key -> ***REDACTED***)."""
    (tmp_path / "secret_demo.yaml").write_text(_REDACT_DEMO_YAML, encoding="utf-8")
    app = FastAPI()
    app.state.limiter = workflow_api.limiter
    app.state.workflow_registry = build_registry(tmp_path)
    app.add_exception_handler(RateLimitExceeded, _rate_limit_exceeded_handler)
    app.include_router(workflow_api.router)
    _add_mock_auth(app)
    with TestClient(app) as test_client:
        response = test_client.post("/workflows/secret_demo/execute", json={"input": "hi"})
    assert response.status_code == 200
    logs = response.json()["data"]["metadata"]["execution_logs"]
    assert len(logs) == 1
    output_data = logs[0]["output_data"]
    assert output_data["api_key"] == "***REDACTED***"
    assert "sk-live-leak-999" not in str(logs)
    assert output_data["result"] == "ok"


def test_execute_logs_truncate_long_values(tmp_path: Path) -> None:
    """spec-04: string values > 500 chars in execution_logs are truncated (max_len=500)."""
    (tmp_path / "long_demo.yaml").write_text(_LONG_YAML, encoding="utf-8")
    app = FastAPI()
    app.state.limiter = workflow_api.limiter
    app.state.workflow_registry = build_registry(tmp_path)
    app.add_exception_handler(RateLimitExceeded, _rate_limit_exceeded_handler)
    app.include_router(workflow_api.router)
    _add_mock_auth(app)
    with TestClient(app) as test_client:
        response = test_client.post("/workflows/long_demo/execute", json={"input": "hi"})
    assert response.status_code == 200
    logs = response.json()["data"]["metadata"]["execution_logs"]
    blob = logs[0]["output_data"]["blob"]
    assert len(blob) < len(_LONG_VALUE)
    assert blob.endswith("...(truncated)")


def test_execute_failure_includes_execution_logs(client: TestClient) -> None:
    """Failed execution envelope includes partial traces in data.metadata (Dify-like)."""
    register_node_type("fail_leak", _FailNode)
    app_state = client.app.state
    fail_path = Path(app_state.workflow_directory) / "fail_demo.yaml"
    fail_path.write_text(_FAIL_YAML, encoding="utf-8")
    app_state.workflow_registry = build_registry(app_state.workflow_directory)
    response = client.post("/workflows/fail_demo/execute", json={})
    assert response.status_code == 500
    data = response.json()["data"]
    assert data is not None
    assert "execution_logs" in data["metadata"]
    assert isinstance(data["metadata"]["execution_logs"], list)


def test_execute_persists_run_listable_via_history(client: TestClient) -> None:
    """Successful execution persists a WorkflowRun row retrievable via the list endpoint."""
    response = client.post("/workflows/echo_demo/execute", json={"input": "hi"})
    assert response.status_code == 200
    run_id = response.json()["data"]["metadata"]["run_id"]

    list_response = client.get("/workflows/echo_demo/runs")
    assert list_response.status_code == 200
    page = list_response.json()["data"]
    assert page["total"] == 1
    assert page["items"][0]["run_id"] == run_id
    assert page["items"][0]["status"] == "success"


def test_execute_failure_persists_run(client: TestClient) -> None:
    """Failed execution also persists a WorkflowRun row with status=failed."""
    register_node_type("fail_leak", _FailNode)
    app_state = client.app.state
    fail_path = Path(app_state.workflow_directory) / "fail_demo.yaml"
    fail_path.write_text(_FAIL_YAML, encoding="utf-8")
    app_state.workflow_registry = build_registry(app_state.workflow_directory)

    response = client.post("/workflows/fail_demo/execute", json={})
    assert response.status_code == 500
    run_id = response.json()["data"]["metadata"]["run_id"]

    list_response = client.get("/workflows/fail_demo/runs")
    assert list_response.status_code == 200
    page = list_response.json()["data"]
    assert page["total"] == 1
    assert page["items"][0]["status"] == "failed"
    assert page["items"][0]["run_id"] == run_id


def test_get_workflow_run_detail(client: TestClient) -> None:
    """GET /workflows/{id}/runs/{run_id} returns full detail with execution_logs."""
    response = client.post("/workflows/echo_demo/execute", json={"input": "hi"})
    run_id = response.json()["data"]["metadata"]["run_id"]

    detail_response = client.get(f"/workflows/echo_demo/runs/{run_id}")
    assert detail_response.status_code == 200
    detail = detail_response.json()["data"]
    assert detail["run_id"] == run_id
    assert detail["status"] == "success"
    assert "execution_logs" in detail
    assert "input_data" in detail


def test_get_workflow_run_unknown_returns_404(client: TestClient) -> None:
    """GET /workflows/{id}/runs/{unknown} returns 404."""
    response = client.get("/workflows/echo_demo/runs/nonexistent_run_id")
    assert response.status_code == 404


def test_list_workflow_runs_pagination(client: TestClient) -> None:
    """GET /workflows/{id}/runs supports pagination."""
    for _ in range(3):
        client.post("/workflows/echo_demo/execute", json={"input": "hi"})

    page1 = client.get("/workflows/echo_demo/runs?page=1&pageSize=2").json()["data"]
    assert len(page1["items"]) == 2
    assert page1["total"] == 3

    page2 = client.get("/workflows/echo_demo/runs?page=2&pageSize=2").json()["data"]
    assert len(page2["items"]) == 1


def _install_probe_workflow(client: TestClient) -> None:
    """Register the ``msg_probe`` node and reload the registry with the probe definition."""
    register_node_type("msg_probe", _MessageProbeNode)
    app_state = client.app.state
    probe_path = Path(app_state.workflow_directory) / "probe_demo.yaml"
    probe_path.write_text(_PROBE_YAML, encoding="utf-8")
    app_state.workflow_registry = build_registry(app_state.workflow_directory)


def test_execute_string_input_synthesizes_messages(client: TestClient) -> None:
    """S21: both wire forms reach the node as one synthesized user message (api.py unchanged)."""
    _install_probe_workflow(client)
    expected = [{"role": "user", "content": "hello"}]

    flat = client.post("/workflows/probe_demo/execute", json={"input": "hello"})
    assert flat.status_code == 200
    assert flat.json()["data"]["probe_result"]["seen_messages"] == expected

    nested = client.post("/workflows/probe_demo/execute", json={"input": {"input": "hello"}})
    assert nested.status_code == 200
    assert nested.json()["data"]["probe_result"]["seen_messages"] == expected


def test_serialize_execution_logs_unit() -> None:
    """spec-04 REFACTOR: _serialize_execution_logs produces redacted JSON-ready dicts."""
    from datetime import datetime

    from app.workflow.models import ExecutionLog

    logs = [
        ExecutionLog(
            node_name="n1",
            node_type="echo",
            timestamp=datetime(2026, 1, 1, 12, 0, 0),
            input_data={"token": "abc123"},
            output_data={"result": "ok"},
            execution_time_ms=10.0,
        ),
    ]
    serialized = workflow_api._serialize_execution_logs(logs)
    assert len(serialized) == 1
    assert serialized[0]["node_name"] == "n1"
    assert serialized[0]["input_data"]["token"] == "***REDACTED***"  # noqa: S105 — dict key, not a secret
    assert serialized[0]["timestamp"] == "2026-01-01T12:00:00"


# -- spec-16: PUT /workflows/{workflow_id} ------------------------------------

_SAVE_PAYLOAD: dict[str, Any] = {
    "workflow_id": "save_test",
    "entry_point": "classify",
    "nodes": [
        {"name": "classify", "type": "llm", "config": {}},
        {"name": "fetch", "type": "http", "config": {"url": "https://example.com", "method": "GET"}},
    ],
    "edges": [
        {"source": "classify", "target": "fetch"},
        {"source": "fetch", "target": "END"},
    ],
    "state_schema": {
        "input": {"type": "str", "description": "user input"},
    },
}


@pytest.fixture()
def save_client(tmp_path: Path) -> Generator[TestClient, None, None]:
    """FastAPI app with an empty registry for PUT handler tests."""
    app = FastAPI()
    app.state.limiter = workflow_api.limiter
    app.state.workflow_registry = build_registry(tmp_path)
    app.add_exception_handler(RateLimitExceeded, _rate_limit_exceeded_handler)
    app.include_router(workflow_api.router)
    _add_mock_auth(app, username="admin_user")
    with TestClient(app) as test_client:
        yield test_client


def test_save_workflow_happy_path(save_client: TestClient) -> None:
    """Valid definition → 200, has_workflow True, _definition_view returned."""
    from unittest.mock import patch

    from app.core.config import settings

    with (
        patch("app.workflow.api.save_definition_yaml") as mock_save,
        patch.object(settings, "WORKFLOW_ADMIN_USERNAMES", ["admin_user"]),
    ):
        mock_save.return_value = Path("/fake/save_test.yaml")
        response = save_client.put("/workflows/save_test", json=_SAVE_PAYLOAD)
    assert response.status_code == 200
    envelope = response.json()
    assert envelope["code"] == 200
    assert envelope["message"] == "success"
    data = envelope["data"]
    assert data["workflow_id"] == "save_test"
    assert data["entry_point"] == "classify"
    assert len(data["nodes"]) == 2
    assert "execution_history" not in data
    registry = save_client.app.state.workflow_registry
    assert registry.has_workflow("save_test")


def test_save_workflow_atomic_replace(save_client: TestClient) -> None:
    """S13: re-PUT with same id replaces; node count does not double."""
    from unittest.mock import patch

    from app.core.config import settings

    with (
        patch("app.workflow.api.save_definition_yaml") as mock_save,
        patch.object(settings, "WORKFLOW_ADMIN_USERNAMES", ["admin_user"]),
    ):
        mock_save.return_value = Path("/fake/save_test.yaml")
        save_client.put("/workflows/save_test", json=_SAVE_PAYLOAD)
        replacement = {
            "workflow_id": "save_test",
            "entry_point": "only_llm",
            "nodes": [{"name": "only_llm", "type": "llm", "config": {}}],
            "edges": [{"source": "only_llm", "target": "END"}],
            "state_schema": {"input": {"type": "str", "description": "x"}},
        }
        save_client.put("/workflows/save_test", json=replacement)
    registry = save_client.app.state.workflow_registry
    stats = registry.get_registry_stats()
    assert stats["workflow_count"] == 1
    definition = registry.get_workflow_definition("save_test")
    assert definition is not None
    assert len(definition.nodes) == 1
    assert definition.entry_point == "only_llm"


def test_save_workflow_missing_entry_point_returns_422(save_client: TestClient) -> None:
    """Payload missing entry_point → 422, no registry side-effect."""
    from unittest.mock import patch

    bad_payload = {**_SAVE_PAYLOAD, "workflow_id": "bad_ep"}
    bad_payload = {k: v for k, v in bad_payload.items() if k != "entry_point"}
    from app.core.config import settings

    with (
        patch("app.workflow.api.save_definition_yaml") as mock_save,
        patch.object(settings, "WORKFLOW_ADMIN_USERNAMES", ["admin_user"]),
    ):
        response = save_client.put("/workflows/bad_ep", json=bad_payload)
    assert response.status_code == 422
    registry = save_client.app.state.workflow_registry
    assert not registry.has_workflow("bad_ep")
    mock_save.assert_not_called()


def test_save_workflow_empty_nodes_returns_422(save_client: TestClient) -> None:
    """Empty nodes list → 422, no registry side-effect."""
    from unittest.mock import patch

    bad_payload = {**_SAVE_PAYLOAD, "workflow_id": "no_nodes", "nodes": []}
    from app.core.config import settings

    with (
        patch("app.workflow.api.save_definition_yaml") as mock_save,
        patch.object(settings, "WORKFLOW_ADMIN_USERNAMES", ["admin_user"]),
    ):
        response = save_client.put("/workflows/no_nodes", json=bad_payload)
    assert response.status_code == 422
    registry = save_client.app.state.workflow_registry
    assert not registry.has_workflow("no_nodes")
    mock_save.assert_not_called()


def test_save_workflow_id_mismatch_returns_422(save_client: TestClient) -> None:
    """body.workflow_id != path id → 422."""
    from unittest.mock import patch

    mismatched = {**_SAVE_PAYLOAD, "workflow_id": "other_id"}
    from app.core.config import settings

    with (
        patch("app.workflow.api.save_definition_yaml") as mock_save,
        patch.object(settings, "WORKFLOW_ADMIN_USERNAMES", ["admin_user"]),
    ):
        response = save_client.put("/workflows/save_test", json=mismatched)
    assert response.status_code == 422
    registry = save_client.app.state.workflow_registry
    assert not registry.has_workflow("other_id")
    mock_save.assert_not_called()


def _python_payload(config: dict[str, Any], workflow_id: str = "py_test") -> dict[str, Any]:
    """A minimal single-node definition carrying one ``python`` node."""
    return {
        "workflow_id": workflow_id,
        "entry_point": "transform",
        "nodes": [{"name": "transform", "type": "python", "config": config}],
        "edges": [{"source": "transform", "target": "END"}],
        "state_schema": {"input": {"type": "str", "description": "x"}},
    }


def test_save_workflow_unknown_node_type_rejected(save_client: TestClient) -> None:
    """S18: a type outside {llm, http, python} is still refused."""
    from unittest.mock import patch

    from app.core.config import settings

    payload = {**_SAVE_PAYLOAD, "workflow_id": "unknown_type"}
    payload["nodes"] = [{"name": "classify", "type": "shell", "config": {}}]
    with (
        patch("app.workflow.api.save_definition_yaml") as mock_save,
        patch.object(settings, "WORKFLOW_ADMIN_USERNAMES", ["admin_user"]),
    ):
        response = save_client.put("/workflows/unknown_type", json=payload)
    assert response.status_code == 422
    assert "shell" in response.json()["message"]
    assert not save_client.app.state.workflow_registry.has_workflow("unknown_type")
    mock_save.assert_not_called()


def test_save_workflow_python_code_node_accepted(save_client: TestClient) -> None:
    """S18: code-only python is allowed, and the server forces sandboxed=true."""
    payload = _python_payload({"code": 'return {"upper": state["input"].upper()}'})
    with (
        patch("app.workflow.api.save_definition_yaml") as mock_save,
        patch.object(settings, "WORKFLOW_ADMIN_USERNAMES", ["admin_user"]),
    ):
        mock_save.return_value = Path("/fake/py_test.yaml")
        response = save_client.put("/workflows/py_test", json=payload)

    assert response.status_code == 200
    config = response.json()["data"]["nodes"][0]["config"]
    assert config["sandboxed"] is True
    assert save_client.app.state.workflow_registry.has_workflow("py_test")
    persisted = mock_save.call_args[0][0]
    assert persisted.nodes[0].config["sandboxed"] is True


def test_save_workflow_python_entry_node_rejected(save_client: TestClient) -> None:
    """S18 condition 1: entry mode can load arbitrary repo modules, so it cannot be sandboxed."""
    payload = _python_payload({"entry": "app.utils:helper"}, workflow_id="entry_test")
    with (
        patch("app.workflow.api.save_definition_yaml") as mock_save,
        patch.object(settings, "WORKFLOW_ADMIN_USERNAMES", ["admin_user"]),
    ):
        response = save_client.put("/workflows/entry_test", json=payload)
    assert response.status_code == 422
    assert "entry" in response.json()["message"]
    assert not save_client.app.state.workflow_registry.has_workflow("entry_test")
    mock_save.assert_not_called()


@pytest.mark.parametrize("config", [{}, {"code": ""}, {"code": "   \n"}])
def test_save_workflow_python_unusable_code_rejected(save_client: TestClient, config: dict[str, Any]) -> None:
    """S18: no usable code is rejected before the AST check, so a blank node can never be registered.

    Reachable in practice: the designer's default python config is an empty code
    string, and the frontend pre-check is not the security boundary.
    """
    payload = _python_payload(config, workflow_id="blank_code")
    with (
        patch("app.workflow.api.save_definition_yaml") as mock_save,
        patch.object(settings, "WORKFLOW_ADMIN_USERNAMES", ["admin_user"]),
    ):
        response = save_client.put("/workflows/blank_code", json=payload)
    assert response.status_code == 422
    assert "code" in response.json()["message"]
    assert not save_client.app.state.workflow_registry.has_workflow("blank_code")
    mock_save.assert_not_called()


def test_save_workflow_python_illegal_code_rejected(save_client: TestClient) -> None:
    """S18 condition 2: AST pre-check rejects, naming the rule but never the code (H6)."""
    payload = _python_payload({"code": 'secret_marker = "hunter2"\nimport os\nreturn {}'}, workflow_id="ast_test")
    with (
        patch("app.workflow.api.save_definition_yaml") as mock_save,
        patch.object(settings, "WORKFLOW_ADMIN_USERNAMES", ["admin_user"]),
    ):
        response = save_client.put("/workflows/ast_test", json=payload)
    assert response.status_code == 422
    message = response.json()["message"]
    assert "no-import" in message
    assert "hunter2" not in message
    assert "secret_marker" not in message
    assert not save_client.app.state.workflow_registry.has_workflow("ast_test")
    mock_save.assert_not_called()


def test_save_workflow_python_sandboxed_flag_is_forced(save_client: TestClient) -> None:
    """S18 condition 3: sandboxed is a security property, so the client value is overwritten."""
    payload = _python_payload({"code": "return {}", "sandboxed": False}, workflow_id="forced_test")
    with (
        patch("app.workflow.api.save_definition_yaml") as mock_save,
        patch.object(settings, "WORKFLOW_ADMIN_USERNAMES", ["admin_user"]),
    ):
        mock_save.return_value = Path("/fake/forced_test.yaml")
        response = save_client.put("/workflows/forced_test", json=payload)

    assert response.status_code == 200
    assert response.json()["data"]["nodes"][0]["config"]["sandboxed"] is True
    assert mock_save.call_args[0][0].nodes[0].config["sandboxed"] is True


def _subworkflow_payload(config: dict[str, Any], workflow_id: str = "wf_outer") -> dict[str, Any]:
    """A minimal single-node definition carrying one ``subworkflow`` node."""
    return {
        "workflow_id": workflow_id,
        "entry_point": "call_inner",
        "nodes": [{"name": "call_inner", "type": "subworkflow", "config": config}],
        "edges": [{"source": "call_inner", "target": "END"}],
        "state_schema": {"input": {"type": "str", "description": "x"}},
    }


def test_save_workflow_subworkflow_node_accepted(save_client: TestClient) -> None:
    """S18 second revision: subworkflow is whitelisted, and its config passes through untouched."""
    payload = _subworkflow_payload({"workflow_id": "wf_inner", "inherit_input": True})
    with (
        patch("app.workflow.api.save_definition_yaml") as mock_save,
        patch.object(settings, "WORKFLOW_ADMIN_USERNAMES", ["admin_user"]),
    ):
        mock_save.return_value = Path("/fake/wf_outer.yaml")
        response = save_client.put("/workflows/wf_outer", json=payload)

    assert response.status_code == 200
    config = response.json()["data"]["nodes"][0]["config"]
    assert config == {"workflow_id": "wf_inner", "inherit_input": True}
    assert save_client.app.state.workflow_registry.has_workflow("wf_outer")


def test_save_workflow_subworkflow_dangling_reference_accepted(save_client: TestClient) -> None:
    """S18: referential existence is a *runtime* check, so saving the outer first must work.

    A registration-time existence check would force topological save order and turn
    ordering into an implicit contract; the run reports WorkflowNotFoundError instead.
    """
    payload = _subworkflow_payload({"workflow_id": "wf_not_registered_yet"}, workflow_id="wf_dangling")
    with (
        patch("app.workflow.api.save_definition_yaml") as mock_save,
        patch.object(settings, "WORKFLOW_ADMIN_USERNAMES", ["admin_user"]),
    ):
        mock_save.return_value = Path("/fake/wf_dangling.yaml")
        response = save_client.put("/workflows/wf_dangling", json=payload)

    assert response.status_code == 200
    assert save_client.app.state.workflow_registry.has_workflow("wf_dangling")


@pytest.mark.parametrize("config", [{}, {"workflow_id": ""}, {"workflow_id": "   "}, {"workflow_id": 123}])
def test_save_workflow_subworkflow_bad_id_rejected(save_client: TestClient, config: dict[str, Any]) -> None:
    """S18 structural validation: workflow_id must be a non-empty string, else 422."""
    payload = _subworkflow_payload(config, workflow_id="wf_bad")
    with (
        patch("app.workflow.api.save_definition_yaml") as mock_save,
        patch.object(settings, "WORKFLOW_ADMIN_USERNAMES", ["admin_user"]),
    ):
        response = save_client.put("/workflows/wf_bad", json=payload)

    assert response.status_code == 422
    assert "workflow_id" in response.json()["message"]
    assert not save_client.app.state.workflow_registry.has_workflow("wf_bad")
    mock_save.assert_not_called()


def test_save_workflow_dangling_edge_returns_422(save_client: TestClient) -> None:
    """S6: dangling edge (target not in nodes, not END) → 422."""
    from unittest.mock import patch

    dangling = {
        "workflow_id": "dangling",
        "entry_point": "step_a",
        "nodes": [{"name": "step_a", "type": "llm", "config": {}}],
        "edges": [{"source": "step_a", "target": "nonexistent"}],
        "state_schema": {"input": {"type": "str", "description": "x"}},
    }
    from app.core.config import settings

    with (
        patch("app.workflow.api.save_definition_yaml") as mock_save,
        patch.object(settings, "WORKFLOW_ADMIN_USERNAMES", ["admin_user"]),
    ):
        response = save_client.put("/workflows/dangling", json=dangling)
    assert response.status_code == 422
    registry = save_client.app.state.workflow_registry
    assert not registry.has_workflow("dangling")
    mock_save.assert_not_called()


def test_save_workflow_entry_point_not_in_nodes_returns_422(save_client: TestClient) -> None:
    """S6: entry_point not in nodes → 422."""
    from unittest.mock import patch

    bad_entry = {
        "workflow_id": "bad_entry",
        "entry_point": "ghost",
        "nodes": [{"name": "step_a", "type": "llm", "config": {}}],
        "edges": [{"source": "step_a", "target": "END"}],
        "state_schema": {"input": {"type": "str", "description": "x"}},
    }
    from app.core.config import settings

    with (
        patch("app.workflow.api.save_definition_yaml") as mock_save,
        patch.object(settings, "WORKFLOW_ADMIN_USERNAMES", ["admin_user"]),
    ):
        response = save_client.put("/workflows/bad_entry", json=bad_entry)
    assert response.status_code == 422
    registry = save_client.app.state.workflow_registry
    assert not registry.has_workflow("bad_entry")
    mock_save.assert_not_called()


def test_save_workflow_calls_save_definition_yaml(save_client: TestClient) -> None:
    """After successful registration, save_definition_yaml is called (spec-17 integration)."""
    from unittest.mock import patch

    from app.core.config import settings

    with (
        patch("app.workflow.api.save_definition_yaml") as mock_save,
        patch.object(settings, "WORKFLOW_ADMIN_USERNAMES", ["admin_user"]),
    ):
        mock_save.return_value = Path("/fake/save_test.yaml")
        save_client.put("/workflows/save_test", json=_SAVE_PAYLOAD)
    mock_save.assert_called_once()
    call_arg = mock_save.call_args[0][0]
    assert call_arg.workflow_id == "save_test"


def test_save_workflow_persist_failure_rolls_back(save_client: TestClient) -> None:
    """save_definition_yaml raises → 500, registry rolled back (has_workflow False)."""
    from unittest.mock import patch

    from app.core.config import settings

    with (
        patch("app.workflow.api.save_definition_yaml", side_effect=OSError("disk full")),
        patch.object(settings, "WORKFLOW_ADMIN_USERNAMES", ["admin_user"]),
    ):
        response = save_client.put("/workflows/save_test", json=_SAVE_PAYLOAD)
    assert response.status_code == 500
    registry = save_client.app.state.workflow_registry
    assert not registry.has_workflow("save_test")


def test_delete_workflow_happy_path(client: TestClient) -> None:
    """DELETE registered workflow → 200, data=null, registry.has_workflow False (C6/H7 four-table sync)."""
    from unittest.mock import patch

    from app.core.config import settings

    with patch.object(settings, "WORKFLOW_ADMIN_USERNAMES", ["admin_user"]):
        response = client.delete("/workflows/echo_demo")
    assert response.status_code == 200
    envelope = response.json()
    assert envelope["code"] == 200
    assert envelope["message"] == "success"
    assert envelope["data"]["result"] is None
    registry = client.app.state.workflow_registry
    assert not registry.has_workflow("echo_demo")


def test_delete_workflow_calls_delete_definition_yaml(client: TestClient) -> None:
    """After successful DELETE, delete_definition_yaml is called (spec-17 integration)."""
    from unittest.mock import patch

    from app.core.config import settings

    with (
        patch("app.workflow.api.delete_definition_yaml") as mock_delete,
        patch.object(settings, "WORKFLOW_ADMIN_USERNAMES", ["admin_user"]),
    ):
        mock_delete.return_value = True
        client.delete("/workflows/echo_demo")
    mock_delete.assert_called_once()
    assert mock_delete.call_args.args == ("echo_demo",)


def test_delete_workflow_unknown_id_returns_404(client: TestClient) -> None:
    """Unknown workflow_id → 404 + envelope; registry and disk unchanged."""
    from unittest.mock import patch

    from app.core.config import settings

    with (
        patch("app.workflow.api.delete_definition_yaml") as mock_delete,
        patch.object(settings, "WORKFLOW_ADMIN_USERNAMES", ["admin_user"]),
    ):
        response = client.delete("/workflows/nonexistent")
    assert response.status_code == 404
    envelope = response.json()
    assert envelope["code"] == 404
    assert "nonexistent" in envelope["message"]
    assert envelope["data"] is None
    mock_delete.assert_not_called()
    registry = client.app.state.workflow_registry
    assert registry.has_workflow("echo_demo")


def test_delete_workflow_idempotent_second_delete_returns_404(client: TestClient) -> None:
    """Second DELETE on same id → 404 (idempotency boundary)."""
    from unittest.mock import patch

    from app.core.config import settings

    with patch.object(settings, "WORKFLOW_ADMIN_USERNAMES", ["admin_user"]):
        first = client.delete("/workflows/echo_demo")
        assert first.status_code == 200
        second = client.delete("/workflows/echo_demo")
        assert second.status_code == 404
        envelope = second.json()
    assert envelope["code"] == 404
    assert envelope["data"] is None


def test_delete_workflow_registry_stats_decrement(client: TestClient) -> None:
    """get_registry_stats workflow_count decrements by 1 after DELETE."""
    from unittest.mock import patch

    from app.core.config import settings

    registry = client.app.state.workflow_registry
    stats_before = registry.get_registry_stats()
    count_before = stats_before["workflow_count"]
    with patch.object(settings, "WORKFLOW_ADMIN_USERNAMES", ["admin_user"]):
        client.delete("/workflows/echo_demo")
    stats_after = registry.get_registry_stats()
    assert stats_after["workflow_count"] == count_before - 1


# ---------------------------------------------------------------------------
# S20: provider_ref registration-time validation (S6 build-time first)
# ---------------------------------------------------------------------------


def _payload_with_provider_ref(ref: str | None) -> dict[str, Any]:
    """Clone the save payload and set provider_ref on its llm node."""
    payload = dict(_SAVE_PAYLOAD)
    config: dict[str, Any] = {"model_name": "gpt-4o-mini"}
    if ref is not None:
        config["provider_ref"] = ref
    payload["nodes"] = [
        {"name": "classify", "type": "llm", "config": config},
        {"name": "fetch", "type": "http", "config": {"url": "https://example.com", "method": "GET"}},
    ]
    return payload


def test_save_workflow_rejects_unresolvable_provider_ref(save_client: TestClient) -> None:
    """A dangling provider_ref is rejected at registration with 422, not at execution (S6/S20)."""

    def fake_validate(reference: str) -> None:
        msg = f"model config '{reference}' not found or disabled. available models: acme/gpt-4o"
        raise ValueError(msg)

    with (
        patch("app.workflow.api.validate_reference", side_effect=fake_validate) as mock_validate,
        patch("app.workflow.api.save_definition_yaml") as mock_save,
        patch.object(settings, "WORKFLOW_ADMIN_USERNAMES", ["admin_user"]),
    ):
        response = save_client.put("/workflows/save_test", json=_payload_with_provider_ref("acme/missing"))

    assert response.status_code == 422
    envelope = response.json()
    assert envelope["code"] == 422
    assert "provider_ref invalid" in envelope["message"]
    assert "classify" in envelope["message"]
    assert "acme/missing" in envelope["message"]
    mock_validate.assert_called_once_with("acme/missing")
    # Neither registration nor persistence happened
    mock_save.assert_not_called()
    assert save_client.get("/workflows/save_test").status_code == 404


def test_save_workflow_accepts_resolvable_provider_ref(save_client: TestClient) -> None:
    """A valid provider_ref passes validation and the definition round-trips it (S20)."""
    with (
        patch("app.workflow.api.validate_reference") as mock_validate,
        patch("app.workflow.api.save_definition_yaml") as mock_save,
        patch.object(settings, "WORKFLOW_ADMIN_USERNAMES", ["admin_user"]),
    ):
        mock_save.return_value = Path("/fake/save_test.yaml")
        response = save_client.put("/workflows/save_test", json=_payload_with_provider_ref("acme/gpt-4o"))

    assert response.json()["code"] == 200
    mock_validate.assert_called_once_with("acme/gpt-4o")
    mock_save.assert_called_once()

    stored = save_client.get("/workflows/save_test").json()["data"]
    llm_node = next(n for n in stored["nodes"] if n["name"] == "classify")
    assert llm_node["config"]["provider_ref"] == "acme/gpt-4o"


def test_save_workflow_without_provider_ref_skips_validation(save_client: TestClient) -> None:
    """No provider_ref -> the facade is never touched, so env-only workflows need no DB (S20)."""
    with (
        patch("app.workflow.api.validate_reference") as mock_validate,
        patch("app.workflow.api.save_definition_yaml") as mock_save,
        patch.object(settings, "WORKFLOW_ADMIN_USERNAMES", ["admin_user"]),
    ):
        mock_save.return_value = Path("/fake/save_test.yaml")
        response = save_client.put("/workflows/save_test", json=_payload_with_provider_ref(None))

    assert response.json()["code"] == 200
    mock_validate.assert_not_called()


def test_validate_provider_refs_only_checks_llm_nodes() -> None:
    """The type filter skips non-llm nodes.

    Asserted against the helper directly: NodeDefinition.config is a loose dict at
    this layer, whereas routing an http node carrying provider_ref through PUT would
    422 for an unrelated reason (HTTPNodeConfig is extra="forbid", S14).
    """
    definition = WorkflowDefinition(
        workflow_id="wf_filter",
        entry_point="ask",
        nodes=[
            NodeDefinition(name="fetch", type="http", config={"url": "https://x.test", "provider_ref": "acme/nope"}),
            NodeDefinition(name="ask", type="llm", config={"provider_ref": "acme/gpt-4o"}),
        ],
        edges=[EdgeDefinition(source="fetch", target="ask"), EdgeDefinition(source="ask", target="END")],
        state_schema={},
    )

    with patch("app.workflow.api.validate_reference") as mock_validate:
        workflow_api._validate_provider_refs(definition)  # noqa: SLF001

    mock_validate.assert_called_once_with("acme/gpt-4o")


def test_api_module_never_touches_the_store_layer() -> None:
    """Layering guard: api.py uses the provider service facade, not llm_store or database_service."""
    source = Path(workflow_api.__file__).read_text(encoding="utf-8")
    assert "provider_service" in source
    assert not re.search(r"^\s*(?:from|import)\s+app\.services\.llm\.llm_store\b", source, re.MULTILINE)
    assert not re.search(r"^\s*(?:from|import)\s+app\.services\.database\b", source, re.MULTILINE)
