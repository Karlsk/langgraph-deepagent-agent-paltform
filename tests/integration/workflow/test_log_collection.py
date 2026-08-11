"""Run-scoped log collection integration tests for WorkflowRegistry (spec-07 TC5, H3).

A 3-node conditional graph: the collector must cover exactly the nodes on the
actual execution path, independent of node creation or branch choice. Zero
network, zero LLM (EchoNode only).
"""

from __future__ import annotations

from typing import Any

import pytest

from app.workflow.models import (
    EdgeDefinition,
    NodeDefinition,
    StateFieldSchema,
    WorkflowDefinition,
)
from app.workflow.registry import WorkflowRegistry

pytestmark = pytest.mark.integration


def make_branch_definition(check_output: dict[str, Any]) -> WorkflowDefinition:
    """Entry -> conditional split -> two branches -> END (one branch executes per run)."""
    return WorkflowDefinition(
        workflow_id="wf_log_branch",
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
def test_logs_cover_all_executed_nodes(check_output: dict[str, Any], hit_node: str, missed_node: str) -> None:
    """execution_logs node set equals the actual path; the missed branch is absent (H3)."""
    registry = WorkflowRegistry()
    registry.register_workflow(make_branch_definition(check_output))
    result = registry.execute_workflow("wf_log_branch", {"input": "go"})

    logged_nodes = [log.node_name for log in result.execution_logs]
    assert set(logged_nodes) == {"check", hit_node}  # exactly the executed path
    assert len(logged_nodes) == 2  # one entry per executed node
    assert missed_node not in logged_nodes
    assert registry.get_execution_history("wf_log_branch") == result.execution_logs
    assert [log.node_name for log in registry.get_node_execution_history("wf_log_branch", "check")] == ["check"]
