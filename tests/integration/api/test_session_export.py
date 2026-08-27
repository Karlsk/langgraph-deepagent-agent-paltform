"""G3 session export integration tests (spec-g3-session §9.3).

Runtime-driven export flows: multiple ainvoke turns produce the L1
checkpoint + L2 JSONL through the production hooks, then the export
endpoint serves the transcript. Includes the HIL interrupt/resume
scenario: the paused turn writes no L2 row, the resumed decision turn
does — the export reflects the decision history.
"""

import asyncio
import time
from collections.abc import Generator
from typing import Any

import pytest
from langchain_core.messages import AIMessage
from langchain_core.tools import StructuredTool
from langgraph.checkpoint.memory import MemorySaver
from sqlmodel import Session as DBSession

from app.models.agent_assets import AgentApp
from app.models.user import User
from app.schemas.chat import Message
from app.services.agents import assembly, runtime as runtime_module
from tests.unit.agents.test_runtime import ScriptedChatModel

pytestmark = pytest.mark.integration

API: str = "/api/v1"


@pytest.fixture(autouse=True)
def clean_process_caches() -> Generator[None, None, None]:
    """Isolate the compile + runtime caches between tests."""
    assembly.clear_compile_cache()
    runtime_module.clear_runtime_cache()
    yield
    assembly.clear_compile_cache()
    runtime_module.clear_runtime_cache()


@pytest.fixture
def scripted_model(monkeypatch: pytest.MonkeyPatch) -> ScriptedChatModel:
    """Serve every chat-model construction from a scripted model."""
    model = ScriptedChatModel(responses=[AIMessage(content="default-reply")])
    monkeypatch.setattr(assembly, "build_chat_model", lambda provider, model_cfg: model)
    return model


@pytest.fixture
def memory_checkpointer(monkeypatch: pytest.MonkeyPatch) -> MemorySaver:
    """Attach a shared in-memory checkpointer to every runtime build."""
    saver = MemorySaver()

    async def fake_build_checkpointer() -> MemorySaver:
        return saver

    monkeypatch.setattr(runtime_module, "_build_checkpointer", fake_build_checkpointer)
    return saver


def _seed_published_app(db_engine: Any, *, allowed_tools: list[str] | None = None, **extra: Any) -> int:
    """Insert one published AgentApp row and return its id."""
    with DBSession(db_engine) as session:
        app = AgentApp(
            name="export-app",
            system_prompt="x",
            status="published",
            allowed_tools=allowed_tools or [],
            **extra,
        )
        session.add(app)
        session.commit()
        session.refresh(app)
        return int(app.id)


def _create_session(client: Any, headers: dict[str, str], app_id: int) -> str:
    """POST /sessions and return the new session id."""
    resp = client.post(f"{API}/sessions", json={"agent_app_id": app_id, "name": "exp"}, headers=headers)
    assert resp.status_code == 201, resp.text
    return resp.json()["data"]["session_id"]


async def _invoke_and_drain(rt: Any, *, session_id: str, user_id: int, text: str) -> list[Message]:
    """Run one ainvoke turn, then wait for the fire-and-forget L2 hook."""
    out = await rt.ainvoke(
        [Message(role="user", content=text)],
        session_id=session_id,
        user_id=str(user_id),
        username="alice",
    )
    deadline = time.monotonic() + 5.0
    while runtime_module._pending_tasks and time.monotonic() < deadline:  # noqa: SLF001 — test drain seam
        await asyncio.sleep(0.01)
    return out


def _run_turns(db_engine: Any, app_id: int, user: User, session_id: str, texts: list[str]) -> None:
    """Load the runtime and drive one drained turn per text."""
    with DBSession(db_engine) as db:
        rt = asyncio.run(runtime_module.get_runtime(db, app_id, user_id=user.id))
    for text in texts:
        asyncio.run(_invoke_and_drain(rt, session_id=session_id, user_id=user.id, text=text))


def test_runtime_invoke_to_export_full_flow(
    client: Any,
    user_headers: dict[str, str],
    db_engine: Any,
    user: User,
    scripted_model: ScriptedChatModel,
    memory_checkpointer: MemorySaver,
) -> None:
    """Multi-turn runtime output exports completely (§9.3)."""
    app_id = _seed_published_app(db_engine)
    session_id = _create_session(client, user_headers, app_id)
    scripted_model.responses = [AIMessage(content="answer-one"), AIMessage(content="answer-two")]

    _run_turns(db_engine, app_id, user, session_id, ["hello agent", "second question"])

    resp = client.get(f"{API}/sessions/{session_id}/export", params={"format": "json"}, headers=user_headers)
    assert resp.status_code == 200, resp.text
    payload = resp.json()
    assert payload["message_count"] == 4
    contents = [row["content"] for row in payload["messages"]]
    assert contents == ["hello agent", "answer-one", "second question", "answer-two"]
    roles = [row["role"] for row in payload["messages"]]
    assert roles == ["user", "assistant", "user", "assistant"]
    seqs = [row["seq"] for row in payload["messages"]]
    assert seqs == [1, 2, 3, 4]


@pytest.fixture
def hil_probe_tool(monkeypatch: pytest.MonkeyPatch) -> str:
    """Replace the builtin catalog with one plain tool (own interrupt_on gate)."""
    probe = StructuredTool.from_function(
        func=lambda: "probe-ok", name="fake_probe", description="fake probe tool"
    )
    monkeypatch.setattr(assembly, "builtin_tools", [probe])
    return probe.name


def test_export_after_hil_interrupt_includes_decision_history(
    client: Any,
    user_headers: dict[str, str],
    db_engine: Any,
    user: User,
    scripted_model: ScriptedChatModel,
    memory_checkpointer: MemorySaver,
    hil_probe_tool: str,
) -> None:
    """Interrupted turn writes no row; the resumed decision turn does."""
    app_id = _seed_published_app(
        db_engine,
        allowed_tools=[hil_probe_tool],
        interrupt_on={hil_probe_tool: True},
    )
    session_id = _create_session(client, user_headers, app_id)
    scripted_model.responses = [
        AIMessage(
            content="",
            tool_calls=[{"name": hil_probe_tool, "args": {}, "id": "tc-1", "type": "tool_call"}],
        ),
        AIMessage(content="approved-and-done"),
    ]

    with DBSession(db_engine) as db:
        rt = asyncio.run(runtime_module.get_runtime(db, app_id, user_id=user.id))

    first = asyncio.run(_invoke_and_drain(rt, session_id=session_id, user_id=user.id, text="start the job"))
    assert first[0].role == "assistant"  # interrupt surfaced as the reply

    decision = '{"decisions": [{"type": "approve"}]}'
    second = asyncio.run(_invoke_and_drain(rt, session_id=session_id, user_id=user.id, text=decision))
    assert second[-1].content == "approved-and-done"

    resp = client.get(f"{API}/sessions/{session_id}/export", headers=user_headers)
    assert resp.status_code == 200, resp.text
    messages = resp.json()["messages"]
    # The paused turn contributes nothing; the decision turn records the
    # user's approval payload and the final assistant answer.
    assert [row["content"] for row in messages] == [decision, "approved-and-done"]
    assert [row["role"] for row in messages] == ["user", "assistant"]


def test_export_consistent_with_get_messages(
    client: Any,
    user_headers: dict[str, str],
    db_engine: Any,
    user: User,
    scripted_model: ScriptedChatModel,
    memory_checkpointer: MemorySaver,
) -> None:
    """Export message_count and the detail endpoint share one source."""
    app_id = _seed_published_app(db_engine)
    session_id = _create_session(client, user_headers, app_id)
    scripted_model.responses = [AIMessage(content="c1"), AIMessage(content="c2"), AIMessage(content="c3")]

    _run_turns(db_engine, app_id, user, session_id, ["t1", "t2", "t3"])

    exported = client.get(f"{API}/sessions/{session_id}/export", headers=user_headers).json()
    detail = client.get(f"{API}/sessions/{session_id}", headers=user_headers).json()

    assert detail["data"]["message_count"] == exported["message_count"] == 6
    assert len(exported["messages"]) == 6
