"""Unit tests for ``app/services/agents/sessions_service.py`` (G3 Phase 4).

Covers the session business orchestration (spec-g3-session §11.7/§11.5.1/
§11.5.3): paginated listing with the agent_app filter, UUID id generation,
rename with ``updated_at`` sync, the L1->L2->L0 delete cascade with
best-effort semantics, the L2-first + L1-fallback read path with self-heal
rewrite, orphan-session degradation and the ``SessionRead`` projection.
Runs against in-memory SQLite with DATA_ROOT redirected into tmp_path;
runtime seams (checkpointer helper / get_runtime) are monkeypatched.
"""

import asyncio
import json
from collections.abc import Generator
from datetime import UTC, datetime
from pathlib import Path
from typing import Any

import pytest
from sqlalchemy.pool import StaticPool
from sqlmodel import SQLModel
from sqlmodel import Session as DBSession
from sqlmodel import create_engine

from app.core.config import settings
from app.models.agent_assets import AgentApp
from app.models.session import Session as SessionRow
from app.models.user import User
from app.schemas.chat import Message
from app.services.agents import context_store, runtime, sessions_service

pytestmark = pytest.mark.unit


@pytest.fixture
def data_root(tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> Path:
    """Redirect settings.DATA_ROOT into an isolated tmp directory."""
    root = tmp_path / "data"
    monkeypatch.setattr(settings, "DATA_ROOT", str(root))
    return root


@pytest.fixture
def db() -> Generator[DBSession, None, None]:
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
        email="sess-owner@example.com",
        hashed_password=User.hash_password("Passw0rd!Strong"),
        username="sess-owner",
    )
    db.add(row)
    db.commit()
    db.refresh(row)
    return row


@pytest.fixture
def other_user(db: DBSession) -> User:
    """A second user whose rows must stay invisible to ``user``."""
    row = User(
        email="sess-other@example.com",
        hashed_password=User.hash_password("Passw0rd!Strong"),
        username="sess-other",
    )
    db.add(row)
    db.commit()
    db.refresh(row)
    return row


@pytest.fixture
def app_row(db: DBSession) -> AgentApp:
    """One published AgentApp the sessions bind to."""
    row = AgentApp(name="g3-app", system_prompt="x", status="published")
    db.add(row)
    db.commit()
    db.refresh(row)
    return row


@pytest.fixture
def other_app(db: DBSession) -> AgentApp:
    """A second AgentApp used to prove the agent_app_id filter."""
    row = AgentApp(name="g3-other", system_prompt="x", status="published")
    db.add(row)
    db.commit()
    db.refresh(row)
    return row


def _seed_session(
    db: DBSession,
    *,
    id: str,  # noqa: A002 — mirrors the ORM column name
    user_id: int,
    agent_app_id: int | None,
    name: str = "",
    created_at: datetime | None = None,
    username: str | None = None,
) -> SessionRow:
    """Insert one Session row and return it."""
    row = SessionRow(
        id=id,
        user_id=user_id,
        username=username,
        agent_app_id=agent_app_id,
        name=name,
        created_at=created_at or datetime(2024, 1, 1, tzinfo=UTC),
    )
    db.add(row)
    db.commit()
    db.refresh(row)
    return row


class _FakeRuntime:
    """Minimal runtime double exposing only ``get_chat_history``."""

    def __init__(self, history: list[Message]) -> None:
        self._history = history

    async def get_chat_history(self, session_id: str) -> list[Message]:
        return list(self._history)


# ---------------------------------------------------------------------------
# list_user_sessions / get_session
# ---------------------------------------------------------------------------


def test_list_user_sessions_paginates_filters_and_scopes(
    db: DBSession,
    user: User,
    other_user: User,
    app_row: AgentApp,
    other_app: AgentApp,
) -> None:
    """created_at desc + agent_app_id filter + user scoping + PageResult echo."""
    _seed_session(db, id="old", user_id=user.id, agent_app_id=app_row.id, created_at=datetime(2024, 1, 1, tzinfo=UTC))
    _seed_session(db, id="new", user_id=user.id, agent_app_id=app_row.id, created_at=datetime(2024, 3, 1, tzinfo=UTC))
    _seed_session(db, id="other-app", user_id=user.id, agent_app_id=other_app.id, created_at=datetime(2024, 2, 1, tzinfo=UTC))
    _seed_session(db, id="foreign", user_id=other_user.id, agent_app_id=app_row.id, created_at=datetime(2024, 4, 1, tzinfo=UTC))

    result = asyncio.run(
        sessions_service.list_user_sessions(db, user_id=user.id, page=1, page_size=2)
    )

    assert result.total == 3  # the other_user row is invisible
    assert [row.id for row in result.items] == ["new", "other-app"]  # created_at desc
    assert result.page == 1
    assert result.page_size == 2

    filtered = asyncio.run(
        sessions_service.list_user_sessions(
            db, user_id=user.id, agent_app_id=app_row.id, page=1, page_size=20
        )
    )
    assert filtered.total == 2
    assert {row.id for row in filtered.items} == {"old", "new"}

    page2 = asyncio.run(
        sessions_service.list_user_sessions(db, user_id=user.id, page=2, page_size=2)
    )
    assert [row.id for row in page2.items] == ["old"]


def test_get_session_returns_row_or_none(db: DBSession, user: User, app_row: AgentApp) -> None:
    """Primary-key lookup; ownership checks stay with the caller."""
    _seed_session(db, id="sid-1", user_id=user.id, agent_app_id=app_row.id)

    found = asyncio.run(sessions_service.get_session(db, "sid-1"))
    missing = asyncio.run(sessions_service.get_session(db, "nope"))

    assert found is not None and found.id == "sid-1"
    assert missing is None


# ---------------------------------------------------------------------------
# create_session / rename_session
# ---------------------------------------------------------------------------


def test_create_session_generates_uuid_and_persists_fields(
    db: DBSession, user: User, app_row: AgentApp
) -> None:
    """Id is a server-generated UUID4 string; every field persists."""
    row = asyncio.run(
        sessions_service.create_session(
            db,
            user_id=user.id,
            username=user.username,
            agent_app_id=app_row.id,
            name="first chat",
        )
    )

    assert len(row.id) == 36 and row.id.count("-") == 4  # UUID string
    assert row.user_id == user.id
    assert row.username == "sess-owner"
    assert row.agent_app_id == app_row.id
    assert row.name == "first chat"

    reloaded = asyncio.run(sessions_service.get_session(db, row.id))
    assert reloaded is not None and reloaded.name == "first chat"


def test_rename_session_updates_name_and_updated_at(
    db: DBSession, user: User, app_row: AgentApp
) -> None:
    """Rename syncs updated_at; unknown id returns None."""
    seeded = _seed_session(db, id="sid-r", user_id=user.id, agent_app_id=app_row.id)
    original_updated = seeded.updated_at

    renamed = asyncio.run(sessions_service.rename_session(db, "sid-r", "renamed"))

    assert renamed is not None
    assert renamed.name == "renamed"
    assert renamed.updated_at is not None
    assert renamed.updated_at != original_updated  # §11.4.1 rename stamps updated_at

    assert asyncio.run(sessions_service.rename_session(db, "ghost", "x")) is None


# ---------------------------------------------------------------------------
# delete_session_cascade (L1 -> L2 -> L0, best effort)
# ---------------------------------------------------------------------------


def _write_l2(app_id: int, user_id: int, session_id: str, rows: list[dict[str, Any]]) -> Path:
    path = context_store.session_file_path(app_id, user_id, session_id)
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text("".join(json.dumps(row) + "\n" for row in rows), encoding="utf-8")
    return path


def test_delete_session_cascade_cleans_all_three_layers(
    db: DBSession, user: User, app_row: AgentApp, data_root: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    """L1 checkpoint deleted, L2 file removed, L0 row gone; result flags True."""
    seeded = _seed_session(db, id="sid-del", user_id=user.id, agent_app_id=app_row.id)
    _write_l2(app_row.id, user.id, "sid-del", [{"seq": 1, "ts": "t", "type": "message", "role": "user", "content": "hi"}])

    calls: list[str] = []

    async def fake_checkpoint(session_id: str) -> None:
        calls.append(session_id)

    monkeypatch.setattr(runtime, "delete_thread_checkpoint", fake_checkpoint)

    result = asyncio.run(sessions_service.delete_session_cascade(db, seeded))

    assert calls == ["sid-del"]  # L1 attempted with the session id
    assert result.checkpoint_cleaned is True
    assert result.jsonl_cleaned is True
    assert not context_store.session_file_path(app_row.id, user.id, "sid-del").exists()
    assert asyncio.run(sessions_service.get_session(db, "sid-del")) is None


def test_delete_session_cascade_survives_checkpoint_failure(
    db: DBSession, user: User, app_row: AgentApp, data_root: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    """A checkpoint failure logs + flags False but never blocks L2/L0."""
    seeded = _seed_session(db, id="sid-l1fail", user_id=user.id, agent_app_id=app_row.id)

    async def boom(session_id: str) -> None:
        raise RuntimeError("pool down")

    monkeypatch.setattr(runtime, "delete_thread_checkpoint", boom)

    result = asyncio.run(sessions_service.delete_session_cascade(db, seeded))

    assert result.checkpoint_cleaned is False
    assert result.jsonl_cleaned is True  # missing file still counts as success
    assert asyncio.run(sessions_service.get_session(db, "sid-l1fail")) is None  # L0 done


def test_delete_session_cascade_orphan_without_app_id(
    db: DBSession, user: User, monkeypatch: pytest.MonkeyPatch
) -> None:
    """agent_app_id NULL skips the L2 path lookup; checkpoint still attempted."""
    seeded = _seed_session(db, id="sid-orphan", user_id=user.id, agent_app_id=None)
    calls: list[str] = []

    async def fake_checkpoint(session_id: str) -> None:
        calls.append(session_id)

    monkeypatch.setattr(runtime, "delete_thread_checkpoint", fake_checkpoint)

    result = asyncio.run(sessions_service.delete_session_cascade(db, seeded))

    assert calls == ["sid-orphan"]
    assert result.checkpoint_cleaned is True
    assert result.jsonl_cleaned is True
    assert asyncio.run(sessions_service.get_session(db, "sid-orphan")) is None


# ---------------------------------------------------------------------------
# read_or_rebuild_l2 / count_messages
# ---------------------------------------------------------------------------


def test_read_or_rebuild_l2_prefers_existing_file(
    db: DBSession, user: User, app_row: AgentApp, data_root: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    """An existing parseable L2 file is returned verbatim; runtime untouched."""
    seeded = _seed_session(db, id="sid-file", user_id=user.id, agent_app_id=app_row.id)
    rows = [
        {"seq": 1, "ts": "t1", "type": "message", "role": "user", "content": "hi"},
        {"seq": 2, "ts": "t2", "type": "message", "role": "assistant", "content": "hello"},
    ]
    _write_l2(app_row.id, user.id, "sid-file", rows)

    async def no_runtime(*args: Any, **kwargs: Any) -> Any:
        raise AssertionError("L2 file present: runtime must not be touched")

    monkeypatch.setattr(runtime, "get_runtime", no_runtime)

    out = asyncio.run(sessions_service.read_or_rebuild_l2(seeded))
    assert out == rows


def test_read_or_rebuild_l2_rebuilds_from_checkpoint_and_selfheals(
    db: DBSession, user: User, app_row: AgentApp, data_root: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    """Missing file -> L1 get_chat_history rebuilt, atomically written back."""
    seeded = _seed_session(db, id="sid-rebuild", user_id=user.id, agent_app_id=app_row.id)
    history = [Message(role="user", content="q1"), Message(role="assistant", content="a1")]
    monkeypatch.setattr(runtime, "get_runtime", lambda *a, **k: _async_value(_FakeRuntime(history)))
    # Fresh session on the same engine: the production helper closes its
    # own session on context exit — the shared fixture session must survive.
    monkeypatch.setattr(sessions_service, "_open_runtime_db_session", lambda: DBSession(db.get_bind()))

    out = asyncio.run(sessions_service.read_or_rebuild_l2(seeded))

    assert [row["role"] for row in out] == ["user", "assistant"]
    assert [row["content"] for row in out] == ["q1", "a1"]
    assert [row["seq"] for row in out] == [1, 2]
    assert all(row["type"] == "message" and row["ts"] for row in out)

    healed = asyncio.run(
        context_store.read_rows(context_store.session_file_path(app_row.id, user.id, "sid-rebuild"))
    )
    assert healed == out  # self-heal: the rebuild is persisted atomically


def test_read_or_rebuild_l2_orphan_app_degrades_to_empty(
    db: DBSession, user: User, app_row: AgentApp, data_root: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    """get_runtime failing (app deleted/unpublished) returns L2 leftovers only."""

    async def gone(*args: Any, **kwargs: Any) -> Any:
        raise ValueError("agent app 99 is not published")

    monkeypatch.setattr(runtime, "get_runtime", gone)
    monkeypatch.setattr(sessions_service, "_open_runtime_db_session", lambda: DBSession(db.get_bind()))
    seeded = _seed_session(db, id="sid-gone", user_id=user.id, agent_app_id=app_row.id)

    assert asyncio.run(sessions_service.read_or_rebuild_l2(seeded)) == []
    assert not context_store.session_file_path(app_row.id, user.id, "sid-gone").exists()


def test_read_or_rebuild_l2_null_app_id_returns_empty(
    db: DBSession, user: User, monkeypatch: pytest.MonkeyPatch
) -> None:
    """NULL agent_app_id has no L2 path and no fallback entry point."""
    seeded = _seed_session(db, id="sid-null", user_id=user.id, agent_app_id=None)

    async def no_runtime(*args: Any, **kwargs: Any) -> Any:
        raise AssertionError("no app: runtime must not be touched")

    monkeypatch.setattr(runtime, "get_runtime", no_runtime)

    assert asyncio.run(sessions_service.read_or_rebuild_l2(seeded)) == []


def test_count_messages_uses_same_entry_as_read(
    db: DBSession, user: User, app_row: AgentApp, data_root: Path
) -> None:
    """Count == len(read_or_rebuild_l2 rows): L2 first, L1 fallback counts too."""
    seeded = _seed_session(db, id="sid-count", user_id=user.id, agent_app_id=app_row.id)
    _write_l2(
        app_row.id,
        user.id,
        "sid-count",
        [{"seq": 1, "ts": "t", "type": "message", "role": "user", "content": "x"}],
    )
    assert asyncio.run(sessions_service.count_messages(seeded)) == 1

    empty = _seed_session(db, id="sid-count2", user_id=user.id, agent_app_id=None)
    assert asyncio.run(sessions_service.count_messages(empty)) == 0


# ---------------------------------------------------------------------------
# to_read
# ---------------------------------------------------------------------------


def test_to_read_projects_alias_and_optional_count(
    db: DBSession, user: User, app_row: AgentApp
) -> None:
    """ORM id -> session_id alias; message_count defaults to None."""
    seeded = _seed_session(db, id="sid-read", user_id=user.id, agent_app_id=app_row.id, name="n")

    read = sessions_service.to_read(seeded)
    assert read.session_id == "sid-read"
    assert read.name == "n"
    assert read.agent_app_id == app_row.id
    assert read.message_count is None
    assert read.created_at == seeded.created_at
    assert read.updated_at == seeded.updated_at

    detailed = sessions_service.to_read(seeded, message_count=7)
    assert detailed.message_count == 7


async def _async_value(value: Any) -> Any:
    """Awaitable returning ``value`` — lets lambdas stand in for async fns."""
    return value
