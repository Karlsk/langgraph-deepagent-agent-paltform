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


@pytest.fixture()
def client(tmp_path: Path) -> Generator[TestClient, None, None]:
    """Minimal FastAPI app hosting the workflow router with an app.state-injected registry."""
    (tmp_path / "echo_demo.yaml").write_text(_ECHO_YAML, encoding="utf-8")
    app = FastAPI()
    app.state.limiter = workflow_api.limiter
    app.state.workflow_registry = build_registry(tmp_path)
    app.state.workflow_directory = tmp_path
    app.add_exception_handler(RateLimitExceeded, _rate_limit_exceeded_handler)
    app.include_router(workflow_api.router)
    with TestClient(app) as test_client:
        yield test_client


def test_api_execute_success(client: TestClient) -> None:
    """POST execute returns the shared envelope with full metadata."""
    response = client.post("/workflows/echo_demo/execute", json={"input": "hi"})
    assert response.status_code == 200
    envelope = response.json()
    assert envelope["success"] is True
    assert envelope["error"] is None
    assert envelope["data"]["response"] == "hello"
    metadata = envelope["metadata"]
    assert metadata["workflow_id"] == "echo_demo"
    assert len(metadata["run_id"]) == 32
    assert metadata["duration_ms"] >= 0.0
    assert metadata["node_count"] == 1


def test_api_unknown_workflow_returns_404(client: TestClient) -> None:
    """Unknown workflow_id: 404 with failure envelope mentioning the id."""
    response = client.post("/workflows/not_exist/execute", json={})
    assert response.status_code == 404
    envelope = response.json()
    assert envelope["success"] is False
    assert "not_exist" in envelope["error"]


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
    assert envelope["success"] is False
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
    assert "workflow_registry" in response.json()["error"]


def test_host_processor_chain_contains_redact() -> None:
    """AD-02 v2: host composition root registers redact_processor globally."""
    assert redact_processor in get_structlog_processors()
