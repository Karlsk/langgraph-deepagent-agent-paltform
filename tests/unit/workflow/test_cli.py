"""Unit tests for app.workflow.cli (spec-08 TC2, CONTRACT §4.12 / H6)."""

from __future__ import annotations

import json
from collections.abc import Generator
from pathlib import Path
from typing import Any, override

import pytest
import structlog
from langchain_core.runnables import Runnable

from app.workflow import logging_conf
from app.workflow.cli import ApiResponse, build_registry, main
from app.workflow.nodes.base import BaseNode
from app.workflow.nodes.factory import register_node_type

pytestmark = pytest.mark.unit


@pytest.fixture(autouse=True)
def _reset_cli_logging(monkeypatch: pytest.MonkeyPatch) -> Generator[None, None, None]:
    """Avoid cached PrintLogger holding a closed capsys stream between tests."""
    monkeypatch.setattr(logging_conf, "_configured", False)
    structlog.reset_defaults()
    yield
    structlog.reset_defaults()


_ECHO_YAML = """
workflow_id: echo_demo
description: "echo demo workflow"
entry_point: greet
nodes:
  - name: greet
    type: echo
    config:
      output:
        response: hello
edges:
  - source: greet
    target: END
state_schema:
  input:
    type: str
    description: user input
  response:
    type: str
    description: greeting response
"""

_FAIL_YAML = """
workflow_id: fail_demo
description: "workflow whose single node raises"
entry_point: boom
nodes:
  - name: boom
    type: fail_leak
    config: {}
edges:
  - source: boom
    target: END
state_schema:
  input:
    type: str
    description: user input
"""


class _FailNode(BaseNode):
    """Test-only node raising an exception that embeds a dummy secret (H6)."""

    @override
    def build_runnable(self) -> Runnable:
        def func(state: Any) -> dict[str, Any]:
            msg = "upstream failed api_key=sk-live-leak-999"
            raise RuntimeError(msg)

        return self.wrap_runnable(func)

    @override
    def validate_config(self) -> bool:
        return True


def _write_echo_dir(tmp_path: Path) -> Path:
    (tmp_path / "echo_demo.yaml").write_text(_ECHO_YAML, encoding="utf-8")
    return tmp_path


def _last_json_line(stdout: str) -> dict[str, Any]:
    """Envelope is the last JSON line on stdout; logs go to stderr."""
    for line in reversed(stdout.strip().splitlines()):
        if line.strip().startswith("{"):
            return json.loads(line)
    msg = f"no JSON envelope found in stdout: {stdout!r}"
    raise AssertionError(msg)


def test_cli_run_success_envelope(tmp_path: Path, capsys: pytest.CaptureFixture[str]) -> None:
    """Success path: exit 0, envelope with data and full metadata (§6 shape)."""
    _write_echo_dir(tmp_path)
    exit_code = main(["run", "--dir", str(tmp_path), "--workflow", "echo_demo", "--input", '{"input":"hi"}'])
    assert exit_code == 0
    envelope = _last_json_line(capsys.readouterr().out)
    assert envelope["success"] is True
    assert envelope["error"] is None
    assert envelope["data"]["response"] == "hello"
    assert envelope["data"]["greet_result"] == {"response": "hello"}
    metadata = envelope["metadata"]
    assert metadata["workflow_id"] == "echo_demo"
    assert len(metadata["run_id"]) == 32
    assert metadata["duration_ms"] >= 0.0
    assert metadata["node_count"] == 1


def test_cli_bad_input_json(tmp_path: Path, capsys: pytest.CaptureFixture[str]) -> None:
    """Malformed --input: exit 1 with a friendly failure envelope."""
    _write_echo_dir(tmp_path)
    exit_code = main(["run", "--dir", str(tmp_path), "--workflow", "echo_demo", "--input", "{bad"])
    assert exit_code == 1
    envelope = _last_json_line(capsys.readouterr().out)
    assert envelope["success"] is False
    assert envelope["error"]
    assert "input" in envelope["error"].lower()


def test_cli_unknown_workflow(tmp_path: Path, capsys: pytest.CaptureFixture[str]) -> None:
    """Unknown workflow_id: exit 1, error mentions the id (WorkflowNotFoundError)."""
    _write_echo_dir(tmp_path)
    exit_code = main(["run", "--dir", str(tmp_path), "--workflow", "not_exist", "--input", "{}"])
    assert exit_code == 1
    envelope = _last_json_line(capsys.readouterr().out)
    assert envelope["success"] is False
    assert "not_exist" in envelope["error"]


def test_cli_error_no_state_leak(tmp_path: Path, capsys: pytest.CaptureFixture[str]) -> None:
    """H6: node exception embedding a dummy secret never leaks it via the envelope."""
    register_node_type("fail_leak", _FailNode)
    (tmp_path / "fail_demo.yaml").write_text(_FAIL_YAML, encoding="utf-8")
    exit_code = main(["run", "--dir", str(tmp_path), "--workflow", "fail_demo", "--input", "{}"])
    assert exit_code == 1
    out = capsys.readouterr().out
    assert "sk-live-leak-999" not in out
    envelope = _last_json_line(out)
    assert envelope["success"] is False
    assert envelope["data"] is None


def test_build_registry_loads_dir(tmp_path: Path) -> None:
    """build_registry loads and registers every definition in the directory."""
    _write_echo_dir(tmp_path)
    second = _ECHO_YAML.replace("echo_demo", "echo_second")
    (tmp_path / "echo_second.yaml").write_text(second, encoding="utf-8")
    registry = build_registry(tmp_path)
    assert sorted(registry.list_workflows()) == ["echo_demo", "echo_second"]


def test_api_response_to_json_keeps_unicode() -> None:
    """to_json uses ensure_ascii=False per CONTRACT §4.12."""
    response = ApiResponse(success=True, data={"greeting": "你好"})
    assert "你好" in response.to_json()
    assert response.metadata == {}
