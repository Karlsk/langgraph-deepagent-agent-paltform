"""Unit tests for app.workflow.models (spec-01 TC3, CONTRACT §4.2/§5)."""

from pathlib import Path
from typing import Any

import pytest
from pydantic import ValidationError

from app.workflow import models
from app.workflow.models import (
    ConditionNotMatchedError,
    ConfigError,
    HTTPNodeError,
    LLMNodeError,
    NodeType,
    WorkflowDefinition,
    WorkflowEngineError,
    WorkflowNotFoundError,
    load_definition_from_yaml,
    parse_definition,
)

# Verbatim acceptance sample from spec-01 §6.
MINIMAL_YAML = """\
# 所需环境变量：OPENAI_API_KEY（运行该示例前设置，见 .env.example）
workflow_id: demo_minimal
description: "最小示例：LLM 单节点"   # 允许保留，解析时被忽略（非模型字段）
entry_point: greet
nodes:
  - name: greet
    type: llm
    config:
      llm_type: openai
      model_name: gpt-4o-mini
      system_prompt: "You are a concise assistant."
edges:
  - source: greet
    target: END
state_schema:
  input:
    type: str
    description: 用户输入
"""


def _minimal_data() -> dict[str, Any]:
    """Return a fresh, valid workflow definition dict for mutation in tests."""
    return {
        "workflow_id": "demo_minimal",
        "entry_point": "greet",
        "nodes": [{"name": "greet", "type": "llm", "config": {}}],
        "edges": [{"source": "greet", "target": "END"}],
        "state_schema": {"input": {"type": "str", "description": "用户输入"}},
    }


@pytest.mark.unit
def test_parse_minimal_yaml(tmp_path: Path) -> None:
    """Happy path: the sample YAML parses into a fully populated model."""
    yaml_file = tmp_path / "minimal.yaml"
    yaml_file.write_text(MINIMAL_YAML, encoding="utf-8")

    definition = load_definition_from_yaml(yaml_file)

    assert isinstance(definition, WorkflowDefinition)
    assert definition.workflow_id == "demo_minimal"
    assert definition.entry_point == "greet"
    assert [n.name for n in definition.nodes] == ["greet"]
    assert definition.nodes[0].type == "llm"
    assert definition.nodes[0].config["model_name"] == "gpt-4o-mini"
    assert len(definition.edges) == 1
    assert definition.edges[0].source == "greet"
    assert definition.edges[0].target == "END"
    assert definition.edges[0].condition is None
    input_field = definition.state_schema["input"]
    assert input_field.type == "str"
    assert input_field.default is None
    assert input_field.description == "用户输入"
    assert input_field.reducer is None
    assert definition.operator_logs == {}
    assert definition.execution_history == []


@pytest.mark.unit
@pytest.mark.parametrize("missing_field", ["workflow_id", "entry_point", "state_schema"])
def test_missing_required_field_raises(missing_field: str) -> None:
    """Dropping any required top-level field raises pydantic ValidationError."""
    data = _minimal_data()
    del data[missing_field]
    with pytest.raises(ValidationError):
        parse_definition(data)


@pytest.mark.unit
def test_duplicate_node_names_raises() -> None:
    """Model-level validation rejects duplicate node names (and empty nodes)."""
    data = _minimal_data()
    data["nodes"] = [
        {"name": "greet", "type": "llm", "config": {}},
        {"name": "greet", "type": "http", "config": {}},
    ]
    with pytest.raises(ValidationError, match="greet"):
        parse_definition(data)

    empty = _minimal_data()
    empty["nodes"] = []
    with pytest.raises(ValidationError):
        parse_definition(empty)


@pytest.mark.unit
def test_invalid_reducer_raises() -> None:
    """A reducer outside Literal["add", "last"] is rejected by pydantic."""
    data = _minimal_data()
    data["state_schema"]["input"]["reducer"] = "append"
    with pytest.raises(ValidationError):
        parse_definition(data)


@pytest.mark.unit
def test_extra_yaml_keys_ignored() -> None:
    """Unknown top-level keys such as description are tolerated (K1, extra="ignore")."""
    data = _minimal_data()
    data["description"] = "最小示例：LLM 单节点"
    data["author"] = "someone"
    definition = parse_definition(data)
    assert definition.workflow_id == "demo_minimal"
    assert not hasattr(definition, "description")


@pytest.mark.unit
def test_unknown_node_type_allowed() -> None:
    """NodeDefinition.type stays a plain str so plugin types pass through (R4)."""
    data = _minimal_data()
    data["nodes"][0]["type"] = "my_custom"
    data["nodes"][0]["name"] = "custom"
    data["entry_point"] = "custom"
    data["edges"] = []
    definition = parse_definition(data)
    assert definition.nodes[0].type == "my_custom"


@pytest.mark.unit
def test_load_file_not_found(tmp_path: Path) -> None:
    """A missing file raises ValueError whose message contains the path."""
    missing = tmp_path / "does_not_exist.yaml"
    with pytest.raises(ValueError, match="does_not_exist.yaml"):
        load_definition_from_yaml(missing)


@pytest.mark.unit
def test_load_invalid_yaml_syntax(tmp_path: Path) -> None:
    """Broken YAML raises ValueError carrying the file path as context."""
    bad = tmp_path / "broken.yaml"
    bad.write_text("workflow_id: [unclosed\n  nodes: {", encoding="utf-8")
    with pytest.raises(ValueError, match="broken.yaml"):
        load_definition_from_yaml(bad)


@pytest.mark.unit
def test_exception_hierarchy() -> None:
    """All five engine exceptions subclass WorkflowEngineError (and Exception)."""
    assert issubclass(WorkflowEngineError, Exception)
    for exc_type in (
        ConfigError,
        WorkflowNotFoundError,
        ConditionNotMatchedError,
        LLMNodeError,
        HTTPNodeError,
    ):
        assert issubclass(exc_type, WorkflowEngineError)
        assert issubclass(exc_type, Exception)


@pytest.mark.unit
def test_node_type_has_exactly_two_members() -> None:
    """Guard (R1/C8): NodeType holds exactly the two built-in members llm/http."""
    assert len(NodeType) == 2
    assert {member.value for member in NodeType} == {"llm", "http"}
    assert NodeType.LLM == "llm"
    assert NodeType.HTTP == "http"


@pytest.mark.unit
def test_no_domain_residue_in_models() -> None:
    """Guard (R2/C2): models.py carries no legacy domain field literals."""
    source = Path(models.__file__).read_text(encoding="utf-8")
    for residue in ("circle_", "planner_", "worker_", "dispatcher"):
        assert residue not in source, f"domain residue found in models.py: {residue}"


@pytest.mark.unit
def test_load_yaml_non_mapping(tmp_path: Path) -> None:
    """YAML whose root is not a mapping raises ValueError with path context."""
    scalar_yaml = tmp_path / "scalar.yaml"
    scalar_yaml.write_text("just a string\n", encoding="utf-8")
    with pytest.raises(ValueError, match="scalar.yaml"):
        load_definition_from_yaml(scalar_yaml)
