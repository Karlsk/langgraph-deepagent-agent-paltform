"""Integration tests for the spec-16 save pipeline (PUT → register → YAML persist).

Uses real ``save_definition_yaml`` with ``tmp_path`` monkeypatched as the user
workflow directory; no mocks for the persistence layer.
"""

from __future__ import annotations

from pathlib import Path
from typing import Any
from unittest.mock import patch

import yaml
from fastapi import FastAPI
from fastapi.testclient import TestClient
from slowapi import _rate_limit_exceeded_handler
from slowapi.errors import RateLimitExceeded

import pytest

from app.workflow import api as workflow_api
from app.workflow.cli import build_registry

pytestmark = pytest.mark.integration

_SAVE_PAYLOAD: dict[str, Any] = {
    "workflow_id": "integ_save",
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
def integ_client(tmp_path: Path) -> TestClient:
    """FastAPI app with real save_definition_yaml (user dir = tmp_path)."""
    app = FastAPI()
    app.state.limiter = workflow_api.limiter
    app.state.workflow_registry = build_registry(tmp_path)
    app.add_exception_handler(RateLimitExceeded, _rate_limit_exceeded_handler)
    app.include_router(workflow_api.router)
    return TestClient(app)


def test_save_pipeline_end_to_end(tmp_path: Path, integ_client: TestClient) -> None:
    """PUT → 200 → YAML on disk matches → registry has_workflow True."""
    with patch("app.workflow.store.user_workflow_dir", return_value=tmp_path):
        response = integ_client.put("/workflows/integ_save", json=_SAVE_PAYLOAD)
    assert response.status_code == 200
    envelope = response.json()
    assert envelope["code"] == 200
    assert envelope["data"]["workflow_id"] == "integ_save"

    yaml_path = tmp_path / "integ_save.yaml"
    assert yaml_path.exists()
    on_disk = yaml.safe_load(yaml_path.read_text(encoding="utf-8"))
    assert on_disk["workflow_id"] == "integ_save"
    assert len(on_disk["nodes"]) == 2
    assert "execution_history" not in on_disk

    registry = integ_client.app.state.workflow_registry
    assert registry.has_workflow("integ_save")


def test_save_pipeline_replace(tmp_path: Path, integ_client: TestClient) -> None:
    """S13: two PUTs with same id → YAML reflects second, registry count = 1."""
    with patch("app.workflow.store.user_workflow_dir", return_value=tmp_path):
        integ_client.put("/workflows/integ_save", json=_SAVE_PAYLOAD)
        replacement = {
            "workflow_id": "integ_save",
            "entry_point": "solo",
            "nodes": [{"name": "solo", "type": "llm", "config": {}}],
            "edges": [{"source": "solo", "target": "END"}],
            "state_schema": {"input": {"type": "str", "description": "x"}},
        }
        integ_client.put("/workflows/integ_save", json=replacement)

    yaml_path = tmp_path / "integ_save.yaml"
    on_disk = yaml.safe_load(yaml_path.read_text(encoding="utf-8"))
    assert on_disk["entry_point"] == "solo"
    assert len(on_disk["nodes"]) == 1

    registry = integ_client.app.state.workflow_registry
    assert registry.get_registry_stats()["workflow_count"] == 1


def test_save_pipeline_disk_failure_rollback(tmp_path: Path, integ_client: TestClient) -> None:
    """Unwritable user dir → 500, registry rolled back (has_workflow False)."""
    blocker = tmp_path / "blocker"
    blocker.write_text("not a directory", encoding="utf-8")
    unwritable = blocker / "nested"
    with patch("app.workflow.store.user_workflow_dir", return_value=unwritable):
        response = integ_client.put("/workflows/integ_save", json=_SAVE_PAYLOAD)
    assert response.status_code == 500
    registry = integ_client.app.state.workflow_registry
    assert not registry.has_workflow("integ_save")
