"""Unit tests for the AgentApp runtime layer and the default-app bootstrap.

Zero real network / zero real LLM: models are scripted ``BaseChatModel``
subclasses, the checkpointer is ``MemorySaver``, the DB session is a fake
in-memory object, and ``memory_service`` / MCP catalog lookups are
monkeypatched. Tests never import ``deepagents`` themselves — only
``app.services.agents.assembly`` is allowed to do so.
"""

import asyncio
import json
import uuid
from collections.abc import Generator
from pathlib import Path
from typing import Any

import pytest
from langchain_core.language_models.chat_models import BaseChatModel
from langchain_core.messages import AIMessage, BaseMessage, ToolMessage
from langchain_core.outputs import ChatGeneration, ChatResult
from langchain_core.tools import BaseTool, tool
from langgraph.checkpoint.memory import MemorySaver
from prometheus_client import REGISTRY
from sqlalchemy.exc import IntegrityError
from sqlmodel import Session, SQLModel, create_engine

from app.core.config import settings
from app.core.prompts import load_static_system_prompt
from app.models.agent_assets import DEFAULT_LLM_CONFIG_NAME, AgentApp, LlmConfig, SubAgentConfig
from app.schemas import Message
from app.services.agents import assembly, bootstrap, runtime

pytestmark = pytest.mark.unit


# ---------------------------------------------------------------------------
# Fakes & fixtures (mirrors test_assembly.py conventions)
# ---------------------------------------------------------------------------


class ScriptedChatModel(BaseChatModel):
    """Deterministic chat model replaying canned AIMessages (zero network)."""

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


@pytest.fixture(autouse=True)
def clean_caches() -> Generator[None, None, None]:
    """Isolate the process-level compile and runtime caches between tests."""
    assembly.clear_compile_cache()
    runtime.clear_runtime_cache()
    yield
    assembly.clear_compile_cache()
    runtime.clear_runtime_cache()


@pytest.fixture(autouse=True)
def skills_root(tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> Path:
    """Redirect settings.SKILLS_ROOT into an isolated tmp directory."""
    root = tmp_path / "skills"
    monkeypatch.setattr(settings, "SKILLS_ROOT", str(root))
    return root


@pytest.fixture(autouse=True)
def mock_catalog(monkeypatch: pytest.MonkeyPatch) -> None:
    """Stub build_tool_catalog/get_mcp_tools so no MCP server is contacted."""

    async def fake_build_tool_catalog(session: Any) -> list[dict[str, Any]]:
        return [{"name": "echo", "source": "builtin"}]

    async def fake_get_mcp_tools(session: Any) -> list[BaseTool]:
        return []

    monkeypatch.setattr(assembly, "build_tool_catalog", fake_build_tool_catalog)
    monkeypatch.setattr(assembly, "get_mcp_tools", fake_get_mcp_tools)


@pytest.fixture(autouse=True)
def mock_memory(monkeypatch: pytest.MonkeyPatch) -> dict[str, Any]:
    """Stub memory_service.search/add and record every call."""
    record: dict[str, Any] = {"search_calls": [], "add_calls": []}

    async def fake_search(user_id: str | None, query: str) -> str:
        record["search_calls"].append((user_id, query))
        return ""

    async def fake_add(user_id: str | None, messages: list[dict], metadata: dict | None = None) -> None:
        record["add_calls"].append((user_id, messages, metadata))

    monkeypatch.setattr(runtime.memory_service, "search", fake_search)
    monkeypatch.setattr(runtime.memory_service, "add", fake_add)
    return record


def _make_app(**overrides: Any) -> AgentApp:
    """Build an AgentApp row with sensible defaults for runtime tests."""
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


def _echo_call_message() -> AIMessage:
    """Scripted model turn requesting the echo tool."""
    return AIMessage(
        content="",
        tool_calls=[{"name": "echo", "args": {"text": "hi"}, "id": "e1", "type": "tool_call"}],
    )


def _patch_llm_seams(monkeypatch: pytest.MonkeyPatch, model: ScriptedChatModel) -> None:
    """Redirect the DB-backed resolution seam to the scripted model."""

    def fake_load(session: Any, reference: str | None) -> LlmConfig:
        name = reference or DEFAULT_LLM_CONFIG_NAME
        return LlmConfig(name=name, model_name=name, api_key="sk-test", content_hash=f"h-{name}")

    monkeypatch.setattr(assembly, "load_llm_config", fake_load)
    monkeypatch.setattr(assembly, "build_chat_model", lambda cfg: model)


def _compile_runtime(model: ScriptedChatModel, app_cfg: AgentApp, monkeypatch: pytest.MonkeyPatch) -> Any:
    """Compile ``app_cfg`` with a MemorySaver and wrap it in a runtime."""
    _patch_llm_seams(monkeypatch, model)
    saver = MemorySaver()
    graph = asyncio.run(
        assembly.compile_agent_app(
            app_cfg,
            subagent_cfgs=[],
            user_id="user-1",
            session=object(),
            checkpointer=saver,
        )
    )
    return runtime.DeepAgentsAppRuntime(app_cfg=app_cfg, graph=graph, checkpointer=saver)


async def _drain_pending() -> None:
    """Let fire-and-forget background tasks (memory add) complete."""
    for _ in range(5):
        await asyncio.sleep(0)


def _hist_count(metric_name: str, **labels: str) -> float:
    """Read the current sample count of a Prometheus histogram label set."""
    value = REGISTRY.get_sample_value(f"{metric_name}_count", labels)
    return value or 0.0


# ---------------------------------------------------------------------------
# ainvoke — happy path + memory write-back
# ---------------------------------------------------------------------------


def test_ainvoke_success_and_memory_writeback(mock_memory: dict[str, Any], monkeypatch: pytest.MonkeyPatch) -> None:
    """One-turn ainvoke returns the assistant reply and fires memory add."""
    model = ScriptedChatModel(responses=[AIMessage(content="hello there")])
    rt = _compile_runtime(model, _make_app(), monkeypatch)

    result = asyncio.run(rt.ainvoke([Message(role="user", content="hi")], session_id="s1", user_id="u1", username="ann"))

    # Same projection semantics as graph.py: user + assistant messages returned.
    assert [message.role for message in result] == ["user", "assistant"]
    assert result[-1].content == "hello there"

    asyncio.run(_drain_pending())
    assert len(mock_memory["add_calls"]) == 1
    user_id, openai_msgs, metadata = mock_memory["add_calls"][0]
    assert user_id == "u1"
    assert any(entry["role"] == "user" and entry["content"] == "hi" for entry in openai_msgs)
    assert metadata["session_id"] == "s1"


# ---------------------------------------------------------------------------
# HIL — interrupt then resume
# ---------------------------------------------------------------------------


def test_ainvoke_interrupt_then_resume(mock_memory: dict[str, Any], monkeypatch: pytest.MonkeyPatch) -> None:
    """interrupt_on pauses before the tool; a second call resumes to completion."""
    model = ScriptedChatModel(responses=[_echo_call_message(), AIMessage(content="final answer")])
    rt = _compile_runtime(model, _make_app(interrupt_on={"echo": True}), monkeypatch)

    first = asyncio.run(
        rt.ainvoke([Message(role="user", content="call echo")], session_id="s-hil", user_id="u1", username="ann")
    )
    assert [message.role for message in first] == ["assistant"]
    assert first[0].content  # interrupt value surfaced as assistant text
    assert first[0].content != "final answer"

    second = asyncio.run(
        rt.ainvoke(
            [Message(role="user", content='{"decisions": [{"type": "approve"}]}')],
            session_id="s-hil",
            user_id="u1",
            username="ann",
        )
    )
    assert second[-1].role == "assistant"
    assert second[-1].content == "final answer"


def test_build_resume_value_fallback_rejects_pending_actions() -> None:
    """Unstructured replies default to rejecting every pending action."""
    rt = runtime.DeepAgentsAppRuntime(app_cfg=_make_app(), graph=None, checkpointer=None)  # pyright: ignore[reportArgumentType]
    interrupt = {"action_requests": [{"name": "a"}, {"name": "b"}]}
    assert rt._build_resume_value([Message(role="user", content="yes")], interrupt) == {
        "decisions": [{"type": "reject"}, {"type": "reject"}]
    }
    # Non-dict interrupt values cannot infer the pending count -> single reject.
    assert rt._build_resume_value([Message(role="user", content="yes")], "plain") == {
        "decisions": [{"type": "reject"}]
    }
    # Explicit structured decisions still pass through untouched.
    explicit = {"decisions": [{"type": "approve"}]}
    assert rt._build_resume_value([Message(role="user", content=json.dumps(explicit))], interrupt) == explicit


def test_hil_natural_language_reply_rejects_tool(mock_memory: dict[str, Any], monkeypatch: pytest.MonkeyPatch) -> None:
    """A plain-text reply resumes safely: the pending tool is rejected, not approved."""
    model = ScriptedChatModel(responses=[_echo_call_message(), AIMessage(content="understood, skipped")])
    rt = _compile_runtime(model, _make_app(interrupt_on={"echo": True}), monkeypatch)

    first = asyncio.run(
        rt.ainvoke([Message(role="user", content="call echo")], session_id="s-nl", user_id="u1", username="ann")
    )
    assert first[0].content  # interrupt surfaced

    second = asyncio.run(
        rt.ainvoke([Message(role="user", content="no, skip it")], session_id="s-nl", user_id="u1", username="ann")
    )
    assert second[-1].content == "understood, skipped"

    resume_messages = model.calls[-1]
    assert any(isinstance(message, ToolMessage) and "rejected" in str(message.content) for message in resume_messages)
    assert not any("echo: hi" in str(message.content) for message in resume_messages)


# ---------------------------------------------------------------------------
# astream — chunk projection + interrupt as final chunk
# ---------------------------------------------------------------------------


def test_astream_yields_coordinator_chunks(mock_memory: dict[str, Any], monkeypatch: pytest.MonkeyPatch) -> None:
    """Astream yields StreamChunks only; done semantics stay with the caller."""
    model = ScriptedChatModel(responses=[AIMessage(content="streamed reply")])
    rt = _compile_runtime(model, _make_app(), monkeypatch)

    async def collect() -> list[Any]:
        chunks: list[Any] = []
        async for chunk in rt.astream(
            [Message(role="user", content="hi")], session_id="s2", user_id="u1", username="ann"
        ):
            chunks.append(chunk)
        return chunks

    chunks = asyncio.run(collect())
    assert chunks, "expected at least one chunk"
    assert all(isinstance(chunk, runtime.StreamChunk) for chunk in chunks)
    assert "".join(chunk.content for chunk in chunks) == "streamed reply"
    assert all(chunk.source == "coordinator" for chunk in chunks)

    asyncio.run(_drain_pending())
    assert len(mock_memory["add_calls"]) == 1


def test_astream_interrupt_is_final_system_chunk(mock_memory: dict[str, Any], monkeypatch: pytest.MonkeyPatch) -> None:
    """An interrupt surfaces as the final chunk tagged source="system"."""
    model = ScriptedChatModel(responses=[_echo_call_message()])
    rt = _compile_runtime(model, _make_app(interrupt_on={"echo": True}), monkeypatch)

    async def collect() -> list[Any]:
        chunks: list[Any] = []
        async for chunk in rt.astream(
            [Message(role="user", content="call echo")], session_id="s3", user_id="u1", username="ann"
        ):
            chunks.append(chunk)
        return chunks

    chunks = asyncio.run(collect())
    assert chunks, "expected the interrupt chunk"
    assert chunks[-1].source == "system"
    assert chunks[-1].content  # interrupt value text

    asyncio.run(_drain_pending())
    assert mock_memory["add_calls"] == []  # interrupted runs skip memory write-back


def test_stream_observes_subagent_task_duration_only_for_subagents() -> None:
    """Per-subagent first/last chunk deltas are observed; coordinator is not timed."""
    from langchain_core.messages import AIMessageChunk

    class FakeStreamingGraph:
        def __init__(self, chunks: list[Any]) -> None:
            self._chunks = chunks

        async def astream(self, graph_input: Any, config: Any, stream_mode: str, subgraphs: bool) -> Any:
            for chunk in self._chunks:
                yield chunk

    chunks = [
        (("researcher:abc",), (AIMessageChunk(content="part1"), {"lc_agent_name": "researcher"})),
        (("researcher:abc",), (AIMessageChunk(content="part2"), {"lc_agent_name": "researcher"})),
        ((), (AIMessageChunk(content="final"), {})),
    ]
    rt = runtime.DeepAgentsAppRuntime(app_cfg=_make_app(), graph=FakeStreamingGraph(chunks), checkpointer=None)  # pyright: ignore[reportArgumentType]

    before = _hist_count("subagent_task_duration_seconds", subagent="researcher")

    async def collect() -> list[Any]:
        return [chunk async for chunk in rt._stream(None, {})]

    collected = asyncio.run(collect())
    assert [chunk.source for chunk in collected] == ["researcher", "researcher", "coordinator"]
    assert _hist_count("subagent_task_duration_seconds", subagent="researcher") == before + 1


def test_first_interrupt_value_handles_empty_tasks_and_interrupts() -> None:
    """The unified interrupt extractor never raises IndexError."""

    class EmptyTasks:
        tasks: tuple[()] = ()

    assert runtime._first_interrupt_value(EmptyTasks(), default="fb") == "fb"  # noqa: SLF001 — unit under test

    class NoInterrupts:
        interrupts: tuple[()] = ()

    class TaskWithoutInterrupts:
        tasks = (NoInterrupts(),)

    assert runtime._first_interrupt_value(TaskWithoutInterrupts()) is None  # noqa: SLF001 — unit under test


# ---------------------------------------------------------------------------
# history & clear with MemorySaver
# ---------------------------------------------------------------------------


def test_history_and_clear_roundtrip(mock_memory: dict[str, Any], monkeypatch: pytest.MonkeyPatch) -> None:
    """get_chat_history reflects the turn; clear_chat_history empties it."""
    model = ScriptedChatModel(responses=[AIMessage(content="remembered")])
    rt = _compile_runtime(model, _make_app(), monkeypatch)

    asyncio.run(rt.ainvoke([Message(role="user", content="hi")], session_id="s4", user_id="u1", username="ann"))

    history = asyncio.run(rt.get_chat_history("s4"))
    assert (len(history), history[-1].content) == (2, "remembered")
    assert [message.role for message in history] == ["user", "assistant"]

    asyncio.run(rt.clear_chat_history("s4"))
    assert asyncio.run(rt.get_chat_history("s4")) == []


def test_clear_without_checkpointer_raises_runtime_error(monkeypatch: pytest.MonkeyPatch) -> None:
    """clear_chat_history without a checkpointer fails loudly (500 contract), no fake 200."""
    model = ScriptedChatModel(responses=[AIMessage(content="x")])
    rt = _compile_runtime(model, _make_app(), monkeypatch)
    degraded = runtime.DeepAgentsAppRuntime(app_cfg=rt.app_cfg, graph=rt._graph, checkpointer=None)  # noqa: SLF001 — test seam
    with pytest.raises(RuntimeError, match="checkpointer"):
        asyncio.run(degraded.clear_chat_history("s-x"))


# ---------------------------------------------------------------------------
# LLM inference duration metric (regression: observe point restored)
# ---------------------------------------------------------------------------


def test_ainvoke_and_astream_observe_llm_inference_duration(
    mock_memory: dict[str, Any], monkeypatch: pytest.MonkeyPatch
) -> None:
    """Both execution paths observe llm_inference_duration_seconds{model}."""
    model = ScriptedChatModel(responses=[AIMessage(content="done")])
    rt = _compile_runtime(model, _make_app(), monkeypatch)
    label = {"model": settings.DEFAULT_LLM_MODEL}
    before = _hist_count("llm_inference_duration_seconds", **label)

    asyncio.run(rt.ainvoke([Message(role="user", content="hi")], session_id="m1", user_id="u1", username="ann"))
    assert _hist_count("llm_inference_duration_seconds", **label) == before + 1

    async def drain_stream() -> None:
        async for _ in rt.astream([Message(role="user", content="hi")], session_id="m2", user_id="u1", username="ann"):
            pass

    asyncio.run(drain_stream())
    asyncio.run(_drain_pending())
    assert _hist_count("llm_inference_duration_seconds", **label) == before + 2


def test_memory_writeback_failure_is_contained_and_tasks_tracked(
    mock_memory: dict[str, Any], monkeypatch: pytest.MonkeyPatch
) -> None:
    """A failing memory add never breaks the reply; tasks are tracked and cleaned up."""

    async def failing_add(user_id: Any, messages: Any, metadata: Any = None) -> None:
        raise RuntimeError("memory down")

    monkeypatch.setattr(runtime.memory_service, "add", failing_add)
    model = ScriptedChatModel(responses=[AIMessage(content="ok")])
    rt = _compile_runtime(model, _make_app(), monkeypatch)

    result = asyncio.run(rt.ainvoke([Message(role="user", content="hi")], session_id="s-f", user_id="u1", username="ann"))
    assert result[-1].content == "ok"

    asyncio.run(_drain_pending())
    assert runtime._pending_tasks == set()  # noqa: SLF001 — done callbacks cleaned up


# ---------------------------------------------------------------------------
# get_runtime — resolution, errors and cache semantics
# ---------------------------------------------------------------------------


def _patch_get_runtime_seams(monkeypatch: pytest.MonkeyPatch, model: ScriptedChatModel, app_cfg: AgentApp) -> None:
    """Patch every DB/pool seam of get_runtime plus the assembly compile path."""

    async def fake_resolve(session: Any, agent_app_id: Any) -> AgentApp:
        return app_cfg

    monkeypatch.setattr(runtime, "_resolve_agent_app", fake_resolve)
    monkeypatch.setattr(runtime, "_load_subagents", lambda session, names: [])

    async def fake_skill_hashes(session: Any, names: Any) -> dict[str, str]:
        return {}

    async def fake_mcp_fingerprint(session: Any) -> str:
        return ""

    async def fake_llm_fingerprint(session: Any, app_cfg: Any, subagent_cfgs: Any) -> tuple[str, str]:
        return f"{DEFAULT_LLM_CONFIG_NAME}:h-default", "real-model-x"

    async def fake_checkpointer() -> Any:
        return MemorySaver()

    monkeypatch.setattr(runtime, "_load_skill_hashes", fake_skill_hashes)
    monkeypatch.setattr(runtime, "_load_mcp_fingerprint", fake_mcp_fingerprint)
    monkeypatch.setattr(runtime, "_load_llm_fingerprint", fake_llm_fingerprint)
    monkeypatch.setattr(runtime, "_build_checkpointer", fake_checkpointer)
    _patch_llm_seams(monkeypatch, model)


def test_get_runtime_draft_raises(monkeypatch: pytest.MonkeyPatch) -> None:
    """Draft apps are rejected with ValueError."""
    _patch_get_runtime_seams(monkeypatch, ScriptedChatModel(responses=[]), _make_app(status="draft"))
    with pytest.raises(ValueError, match="not published"):
        asyncio.run(runtime.get_runtime(object(), "1"))


def test_get_runtime_unknown_engine_raises(monkeypatch: pytest.MonkeyPatch) -> None:
    """Unknown engine backends are rejected with ValueError."""
    _patch_get_runtime_seams(monkeypatch, ScriptedChatModel(responses=[]), _make_app(engine="quantum"))
    with pytest.raises(ValueError, match="unknown engine"):
        asyncio.run(runtime.get_runtime(object(), "1"))


def test_get_runtime_cache_returns_same_instance(monkeypatch: pytest.MonkeyPatch) -> None:
    """Identical (app_id, fingerprint) lookups return the cached runtime."""
    _patch_get_runtime_seams(monkeypatch, ScriptedChatModel(responses=[AIMessage(content="x")]), _make_app())
    first = asyncio.run(runtime.get_runtime(object(), "1"))
    second = asyncio.run(runtime.get_runtime(object(), "1"))
    assert first is second
    assert isinstance(first, runtime.DeepAgentsAppRuntime)


def test_get_runtime_fingerprint_change_builds_new_instance(monkeypatch: pytest.MonkeyPatch) -> None:
    """A fingerprint change (prompt edit) yields a fresh runtime instance."""
    model = ScriptedChatModel(responses=[AIMessage(content="x")])
    _patch_get_runtime_seams(monkeypatch, model, _make_app())
    first = asyncio.run(runtime.get_runtime(object(), "1"))

    v2 = _make_app(system_prompt="v2")

    async def resolve_v2(session: Any, agent_app_id: Any) -> AgentApp:
        return v2

    monkeypatch.setattr(runtime, "_resolve_agent_app", resolve_v2)
    second = asyncio.run(runtime.get_runtime(object(), "1"))
    assert first is not second


def test_get_runtime_workflow_engine_placeholder(monkeypatch: pytest.MonkeyPatch) -> None:
    """Workflow apps resolve to the placeholder runtime that always raises."""
    _patch_get_runtime_seams(monkeypatch, ScriptedChatModel(responses=[]), _make_app(engine="workflow"))
    rt = asyncio.run(runtime.get_runtime(object(), "1"))
    assert isinstance(rt, runtime.WorkflowAppRuntime)
    with pytest.raises(NotImplementedError, match="workflow engine runtime reserved"):
        asyncio.run(rt.get_chat_history("s"))


def test_hil_disabled_without_checkpointer_copies_config(monkeypatch: pytest.MonkeyPatch) -> None:
    """No checkpointer => interrupt_on stripped from a copy of the config."""
    app_cfg = _make_app(interrupt_on={"echo": True})
    _patch_get_runtime_seams(monkeypatch, ScriptedChatModel(responses=[AIMessage(content="x")]), app_cfg)

    async def no_checkpointer() -> Any:
        return None

    monkeypatch.setattr(runtime, "_build_checkpointer", no_checkpointer)

    rt = asyncio.run(runtime.get_runtime(object(), "1"))
    assert isinstance(rt, runtime.DeepAgentsAppRuntime)
    assert rt.app_cfg.interrupt_on == {}  # degraded copy
    assert app_cfg.interrupt_on == {"echo": True}  # original untouched


def test_hil_degraded_runtime_not_cached(monkeypatch: pytest.MonkeyPatch) -> None:
    """A checkpointer-less degraded runtime is never written to the cache."""
    app_cfg = _make_app(interrupt_on={"echo": True})
    _patch_get_runtime_seams(monkeypatch, ScriptedChatModel(responses=[AIMessage(content="x")]), app_cfg)

    async def no_checkpointer() -> Any:
        return None

    monkeypatch.setattr(runtime, "_build_checkpointer", no_checkpointer)

    first = asyncio.run(runtime.get_runtime(object(), "1"))
    second = asyncio.run(runtime.get_runtime(object(), "1"))
    assert first is not second  # rebuilt every time once the pool recovers
    assert runtime._runtime_cache == {}  # noqa: SLF001 — cache stayed clean


def test_runtime_cache_evicts_stale_fingerprints_of_same_app(monkeypatch: pytest.MonkeyPatch) -> None:
    """Writing a new fingerprint for one app drops its stale cache entries."""
    app_cfg = _make_app()
    app_cfg.id = 5
    _patch_get_runtime_seams(monkeypatch, ScriptedChatModel(responses=[AIMessage(content="x")]), app_cfg)
    asyncio.run(runtime.get_runtime(object(), "5"))

    v2 = _make_app(system_prompt="v2")
    v2.id = 5

    async def resolve_v2(session: Any, agent_app_id: Any) -> AgentApp:
        return v2

    monkeypatch.setattr(runtime, "_resolve_agent_app", resolve_v2)
    asyncio.run(runtime.get_runtime(object(), "5"))

    keys = list(runtime._runtime_cache)  # noqa: SLF001 — cache introspection
    assert len(keys) == 1 and keys[0][0] == 5


def test_get_runtime_exposes_resolved_model_name(monkeypatch: pytest.MonkeyPatch) -> None:
    """The runtime caches the resolved model_name and metrics label uses it."""
    _patch_get_runtime_seams(monkeypatch, ScriptedChatModel(responses=[AIMessage(content="x")]), _make_app())
    rt = asyncio.run(runtime.get_runtime(object(), "1"))
    assert rt.resolved_model_name == "real-model-x"  # real model name, not a reference
    assert rt._model_label() == "real-model-x"  # noqa: SLF001 — unit under test


def test_model_label_falls_back_to_default_model_when_unresolved() -> None:
    """Without a resolved model_name the label degrades to settings.DEFAULT_LLM_MODEL."""
    rt = runtime.DeepAgentsAppRuntime(app_cfg=_make_app(), graph=None, checkpointer=None)  # pyright: ignore[reportArgumentType]
    assert rt.resolved_model_name is None
    assert rt._model_label() == settings.DEFAULT_LLM_MODEL  # noqa: SLF001 — unit under test


def test_load_subagents_orders_by_name() -> None:
    """_load_subagents sorts rows by name (stable fingerprint input)."""

    class RecordingSession:
        def __init__(self) -> None:
            self.statements: list[Any] = []

        def exec(self, statement: Any) -> FakeExecResult:
            self.statements.append(statement)
            return FakeExecResult([])

    session = RecordingSession()
    assert runtime._load_subagents(session, ["b", "a"]) == []  # noqa: SLF001 — unit under test
    assert "ORDER BY" in str(session.statements[0]).upper()


def test_resolve_agent_app_lazy_bootstraps_missing_default(monkeypatch: pytest.MonkeyPatch) -> None:
    """A missing default app triggers one lazy ensure_default_agent_app call."""
    created = _make_app(name="default")

    async def fake_ensure(session: Any) -> AgentApp:
        return created

    monkeypatch.setattr(runtime, "ensure_default_agent_app", fake_ensure)
    session = FakeDBSession(default_app=None)
    resolved = asyncio.run(runtime._resolve_agent_app(session, None))  # noqa: SLF001 — unit under test
    assert resolved is created


def test_resolve_agent_app_reuses_existing_default(monkeypatch: pytest.MonkeyPatch) -> None:
    """An existing default app short-circuits the lazy bootstrap."""
    existing = _make_app(name="default")
    calls: list[int] = []

    async def fake_ensure(session: Any) -> AgentApp:
        calls.append(1)
        return existing

    monkeypatch.setattr(runtime, "ensure_default_agent_app", fake_ensure)
    session = FakeDBSession(default_app=existing)
    resolved = asyncio.run(runtime._resolve_agent_app(session, None))  # noqa: SLF001 — unit under test
    assert resolved is existing
    assert calls == []


# ---------------------------------------------------------------------------
# bootstrap.ensure_default_agent_app
# ---------------------------------------------------------------------------


@pytest.fixture
def patched_llm_bootstrap(monkeypatch: pytest.MonkeyPatch) -> LlmConfig:
    """Isolate the FakeDBSession bootstrap tests from the LlmConfig seeding.

    ``ensure_default_agent_app`` provisions the default LlmConfig first; the
    fake session cannot serve that lookup, so the seam returns a canned row.
    """
    default_llm = LlmConfig(
        name=DEFAULT_LLM_CONFIG_NAME,
        model_name=settings.DEFAULT_LLM_MODEL,
        api_key="sk-seeded",
        content_hash="h-seeded",
    )

    async def fake_ensure(session: Any) -> LlmConfig:
        return default_llm

    monkeypatch.setattr(bootstrap, "ensure_default_llm_config", fake_ensure)
    return default_llm


class FakeExecResult:
    """Minimal stand-in for a SQLModel exec() cursor."""

    def __init__(self, rows: list[Any] | None = None, rowcount: int = 0) -> None:
        """Store the canned rows and rowcount."""
        self._rows = rows or []
        self.rowcount = rowcount

    def first(self) -> Any:
        """Return the first row or None."""
        return self._rows[0] if self._rows else None

    def all(self) -> list[Any]:
        """Return every row."""
        return list(self._rows)


class FakeDBSession:
    """In-memory SQLModel Session double for bootstrap tests."""

    def __init__(self, default_app: AgentApp | None = None, backfill_rowcount: int = 3) -> None:
        """Configure the canned default app row and backfill rowcount."""
        self.default_app = default_app
        self.backfill_rowcount = backfill_rowcount
        self.added: list[Any] = []
        self.committed = 0
        self.executed: list[Any] = []

    def get(self, model: Any, pk: Any) -> Any:
        """Primary-key lookup (unused by bootstrap)."""
        return None

    def add(self, obj: Any) -> None:
        """Record the inserted row."""
        self.added.append(obj)

    def commit(self) -> None:
        """Count the commit and assign a fake id to inserted rows."""
        self.committed += 1
        for obj in self.added:
            if getattr(obj, "id", None) is None and hasattr(obj, "id"):
                obj.id = 7

    def rollback(self) -> None:
        """No-op rollback (records nothing; the fake keeps its state)."""
        return None

    def refresh(self, obj: Any) -> None:
        """No-op refresh (ids are assigned at commit time)."""
        return None

    def exec(self, statement: Any) -> FakeExecResult:
        """Record the statement and return canned select/update results."""
        self.executed.append(statement)
        statement_type = type(statement).__name__
        if statement_type.startswith("Select"):
            return FakeExecResult(rows=[self.default_app] if self.default_app else [])
        return FakeExecResult(rowcount=self.backfill_rowcount)  # backfill update


def test_bootstrap_creates_default_app_and_backfills(
    mock_memory: dict[str, Any], patched_llm_bootstrap: LlmConfig
) -> None:
    """First call creates the published default app and backfills sessions."""
    session = FakeDBSession(default_app=None)
    app_cfg = asyncio.run(bootstrap.ensure_default_agent_app(session))

    assert app_cfg.name == "default"
    assert app_cfg.status == "published"
    assert app_cfg.engine == "deepagents"
    assert app_cfg.allowed_tools is None
    assert app_cfg.id == 7
    assert app_cfg.published_hash and len(app_cfg.published_hash) == 64
    # The llm fingerprint embeds the seeded default config content hash.
    recomputed = assembly.compute_fingerprint(
        app_cfg, [], {}, "", f"{DEFAULT_LLM_CONFIG_NAME}:{patched_llm_bootstrap.content_hash}"
    )
    assert app_cfg.published_hash == recomputed
    assert app_cfg.system_prompt  # static baseline template
    assert session.committed == 2  # insert + backfill

    # Single backfill UPDATE executed after the insert.
    update_statements = [stmt for stmt in session.executed if type(stmt).__name__.startswith("Update")]
    assert len(update_statements) == 1


def test_bootstrap_is_idempotent(mock_memory: dict[str, Any], patched_llm_bootstrap: LlmConfig) -> None:
    """A second call reuses the existing row and still runs the backfill."""
    existing = _make_app(name="default", status="published", published_hash="h" * 64)
    existing.id = 9
    session = FakeDBSession(default_app=existing)

    first = asyncio.run(bootstrap.ensure_default_agent_app(session))
    second = asyncio.run(bootstrap.ensure_default_agent_app(session))

    assert first is existing and second is existing
    assert session.added == []  # never inserted twice
    update_statements = [stmt for stmt in session.executed if type(stmt).__name__.startswith("Update")]
    assert len(update_statements) == 2  # backfill runs on every call


def test_static_system_prompt_strips_dynamic_segments() -> None:
    """The static template keeps no per-request placeholders or sections."""
    prompt = load_static_system_prompt()
    assert "# Role: A world class assistant" in prompt
    assert "{" not in prompt
    assert "# What you know about the user" not in prompt
    assert "# Current date and time" not in prompt


def test_bootstrap_stores_static_template(mock_memory: dict[str, Any], patched_llm_bootstrap: LlmConfig) -> None:
    """The default app persists the static template, never frozen dynamics."""
    session = FakeDBSession(default_app=None)
    app_cfg = asyncio.run(bootstrap.ensure_default_agent_app(session))

    assert app_cfg.system_prompt == load_static_system_prompt()
    assert "alice" not in app_cfg.system_prompt
    assert "# Current date and time" not in app_cfg.system_prompt


def test_bootstrap_refreshes_frozen_legacy_prompt(
    mock_memory: dict[str, Any], patched_llm_bootstrap: LlmConfig
) -> None:
    """A legacy row holding a frozen rendered prompt is migrated in place."""
    frozen = (
        "# Role: A world class assistant\n...\n# User\nYou are talking to bob.\n"
        "# Current date and time\n2024-01-01 00:00:00\n"
    )
    existing = _make_app(name="default", status="published", published_hash="f" * 64)
    existing.id = 9
    existing.system_prompt = frozen
    session = FakeDBSession(default_app=existing)

    app_cfg = asyncio.run(bootstrap.ensure_default_agent_app(session))

    assert app_cfg is existing
    assert app_cfg.system_prompt == load_static_system_prompt()
    assert app_cfg.published_hash != "f" * 64  # fingerprint recomputed
    assert session.committed == 2  # migration update + backfill
    assert session.added == []


def test_bootstrap_skips_update_when_prompt_current(
    mock_memory: dict[str, Any], patched_llm_bootstrap: LlmConfig
) -> None:
    """A row already on the static template is left untouched (no UPDATE)."""
    existing = _make_app(name="default", status="published", published_hash="e" * 64)
    existing.id = 9
    existing.system_prompt = load_static_system_prompt()
    session = FakeDBSession(default_app=existing)

    asyncio.run(bootstrap.ensure_default_agent_app(session))

    assert session.committed == 1  # backfill only, no prompt migration


def test_bootstrap_recovers_from_concurrent_insert(
    mock_memory: dict[str, Any], patched_llm_bootstrap: LlmConfig
) -> None:
    """An IntegrityError on insert (multi-worker race) rolls back and re-queries."""
    winner = _make_app(name="default", status="published", published_hash="w" * 64)
    winner.id = 11

    class RacingSession(FakeDBSession):
        def commit(self) -> None:
            if self.committed == 0:
                self.committed += 1
                self.default_app = winner  # the other worker won the race
                raise IntegrityError("insert", {}, Exception("duplicate key"))
            super().commit()

    session = RacingSession(default_app=None)
    app_cfg = asyncio.run(bootstrap.ensure_default_agent_app(session))

    assert app_cfg is winner
    assert session.added and session.added[0] is not winner  # loser row discarded


def test_bootstrap_tolerates_unseeded_default_llm_config(
    mock_memory: dict[str, Any], monkeypatch: pytest.MonkeyPatch
) -> None:
    """A skipped LlmConfig seed (empty API key) degrades the fingerprint to ''."""

    async def skipped_ensure(session: Any) -> LlmConfig | None:
        return None

    monkeypatch.setattr(bootstrap, "ensure_default_llm_config", skipped_ensure)
    session = FakeDBSession(default_app=None)

    app_cfg = asyncio.run(bootstrap.ensure_default_agent_app(session))

    assert app_cfg.name == "default"
    recomputed = assembly.compute_fingerprint(app_cfg, [], {}, "", "")
    assert app_cfg.published_hash == recomputed


# ---------------------------------------------------------------------------
# bootstrap.ensure_default_llm_config
# ---------------------------------------------------------------------------


@pytest.fixture
def llm_bootstrap_session() -> Generator[Session, None, None]:
    """In-memory SQLite session for the default LlmConfig bootstrap tests."""
    engine = create_engine("sqlite://")
    SQLModel.metadata.create_all(engine)
    with Session(engine) as session:
        yield session


@pytest.fixture
def llm_seed_settings(monkeypatch: pytest.MonkeyPatch) -> None:
    """Fix the environment seed sources of ensure_default_llm_config."""
    monkeypatch.setattr(settings, "OPENAI_API_KEY", "sk-seed-key")
    monkeypatch.setattr(settings, "DEFAULT_LLM_MODEL", "MiniMax-M3")
    monkeypatch.setattr(settings, "DEFAULT_LLM_TEMPERATURE", 0.2)
    monkeypatch.setattr(settings, "MAX_TOKENS", 2048)
    monkeypatch.setenv("OPENAI_BASE_URL", "https://proxy.example.com/v1")


def test_ensure_default_llm_config_seeds_from_environment(
    llm_bootstrap_session: Session, llm_seed_settings: None
) -> None:
    """First call inserts the default config from the environment seed sources."""
    cfg = asyncio.run(bootstrap.ensure_default_llm_config(llm_bootstrap_session))

    assert cfg is not None
    assert cfg.name == DEFAULT_LLM_CONFIG_NAME
    assert cfg.model_name == "MiniMax-M3"
    assert cfg.api_key == "sk-seed-key"
    assert cfg.base_url == "https://proxy.example.com/v1"
    assert cfg.temperature == 0.2
    # Never freeze the process-level token budget: None = provider default.
    assert cfg.max_tokens is None
    assert cfg.content_hash and len(cfg.content_hash) == 64


def test_ensure_default_llm_config_skips_seed_without_api_key(
    llm_bootstrap_session: Session, llm_seed_settings: None, monkeypatch: pytest.MonkeyPatch
) -> None:
    """An empty OPENAI_API_KEY skips seeding entirely (retry next startup)."""
    monkeypatch.setattr(settings, "OPENAI_API_KEY", "")

    cfg = asyncio.run(bootstrap.ensure_default_llm_config(llm_bootstrap_session))

    assert cfg is None
    assert llm_bootstrap_session.get(LlmConfig, DEFAULT_LLM_CONFIG_NAME) is None


def test_ensure_default_llm_config_empty_key_keeps_existing_row(
    llm_bootstrap_session: Session, llm_seed_settings: None, monkeypatch: pytest.MonkeyPatch
) -> None:
    """The empty-key guard never touches a pre-existing row."""
    monkeypatch.setattr(settings, "OPENAI_API_KEY", "")
    edited = LlmConfig(
        name=DEFAULT_LLM_CONFIG_NAME, model_name="kept", api_key="sk-kept", content_hash="h-kept"
    )
    llm_bootstrap_session.add(edited)
    llm_bootstrap_session.commit()

    cfg = asyncio.run(bootstrap.ensure_default_llm_config(llm_bootstrap_session))

    assert cfg is not None and cfg.model_name == "kept"


def test_ensure_default_llm_config_never_overwrites_existing(
    llm_bootstrap_session: Session, llm_seed_settings: None
) -> None:
    """A pre-existing (admin-edited) row is returned untouched, never reseeded."""
    edited = LlmConfig(
        name=DEFAULT_LLM_CONFIG_NAME,
        model_name="custom-model",
        api_key="sk-admin-edited",
        description="edited by admin",
        content_hash="h-edited",
    )
    llm_bootstrap_session.add(edited)
    llm_bootstrap_session.commit()

    cfg = asyncio.run(bootstrap.ensure_default_llm_config(llm_bootstrap_session))

    assert cfg.model_name == "custom-model"
    assert cfg.api_key == "sk-admin-edited"
    assert cfg.content_hash == "h-edited"  # untouched, not recomputed


def test_ensure_default_llm_config_recovers_from_concurrent_insert(
    llm_bootstrap_session: Session, llm_seed_settings: None, monkeypatch: pytest.MonkeyPatch
) -> None:
    """An IntegrityError on insert rolls back and adopts the concurrent winner."""
    winner = LlmConfig(
        name=DEFAULT_LLM_CONFIG_NAME,
        model_name="winner-model",
        api_key="sk-winner",
        content_hash="h-winner",
    )
    call_state = {"failed": False}
    real_commit = llm_bootstrap_session.commit

    def racing_commit() -> None:
        if not call_state["failed"]:
            call_state["failed"] = True
            # The other worker won: their row appears before our retry.
            llm_bootstrap_session.rollback()
            llm_bootstrap_session.add(winner)
            real_commit()
            raise IntegrityError("insert", {}, Exception("duplicate key"))
        real_commit()

    monkeypatch.setattr(llm_bootstrap_session, "commit", racing_commit)

    cfg = asyncio.run(bootstrap.ensure_default_llm_config(llm_bootstrap_session))

    assert cfg.name == DEFAULT_LLM_CONFIG_NAME
    assert cfg.model_name == "winner-model"


# ---------------------------------------------------------------------------
# runtime._load_llm_fingerprint
# ---------------------------------------------------------------------------


def _sub_cfg(name: str, model: str | None) -> SubAgentConfig:
    return SubAgentConfig(
        name=name,
        description="d",
        when_to_use="w",
        system_prompt="p",
        model=model,
        content_hash=f"h-{name}",
        version=1,
    )


def test_load_llm_fingerprint_collects_referenced_configs(llm_bootstrap_session: Session) -> None:
    """The fingerprint covers app + subagent references (NULL -> default)."""
    for row in [
        LlmConfig(name=DEFAULT_LLM_CONFIG_NAME, model_name="m1", api_key="k1", content_hash="h-a"),
        LlmConfig(name="minimax", model_name="m2", api_key="k2", content_hash="h-b"),
    ]:
        llm_bootstrap_session.add(row)
    llm_bootstrap_session.commit()

    app_cfg = _make_app(model="minimax")
    fingerprint, resolved_model_name = asyncio.run(
        runtime._load_llm_fingerprint(  # noqa: SLF001 — unit under test
            llm_bootstrap_session, app_cfg, [_sub_cfg("helper", None)]
        )
    )

    assert fingerprint == f"{DEFAULT_LLM_CONFIG_NAME}:h-a|minimax:h-b"
    assert resolved_model_name == "m2"  # explicit reference resolves the real model


def test_load_llm_fingerprint_null_model_resolves_default_model_name(
    llm_bootstrap_session: Session,
) -> None:
    """A NULL app model reference resolves the default config's model_name."""
    llm_bootstrap_session.add(
        LlmConfig(name=DEFAULT_LLM_CONFIG_NAME, model_name="real-default", api_key="k", content_hash="h-a")
    )
    llm_bootstrap_session.commit()

    fingerprint, resolved_model_name = asyncio.run(
        runtime._load_llm_fingerprint(llm_bootstrap_session, _make_app(model=None), [])  # noqa: SLF001
    )

    assert fingerprint == f"{DEFAULT_LLM_CONFIG_NAME}:h-a"
    assert resolved_model_name == "real-default"


def test_load_llm_fingerprint_missing_reference_raises(llm_bootstrap_session: Session) -> None:
    """A missing referenced config fails fast before compilation."""
    app_cfg = _make_app(model="ghost")
    with pytest.raises(ValueError, match="ghost"):
        asyncio.run(runtime._load_llm_fingerprint(llm_bootstrap_session, app_cfg, []))  # noqa: SLF001


def test_load_llm_fingerprint_disabled_reference_raises(llm_bootstrap_session: Session) -> None:
    """A disabled referenced config fails fast before compilation."""
    llm_bootstrap_session.add(
        LlmConfig(name="frozen", model_name="m", api_key="k", enabled=False, content_hash="h-f")
    )
    llm_bootstrap_session.commit()

    app_cfg = _make_app(model="frozen")
    with pytest.raises(ValueError, match="frozen"):
        asyncio.run(runtime._load_llm_fingerprint(llm_bootstrap_session, app_cfg, []))  # noqa: SLF001


# ---------------------------------------------------------------------------
# checkpointer DDL one-shot gate
# ---------------------------------------------------------------------------


def test_build_checkpointer_setup_runs_once_per_process(monkeypatch: pytest.MonkeyPatch) -> None:
    """Concurrent first-compiles run checkpointer.setup() exactly once."""
    monkeypatch.setattr(runtime, "_checkpointer_setup_done", False)
    setup_calls: list[int] = []

    class FakeSaver:
        def __init__(self, pool: Any) -> None:
            self.pool = pool

        async def setup(self) -> None:
            setup_calls.append(1)

    monkeypatch.setattr(runtime, "AsyncPostgresSaver", FakeSaver)

    async def fake_pool() -> Any:
        return object()

    monkeypatch.setattr(runtime, "get_shared_connection_pool", fake_pool)

    async def scenario() -> None:
        await asyncio.gather(runtime._build_checkpointer(), runtime._build_checkpointer())  # noqa: SLF001
        await runtime._build_checkpointer()  # noqa: SLF001 — a later compile too

    asyncio.run(scenario())

    assert len(setup_calls) == 1  # DDL never races across concurrent compiles
