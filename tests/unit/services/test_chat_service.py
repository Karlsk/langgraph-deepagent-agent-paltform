"""Unit tests for the G4 chat service (spec-g4-chat §10.1).

Covers the five responsibilities: the non-streaming auto-approve loop with
RunTracer persistence and the naming hook (§4.4/§7/§8), the SSE stream
frame sequence with heartbeat (§4.1), the L2 row history projection with
pending-interrupt pull-along (§5.3/§6.1), the disaster rebuild orchestration
(§6.2) and the chat trace query (§7.3). The runtime / naming seams are
faked; trace rows land in in-memory SQLite.
"""

import asyncio
import functools
import json
from collections.abc import AsyncGenerator
from typing import Any

import pytest
from sqlalchemy.pool import StaticPool
from sqlmodel import SQLModel
from sqlmodel import Session as DBSession
from sqlmodel import create_engine

from app.core.config import settings
from app.models.agent_assets import AgentApp
from app.models.session import Session as SessionRow
from app.models.subagent_trace import SubAgentTrace
from app.models.user import User
from app.schemas.chat import Message
from app.services.agents import chat_service, runtime, session_naming
from app.services.agents.runtime import StreamChunk

pytestmark = pytest.mark.unit


def _sync(test):
    """Wrap an async scenario into a sync pytest test.

    Project convention: no async pytest plugin, every async case runs
    through ``asyncio.run``.
    """

    @functools.wraps(test)
    def wrapper(*args: Any, **kwargs: Any) -> None:
        asyncio.run(test(*args, **kwargs))

    return wrapper


# ---------------------------------------------------------------------------
# Fixtures
# ---------------------------------------------------------------------------


@pytest.fixture
def db() -> DBSession:
    """In-memory SQLite session with every table created (StaticPool)."""
    engine = create_engine(
        "sqlite://",
        connect_args={"check_same_thread": False},
        poolclass=StaticPool,
    )
    SQLModel.metadata.create_all(engine)
    session = DBSession(engine)
    yield session
    session.close()


@pytest.fixture
def user(db: DBSession) -> User:
    """Session owner."""
    row = User(
        email="chat-owner@example.com",
        hashed_password=User.hash_password("Passw0rd!Strong"),
        username="chat-owner",
    )
    db.add(row)
    db.commit()
    db.refresh(row)
    return row


@pytest.fixture
def app_row(db: DBSession) -> AgentApp:
    """One published app the chat session binds to (model ref for naming)."""
    row = AgentApp(
        name="g4-app",
        system_prompt="x",
        status="published",
        model="provider/app-model",
    )
    db.add(row)
    db.commit()
    db.refresh(row)
    return row


@pytest.fixture
def session_row(db: DBSession, user: User, app_row: AgentApp) -> SessionRow:
    """One unnamed chat session row."""
    row = SessionRow(id="s-chat", user_id=user.id, username=user.username, agent_app_id=app_row.id, name="")
    db.add(row)
    db.commit()
    db.refresh(row)
    return row


@pytest.fixture
def naming_calls(monkeypatch: pytest.MonkeyPatch) -> list[dict[str, Any]]:
    """Spy capturing every maybe_name_session invocation."""
    calls: list[dict[str, Any]] = []

    async def fake_maybe_name(
        db: DBSession,
        session_id: str,
        session_name: str,
        messages: list[Message],
        *,
        model_name: str | None = None,
    ) -> bool:
        calls.append(
            {
                "session_id": session_id,
                "session_name": session_name,
                "model_name": model_name,
                "messages": list(messages),
            }
        )
        return True

    monkeypatch.setattr(session_naming, "maybe_name_session", fake_maybe_name)
    return calls


def _interrupt_projection() -> dict[str, Any]:
    """The §4.2 projection get_pending_interrupt returns."""
    return {"action_requests": [{"tool": "write_file", "args": {"path": "a.txt"}}]}


class _FakeRuntime:
    """Scripted runtime standing in for the real AgentAppRuntime.

    ``pending_after`` indexes by ainvoke call: entry n-1 is what
    ``get_pending_interrupt`` reports after the n-th ainvoke. ``chunks``
    feeds astream; ``astream_delay`` sleeps before the first chunk so the
    heartbeat path can be exercised deterministically.
    """

    def __init__(
        self,
        *,
        replies: list[list[Message]] | None = None,
        pending_after: list[dict[str, Any] | None] | None = None,
        chunks: list[StreamChunk] | None = None,
        astream_error: Exception | None = None,
        astream_delay: float = 0.0,
        invoke_error: Exception | None = None,
        history: list[Message] | None = None,
    ) -> None:
        self.replies = replies or [[Message(role="assistant", content="ok")]]
        self.pending_after = pending_after or [None]
        self.chunks = chunks or []
        self.astream_error = astream_error
        self.astream_delay = astream_delay
        self.invoke_error = invoke_error
        self.history = history or []
        self.ainvoke_calls: list[list[Message]] = []
        self.astream_calls: list[list[Message]] = []
        self.rebuild_calls: list[tuple[str, list[Any]]] = []

    async def get_chat_history(self, session_id: str) -> list[Message]:
        return list(self.history)

    async def ainvoke(
        self,
        messages: list[Message],
        *,
        session_id: str,
        user_id: Any = None,
        username: Any = None,
        extra_callbacks: Any = None,
    ) -> list[Message]:
        self.ainvoke_calls.append(list(messages))
        if self.invoke_error is not None:
            raise self.invoke_error
        index = min(len(self.ainvoke_calls) - 1, len(self.replies) - 1)
        return list(self.replies[index])

    async def get_pending_interrupt(self, session_id: str) -> dict[str, Any] | None:
        index = min(max(len(self.ainvoke_calls) - 1, 0), len(self.pending_after) - 1)
        return self.pending_after[index]

    async def astream(
        self,
        messages: list[Message],
        *,
        session_id: str,
        user_id: Any = None,
        username: Any = None,
        extra_callbacks: Any = None,
    ) -> AsyncGenerator[StreamChunk, None]:
        self.astream_calls.append(list(messages))
        if self.astream_delay:
            await asyncio.sleep(self.astream_delay)
        for chunk in self.chunks:
            yield chunk
        if self.astream_error is not None:
            raise self.astream_error

    async def rebuild_thread(self, session_id: str, messages: Any) -> None:
        self.rebuild_calls.append((session_id, list(messages)))

    def _model_label(self) -> str:
        return "fake-model"


@pytest.fixture
def deleted_checkpoints(monkeypatch: pytest.MonkeyPatch) -> list[str]:
    """Spy capturing delete_thread_checkpoint invocations."""
    deleted: list[str] = []

    async def fake_delete(session_id: str) -> None:
        deleted.append(session_id)

    monkeypatch.setattr(runtime, "delete_thread_checkpoint", fake_delete)
    return deleted


def _install_runtime(monkeypatch: pytest.MonkeyPatch, fake: _FakeRuntime) -> _FakeRuntime:
    async def fake_get_runtime(session: DBSession, app_id: int, *, user_id: int) -> Any:
        return fake

    monkeypatch.setattr(runtime, "get_runtime", fake_get_runtime)
    return fake


def _chat_traces(db: DBSession) -> list[SubAgentTrace]:
    from sqlmodel import select

    return list(db.exec(select(SubAgentTrace)).all())


def _frames_payloads(frames: list[str]) -> list[dict[str, Any]]:
    """Parse every data frame's JSON payload in order."""
    payloads = []
    for frame in frames:
        assert frame.startswith("data: ") and frame.endswith("\n\n"), frame
        payloads.append(json.loads(frame[len("data: ") : -2]))
    return payloads


def _collect(frames: list[str]) -> list[str]:
    """Filter out heartbeat comment frames."""
    return [frame for frame in frames if not frame.startswith(":")]


# ---------------------------------------------------------------------------
# D1: chat() non-streaming auto-approve (§4.4/§7.2/§8)
# ---------------------------------------------------------------------------


@_sync
async def test_chat_single_turn_success(
    db: DBSession, session_row: SessionRow, naming_calls: list[dict[str, Any]], monkeypatch: pytest.MonkeyPatch
) -> None:
    """Plain turn: assistant-only reply projection, trace row landed, naming hook fired.

    The real runtime returns the FULL turn messages (user echo + assistant
    segments); ``ChatResponse.messages`` carries assistant replies only (§4.5).
    """
    _install_runtime(
        monkeypatch,
        _FakeRuntime(
            replies=[
                [
                    Message(role="user", content="hi"),
                    Message(role="assistant", content="你好"),
                ]
            ]
        ),
    )

    result = await chat_service.chat(db, session_row, [Message(role="user", content="hi")], user_id="7", username="u7")

    assert [m.content for m in result.messages] == ["你好"]
    assert result.interrupt is None
    traces = _chat_traces(db)
    assert len(traces) == 1
    trace = traces[0]
    assert trace.source == "chat"
    assert trace.session_id == session_row.id
    assert trace.name == "g4-app"
    assert trace.status == "success"
    assert trace.created_by == "u7"
    assert naming_calls == [
        {
            "session_id": session_row.id,
            "session_name": "",
            "model_name": "provider/app-model",
            "messages": [Message(role="user", content="hi")],
        }
    ]


@_sync
async def test_chat_drops_prior_turn_history_in_reply(
    db: DBSession, session_row: SessionRow, naming_calls: list[dict[str, Any]], monkeypatch: pytest.MonkeyPatch
) -> None:
    """Multi-turn replies keep only this turn's new assistant messages.

    ainvoke carries the FULL thread projection (prior turns included);
    ``ChatResponse.messages`` keeps only THIS turn's new assistant replies
    (§4.5) so the frontend never double-renders history.
    """
    prior = [
        Message(role="user", content="q1"),
        Message(role="assistant", content="r1"),
    ]
    _install_runtime(
        monkeypatch,
        _FakeRuntime(
            history=prior,
            replies=[[*prior, Message(role="user", content="q2"), Message(role="assistant", content="r2")]],
        ),
    )

    result = await chat_service.chat(
        db, session_row, [Message(role="user", content="q2")], user_id="7", username="u7"
    )

    assert [m.content for m in result.messages] == ["r2"]


@_sync
async def test_chat_auto_approve_resumes_until_complete(
    db: DBSession, session_row: SessionRow, naming_calls: list[dict[str, Any]], monkeypatch: pytest.MonkeyPatch
) -> None:
    """Interrupt → approve decisions resume → second segment completes."""
    fake = _install_runtime(
        monkeypatch,
        _FakeRuntime(
            replies=[
                [Message(role="assistant", content="需要写文件")],
                [
                    Message(role="user", content='{"decisions": ...}'),
                    Message(role="assistant", content="文件已写入"),
                ],
            ],
            pending_after=[_interrupt_projection(), None],
        ),
    )

    result = await chat_service.chat(
        db, session_row, [Message(role="user", content="写个文件")], user_id="7", username="u7"
    )

    assert [m.content for m in result.messages] == ["文件已写入"]
    assert result.interrupt is None
    assert len(fake.ainvoke_calls) == 2
    resume = fake.ainvoke_calls[1][0]
    assert json.loads(resume.content) == {"decisions": [{"type": "approve"}]}


@_sync
async def test_chat_auto_approve_limit_exceeded(
    db: DBSession, session_row: SessionRow, naming_calls: list[dict[str, Any]], monkeypatch: pytest.MonkeyPatch
) -> None:
    """Round limit hit: interrupt projection returned, thread stays paused."""
    monkeypatch.setattr(settings, "CHAT_AUTO_APPROVE_MAX_ROUNDS", 2)
    fake = _install_runtime(
        monkeypatch,
        _FakeRuntime(
            replies=[[Message(role="assistant", content=f"第{i}段")] for i in range(4)],
            pending_after=[_interrupt_projection()] * 4,
        ),
    )

    result = await chat_service.chat(
        db, session_row, [Message(role="user", content="写个文件")], user_id="7", username="u7"
    )

    assert len(fake.ainvoke_calls) == 3  # initial + 2 auto-approve resumes
    assert result.messages == []
    assert result.interrupt is not None
    assert result.interrupt.action_requests[0].tool == "write_file"


@_sync
async def test_chat_invoke_error_persists_trace_and_reraises(
    db: DBSession, session_row: SessionRow, naming_calls: list[dict[str, Any]], monkeypatch: pytest.MonkeyPatch
) -> None:
    """Failed rounds land an error trace row, then the exception escapes (§7.2)."""
    _install_runtime(monkeypatch, _FakeRuntime(invoke_error=RuntimeError("graph blew up"), replies=[]))

    with pytest.raises(RuntimeError, match="graph blew up"):
        await chat_service.chat(db, session_row, [Message(role="user", content="hi")], user_id="7", username="u7")

    traces = _chat_traces(db)
    assert len(traces) == 1
    assert traces[0].status == "error"
    assert "graph blew up" in (traces[0].error or "")


@_sync
async def test_chat_trace_disabled_writes_nothing(
    db: DBSession, session_row: SessionRow, naming_calls: list[dict[str, Any]], monkeypatch: pytest.MonkeyPatch
) -> None:
    """CHAT_TRACE_ENABLED=false: no trace rows (§7.2)."""
    monkeypatch.setattr(settings, "CHAT_TRACE_ENABLED", False)
    _install_runtime(monkeypatch, _FakeRuntime())

    await chat_service.chat(db, session_row, [Message(role="user", content="hi")], user_id="7", username="u7")

    assert _chat_traces(db) == []


# ---------------------------------------------------------------------------
# D2: chat_stream() SSE generator (§4.1)
# ---------------------------------------------------------------------------


@_sync
async def test_chat_stream_frame_sequence(
    db: DBSession, session_row: SessionRow, naming_calls: list[dict[str, Any]], monkeypatch: pytest.MonkeyPatch
) -> None:
    """message/tool_call chunks map to typed frames; done closes the stream."""
    _install_runtime(
        monkeypatch,
        _FakeRuntime(
            chunks=[
                StreamChunk(content="部分一", source="coordinator"),
                StreamChunk(content="echo output", source="writer", type="tool_call", name="echo"),
                StreamChunk(content="部分二", source="coordinator"),
            ]
        ),
    )

    frames = _collect(
        [
            frame
            async for frame in chat_service.chat_stream(
                db, session_row, [Message(role="user", content="hi")], user_id="7", username="u7"
            )
        ]
    )

    payloads = _frames_payloads(frames)
    assert [p["type"] for p in payloads] == ["message", "tool_call", "message", "done"]
    assert payloads[0] == {"type": "message", "content": "部分一", "source": "coordinator"}
    assert payloads[1] == {"type": "tool_call", "name": "echo", "content": "echo output", "source": "writer"}
    assert payloads[3]["message_count"] == 2
    assert payloads[3]["compressed"] is False
    assert payloads[3]["interrupted"] is False


@_sync
async def test_chat_stream_interrupt_then_done(
    db: DBSession, session_row: SessionRow, naming_calls: list[dict[str, Any]], monkeypatch: pytest.MonkeyPatch
) -> None:
    """Interrupt frame carries structured action_requests; done marks interrupted (§5.1)."""
    projection = _interrupt_projection()
    _install_runtime(
        monkeypatch,
        _FakeRuntime(
            chunks=[
                StreamChunk(content="正文", source="coordinator"),
                StreamChunk(content=json.dumps(projection), source="system", type="interrupt"),
            ]
        ),
    )

    frames = _collect(
        [
            frame
            async for frame in chat_service.chat_stream(
                db, session_row, [Message(role="user", content="hi")], user_id="7", username="u7"
            )
        ]
    )

    payloads = _frames_payloads(frames)
    assert [p["type"] for p in payloads] == ["message", "interrupt", "done"]
    assert payloads[1]["action_requests"] == [{"tool": "write_file", "args": {"path": "a.txt"}}]
    assert payloads[2]["interrupted"] is True


@_sync
async def test_chat_stream_summary_sets_compressed(
    db: DBSession, session_row: SessionRow, naming_calls: list[dict[str, Any]], monkeypatch: pytest.MonkeyPatch
) -> None:
    """A summary chunk surfaces as a summary frame and flags done.compressed."""
    _install_runtime(
        monkeypatch,
        _FakeRuntime(
            chunks=[
                StreamChunk(content="正文", source="coordinator"),
                StreamChunk(content="已压缩的摘要", source="system", type="summary"),
            ]
        ),
    )

    frames = _collect(
        [
            frame
            async for frame in chat_service.chat_stream(
                db, session_row, [Message(role="user", content="hi")], user_id="7", username="u7"
            )
        ]
    )

    payloads = _frames_payloads(frames)
    assert [p["type"] for p in payloads] == ["message", "summary", "done"]
    assert payloads[1] == {"type": "summary", "summary_text": "已压缩的摘要"}
    assert payloads[2]["compressed"] is True


@_sync
async def test_chat_stream_error_frame_then_done(
    db: DBSession, session_row: SessionRow, naming_calls: list[dict[str, Any]], monkeypatch: pytest.MonkeyPatch
) -> None:
    """Stream failures emit an error frame and STILL emit done (§4.1)."""
    _install_runtime(
        monkeypatch,
        _FakeRuntime(
            chunks=[StreamChunk(content="部分", source="coordinator")],
            astream_error=RuntimeError("stream blew up"),
        ),
    )

    frames = _collect(
        [
            frame
            async for frame in chat_service.chat_stream(
                db, session_row, [Message(role="user", content="hi")], user_id="7", username="u7"
            )
        ]
    )

    payloads = _frames_payloads(frames)
    assert [p["type"] for p in payloads] == ["message", "error", "done"]
    assert payloads[1] == {"type": "error", "message": "stream blew up"}
    traces = _chat_traces(db)
    assert len(traces) == 1
    assert traces[0].status == "error"


@_sync
async def test_chat_stream_heartbeat_comment_frames(
    db: DBSession, session_row: SessionRow, naming_calls: list[dict[str, Any]], monkeypatch: pytest.MonkeyPatch
) -> None:
    """Idle gaps longer than the heartbeat interval emit ``: ping`` comments."""
    monkeypatch.setattr(chat_service, "_HEARTBEAT_SECONDS", 0.05)
    _install_runtime(
        monkeypatch,
        _FakeRuntime(
            chunks=[StreamChunk(content="慢回复", source="coordinator")],
            astream_delay=0.15,
        ),
    )

    frames = [
        frame
        async for frame in chat_service.chat_stream(
            db, session_row, [Message(role="user", content="hi")], user_id="7", username="u7"
        )
    ]

    assert frames[0] == ": ping\n\n"
    payload_frames = _frames_payloads(_collect(frames))
    assert [p["type"] for p in payload_frames] == ["message", "done"]


@_sync
async def test_chat_stream_naming_hook_and_trace(
    db: DBSession, session_row: SessionRow, naming_calls: list[dict[str, Any]], monkeypatch: pytest.MonkeyPatch
) -> None:
    """The stream path fires the naming hook and lands a success trace row."""
    _install_runtime(
        monkeypatch,
        _FakeRuntime(chunks=[StreamChunk(content="好的", source="coordinator")]),
    )

    frames = _collect(
        [
            frame
            async for frame in chat_service.chat_stream(
                db, session_row, [Message(role="user", content="hi")], user_id="7", username="u7"
            )
        ]
    )
    assert frames  # sanity

    assert naming_calls and naming_calls[0]["model_name"] == "provider/app-model"
    traces = _chat_traces(db)
    assert len(traces) == 1
    assert traces[0].status == "success"
    assert traces[0].source == "chat"


# ---------------------------------------------------------------------------
# D3: get_history() L2 row projection (§5.3/§6.1)
# ---------------------------------------------------------------------------


@pytest.fixture
def l2_rows(monkeypatch: pytest.MonkeyPatch) -> list[dict[str, Any]]:
    """Canned L2 rows returned by read_or_rebuild_l2 (verifiable content)."""
    rows = [
        {"seq": 1, "ts": "2026-01-01T00:00:00+00:00", "type": "message", "role": "user", "content": "你好"},
        {
            "seq": 2,
            "ts": "2026-01-01T00:00:01+00:00",
            "type": "tool_call",
            "name": "echo",
            "summary": "echo done",
        },
        {"seq": 3, "ts": "2026-01-01T00:00:02+00:00", "type": "summary", "content": "压缩摘要"},
        {"seq": 4, "ts": "2026-01-01T00:00:03+00:00", "type": "message", "role": "assistant", "content": "完成"},
    ]

    async def fake_read(target: SessionRow) -> list[dict[str, Any]]:
        return rows

    from app.services.agents import sessions_service

    monkeypatch.setattr(sessions_service, "read_or_rebuild_l2", fake_read)
    return rows


@_sync
async def test_get_history_projects_rows_and_pending(
    db: DBSession, session_row: SessionRow, l2_rows: list[dict[str, Any]], monkeypatch: pytest.MonkeyPatch
) -> None:
    """History = L2 row projection + pending interrupt pull-along (§5.3)."""
    _install_runtime(monkeypatch, _FakeRuntime(pending_after=[_interrupt_projection()]))

    result = await chat_service.get_history(db, session_row, user_id=7)

    assert [item.type for item in result.messages] == ["message", "tool_call", "summary", "message"]
    assert result.messages[0].model_dump(exclude_none=True) == {
        "type": "message",
        "seq": 1,
        "ts": "2026-01-01T00:00:00+00:00",
        "role": "user",
        "content": "你好",
    }
    assert result.messages[1].name == "echo"
    assert result.messages[1].summary == "echo done"
    assert result.messages[2].content == "压缩摘要"
    assert result.pending_interrupt is not None
    assert result.pending_interrupt.action_requests[0].tool == "write_file"


@_sync
async def test_get_history_without_pending(
    db: DBSession, session_row: SessionRow, l2_rows: list[dict[str, Any]], monkeypatch: pytest.MonkeyPatch
) -> None:
    """No pending interrupt → pending_interrupt stays None."""
    _install_runtime(monkeypatch, _FakeRuntime(pending_after=[None]))

    result = await chat_service.get_history(db, session_row, user_id=7)

    assert result.pending_interrupt is None
    assert len(result.messages) == 4


@_sync
async def test_get_history_projects_subagent_source(
    db: DBSession, session_row: SessionRow, monkeypatch: pytest.MonkeyPatch
) -> None:
    """Display-only subagent rows surface their ``source`` for card rendering."""
    from app.services.agents import sessions_service

    rows = [
        {"seq": 1, "ts": "2026-01-01T00:00:00+00:00", "type": "message", "role": "user", "content": "hi"},
        {
            "seq": 2,
            "ts": "2026-01-01T00:00:01+00:00",
            "type": "message",
            "role": "assistant",
            "content": "研究中…",
            "source": "researcher",
        },
        {"seq": 3, "ts": "2026-01-01T00:00:02+00:00", "type": "message", "role": "assistant", "content": "完成"},
    ]

    async def fake_read(target: SessionRow) -> list[dict[str, Any]]:
        return rows

    monkeypatch.setattr(sessions_service, "read_or_rebuild_l2", fake_read)
    _install_runtime(monkeypatch, _FakeRuntime(pending_after=[None]))

    result = await chat_service.get_history(db, session_row, user_id=7)

    assert [item.source for item in result.messages] == [None, "researcher", None]


# ---------------------------------------------------------------------------
# D4: rebuild() disaster recovery (§6.2)
# ---------------------------------------------------------------------------


@_sync
async def test_rebuild_rejects_empty_history(
    db: DBSession, session_row: SessionRow, monkeypatch: pytest.MonkeyPatch
) -> None:
    """L2 has no readable rows → NothingToRebuildError (API maps 422)."""
    from app.services.agents import sessions_service

    async def fake_read(target: SessionRow) -> list[dict[str, Any]]:
        return []

    monkeypatch.setattr(sessions_service, "read_or_rebuild_l2", fake_read)

    with pytest.raises(chat_service.NothingToRebuildError):
        await chat_service.rebuild(db, session_row, user_id=7)


@_sync
async def test_rebuild_rejects_pending_interrupt(
    db: DBSession, session_row: SessionRow, l2_rows: list[dict[str, Any]], monkeypatch: pytest.MonkeyPatch
) -> None:
    """Thread paused on an interrupt → InterruptPendingError (API maps 409)."""
    _install_runtime(monkeypatch, _FakeRuntime(pending_after=[_interrupt_projection()]))

    with pytest.raises(chat_service.InterruptPendingError):
        await chat_service.rebuild(db, session_row, user_id=7)


@_sync
async def test_rebuild_rehydrates_checkpoint(
    db: DBSession,
    session_row: SessionRow,
    l2_rows: list[dict[str, Any]],
    deleted_checkpoints: list[str],
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    """Happy path: delete old checkpoint, re-inject message+summary rows, skip tool_calls."""
    from langchain_core.messages import AIMessage, HumanMessage

    fake = _install_runtime(monkeypatch, _FakeRuntime())

    result = await chat_service.rebuild(db, session_row, user_id=7)

    assert deleted_checkpoints == [session_row.id]
    assert len(fake.rebuild_calls) == 1
    rebuilt_session_id, rebuilt_messages = fake.rebuild_calls[0]
    assert rebuilt_session_id == session_row.id
    assert [type(m) for m in rebuilt_messages] == [HumanMessage, HumanMessage, AIMessage]
    assert rebuilt_messages[0].content == "你好"
    assert rebuilt_messages[1].content == "压缩摘要"  # summary row → HumanMessage
    assert rebuilt_messages[2].content == "完成"
    assert result.rebuilt_messages == 3
    assert result.skipped_tool_calls == 1
    assert result.skipped_subagent_messages == 0
    assert result.l2_source_lines == 4


@_sync
async def test_rebuild_skips_display_only_subagent_rows(
    db: DBSession,
    session_row: SessionRow,
    deleted_checkpoints: list[str],
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    """Rows carrying ``source`` are never re-injected (checkpoint stays clean)."""
    from langchain_core.messages import AIMessage, HumanMessage

    from app.services.agents import sessions_service

    rows = [
        {"seq": 1, "ts": "2026-01-01T00:00:00+00:00", "type": "message", "role": "user", "content": "hi"},
        {
            "seq": 2,
            "ts": "2026-01-01T00:00:01+00:00",
            "type": "message",
            "role": "assistant",
            "content": "研究中…",
            "source": "researcher",
        },
        {"seq": 3, "ts": "2026-01-01T00:00:02+00:00", "type": "message", "role": "assistant", "content": "完成"},
    ]

    async def fake_read(target: SessionRow) -> list[dict[str, Any]]:
        return rows

    monkeypatch.setattr(sessions_service, "read_or_rebuild_l2", fake_read)
    fake = _install_runtime(monkeypatch, _FakeRuntime())

    result = await chat_service.rebuild(db, session_row, user_id=7)

    _, rebuilt_messages = fake.rebuild_calls[0]
    assert [type(m) for m in rebuilt_messages] == [HumanMessage, AIMessage]
    assert [m.content for m in rebuilt_messages] == ["hi", "完成"]
    assert result.rebuilt_messages == 2
    assert result.skipped_subagent_messages == 1
    assert result.l2_source_lines == 3


# ---------------------------------------------------------------------------
# D5: get_traces() chat trace query (§7.3)
# ---------------------------------------------------------------------------


@_sync
async def test_get_traces_filters_chat_rows_desc(db: DBSession, session_row: SessionRow, app_row: AgentApp) -> None:
    """Only chat rows of this session return, newest first, limit honoured."""
    from datetime import UTC, datetime, timedelta

    base_time = datetime.now(UTC)
    db.add(
        SubAgentTrace(
            name=app_row.name,
            status="success",
            prompt="p1",
            model="m",
            turns=1,
            duration_seconds=0.1,
            final_message="r1",
            events=[{"seq": 1, "agent": "coordinator"}],
            source="chat",
            session_id=session_row.id,
            created_at=base_time - timedelta(seconds=10),
        )
    )
    db.add(
        SubAgentTrace(
            name=app_row.name,
            status="error",
            prompt="p2",
            model="m",
            turns=2,
            duration_seconds=0.2,
            final_message="",
            events=[],
            error="boom",
            source="chat",
            session_id=session_row.id,
            created_at=base_time,
        )
    )
    db.add(
        SubAgentTrace(
            name=app_row.name,
            status="success",
            prompt="p3",
            model="m",
            turns=1,
            duration_seconds=0.1,
            final_message="r3",
            events=[],
            source="test",  # test row: must stay invisible
            session_id=session_row.id,
            created_at=base_time - timedelta(seconds=5),
        )
    )
    db.commit()

    result = await chat_service.get_traces(db, session_row, limit=100)

    assert len(result) == 2
    assert [item.status for item in result] == ["error", "success"]  # created_at desc
    assert result[0].error == "boom"
    assert result[1].events == [{"seq": 1, "agent": "coordinator"}]
    assert result[1].id > 0
