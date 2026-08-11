"""Unit tests for GraphBuilder (spec-06 TC1-TC3, CONTRACT §4.9 / S6 / S7)."""

from __future__ import annotations

from collections.abc import Iterator
from typing import Any, override

import pytest
from langchain_core.runnables import Runnable

from app.workflow.graph_builder import GraphBuilder
from app.workflow.models import EdgeDefinition, NodeDefinition, WorkflowDefinition
from app.workflow.nodes.base import BaseNode
from app.workflow.nodes.factory import _NODE_REGISTRY, register_node_type
from app.workflow.utils import convert_state_to_dict, map_output_to_state

pytestmark = pytest.mark.unit


class EchoNode(BaseNode):
    """Test-only node: merges config['output'] into state (zero network, zero LLM)."""

    @override
    def build_runnable(self) -> Runnable:
        def func(state: Any) -> dict[str, Any]:
            state_dict = convert_state_to_dict(state)
            return map_output_to_state(self.name, dict(self.config.get("output", {})), state_dict)

        return self.wrap_runnable(func)

    @override
    def validate_config(self) -> bool:
        return True


@pytest.fixture(autouse=True)
def restore_node_registry() -> Iterator[None]:
    """Snapshot/restore the plugin registry and register EchoNode (D7 isolation)."""
    snapshot = dict(_NODE_REGISTRY)
    register_node_type("echo", EchoNode)
    yield
    _NODE_REGISTRY.clear()
    _NODE_REGISTRY.update(snapshot)


def make_definition(
    *,
    workflow_id: str = "wf_test",
    entry_point: str = "a",
    nodes: list[NodeDefinition] | None = None,
    edges: list[EdgeDefinition] | None = None,
) -> WorkflowDefinition:
    """Build a minimal WorkflowDefinition; model_construct bypasses model-layer checks for C5 deep-defense cases."""
    return WorkflowDefinition.model_construct(
        workflow_id=workflow_id,
        entry_point=entry_point,
        nodes=nodes if nodes is not None else [NodeDefinition(name="a", type="echo", config={})],
        edges=edges if edges is not None else [],
        state_schema={},
        operator_logs={},
        execution_history=[],
    )


# ---------------------------------------------------------------------------
# TC1: _validate_definition (C5 clean validation, no dispatcher exemption)
# ---------------------------------------------------------------------------


def test_validate_empty_workflow_id() -> None:
    """Empty workflow_id must fail with workflow_id named in the message."""
    definition = make_definition(workflow_id="")
    with pytest.raises(ValueError, match="workflow_id"):
        GraphBuilder()._validate_definition(definition)


def test_validate_empty_nodes() -> None:
    """Empty node list must fail even when the model layer is bypassed (C5 deep defense)."""
    definition = make_definition(nodes=[])
    with pytest.raises(ValueError, match="nodes"):
        GraphBuilder()._validate_definition(definition)


def test_validate_entry_point_missing() -> None:
    """entry_point not in node names must fail naming the offending entry point."""
    definition = make_definition(entry_point="ghost")
    with pytest.raises(ValueError, match="ghost"):
        GraphBuilder()._validate_definition(definition)


@pytest.mark.parametrize(
    ("edge", "expected_fragment"),
    [
        (EdgeDefinition(source="ghost", target="a"), "ghost"),
        (EdgeDefinition(source="a", target="ghost"), "ghost"),
    ],
    ids=["dangling_source", "dangling_target"],
)
def test_validate_edge_endpoint_missing(edge: EdgeDefinition, expected_fragment: str) -> None:
    """Dangling edge endpoints must fail naming the offending endpoint; target='END' is legal."""
    definition = make_definition(edges=[edge])
    with pytest.raises(ValueError, match=expected_fragment):
        GraphBuilder()._validate_definition(definition)
    # Literal "END" is a legal target (EXP-G5: builder maps it to the END object later).
    legal = make_definition(edges=[EdgeDefinition(source="a", target="END")])
    GraphBuilder()._validate_definition(legal)


def test_validate_no_dispatcher_exemption() -> None:
    """A node named 'dispatcher' gets no exemption: dangling edges fail as usual (C5 guard)."""
    definition = make_definition(
        nodes=[NodeDefinition(name="dispatcher", type="dispatcher", config={})],
        entry_point="dispatcher",
        edges=[EdgeDefinition(source="dispatcher", target="nonexistent")],
    )
    with pytest.raises(ValueError, match="nonexistent"):
        GraphBuilder()._validate_definition(definition)


# ---------------------------------------------------------------------------
# TC2: seven-step build_graph + _add_nodes/_add_edges
# ---------------------------------------------------------------------------


def test_build_two_node_linear() -> None:
    """A->B->END compiles and invokes; final state carries both node outputs (EXP-G6)."""
    definition = make_definition(
        workflow_id="wf_linear",
        nodes=[
            NodeDefinition(name="a", type="echo", config={"output": {"value": "from_a"}}),
            NodeDefinition(name="b", type="echo", config={"output": {"value": "from_b"}}),
        ],
        edges=[EdgeDefinition(source="a", target="b"), EdgeDefinition(source="b", target="END")],
    )
    result = GraphBuilder().build_graph(definition)
    assert set(result.nodes_map) == {"a", "b"}
    assert all(isinstance(node, EchoNode) for node in result.nodes_map.values())
    final_state = result.compiled_graph.invoke({})
    assert final_state["a_result"] == {"value": "from_a"}
    assert final_state["b_result"] == {"value": "from_b"}
    assert len(final_state["history"]) == 2


def test_mixed_conditional_and_normal_raises() -> None:
    """Same source mixing normal and conditional edges must fail at build time (C3)."""
    definition = make_definition(
        nodes=[
            NodeDefinition(name="a", type="echo", config={}),
            NodeDefinition(name="b", type="echo", config={}),
        ],
        edges=[
            EdgeDefinition(source="a", target="b"),
            EdgeDefinition(source="a", target="b", condition="flag"),
        ],
    )
    with pytest.raises(ValueError, match="'a'"):
        GraphBuilder().build_graph(definition)
