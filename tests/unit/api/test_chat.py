"""Unit tests for the G4 chat API routes (spec-g4-chat §3/§10.3).

Five endpoints behind the mandatory ``X-Session-Id`` header. The service
layer is mocked — these tests pin the routing responsibilities only:
auth + ownership 404 anti-enumeration, header validation, the ApiResponse
envelope (with the auto-approve-limit reason string), SSE response headers
and the 422/409 rebuild boundary mapping.
"""

import json
from collections.abc import Generator
from typing import Any

import pytest
from fastapi import FastAPI
from fastapi.exceptions import RequestValidationError
from fastapi.testclient import TestClient
from sqlalchemy.pool import StaticPool
from slowapi.errors import RateLimitExceeded  # type: ignore[import-untyped]
from sqlmodel import SQLModel
from sqlmodel import Session as DBSession
from sqlmodel import create_engine
from starlette.exceptions import HTTPException as StarletteHTTPException

from app.api.v1 import agent_assets_common as common_module
from app.api.v1 import auth as auth_module
from app.api.v1 import chat as chat_module
from app.api.error_handlers import (
    http_exception_handler,
    rate_limit_exceeded_handler,
    validation_exception_handler,
)
from app.core.limiter import limiter
from app.models.agent_assets import AgentApp
from app.models.session import Session as SessionRow
from app.models.user import User
from app.schemas.chat import (
    ActionRequest,
    ChatResponse,
    ChatTraceItem,
    HistoryItem,
    InterruptPayload,
    Message,
    MessagesResponse,
    RebuildResult,
)
from app.services.agents import chat_service

pytestmark = pytest.mark.unit

OTHER_USER_ID = 999  # foreign owner proving the 404 anti-enumeration


@pytest.fixture(autouse=True)
def reset_rate_limiter() -> Generator[None, None, None]:
    """Isolate slowapi counters between tests (shared in-memory storage)."""
    limiter.reset()
    yield


@pytest.fixture
def db_session() -> Generator[DBSession, None, None]:
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
def db_user(db_session: DBSession) -> User:
    """A real User row standing in for get_current_user."""
    row = User(
        email="chat-api-user@example.com",
        hashed_password=User.hash_password("Passw0rd!Strong"),
        username="chat-api-user",
    )
    db_session.add(row)
    db_session.commit()
    db_session.refresh(row)
    return row


@pytest.fixture
def app_row(db_session: DBSession, db_user: User) -> AgentApp:
    """A published app the chat session binds to."""
    row = AgentApp(name="g4-api-app", system_prompt="x", status="published")
    db_session.add(row)
    db_session.commit()
    db_session.refresh(row)
    return row


@pytest.fixture
def session_row(db_session: DBSession, db_user: User, app_row: AgentApp) -> SessionRow:
    """One chat session owned by db_user."""
    row = SessionRow(id="s-api", user_id=db_user.id, username=db_user.username, agent_app_id=app_row.id, name="")
    db_session.add(row)
    db_session.commit()
    db_session.refresh(row)
    return row


@pytest.fixture
def client(db_session: DBSession, db_user: User) -> Generator[TestClient, None, None]:
    """Minimal app wiring the chat router with limiter + dependency overrides."""
    app = FastAPI()
    app.state.limiter = limiter
    app.add_exception_handler(RequestValidationError, validation_exception_handler)
    app.add_exception_handler(StarletteHTTPException, http_exception_handler)  # pyright: ignore[reportArgumentType]
    app.add_exception_handler(RateLimitExceeded, rate_limit_exceeded_handler)  # pyright: ignore[reportArgumentType]
    app.include_router(chat_module.router)
    app.dependency_overrides[auth_module.get_current_user] = lambda: db_user
    app.dependency_overrides[common_module.get_db_session] = lambda: db_session
    with TestClient(app) as test_client:
        yield test_client


def _seed_foreign_session(db: DBSession, agent_app_id: int) -> SessionRow:
    """Insert a session owned by another user (404 anti-enumeration case)."""
    row = SessionRow(id="s-foreign", user_id=OTHER_USER_ID, username="other", agent_app_id=agent_app_id, name="x")
    db.add(row)
    db.commit()
    db.refresh(row)
    return row


def _install_chat_service(monkeypatch: pytest.MonkeyPatch, name: str, outcome: Any) -> dict[str, Any]:
    """Replace one chat_service function with an async fake returning outcome."""
    calls: list[dict[str, Any]] = []

    async def fake(*args: Any, **kwargs: Any) -> Any:
        calls.append({"args": args, "kwargs": kwargs})
        if isinstance(outcome, Exception):
            raise outcome
        return outcome

    monkeypatch.setattr(chat_service, name, fake)
    return {"calls": calls}


# ---------------------------------------------------------------------------
# POST /chat — non-streaming envelope (§3.2/§4.5)
# ---------------------------------------------------------------------------


def test_post_chat_success_envelope(
    client: TestClient, session_row: SessionRow, monkeypatch: pytest.MonkeyPatch
) -> None:
    """Happy turn: ApiResponse envelope carries the ChatResponse payload."""
    spy = _install_chat_service(
        monkeypatch,
        "chat",
        ChatResponse(messages=[Message(role="assistant", content="hi")]),
    )

    response = client.post(
        "/chat",
        json={"messages": [{"role": "user", "content": "hello"}]},
        headers={"X-Session-Id": session_row.id},
    )

    assert response.status_code == 200
    body = response.json()
    assert body["code"] == 200
    assert body["data"]["messages"] == [{"role": "assistant", "content": "hi"}]
    call = spy["calls"][0]
    assert call["kwargs"]["user_id"] == 1
    assert call["kwargs"]["username"] == "chat-api-user"


def test_post_chat_auto_approve_limit_message(
    client: TestClient, session_row: SessionRow, monkeypatch: pytest.MonkeyPatch
) -> None:
    """Limit-exceeded responses carry the programmatic reason in the envelope."""
    _install_chat_service(
        monkeypatch,
        "chat",
        ChatResponse(
            messages=[],
            interrupt=InterruptPayload(action_requests=[ActionRequest(tool="write_file", args={})]),
        ),
    )

    response = client.post(
        "/chat",
        json={"messages": [{"role": "user", "content": "hello"}]},
        headers={"X-Session-Id": session_row.id},
    )

    assert response.status_code == 200
    assert response.json()["message"] == "auto_approve_limit_exceeded"
    assert response.json()["data"]["interrupt"]["action_requests"][0]["tool"] == "write_file"


def test_post_chat_missing_session_header_422(client: TestClient) -> None:
    """Missing X-Session-Id fails request validation with 422 (§3.1)."""
    response = client.post("/chat", json={"messages": [{"role": "user", "content": "hi"}]})

    assert response.status_code == 422


def test_post_chat_unknown_session_404(client: TestClient, monkeypatch: pytest.MonkeyPatch) -> None:
    """Unknown session ids 404 — no service call, no enumeration signal."""

    async def fail_chat(*args: Any, **kwargs: Any) -> Any:
        raise AssertionError("chat() must not be reached for unknown sessions")

    monkeypatch.setattr(chat_service, "chat", fail_chat)

    response = client.post(
        "/chat",
        json={"messages": [{"role": "user", "content": "hi"}]},
        headers={"X-Session-Id": "s-nonexistent"},
    )

    assert response.status_code == 404


def test_post_chat_foreign_session_404(
    client: TestClient, app_row: AgentApp, db_session: DBSession, monkeypatch: pytest.MonkeyPatch
) -> None:
    """Foreign sessions 404 identically (anti-enumeration, §3.1)."""
    foreign = _seed_foreign_session(db_session, app_row.id)
    _install_chat_service(monkeypatch, "chat", ChatResponse(messages=[]))

    response = client.post(
        "/chat",
        json={"messages": [{"role": "user", "content": "hi"}]},
        headers={"X-Session-Id": foreign.id},
    )

    assert response.status_code == 404


# ---------------------------------------------------------------------------
# POST /chat/stream — SSE response (§3.2/§10.3)
# ---------------------------------------------------------------------------


def test_post_chat_stream_sse_headers_and_frames(
    client: TestClient, session_row: SessionRow, monkeypatch: pytest.MonkeyPatch
) -> None:
    """StreamingResponse: event-stream media type + anti-proxy headers + frames."""

    async def fake_stream(*args: Any, **kwargs: Any) -> Any:
        yield 'data: {"type": "message", "content": "hi"}\n\n'
        yield 'data: {"type": "done", "message_count": 1}\n\n'

    monkeypatch.setattr(chat_service, "chat_stream", fake_stream)

    with client.stream(
        "POST",
        "/chat/stream",
        json={"messages": [{"role": "user", "content": "hello"}]},
        headers={"X-Session-Id": session_row.id},
    ) as response:
        assert response.status_code == 200
        assert response.headers["content-type"].startswith("text/event-stream")
        assert response.headers["cache-control"] == "no-cache"
        assert response.headers["x-accel-buffering"] == "no"
        frames = [line for line in response.iter_lines() if line.startswith("data:")]

    assert len(frames) == 2
    assert json.loads(frames[0][len("data: ") :])["content"] == "hi"


def test_post_chat_stream_missing_header_422(client: TestClient) -> None:
    """SSE endpoint enforces the same mandatory header."""
    response = client.post("/chat/stream", json={"messages": [{"role": "user", "content": "hi"}]})

    assert response.status_code == 422


# ---------------------------------------------------------------------------
# GET /messages — history envelope (§3.2/§6.1)
# ---------------------------------------------------------------------------


def test_get_messages_envelope(client: TestClient, session_row: SessionRow, monkeypatch: pytest.MonkeyPatch) -> None:
    """History endpoint wraps the L2 projection in the envelope."""
    _install_chat_service(
        monkeypatch,
        "get_history",
        MessagesResponse(messages=[HistoryItem(type="message", seq=1, ts="t", role="user", content="hi")]),
    )

    response = client.get("/messages", headers={"X-Session-Id": session_row.id})

    assert response.status_code == 200
    body = response.json()
    assert body["code"] == 200
    assert body["data"]["messages"][0]["content"] == "hi"
    assert body["data"]["pending_interrupt"] is None


def test_get_messages_foreign_404(client: TestClient, app_row: AgentApp, db_session: DBSession) -> None:
    """Foreign or unknown sessions 404 before any service call."""
    foreign = _seed_foreign_session(db_session, app_row.id)

    response = client.get("/messages", headers={"X-Session-Id": foreign.id})

    assert response.status_code == 404


# ---------------------------------------------------------------------------
# POST /rebuild — boundary mapping (§6.2/§10.3)
# ---------------------------------------------------------------------------


def test_post_rebuild_success_envelope(
    client: TestClient, session_row: SessionRow, monkeypatch: pytest.MonkeyPatch
) -> None:
    """Rebuild outcome lands in the envelope."""
    _install_chat_service(
        monkeypatch,
        "rebuild",
        RebuildResult(rebuilt_messages=3, skipped_tool_calls=1, skipped_subagent_messages=2, l2_source_lines=6),
    )

    response = client.post("/rebuild", headers={"X-Session-Id": session_row.id})

    assert response.status_code == 200
    assert response.json()["data"] == {
        "rebuilt_messages": 3,
        "skipped_tool_calls": 1,
        "skipped_subagent_messages": 2,
        "l2_source_lines": 6,
    }


def test_post_rebuild_empty_history_422(
    client: TestClient, session_row: SessionRow, monkeypatch: pytest.MonkeyPatch
) -> None:
    """NothingToRebuildError maps to 422 (§6.2)."""
    _install_chat_service(monkeypatch, "rebuild", chat_service.NothingToRebuildError("no rows"))

    response = client.post("/rebuild", headers={"X-Session-Id": session_row.id})

    assert response.status_code == 422


def test_post_rebuild_pending_interrupt_409(
    client: TestClient, session_row: SessionRow, monkeypatch: pytest.MonkeyPatch
) -> None:
    """InterruptPendingError maps to 409 (§6.2)."""
    _install_chat_service(monkeypatch, "rebuild", chat_service.InterruptPendingError("pending"))

    response = client.post("/rebuild", headers={"X-Session-Id": session_row.id})

    assert response.status_code == 409


# ---------------------------------------------------------------------------
# GET /chat/traces — trace listing (§7.3)
# ---------------------------------------------------------------------------


def test_get_chat_traces_envelope(
    client: TestClient, session_row: SessionRow, monkeypatch: pytest.MonkeyPatch
) -> None:
    """Trace rows project through the envelope, newest first."""
    _install_chat_service(
        monkeypatch,
        "get_traces",
        [
            ChatTraceItem(
                id=1,
                status="success",
                turns=2,
                duration_seconds=0.5,
                created_at="2026-01-01T00:00:00+00:00",
                events=[{"seq": 1, "agent": "coordinator"}],
            )
        ],
    )

    response = client.get("/chat/traces", headers={"X-Session-Id": session_row.id})

    assert response.status_code == 200
    body = response.json()
    assert body["code"] == 200
    assert body["data"][0]["events"][0]["agent"] == "coordinator"


def test_get_chat_traces_unknown_session_404(client: TestClient) -> None:
    """Trace listing enforces ownership too."""
    response = client.get("/chat/traces", headers={"X-Session-Id": "s-nonexistent"})

    assert response.status_code == 404
