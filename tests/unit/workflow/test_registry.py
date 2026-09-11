"""Unit tests for WorkflowRegistry runtime (spec-07, CONTRACT §4.10 / S10-S13)."""

from __future__ import annotations

import uuid
from dataclasses import FrozenInstanceError
from datetime import datetime, timedelta
from pathlib import Path
from typing import Any, override

import pytest
from langchain_core.messages import AIMessage, HumanMessage
from langchain_core.runnables import Runnable

from app.workflow.cli import build_registry
from app.workflow.models import (
    EdgeDefinition,
    ExecutionLog,
    NodeDefinition,
    OperatorLog,
    StateFieldSchema,
    WorkflowDefinition,
    WorkflowNotFoundError,
)
from app.workflow.nodes.base import BaseNode, RunLogCollectorLike, get_run_collector
from app.workflow.nodes.factory import register_node_type
from app.workflow.registry import (
    RunLogCollector,
    RunResult,
    WorkflowRegistry,
    load_definitions_from_dir,
    synthesize_run_input,
)

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


# --- TC3: execute_workflow (H1 lock + H3 collector) --------------------------


def test_execute_returns_run_result() -> None:
    """Output correct, run_id is 32-hex, duration_ms >= 0, logs cover executed nodes."""
    registry = WorkflowRegistry()
    registry.register_workflow(make_echo_definition())
    result = registry.execute_workflow("wf_test", {"input": "hello"})
    assert isinstance(result, RunResult)
    assert result.workflow_id == "wf_test"
    assert len(result.run_id) == 32
    uuid.UUID(result.run_id)  # valid hex
    assert result.duration_ms >= 0
    assert result.output["a_result"] == {"v": 1}
    assert result.output["b_result"] == {"v": 2}
    assert {log.node_name for log in result.execution_logs} == {"a", "b"}
    assert len(result.execution_logs) == 2


def test_execute_unknown_workflow_raises() -> None:
    """Unknown workflow_id raises WorkflowNotFoundError (CONTRACT §5)."""
    with pytest.raises(WorkflowNotFoundError):
        WorkflowRegistry().execute_workflow("missing", {})


def test_collector_reset_after_run() -> None:
    """ContextVar must not leak: no collector active after execution (S11)."""
    registry = WorkflowRegistry()
    registry.register_workflow(make_echo_definition())
    registry.execute_workflow("wf_test", {})
    assert get_run_collector() is None


def test_execution_history_keeps_last_run() -> None:
    """definition.execution_history holds only the latest run's logs (S12)."""
    registry = WorkflowRegistry()
    registry.register_workflow(make_echo_definition(output={"v": 1}))
    registry.execute_workflow("wf_test", {})
    second = registry.execute_workflow("wf_test", {})
    history = registry.get_execution_history("wf_test")
    assert len(history) == 2
    assert all(log.node_name in {"a", "b"} for log in history)
    assert history == second.execution_logs  # single slot holds the latest run (S12)
    assert registry.get_node_execution_history("wf_test", "a") == [log for log in history if log.node_name == "a"]


class _FailNode(BaseNode):
    """Test-only node that raises during execution (EXP-G7 propagation guard)."""

    @override
    def build_runnable(self) -> Runnable:
        def func(state: Any) -> dict[str, Any]:
            raise RuntimeError("boom from fail node")

        return self.wrap_runnable(func)

    @override
    def validate_config(self) -> bool:
        return True


def test_execute_failure_reraises_and_resets_collector() -> None:
    """Node exceptions propagate unchanged (EXP-G7) and the ContextVar is still reset."""
    register_node_type("boom", _FailNode)
    definition = WorkflowDefinition(
        workflow_id="wf_fail",
        entry_point="bad",
        nodes=[NodeDefinition(name="bad", type="boom", config={})],
        edges=[EdgeDefinition(source="bad", target="END")],
        state_schema={},
        operator_logs={},
        execution_history=[],
    )
    registry = WorkflowRegistry()
    registry.register_workflow(definition)
    with pytest.raises(RuntimeError, match="boom from fail node"):
        registry.execute_workflow("wf_fail", {})
    assert get_run_collector() is None


# --- TC4: load_definitions_from_dir ----------------------------------------

_YAML_TEMPLATE = """workflow_id: {workflow_id}
entry_point: n
nodes:
  - name: n
    type: echo
state_schema: {{}}
"""


def test_load_definitions_from_dir(tmp_path: Path) -> None:
    """Recursive scan of *.yaml/*.yml sorted by file name; other extensions ignored."""
    (tmp_path / "b.yml").write_text(_YAML_TEMPLATE.format(workflow_id="wf_b"))
    (tmp_path / "notes.txt").write_text("not a workflow")
    sub = tmp_path / "sub"
    sub.mkdir()
    (sub / "a.yaml").write_text(_YAML_TEMPLATE.format(workflow_id="wf_a"))
    definitions = load_definitions_from_dir(tmp_path)
    assert [definition.workflow_id for definition in definitions] == ["wf_a", "wf_b"]


def test_load_definitions_bad_file_fails_fast(tmp_path: Path) -> None:
    """A file failing validation raises ValueError carrying the file path (fail fast)."""
    bad = tmp_path / "bad.yaml"
    bad.write_text("workflow_id: wf_bad\nentry_point: n\n")  # missing nodes/state_schema
    with pytest.raises(ValueError, match=str(bad)):
        load_definitions_from_dir(tmp_path)


def test_load_definitions_empty_dir(tmp_path: Path) -> None:
    """Empty (or missing) directory yields an empty list."""
    assert load_definitions_from_dir(tmp_path / "nowhere") == []
    assert load_definitions_from_dir(tmp_path) == []


# ---------------------------------------------------------------------------
# S20: chat_model_factory injection reaches LLM nodes through the build chain
# ---------------------------------------------------------------------------


def make_llm_definition(workflow_id: str = "wf_provider", *, provider_ref: str | None = None) -> WorkflowDefinition:
    """Single LLM node workflow used to assert factory plumbing (zero real LLM calls)."""
    config: dict[str, Any] = {"model_name": "gpt-4o-mini"}
    if provider_ref is not None:
        config["provider_ref"] = provider_ref
    return WorkflowDefinition(
        workflow_id=workflow_id,
        entry_point="ask",
        nodes=[NodeDefinition(name="ask", type="llm", config=config)],
        edges=[EdgeDefinition(source="ask", target="END")],
        state_schema={"messages": StateFieldSchema(type="list")},
        operator_logs={},
        execution_history=[],
    )


class _RecordingClient:
    """Minimal chat client stand-in returned by the injected factory."""

    def invoke(self, messages: list[Any]) -> Any:
        """Return a fixed AI message so the run completes without network."""
        return AIMessage(content="from-injected-factory")


def test_factory_reaches_llm_node_via_registry() -> None:
    """Registry -> GraphBuilder -> create_node -> LLMNode carries the injected factory (S20)."""
    seen: list[tuple[str, dict[str, Any]]] = []

    def factory(ref: str, overrides: dict[str, Any]) -> _RecordingClient:
        seen.append((ref, overrides))
        return _RecordingClient()

    registry = WorkflowRegistry(chat_model_factory=factory)
    registry.register_workflow(make_llm_definition(provider_ref="acme/gpt-4o"))

    node = registry.get_node_by_name("wf_provider", "ask")
    assert node is not None
    # Not yet called: the client is lazy (K10)
    assert seen == []

    registry.execute_workflow("wf_provider", {"messages": [HumanMessage(content="hi")]})

    assert seen == [("acme/gpt-4o", {"temperature": 0.7})]


def test_registry_without_factory_leaves_node_env_path() -> None:
    """No factory injected -> the node keeps the env path, so existing YAML is unaffected (S20)."""
    registry = WorkflowRegistry()
    registry.register_workflow(make_llm_definition())

    node = registry.get_node_by_name("wf_provider", "ask")
    assert node is not None
    assert getattr(node, "_chat_model_factory", "missing") is None


def test_build_registry_forwards_factory(tmp_path: Path) -> None:
    """cli.build_registry forwards the factory so the composition root can inject it (S20)."""
    sentinel = object()
    registry = build_registry(tmp_path, user_dir=tmp_path, chat_model_factory=sentinel)  # type: ignore[arg-type]
    assert registry._builder._chat_model_factory is sentinel  # noqa: SLF001


# --- TC: S21 run-input synthesis --------------------------------------------


class _MessageRecordingClient:
    """Chat client stand-in capturing the message list handed to invoke()."""

    def __init__(self) -> None:
        self.seen_messages: list[Any] = []

    def invoke(self, messages: list[Any]) -> Any:
        self.seen_messages = list(messages)
        return AIMessage(content="ok")


def test_synthesize_str_input_adds_messages() -> None:
    """S21 ②: a non-empty str `input` is additively synthesized into a user message."""
    definition = make_llm_definition()
    result = synthesize_run_input(definition, {"input": "hello", "user_id": "u1"})
    assert result == {
        "input": "hello",
        "user_id": "u1",
        "messages": [{"role": "user", "content": "hello"}],
    }


def test_synthesize_skips_when_messages_present() -> None:
    """S21 ①: explicit messages win and are passed through untouched."""
    definition = make_llm_definition()
    given = {"input": "hello", "messages": [{"role": "user", "content": "explicit"}]}
    assert synthesize_run_input(definition, given) == given


def test_synthesize_noop_for_dict_or_missing_input() -> None:
    """S21 ③: missing / non-str / blank `input` never synthesizes messages."""
    definition = make_llm_definition()
    for payload in ({}, {"input": {"a": 1}}, {"input": ""}, {"input": "   "}, {"input": 42}):
        assert synthesize_run_input(definition, payload) == payload


def test_synthesize_does_not_mutate_input() -> None:
    """synthesize_run_input is pure: the caller's dict is never modified (S21)."""
    definition = make_llm_definition()
    payload = {"input": "hello"}
    snapshot = dict(payload)
    result = synthesize_run_input(definition, payload)
    assert payload == snapshot
    assert result is not payload


def test_execute_workflow_synthesizes_messages_for_llm_node() -> None:
    """S21: execute_workflow synthesizes before invoke, so LLMNode sees the user message."""
    client = _MessageRecordingClient()
    registry = WorkflowRegistry(chat_model_factory=lambda ref, overrides: client)
    registry.register_workflow(make_llm_definition(provider_ref="acme/gpt-4o"))

    registry.execute_workflow("wf_provider", {"input": "hello"})

    assert client.seen_messages == [{"role": "user", "content": "hello"}]
