"""Unit tests for GraphBuilder (spec-06 TC1-TC3, CONTRACT §4.9 / S6 / S7)."""

from __future__ import annotations

import logging
from collections.abc import Iterator
from typing import Any, override

import pytest
import structlog
import structlog.stdlib
from langchain_core.runnables import Runnable

from app.workflow.graph_builder import GraphBuilder
from app.workflow.models import (
    ConditionNotMatchedError,
    EdgeDefinition,
    NodeDefinition,
    StateFieldSchema,
    WorkflowDefinition,
)
from app.workflow.nodes.base import BaseNode
from app.workflow.nodes.factory import register_node_type
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
def register_echo_node() -> Iterator[None]:
    """Register EchoNode for the test; registry restore is handled by the global conftest fixture (D7)."""
    register_node_type("echo", EchoNode)
    yield


def make_definition(
    *,
    workflow_id: str = "wf_test",
    entry_point: str = "a",
    nodes: list[NodeDefinition] | None = None,
    edges: list[EdgeDefinition] | None = None,
    state_schema: dict[str, Any] | None = None,
) -> WorkflowDefinition:
    """Build a minimal WorkflowDefinition; model_construct bypasses model-layer checks for C5 deep-defense cases."""
    return WorkflowDefinition.model_construct(
        workflow_id=workflow_id,
        entry_point=entry_point,
        nodes=nodes if nodes is not None else [NodeDefinition(name="a", type="echo", config={})],
        edges=edges if edges is not None else [],
        state_schema=state_schema if state_schema is not None else {},
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


# ---------------------------------------------------------------------------
# TC3: condition router (C3 no-match policy, S6/S7)
# ---------------------------------------------------------------------------

STATE_MARK = "full-state-dump-marker-spec06"


def make_branch_definition(check_output: dict[str, Any]) -> WorkflowDefinition:
    """Check node branching to ok_node/fail_node; shared fixture for router tests."""
    return make_definition(
        workflow_id="wf_branch",
        entry_point="check",
        nodes=[
            NodeDefinition(name="check", type="echo", config={"output": check_output}),
            NodeDefinition(name="ok_node", type="echo", config={"output": {"branch": "ok"}}),
            NodeDefinition(name="fail_node", type="echo", config={"output": {"branch": "fail"}}),
        ],
        edges=[
            EdgeDefinition(source="check", target="ok_node", condition="check_result.status == 'ok'"),
            EdgeDefinition(source="check", target="fail_node", condition="check_result.status == 'fail'"),
        ],
    )


def test_router_equality_branch() -> None:
    """Equality condition routes to the matching branch; the other branch stays untouched."""
    definition = make_branch_definition({"status": "ok"})
    final = GraphBuilder().build_graph(definition).compiled_graph.invoke({})
    assert final["ok_node_result"] == {"branch": "ok"}
    # Unexecuted branch slots carry no channel value (EXP-G6 memo: output holds written fields only).
    assert final.get("fail_node_result") is None
    assert len(final["history"]) == 2


def test_router_truthiness_branch() -> None:
    """Pure-path condition is evaluated as truthiness (S7); the field needs a state channel."""
    definition = make_definition(
        workflow_id="wf_truth",
        entry_point="check",
        nodes=[
            NodeDefinition(name="check", type="echo", config={"output": {"flag": True}}),
            NodeDefinition(name="yes", type="echo", config={"output": {"branch": "yes"}}),
            NodeDefinition(name="no", type="echo", config={"output": {"branch": "no"}}),
        ],
        edges=[
            EdgeDefinition(source="check", target="yes", condition="flag"),
            EdgeDefinition(source="check", target="no", condition="no_flag"),
        ],
        state_schema={"flag": StateFieldSchema(type="bool")},
    )
    final = GraphBuilder().build_graph(definition).compiled_graph.invoke({})
    assert final["yes_result"] == {"branch": "yes"}
    assert final.get("no_result") is None


def test_router_no_match_raises() -> None:
    """All conditions missed + policy='raise' -> ConditionNotMatchedError with source and conditions (S6, EXP-G7)."""
    definition = make_branch_definition({"status": "unknown"})
    compiled = GraphBuilder().build_graph(definition).compiled_graph
    with pytest.raises(ConditionNotMatchedError) as exc_info:
        compiled.invoke({})
    message = str(exc_info.value)
    assert "check" in message
    assert "check_result.status == 'ok'" in message
    assert "check_result.status == 'fail'" in message


def test_router_no_match_default() -> None:
    """policy='default' with default_edges routes unmatched runs to the fallback (S6)."""
    definition = make_branch_definition({"status": "unknown"})
    result = GraphBuilder(no_match_policy="default").build_graph(definition, default_edges={"check": "fail_node"})
    final = result.compiled_graph.invoke({})
    assert final["fail_node_result"] == {"branch": "fail"}
    assert final.get("ok_node_result") is None


def test_router_default_missing_at_build() -> None:
    """policy='default' without a default_edges entry must fail at build time (S6)."""
    definition = make_branch_definition({"status": "unknown"})
    with pytest.raises(ValueError, match="default_edges"):
        GraphBuilder(no_match_policy="default").build_graph(definition)


def test_router_no_print_no_full_state(caplog: pytest.LogCaptureFixture, capsys: pytest.CaptureFixture[str]) -> None:
    """Router DEBUG log carries only condition and target; no state dump, empty stdout (C3/H6)."""
    structlog.configure(
        processors=[structlog.stdlib.add_log_level, structlog.processors.KeyValueRenderer()],
        logger_factory=structlog.stdlib.LoggerFactory(),
        wrapper_class=structlog.stdlib.BoundLogger,
    )
    try:
        definition = make_branch_definition({"status": "ok"})
        compiled = GraphBuilder().build_graph(definition).compiled_graph
        with caplog.at_level(logging.DEBUG, logger="app.workflow.graph_builder"):
            compiled.invoke({"sensitive": STATE_MARK})
    finally:
        structlog.reset_defaults()
    assert capsys.readouterr().out == ""
    router_records = [record for record in caplog.records if record.name == "app.workflow.graph_builder"]
    assert router_records, "expected a router DEBUG record"
    for record in router_records:
        rendered = record.getMessage()
        assert STATE_MARK not in rendered
        assert "sensitive" not in rendered


def test_parse_condition_variants() -> None:
    """Equality (both quote styles, arbitrary spacing) and pure-path forms (S7)."""
    assert GraphBuilder._parse_condition("a.b == 'x'") == ("a.b", "x")
    assert GraphBuilder._parse_condition('a.b=="y"') == ("a.b", "y")
    assert GraphBuilder._parse_condition("flag") == ("flag", None)
