"""``react`` node: ReAct agent loop via langgraph.prebuilt.create_react_agent.

Wraps the prebuilt react agent with dual-mode tool configuration:
- Fine-grained ``tools``: explicit tool name whitelist (e.g. ["search", "browser-use__click"])
- Server-level ``mcp_servers``: all tools from named MCP servers (e.g. ["browser-use"])

Tool resolution happens at build time via the injected ``ToolResolver`` callable.
The node never imports app.services.* or app.core.* — tools cross the boundary
as resolved BaseTool instances, preserving the dependency red-line.

Follows the R3 pipeline and logs summaries only (S15/H6).
"""

from __future__ import annotations

import re
import time
from typing import Any, override

import structlog
from langchain_core.messages import SystemMessage
from langchain_core.runnables import Runnable
from langgraph.prebuilt import create_react_agent
from pydantic import BaseModel, ConfigDict, Field

from app.workflow.models import ConfigError, ExecutionLog, OperatorLog
from app.workflow.nodes.base import BaseNode
from app.workflow.nodes.factory import register_node_type
from app.workflow.ports import ChatModelFactory, ToolResolver
from app.workflow.utils import convert_state_to_dict, map_output_to_state, resolve_dot_path

logger = structlog.get_logger(__name__)

_PLACEHOLDER_PATTERN = re.compile(r"\{([A-Za-z_][A-Za-z0-9_.]*)\}")


class ReactNodeConfig(BaseModel):
    """ReactNode configuration (S14 extra='forbid')."""

    model_config = ConfigDict(extra="forbid")

    provider_ref: str
    tools: list[str] = Field(default_factory=list)
    mcp_servers: list[str] = Field(default_factory=list)
    system_prompt: str = ""
    max_iterations: int = Field(default=10, ge=1, le=100)
    inputs: dict[str, str] = Field(default_factory=dict)


class ReactNode(BaseNode):
    """ReAct agent node: tool-calling loop via create_react_agent (plugin type ``react``)."""

    def __init__(
        self,
        name: str,
        config: dict[str, Any] | ReactNodeConfig,
        node_type: str = "react",
        operator_log: OperatorLog | None = None,
        chat_model_factory: ChatModelFactory | None = None,
        tool_resolver: ToolResolver | None = None,
    ) -> None:
        """Validate config and store injected factories; fail at build time if deps missing."""
        node_config = config if isinstance(config, ReactNodeConfig) else ReactNodeConfig(**config)
        if not node_config.tools and not node_config.mcp_servers:
            msg = f"ReactNode '{name}' requires at least one of 'tools' or 'mcp_servers' in config"
            raise ConfigError(msg)
        super().__init__(name, node_type, node_config.model_dump(), operator_log)
        self._node_config = node_config
        self._chat_model_factory = chat_model_factory
        self._tool_resolver = tool_resolver

    @override
    def validate_config(self) -> bool:
        """Config was validated in __init__; kept for the BaseNode contract (K4)."""
        return True

    def render_template(self, template: str, context: dict[str, Any]) -> str:
        """Replace {key} and {a.b.c} placeholders via dot-path resolution."""

        def _sub(match: re.Match[str]) -> str:
            path = match.group(1)
            value = resolve_dot_path(context, path)
            if value is not None:
                return str(value)
            logger.debug("react_node_placeholder_unresolved", node=self.name, placeholder=path)
            return match.group(0)

        return _PLACEHOLDER_PATTERN.sub(_sub, template)

    def _resolve_inputs(self, state_dict: dict[str, Any]) -> dict[str, Any]:
        """Resolve inputs config against state_dict; each value is a dot-path into state."""
        resolved: dict[str, Any] = {}
        for var_name, dot_path in self._node_config.inputs.items():
            resolved[var_name] = resolve_dot_path(state_dict, dot_path)
        return resolved

    @override
    def build_runnable(self) -> Runnable:
        """Build the react agent at build time; R3 pipeline for state I/O."""
        if self._tool_resolver is None:
            msg = (
                f"ReactNode '{self.name}' requires a tool_resolver, which the composition root injects. "
                "Building this node without one is an assembly error."
            )
            raise ConfigError(msg)
        if self._chat_model_factory is None:
            msg = (
                f"ReactNode '{self.name}' requires a chat_model_factory for provider_ref resolution. "
                "Building this node without one is an assembly error."
            )
            raise ConfigError(msg)

        resolved_tools = self._tool_resolver(
            self._node_config.tools or None,
            self._node_config.mcp_servers or None,
        )
        if not resolved_tools:
            msg = f"ReactNode '{self.name}': tool resolution returned empty list; check config"
            raise ConfigError(msg)

        model = self._chat_model_factory(self._node_config.provider_ref, {})
        agent = create_react_agent(
            model=model,
            tools=resolved_tools,
            recursion_limit=self._node_config.max_iterations * 2,
        )

        def func(state: dict[str, Any]) -> dict[str, Any]:
            started = time.perf_counter()
            state_dict = convert_state_to_dict(state)
            messages = list(state_dict.get("messages", []))
            output: dict[str, Any] = {}

            resolved_inputs = self._resolve_inputs(state_dict)
            if self._node_config.system_prompt:
                rendered_prompt = self.render_template(self._node_config.system_prompt, resolved_inputs)
                messages.insert(0, SystemMessage(content=rendered_prompt))

            try:
                result = agent.invoke({"messages": messages})
                output_messages = result.get("messages", [])
                final_msg = output_messages[-1].content if output_messages else ""
                output = {"result": final_msg, "messages": output_messages}
                self._log(output, resolved_tools, (time.perf_counter() - started) * 1000, error=None)
            except Exception as exc:
                self._log(output, resolved_tools, (time.perf_counter() - started) * 1000, error=str(exc))
                logger.exception("react_node_execution_failed", node=self.name, error=str(exc))
                raise
            return map_output_to_state(self.name, output, state_dict)

        return self.wrap_runnable(func)

    def _log(
        self,
        output: dict[str, Any],
        tools: list[Any],
        execution_time_ms: float,
        error: str | None,
    ) -> None:
        """Summary-only log (S15): tool names and message count, never full payloads."""
        tool_names = [getattr(t, "name", str(t)) for t in tools]
        msg_count = len(output.get("messages", []))
        self.log_execution(
            ExecutionLog(
                node_name=self.name,
                node_type=str(self.node_type),
                input_data={
                    "provider_ref": self._node_config.provider_ref,
                    "tool_count": len(tools),
                    "tools": sorted(tool_names),
                    "max_iterations": self._node_config.max_iterations,
                    "inputs_count": len(self._node_config.inputs),
                },
                output_data={
                    "result_preview": str(output.get("result", ""))[:200],
                    "message_count": msg_count,
                    "duration_ms": execution_time_ms,
                },
                execution_time_ms=execution_time_ms,
                error=error,
            )
        )


register_node_type("react", ReactNode)
