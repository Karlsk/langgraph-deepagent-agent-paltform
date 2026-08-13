"""Unit tests for the agent-app assembly service (deepagents integration layer).

Zero real network / zero real LLM: models are scripted ``BaseChatModel``
subclasses, the checkpointer is ``MemorySaver``, skills live under
``tmp_path`` via a monkeypatched ``SKILLS_ROOT``, and both
``mcp_manager.build_tool_catalog`` / ``memory_service`` are monkeypatched.

The tests deliberately never import ``deepagents`` themselves — only
``app.services.agents.assembly`` is allowed to do so.
"""

import asyncio
import uuid
from collections.abc import Generator
from datetime import datetime
from pathlib import Path
from typing import Any

import pytest
from langchain_core.language_models.chat_models import BaseChatModel
from langchain_core.messages import AIMessage, BaseMessage, HumanMessage, SystemMessage
from langchain_core.outputs import ChatGeneration, ChatResult
from langchain_core.tools import BaseTool, tool
from langgraph.checkpoint.memory import MemorySaver

from app.core.config import settings
from app.core.metrics import agent_graph_cache_hits_total
from app.models.agent_assets import AgentApp, LlmConfig, SubAgentConfig
from app.services.agents import assembly

pytestmark = pytest.mark.unit


# ---------------------------------------------------------------------------
# Fakes & fixtures
# ---------------------------------------------------------------------------


class ScriptedChatModel(BaseChatModel):
    """Deterministic chat model replaying canned AIMessages (zero network).

    Every replayed message gets a fresh ``id`` (and fresh tool-call ids) so
    the deepagents messages reducer treats each turn as a distinct message.
    """

    responses: list[AIMessage]
    calls: list[list[BaseMessage]] = []
    n: int = 0

    @property
    def _llm_type(self) -> str:
        return "scripted"

    def bind_tools(self, tools: Any, **kwargs: Any) -> "ScriptedChatModel":
        """Tools are irrelevant for scripted replies; return self."""
        return self

    def _generate(
        self,
        messages: list[BaseMessage],
        stop: Any = None,
        run_manager: Any = None,
        **kwargs: Any,
    ) -> ChatResult:
        """Record the call and replay the next canned response."""
        self.n += 1
        self.calls.append(list(messages))
        message = self.responses[(self.n - 1) % len(self.responses)].model_copy(deep=True)
        message.id = str(uuid.uuid4())
        for index, tool_call in enumerate(message.tool_calls):
            tool_call["id"] = f"tc-{self.n}-{index}"
        return ChatResult(generations=[ChatGeneration(message=message)])


@tool
def echo(text: str) -> str:
    """Echo the given text back."""
    return f"echo: {text}"


@tool
def upper(text: str) -> str:
    """Uppercase the given text."""
    return text.upper()


@pytest.fixture(autouse=True)
def clean_compile_cache() -> Generator[None, None, None]:
    """Isolate the process-level compile cache between tests."""
    assembly.clear_compile_cache()
    yield
    assembly.clear_compile_cache()


@pytest.fixture
def skills_root(tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> Path:
    """Redirect settings.SKILLS_ROOT into an isolated tmp directory."""
    root = tmp_path / "skills"
    monkeypatch.setattr(settings, "SKILLS_ROOT", str(root))
    return root


@pytest.fixture
def mock_catalog(monkeypatch: pytest.MonkeyPatch) -> list[dict[str, Any]]:
    """Stub build_tool_catalog/get_mcp_tools so no MCP server is contacted."""
    catalog: list[dict[str, Any]] = [
        {"name": "echo", "source": "builtin"},
        {"name": "upper", "source": "builtin"},
    ]

    async def fake_build_tool_catalog(session: Any) -> list[dict[str, Any]]:
        return catalog

    async def fake_get_mcp_tools(session: Any) -> list[BaseTool]:
        return []

    monkeypatch.setattr(assembly, "build_tool_catalog", fake_build_tool_catalog)
    monkeypatch.setattr(assembly, "get_mcp_tools", fake_get_mcp_tools)
    return catalog


@pytest.fixture
def mock_memory(monkeypatch: pytest.MonkeyPatch) -> dict[str, Any]:
    """Stub memory_service.search and record every call."""
    record: dict[str, Any] = {"calls": [], "result": "* user likes tea"}

    async def fake_search(user_id: str | None, query: str) -> str:
        record["calls"].append((user_id, query))
        return record["result"]

    monkeypatch.setattr(assembly.memory_service, "search", fake_search)
    return record


def _make_app(**overrides: Any) -> AgentApp:
    """Build an AgentApp row with sensible defaults for assembly tests."""
    defaults: dict[str, Any] = {
        "name": "demo-app",
        "system_prompt": "You are the demo app.",
        "allowed_tools": None,
        "model": None,
        "skill_names": [],
        "subagent_names": [],
        "interrupt_on": {},
        "engine": "deepagents",
        "status": "published",
        "version": 1,
    }
    defaults.update(overrides)
    return AgentApp(**defaults)


def _make_subagent(**overrides: Any) -> SubAgentConfig:
    """Build a SubAgentConfig row with sensible defaults for assembly tests."""
    defaults: dict[str, Any] = {
        "name": "helper",
        "description": "generic helper",
        "when_to_use": "Use for generic help.",
        "system_prompt": "You are a helper.",
        "allowed_tools": None,
        "model": None,
        "max_turns": None,
        "content_hash": "h-helper",
        "version": 1,
    }
    defaults.update(overrides)
    return SubAgentConfig(**defaults)


def _tool_index() -> dict[str, BaseTool]:
    return {echo.name: echo, upper.name: upper}


def _parent_model(final_text: str = "all done") -> ScriptedChatModel:
    return ScriptedChatModel(responses=[AIMessage(content=final_text)])


def _make_llm_config(**overrides: Any) -> LlmConfig:
    """Build an LlmConfig row for publish-validation tests."""
    defaults: dict[str, Any] = {
        "name": "default",
        "model_name": "MiniMax-M3",
        "api_key": "sk-test",
        "content_hash": "h-default",
    }
    defaults.update(overrides)
    return LlmConfig(**defaults)


def _default_llm_configs() -> dict[str, LlmConfig]:
    return {"default": _make_llm_config()}


def _patch_llm_seams(monkeypatch: pytest.MonkeyPatch, models: dict[str, Any]) -> None:
    """Redirect the DB-backed resolution seam: reference name -> scripted model."""

    def fake_load(session: Any, reference: str | None) -> LlmConfig:
        name = reference or "default"
        return _make_llm_config(name=name, model_name=name)

    monkeypatch.setattr(assembly, "load_llm_config", fake_load)
    monkeypatch.setattr(assembly, "build_chat_model", lambda cfg: models[cfg.model_name])


# ---------------------------------------------------------------------------
# resolve_tools
# ---------------------------------------------------------------------------


def test_resolve_tools_none_returns_full_catalog() -> None:
    """None whitelist resolves to every catalog tool."""
    resolved = assembly.resolve_tools(None, _tool_index())
    assert {t.name for t in resolved} == {"echo", "upper"}


def test_resolve_tools_filters_by_name() -> None:
    """An explicit whitelist keeps only the named tools."""
    resolved = assembly.resolve_tools(["upper"], _tool_index())
    assert [t.name for t in resolved] == ["upper"]


def test_resolve_tools_unknown_names_raise_with_listing() -> None:
    """Unknown whitelist names raise ValueError listing every offender."""
    with pytest.raises(ValueError, match="ghost") as exc_info:
        assembly.resolve_tools(["echo", "ghost", "phantom"], _tool_index())
    assert "phantom" in str(exc_info.value)


# ---------------------------------------------------------------------------
# build_subagent_spec — inheritance resolution
# ---------------------------------------------------------------------------


def test_build_subagent_spec_blank_fields_inherit_parent() -> None:
    """Blank allowed_tools/model inherit the parent's tools and model (same object)."""
    parent_tools = [echo, upper]
    parent_model = _parent_model()
    resolved: list[str] = []

    def resolve_model(reference: str) -> BaseChatModel:
        resolved.append(reference)
        raise AssertionError("resolve_model must not be called when cfg.model is None")

    spec = assembly.build_subagent_spec(
        _make_subagent(),
        parent_tools=parent_tools,
        parent_model=parent_model,
        resolve_model=resolve_model,
    )

    assert spec["name"] == "helper"
    assert spec["description"] == "Use for generic help."  # when_to_use -> description
    assert spec["system_prompt"] == "You are a helper."
    assert [t.name for t in spec["tools"]] == ["echo", "upper"]
    assert spec["model"] is parent_model
    assert resolved == []
    assert "middleware" not in spec


def test_build_subagent_spec_explicit_tools_and_model() -> None:
    """Explicit tools/model/max_turns resolve via the index, resolver and gate."""
    custom_model = _parent_model("sub model")

    cfg = _make_subagent(allowed_tools=["upper"], model="mini", max_turns=3)
    spec = assembly.build_subagent_spec(
        cfg,
        parent_tools=[echo],
        parent_model=_parent_model(),
        resolve_model=lambda reference: custom_model,
        tool_index=_tool_index(),
    )

    assert [t.name for t in spec["tools"]] == ["upper"]
    assert spec["model"] is custom_model
    middleware = spec.get("middleware", [])
    assert len(middleware) == 1
    assert isinstance(middleware[0], assembly.TurnLimitMiddleware)
    assert middleware[0].max_turns == 3


def test_build_subagent_spec_unknown_tool_raises() -> None:
    """An explicit whitelist with unknown names raises ValueError."""
    cfg = _make_subagent(allowed_tools=["ghost"])
    with pytest.raises(ValueError, match="ghost"):
        assembly.build_subagent_spec(
            cfg,
            parent_tools=[echo],
            parent_model=_parent_model(),
            resolve_model=lambda reference: _parent_model(),
            tool_index=_tool_index(),
        )


# ---------------------------------------------------------------------------
# max_turns gate — looping fake model is terminated
# ---------------------------------------------------------------------------


def _task_tool_call(subagent_type: str = "helper") -> AIMessage:
    return AIMessage(
        content="",
        tool_calls=[
            {
                "name": "task",
                "args": {
                    "description": "run the helper",
                    "subagent_type": subagent_type,
                    "task": "loop forever",
                },
                "id": "task-1",
                "type": "tool_call",
            }
        ],
    )


def test_max_turns_gate_stops_looping_subagent(
    skills_root: Path, mock_catalog: list[dict[str, Any]], mock_memory: dict[str, Any]
) -> None:
    """A looping subagent model is terminated exactly at the max_turns budget."""
    loop_call = AIMessage(
        content="", tool_calls=[{"name": "echo", "args": {"text": "hi"}, "id": "s1", "type": "tool_call"}]
    )
    sub_model = ScriptedChatModel(responses=[loop_call])
    parent_model = ScriptedChatModel(responses=[_task_tool_call(), AIMessage(content="all done")])

    monkeypatch_registry = pytest.MonkeyPatch()
    try:
        models = {"parent": parent_model, "sub": sub_model}
        _patch_llm_seams(monkeypatch_registry, models)

        app_cfg = _make_app(model="parent")
        sub_cfg = _make_subagent(model="sub", max_turns=2)

        graph = asyncio.run(
            assembly.compile_agent_app(
                app_cfg,
                subagent_cfgs=[sub_cfg],
                user_id="user-1",
                session=object(),
                checkpointer=MemorySaver(),
            )
        )
        result = asyncio.run(
            graph.ainvoke(
                {"messages": [HumanMessage(content="go")]},
                config={"configurable": {"thread_id": "t-max-turns"}},
            )
        )
    finally:
        monkeypatch_registry.undo()

    # The subagent model looped: exactly max_turns real calls were allowed.
    assert sub_model.n == 2
    contents = [str(m.content) for m in result["messages"]]
    assert any("turn limit" in text.lower() for text in contents)
    assert contents[-1] == "all done"


# ---------------------------------------------------------------------------
# validate_publish
# ---------------------------------------------------------------------------


def test_validate_publish_accepts_subset_of_catalog(mock_catalog: list[dict[str, Any]]) -> None:
    """Whitelists contained in the catalog pass validation."""
    app_cfg = _make_app(allowed_tools=["echo"])
    sub_cfg = _make_subagent(allowed_tools=["upper"])
    assembly.validate_publish(app_cfg, [sub_cfg], mock_catalog, _default_llm_configs())  # must not raise


def test_validate_publish_rejects_out_of_catalog_tools(mock_catalog: list[dict[str, Any]]) -> None:
    """Whitelist entries outside the catalog raise, listing app and subagent offenders."""
    app_cfg = _make_app(allowed_tools=["echo", "ghost"])
    sub_cfg = _make_subagent(allowed_tools=["phantom"])
    with pytest.raises(ValueError, match="ghost") as exc_info:
        assembly.validate_publish(app_cfg, [sub_cfg], mock_catalog, _default_llm_configs())
    assert "phantom" in str(exc_info.value)


def test_validate_publish_none_model_requires_default_config(mock_catalog: list[dict[str, Any]]) -> None:
    """A NULL model reference resolves to the default config and must exist."""
    app_cfg = _make_app(model=None)
    with pytest.raises(ValueError, match="default"):
        assembly.validate_publish(app_cfg, [], mock_catalog, {})


def test_validate_publish_rejects_missing_and_disabled_llm_refs(mock_catalog: list[dict[str, Any]]) -> None:
    """Missing and disabled LlmConfig references are aggregated into one error."""
    app_cfg = _make_app(model="missing")
    sub_cfg = _make_subagent(model="frozen")
    configs = {"default": _make_llm_config(), "frozen": _make_llm_config(name="frozen", enabled=False)}
    with pytest.raises(ValueError, match="missing") as exc_info:
        assembly.validate_publish(app_cfg, [sub_cfg], mock_catalog, configs)
    assert "frozen" in str(exc_info.value)
    assert "disabled" in str(exc_info.value)


# ---------------------------------------------------------------------------
# fingerprint
# ---------------------------------------------------------------------------


def _fingerprint_inputs(**overrides: Any) -> dict[str, Any]:
    defaults: dict[str, Any] = {
        "app_cfg": _make_app(),
        "subagent_cfgs": [_make_subagent()],
        "skill_hashes": {"greet": "hash-a"},
        "mcp_fingerprint": "srv:hash-1",
        "llm_fingerprint": "default:hash-1",
    }
    defaults.update(overrides)
    return defaults


def test_compute_fingerprint_is_stable() -> None:
    """Identical inputs always produce the same sha256 fingerprint."""
    first = assembly.compute_fingerprint(**_fingerprint_inputs())
    second = assembly.compute_fingerprint(**_fingerprint_inputs())
    assert first == second
    assert len(first) == 64


def test_compute_fingerprint_is_subagent_order_insensitive() -> None:
    """Subagents are projected sorted by name: row order never changes the hash."""
    alpha = _make_subagent(name="alpha")
    beta = _make_subagent(name="beta")
    forward = assembly.compute_fingerprint(**_fingerprint_inputs(subagent_cfgs=[alpha, beta]))
    reversed_order = assembly.compute_fingerprint(**_fingerprint_inputs(subagent_cfgs=[beta, alpha]))
    assert forward == reversed_order


@pytest.mark.parametrize(
    "mutation",
    [
        {"app_cfg": _make_app(system_prompt="changed prompt")},
        {"app_cfg": _make_app(allowed_tools=["echo"])},
        {"app_cfg": _make_app(model="gpt-5")},
        {"app_cfg": _make_app(skill_names=["other"])},
        {"subagent_cfgs": [_make_subagent(max_turns=4)]},
        {"subagent_cfgs": [_make_subagent(system_prompt="new sub prompt")]},
        {"skill_hashes": {"greet": "hash-b"}},
        {"mcp_fingerprint": "srv:hash-2"},
        {"llm_fingerprint": "default:hash-2"},
    ],
)
def test_compute_fingerprint_changes_with_sensitive_fields(mutation: dict[str, Any]) -> None:
    """Any sensitive-field mutation changes the fingerprint."""
    baseline = assembly.compute_fingerprint(**_fingerprint_inputs())
    mutated = assembly.compute_fingerprint(**_fingerprint_inputs(**mutation))
    assert mutated != baseline


# ---------------------------------------------------------------------------
# get_or_compile LRU cache
# ---------------------------------------------------------------------------


def _counter_value(result: str) -> float:
    """Read the current value of agent_graph_cache_hits_total{result}."""
    return float(agent_graph_cache_hits_total.labels(result=result)._value.get())  # noqa: SLF001 — test introspection


def test_get_or_compile_counts_miss_then_hit(
    skills_root: Path, mock_catalog: list[dict[str, Any]], mock_memory: dict[str, Any], monkeypatch: pytest.MonkeyPatch
) -> None:
    """First lookup misses and compiles; the identical second lookup hits the cache."""
    model = _parent_model()
    _patch_llm_seams(monkeypatch, {"default": model})

    kwargs = _fingerprint_inputs()
    runtime = {"user_id": "user-1", "session": object(), "checkpointer": MemorySaver()}

    misses_before = _counter_value("miss")
    hits_before = _counter_value("hit")

    first = asyncio.run(assembly.get_or_compile(**kwargs, **runtime))
    second = asyncio.run(assembly.get_or_compile(**kwargs, **runtime))

    assert first is second
    assert _counter_value("miss") == misses_before + 1
    assert _counter_value("hit") == hits_before + 1


def test_get_or_compile_without_checkpointer_never_caches(
    skills_root: Path, mock_catalog: list[dict[str, Any]], mock_memory: dict[str, Any], monkeypatch: pytest.MonkeyPatch
) -> None:
    """checkpointer=None compiles are returned but never written to the cache."""
    model = _parent_model()
    _patch_llm_seams(monkeypatch, {"default": model})

    kwargs = _fingerprint_inputs()
    runtime = {"user_id": "user-1", "session": object(), "checkpointer": None}

    misses_before = _counter_value("miss")

    first = asyncio.run(assembly.get_or_compile(**kwargs, **runtime))
    second = asyncio.run(assembly.get_or_compile(**kwargs, **runtime))

    assert first is not second  # recompiled on every call
    assert _counter_value("miss") == misses_before + 2
    assert assembly._compile_cache == {}  # noqa: SLF001 — cache stayed clean


def test_get_or_compile_with_checkpointer_caches(
    skills_root: Path, mock_catalog: list[dict[str, Any]], mock_memory: dict[str, Any], monkeypatch: pytest.MonkeyPatch
) -> None:
    """checkpointer-bound compiles keep the cache-write path."""
    model = _parent_model()
    _patch_llm_seams(monkeypatch, {"default": model})

    kwargs = _fingerprint_inputs()
    runtime = {"user_id": "user-1", "session": object(), "checkpointer": MemorySaver()}

    asyncio.run(assembly.get_or_compile(**kwargs, **runtime))

    assert len(assembly._compile_cache) == 1  # noqa: SLF001 — cache introspection


# ---------------------------------------------------------------------------
# compile_agent_app end-to-end (MemorySaver, scripted model)
# ---------------------------------------------------------------------------


def test_compile_agent_app_end_to_end_with_memory_injection(
    skills_root: Path, mock_catalog: list[dict[str, Any]], mock_memory: dict[str, Any], monkeypatch: pytest.MonkeyPatch
) -> None:
    """One-turn ainvoke succeeds, syncs skills and injects the memory section."""
    # Global skill on disk so sync_user_skills can copy it for the user.
    skill_dir = skills_root / "global" / "greet"
    skill_dir.mkdir(parents=True)
    (skill_dir / "SKILL.md").write_text(
        "---\nname: greet\ndescription: greeting skill\n---\n\n# Greet\n\nSay hi politely.\n",
        encoding="utf-8",
    )

    model = _parent_model("hello from the app")
    _patch_llm_seams(monkeypatch, {"default": model})

    app_cfg = _make_app(skill_names=["greet"])
    graph = asyncio.run(
        assembly.compile_agent_app(
            app_cfg,
            subagent_cfgs=[],
            user_id="user-42",
            session=object(),
            checkpointer=MemorySaver(),
        )
    )

    # Skills were synced into the per-user directory.
    assert (skills_root / "users" / "user-42" / "greet" / "SKILL.md").exists()

    result = asyncio.run(
        graph.ainvoke(
            {"messages": [HumanMessage(content="what is my favourite drink?")]},
            config={
                "configurable": {"thread_id": "t-e2e"},
                "metadata": {"user_id": "user-42"},
            },
        )
    )

    contents = [str(m.content) for m in result["messages"]]
    assert contents[-1] == "hello from the app"

    # MemoryMiddleware queried the memory service with the user's last message.
    assert mock_memory["calls"] == [("user-42", "what is my favourite drink?")]

    # The system prompt sent to the model carries the AgentApp prompt as the
    # main body plus the injected long-term memory section.
    first_call = model.calls[0]
    assert isinstance(first_call[0], SystemMessage)
    system_text = "".join(block.get("text", "") for block in first_call[0].content_blocks)
    assert "You are the demo app." in system_text
    assert "user likes tea" in system_text


def test_compile_agent_app_injects_username_time_and_memory_fallback_per_turn(
    skills_root: Path, mock_catalog: list[dict[str, Any]], mock_memory: dict[str, Any], monkeypatch: pytest.MonkeyPatch
) -> None:
    """Dynamic context (username, current time, memory fallback) is injected per turn.

    Regression guard for the frozen-prompt bug: the stored AgentApp prompt is
    static, so username / current date-time / long-term memory must be added
    by the middleware on every model call instead of being baked into the DB.
    """
    mock_memory["result"] = ""  # empty memory -> fallback placeholder

    model = _parent_model("hi")
    _patch_llm_seams(monkeypatch, {"default": model})

    app_cfg = _make_app()
    graph = asyncio.run(
        assembly.compile_agent_app(
            app_cfg,
            subagent_cfgs=[],
            user_id="user-77",
            session=object(),
            checkpointer=MemorySaver(),
        )
    )

    asyncio.run(
        graph.ainvoke(
            {"messages": [HumanMessage(content="hello")]},
            config={
                "configurable": {"thread_id": "t-dyn"},
                "metadata": {"user_id": "user-77", "username": "alice"},
            },
        )
    )

    first_call = model.calls[0]
    assert isinstance(first_call[0], SystemMessage)
    system_text = "".join(block.get("text", "") for block in first_call[0].content_blocks)
    # Static app prompt stays the main body.
    assert "You are the demo app." in system_text
    # Username context injected from request metadata.
    assert "You are talking to alice" in system_text
    # Memory fallback placeholder when the search returns nothing.
    assert "No relevant memory found." in system_text
    # Current date and time reflect the request moment (not DB bootstrap time).
    assert "# Current date and time" in system_text
    assert str(datetime.now().year) in system_text
