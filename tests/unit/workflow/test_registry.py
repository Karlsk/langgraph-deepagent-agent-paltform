"""Unit tests for WorkflowRegistry runtime (spec-07, CONTRACT §4.10 / S10-S13)."""

from __future__ import annotations

from dataclasses import FrozenInstanceError
from datetime import datetime, timedelta

import pytest

from app.workflow.models import ExecutionLog
from app.workflow.nodes.base import RunLogCollectorLike
from app.workflow.registry import RunLogCollector, RunResult

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
