"""Unit tests for GraphBuilder (spec-06 TC1-TC3, CONTRACT §4.9 / S6 / S7)."""

from __future__ import annotations

import pytest

from app.workflow.graph_builder import GraphBuilder
from app.workflow.models import EdgeDefinition, NodeDefinition, WorkflowDefinition

pytestmark = pytest.mark.unit


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
