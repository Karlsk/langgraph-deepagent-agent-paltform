"""Unit tests for app.workflow.store (spec-17: YAML persistence).

Tests use tmp_path + monkeypatch to isolate from the real filesystem.
Zero network, zero real LLM calls.
"""

from __future__ import annotations

from pathlib import Path
from unittest.mock import patch

import pytest
import yaml

from app.workflow.models import (
    EdgeDefinition,
    NodeDefinition,
    StateFieldSchema,
    WorkflowDefinition,
    load_definition_from_yaml,
)

pytestmark = pytest.mark.unit


def _make_definition(workflow_id: str = "test_wf") -> WorkflowDefinition:
    return WorkflowDefinition(
        workflow_id=workflow_id,
        entry_point="step_one",
        nodes=[
            NodeDefinition(name="step_one", type="llm", config={"model_name": "gpt-4o-mini"}),
            NodeDefinition(name="step_two", type="http", config={"url": "https://example.com", "method": "GET"}),
        ],
        edges=[
            EdgeDefinition(source="step_one", target="step_two"),
            EdgeDefinition(source="step_two", target="END"),
        ],
        state_schema={"input": StateFieldSchema(type="str", description="user input")},
    )


class TestUserWorkflowDir:
    def test_returns_path_ending_in_config_user(self) -> None:
        from app.workflow.store import user_workflow_dir

        result = user_workflow_dir()
        assert result.name == "user"
        assert result.parent.name == "config"


class TestSaveDefinitionYaml:
    def test_save_creates_yaml_file(self, tmp_path: Path) -> None:
        from app.workflow.store import save_definition_yaml

        with patch("app.workflow.store.user_workflow_dir", return_value=tmp_path):
            definition = _make_definition()
            result_path = save_definition_yaml(definition)

        assert result_path == tmp_path / "test_wf.yaml"
        assert result_path.exists()

        content = yaml.safe_load(result_path.read_text(encoding="utf-8"))
        assert content["workflow_id"] == "test_wf"
        assert content["entry_point"] == "step_one"
        assert len(content["nodes"]) == 2
        assert "execution_history" not in content

    def test_roundtrip_save_then_load(self, tmp_path: Path) -> None:
        from app.workflow.store import save_definition_yaml

        with patch("app.workflow.store.user_workflow_dir", return_value=tmp_path):
            definition = _make_definition()
            save_definition_yaml(definition)

            loaded = load_definition_from_yaml(tmp_path / "test_wf.yaml")

        assert loaded.workflow_id == definition.workflow_id
        assert loaded.entry_point == definition.entry_point
        assert len(loaded.nodes) == len(definition.nodes)
        assert len(loaded.edges) == len(definition.edges)

    @pytest.mark.parametrize(
        "bad_id",
        [
            "has/slash",
            "has..dotdot",
            "has space",
            "x" * 65,
            "",
        ],
    )
    def test_invalid_workflow_id_rejected(self, tmp_path: Path, bad_id: str) -> None:
        from app.workflow.store import save_definition_yaml

        definition = _make_definition(workflow_id=bad_id)
        with patch("app.workflow.store.user_workflow_dir", return_value=tmp_path):
            with pytest.raises(ValueError, match="invalid workflow_id"):
                save_definition_yaml(definition)

        yaml_files = list(tmp_path.glob("*.yaml"))
        assert not yaml_files

    def test_atomic_write_no_partial_file(self, tmp_path: Path) -> None:
        from app.workflow.store import save_definition_yaml

        definition = _make_definition()
        with patch("app.workflow.store.user_workflow_dir", return_value=tmp_path):
            save_definition_yaml(definition)

        tmp_files = list(tmp_path.glob("*.tmp"))
        assert not tmp_files

        yaml_files = list(tmp_path.glob("*.yaml"))
        assert len(yaml_files) == 1


class TestDeleteDefinitionYaml:
    def test_delete_existing_returns_true(self, tmp_path: Path) -> None:
        from app.workflow.store import delete_definition_yaml, save_definition_yaml

        with patch("app.workflow.store.user_workflow_dir", return_value=tmp_path):
            save_definition_yaml(_make_definition())
            assert (tmp_path / "test_wf.yaml").exists()

            result = delete_definition_yaml("test_wf")

        assert result is True
        assert not (tmp_path / "test_wf.yaml").exists()

    def test_delete_nonexistent_returns_false(self, tmp_path: Path) -> None:
        from app.workflow.store import delete_definition_yaml

        with patch("app.workflow.store.user_workflow_dir", return_value=tmp_path):
            result = delete_definition_yaml("nonexistent")

        assert result is False

    def test_delete_invalid_workflow_id_rejected(self, tmp_path: Path) -> None:
        from app.workflow.store import delete_definition_yaml

        with patch("app.workflow.store.user_workflow_dir", return_value=tmp_path):
            with pytest.raises(ValueError, match="invalid workflow_id"):
                delete_definition_yaml("has/slash")


class TestBuildRegistryScansBothDirs:
    def test_build_registry_scans_examples_and_user(self, tmp_path: Path) -> None:
        from app.workflow.cli import build_registry

        examples_dir = tmp_path / "examples"
        user_dir = tmp_path / "user"
        examples_dir.mkdir()
        user_dir.mkdir()

        examples_yaml = """
workflow_id: example_wf
entry_point: n1
nodes:
  - name: n1
    type: echo
    config:
      output:
        result: ok
edges:
  - source: n1
    target: END
state_schema:
  input:
    type: str
    description: input
"""
        user_yaml = """
workflow_id: user_wf
entry_point: u1
nodes:
  - name: u1
    type: echo
    config:
      output:
        result: ok
edges:
  - source: u1
    target: END
state_schema:
  input:
    type: str
    description: input
"""
        (examples_dir / "example.yaml").write_text(examples_yaml, encoding="utf-8")
        (user_dir / "user.yaml").write_text(user_yaml, encoding="utf-8")

        registry = build_registry(examples_dir, user_dir=user_dir)
        assert registry.has_workflow("example_wf")
        assert registry.has_workflow("user_wf")

    def test_build_registry_empty_user_dir_ok(self, tmp_path: Path) -> None:
        from app.workflow.cli import build_registry

        examples_dir = tmp_path / "examples"
        user_dir = tmp_path / "user"
        examples_dir.mkdir()
        user_dir.mkdir()

        examples_yaml = """
workflow_id: only_example
entry_point: n1
nodes:
  - name: n1
    type: echo
    config:
      output:
        result: ok
edges:
  - source: n1
    target: END
state_schema:
  input:
    type: str
    description: input
"""
        (examples_dir / "example.yaml").write_text(examples_yaml, encoding="utf-8")

        registry = build_registry(examples_dir, user_dir=user_dir)
        assert registry.has_workflow("only_example")
        assert not registry.has_workflow("nonexistent")


class TestStoreUsesOnlySafeYaml:
    def test_no_unsafe_yaml_calls(self) -> None:
        import inspect
        import re

        from app.workflow import store

        source = inspect.getsource(store)
        assert "safe_dump" in source

        unsafe_dump = re.findall(r"yaml\.dump\s*\(", source)
        assert not unsafe_dump, "Found unsafe yaml.dump() calls in store.py"
        unsafe_load = re.findall(r"yaml\.load\s*\(", source)
        assert not unsafe_load, "Found unsafe yaml.load() calls in store.py"
