"""Unit tests for app.workflow.api (spec-08 TC3, AD-10 / AD-02 v2 host registration)."""

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
from app.workflow.logging_conf import redact_processor
from app.workflow.nodes.factory import register_node_type
from tests.unit.workflow.test_cli import _ECHO_YAML, _FAIL_YAML, _FailNode

pytestmark = pytest.mark.unit


@pytest.fixture()
def client(tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> Generator[TestClient, None, None]:
    """Minimal FastAPI app hosting the workflow router against a tmp YAML dir."""
    (tmp_path / "echo_demo.yaml").write_text(_ECHO_YAML, encoding="utf-8")
    monkeypatch.setattr(workflow_api, "_registry_directory", tmp_path)
    monkeypatch.setattr(workflow_api, "_registry_cache", {})
    app = FastAPI()
    app.state.limiter = workflow_api.limiter
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
    fail_path = Path(workflow_api._registry_directory) / "fail_demo.yaml"  # noqa: SLF001 — fixture-scoped attr
    fail_path.write_text(_FAIL_YAML, encoding="utf-8")
    workflow_api._registry_cache.clear()  # noqa: SLF001 — force rebuild with the new definition
    response = client.post("/workflows/fail_demo/execute", json={})
    assert response.status_code == 500
    assert "sk-live-leak-999" not in response.text
    envelope = response.json()
    assert envelope["success"] is False
    assert envelope["data"] is None


def test_host_processor_chain_contains_redact() -> None:
    """AD-02 v2: host composition root registers redact_processor globally."""
    assert redact_processor in get_structlog_processors()
