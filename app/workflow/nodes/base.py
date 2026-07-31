"""Node infrastructure: abstract BaseNode and run-level log collector (spec-03).

CONTRACT §4.4: BaseNode is the abstract contract for every workflow node.
The build_runnable() output is the single execution unit (K4). Run-level
execution logs are mirrored into a ContextVar-scoped collector (H3) whose
implementation lands in Phase 7.

Dependency red-line: no network / model invocation logic lives here.
"""

from __future__ import annotations

from abc import ABC, abstractmethod
from collections.abc import Callable
from contextvars import ContextVar, Token
from typing import Any, Protocol, override, runtime_checkable

from langchain_core.runnables import Runnable, RunnableLambda

from app.workflow.models import ExecutionLog, NodeType, OperatorLog


@runtime_checkable
class RunLogCollectorLike(Protocol):
    """运行级日志收集器接口（Phase 7 提供实现，H3）."""

    def add(self, log: ExecutionLog) -> None: ...  # noqa: D102 — protocol stub per CONTRACT §4.4


_RUN_COLLECTOR: ContextVar[RunLogCollectorLike | None] = ContextVar("workflow_run_collector", default=None)


def set_run_collector(collector: RunLogCollectorLike | None) -> Token[RunLogCollectorLike | None]:
    """设置当前运行级收集器，返回复位 token."""
    return _RUN_COLLECTOR.set(collector)


def get_run_collector() -> RunLogCollectorLike | None:
    """获取当前运行级收集器（无则 None）."""
    return _RUN_COLLECTOR.get()


class BaseNode(ABC):
    """Abstract base class for all workflow nodes (CONTRACT §4.4)."""

    name: str
    node_type: NodeType | str
    config: dict[str, Any]
    operator_log: OperatorLog | None
    _execution_history: list[ExecutionLog]

    def __init__(
        self,
        name: str,
        node_type: NodeType | str,
        config: dict[str, Any],
        operator_log: OperatorLog | None = None,
    ) -> None:
        """Store node identity, config and optional operator log."""
        self.name = name
        self.node_type = node_type
        self.config = config
        self.operator_log = operator_log
        self._execution_history = []

    @abstractmethod
    def build_runnable(self) -> Runnable:
        """唯一执行单元（K4）."""

    @abstractmethod
    def validate_config(self) -> bool:
        """配置非法时抛 ValueError."""

    def log_execution(self, execution_log: ExecutionLog) -> None:
        """写实例历史 + 当前运行级收集器（若存在）."""
        self._execution_history.append(execution_log)
        collector = get_run_collector()
        if collector is not None:
            collector.add(execution_log)

    def get_execution_history(self) -> list[ExecutionLog]:
        """返回执行历史的浅拷贝."""
        return list(self._execution_history)

    def clear_execution_history(self) -> None:
        """仅调试用：清空实例历史."""
        self._execution_history.clear()

    def wrap_runnable(self, func: Callable[[dict[str, Any]], dict[str, Any]]) -> RunnableLambda:
        """统一包装：RunnableLambda(func).with_config(tags=[self.name])."""
        return RunnableLambda(func).with_config(tags=[self.name])  # pyright: ignore[reportReturnType] — with_config returns RunnableBinding at runtime (AD-11)

    @override
    def __str__(self) -> str:
        """Human-readable node identity."""
        return f"{type(self).__name__}(name={self.name}, type={self.node_type})"
