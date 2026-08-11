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
import uuid
from dataclasses import dataclass
from datetime import datetime
from typing import Any, Literal

import structlog

from app.workflow.graph_builder import GraphBuilder
from app.workflow.models import (
    ExecutionLog,
    OperatorLog,
    WorkflowDefinition,
    WorkflowNotFoundError,
)
from app.workflow.nodes.base import _RUN_COLLECTOR, BaseNode, set_run_collector  # noqa: SLF001 — token reset per S11

logger = structlog.get_logger(__name__)


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


class WorkflowRegistry:
    """Process-level registry of compiled workflows (CONTRACT §4.10).

    Concurrency model (S10, ADR-004): a per-workflow RLock serializes
    ``execute_workflow`` calls of the same workflow while different workflows
    run in parallel; ``_meta_lock`` guards lazy lock-table creation and the
    register/delete map mutations. Run-scoped log collection via the
    ``_RUN_COLLECTOR`` ContextVar isolates logs per run/task as a second line
    of defense (D3, H1).
    """

    def __init__(self, *, no_match_policy: Literal["raise", "default"] = "raise") -> None:
        """Create an empty registry with the given condition-router no-match policy."""
        self._registry: dict[str, Any] = {}
        self._definitions: dict[str, WorkflowDefinition] = {}
        self._nodes_map: dict[str, dict[str, BaseNode]] = {}
        self._run_locks: dict[str, threading.RLock] = {}
        self._meta_lock = threading.RLock()
        self._builder = GraphBuilder(no_match_policy=no_match_policy)

    # -- registration ---------------------------------------------------------

    def register_workflow(
        self,
        definition: WorkflowDefinition,
        *,
        default_edges: dict[str, str] | None = None,
    ) -> str:
        """Compile and store a workflow; re-registration atomically replaces it (S13).

        Missing operator_logs entries are filled with generic empty schemas
        (no node-type special-casing, replacing the legacy domain branching).
        """
        self._ensure_operator_logs(definition)
        result = self._builder.build_graph(definition, default_edges=default_edges)
        workflow_id = definition.workflow_id
        with self._meta_lock:
            if workflow_id in self._registry:
                self.delete_workflow(workflow_id)
            self._registry[workflow_id] = result.compiled_graph
            self._definitions[workflow_id] = definition
            self._nodes_map[workflow_id] = result.nodes_map
        return workflow_id

    def delete_workflow(self, workflow_id: str) -> bool:
        """Sole deletion entry (C6/H7): drop all four internal map entries atomically."""
        with self._meta_lock:
            if workflow_id not in self._registry:
                return False
            del self._registry[workflow_id]
            self._definitions.pop(workflow_id, None)
            self._nodes_map.pop(workflow_id, None)
            self._run_locks.pop(workflow_id, None)
        return True

    # -- core access ----------------------------------------------------------

    def get_workflow(self, workflow_id: str) -> Any:
        """Return the compiled graph; raise WorkflowNotFoundError for unknown ids."""
        workflow = self._registry.get(workflow_id)
        if workflow is None:
            raise WorkflowNotFoundError(f"workflow not found: {workflow_id}")
        return workflow

    def has_workflow(self, workflow_id: str) -> bool:
        """Check whether a workflow id is registered."""
        return workflow_id in self._registry

    def list_workflows(self) -> list[str]:
        """Return registered workflow ids in sorted order."""
        return sorted(self._registry)

    def execute_workflow(self, workflow_id: str, input_data: dict[str, Any]) -> RunResult:
        """Run one workflow under its per-workflow RLock and return the RunResult.

        Log collection is run-scoped (S11): a fresh RunLogCollector is bound to
        the ContextVar and reset in finally, so the ContextVar never leaks.
        Node exceptions propagate to the caller unchanged (EXP-G7). The
        definition's execution_history keeps only the latest run (S12, bounded).
        """
        workflow = self.get_workflow(workflow_id)
        definition = self._definitions[workflow_id]
        run_lock = self._get_run_lock(workflow_id)
        with run_lock:
            run_id = uuid.uuid4().hex
            collector = RunLogCollector(run_id)
            started_at = datetime.now()
            token = set_run_collector(collector)
            try:
                output = workflow.invoke(input_data)
            except Exception:
                logger.exception("workflow_execution_failed", workflow_id=workflow_id, run_id=run_id)
                raise
            finally:
                _RUN_COLLECTOR.reset(token)  # noqa: SLF001 — paired set/reset (S11)
            finished_at = datetime.now()
            logs = collector.collect()
            definition.execution_history = logs
            return RunResult(
                workflow_id=workflow_id,
                run_id=run_id,
                output=output,
                execution_logs=logs,
                started_at=started_at,
                finished_at=finished_at,
            )

    # -- queries --------------------------------------------------------------

    def get_workflow_definition(self, workflow_id: str) -> WorkflowDefinition | None:
        """Return the stored definition, or None for unknown ids."""
        return self._definitions.get(workflow_id)

    def get_operator_logs(self, workflow_id: str) -> dict[str, OperatorLog]:
        """Return the definition's operator logs (empty dict for unknown ids)."""
        definition = self._definitions.get(workflow_id)
        return dict(definition.operator_logs) if definition is not None else {}

    def get_operator_log_by_node(self, workflow_id: str, node_name: str) -> OperatorLog | None:
        """Return one node's operator log, or None when absent."""
        return self.get_operator_logs(workflow_id).get(node_name)

    def get_execution_history(self, workflow_id: str) -> list[ExecutionLog]:
        """Return a copy of the last-run execution history (empty for unknown ids)."""
        definition = self._definitions.get(workflow_id)
        return list(definition.execution_history) if definition is not None else []

    def get_node_execution_history(self, workflow_id: str, node_name: str) -> list[ExecutionLog]:
        """Return last-run history entries filtered by node name."""
        return [log for log in self.get_execution_history(workflow_id) if log.node_name == node_name]

    def get_node_by_name(self, workflow_id: str, node_name: str) -> BaseNode | None:
        """Return the node instance for a registered workflow, or None when absent."""
        return self._nodes_map.get(workflow_id, {}).get(node_name)

    def get_registry_stats(self) -> dict[str, Any]:
        """Return registry-level counters and ids."""
        return {
            "workflow_count": len(self._registry),
            "workflow_ids": self.list_workflows(),
            "node_count": sum(len(nodes) for nodes in self._nodes_map.values()),
        }

    # -- internals ------------------------------------------------------------

    def _ensure_operator_logs(self, definition: WorkflowDefinition) -> None:
        """Fill missing operator_logs with generic empty schemas (no type branching)."""
        for node in definition.nodes:
            if node.name not in definition.operator_logs:
                definition.operator_logs[node.name] = OperatorLog(
                    node_name=node.name,
                    input_schema={},
                    output_schema={},
                )

    def _get_run_lock(self, workflow_id: str) -> threading.RLock:
        """Lazily create the per-workflow run lock under _meta_lock (S10)."""
        with self._meta_lock:
            if workflow_id not in self._run_locks:
                self._run_locks[workflow_id] = threading.RLock()
            return self._run_locks[workflow_id]
