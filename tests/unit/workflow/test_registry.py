"""Unit tests for WorkflowRegistry runtime (spec-07, CONTRACT §4.10 / S10-S13)."""

from __future__ import annotations

from dataclasses import FrozenInstanceError
from datetime import datetime, timedelta
from typing import Any

import pytest

from app.workflow.models import (
    EdgeDefinition,
    ExecutionLog,
    NodeDefinition,
    OperatorLog,
    StateFieldSchema,
    WorkflowDefinition,
    WorkflowNotFoundError,
)
from app.workflow.nodes.base import RunLogCollectorLike
from app.workflow.registry import RunLogCollector, RunResult, WorkflowRegistry

pytestmark = pytest.mark.unit


def make_log(node_name: str, *, timestamp: datetime | None = None) -> ExecutionLog:
    """Build a minimal ExecutionLog entry for collector/result tests."""
    return ExecutionLog(
        node_name=node_name,
        node_type="echo",
        timestamp=timestamp if timestamp is not None else datetime.now(),
        input_data={},
        output_data={},
        execution_time_ms=1.0,
    )


# --- TC1: RunResult / RunLogCollector ---------------------------------------


def test_run_result_frozen_and_duration() -> None:
    """RunResult is immutable and duration_ms derives from the two timestamps."""
    started = datetime(2026, 8, 11, 12, 0, 0)
    finished = started + timedelta(milliseconds=250)
    result = RunResult(
        workflow_id="wf_test",
        run_id="a" * 32,
        output={"done": True},
        execution_logs=[],
        started_at=started,
        finished_at=finished,
    )
    assert result.duration_ms == pytest.approx(250.0)
    with pytest.raises(FrozenInstanceError):
        result.workflow_id = "other"  # type: ignore[misc]


def test_collector_add_and_collect_sorted() -> None:
    """collect() returns a timestamp-sorted copy; the internal list is untouched."""
    collector = RunLogCollector(run_id="r1")
    late = make_log("late", timestamp=datetime(2026, 8, 11, 12, 0, 2))
    early = make_log("early", timestamp=datetime(2026, 8, 11, 12, 0, 1))
    collector.add(late)
    collector.add(early)
    sorted_logs = collector.collect()
    assert [log.node_name for log in sorted_logs] == ["early", "late"]
    # A second collect() call returns an independent copy.
    assert collector.collect() is not sorted_logs


def test_collector_is_run_log_collector_like() -> None:
    """RunLogCollector satisfies the run-scoped collector protocol (spec-03)."""
    assert isinstance(RunLogCollector(run_id="r1"), RunLogCollectorLike)


# --- TC2: register / query / delete (H7) ------------------------------------


def make_echo_definition(workflow_id: str = "wf_test", *, output: dict[str, Any] | None = None) -> WorkflowDefinition:
    """Two-node linear echo workflow: a -> b -> END (zero network, zero LLM)."""
    return WorkflowDefinition(
        workflow_id=workflow_id,
        entry_point="a",
        nodes=[
            NodeDefinition(name="a", type="echo", config={"output": output if output is not None else {"v": 1}}),
            NodeDefinition(name="b", type="echo", config={"output": {"v": 2}}),
        ],
        edges=[
            EdgeDefinition(source="a", target="b"),
            EdgeDefinition(source="b", target="END"),
        ],
        state_schema={"input": StateFieldSchema(type="str")},
        operator_logs={},
        execution_history=[],
    )


def test_register_and_get() -> None:
    """Register -> has/get/list stay consistent; missing operator_logs get generic empty schemas."""
    registry = WorkflowRegistry()
    workflow_id = registry.register_workflow(make_echo_definition())
    assert workflow_id == "wf_test"
    assert registry.has_workflow("wf_test")
    assert registry.list_workflows() == ["wf_test"]
    assert registry.get_workflow("wf_test") is not None
    # _ensure_operator_logs: generic empty schema, no node-type special-casing.
    operator_logs = registry.get_operator_logs("wf_test")
    assert set(operator_logs) == {"a", "b"}
    assert all(log.input_schema == {} and log.output_schema == {} for log in operator_logs.values())
    assert registry.get_registry_stats() == {"workflow_count": 1, "workflow_ids": ["wf_test"], "node_count": 2}


def test_delete_removes_all_three_maps() -> None:
    """delete_workflow drops _registry/_definitions/_nodes_map/_run_locks entries (H7/S13 white-box guard)."""
    registry = WorkflowRegistry()
    registry.register_workflow(make_echo_definition())
    registry._get_run_lock("wf_test")  # noqa: SLF001 — force lazy lock entry for the guard
    assert registry.delete_workflow("wf_test") is True
    assert "wf_test" not in registry._registry  # noqa: SLF001
    assert "wf_test" not in registry._definitions  # noqa: SLF001
    assert "wf_test" not in registry._nodes_map  # noqa: SLF001
    assert "wf_test" not in registry._run_locks  # noqa: SLF001


def test_delete_absent_returns_false() -> None:
    """Deleting an unknown id returns False without raising."""
    assert WorkflowRegistry().delete_workflow("missing") is False


def test_no_unregister_api() -> None:
    """H7 guard: the sole deletion entry is delete_workflow; no unregister_workflow exists."""
    assert hasattr(WorkflowRegistry(), "unregister_workflow") is False


def test_re_register_replaces_atomically() -> None:
    """Re-registering the same id swaps definition, nodes and compiled graph (S13)."""
    registry = WorkflowRegistry()
    registry.register_workflow(make_echo_definition(output={"version": 1}))
    old_graph = registry.get_workflow("wf_test")
    registry.register_workflow(make_echo_definition(output={"version": 2}))
    assert registry.list_workflows() == ["wf_test"]
    definition = registry.get_workflow_definition("wf_test")
    assert definition is not None
    assert definition.nodes[0].config["output"] == {"version": 2}
    assert registry.get_workflow("wf_test") is not old_graph
    assert registry.get_node_by_name("wf_test", "a") is not None


def test_query_lenient_returns_for_unknown_id() -> None:
    """Query APIs other than get_workflow return empty values for unknown ids."""
    registry = WorkflowRegistry()
    assert registry.get_workflow_definition("missing") is None
    assert registry.get_operator_logs("missing") == {}
    assert registry.get_operator_log_by_node("missing", "a") is None
    assert registry.get_execution_history("missing") == []
    assert registry.get_node_execution_history("missing", "a") == []
    assert registry.get_node_by_name("missing", "a") is None
    with pytest.raises(WorkflowNotFoundError):
        registry.get_workflow("missing")


def test_register_preserves_existing_operator_logs() -> None:
    """Declared operator_logs are kept verbatim; only missing nodes are filled."""
    definition = make_echo_definition()
    declared = OperatorLog(node_name="a", input_schema={"input": StateFieldSchema(type="str")}, output_schema={})
    definition.operator_logs = {"a": declared}
    registry = WorkflowRegistry()
    registry.register_workflow(definition)
    operator_logs = registry.get_operator_logs("wf_test")
    assert operator_logs["a"] is declared
    assert operator_logs["b"].input_schema == {}
    assert registry.get_operator_log_by_node("wf_test", "a") is declared
