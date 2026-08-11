"""Concurrency integration tests for WorkflowRegistry (spec-07 TC5, H1).

16 threads x 64 runs against one workflow id: per-workflow RLock serializes
the runs while the run-scoped collector keeps logs isolated (D3 double
insurance). Zero network, zero LLM (EchoNode only).
"""

from __future__ import annotations

from concurrent.futures import ThreadPoolExecutor

import pytest

from app.workflow.models import (
    EdgeDefinition,
    NodeDefinition,
    StateFieldSchema,
    WorkflowDefinition,
)
from app.workflow.registry import RunResult, WorkflowRegistry

pytestmark = pytest.mark.integration

THREAD_COUNT = 16
RUNS_PER_THREAD = 64


def make_two_node_definition() -> WorkflowDefinition:
    """Linear echo workflow a -> b -> END with marker outputs."""
    return WorkflowDefinition(
        workflow_id="wf_concurrent",
        entry_point="a",
        nodes=[
            NodeDefinition(name="a", type="echo", config={"output": {"marker": "a"}}),
            NodeDefinition(name="b", type="echo", config={"output": {"marker": "b"}}),
        ],
        edges=[
            EdgeDefinition(source="a", target="b"),
            EdgeDefinition(source="b", target="END"),
        ],
        state_schema={"input": StateFieldSchema(type="str")},
        operator_logs={},
        execution_history=[],
    )


def test_concurrent_same_workflow_logs_isolated() -> None:
    """16 threads x 64 runs: run_ids unique, logs exactly cover executed nodes, no cross-run leakage."""
    registry = WorkflowRegistry()
    registry.register_workflow(make_two_node_definition())

    def run_batch(thread_index: int) -> list[RunResult]:
        return [
            registry.execute_workflow("wf_concurrent", {"input": f"t{thread_index}-r{i}"})
            for i in range(RUNS_PER_THREAD)
        ]

    with ThreadPoolExecutor(max_workers=THREAD_COUNT) as pool:
        batches = list(pool.map(run_batch, range(THREAD_COUNT)))

    results = [result for batch in batches for result in batch]
    assert len(results) == THREAD_COUNT * RUNS_PER_THREAD

    run_ids = {result.run_id for result in results}
    assert len(run_ids) == THREAD_COUNT * RUNS_PER_THREAD  # every run_id unique

    for result in results:
        assert {log.node_name for log in result.execution_logs} == {"a", "b"}  # exactly one per node
        assert len(result.execution_logs) == 2
        assert result.output["a_result"] == {"marker": "a"}
        assert result.output["b_result"] == {"marker": "b"}
