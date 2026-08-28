"""Unit tests for the G4 runtime increments (spec-g4-chat §4.2/§4.4/§4.6).

Zero real network / zero real LLM: models are scripted ``BaseChatModel``
subclasses, the checkpointer is ``MemorySaver``, MCP catalog and memory
seams are monkeypatched (mirrors test_runtime.py conventions).

Covers: StreamChunk type/name fields, the interrupt projection
(``project_interrupt`` + ``get_pending_interrupt``), tool_call stream
frames, ``extra_callbacks`` and the turn-end summary detection.
"""

import asyncio
import json
import uuid
from collections.abc import Generator
from pathlib import Path
from typing import Any, Optional, override

import pytest
from langchain_core.callbacks import BaseCallbackHandler
from langchain_core.language_models.chat_models import BaseChatModel
from langchain_core.messages import AIMessage, BaseMessage, HumanMessage
from langchain_core.outputs import ChatGeneration, ChatResult
from langchain_core.tools import BaseTool, tool
from langgraph.checkpoint.memory import MemorySaver
from langgraph.types import StateSnapshot

from app.core.config import settings
from app.models.agent_assets import AgentApp
from app.models.provider import DEFAULT_MODEL_REF, ModelConfig, Provider
from app.schemas import Message
from app.services.agents import assembly, runtime

pytestmark = pytest.mark.unit


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


@pytest.fixture(autouse=True)
def clean_caches() -> Generator[None, None, None]:
    """Isolate the process-level compile and runtime caches between tests."""
    assembly.clear_compile_cache()
    runtime.clear_runtime_cache()
    yield
    assembly.clear_compile_cache()
    runtime.clear_runtime_cache()


@pytest.fixture(autouse=True)
def workspace_root(tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> Path:
    """Redirect workspace roots (DATA_ROOT + legacy SKILLS_ROOT) into tmp."""
    root = tmp_path / "data"
    monkeypatch.setattr(settings, "DATA_ROOT", str(root))
    monkeypatch.setattr(settings, "SKILLS_ROOT", str(root / "skills"))
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

    def fake_load(session: Any, reference: str | None) -> tuple[Provider, ModelConfig]:
        ref = reference or DEFAULT_MODEL_REF
        provider_name, _, model_name = ref.partition("/")
        provider = Provider(name=provider_name, type="OPENAI_COMPATIBLE", auth_config={"api_key": "sk-test"})
        provider.id = 1
        model_cfg = ModelConfig(
            provider_id=provider.id, name=model_name or provider_name, model_id=model_name or provider_name
        )
        return provider, model_cfg

    monkeypatch.setattr(assembly, "load_model_config", fake_load)
    monkeypatch.setattr(assembly, "build_chat_model", lambda provider, model_cfg: model)


def _compile_runtime(model: ScriptedChatModel, app_cfg: AgentApp, monkeypatch: pytest.MonkeyPatch) -> Any:
    """Compile ``app_cfg`` with a MemorySaver and wrap it in a runtime."""
    _patch_llm_seams(monkeypatch, model)
    saver = MemorySaver()
    graph = asyncio.run(
        assembly.compile_agent_app(
            object(),
            app_cfg,
            subagent_cfgs=[],
            user_id=1,
            checkpointer=saver,
        )
    )
    return runtime.DeepAgentsAppRuntime(app_cfg=app_cfg, graph=graph, checkpointer=saver)


async def _drain_pending() -> None:
    """Let fire-and-forget background tasks (memory add) complete."""
    for _ in range(5):
        await asyncio.sleep(0)


# ---------------------------------------------------------------------------
# B1 — StreamChunk type/name + project_interrupt + get_pending_interrupt
# ---------------------------------------------------------------------------


def test_stream_chunk_defaults_type_message_name_none() -> None:
    """StreamChunk defaults keep the pre-G4 shape (type=message, name=None)."""
    chunk = runtime.StreamChunk(content="hello", source="coordinator")
    assert chunk.type == "message"
    assert chunk.name is None


def test_stream_chunk_carries_type_and_name() -> None:
    """G4 chunks can be flagged tool_call/interrupt/summary with a tool name."""
    chunk = runtime.StreamChunk(content="out", source="coordinator", type="tool_call", name="echo")
    assert chunk.type == "tool_call"
    assert chunk.name == "echo"


def test_project_interrupt_keeps_only_tool_and_args() -> None:
    """Projection strips internal fields; the langchain key is ``name``."""
    value = {
        "action_requests": [
            {"name": "write_file", "args": {"path": "a.txt"}, "description": "writes a file"},
            {"tool": "bash", "args": {"cmd": "ls"}},  # legacy/spec spelling tolerated
        ],
        "review_configs": [{"foo": "bar"}],
    }
    projected = runtime.project_interrupt(value)
    assert projected == {
        "action_requests": [
            {"tool": "write_file", "args": {"path": "a.txt"}},
            {"tool": "bash", "args": {"cmd": "ls"}},
        ]
    }


def test_project_interrupt_returns_none_for_unprojectable_values() -> None:
    """Non-dict values and empty action lists cannot yield a projection."""
    assert runtime.project_interrupt("plain text") is None
    assert runtime.project_interrupt({"action_requests": []}) is None
    assert runtime.project_interrupt({"other": 1}) is None


def test_get_pending_interrupt_returns_projection_when_paused(
    mock_memory: dict[str, Any], monkeypatch: pytest.MonkeyPatch
) -> None:
    """After an interrupt the public probe returns the projected payload."""
    model = ScriptedChatModel(responses=[_echo_call_message()])
    rt = _compile_runtime(model, _make_app(interrupt_on={"echo": True}), monkeypatch)

    asyncio.run(
        rt.ainvoke([Message(role="user", content="call echo")], session_id="s-gpi", user_id="u1", username="ann")
    )

    projected = asyncio.run(rt.get_pending_interrupt("s-gpi"))
    assert projected == {"action_requests": [{"tool": "echo", "args": {"text": "hi"}}]}


def test_get_pending_interrupt_none_when_thread_live(
    mock_memory: dict[str, Any], monkeypatch: pytest.MonkeyPatch
) -> None:
    """A normally completed (or unknown) thread yields None."""
    model = ScriptedChatModel(responses=[AIMessage(content="done")])
    rt = _compile_runtime(model, _make_app(), monkeypatch)

    asyncio.run(rt.ainvoke([Message(role="user", content="hi")], session_id="s-live", user_id="u1", username="ann"))

    assert asyncio.run(rt.get_pending_interrupt("s-live")) is None
    assert asyncio.run(rt.get_pending_interrupt("s-unknown")) is None


# ---------------------------------------------------------------------------
# B2 — tool_call stream frames + typed interrupt tail chunk (§4.1)
# ---------------------------------------------------------------------------


@tool
def echo(text: str) -> str:
    """Echo the given text back."""
    return f"echo: {text}"


def _collect_chunks(rt: Any, session_id: str) -> list[Any]:
    """Drain one astream turn synchronously and return the chunks."""

    async def collect() -> list[Any]:
        chunks: list[Any] = []
        async for chunk in rt.astream(
            [Message(role="user", content="call echo")], session_id=session_id, user_id="u1", username="ann"
        ):
            chunks.append(chunk)
        return chunks

    return asyncio.run(collect())


def test_stream_emits_tool_call_frame_for_tool_message(
    mock_memory: dict[str, Any], monkeypatch: pytest.MonkeyPatch
) -> None:
    """An executed tool surfaces as a typed tool_call chunk (name+content)."""
    monkeypatch.setattr(assembly, "builtin_tools", [echo])
    model = ScriptedChatModel(responses=[_echo_call_message(), AIMessage(content="done")])
    rt = _compile_runtime(model, _make_app(), monkeypatch)

    chunks = _collect_chunks(rt, "s-toolcall")

    tool_frames = [chunk for chunk in chunks if chunk.type == "tool_call"]
    assert len(tool_frames) == 1
    assert tool_frames[0].name == "echo"
    assert tool_frames[0].content == "echo: hi"
    assert tool_frames[0].source == "coordinator"
    message_frames = [chunk for chunk in chunks if chunk.type == "message"]
    assert "".join(frame.content for frame in message_frames) == "done"


def test_astream_interrupt_tail_chunk_is_typed_projection(
    mock_memory: dict[str, Any], monkeypatch: pytest.MonkeyPatch
) -> None:
    """The interrupt tail chunk is typed and carries the §4.2 projection JSON."""
    model = ScriptedChatModel(responses=[_echo_call_message()])
    rt = _compile_runtime(model, _make_app(interrupt_on={"echo": True}), monkeypatch)

    chunks = _collect_chunks(rt, "s-interrupt")

    tail = chunks[-1]
    assert tail.type == "interrupt"
    assert tail.source == "system"
    assert json.loads(tail.content) == {"action_requests": [{"tool": "echo", "args": {"text": "hi"}}]}


# ---------------------------------------------------------------------------
# B3 — ainvoke/astream extra_callbacks (§7.2)
# ---------------------------------------------------------------------------


class RecordingHandler(BaseCallbackHandler):
    """Callback handler recording chat-model lifecycle events."""

    def __init__(self) -> None:
        """Initialise the event log."""
        self.events: list[str] = []

    def on_chat_model_start(self, serialized: dict[str, Any], messages: list[Any], **kwargs: Any) -> None:
        """Record the model start event."""
        self.events.append("model_start")


def test_ainvoke_accepts_extra_callbacks(mock_memory: dict[str, Any], monkeypatch: pytest.MonkeyPatch) -> None:
    """extra_callbacks ride along the invoke config and receive model events."""
    handler = RecordingHandler()
    model = ScriptedChatModel(responses=[AIMessage(content="ok")])
    rt = _compile_runtime(model, _make_app(), monkeypatch)

    asyncio.run(
        rt.ainvoke(
            [Message(role="user", content="hi")],
            session_id="s-cb-invoke",
            user_id="u1",
            username="ann",
            extra_callbacks=[handler],
        )
    )

    assert handler.events, "callback handler must observe the model call"


def test_astream_accepts_extra_callbacks(mock_memory: dict[str, Any], monkeypatch: pytest.MonkeyPatch) -> None:
    """extra_callbacks ride along the stream config and receive model events."""
    handler = RecordingHandler()
    model = ScriptedChatModel(responses=[AIMessage(content="ok")])
    rt = _compile_runtime(model, _make_app(), monkeypatch)

    async def collect() -> list[Any]:
        chunks: list[Any] = []
        async for chunk in rt.astream(
            [Message(role="user", content="hi")],
            session_id="s-cb-stream",
            user_id="u1",
            username="ann",
            extra_callbacks=[handler],
        ):
            chunks.append(chunk)
        return chunks

    chunks = asyncio.run(collect())

    assert chunks
    assert handler.events, "callback handler must observe the model call"


# ---------------------------------------------------------------------------
# B4 — turn-end summary detection (§4.1 summary frame, §4.3 compression)
# ---------------------------------------------------------------------------


def _snapshot(values: dict[str, Any]) -> StateSnapshot:
    """Build an empty-progress snapshot carrying the given state values."""
    return StateSnapshot(
        values=values,
        next=(),
        config={"configurable": {"thread_id": "s"}},
        tasks=(),
        interrupts=(),
        metadata=None,
        created_at=None,
        parent_config=None,
    )


class _ScriptedStateRuntime(runtime.AgentAppRuntime):
    """Template-level runtime replaying scripted state snapshots.

    ``_get_state`` pops snapshots in order and keeps serving the last one —
    this exercises the ``astream`` template logic (summary detection, hook
    wiring) without compiling a real graph.
    """

    def __init__(self, snapshots: list[StateSnapshot]) -> None:
        """Store the scripted snapshot sequence."""
        self.snapshots = list(snapshots)
        self.app_id: Optional[int] = None
        self._compression_seen: dict[str, tuple] = {}

    @override
    async def _get_state(self, config: Any) -> StateSnapshot:
        """Serve the next scripted snapshot (last one repeats)."""
        if len(self.snapshots) > 1:
            return self.snapshots.pop(0)
        return self.snapshots[0]

    @override
    async def _run(self, graph_input: Any, config: Any) -> dict[str, Any]:
        """Unused by astream; return an empty final state."""
        return {}

    @override
    async def _stream(self, graph_input: Any, config: Any) -> Any:
        """Yield one plain message chunk."""
        yield runtime.StreamChunk(content="reply", source="coordinator")

    @override
    async def _history(self, config: Any) -> list[BaseMessage]:
        """No history for scripted runtimes."""
        return []

    @override
    async def _clear(self, session_id: str) -> None:
        """No checkpoints to clear."""
        return None


def _summary_event_state(text: str = "Summary of earlier turns") -> dict[str, Any]:
    """State values carrying a fresh _summarization_event."""
    return {
        "messages": [],
        "_summarization_event": {
            "cutoff_index": 4,
            "summary_message": type("S", (), {"content": text})(),
            "file_path": "data/summaries/x.json",
        },
    }


def _collect_from(rt: Any) -> list[Any]:
    """Drain one scripted astream turn synchronously."""

    async def collect() -> list[Any]:
        chunks: list[Any] = []
        async for chunk in rt.astream(
            [Message(role="user", content="hi")], session_id="s-summary", user_id=None, username=None
        ):
            chunks.append(chunk)
        return chunks

    return asyncio.run(collect())


def test_astream_emits_summary_chunk_when_compression_occurred() -> None:
    """A fresh turn-end _summarization_event yields a typed summary chunk."""
    rt = _ScriptedStateRuntime(
        [
            _snapshot({"messages": []}),  # turn-start probe (no event yet)
            _snapshot({"messages": []}),  # _prepare_input resume check
            _snapshot(_summary_event_state()),  # turn end: compression happened
        ]
    )

    chunks = _collect_from(rt)

    summary_frames = [chunk for chunk in chunks if chunk.type == "summary"]
    assert len(summary_frames) == 1
    assert summary_frames[0].source == "system"
    assert summary_frames[0].content == "Summary of earlier turns"
    # Summary rides after the message chunk, before caller-added done frames.
    assert chunks[-1].type == "summary"


def test_astream_skips_summary_chunk_without_new_compression() -> None:
    """No event, or an unchanged fingerprint, emits no summary chunk."""
    same_state = _summary_event_state("Stable summary")
    no_event = _ScriptedStateRuntime([_snapshot({"messages": []})] * 3)
    unchanged = _ScriptedStateRuntime([_snapshot(same_state)] * 3)

    for rt in (no_event, unchanged):
        chunks = _collect_from(rt)
        assert not [chunk for chunk in chunks if chunk.type == "summary"]


# ---------------------------------------------------------------------------
# B5 — spike: aupdate_state append semantics + rebuild_thread (§6.2)
# ---------------------------------------------------------------------------


def test_spike_aupdate_state_appends_via_add_messages(
    mock_memory: dict[str, Any], monkeypatch: pytest.MonkeyPatch
) -> None:
    """Spike (§6.2 探索项): aupdate_state append semantics on add_messages.

    Conclusion recorded for the rebuild design (verified against a REAL
    compiled deepagents graph with tools, langgraph 1.2.x):
    - On an EMPTY thread a bare ``aupdate_state(config, {"messages": ...})``
      works (default write path), but once the thread HAS a checkpoint the
      bare call raises ``InvalidUpdateError: Ambiguous update, specify
      as_node`` (model+tools both write messages) — so rebuild must always
      pass ``as_node="model"`` (safe in both states).
    - With ``as_node="model"`` the batch seeds the empty checkpoint and
      repeat calls APPEND through the ``add_messages`` reducer (prior
      messages kept, ids auto-generated when absent) — no manual id
      stitching needed for L2 replay; re-running rebuild after a clear is
      therefore deterministic (幂等).
    - After rehydration a normal ``ainvoke`` continues on the same thread:
      the model receives the replayed history and its reply appends after
      it (rebuild → 续聊 continuity holds).
    """
    model = ScriptedChatModel(responses=[AIMessage(content="ok")])
    rt = _compile_runtime(model, _make_app(), monkeypatch)
    config = {"configurable": {"thread_id": "s-spike"}}

    batch = [HumanMessage(content="hello"), AIMessage(content="hi")]
    asyncio.run(rt._graph.aupdate_state(config, {"messages": batch}))  # empty thread: bare OK
    state = asyncio.run(rt._graph.aget_state(config))
    assert [message.content for message in state.values["messages"]] == ["hello", "hi"]

    with pytest.raises(Exception, match="[Aa]mbiguous update"):
        asyncio.run(rt._graph.aupdate_state(config, {"messages": [HumanMessage(content="x")]}))

    asyncio.run(rt._graph.aupdate_state(config, {"messages": [HumanMessage(content="second turn")]}, as_node="model"))
    state = asyncio.run(rt._graph.aget_state(config))
    assert [message.content for message in state.values["messages"]] == ["hello", "hi", "second turn"]

    asyncio.run(rt._graph.ainvoke({"messages": [HumanMessage(content="continue")]}, config))
    state = asyncio.run(rt._graph.aget_state(config))
    assert [message.content for message in state.values["messages"]] == [
        "hello",
        "hi",
        "second turn",
        "continue",
        "ok",
    ]


def test_rebuild_thread_base_runtime_raises() -> None:
    """Base/workflow runtimes reject rebuild explicitly (§6.2)."""
    rt = _ScriptedStateRuntime([])
    with pytest.raises(NotImplementedError):
        asyncio.run(rt.rebuild_thread("s-rb", [HumanMessage(content="x")]))


def test_rebuild_thread_rehydrates_checkpoint_for_continuity(
    mock_memory: dict[str, Any], monkeypatch: pytest.MonkeyPatch
) -> None:
    """DeepAgents rebuild_thread replays L2 rows; the next turn sees them."""
    model = ScriptedChatModel(responses=[AIMessage(content="final")])
    rt = _compile_runtime(model, _make_app(), monkeypatch)

    replayed = [HumanMessage(content="earlier question"), AIMessage(content="earlier answer")]
    asyncio.run(rt.rebuild_thread("s-rebuild", replayed))

    history = asyncio.run(rt.get_chat_history("s-rebuild"))
    assert [(message.role, message.content) for message in history] == [
        ("user", "earlier question"),
        ("assistant", "earlier answer"),
    ]

    # Continuity: the next invoke feeds the replayed history to the model.
    asyncio.run(
        rt.ainvoke([Message(role="user", content="continue")], session_id="s-rebuild", user_id="u1", username="ann")
    )
    seen = " ".join(str(message.content) for message in model.calls[0])
    assert "earlier question" in seen
    assert "earlier answer" in seen
