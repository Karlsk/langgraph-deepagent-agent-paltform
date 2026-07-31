"""Unit tests for app.workflow.nodes.factory (spec-03, CONTRACT §4.5, R4/H5/D7/AD-04/AD-08)."""

import pytest

from app.workflow.models import NodeDefinition, OperatorLog
from app.workflow.nodes import factory
from app.workflow.nodes.factory import create_node, list_node_types, register_node_type
from tests.unit.workflow.nodes.test_base import FakeNode


@pytest.mark.unit
def test_register_and_create_plugin_node() -> None:
    """A registered plugin type is created via the registry with definition attributes."""
    register_node_type("fake", FakeNode)
    definition = NodeDefinition(name="n1", type="fake", config={"k": "v"})
    node = create_node(definition)
    assert isinstance(node, FakeNode)
    assert node.name == "n1"
    assert node.node_type == "fake"
    assert node.config == {"k": "v"}


@pytest.mark.unit
def test_register_rejects_non_basenode_subclass() -> None:
    """register_node_type raises TypeError for a non-BaseNode class (CONTRACT §4.5)."""

    class NotANode:
        pass

    with pytest.raises(TypeError, match="BaseNode subclass"):
        register_node_type("bad", NotANode)  # type: ignore[arg-type]


@pytest.mark.unit
def test_create_unknown_type_raises_value_error() -> None:
    """An unknown type raises ValueError listing registered types and the hint."""
    register_node_type("fake", FakeNode)
    definition = NodeDefinition(name="n1", type="nope", config={})
    with pytest.raises(ValueError, match="Unknown node type 'nope'") as exc_info:
        create_node(definition)
    message = str(exc_info.value)
    assert "fake" in message
    assert "register_node_type" in message


@pytest.mark.unit
@pytest.mark.parametrize("builtin_type", ["llm", "LLM", "http", "HTTP"])
def test_builtin_types_not_yet_wired(builtin_type: str) -> None:
    """Built-in llm/http placeholders (scheme D) raise ValueError until spec-04/05 land (AD-04)."""
    definition = NodeDefinition(name="n1", type=builtin_type, config={})
    with pytest.raises(ValueError, match="spec-0[45]"):
        create_node(definition)


@pytest.mark.unit
def test_default_operator_log_synthesized() -> None:
    """Without operator_log, create_node synthesizes one with empty schemas (CONTRACT §4.5)."""
    register_node_type("fake", FakeNode)
    definition = NodeDefinition(name="n1", type="fake", config={})
    node = create_node(definition)
    assert node.operator_log == OperatorLog(node_name="n1", input_schema={}, output_schema={})


@pytest.mark.unit
def test_registry_isolated_between_tests() -> None:
    """The autouse snapshot-restore fixture keeps registrations from leaking (D7/AD-08)."""
    assert "fake" not in list_node_types()
    assert "fake" not in factory._NODE_REGISTRY  # noqa: SLF001 — asserting isolation per AD-08
