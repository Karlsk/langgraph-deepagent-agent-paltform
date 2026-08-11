"""Generic ``python`` code node: in-process trusted code execution (K5 plugin).

The only non-built-in node type shipped with the engine. Config is exactly
one of:

- ``code``: inline YAML code, wrapped into a function with ``state`` (a plain
  dict snapshot of the workflow state) injected; must ``return`` a dict.
- ``entry``: ``module:function`` pointing at a repository function invoked
  with the state dict; must return a dict.

Execution is in-process and NOT sandboxed: only trusted, repository-owned
code may run here (langchain-sandbox was evaluated and rejected: unmaintained,
Deno runtime, per-invocation startup latency). Follows the R3 pipeline
(convert_state_to_dict in / map_output_to_state out) and logs summaries only
(code length or entry name — never the code body, H6/S15).
"""

from __future__ import annotations

import importlib
import textwrap
import time
from typing import Any, override

import structlog
from langchain_core.runnables import Runnable
from pydantic import BaseModel, model_validator

from app.workflow.models import ExecutionLog, OperatorLog, PythonNodeError
from app.workflow.nodes.base import BaseNode
from app.workflow.nodes.factory import register_node_type
from app.workflow.utils import convert_state_to_dict, map_output_to_state

logger = structlog.get_logger(__name__)


class PythonNodeConfig(BaseModel, extra="forbid"):
    """Exactly one of ``code`` / ``entry`` must be provided (S14 forbid extras)."""

    code: str | None = None
    entry: str | None = None

    @model_validator(mode="after")
    def _check_exclusivity(self) -> PythonNodeConfig:
        if (self.code is None) == (self.entry is None):
            msg = "PythonNodeConfig requires exactly one of 'code' or 'entry'"
            raise ValueError(msg)
        return self


class PythonNode(BaseNode):
    """Generic code-execution node registered as plugin type ``python`` (K5/R4)."""

    def __init__(
        self,
        name: str,
        config: dict[str, Any] | PythonNodeConfig,
        node_type: str = "python",
        operator_log: OperatorLog | None = None,
    ) -> None:
        """Validate config up front (S14); execution is deferred to build_runnable."""
        node_config = config if isinstance(config, PythonNodeConfig) else PythonNodeConfig(**config)
        super().__init__(name, node_type, node_config.model_dump(), operator_log)
        self._node_config = node_config

    @override
    def validate_config(self) -> bool:
        """Config was validated in __init__; kept for the BaseNode contract (K4)."""
        return True

    @override
    def build_runnable(self) -> Runnable:
        """唯一执行单元（K4）：R3 标准进出管线."""

        def func(state: dict[str, Any]) -> dict[str, Any]:
            started = time.perf_counter()
            # 1. R3 入口：state → dict（不 mutate 输入）
            state_dict = convert_state_to_dict(state)
            output: dict[str, Any] = {}
            try:
                cfg = self._node_config
                if cfg.code is not None:
                    output = self._run_inline_code(cfg.code, state_dict)
                else:
                    output = self._run_entry(cfg.entry or "", state_dict)
                # 2. log_execution：input_data 仅摘要（模式 + 代码长度/entry 名），H6/S15
                self._log(output, (time.perf_counter() - started) * 1000, error=None)
            except Exception as exc:
                # 异常分支：记录后重抛（H2/R6，禁止死 except）
                self._log(output, (time.perf_counter() - started) * 1000, error=str(exc))
                logger.exception("python_node_execution_failed", node=self.name, error=str(exc))
                raise
            # 3. R3 出口：双写 + history 增量
            return map_output_to_state(self.name, output, state_dict)

        return self.wrap_runnable(func)

    def _run_inline_code(self, code: str, state_dict: dict[str, Any]) -> dict[str, Any]:
        """Wrap inline code into a function so ``return`` / local imports work."""
        wrapped = "def __python_node_fn(state):\n" + textwrap.indent(code, "    ")
        namespace: dict[str, Any] = {}
        try:
            exec(wrapped, namespace)  # noqa: S102 — trusted repository-owned code by design
        except SyntaxError as exc:
            msg = f"PythonNode '{self.name}': inline code failed to compile: {exc}"
            raise PythonNodeError(msg) from exc
        result = namespace["__python_node_fn"](state_dict)
        return self._ensure_dict(result, hint="inline code must end with a return statement that produces a dict")

    def _run_entry(self, entry: str, state_dict: dict[str, Any]) -> dict[str, Any]:
        """Load ``module:function`` via importlib and invoke with the state dict."""
        if ":" not in entry:
            msg = f"PythonNode '{self.name}': entry must be 'module:function', got '{entry}'"
            raise PythonNodeError(msg)
        module_path, _, attr_name = entry.partition(":")
        try:
            module = importlib.import_module(module_path)
        except ImportError as exc:
            msg = f"PythonNode '{self.name}': failed to import module '{module_path}': {exc}"
            raise PythonNodeError(msg) from exc
        fn = getattr(module, attr_name, None)
        if fn is None:
            msg = f"PythonNode '{self.name}': function '{attr_name}' not found in module '{module_path}'"
            raise PythonNodeError(msg)
        result = fn(state_dict)
        return self._ensure_dict(result, hint=f"entry '{entry}' must return a dict")

    def _ensure_dict(self, result: Any, *, hint: str) -> dict[str, Any]:
        """Node output must be a dict; None is reported as a missing return."""
        if result is None:
            msg = f"PythonNode '{self.name}': {hint}"
            raise PythonNodeError(msg)
        if not isinstance(result, dict):
            msg = f"PythonNode '{self.name}': output must be a dict, got {type(result).__name__}"
            raise PythonNodeError(msg)
        return result

    def _log(self, output: dict[str, Any], execution_time_ms: float, error: str | None) -> None:
        """Write an ExecutionLog whose input_data is a summary only (S15/H6)."""
        cfg = self._node_config
        input_summary: dict[str, Any] = (
            {"mode": "code", "code_chars": len(cfg.code or "")}
            if cfg.code is not None
            else {"mode": "entry", "entry": cfg.entry or ""}
        )
        self.log_execution(
            ExecutionLog(
                node_name=self.name,
                node_type=str(self.node_type),
                input_data=input_summary,
                output_data=output,
                execution_time_ms=execution_time_ms,
                error=error,
            )
        )


# 模块底部自注册（K5 插件路径；factory 底部 import 本模块触发注册，R4 不加内置分支）
register_node_type("python", PythonNode)
