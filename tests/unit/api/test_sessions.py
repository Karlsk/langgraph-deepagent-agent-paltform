"""Unit tests for the G3 session CRUD + export API (spec-g3-session §9.1/§9.2).

Zero real network / zero real LLM: in-memory SQLite via dependency
overrides, the auth dependency overridden with a seeded User row, and the
runtime seams (checkpointer helper / get_runtime) monkeypatched. Export
asserts the non-envelope file-download contract (§11.5.3).
"""

import json
from collections.abc import Generator
from datetime import UTC, datetime
from pathlib import Path
from typing import Any

import pytest
from fastapi import FastAPI, HTTPException
from fastapi.exceptions import RequestValidationError
from fastapi.testclient import TestClient
from slowapi.errors import RateLimitExceeded
from sqlalchemy.pool import StaticPool
from sqlmodel import SQLModel, create_engine, select
from sqlmodel import Session as DBSession
from starlette.exceptions import HTTPException as StarletteHTTPException

from app.api.error_handlers import (
    http_exception_handler,
    rate_limit_exceeded_handler,
    validation_exception_handler,
)
from app.api.v1 import agent_assets_common as common_module
from app.api.v1 import auth as auth_module
from app.api.v1 import sessions as sessions_module
from app.core.config import settings
from app.core.limiter import limiter
from app.models.agent_assets import AgentApp, UserAgentAppAssociation
from app.models.session import Session as SessionRow
from app.models.user import User
from app.schemas.chat import Message
from app.services.agents import context_store, runtime, sessions_service

pytestmark = pytest.mark.unit

OTHER_USER_ID = 999  # foreign owner proving the 404 anti-enumeration


@pytest.fixture(autouse=True)
def reset_rate_limiter() -> Generator[None, None, None]:
    """Isolate slowapi counters between tests (shared in-memory storage)."""
    limiter.reset()
    yield


@pytest.fixture(autouse=True)
def data_root(tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> Path:
    """Redirect settings.DATA_ROOT into an isolated tmp directory."""
    root = tmp_path / "data"
    monkeypatch.setattr(settings, "DATA_ROOT", str(root))
    return root


@pytest.fixture
def db_session() -> Generator[DBSession, None, None]:
    """Provide an isolated in-memory SQLite session with all tables created."""
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
        email="api-user@example.com",
        hashed_password=User.hash_password("Passw0rd!Strong"),
        username="api-user",
    )
    db_session.add(row)
    db_session.commit()
    db_session.refresh(row)
    return row


@pytest.fixture
def published_app(db_session: DBSession) -> AgentApp:
    """A published AgentApp sessions may bind to."""
    row = AgentApp(name="pub-app", system_prompt="x", status="published")
    db_session.add(row)
    db_session.commit()
    db_session.refresh(row)
    return row


@pytest.fixture
def draft_app(db_session: DBSession) -> AgentApp:
    """A draft AgentApp that must reject session creation with 422."""
    row = AgentApp(name="draft-app", system_prompt="x", status="draft")
    db_session.add(row)
    db_session.commit()
    db_session.refresh(row)
    return row


@pytest.fixture
def client(db_session: DBSession, db_user: User) -> Generator[TestClient, None, None]:
    """Minimal app wiring the sessions router with limiter + overrides."""
    app = FastAPI()
    app.state.limiter = limiter
    app.add_exception_handler(RequestValidationError, validation_exception_handler)
    app.add_exception_handler(HTTPException, http_exception_handler)  # pyright: ignore[reportArgumentType]
    app.add_exception_handler(StarletteHTTPException, http_exception_handler)  # pyright: ignore[reportArgumentType]
    app.add_exception_handler(RateLimitExceeded, rate_limit_exceeded_handler)  # pyright: ignore[reportArgumentType]
    app.include_router(sessions_module.router)
    app.dependency_overrides[auth_module.get_current_user] = lambda: db_user
    app.dependency_overrides[common_module.get_db_session] = lambda: db_session
    with TestClient(app) as test_client:
        yield test_client


def _seed_session(
    db: DBSession,
    *,
    id: str,  # noqa: A002 — mirrors the ORM column name
    user_id: int,
    agent_app_id: int | None,
    name: str = "chat",
    created_at: datetime | None = None,
) -> SessionRow:
    """Insert one Session row and return it."""
    row = SessionRow(
        id=id,
        user_id=user_id,
        agent_app_id=agent_app_id,
        name=name,
        created_at=created_at or datetime(2024, 1, 1, tzinfo=UTC),
    )
    db.add(row)
    db.commit()
    db.refresh(row)
    return row


def _write_l2(app_id: int, user_id: int, session_id: str, rows: list[dict[str, Any]]) -> None:
    """Pre-seed an L2 JSONL file for export/read tests."""
    path = context_store.session_file_path(app_id, user_id, session_id)
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text("".join(json.dumps(row) + "\n" for row in rows), encoding="utf-8")


class _FakeRuntime:
    """Minimal runtime double exposing only ``get_chat_history``."""

    def __init__(self, history: list[Message]) -> None:
        self._history = history

    async def get_chat_history(self, session_id: str) -> list[Message]:
        return list(self._history)


async def _async_value(value: Any) -> Any:
    """Awaitable returning ``value`` — lets lambdas stand in for async fns."""
    return value


def _patch_l1_fallback(monkeypatch: pytest.MonkeyPatch, db: DBSession, history: list[Message]) -> None:
    """Point the L1 fallback at a fake runtime bound to the test engine."""
    monkeypatch.setattr(runtime, "get_runtime", lambda *a, **k: _async_value(_FakeRuntime(history)))
    monkeypatch.setattr(sessions_service, "_open_runtime_db_session", lambda: DBSession(db.get_bind()))


# ---------------------------------------------------------------------------
# §9.2 CRUD unit tests
# ---------------------------------------------------------------------------


def test_list_sessions_returns_only_owned(
    client: TestClient, db_session: DBSession, db_user: User, published_app: AgentApp
) -> None:
    """The list shows only the caller's rows, newest first, as PageResult."""
    _seed_session(db_session, id="mine-old", user_id=db_user.id, agent_app_id=published_app.id, created_at=datetime(2024, 1, 1, tzinfo=UTC))
    _seed_session(db_session, id="mine-new", user_id=db_user.id, agent_app_id=published_app.id, created_at=datetime(2024, 3, 1, tzinfo=UTC))
    _seed_session(db_session, id="foreign", user_id=OTHER_USER_ID, agent_app_id=published_app.id, created_at=datetime(2024, 4, 1, tzinfo=UTC))

    resp = client.get("/sessions", params={"page": 1, "pageSize": 10})

    assert resp.status_code == 200
    data = resp.json()["data"]
    assert data["total"] == 2
    assert [item["session_id"] for item in data["items"]] == ["mine-new", "mine-old"]
    assert all(item["message_count"] is None for item in data["items"])  # list: no N+1


def test_get_session_404_for_other_user(
    client: TestClient, db_session: DBSession, db_user: User
) -> None:
    """A foreign session id returns 404 (anti-enumeration, never 403)."""
    _seed_session(db_session, id="theirs", user_id=OTHER_USER_ID, agent_app_id=None)
    _seed_session(db_session, id="mine", user_id=db_user.id, agent_app_id=None)

    assert client.get("/sessions/theirs").status_code == 404

    mine = client.get("/sessions/mine")
    assert mine.status_code == 200
    assert mine.json()["data"]["session_id"] == "mine"
    assert mine.json()["data"]["message_count"] == 0  # detail fills the count


def test_create_session_validates_agent_app_published(
    client: TestClient, draft_app: AgentApp
) -> None:
    """Creating a session for a draft app fails with 422."""
    resp = client.post("/sessions", json={"agent_app_id": draft_app.id, "name": "x"})

    assert resp.status_code == 422


def test_create_session_requires_agent_app_id(client: TestClient) -> None:
    """Omitting agent_app_id fails validation with 422 (mandatory int)."""
    resp = client.post("/sessions", json={"name": "x"})

    assert resp.status_code == 422


def test_create_session_associates_user(
    client: TestClient, db_session: DBSession, db_user: User, published_app: AgentApp
) -> None:
    """POST auto-associates the user (idempotent) and creates the session."""
    first = client.post("/sessions", json={"agent_app_id": published_app.id, "name": "first"})

    assert first.status_code == 201
    body = first.json()["data"]
    assert body["agent_app_id"] == published_app.id
    assert body["name"] == "first"
    assert len(body["session_id"]) == 36  # server-generated UUID

    assocs = db_session.exec(
        select(UserAgentAppAssociation).where(
            UserAgentAppAssociation.user_id == db_user.id,
            UserAgentAppAssociation.agent_app_id == published_app.id,
        )
    ).all()
    assert len(assocs) == 1

    second = client.post("/sessions", json={"agent_app_id": published_app.id, "name": "again"})
    assert second.status_code == 201  # idempotent association, no error
    assocs_after = db_session.exec(
        select(UserAgentAppAssociation).where(
            UserAgentAppAssociation.user_id == db_user.id,
            UserAgentAppAssociation.agent_app_id == published_app.id,
        )
    ).all()
    assert len(assocs_after) == 1  # still a single association row


def test_update_session_other_user_returns_404(
    client: TestClient, db_session: DBSession, db_user: User, published_app: AgentApp
) -> None:
    """PATCH on a foreign session 404s; on an owned session it renames."""
    _seed_session(db_session, id="theirs-p", user_id=OTHER_USER_ID, agent_app_id=published_app.id)
    seeded = _seed_session(db_session, id="mine-p", user_id=db_user.id, agent_app_id=published_app.id)

    assert client.patch("/sessions/theirs-p", json={"name": "hack"}).status_code == 404

    ok = client.patch("/sessions/mine-p", json={"name": "renamed"})
    assert ok.status_code == 200
    assert ok.json()["data"]["name"] == "renamed"
    refreshed = db_session.get(SessionRow, seeded.id)
    assert refreshed is not None and refreshed.name == "renamed"

    assert client.patch("/sessions/mine-p", json={"name": ""}).status_code == 422  # min_length=1


def test_delete_session_cascades_checkpoint(
    client: TestClient,
    db_session: DBSession,
    db_user: User,
    published_app: AgentApp,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    """DELETE triggers checkpoint cleanup, drops the row, stays idempotent."""
    _seed_session(db_session, id="del-me", user_id=db_user.id, agent_app_id=published_app.id)
    calls: list[str] = []

    async def fake_checkpoint(session_id: str) -> None:
        calls.append(session_id)

    monkeypatch.setattr(runtime, "delete_thread_checkpoint", fake_checkpoint)

    resp = client.delete("/sessions/del-me")

    assert resp.status_code == 200
    assert resp.json()["data"] is None  # ApiResponse[None]
    assert calls == ["del-me"]
    assert db_session.get(SessionRow, "del-me") is None
    assert client.delete("/sessions/del-me").status_code == 404  # idempotent re-delete


# ---------------------------------------------------------------------------
# §9.1 export unit tests
# ---------------------------------------------------------------------------


def test_export_session_history_returns_messages(
    client: TestClient, db_session: DBSession, db_user: User, published_app: AgentApp
) -> None:
    """format=json: metadata header + messages array + attachment header."""
    _seed_session(db_session, id="exp-json", user_id=db_user.id, agent_app_id=published_app.id, name="the chat")
    rows = [
        {"seq": 1, "ts": "t1", "type": "message", "role": "user", "content": "hi"},
        {"seq": 2, "ts": "t2", "type": "message", "role": "assistant", "content": "hello"},
    ]
    _write_l2(published_app.id, db_user.id, "exp-json", rows)

    resp = client.get("/sessions/exp-json/export", params={"format": "json"})

    assert resp.status_code == 200
    assert resp.headers["content-disposition"] == 'attachment; filename="exp-json.json"'
    payload = resp.json()
    assert payload["session_id"] == "exp-json"
    assert payload["name"] == "the chat"
    assert payload["agent_app_id"] == published_app.id
    assert payload["message_count"] == 2
    assert payload["messages"] == rows
    assert payload["exported_at"]
    assert "code" not in payload and "data" not in payload  # non-envelope


def test_export_session_history_format_jsonl(
    client: TestClient, db_session: DBSession, db_user: User, published_app: AgentApp
) -> None:
    """format=jsonl: application/x-ndjson with one row per line."""
    _seed_session(db_session, id="exp-jsonl", user_id=db_user.id, agent_app_id=published_app.id)
    rows = [
        {"seq": 1, "ts": "t1", "type": "message", "role": "user", "content": "q"},
        {"seq": 2, "ts": "t2", "type": "message", "role": "assistant", "content": "a"},
    ]
    _write_l2(published_app.id, db_user.id, "exp-jsonl", rows)

    resp = client.get("/sessions/exp-jsonl/export", params={"format": "jsonl"})

    assert resp.status_code == 200
    assert resp.headers["content-type"].startswith("application/x-ndjson")
    assert resp.headers["content-disposition"] == 'attachment; filename="exp-jsonl.jsonl"'
    lines = resp.text.strip().splitlines()
    assert [json.loads(line) for line in lines] == rows


def test_export_fallback_rebuilds_l2(
    client: TestClient,
    db_session: DBSession,
    db_user: User,
    published_app: AgentApp,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    """Missing L2 file: L1 history is rebuilt, exported and self-healed."""
    _seed_session(db_session, id="exp-rebuild", user_id=db_user.id, agent_app_id=published_app.id)
    history = [Message(role="user", content="re-q"), Message(role="assistant", content="re-a")]
    _patch_l1_fallback(monkeypatch, db_session, history)

    resp = client.get("/sessions/exp-rebuild/export")

    assert resp.status_code == 200
    payload = resp.json()
    assert payload["message_count"] == 2
    assert [row["content"] for row in payload["messages"]] == ["re-q", "re-a"]
    # Self-heal: the rebuilt transcript is now persisted on disk.
    healed = context_store.session_file_path(published_app.id, db_user.id, "exp-rebuild")
    assert healed.exists()


def test_export_orphan_session_without_app(
    client: TestClient, db_session: DBSession, db_user: User
) -> None:
    """A session whose app was hard-deleted exports the L2 leftovers only."""
    _seed_session(db_session, id="exp-orphan", user_id=db_user.id, agent_app_id=None)

    resp = client.get("/sessions/exp-orphan/export")

    assert resp.status_code == 200
    assert resp.json()["messages"] == []


def test_export_session_history_other_user_forbidden(
    client: TestClient, db_session: DBSession, published_app: AgentApp
) -> None:
    """Exporting a foreign session returns 404 (anti-enumeration)."""
    _seed_session(db_session, id="exp-theirs", user_id=OTHER_USER_ID, agent_app_id=published_app.id)

    assert client.get("/sessions/exp-theirs/export").status_code == 404


def test_export_session_history_empty_messages(
    client: TestClient,
    db_session: DBSession,
    db_user: User,
    published_app: AgentApp,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    """An existing session with no transcript exports empty messages (200)."""
    _seed_session(db_session, id="exp-empty", user_id=db_user.id, agent_app_id=published_app.id)
    _patch_l1_fallback(monkeypatch, db_session, [])  # empty checkpoint history

    resp = client.get("/sessions/exp-empty/export")

    assert resp.status_code == 200
    assert resp.json()["messages"] == []
    assert resp.json()["message_count"] == 0


def test_export_invalid_format_rejected(
    client: TestClient, db_session: DBSession, db_user: User, published_app: AgentApp
) -> None:
    """format=xml fails the Query pattern validation with 422."""
    _seed_session(db_session, id="exp-bad", user_id=db_user.id, agent_app_id=published_app.id)

    resp = client.get("/sessions/exp-bad/export", params={"format": "xml"})

    assert resp.status_code == 422
