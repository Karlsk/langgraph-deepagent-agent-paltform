"""Workflow registry runtime: thread-safe registry and run-scoped log collection (spec-07).

CONTRACT §4.10: WorkflowRegistry owns compiled graphs, definitions and node
maps with per-workflow RLock serialization of ``execute_workflow`` (H1/S10:
same workflow runs serially, different workflows run in parallel). Run-level
logs flow through a run-scoped ``RunLogCollector`` propagated via the
``_RUN_COLLECTOR`` ContextVar set/reset in try/finally (S11); the runtime
never relies on clearing shared node instances to collect logs (H1).
``delete_workflow`` is the sole deletion entry keeping the internal maps in
lock-step (C6/H7/S13).

Dependency red-line: stdlib + app.workflow internals only; never import
app.core.* (AD-02).
"""

from __future__ import annotations

import threading
from dataclasses import dataclass
from datetime import datetime
from typing import Any

from app.workflow.models import ExecutionLog


@dataclass(frozen=True)
class RunResult:
    """Immutable outcome of a single workflow run (one instance per run)."""

    workflow_id: str
    run_id: str
    output: dict[str, Any]
    execution_logs: list[ExecutionLog]
    started_at: datetime
    finished_at: datetime

    @property
    def duration_ms(self) -> float:
        """Wall-clock duration of the run in milliseconds."""
        return (self.finished_at - self.started_at).total_seconds() * 1000.0


class RunLogCollector:
    """Run-scoped execution-log collector (H1/H3, implements RunLogCollectorLike).

    Decoupled from shared node instances: nodes mirror each ``log_execution``
    entry into the active collector via the ContextVar hook, so coverage spans
    every node actually executed in this run regardless of creation path (H3).
    """

    def __init__(self, run_id: str) -> None:
        """Bind the collector to one run identifier."""
        self.run_id = run_id
        self._logs: list[ExecutionLog] = []
        self._lock = threading.Lock()

    def add(self, log: ExecutionLog) -> None:
        """Append one log entry; collection itself is thread-safe."""
        with self._lock:
            self._logs.append(log)

    def collect(self) -> list[ExecutionLog]:
        """Return a timestamp-sorted copy of all collected logs."""
        with self._lock:
            return sorted(self._logs, key=lambda log: log.timestamp)
