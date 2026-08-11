"""Concurrency integration tests for WorkflowRegistry (spec-07 TC5, H1).

16 threads x 64 runs against one workflow id: per-workflow RLock serializes
the runs while the run-scoped collector keeps logs isolated (D3 double
insurance). Zero network, zero LLM (EchoNode only).
"""

from __future__ import annotations

import threading
from concurrent.futures import ThreadPoolExecutor
from typing import Any, override

import pytest
from langchain_core.runnables import Runnable

from app.workflow.models import (
    EdgeDefinition,
    ExecutionLog,
    NodeDefinition,
    StateFieldSchema,
    WorkflowDefinition,
)
from app.workflow.nodes.base import BaseNode
from app.workflow.nodes.factory import register_node_type
from app.workflow.registry import RunResult, WorkflowRegistry
from app.workflow.utils import convert_state_to_dict, map_output_to_state

pytestmark = pytest.mark.integration

THREAD_COUNT = 16
RUNS_PER_THREAD = 64

_BARRIER: threading.Barrier | None = None


class BarrierNode(BaseNode):
    """Test-only node that waits on a shared barrier; proves cross-workflow runs interleave (D3, spec-09 TC2)."""

    @override
    def build_runnable(self) -> Runnable:
        def func(state: Any) -> dict[str, Any]:
            if _BARRIER is None:  # pragma: no cover - defensive, set by the test
                msg = "barrier not initialised"
                raise RuntimeError(msg)
            _BARRIER.wait(timeout=5)
            state_dict = convert_state_to_dict(state)
            output = {"marker": self.name}
            result = map_output_to_state(self.name, output, state_dict)
            self.log_execution(
                ExecutionLog(
                    node_name=self.name,
                    node_type=str(self.node_type),
                    input_data={},
                    output_data=output,
                    execution_time_ms=0.0,
                )
            )
            return result

        return self.wrap_runnable(func)

    @override
    def validate_config(self) -> bool:
        return True


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


def make_single_node_definition(workflow_id: str, node_name: str) -> WorkflowDefinition:
    """One-node barrier workflow used by the cross-workflow non-blocking test."""
    return WorkflowDefinition(
        workflow_id=workflow_id,
        entry_point=node_name,
        nodes=[NodeDefinition(name=node_name, type="barrier", config={})],
        edges=[EdgeDefinition(source=node_name, target="END")],
        state_schema={"input": StateFieldSchema(type="str")},
        operator_logs={},
        execution_history=[],
    )


def test_different_workflows_not_blocked() -> None:
    """D3 (spec-09 TC2): runs of two different workflow_ids interleave and never block each other.

    Both nodes rendezvous on a shared threading.Barrier. If the per-workflow
    RLocks were actually shared across workflow ids, the first run would hold
    the lock inside the barrier wait while the second workflow could not enter
    its node, so the barrier would time out (BrokenBarrierError). Reaching the
    rendezvous proves the two runs executed concurrently.
    """
    global _BARRIER  # noqa: PLW0603 - test-scoped rendezvous point
    _BARRIER = threading.Barrier(2)
    register_node_type("barrier", BarrierNode)

    registry = WorkflowRegistry()
    registry.register_workflow(make_single_node_definition("wf_alpha", "alpha"))
    registry.register_workflow(make_single_node_definition("wf_beta", "beta"))

    with ThreadPoolExecutor(max_workers=2) as pool:
        future_alpha = pool.submit(registry.execute_workflow, "wf_alpha", {"input": "a"})
        future_beta = pool.submit(registry.execute_workflow, "wf_beta", {"input": "b"})
        result_alpha = future_alpha.result(timeout=10)
        result_beta = future_beta.result(timeout=10)

    assert result_alpha.output["alpha_result"] == {"marker": "alpha"}
    assert result_beta.output["beta_result"] == {"marker": "beta"}
    assert result_alpha.run_id != result_beta.run_id
    _BARRIER = None
