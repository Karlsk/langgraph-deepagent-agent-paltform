"""Integration test: concurrent nested runs do not deadlock (S23).

The unit suite covers the guards with a single thread. What only a real
concurrent run can show is that two threads executing *different* outer
workflows, each nesting its own inner workflow, complete instead of blocking on
each other's per-id RLocks.

The topology is deliberately non-mutual. Two threads running ``A -> B`` and
``B -> A`` would be a genuine AB-BA deadlock: neither run stack contains a
repeated id, so the S23 cycle guard does not fire, and per-id RLocks are only
re-entrant within a thread. That is recorded as residual risk 2 in
``docs/changelog/workflow-subworkflow-node/spec-01-contract-change.md`` rather
than tested here — a test that hangs is not a test.
"""

from __future__ import annotations

import threading

import pytest

from app.workflow.models import EdgeDefinition, NodeDefinition, StateFieldSchema, WorkflowDefinition
from app.workflow.registry import WorkflowRegistry

pytestmark = pytest.mark.integration

JOIN_TIMEOUT_S = 30.0
ITERATIONS = 5


def _echo_wf(workflow_id: str, node_name: str) -> WorkflowDefinition:
    """A single-echo-node workflow."""
    return WorkflowDefinition(
        workflow_id=workflow_id,
        entry_point=node_name,
        nodes=[NodeDefinition(name=node_name, type="echo", config={"output": {"v": workflow_id}})],
        edges=[EdgeDefinition(source=node_name, target="END")],
        state_schema={"input": StateFieldSchema(type="str")},
    )


def _outer_wf(workflow_id: str, inner_id: str, sub_node: str) -> WorkflowDefinition:
    """A single-subworkflow-node workflow referencing ``inner_id``."""
    return WorkflowDefinition(
        workflow_id=workflow_id,
        entry_point=sub_node,
        nodes=[NodeDefinition(name=sub_node, type="subworkflow", config={"workflow_id": inner_id})],
        edges=[EdgeDefinition(source=sub_node, target="END")],
        state_schema={"input": StateFieldSchema(type="str")},
    )


def _registry() -> WorkflowRegistry:
    registry = WorkflowRegistry()
    for definition in (
        _echo_wf("inner_1", "i1"),
        _echo_wf("inner_2", "i2"),
        _outer_wf("outer_1", "inner_1", "sub_a"),
        _outer_wf("outer_2", "inner_2", "sub_b"),
    ):
        registry.register_workflow(definition)
    return registry


def test_concurrent_nested_runs_on_distinct_ids_complete() -> None:
    """Two threads, two disjoint nesting chains, repeated: all finish, all traces are prefixed."""
    registry = _registry()
    failures: list[BaseException] = []
    traces: dict[str, list[list[str]]] = {"outer_1": [], "outer_2": []}

    def run(workflow_id: str, expected_prefix: str) -> None:
        try:
            for _ in range(ITERATIONS):
                result = registry.execute_workflow(workflow_id, {"input": "concurrent"})
                names = [log.node_name for log in result.execution_logs]
                traces[workflow_id].append(names)
                assert f"{expected_prefix}/i1" in names or f"{expected_prefix}/i2" in names, names
        except BaseException as exc:  # noqa: BLE001 — surfaced to the main thread, not swallowed
            failures.append(exc)

    threads = [
        threading.Thread(target=run, args=("outer_1", "sub_a"), name="nested-1"),
        threading.Thread(target=run, args=("outer_2", "sub_b"), name="nested-2"),
    ]
    for thread in threads:
        thread.start()
    for thread in threads:
        thread.join(timeout=JOIN_TIMEOUT_S)

    alive = [thread.name for thread in threads if thread.is_alive()]
    assert not alive, f"threads did not finish within {JOIN_TIMEOUT_S}s (deadlock?): {alive}"
    assert not failures, f"nested runs raised: {failures!r}"
    assert all(len(names) == ITERATIONS for names in traces.values())


def test_concurrent_runs_of_the_same_nested_workflow_serialize() -> None:
    """The same outer id from two threads is serialized by its RLock, and both still succeed."""
    registry = _registry()
    failures: list[BaseException] = []
    results: list[int] = []
    lock = threading.Lock()

    def run() -> None:
        try:
            for _ in range(ITERATIONS):
                result = registry.execute_workflow("outer_1", {"input": "same-id"})
                with lock:
                    results.append(len(result.execution_logs))
        except BaseException as exc:  # noqa: BLE001 — surfaced to the main thread, not swallowed
            failures.append(exc)

    threads = [threading.Thread(target=run, name=f"same-{i}") for i in range(2)]
    for thread in threads:
        thread.start()
    for thread in threads:
        thread.join(timeout=JOIN_TIMEOUT_S)

    alive = [thread.name for thread in threads if thread.is_alive()]
    assert not alive, f"threads did not finish within {JOIN_TIMEOUT_S}s (deadlock?): {alive}"
    assert not failures, f"nested runs raised: {failures!r}"
    assert len(results) == 2 * ITERATIONS
    # Every run collected the same number of logs: no interleaving between the two threads.
    assert len(set(results)) == 1
