"""Assembly service: compile AgentApp configurations into deepagents graphs.

This is the ONLY module in the repository allowed to import ``deepagents``.
Everything upstream (API routes, runtime services) talks to the compiled
``CompiledStateGraph`` objects produced here and stays engine-agnostic.

Assembly pipeline (``compile_agent_app``):

1. The effective skill set is the union of ``app_cfg.skill_names`` and every
   bound subagent's effective ``skill_names`` (``None`` contributes nothing
   because the sub-agent will inherit the parent's skill set; explicit empty
   lists and explicit whitelists both contribute their names). G2 v3.3: the
   union is no longer materialised here — the User layer is filled by the
   lazy validation in front of ``runtime.get_runtime`` instead.
2. ``build_tool_catalog`` merges builtin + MCP tools (fail-fast whitelist
   validation happens against this catalog in ``validate_publish``).
3. ``resolve_tools`` applies the app-level tool whitelist (None = all).
4. Each ``SubAgentConfig`` becomes a declarative deepagents ``SubAgent`` via
   ``build_subagent_spec`` with explicit inheritance resolution
   (including skill inheritance: ``cfg.skill_names is None`` -> inherit the
   app's effective skill set, ``[]`` -> none, ``[...]`` -> explicit whitelist).
5. Skills are served through ``FilesystemBackend(root_dir=<user skill dir>)``
   with source ``"/"`` (each ``<name>/SKILL.md`` directory is one skill).
6. ``MemoryMiddleware`` injects the per-request dynamic context on every
   model call — username section, long-term memory (mem0 search per user_id +
   last user message, ``"No relevant memory found."`` fallback) and the
   current date/time — appended as extra system-prompt sections. The stored
   AgentApp prompt is a static template, so these request-time values are
   never frozen into the DB (prepare_messages + load_system_prompt semantics
   with the AgentApp prompt as the main body).

``max_turns`` gate decision (verified against deepagents 0.7.5 source):

- ``SubAgent`` (``deepagents/middleware/subagents.py``) is a ``TypedDict``
  with an optional ``middleware: list[AgentMiddleware]`` field, and
  ``create_deep_agent`` splices those entries into the per-subagent stack
  (``deepagents/graph.py`` via ``_apply_custom_middleware``). Therefore the
  turn budget is implemented with the self-authored ``TurnLimitMiddleware``
  attached through ``SubAgent["middleware"]`` — the primary mechanism.
- Fallback (documented, NOT used on 0.7.5): if a future deepagents release
  dropped ``SubAgent["middleware"]``, the spec would degrade to
  ``CompiledSubAgent(runnable=create_deep_agent(...).with_config(
  {"recursion_limit": 2 * max_turns + 2}))``.
"""

import asyncio
import hashlib
import json
import time
from collections import OrderedDict
from collections.abc import Callable, Mapping, Sequence
from datetime import datetime
from pathlib import Path
from typing import Any, Optional, cast, override

from deepagents import SubAgent, create_deep_agent
from deepagents.backends import FilesystemBackend
from deepagents.middleware import SummarizationMiddleware
from langchain.agents.middleware.types import AgentMiddleware, ModelRequest
from langchain_core.language_models.chat_models import BaseChatModel
from langchain_core.messages import AIMessage, AnyMessage, HumanMessage, SystemMessage
from langchain_core.runnables.config import ensure_config
from langchain_core.tools.base import BaseTool
from langgraph.graph.state import CompiledStateGraph
from langgraph.types import Checkpointer
from sqlmodel import Session

from app.core.langgraph.tools import tools as builtin_tools
from app.core.config import settings
from app.core.logging import logger
from app.core.metrics import agent_graph_cache_hits_total, agent_graph_compile_duration_seconds
from app.models.agent_assets import AgentApp, SubAgentConfig
from app.models.provider import DEFAULT_MODEL_REF, ModelConfig, Provider
from app.services.agents import skills_store
from app.services.agents.mcp_manager import build_tool_catalog, get_mcp_tools
from app.services.llm.llm_store import build_chat_model, load_model_config
from app.services.memory import memory_service

_COMPILE_CACHE_CAPACITY = 64

# Process-level LRU of successfully compiled graphs keyed by
# (config fingerprint, user_id): the graph embeds a per-user
# FilesystemBackend, so two users must never share a compiled entry.
# Only successful compiles are cached; failures always recompile.
_compile_cache: "OrderedDict[tuple[str, int], CompiledStateGraph]" = OrderedDict()


# ---------------------------------------------------------------------------
# Custom middleware
# ---------------------------------------------------------------------------


class TurnLimitMiddleware(AgentMiddleware):
    """Bound the number of model turns an agent (typically a subagent) may take.

    A "turn" is one model call that produced an AIMessage: the middleware
    counts the AIMessages already present in ``request.messages`` and, once
    the budget is exhausted, short-circuits the model call by returning a
    terminal tool-call-free AIMessage so the agent loop ends cleanly.
    Counting from state (instead of an instance counter) keeps the gate
    correct across concurrent invocations and checkpointed resumes.
    """

    def __init__(self, max_turns: int) -> None:
        """Store the turn budget.

        Args:
            max_turns: Maximum number of model turns allowed.
        """
        super().__init__()
        self.max_turns = max_turns

    def _budget_exhausted(self, messages: Sequence[AnyMessage]) -> bool:
        """Return True when the AIMessage count already reached the budget."""
        ai_count = sum(1 for message in messages if isinstance(message, AIMessage))
        return ai_count >= self.max_turns

    def _termination_message(self) -> AIMessage:
        """Build the terminal AIMessage emitted when the budget is exhausted."""
        logger.warning("subagent_turn_limit_reached", max_turns=self.max_turns)
        return AIMessage(content=f"Turn limit reached: stopping after {self.max_turns} turns.")

    @override
    def wrap_model_call(self, request: ModelRequest[Any], handler: Any) -> Any:
        """Sync hook: block the model call once the turn budget is spent."""
        if self._budget_exhausted(request.messages):
            return self._termination_message()
        return handler(request)

    @override
    async def awrap_model_call(self, request: ModelRequest[Any], handler: Any) -> Any:
        """Async hook: block the model call once the turn budget is spent."""
        if self._budget_exhausted(request.messages):
            return self._termination_message()
        return await handler(request)


def _extract_text(content: Any) -> str:
    """Flatten a message content value (str or content-block list) into text."""
    if isinstance(content, str):
        return content
    if isinstance(content, list):
        return " ".join(str(block.get("text", "")) for block in content if isinstance(block, dict)).strip()
    return str(content)


class MemoryMiddleware(AgentMiddleware):
    """Inject the per-request dynamic context into the system prompt per model call.

    The AgentApp row persists a **static** prompt template; everything that is
    request-time data is appended by this middleware on every model call:

    - ``# User`` section with the calling user's display name (metadata
      ``username``),
    - ``# What you know about the user`` with ``memory_service.search``
      results (``user_id`` from config metadata + last user message;
      ``"No relevant memory found."`` fallback),
    - ``# Current date and time`` with the request-time timestamp.

    This keeps the stored prompt from freezing first-startup values (username
    / clock / memory) — the regression that motivated the per-turn injection.
    """

    async def _inject_memory(self, request: ModelRequest[Any]) -> ModelRequest[Any]:
        """Return a request whose system message carries the dynamic sections."""
        # ``Runtime`` exposes no config attribute; the active RunnableConfig is
        # propagated via contextvars and recovered with ensure_config().
        metadata = ensure_config().get("metadata")
        user_id = (metadata or {}).get("user_id")
        if not user_id:
            return request
        username = (metadata or {}).get("username")

        query = ""
        for message in reversed(request.messages):
            if isinstance(message, HumanMessage):
                query = _extract_text(message.content)
                break

        memory = await memory_service.search(user_id, query) if query else ""
        if not memory:
            memory = "No relevant memory found."

        sections: list[str] = []
        if username:
            sections.append(f"# User\nYou are talking to {username}.")
        sections.append(f"# What you know about the user\n{memory}")
        sections.append(f"# Current date and time\n{datetime.now().strftime('%Y-%m-%d %H:%M:%S')}")

        logger.debug("dynamic_context_injected", user_id=user_id, memory_length=len(memory))
        blocks = list(request.system_message.content_blocks) if request.system_message else []
        blocks.append({"type": "text", "text": "\n\n" + "\n\n".join(sections)})
        return request.override(system_message=SystemMessage(content_blocks=blocks))

    @override
    def wrap_model_call(self, request: ModelRequest[Any], handler: Any) -> Any:
        """Sync hook: inject memory (only usable outside a running event loop)."""
        return handler(asyncio.run(self._inject_memory(request)))

    @override
    async def awrap_model_call(self, request: ModelRequest[Any], handler: Any) -> Any:
        """Async hook: inject memory before delegating to the inner handler."""
        return await handler(await self._inject_memory(request))


# ---------------------------------------------------------------------------
# Tool resolution & subagent specs
# ---------------------------------------------------------------------------


def resolve_tools(allowed_tools: Optional[list[str]], catalog: Mapping[str, BaseTool]) -> list[BaseTool]:
    """Resolve a tool whitelist against a name -> tool index.

    Args:
        allowed_tools: Whitelist of tool names; ``None`` means every catalog tool.
        catalog: Mapping of tool name to tool instance (builtin + MCP).

    Returns:
        The resolved tool instances (whitelist order preserved).

    Raises:
        ValueError: Listing every name missing from the catalog.
    """
    if allowed_tools is None:
        return list(catalog.values())

    unknown = sorted(name for name in allowed_tools if name not in catalog)
    if unknown:
        raise ValueError(f"unknown tool names in allowed_tools: {', '.join(unknown)}")
    return [catalog[name] for name in allowed_tools]


def build_subagent_spec(
    cfg: SubAgentConfig,
    *,
    parent_tools: Sequence[BaseTool],
    parent_model: BaseChatModel,
    resolve_model: Callable[[str], BaseChatModel],
    parent_skills: Sequence[str] = (),
    tool_index: Optional[Mapping[str, BaseTool]] = None,
) -> SubAgent:
    """Convert a SubAgentConfig row into a declarative deepagents SubAgent spec.

    Inheritance is explicit:

    - ``allowed_tools=None`` inherits the parent's resolved tools.
    - ``model=None`` inherits the parent's model instance (the same object).
    - ``skill_names=None`` inherits the parent's published skill set
      (``parent_skills``, the parent's effective ``["/skills/<name>", ...]``
      list).
    - ``skill_names=[]`` explicitly binds no skills (overrides inheritance).
    - ``skill_names=[<name>, ...]`` becomes ``["/skills/<name>", ...]`` after
      the bind-time whitelist resolution (skills mount at ``/skills/``
      relative to the shared per-(app, user) backend root).

    A non-NULL ``model`` is a ``"provider/model"`` reference resolved through
    ``resolve_model``. ``when_to_use`` maps to ``SubAgent["description"]``
    (what the orchestrator sees when deciding whether to delegate). When
    ``max_turns`` is set, a ``TurnLimitMiddleware`` is attached via the
    spec's ``middleware`` field (supported since deepagents 0.7.5 — see
    module docstring).

    Args:
        cfg: The persisted sub-agent configuration row.
        parent_tools: Tools already resolved for the parent agent.
        parent_model: Model instance used by the parent agent.
        resolve_model: Resolver mapping a ``"provider/model"`` reference to a
            chat model instance (supplied by ``compile_agent_app``).
        parent_skills: Effective skill source paths of the parent agent
            (already prefixed with ``"/"``); ``None`` for the standalone
            runner, where inheritance degenerates to ``[]``.
        tool_index: Optional full name -> tool index used to resolve an
            explicit ``allowed_tools`` whitelist; defaults to the parent's
            tool set when omitted.

    Returns:
        A deepagents SubAgent TypedDict ready for ``create_deep_agent``.

    Raises:
        ValueError: When ``allowed_tools`` references unknown tool names or
            the model reference cannot be resolved.
    """
    if cfg.allowed_tools is None:
        tools: list[BaseTool] = list(parent_tools)
    else:
        index: Mapping[str, BaseTool] = (
            tool_index if tool_index is not None else {tool.name: tool for tool in parent_tools}
        )
        tools = resolve_tools(cfg.allowed_tools, index)

    model: BaseChatModel = resolve_model(cfg.model) if cfg.model else parent_model

    spec: SubAgent = {
        "name": cfg.name,
        "description": cfg.when_to_use,
        "system_prompt": cfg.system_prompt,
        "tools": tools,
        "model": model,
    }
    if cfg.max_turns is not None:
        spec["middleware"] = [TurnLimitMiddleware(cfg.max_turns)]

    if cfg.skill_names is None:
        skills: list[str] = list(parent_skills)
        skill_source = "inherited"
    elif cfg.skill_names == []:
        skills = []
        skill_source = "none"
    else:
        skills = [f"/skills/{name}" for name in cfg.skill_names]
        skill_source = "whitelist"
    if skills:
        spec["skills"] = skills  # type: ignore[typeddict-item]

    logger.debug(
        "subagent_spec_built",
        name=cfg.name,
        inherited_tools=cfg.allowed_tools is None,
        inherited_model=cfg.model is None,
        max_turns=cfg.max_turns,
        skill_source=skill_source,
        skill_count=len(skills),
    )
    return spec


# ---------------------------------------------------------------------------
# Standalone one-shot subagent compilation (test runs)
# ---------------------------------------------------------------------------


def compile_standalone_subagent(
    cfg: SubAgentConfig,
    *,
    tools: list[BaseTool],
    model: BaseChatModel,
    checkpointer: Checkpointer | None = None,
    skills_dir: Path | None = None,
) -> CompiledStateGraph:
    """Compile a standalone one-shot graph for test-running a subagent config.

    Unlike ``compile_agent_app`` the graph has no subagents, no memory
    middleware and no shared compile cache — it is the bare subagent itself
    (``cfg.system_prompt`` as the system prompt) so a test run exercises
    exactly this configuration.

    Skill support (mirrors ``build_subagent_spec`` semantics at the sub-agent
    boundary, with ``None`` degenerating to ``[]`` because there is no
    parent to inherit from):

    - ``cfg.skill_names is None`` -> bind no skills (standalone has no
      parent to inherit).
    - ``cfg.skill_names == []`` -> bind no skills.
    - ``cfg.skill_names == [..]`` -> bind exactly those skills, served from
      the caller-supplied ``skills_dir``.

    When any skills are bound, a ``FilesystemBackend(root_dir=skills_dir)``
    is attached so the agent can read ``<name>/SKILL.md`` files. The caller
    is responsible for materializing skill contents into ``skills_dir``
    (typically ``skills_store.materialize_for_user`` writing into a tmp dir)
    so the standalone runner never touches ``settings.SKILLS_ROOT`` directly
    and tests stay isolated.

    When ``cfg.max_turns`` is set the same ``TurnLimitMiddleware`` gate used
    for embedded subagents is attached. The compiled graph is returned as-is:
    nothing is written into the process-level compile cache and, unless the
    caller supplies one, no checkpointer is attached.

    Args:
        cfg: The persisted sub-agent configuration row to compile.
        tools: Resolved tool instances the subagent may call.
        model: Chat model instance executing the subagent.
        checkpointer: Optional checkpointer to attach (defaults to None).
        skills_dir: Optional directory whose ``<skill>/SKILL.md`` layout backs
            the skills backend. Required whenever ``cfg.skill_names`` is a
            non-empty list.

    Returns:
        The compiled standalone deep agent graph.
    """
    middleware: list[TurnLimitMiddleware] = []
    if cfg.max_turns is not None:
        middleware.append(TurnLimitMiddleware(cfg.max_turns))

    # Standalone runner has no parent: ``None`` collapses to ``[]`` per the
    # inheritance contract documented at the module level.
    requested_skills: list[str] = list(cfg.skill_names or [])
    skills: list[str] = [f"/{name}" for name in requested_skills]

    backend: FilesystemBackend | None = None
    if skills:
        if skills_dir is None:
            raise ValueError(
                f"skills_dir is required when subagent '{cfg.name}' declares skill_names={cfg.skill_names!r}"
            )
        backend = FilesystemBackend(root_dir=str(skills_dir))

    graph = create_deep_agent(
        model=model,
        tools=tools,
        system_prompt=cfg.system_prompt,
        middleware=middleware,
        checkpointer=checkpointer,
        skills=skills or None,
        backend=backend,
        name=f"subagent-test:{cfg.name}",
    )
    logger.info(
        "standalone_subagent_compiled",
        name=cfg.name,
        tool_count=len(tools),
        max_turns=cfg.max_turns,
        checkpointer=checkpointer is not None,
        skill_count=len(skills),
    )
    return graph


# ---------------------------------------------------------------------------
# Compile & publish validation
# ---------------------------------------------------------------------------


async def compile_agent_app(
    session: Session,
    app_cfg: AgentApp,
    *,
    subagent_cfgs: Sequence[SubAgentConfig],
    user_id: int,
    checkpointer: Checkpointer | None = None,
) -> CompiledStateGraph:
    """Assemble and compile an AgentApp into an executable deepagents graph.

    G2 v3.3 (spec-g2-workspace §6.1.3): ``user_id`` is a required int and the
    skills backend reads the nested per-(app, user) User layer. The compile
    path no longer materialises anything — the User layer is filled by the
    lazy validation in front of ``runtime.get_runtime`` (spec §6.1.1).

    Args:
        session: SQLModel database session (for the MCP tool catalog).
        app_cfg: The AgentApp configuration row to compile.
        subagent_cfgs: SubAgentConfig rows referenced by ``app_cfg.subagent_names``.
        user_id: User whose nested workspace backs the skills backend.
        checkpointer: Checkpointer to attach (production: AsyncPostgresSaver
            over the shared connection pool; tests: MemorySaver).

    Returns:
        The compiled deep agent graph.

    Raises:
        ValueError: When the tool whitelist references unknown tool names or
            a model reference cannot be resolved.
    """
    effective_skill_names = sorted(
        set(app_cfg.skill_names) | {n for cfg in subagent_cfgs for n in (cfg.skill_names or [])}
    )

    catalog = await build_tool_catalog(session)
    mcp_tools = await get_mcp_tools(session)
    tool_index: dict[str, BaseTool] = {tool.name: tool for tool in [*builtin_tools, *mcp_tools]}
    logger.debug(
        "agent_app_tool_index_built",
        app_name=app_cfg.name,
        catalog_entries=len(catalog),
        tool_count=len(tool_index),
    )

    tools = resolve_tools(app_cfg.allowed_tools, tool_index)
    model = build_chat_model(*load_model_config(session, app_cfg.model))

    def resolve_model(reference: str) -> BaseChatModel:
        """Resolve a subagent model reference against the live DB."""
        return build_chat_model(*load_model_config(session, reference))

    parent_skills: list[str] = [f"/skills/{name}" for name in effective_skill_names]
    subagents = [
        build_subagent_spec(
            cfg,
            parent_tools=tools,
            parent_model=model,
            tool_index=tool_index,
            resolve_model=resolve_model,
            parent_skills=parent_skills,
        )
        for cfg in subagent_cfgs
    ]

    # G2 v3 (D15, spec §2.1/§6.1.3): the backend roots at the per-(app, user)
    # workspace — the agent's sandboxed filesystem. Skills are referenced as
    # virtual "/skills/<name>" paths relative to root_dir (deepagents mount
    # convention), so the physical files resolve to
    # {user_root}/skills/<name>/SKILL.md — exactly the §2.1 User-layer
    # template that materialize_to_user_combined and the lazy validation
    # write. Sourced from the Phase-1 path helper (single source of truth).
    user_root = skills_store._user_dir(app_cfg.id, user_id)  # noqa: SLF001 — same-package path helper
    backend = FilesystemBackend(root_dir=str(user_root))
    interrupt_on = cast(Optional[dict[str, Any]], app_cfg.interrupt_on or None)

    # G3 (spec-g3-session §4.2): context compression is on by default. The
    # token trigger prefers the per-app AgentApp.context_size and falls back
    # to the global settings default; deepagents replaces its base-stack
    # SummarizationMiddleware in place because the custom instance reports
    # the same middleware name (no double stack).
    context_threshold = app_cfg.context_size or settings.DEFAULT_AGENT_CONTEXT_SIZE

    graph = create_deep_agent(
        model=model,
        tools=tools,
        system_prompt=app_cfg.system_prompt,
        middleware=[
            SummarizationMiddleware(
                model=model,
                backend=backend,
                trigger=("tokens", context_threshold),
            ),
            MemoryMiddleware(),
        ],
        subagents=subagents or None,
        skills=parent_skills or None,
        backend=backend,
        interrupt_on=interrupt_on,
        checkpointer=checkpointer,
        name=app_cfg.name,
    )

    logger.info(
        "agent_app_compiled",
        app_name=app_cfg.name,
        user_id=user_id,
        tool_count=len(tools),
        subagent_count=len(subagents),
        skill_count=len(effective_skill_names),
    )
    return graph


def validate_publish(
    app_cfg: AgentApp,
    subagent_cfgs: Sequence[SubAgentConfig],
    catalog: Sequence[Mapping[str, Any]],
    model_catalog: Mapping[str, tuple[Provider, ModelConfig]],
) -> None:
    """Validate tool whitelists and model references before publishing.

    Skill/subagent referential integrity is enforced by the caller at the DB
    layer; this function checks that every ``allowed_tools`` entry of the
    AgentApp and of each subagent exists in the current tool catalog, and
    that every ``model`` reference (NULL resolves to the default pair)
    points at an existing, enabled provider/model pair.

    Args:
        app_cfg: The AgentApp configuration row being published.
        subagent_cfgs: SubAgentConfig rows bound to the app.
        catalog: Tool catalog entries (mappings with a ``name`` key) as
            returned by ``build_tool_catalog``.
        model_catalog: Mapping of ``"provider/model"`` reference ->
            (provider, model config) pair for lookup; missing or disabled
            references are reported as violations.

    Raises:
        ValueError: Listing every owner and unknown tool name, then every
            invalid model reference (aggregated per category).
    """
    catalog_names = {entry["name"] for entry in catalog}
    violations: list[str] = []

    owners: list[tuple[str, Optional[list[str]]]] = [("agent_app:" + app_cfg.name, app_cfg.allowed_tools)]
    owners.extend((f"subagent:{cfg.name}", cfg.allowed_tools) for cfg in subagent_cfgs)

    for owner, allowed_tools in owners:
        if allowed_tools is None:
            continue
        unknown = sorted(name for name in allowed_tools if name not in catalog_names)
        violations.extend(f"{owner} -> {name}" for name in unknown)

    if violations:
        raise ValueError(f"allowed_tools not in tool catalog: {', '.join(violations)}")

    model_violations: list[str] = []
    model_refs: list[tuple[str, Optional[str]]] = [("agent_app:" + app_cfg.name, app_cfg.model)]
    model_refs.extend((f"subagent:{cfg.name}", cfg.model) for cfg in subagent_cfgs)

    for owner, reference in model_refs:
        ref_name = reference or DEFAULT_MODEL_REF
        pair = model_catalog.get(ref_name)
        if pair is None:
            model_violations.append(f"{owner} -> model '{ref_name}' does not exist")
            continue
        provider, model_cfg = pair
        if not provider.enabled:
            model_violations.append(f"{owner} -> provider '{provider.name}' is disabled")
        if not model_cfg.enabled:
            model_violations.append(f"{owner} -> model '{ref_name}' is disabled")

    if model_violations:
        raise ValueError(f"model references invalid: {', '.join(model_violations)}")

    logger.debug("agent_app_publish_tools_validated", app_name=app_cfg.name, owner_count=len(owners))


# ---------------------------------------------------------------------------
# Fingerprint & compile cache
# ---------------------------------------------------------------------------

_APP_FIELDS = (
    "name",
    "system_prompt",
    "allowed_tools",
    "model",
    "skill_names",
    "subagent_names",
    "interrupt_on",
    "engine",
    "context_size",
)
_SUBAGENT_FIELDS = (
    "name",
    "description",
    "when_to_use",
    "system_prompt",
    "allowed_tools",
    "model",
    "max_turns",
    "skill_names",
)


def _project(obj: Any, fields: Sequence[str]) -> dict[str, Any]:
    """Project an object's attributes into a plain dict for hashing."""
    return {field: getattr(obj, field) for field in fields}


def compute_fingerprint(
    app_cfg: AgentApp,
    subagent_cfgs: Sequence[SubAgentConfig],
    skill_hashes: Mapping[str, str],
    mcp_fingerprint: str,
    model_fingerprint: str,
) -> str:
    """Compute a stable sha256 fingerprint of everything that shapes the graph.

    ``model`` fields in the app/subagent projections stay reference names;
    the effective model configuration content is covered by ``model_fingerprint``
    (``ref:content_hash`` pairs) so auth/base_url/model_id edits drift the
    fingerprint and force a recompile without demoting published apps.

    Args:
        app_cfg: The AgentApp configuration row.
        subagent_cfgs: Bound SubAgentConfig rows.
        skill_hashes: Mapping of skill name -> content hash.
        mcp_fingerprint: Fingerprint of the enabled MCP server configuration.
        model_fingerprint: Fingerprint of the referenced model config contents.

    Returns:
        Hex sha256 over the canonical (sorted-keys, compact) JSON payload.
    """
    payload = {
        "app": _project(app_cfg, _APP_FIELDS),
        # Sort by name before projecting: row order must never change the hash.
        "subagents": [_project(cfg, _SUBAGENT_FIELDS) for cfg in sorted(subagent_cfgs, key=lambda cfg: cfg.name)],
        "skill_hashes": dict(sorted(skill_hashes.items())),
        "mcp_fingerprint": mcp_fingerprint,
        "model_fingerprint": model_fingerprint,
    }
    canonical = json.dumps(payload, sort_keys=True, separators=(",", ":"), ensure_ascii=False, default=str)
    return hashlib.sha256(canonical.encode("utf-8")).hexdigest()


def clear_compile_cache() -> None:
    """Drop every cached compiled graph (test isolation / shutdown hook)."""
    _compile_cache.clear()


async def get_or_compile(
    session: Session,
    app_cfg: AgentApp,
    *,
    subagent_cfgs: Sequence[SubAgentConfig],
    skill_hashes: Mapping[str, str],
    mcp_fingerprint: str,
    model_fingerprint: str,
    user_id: int,
    checkpointer: Checkpointer | None = None,
) -> CompiledStateGraph:
    """Return a cached compiled graph for the (fingerprint, user), or compile.

    The cache is a process-level OrderedDict LRU (capacity 64) keyed by
    ``(fingerprint, user_id)``: the compiled graph embeds a per-user
    FilesystemBackend, so identical fingerprints of different users must
    never share an entry. Hits count ``agent_graph_cache_hits_total{result="hit"}``,
    misses compile under ``agent_graph_compile_duration_seconds`` and count
    ``result="miss"``. Only successful compiles are cached. Checkpointer-less
    compiles are returned but never written to the cache: a graph compiled
    without a checkpointer must not be served again once the shared pool
    recovers (same hygiene as the degraded-runtime guard in
    ``runtime.get_runtime``).

    Args:
        session: SQLModel database session (for the MCP tool catalog).
        app_cfg: The AgentApp configuration row to compile.
        subagent_cfgs: Bound SubAgentConfig rows.
        skill_hashes: Mapping of skill name -> content hash (fingerprint input).
        mcp_fingerprint: MCP configuration fingerprint (fingerprint input).
        model_fingerprint: Model config content fingerprint (fingerprint input).
        user_id: User whose nested workspace backs the skills backend.
        checkpointer: Checkpointer to attach to freshly compiled graphs.

    Returns:
        The compiled deep agent graph (cached or newly compiled).
    """
    fingerprint = compute_fingerprint(app_cfg, subagent_cfgs, skill_hashes, mcp_fingerprint, model_fingerprint)
    cache_key = (fingerprint, user_id)

    cached = _compile_cache.get(cache_key)
    if cached is not None:
        _compile_cache.move_to_end(cache_key)
        agent_graph_cache_hits_total.labels(result="hit").inc()
        logger.debug("agent_graph_compile_cache_hit", fingerprint=fingerprint, app_name=app_cfg.name)
        return cached

    agent_graph_cache_hits_total.labels(result="miss").inc()
    started = time.perf_counter()
    graph = await compile_agent_app(
        session,
        app_cfg,
        subagent_cfgs=subagent_cfgs,
        user_id=user_id,
        checkpointer=checkpointer,
    )
    agent_graph_compile_duration_seconds.observe(time.perf_counter() - started)

    if checkpointer is None:
        # A checkpointer-less compile must not pollute the cache: once the
        # shared pool recovers the next request recompiles with checkpoints.
        logger.info("agent_graph_compiled_not_cached_no_checkpointer", fingerprint=fingerprint, app_name=app_cfg.name)
        return graph

    _compile_cache[cache_key] = graph
    _compile_cache.move_to_end(cache_key)
    while len(_compile_cache) > _COMPILE_CACHE_CAPACITY:
        evicted, _ = _compile_cache.popitem(last=False)
        logger.debug("agent_graph_compile_cache_evicted", fingerprint=evicted)

    logger.info("agent_graph_compiled_and_cached", fingerprint=fingerprint, app_name=app_cfg.name)
    return graph
