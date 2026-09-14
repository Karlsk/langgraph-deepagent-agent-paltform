"""Integration tests for the S18/S22 python-node pipeline: PUT → persist → execute.

The unit suites stub ``run_sandboxed`` (no child is spawned) and
``subprocess.run`` (no worker is started), so neither can observe the seam that
actually carries the security claim: whether the ``sandboxed=true`` the server
forces at registration survives the YAML round trip and still routes the node
into a real restricted child at execution time. These tests write real YAML
under ``tmp_path`` and spawn real child processes. Zero network / zero LLM.
"""

from __future__ import annotations

from pathlib import Path
from typing import Any
from unittest.mock import MagicMock, patch

import httpx
import pytest
import yaml
from fastapi import FastAPI
from fastapi.testclient import TestClient
from slowapi import _rate_limit_exceeded_handler
from slowapi.errors import RateLimitExceeded

from app.workflow import api as workflow_api
from app.workflow.auth import require_workflow_admin
from app.workflow.cli import build_registry
from app.workflow.models import PythonNodeError
from app.workflow.registry import WorkflowRegistry

pytestmark = [pytest.mark.integration, pytest.mark.usefixtures("isolated_user_workflow_dir")]

UPPER_CODE = 'return {"upper": state["input"].upper()}'


def _python_payload(code: str, *, workflow_id: str = "integ_python", **config_extra: Any) -> dict[str, Any]:
    """A one-node python workflow whose output field is declared (undeclared keys are not writable, EXP-G8)."""
    return {
        "workflow_id": workflow_id,
        "entry_point": "transform",
        "nodes": [{"name": "transform", "type": "python", "config": {"code": code, **config_extra}}],
        "edges": [{"source": "transform", "target": "END"}],
        "state_schema": {
            "input": {"type": "str", "description": "user input"},
            "upper": {"type": "str", "description": "uppercased input"},
        },
    }


@pytest.fixture()
def integ_client(tmp_path: Path) -> TestClient:
    """FastAPI app with real YAML persistence (user dir = tmp_path) and a stubbed admin gate.

    PUT is admin-gated (S19) and ``test_api_auth`` covers that gate, so it is
    stubbed here to keep these cards focused on the sandbox pipeline.
    """
    app = FastAPI()
    app.state.limiter = workflow_api.limiter
    app.state.workflow_registry = build_registry(tmp_path)
    app.add_exception_handler(RateLimitExceeded, _rate_limit_exceeded_handler)
    app.include_router(workflow_api.router)
    admin = MagicMock()
    admin.username = "integ_admin"

    async def stub_admin() -> MagicMock:
        return admin

    app.dependency_overrides[require_workflow_admin] = stub_admin
    return TestClient(app)


def _save(client: TestClient, payload: dict[str, Any], tmp_path: Path) -> httpx.Response:
    """PUT the payload with the real persistence layer pointed at tmp_path."""
    with patch("app.workflow.store.user_workflow_dir", return_value=tmp_path):
        return client.put(f"/workflows/{payload['workflow_id']}", json=payload)


def _registry(client: TestClient) -> WorkflowRegistry:
    registry: WorkflowRegistry = client.app.state.workflow_registry
    return registry


def test_client_sandboxed_false_is_overwritten_on_disk(tmp_path: Path, integ_client: TestClient) -> None:
    """S18 condition 3 end to end: the persisted YAML and the registry both carry sandboxed=true."""
    response = _save(integ_client, _python_payload(UPPER_CODE, sandboxed=False), tmp_path)
    assert response.status_code == 200

    on_disk = yaml.safe_load((tmp_path / "integ_python.yaml").read_text(encoding="utf-8"))
    assert on_disk["nodes"][0]["config"]["sandboxed"] is True

    definition = _registry(integ_client).get_workflow_definition("integ_python")
    assert definition is not None
    assert definition.nodes[0].config["sandboxed"] is True


def test_saved_node_executes_in_the_restricted_child(tmp_path: Path, integ_client: TestClient) -> None:
    """A builtin the host has but SAFE_BUILTINS lacks proves the code ran in the child, not in-process.

    ``hash`` passes every AST rule yet is absent from the sandbox whitelist, so
    the in-process path would return a value while the sandboxed path raises
    NameError from the worker. Deterministic and fast, unlike a timeout probe.
    """
    response = _save(integ_client, _python_payload('return {"h": hash("x")}'), tmp_path)
    assert response.status_code == 200

    with pytest.raises(PythonNodeError, match="NameError"):
        _registry(integ_client).execute_workflow("integ_python", {"input": "abc"})


def test_saved_node_output_and_log_summary(tmp_path: Path, integ_client: TestClient) -> None:
    """The forced flag reaches execution: real child output plus a summary-only log (H6/S15)."""
    response = _save(integ_client, _python_payload(UPPER_CODE), tmp_path)
    assert response.status_code == 200

    result = _registry(integ_client).execute_workflow("integ_python", {"input": "abc"})
    assert result.output["upper"] == "ABC"
    assert result.output["transform_result"] == {"upper": "ABC"}

    assert len(result.execution_logs) == 1
    summary = result.execution_logs[0].input_data
    assert summary == {"mode": "code", "code_chars": len(UPPER_CODE), "sandboxed": True}
    assert UPPER_CODE not in str(summary)


def test_illegal_code_is_rejected_before_persisting(tmp_path: Path, integ_client: TestClient) -> None:
    """S18 condition 2: AST rejection → 422 naming the rule, with no YAML and no registry entry (H6)."""
    code = 'secret_marker = "hunter2"\nimport os\nreturn {}'
    response = _save(integ_client, _python_payload(code, workflow_id="integ_ast"), tmp_path)
    assert response.status_code == 422

    message = response.json()["message"].lower()
    assert "no-import" in message
    assert "hunter2" not in message
    assert "secret_marker" not in message
    assert not (tmp_path / "integ_ast.yaml").exists()
    assert not _registry(integ_client).has_workflow("integ_ast")


def test_entry_mode_is_rejected_before_persisting(tmp_path: Path, integ_client: TestClient) -> None:
    """S18 condition 1: entry mode can importlib-load any repository module, so it stays out of HTTP."""
    payload = _python_payload("", workflow_id="integ_entry")
    payload["nodes"][0]["config"] = {"entry": "app.workflow.utils:convert_state_to_dict"}
    response = _save(integ_client, payload, tmp_path)
    assert response.status_code == 422
    assert "entry" in response.json()["message"].lower()
    assert not (tmp_path / "integ_entry.yaml").exists()
    assert not _registry(integ_client).has_workflow("integ_entry")
