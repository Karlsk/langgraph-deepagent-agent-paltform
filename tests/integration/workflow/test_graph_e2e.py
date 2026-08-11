"""E2E integration tests for GraphBuilder (spec-06 TC4, milestone M5).

Real langgraph compile + invoke with EchoNode fixtures; zero network and
zero real LLM/HTTP calls.
"""

from __future__ import annotations

from collections.abc import Iterator
from pathlib import Path
from typing import Any, override

import pytest
from langchain_core.runnables import Runnable

from app.workflow.graph_builder import BuildResult, GraphBuilder
from app.workflow.models import (
    EdgeDefinition,
    NodeDefinition,
    StateFieldSchema,
    WorkflowDefinition,
    load_definition_from_yaml,
)
from app.workflow.nodes.base import BaseNode
from app.workflow.nodes.factory import register_node_type
from app.workflow.utils import convert_state_to_dict, map_output_to_state

pytestmark = pytest.mark.integration

EXAMPLES_DIR = Path(__file__).resolve().parents[3] / "app" / "workflow" / "config" / "examples"


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


def make_condition_branch_definition(check_output: dict[str, Any]) -> WorkflowDefinition:
    """Entry -> conditional split -> two branches -> END (3 nodes, one conditional source)."""
    return WorkflowDefinition.model_construct(
        workflow_id="wf_e2e_branch",
        entry_point="check",
        nodes=[
            NodeDefinition(name="check", type="echo", config={"output": check_output}),
            NodeDefinition(name="branch_a", type="echo", config={"output": {"branch": "a"}}),
            NodeDefinition(name="branch_b", type="echo", config={"output": {"branch": "b"}}),
        ],
        edges=[
            EdgeDefinition(source="check", target="branch_a", condition="check_result.status == 'ok'"),
            EdgeDefinition(source="check", target="branch_b", condition="check_result.status == 'fail'"),
            EdgeDefinition(source="branch_a", target="END"),
            EdgeDefinition(source="branch_b", target="END"),
        ],
        state_schema={"input": StateFieldSchema(type="str")},
        operator_logs={},
        execution_history=[],
    )


@pytest.mark.parametrize(
    ("check_output", "hit_node", "missed_node"),
    [
        ({"status": "ok"}, "branch_a", "branch_b"),
        ({"status": "fail"}, "branch_b", "branch_a"),
    ],
    ids=["hit_branch_a", "hit_branch_b"],
)
def test_condition_branch_e2e(check_output: dict[str, Any], hit_node: str, missed_node: str) -> None:
    """A 3-node conditional graph compiles and runs end to end; hit-path outputs are complete."""
    definition = make_condition_branch_definition(check_output)
    result = GraphBuilder().build_graph(definition)
    assert isinstance(result, BuildResult)
    assert set(result.nodes_map) == {"check", "branch_a", "branch_b"}

    final = result.compiled_graph.invoke({"input": "hello"})

    assert final["check_result"] == check_output
    assert final[f"{hit_node}_result"] == {"branch": hit_node[-1]}
    assert final.get(f"{missed_node}_result") is None
    assert len(final["history"]) == 2  # check + hit branch only


def test_condition_branch_example_yaml_parses() -> None:
    """condition_branch.yaml is loadable and passes build-time validation (no graph run: needs real LLM)."""
    definition = load_definition_from_yaml(EXAMPLES_DIR / "condition_branch.yaml")
    assert definition.workflow_id == "condition_branch_demo"
    assert [node.name for node in definition.nodes] == ["check", "notify", "summarize"]
    conditional = [edge for edge in definition.edges if edge.condition]
    assert len(conditional) == 2
    assert all(edge.source == "check" for edge in conditional)
    GraphBuilder()._validate_definition(definition)
