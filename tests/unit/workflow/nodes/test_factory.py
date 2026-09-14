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


# --- optional injected parameters: signature probing (CONTRACT §4.5, node-development §7.1) ---


class _RunnerAwareNode(FakeNode):
    """Plugin that declares the optional injected parameter."""

    received_runner: object = None

    def __init__(self, *args: object, workflow_runner: object = None, **kwargs: object) -> None:
        _RunnerAwareNode.received_runner = workflow_runner
        super().__init__(*args, **kwargs)  # pyright: ignore[reportArgumentType] — test double wiring


class _PlainNode(FakeNode):
    """Plugin that knows nothing about workflow_runner (the PythonNode situation)."""


class _KwargsNode(FakeNode):
    """Plugin swallowing arbitrary kwargs — probing must treat VAR_KEYWORD as accepting."""

    seen: dict[str, object] = {}

    def __init__(self, *args: object, **kwargs: object) -> None:
        _KwargsNode.seen = dict(kwargs)
        # BaseNode.__init__ has no such parameter, so a real **kwargs plugin must consume it itself
        kwargs.pop("workflow_runner", None)
        super().__init__(*args, **kwargs)  # pyright: ignore[reportArgumentType] — test double wiring


def _fake_runner() -> object:
    return lambda workflow_id, input_data, caller_label: {"output": {}, "run_id": "r", "inner_log_count": 0}


@pytest.mark.unit
def test_plugin_declaring_workflow_runner_receives_it() -> None:
    """Probing finds the declared parameter and passes the injected callable."""
    register_node_type("runner_aware", _RunnerAwareNode)
    runner = _fake_runner()
    node = create_node(NodeDefinition(name="n1", type="runner_aware", config={}), workflow_runner=runner)
    assert isinstance(node, _RunnerAwareNode)
    assert _RunnerAwareNode.received_runner is runner


@pytest.mark.unit
def test_plugin_without_the_parameter_is_unaffected() -> None:
    """Probing must not pass the kwarg to a class that cannot accept it (no TypeError)."""
    register_node_type("plain", _PlainNode)
    node = create_node(NodeDefinition(name="n1", type="plain", config={}), workflow_runner=_fake_runner())
    assert isinstance(node, _PlainNode)


@pytest.mark.unit
def test_plugin_with_var_keyword_receives_it() -> None:
    """A **kwargs plugin is treated as accepting, per the frozen probing rule."""
    register_node_type("kwargs_node", _KwargsNode)
    runner = _fake_runner()
    create_node(NodeDefinition(name="n1", type="kwargs_node", config={}), workflow_runner=runner)
    assert _KwargsNode.seen.get("workflow_runner") is runner


@pytest.mark.unit
def test_no_runner_passed_when_none() -> None:
    """Probing still runs when the value is None, so a declaring plugin gets an explicit None."""
    register_node_type("runner_aware", _RunnerAwareNode)
    _RunnerAwareNode.received_runner = "stale"
    create_node(NodeDefinition(name="n1", type="runner_aware", config={}))
    assert _RunnerAwareNode.received_runner is None


@pytest.mark.unit
def test_builtin_branches_ignore_workflow_runner() -> None:
    """The two built-in branches keep their specialized constructors; R4 count stays 2."""
    node = create_node(
        NodeDefinition(name="http_a", type="http", config={"url": "https://api.example.com/v1"}),
        workflow_runner=_fake_runner(),
    )
    assert isinstance(node, HTTPNode)
