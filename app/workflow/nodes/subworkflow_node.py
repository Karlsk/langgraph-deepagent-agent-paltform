"""``subworkflow`` node: composition through an injected runner (S23/S24, CONTRACT §4.15).

Lets one workflow call another. The node never sees the registry: it holds an
opaque ``WorkflowRunner`` callable that ``WorkflowRegistry`` injects with its own
bound ``_run_nested``, which owns the cycle and depth guards (S23) and the
prefixed inner-log merge (S24). Keeping the registry out of ``nodes/*`` is red
line 2, and it is what dissolves the H5 build-time-snapshot hazard that had this
feature deferred.

The runner returns a frozen three-key envelope ``{"output", "run_id",
"inner_log_count"}``. Only ``output`` — the inner workflow's whole final state —
goes onward, and it is written with ``dual_write=False``: flattening a whole
inner state would let its channels silently overwrite same-named outer ones,
``input`` above all, which every workflow declares. Inner data stays reachable at
``{node_name}_result`` via S7 dot paths.

Follows the R3 pipeline and logs summaries only (S24/H6/S15).
"""

from __future__ import annotations

import time
from typing import Any, override

import structlog
from langchain_core.runnables import Runnable
from pydantic import BaseModel, Field, field_validator

from app.workflow.models import ConfigError, ExecutionLog, OperatorLog
from app.workflow.nodes.base import BaseNode
from app.workflow.nodes.factory import register_node_type
from app.workflow.ports import WorkflowRunner
from app.workflow.utils import convert_state_to_dict, map_output_to_state

logger = structlog.get_logger(__name__)


def _resolve_path(state_dict: dict[str, Any], path: str) -> Any:
    """Dot-path lookup; hitting a non-dict mid-way resolves to None (S7 semantics)."""
    current: Any = state_dict
    for part in path.split("."):
        if not isinstance(current, dict):
            return None
        current = current.get(part)
    return current


class SubWorkflowNodeConfig(BaseModel, extra="forbid"):
    """Reference to another registered workflow (S14 forbid extras)."""

    workflow_id: str
    input_map: dict[str, str] = Field(default_factory=dict)
    inherit_input: bool = False

    @field_validator("workflow_id")
    @classmethod
    def _reject_blank(cls, value: str) -> str:
        """A blank id would only fail much later, at run time, so it is rejected here."""
        stripped = value.strip()
        if not stripped:
            msg = "SubWorkflowNodeConfig.workflow_id must be a non-empty string"
            raise ValueError(msg)
        return stripped


class SubWorkflowNode(BaseNode):
    """Runs another registered workflow; plugin type ``subworkflow`` (K5/R4)."""

    def __init__(
        self,
        name: str,
        config: dict[str, Any] | SubWorkflowNodeConfig,
        node_type: str = "subworkflow",
        operator_log: OperatorLog | None = None,
        workflow_runner: WorkflowRunner | None = None,
    ) -> None:
        """Validate config and the runner up front, so a mis-assembly fails at build time (S6)."""
        node_config = config if isinstance(config, SubWorkflowNodeConfig) else SubWorkflowNodeConfig(**config)
        if workflow_runner is None:
            msg = (
                f"SubWorkflowNode '{name}' requires a workflow_runner, which WorkflowRegistry injects. "
                "Building this node without one is an assembly error, not a runtime condition."
            )
            raise ConfigError(msg)
        super().__init__(name, node_type, node_config.model_dump(), operator_log)
        self._node_config = node_config
        self._workflow_runner = workflow_runner

    @override
    def validate_config(self) -> bool:
        """Config was validated in __init__; kept for the BaseNode contract (K4)."""
        return True

    @override
    def build_runnable(self) -> Runnable:
        """唯一执行单元（K4）：R3 标准进出管线，出口关平铺双写（§4.15）."""

        def func(state: dict[str, Any]) -> dict[str, Any]:
            started = time.perf_counter()
            state_dict = convert_state_to_dict(state)
            envelope: dict[str, Any] = {}
            output: dict[str, Any] = {}
            try:
                envelope = self._workflow_runner(
                    self._node_config.workflow_id,
                    self._build_inner_input(state_dict),
                    self.name,
                )
                output = envelope["output"]
                self._log(output, envelope, (time.perf_counter() - started) * 1000, error=None)
            except Exception as exc:
                # 异常分支：记录后重抛（H2/R6，禁止死 except）
                self._log(output, envelope, (time.perf_counter() - started) * 1000, error=str(exc))
                logger.exception("subworkflow_node_execution_failed", node=self.name, error=str(exc))
                raise
            return map_output_to_state(self.name, output, state_dict, dual_write=False)

        return self.wrap_runnable(func)

    def _build_inner_input(self, state_dict: dict[str, Any]) -> dict[str, Any]:
        """Assemble what the inner workflow receives; never mutates the outer state (R3/S5)."""
        inner: dict[str, Any] = dict(state_dict) if self._node_config.inherit_input else {}
        for inner_key, outer_path in self._node_config.input_map.items():
            value = _resolve_path(state_dict, outer_path)
            # A dangling path leaves the key out so the inner workflow falls back to its
            # declared S14 default, rather than failing the outer run over a rename.
            if value is None:
                continue
            inner[inner_key] = value
        return inner

    def _log(
        self,
        output: dict[str, Any],
        envelope: dict[str, Any],
        execution_time_ms: float,
        error: str | None,
    ) -> None:
        """Summary only (S24): the inner payload and the inner logs are never embedded here."""
        cfg = self._node_config
        self.log_execution(
            ExecutionLog(
                node_name=self.name,
                node_type=str(self.node_type),
                input_data={
                    "workflow_id": cfg.workflow_id,
                    "inherit_input": cfg.inherit_input,
                    "mapped_keys": sorted(cfg.input_map),
                },
                output_data={
                    "output_keys": sorted(output),
                    "run_id": envelope.get("run_id"),
                    "duration_ms": execution_time_ms,
                    "inner_log_count": envelope.get("inner_log_count", 0),
                },
                execution_time_ms=execution_time_ms,
                error=error,
            )
        )


# 模块底部自注册（K5 插件路径；factory 底部 import 本模块触发注册，R4 不加内置分支）
register_node_type("subworkflow", SubWorkflowNode)
