"""G4 chat flow integration tests (spec-g4-chat §11.3).

Five end-to-end scenarios driven through the full ``api_router`` under
TestClient: real JWT auth path, in-memory SQLite, a scripted chat model and a
shared MemorySaver checkpointer — zero real PG, zero real LLM/network (R7):

1. non-streaming: ``POST /chat`` envelope -> history lands in L2 (+ traces row)
2. stream frame order: message(s) -> summary (real compression via a tiny
   ``context_size``; the SummarizationMiddleware reuses the scripted model) ->
   ``done(compressed=true)``
3. HIL: interrupt frame -> ``done(interrupted=true)`` -> decisions JSON resume
   (reject path: the network tool never executes) -> completion
4. pending restore: after an interrupt ``GET /messages`` reports
   ``pending_interrupt`` (the approval card can be rebuilt)
5. rebuild: checkpoint wiped -> ``POST /rebuild`` -> continued chat sees the
   re-injected history (coherence via the scripted model's recorded calls)
"""

import asyncio
import json
import time
from collections.abc import Generator
from typing import Any

import pytest
from langchain_core.messages import AIMessage
from langgraph.checkpoint.memory import MemorySaver
from sqlmodel import Session as DBSession

from app.core.config import settings
from app.models.agent_assets import AgentApp
from app.services.agents import assembly, runtime as runtime_module
from tests.unit.agents.test_runtime import ScriptedChatModel

pytestmark = pytest.mark.integration

API: str = settings.API_V1_STR

# The only network-free interruptible builtin tool (reject path never runs it).
SEARCH_TOOL = "duckduckgo_results_json"


@pytest.fixture(autouse=True)
def clean_process_caches() -> Generator[None, None, None]:
    """Isolate the compile + runtime caches between tests."""
    assembly.clear_compile_cache()
    runtime_module.clear_runtime_cache()
    yield
    assembly.clear_compile_cache()
    runtime_module.clear_runtime_cache()


@pytest.fixture(autouse=True)
def quiet_chat_options(monkeypatch: pytest.MonkeyPatch) -> None:
    """Session naming would call the LLM service (network); traces stay on."""
    monkeypatch.setattr(settings, "SESSION_NAMING_ENABLED", False)


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


# ---------------------------------------------------------------------------
# helpers
# ---------------------------------------------------------------------------


def _seed_published_app(
    db_engine: Any, name: str = "chat-app", *, allowed_tools: list[str] | None = None, **extra: Any
) -> int:
    """Insert one published AgentApp row and return its id."""
    with DBSession(db_engine) as session:
        app = AgentApp(
            name=name,
            system_prompt="x",
            status="published",
            allowed_tools=allowed_tools or [],
            **extra,
        )
        session.add(app)
        session.commit()
        session.refresh(app)
        return int(app.id)


def _create_session(client: Any, headers: dict[str, str], app_id: int, name: str = "s") -> str:
    """POST /sessions and return the new session id."""
    resp = client.post(
        f"{API}/sessions", json={"agent_app_id": app_id, "name": name}, headers=headers
    )
    assert resp.status_code == 201, resp.text
    return resp.json()["data"]["session_id"]


def _chat_headers(headers: dict[str, str], session_id: str) -> dict[str, str]:
    return {**headers, "X-Session-Id": session_id}


def _drain_l2_hooks() -> None:
    """Wait for fire-and-forget L2 hook tasks (they run on the TestClient portal loop)."""
    deadline = time.monotonic() + 5.0
    while runtime_module._pending_tasks and time.monotonic() < deadline:  # noqa: SLF001 — test drain seam
        time.sleep(0.01)


def _parse_sse(text: str) -> list[dict[str, Any]]:
    """Split one aggregated SSE body into parsed data frames (skip comments)."""
    events: list[dict[str, Any]] = []
    for block in text.split("\n\n"):
        data_lines = [line for line in block.split("\n") if line.startswith("data:")]
        if not data_lines:
            continue
        payload = "\n".join(line[len("data:"):].lstrip(" ") for line in data_lines)
        events.append(json.loads(payload))
    return events


def _messages_payload(content: str) -> dict[str, Any]:
    return {"messages": [{"role": "user", "content": content}]}


# ---------------------------------------------------------------------------
# scenario 1: non-streaming envelope + L2 landing
# ---------------------------------------------------------------------------


def test_non_streaming_chat_envelope_and_l2_landing(
    client: Any,
    user_headers: dict[str, str],
    db_engine: Any,
    scripted_model: ScriptedChatModel,
    memory_checkpointer: MemorySaver,
) -> None:
    """POST /chat returns the envelope ChatResponse; the turn lands in L2 + traces."""
    app_id = _seed_published_app(db_engine, name="plain-app")
    session_id = _create_session(client, user_headers, app_id)
    scripted_model.responses = [AIMessage(content="integrated-reply-1")]

    resp = client.post(
        f"{API}/chat", json=_messages_payload("q1"), headers=_chat_headers(user_headers, session_id)
    )
    assert resp.status_code == 200, resp.text
    body = resp.json()
    assert body["code"] == 200
    assert body["message"] == "success"
    assert body["data"]["interrupt"] is None
    assert [m["content"] for m in body["data"]["messages"]] == ["integrated-reply-1"]

    _drain_l2_hooks()

    history = client.get(f"{API}/messages", headers=_chat_headers(user_headers, session_id))
    assert history.status_code == 200, history.text
    rows = history.json()["data"]["messages"]
    assert [(row["type"], row.get("role"), row.get("content")) for row in rows] == [
        ("message", "user", "q1"),
        ("message", "assistant", "integrated-reply-1"),
    ]
    assert history.json()["data"]["pending_interrupt"] is None

    traces = client.get(f"{API}/chat/traces", headers=_chat_headers(user_headers, session_id))
    assert traces.status_code == 200, traces.text
    trace_rows = traces.json()["data"]
    assert len(trace_rows) == 1
    assert trace_rows[0]["status"] == "success"


# ---------------------------------------------------------------------------
# scenario 2: stream frame order with a real compression round
# ---------------------------------------------------------------------------


def test_stream_frame_order_message_summary_done(
    client: Any,
    user_headers: dict[str, str],
    db_engine: Any,
    scripted_model: ScriptedChatModel,
    memory_checkpointer: MemorySaver,
) -> None:
    """A real compression round emits message -> summary -> done(compressed=true).

    The middleware triggers only when BOTH thresholds hold: total tokens
    exceed ``context_size`` AND more than the ``keep`` (default 20) recent
    messages exist to preserve. The request therefore replays a 22-message
    history (short filler turns + one long reply) in a single turn; the
    scripted model serves the middleware's summary call first, then the
    turn's final reply.
    """
    app_id = _seed_published_app(db_engine, name="compress-app", context_size=16)
    session_id = _create_session(client, user_headers, app_id)
    history = [{"role": "user", "content": f"历史问题{i}，铺垫上下文。"} for i in range(20)]
    history.append({"role": "assistant", "content": "压缩场景长回复。" * 12})
    history.append({"role": "user", "content": "继续"})
    scripted_model.responses = [
        AIMessage(content="middleware-summarized-history"),  # compression call
        AIMessage(content="final-reply"),  # the turn's actual reply
    ]

    resp = client.post(
        f"{API}/chat/stream",
        json={"messages": history},
        headers=_chat_headers(user_headers, session_id),
    )
    assert resp.status_code == 200, resp.text
    frames = _parse_sse(resp.text)

    kinds = [frame["type"] for frame in frames]
    assert kinds[-1] == "done"
    assert frames[-1]["compressed"] is True
    assert frames[-1]["interrupted"] is False
    assert "summary" in kinds, f"expected a summary frame, got {kinds}"
    assert "message" in kinds, f"expected at least one message frame, got {kinds}"
    # frame order: every message/summary frame precedes the trailing done
    assert kinds.index("message") < kinds.index("summary") < len(kinds) - 1
    summary_frame = frames[kinds.index("summary")]
    # deepagents wraps the condensed summary in its handoff template; assert
    # the scripted summary text is embedded rather than an exact match.
    assert "middleware-summarized-history" in summary_frame["summary_text"]


# ---------------------------------------------------------------------------
# scenario 3: HIL interrupt -> decisions resume (reject path, zero network)
# ---------------------------------------------------------------------------


def test_hil_interrupt_then_decisions_resume(
    client: Any,
    user_headers: dict[str, str],
    db_engine: Any,
    scripted_model: ScriptedChatModel,
    memory_checkpointer: MemorySaver,
) -> None:
    """interrupt_on pauses on the tool call; a reject decisions JSON resumes to completion."""
    app_id = _seed_published_app(
        db_engine,
        name="hil-app",
        allowed_tools=[SEARCH_TOOL],
        interrupt_on={SEARCH_TOOL: True},
    )
    session_id = _create_session(client, user_headers, app_id)
    scripted_model.responses = [
        AIMessage(
            content="",
            tool_calls=[{"name": SEARCH_TOOL, "args": {"query": "g4"}, "id": "tc-1", "type": "tool_call"}],
        ),
        AIMessage(content="rejected-final"),
    ]

    first = client.post(
        f"{API}/chat/stream",
        json=_messages_payload("搜一下"),
        headers=_chat_headers(user_headers, session_id),
    )
    assert first.status_code == 200, first.text
    frames = _parse_sse(first.text)
    kinds = [frame["type"] for frame in frames]

    assert "interrupt" in kinds, f"expected an interrupt frame, got {kinds}"
    assert kinds[-1] == "done" and frames[-1]["interrupted"] is True
    interrupt_frame = frames[kinds.index("interrupt")]
    assert interrupt_frame["action_requests"][0]["tool"] == SEARCH_TOOL
    assert scripted_model.n == 1  # paused before the tool ran

    second = client.post(
        f"{API}/chat/stream",
        json=_messages_payload('{"decisions": [{"type": "reject"}]}'),
        headers=_chat_headers(user_headers, session_id),
    )
    assert second.status_code == 200, second.text
    resume_frames = _parse_sse(second.text)
    resume_kinds = [frame["type"] for frame in resume_frames]

    assert resume_kinds[-1] == "done"
    assert resume_frames[-1]["interrupted"] is False
    message_frames = [f for f in resume_frames if f["type"] == "message"]
    assert message_frames, f"expected the final reply frames, got {resume_kinds}"
    assert "".join(f["content"] for f in message_frames) == "rejected-final"
    assert scripted_model.n == 2  # exactly one extra model call for the resume


# ---------------------------------------------------------------------------
# scenario 4: pending restore after an interrupt
# ---------------------------------------------------------------------------


def test_pending_interrupt_restored_by_messages(
    client: Any,
    user_headers: dict[str, str],
    db_engine: Any,
    scripted_model: ScriptedChatModel,
    memory_checkpointer: MemorySaver,
) -> None:
    """A fresh page load after the interrupt rebuilds the approval card (§5.3)."""
    app_id = _seed_published_app(
        db_engine,
        name="pending-app",
        allowed_tools=[SEARCH_TOOL],
        interrupt_on={SEARCH_TOOL: True},
    )
    session_id = _create_session(client, user_headers, app_id)
    scripted_model.responses = [
        AIMessage(
            content="",
            tool_calls=[{"name": SEARCH_TOOL, "args": {"query": "x"}, "id": "tc-9", "type": "tool_call"}],
        ),
    ]

    resp = client.post(
        f"{API}/chat/stream",
        json=_messages_payload("触发审批"),
        headers=_chat_headers(user_headers, session_id),
    )
    assert resp.status_code == 200, resp.text
    _drain_l2_hooks()

    history = client.get(f"{API}/messages", headers=_chat_headers(user_headers, session_id))
    assert history.status_code == 200, history.text
    pending = history.json()["data"]["pending_interrupt"]
    assert pending is not None
    assert pending["action_requests"][0]["tool"] == SEARCH_TOOL


# ---------------------------------------------------------------------------
# scenario 5: rebuild after a checkpoint wipe keeps continuation coherent
# ---------------------------------------------------------------------------


def test_rebuild_restores_history_for_continued_chat(
    client: Any,
    user_headers: dict[str, str],
    db_engine: Any,
    user: Any,
    scripted_model: ScriptedChatModel,
    memory_checkpointer: MemorySaver,
) -> None:
    """Wipe the checkpoint; POST /rebuild re-injects L2 so the next turn sees the history."""
    app_id = _seed_published_app(db_engine, name="rebuild-app")
    session_id = _create_session(client, user_headers, app_id)
    scripted_model.responses = [
        AIMessage(content="r1-text"),
        AIMessage(content="r2-text"),
        AIMessage(content="after-rebuild"),
    ]
    headers = _chat_headers(user_headers, session_id)

    for prompt in ("q1", "q2"):
        resp = client.post(f"{API}/chat", json=_messages_payload(prompt), headers=headers)
        assert resp.status_code == 200, resp.text
    _drain_l2_hooks()

    history = client.get(f"{API}/messages", headers=headers)
    assert len(history.json()["data"]["messages"]) == 4  # 2 turns x (user + assistant)

    # Simulate the disaster: drop every checkpoint of the thread.
    asyncio.run(runtime_module.delete_thread_checkpoint(session_id))

    rebuilt = client.post(f"{API}/rebuild", headers=headers)
    assert rebuilt.status_code == 200, rebuilt.text
    counts = rebuilt.json()["data"]
    assert counts == {"rebuilt_messages": 4, "skipped_tool_calls": 0, "l2_source_lines": 4}

    # Continuation: the scripted model's next call must see the re-injected history.
    third = client.post(f"{API}/chat", json=_messages_payload("q3"), headers=headers)
    assert third.status_code == 200, third.text
    assert [m["content"] for m in third.json()["data"]["messages"]] == ["after-rebuild"]

    last_call = scripted_model.calls[-1]
    flattened = "\n".join(str(m.content) for m in last_call)
    assert "q1" in flattened and "r1-text" in flattened, (
        "the rebuilt checkpoint must carry the prior turns into the next model call"
    )


__all__: list[str] = []
