"""Unit tests for the G4 runtime increments (spec-g4-chat §4.2/§4.4/§4.6).

Zero real network / zero real LLM: models are scripted ``BaseChatModel``
subclasses, the checkpointer is ``MemorySaver``, MCP catalog and memory
seams are monkeypatched (mirrors test_runtime.py conventions).

Covers: StreamChunk type/name fields, the interrupt projection
(``project_interrupt`` + ``get_pending_interrupt``), tool_call stream
frames, ``extra_callbacks`` and the turn-end summary detection.
"""

import asyncio
import uuid
from collections.abc import Generator
from pathlib import Path
from typing import Any

import pytest
from langchain_core.language_models.chat_models import BaseChatModel
from langchain_core.messages import AIMessage, BaseMessage
from langchain_core.outputs import ChatGeneration, ChatResult
from langchain_core.tools import BaseTool
from langgraph.checkpoint.memory import MemorySaver

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
