"""Unit tests for app.workflow.nodes.factory (spec-03, CONTRACT §4.5, R4/H5/D7/AD-04/AD-08)."""

import pytest

from app.workflow.models import NodeDefinition, OperatorLog
from app.workflow.nodes import factory
from app.workflow.nodes.factory import create_node, list_node_types, register_node_type
from app.workflow.nodes.http_node import HTTPNode
from app.workflow.nodes.llm_node import LLMNode
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
@pytest.mark.parametrize("http_type", ["http", "HTTP"])
def test_factory_creates_http_node(http_type: str) -> None:
    """Built-in http branch wires HTTPNode with its frozen constructor (spec-05 TC4, scheme A)."""
    definition = NodeDefinition(name="http_a", type=http_type, config={"url": "https://api.example.com/v1"})
    node = create_node(definition)
    assert isinstance(node, HTTPNode)
    assert node.name == "http_a"
    assert node._node_config.url == "https://api.example.com/v1"  # noqa: SLF001 — asserting wiring per frozen contract
    assert node.operator_log == OperatorLog(node_name="http_a", input_schema={}, output_schema={})


@pytest.mark.unit
def test_http_self_registered_for_visibility() -> None:
    """http_node self-registration keeps 'http' visible in list_node_types() (CONTRACT §4.8)."""
    assert "http" in list_node_types()


@pytest.mark.unit
@pytest.mark.parametrize("llm_type", ["llm", "LLM"])
def test_factory_creates_llm_node(llm_type: str) -> None:
    """Built-in llm branch wires LLMNode with its frozen constructor (spec-04 TC4, scheme A)."""
    definition = NodeDefinition(name="llm_a", type=llm_type, config={"model_name": "gpt-4o"})
    node = create_node(definition)
    assert isinstance(node, LLMNode)
    assert node.name == "llm_a"
    assert node._llm_config.model_name == "gpt-4o"  # noqa: SLF001 — asserting wiring per frozen contract
    assert node.operator_log == OperatorLog(node_name="llm_a", input_schema={}, output_schema={})


@pytest.mark.unit
def test_llm_self_registered_for_visibility() -> None:
    """llm_node self-registration keeps 'llm' visible in list_node_types() (CONTRACT §4.7)."""
    assert "llm" in list_node_types()


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
