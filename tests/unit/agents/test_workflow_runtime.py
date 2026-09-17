"""Unit tests for the chatflow wrapper graph + WorkflowAppRuntime (Phase 3).

Zero real network / zero real LLM / zero real workflow engine: the registry
is a fake recording every ``execute_workflow`` payload, the wrapper graph uses
``MemorySaver`` for real message accumulation + replay, and ``memory_service``
is stubbed. These tests pin the D1-D5 decisions: a thin single-node graph
holding the ``messages`` channel feeds the full history to the workflow as a
black box and projects the reply back as one AIMessage.
"""

import asyncio
import json
from collections.abc import Generator
from datetime import datetime
from typing import Any

import pytest
from langchain_core.messages import AIMessage, BaseMessage, HumanMessage
from langgraph.checkpoint.memory import MemorySaver

from app.models.agent_assets import AgentApp
from app.schemas import Message
from app.services.agents import runtime
from app.services.agents.chatflow_graph import build_chatflow_graph, extract_reply
from app.services.agents.workflow_bridge import (
    get_workflow_registry,
    reset_workflow_registry,
    set_workflow_registry,
)
from app.workflow.registry import RunResult

pytestmark = pytest.mark.unit


# ---------------------------------------------------------------------------
# Fakes & fixtures
# ---------------------------------------------------------------------------


class FakeRegistry:
    """Records each ``execute_workflow`` payload and replays a canned output."""

    def __init__(
        self,
        *,
        output_messages: list[BaseMessage] | None = None,
        output: dict[str, Any] | None = None,
        status: str = "success",
        error_message: str | None = None,
    ) -> None:
        """Bind the canned reply / status this fake replays on every call."""
        self.calls: list[dict[str, Any]] = []
        self._output_messages = output_messages
        self._output = output
        self._status = status
        self._error_message = error_message

    def has_workflow(self, workflow_id: str) -> bool:
        return True

    def execute_workflow(self, workflow_id: str, input_data: dict[str, Any]) -> RunResult:
        self.calls.append(input_data)
        if self._output is not None:
            output = dict(self._output)
        elif self._output_messages is not None:
            output = {"messages": list(self._output_messages)}
        else:
            output = {}
        now = datetime.now()
        return RunResult(
            workflow_id=workflow_id,
            run_id="run-fixed",
            output=output,
            execution_logs=[],
            started_at=now,
            finished_at=now,
            status=self._status,  # type: ignore[arg-type]
            error_message=self._error_message,
        )


@pytest.fixture(autouse=True)
def reset_bridge() -> Generator[None, None, None]:
    """Isolate the module-level registry bridge between tests."""
    reset_workflow_registry()
    yield
    reset_workflow_registry()


@pytest.fixture(autouse=True)
def mock_memory(monkeypatch: pytest.MonkeyPatch) -> dict[str, Any]:
    """Stub memory_service.add/search so the fire-and-forget write is inert."""
    record: dict[str, Any] = {"add_calls": [], "search_calls": []}

    async def fake_add(user_id: str | None, messages: list[dict], metadata: dict | None = None) -> None:
        record["add_calls"].append((user_id, messages, metadata))

    async def fake_search(user_id: str | None, query: str) -> str:
        record["search_calls"].append((user_id, query))
        return ""

    monkeypatch.setattr(runtime.memory_service, "add", fake_add)
    monkeypatch.setattr(runtime.memory_service, "search", fake_search)
    return record


def _make_app(**overrides: Any) -> AgentApp:
    """Build a published workflow-engine AgentApp row."""
    defaults: dict[str, Any] = {
        "name": "chatflow-demo",
        "system_prompt": "",
        "skill_names": [],
        "subagent_names": [],
        "interrupt_on": {},
        "engine": "workflow",
        "workflow_id": "wf-1",
        "status": "published",
        "version": 1,
    }
    defaults.update(overrides)
    return AgentApp(**defaults)


def _make_runtime(registry: FakeRegistry, *, app: AgentApp | None = None) -> tuple[Any, FakeRegistry]:
    """Wire a fake registry into a WorkflowAppRuntime over a real MemorySaver."""
    set_workflow_registry(registry)  # type: ignore[arg-type]
    saver = MemorySaver()
    cfg = app or _make_app()
    graph = build_chatflow_graph(cfg.workflow_id or "wf-1", saver)
    rt = runtime.WorkflowAppRuntime(app_cfg=cfg, graph=graph, checkpointer=saver)
    return rt, registry


async def _drain_pending() -> None:
    """Let fire-and-forget background tasks (memory add) complete."""
    for _ in range(5):
        await asyncio.sleep(0)


# ---------------------------------------------------------------------------
# extract_reply — D5 reply projection
# ---------------------------------------------------------------------------


def test_extract_reply_from_last_ai_message() -> None:
    """The last AIMessage text of output['messages'] is the reply."""
    output = {"messages": [HumanMessage(content="q"), AIMessage(content="hi")]}
    assert extract_reply(output) == "hi"


def test_extract_reply_falls_back_to_json_without_ai_message() -> None:
    """No messages / no AIMessage -> deterministic JSON of the whole output."""
    assert json.loads(extract_reply({"foo": "bar"})) == {"foo": "bar"}
    # messages present but the last one is not an AI message -> JSON fallback.
    text = extract_reply({"messages": [HumanMessage(content="only human")]})
    assert "messages" in text


def test_extract_reply_handles_structured_ai_content() -> None:
    """Block-list AI content is flattened to its text parts."""
    output = {"messages": [AIMessage(content=[{"type": "text", "text": "part one"}])]}
    assert extract_reply(output) == "part one"


# ---------------------------------------------------------------------------
# ainvoke — reply projection + stateless replay (D2)
# ---------------------------------------------------------------------------


def test_ainvoke_returns_assistant_reply() -> None:
    """One turn returns the workflow reply as the trailing assistant message."""
    registry = FakeRegistry(output_messages=[HumanMessage(content="q"), AIMessage(content="hi")])
    rt, _ = _make_runtime(registry)

    result = asyncio.run(rt.ainvoke([Message(role="user", content="hi")], session_id="s1", user_id="1"))

    assert result[-1].role == "assistant"
    assert result[-1].content == "hi"
    # The wrapper fed the conversation through the messages channel.
    assert registry.calls and "messages" in registry.calls[0]


def test_second_turn_replays_prior_ai_message() -> None:
    """Stateless replay: turn 2 re-feeds the accumulated history incl. the AI reply."""
    registry = FakeRegistry(output_messages=[AIMessage(content="hi")])
    rt, _ = _make_runtime(registry)

    asyncio.run(rt.ainvoke([Message(role="user", content="first")], session_id="s2", user_id="1"))
    asyncio.run(rt.ainvoke([Message(role="user", content="second")], session_id="s2", user_id="1"))

    assert len(registry.calls) == 2
    second_messages = registry.calls[1]["messages"]
    assert any(isinstance(m, AIMessage) and "hi" in str(m.content) for m in second_messages)


def test_ainvoke_failed_run_raises() -> None:
    """A failed RunResult surfaces as an exception (base class logs invoke_failed)."""
    registry = FakeRegistry(output_messages=[AIMessage(content="hi")], status="failed", error_message="boom")
    rt, _ = _make_runtime(registry)

    with pytest.raises(Exception, match="boom"):
        asyncio.run(rt.ainvoke([Message(role="user", content="hi")], session_id="s3", user_id="1"))


# ---------------------------------------------------------------------------
# astream — single coordinator chunk (D4)
# ---------------------------------------------------------------------------


def test_astream_yields_reply_chunk() -> None:
    """Astream yields at least one coordinator chunk carrying the reply text."""
    registry = FakeRegistry(output_messages=[AIMessage(content="hi")])
    rt, _ = _make_runtime(registry)

    async def collect() -> list[Any]:
        chunks: list[Any] = []
        async for chunk in rt.astream([Message(role="user", content="hi")], session_id="s4", user_id="1"):
            chunks.append(chunk)
        return chunks

    chunks = asyncio.run(collect())
    assert chunks, "expected at least one chunk"
    assert all(isinstance(chunk, runtime.StreamChunk) for chunk in chunks)
    assert "hi" in "".join(chunk.content for chunk in chunks)
    assert all(chunk.source == "coordinator" for chunk in chunks if chunk.type == "message")


# ---------------------------------------------------------------------------
# history / clear
# ---------------------------------------------------------------------------


def test_get_chat_history_returns_projected_messages() -> None:
    """History reflects the accumulated thread after a turn."""
    registry = FakeRegistry(output_messages=[AIMessage(content="hi")])
    rt, _ = _make_runtime(registry)

    asyncio.run(rt.ainvoke([Message(role="user", content="hello")], session_id="s5", user_id="1"))
    history = asyncio.run(rt.get_chat_history("s5"))

    roles = [m.role for m in history]
    assert "user" in roles and "assistant" in roles
    assert history[-1].content == "hi"


def test_clear_chat_history_empties_thread() -> None:
    """Clearing drops every checkpoint of the thread."""
    registry = FakeRegistry(output_messages=[AIMessage(content="hi")])
    rt, _ = _make_runtime(registry)

    asyncio.run(rt.ainvoke([Message(role="user", content="hello")], session_id="s6", user_id="1"))
    asyncio.run(rt.clear_chat_history("s6"))

    assert asyncio.run(rt.get_chat_history("s6")) == []


def test_bridge_registry_is_the_fake() -> None:
    """Sanity: the bridge hands the chatflow node the fake registry."""
    registry = FakeRegistry(output_messages=[AIMessage(content="hi")])
    set_workflow_registry(registry)  # type: ignore[arg-type]
    assert get_workflow_registry() is registry
