"""One-shot test runner for SubAgentConfig assets.

Compiles a standalone deepagents graph via ``compile_standalone_subagent``
(no subagents, no memory middleware, no checkpointer by default) and
executes a single prompt end-to-end, returning a ``SubAgentTestResult``
with turn and duration statistics. Runs are fully isolated: no checkpoints
are written and the assembly compile cache is never touched.

When ``cfg.skill_names`` is a non-empty list, the bound global skills are
materialised into a caller-supplied tmp directory (the ``tmp_skills_root``
parameter) so the agent can read them through the standard
``FilesystemBackend`` layout. ``None`` collapses to ``[]`` because a
standalone run has no parent app to inherit from.
"""

import time
from pathlib import Path
from typing import Any

from langchain_core.messages import AIMessage, HumanMessage
from langchain_core.runnables import RunnableConfig
from sqlmodel import Session, select

from app.core.config import settings
from app.core.langgraph.tools import tools as builtin_tools
from app.core.logging import logger
from app.core.metrics import agent_test_runs_total, subagent_task_duration_seconds
from app.core.observability import langfuse_callback_handler
from app.models.agent_assets import SubAgentConfig
from app.schemas.agent_apps import SubAgentTestResult
from app.services.agents.assembly import compile_standalone_subagent, resolve_tools
from app.services.agents.mcp_manager import build_tool_catalog, get_mcp_tools
from app.services.agents.skills_store import materialize_into_directory
from app.services.llm.llm_store import build_chat_model, load_model_config


def _message_text(message: AIMessage) -> str:
    """Flatten an AIMessage content value (str or content-block list) to text."""
    content = message.content
    if isinstance(content, str):
        return content
    if isinstance(content, list):
        return " ".join(str(block.get("text", "")) for block in content if isinstance(block, dict)).strip()
    return str(content)


def _load_config(session: Session, name: str) -> SubAgentConfig | None:
    """Fetch the SubAgentConfig row by primary key, or None when missing."""
    return session.exec(select(SubAgentConfig).where(SubAgentConfig.name == name)).first()


async def run_subagent_once(
    session: Session,
    *,
    name: str,
    prompt: str,
    tmp_skills_root: Path | None = None,
) -> SubAgentTestResult:
    """Execute one isolated one-shot test run of a sub-agent configuration.

    Args:
        session: SQLModel database session (config lookup + MCP tool catalog).
        name: Primary key of the SubAgentConfig to test-run.
        prompt: User prompt the sub-agent is invoked with.
        tmp_skills_root: Optional tmp directory into which the sub-agent's
            bound skills are materialised for this run only. Required when
            ``cfg.skill_names`` is non-empty; the caller is responsible for
            the directory's lifecycle (typically a ``tmp_path`` fixture) so
            no state leaks back to ``settings.SKILLS_ROOT``.

    Returns:
        SubAgentTestResult with the final AIMessage text, the number of model
        turns consumed (AIMessage count), wall-clock duration and the upstream
        model id of the resolved model config.

    Raises:
        ValueError: When no SubAgentConfig exists under ``name``, its model
            reference cannot be resolved, or a bound skill is missing.
        Exception: Re-raised after error counting and structured logging.
    """
    cfg = _load_config(session, name)
    if cfg is None:
        agent_test_runs_total.labels(status="error").inc()
        logger.error("subagent_test_config_not_found", name=name)
        raise ValueError(f"subagent config not found: {name}")

    try:
        provider_cfg, model_cfg = load_model_config(session, cfg.model)
    except ValueError:
        agent_test_runs_total.labels(status="error").inc()
        logger.exception("subagent_test_model_unresolvable", name=name)
        raise
    model_name = model_cfg.model_id
    started = time.perf_counter()
    try:
        # The catalog validates MCP server health and mirrors what the API
        # layer advertises; executable tool instances come from the same
        # builtin + MCP index pattern used by ``compile_agent_app``.
        catalog = await build_tool_catalog(session)
        mcp_tools = await get_mcp_tools(session)
        tool_index = {tool.name: tool for tool in [*builtin_tools, *mcp_tools]}
        tools = resolve_tools(cfg.allowed_tools, tool_index)  # None = full catalog
        model = build_chat_model(provider_cfg, model_cfg)

        # Materialise bound skills into the caller-supplied tmp directory
        # (independent of ``settings.SKILLS_ROOT`` so test runs stay isolated
        # and can never pollute the global/user skill trees). ``None``
        # collapses to ``[]`` here because the standalone runner has no
        # parent app to inherit from.
        skills = list(cfg.skill_names or [])
        if skills:
            if tmp_skills_root is None:
                raise ValueError(
                    f"tmp_skills_root is required when subagent '{cfg.name}' declares skill_names={cfg.skill_names!r}"
                )
            await materialize_into_directory(session, tmp_skills_root, skills)

        graph = compile_standalone_subagent(
            cfg,
            tools=tools,
            model=model,
            checkpointer=None,
            skills_dir=tmp_skills_root,
        )
        # Tracing parity with the chat runtime (_build_config): the Langfuse
        # callback is attached only when tracing is enabled.
        invoke_config: RunnableConfig = {
            "callbacks": [langfuse_callback_handler] if settings.LANGFUSE_TRACING_ENABLED else []
        }
        state = await graph.ainvoke({"messages": [HumanMessage(content=prompt)]}, config=invoke_config)
    except Exception:
        agent_test_runs_total.labels(status="error").inc()
        logger.exception("subagent_test_run_failed", name=name, model=model_name)
        raise

    duration = time.perf_counter() - started
    messages: list[Any] = list(state["messages"])
    ai_messages = [message for message in messages if isinstance(message, AIMessage)]
    final_message = _message_text(ai_messages[-1]) if ai_messages else ""

    agent_test_runs_total.labels(status="success").inc()
    subagent_task_duration_seconds.labels(subagent=name).observe(duration)
    logger.info(
        "subagent_test_run_complete",
        name=name,
        model=model_name,
        turns=len(ai_messages),
        catalog_entries=len(catalog),
        tool_count=len(tools),
        skill_count=len(skills),
        duration_seconds=duration,
    )
    return SubAgentTestResult(
        final_message=final_message,
        turns=len(ai_messages),
        duration_seconds=duration,
        model=model_name,
    )
