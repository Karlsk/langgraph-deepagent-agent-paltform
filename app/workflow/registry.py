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
from contextvars import ContextVar
from dataclasses import dataclass
from datetime import datetime
from pathlib import Path
from typing import Any, Literal

import structlog
from pydantic import ValidationError

from app.workflow.graph_builder import GraphBuilder
from app.workflow.models import (
    ExecutionLog,
    NestedWorkflowError,
    OperatorLog,
    WorkflowDefinition,
    WorkflowNotFoundError,
    load_definition_from_yaml,
)
from app.workflow.nodes.base import (  # noqa: SLF001 — token reset per S11
    _RUN_COLLECTOR,
    BaseNode,
    get_run_collector,
    set_run_collector,
)
from app.workflow.ports import ChatModelFactory

logger = structlog.get_logger(__name__)

# Workflows currently executing, outermost first (S23). Pushed by execute_workflow
# and reset in its finally, the same paired discipline as _RUN_COLLECTOR (S11), so
# it never leaks between runs and never crosses threads or coroutines.
_RUN_STACK: ContextVar[tuple[str, ...]] = ContextVar("workflow_run_stack", default=())


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


def synthesize_run_input(definition: WorkflowDefinition, input_data: dict[str, Any]) -> dict[str, Any]:
    """Derive the ``messages`` channel from a bare string ``input`` (S21).

    A truthy ``messages`` wins and passes through as-is; otherwise a non-empty
    string ``input`` is additively turned into one user message. Any other
    shape is a no-op. Applies to every workflow regardless of node types (R2):
    LangGraph drops keys absent from the state schema. Returns a new dict,
    never mutates ``input_data``, never removes a caller-supplied key.
    """
    if input_data.get("messages"):
        return input_data

    source = input_data.get("input")
    if not isinstance(source, str) or not source.strip():
        return input_data

    logger.debug(
        "run_input_synthesized",
        workflow_id=definition.workflow_id,
        input_keys=sorted(input_data),
    )
    return {**input_data, "messages": [{"role": "user", "content": source}]}


class WorkflowRegistry:
    """Process-level registry of compiled workflows (CONTRACT §4.10).

    Concurrency model (S10, ADR-004): a per-workflow RLock serializes
    ``execute_workflow`` calls of the same workflow while different workflows
    run in parallel; ``_meta_lock`` guards lazy lock-table creation and the
    register/delete map mutations. Run-scoped log collection via the
    ``_RUN_COLLECTOR`` ContextVar isolates logs per run/task as a second line
    of defense (D3, H1).
    """

    def __init__(
        self,
        *,
        no_match_policy: Literal["raise", "default"] = "raise",
        chat_model_factory: ChatModelFactory | None = None,
        max_nesting_depth: int = 3,
    ) -> None:
        """Create an empty registry with the given condition-router no-match policy.

        ``max_nesting_depth`` (S23) counts the workflows allowed on the run stack
        **at once, outermost included**, so the default 3 runs ``a -> b -> c`` and
        rejects a fourth. It is registry-level on purpose: a per-node override
        would make the global maximum uninferrable and the guard worthless.

        The builder receives this registry's own bound ``_run_nested`` as its
        ``workflow_runner`` — self-injection, which is why the composition root
        (``app/main.py``, ``cli.py``) needs no wiring for nesting.
        """
        self._registry: dict[str, Any] = {}
        self._definitions: dict[str, WorkflowDefinition] = {}
        self._nodes_map: dict[str, dict[str, BaseNode]] = {}
        self._run_locks: dict[str, threading.RLock] = {}
        self._meta_lock = threading.RLock()
        self._max_nesting_depth = max_nesting_depth
        self._builder = GraphBuilder(
            no_match_policy=no_match_policy,
            chat_model_factory=chat_model_factory,
            workflow_runner=self._run_nested,
        )

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

        The caller payload goes through ``synthesize_run_input`` (S21) before
        the graph runs. Log collection is run-scoped (S11): a fresh
        RunLogCollector is bound to the ContextVar and reset in finally, so the
        ContextVar never leaks. Node exceptions propagate to the caller
        unchanged (EXP-G7). The definition's execution_history keeps only the
        latest run (S12, bounded).

        The workflow id is also pushed onto ``_RUN_STACK`` for the duration of the
        run and popped in the finally (S23), which is what lets a nested call see
        its callers and reject cycles or excess depth before recursing.
        """
        workflow = self.get_workflow(workflow_id)
        definition = self._definitions[workflow_id]
        run_lock = self._get_run_lock(workflow_id)
        stack_token = _RUN_STACK.set((*_RUN_STACK.get(), workflow_id))
        try:
            with run_lock:
                run_id = uuid.uuid4().hex
                collector = RunLogCollector(run_id)
                started_at = datetime.now()
                token = set_run_collector(collector)
                try:
                    output = workflow.invoke(synthesize_run_input(definition, input_data))
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
        finally:
            _RUN_STACK.reset(stack_token)  # paired set/reset (S11/S23)

    # -- nesting (S23/S24) ----------------------------------------------------

    def _run_nested(self, workflow_id: str, input_data: dict[str, Any], caller_label: str) -> dict[str, Any]:
        """Execute a referenced workflow on behalf of a ``subworkflow`` node.

        This bound method is the ``WorkflowRunner`` the builder injects, so the
        node never learns what a registry is (red line 2) and the compiled graph
        holds no registry snapshot (H5).

        Guards run **before** recursing, so a cycle or excess depth is refused
        rather than discovered as a ``RecursionError`` — which is not part of the
        ``WorkflowEngineError`` family and would escape the CLI's classification.
        Recursing through ``execute_workflow`` means the inner run inherits its
        own per-id RLock, its own log collector and S21 input synthesis: nesting
        adds guards and log orchestration, never a second execution path.

        Returns:
            The frozen three-key envelope ``{"output", "run_id", "inner_log_count"}``
            (CONTRACT §4.15). ``output`` is the inner workflow's whole final state.

        Raises:
            NestedWorkflowError: If the id is already on the run stack (a cycle,
                self-reference included) or the depth limit is reached.
            WorkflowNotFoundError: If the referenced workflow is not registered —
                existence is deliberately a runtime check (S18), so saving an
                outer workflow before its inner one is allowed.
        """
        stack = _RUN_STACK.get()
        path = " -> ".join((*stack, workflow_id))
        if workflow_id in stack:
            msg = f"nested workflow cycle detected: {path}"
            raise NestedWorkflowError(msg)
        if len(stack) >= self._max_nesting_depth:
            msg = f"nested workflow depth limit {self._max_nesting_depth} exceeded: {path}"
            raise NestedWorkflowError(msg)

        result = self.execute_workflow(workflow_id, input_data)
        self._merge_inner_logs(result.execution_logs, caller_label)
        logger.debug(
            "nested_workflow_completed",
            workflow_id=workflow_id,
            caller=caller_label,
            inner_run_id=result.run_id,
            inner_log_count=len(result.execution_logs),
            depth=len(stack) + 1,
        )
        return {
            "output": result.output,
            "run_id": result.run_id,
            "inner_log_count": len(result.execution_logs),
        }

    @staticmethod
    def _merge_inner_logs(inner_logs: list[ExecutionLog], caller_label: str) -> None:
        """Copy the inner logs into the outer collector, prefixed with the caller (S24).

        Prefixes compose naturally, so no per-layer special-casing is needed: C's
        log reaches B as ``sub_c/c1`` and then reaches A as ``sub_b/sub_c/c1``.
        """
        outer = get_run_collector()
        # Narrowing for pyright: inside a run the outer collector always exists (S11).
        if outer is not None:
            for log in inner_logs:
                outer.add(log.model_copy(update={"node_name": f"{caller_label}/{log.node_name}"}))

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


def load_definitions_from_dir(directory: str | Path) -> list[WorkflowDefinition]:
    """Recursively load workflow definitions from *.yaml/*.yml files.

    Files are parsed in file-name order (full path breaks ties). Any single
    file failure raises ValueError carrying the file path (fail fast); an
    empty or missing directory logs a warning and returns an empty list.
    """
    root = Path(directory)
    paths = sorted([*root.rglob("*.yaml"), *root.rglob("*.yml")], key=lambda path: (path.name, str(path)))
    if not paths:
        logger.warning("no_workflow_definitions_found", directory=str(root))
        return []
    definitions: list[WorkflowDefinition] = []
    for path in paths:
        try:
            definitions.append(load_definition_from_yaml(path))
        except ValidationError as exc:
            raise ValueError(f"failed to load workflow definition from {path}: {exc}") from exc
    return definitions
