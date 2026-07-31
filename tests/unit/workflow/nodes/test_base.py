"""Unit tests for app.workflow.nodes.base (spec-03, CONTRACT §4.4, K4/H3/AD-11)."""

from typing import Any

import pytest
from langchain_core.runnables import Runnable, RunnableLambda

from app.workflow.models import ExecutionLog
from app.workflow.nodes.base import (
    BaseNode,
    RunLogCollectorLike,
    get_run_collector,
    set_run_collector,
)


class FakeNode(BaseNode):
    """Minimal concrete node used across spec-03 unit tests."""

    def build_runnable(self) -> Runnable:
        """Return a trivial passthrough runnable."""
        return self.wrap_runnable(lambda state: dict(state))

    def validate_config(self) -> bool:
        """Always-valid config."""
        return True


class FakeCollector:
    """Duck-typed run-level collector recording added logs."""

    def __init__(self) -> None:
        """Start with an empty log list."""
        self.logs: list[ExecutionLog] = []

    def add(self, log: ExecutionLog) -> None:
        """Record one execution log."""
        self.logs.append(log)


def _make_log(node_name: str = "n1") -> ExecutionLog:
    """Build a minimal ExecutionLog for tests."""
    return ExecutionLog(
        node_name=node_name,
        node_type="fake",
        input_data={},
        output_data={},
        execution_time_ms=1.0,
    )


@pytest.mark.unit
def test_base_node_is_abstract() -> None:
    """BaseNode cannot be instantiated directly (abstract build_runnable/validate_config)."""
    with pytest.raises(TypeError):
        BaseNode("n1", "fake", {})  # type: ignore[abstract]


@pytest.mark.unit
def test_init_stores_attributes() -> None:
    """Constructor stores name/node_type/config/operator_log and empty history."""
    config: dict[str, Any] = {"key": "value"}
    node = FakeNode("n1", "fake", config)
    assert node.name == "n1"
    assert node.node_type == "fake"
    assert node.config is config
    assert node.operator_log is None
    assert node.get_execution_history() == []


@pytest.mark.unit
def test_log_execution_appends_history_copy() -> None:
    """log_execution appends to history; get_execution_history returns a shallow copy."""
    node = FakeNode("n1", "fake", {})
    log = _make_log()
    node.log_execution(log)
    history = node.get_execution_history()
    assert history == [log]
    history.append(_make_log("other"))
    assert node.get_execution_history() == [log]


@pytest.mark.unit
def test_wrap_runnable_tags() -> None:
    """wrap_runnable wraps func with a RunnableLambda-backed runnable tagged by node name."""
    node = FakeNode("tagged", "fake", {})
    runnable = node.wrap_runnable(lambda state: {"echo": state})
    assert isinstance(runnable, Runnable)
    config = getattr(runnable, "config", {})
    assert config.get("tags") == ["tagged"]
    bound = getattr(runnable, "bound", None)
    assert isinstance(bound, RunnableLambda)
    assert runnable.invoke({"x": 1}) == {"echo": {"x": 1}}


@pytest.mark.unit
def test_clear_execution_history() -> None:
    """clear_execution_history empties the instance history."""
    node = FakeNode("n1", "fake", {})
    node.log_execution(_make_log())
    node.clear_execution_history()
    assert node.get_execution_history() == []


@pytest.mark.unit
def test_str_representation() -> None:
    """__str__ contains the concrete class name, node name and type."""
    node = FakeNode("n1", "fake", {})
    assert str(node) == "FakeNode(name=n1, type=fake)"


@pytest.mark.unit
def test_run_collector_roundtrip() -> None:
    """set_run_collector installs the collector; log_execution mirrors into it; token resets (H3)."""
    assert get_run_collector() is None
    collector = FakeCollector()
    token = set_run_collector(collector)
    try:
        assert get_run_collector() is collector
        node = FakeNode("n1", "fake", {})
        log = _make_log()
        node.log_execution(log)
        assert collector.logs == [log]
        assert node.get_execution_history() == [log]
    finally:
        set_run_collector(None)
        del token
    node2 = FakeNode("n2", "fake", {})
    node2.log_execution(_make_log("n2"))
    assert collector.logs == [log]


@pytest.mark.unit
def test_runtime_checkable_protocol() -> None:
    """RunLogCollectorLike is runtime-checkable and matched by duck-typed collectors."""
    assert isinstance(FakeCollector(), RunLogCollectorLike)

    class NotACollector:
        pass

    assert not isinstance(NotACollector(), RunLogCollectorLike)
