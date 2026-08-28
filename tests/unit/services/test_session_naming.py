"""Unit tests for the G4 session auto-naming chain (spec-g4-chat §8).

Two-level strategy: immediate placeholder (20-char truncate, ``新会话``
fallback, atomic claim via ``claim_session_name``) + fire-and-forget LLM
overwrite (app-resolved model with default-model fallback, failures logged
never retried). Runs against in-memory SQLite; the LLM seam is monkeypatched.
"""

import asyncio
from collections.abc import Generator
from typing import Any

import pytest
from sqlalchemy.pool import StaticPool
from sqlmodel import SQLModel
from sqlmodel import Session as DBSession
from sqlmodel import create_engine

from app.core.config import settings
from app.models.session import Session as SessionRow
from app.models.user import User
from app.schemas.chat import Message, SessionTitle
from app.services.agents import session_naming, sessions_service
from app.services.database import database_service
from app.services.llm import llm_service

pytestmark = pytest.mark.unit


@pytest.fixture
def db(monkeypatch: pytest.MonkeyPatch) -> Generator[DBSession, None, None]:
    """In-memory SQLite session; the shared engine also serves background tasks."""
    engine = create_engine(
        "sqlite://",
        connect_args={"check_same_thread": False},
        poolclass=StaticPool,
    )
    SQLModel.metadata.create_all(engine)
    # Background naming tasks open their own session on database_service.engine;
    # redirect it so writes land in the same in-memory database.
    monkeypatch.setattr(database_service, "engine", engine)
    session = DBSession(engine)
    yield session
    session.close()


@pytest.fixture
def user(db: DBSession) -> User:
    """Session owner row."""
    row = User(
        email="naming-owner@example.com",
        hashed_password=User.hash_password("Passw0rd!Strong"),
        username="naming-owner",
    )
    db.add(row)
    db.commit()
    db.refresh(row)
    return row


@pytest.fixture
def unnamed_session(db: DBSession, user: User) -> SessionRow:
    """One unnamed chat session awaiting its first turn."""
    row = SessionRow(id="s-naming", user_id=user.id, username=user.username, agent_app_id=1, name="")
    db.add(row)
    db.commit()
    db.refresh(row)
    return row


@pytest.fixture
def llm_calls(monkeypatch: pytest.MonkeyPatch) -> dict[str, Any]:
    """Recording stub for the LLM seam (default: succeed with a fixed title)."""
    record: dict[str, Any] = {
        "calls": [],
        "fail_with": None,
        "fail_model_names": set(),
        "title": "LLM 生成的标题",
    }

    async def fake_call(messages: Any, model_name: Any = None, response_format: Any = None, **kwargs: Any) -> Any:
        record["calls"].append(model_name)
        if record["fail_with"] is not None:
            raise record["fail_with"]
        if model_name in record["fail_model_names"]:
            raise RuntimeError(f"model down: {model_name}")
        assert response_format is SessionTitle
        return SessionTitle(title=record["title"])

    monkeypatch.setattr(llm_service, "call", fake_call)
    return record


async def _drain_background() -> None:
    """Let fire-and-forget naming tasks settle before asserting."""
    for _ in range(10):
        await asyncio.sleep(0)
    pending = list(session_naming._background_tasks)
    for task in pending:
        try:
            await task
        except Exception:  # noqa: BLE001, S110 — naming tasks must never raise anyway
            pass


def _reload(db: DBSession, session_id: str) -> SessionRow:
    """Fresh read of the row (expiry-safe)."""
    db.expire_all()
    row = db.get(SessionRow, session_id)
    assert row is not None
    return row


# ---------------------------------------------------------------------------
# Placeholder rules (§8.1 immediate level)
# ---------------------------------------------------------------------------


def test_placeholder_truncates_to_20_chars() -> None:
    """Long first messages are cut at 20 characters (CJK-friendly)."""
    assert (
        session_naming._build_placeholder("一二三四五六七八九十一二三四五六七八九十一二三四五")
        == "一二三四五六七八九十一二三四五六七八九十"
    )


def test_placeholder_collapses_whitespace() -> None:
    """Inner runs of whitespace collapse to single spaces before truncating."""
    assert session_naming._build_placeholder("  hello   world  ") == "hello world"


def test_placeholder_falls_back_for_blank_message() -> None:
    """A whitespace-only message still yields a sensible default name."""
    assert session_naming._build_placeholder("   ") == "新会话"


# ---------------------------------------------------------------------------
# claim_session_name (C1, atomic WHERE name = '')
# ---------------------------------------------------------------------------


def test_claim_session_name_is_atomic(db: DBSession, unnamed_session: SessionRow) -> None:
    """Only the first claim wins; later callers see False and keep off."""
    assert asyncio.run(sessions_service.claim_session_name(db, unnamed_session.id, "第一个"))
    assert not asyncio.run(sessions_service.claim_session_name(db, unnamed_session.id, "第二个"))
    assert _reload(db, unnamed_session.id).name == "第一个"


def test_claim_session_name_skips_named_rows(db: DBSession, unnamed_session: SessionRow) -> None:
    """Rows already carrying a name (manual rename) are never claimed."""
    asyncio.run(sessions_service.rename_session(db, unnamed_session.id, "手动命名"))
    assert not asyncio.run(sessions_service.claim_session_name(db, unnamed_session.id, "占位"))


# ---------------------------------------------------------------------------
# maybe_name_session orchestration (§8.1 two levels)
# ---------------------------------------------------------------------------


def test_maybe_name_session_skips_named_sessions(
    db: DBSession, unnamed_session: SessionRow, llm_calls: dict[str, Any]
) -> None:
    """Named sessions return False without any DB write or LLM call."""
    asyncio.run(sessions_service.rename_session(db, unnamed_session.id, "已有名字"))

    claimed = asyncio.run(
        session_naming.maybe_name_session(db, unnamed_session.id, "已有名字", [Message(role="user", content="hi")])
    )

    assert claimed is False
    assert llm_calls["calls"] == []
    assert _reload(db, unnamed_session.id).name == "已有名字"


def test_naming_disabled_truncates_only(
    db: DBSession,
    unnamed_session: SessionRow,
    llm_calls: dict[str, Any],
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    """SESSION_NAMING_ENABLED=false: placeholder only, no LLM task (§8.3)."""
    monkeypatch.setattr(settings, "SESSION_NAMING_ENABLED", False)

    claimed = asyncio.run(
        session_naming.maybe_name_session(
            db,
            unnamed_session.id,
            "",
            [Message(role="user", content="一二三四五六七八九十一二三四五六七八九十一二三四五")],
        )
    )
    asyncio.run(_drain_background())

    assert claimed is True
    assert llm_calls["calls"] == []
    assert _reload(db, unnamed_session.id).name == "一二三四五六七八九十一二三四五六七八九十"


def test_llm_overwrite_succeeds_with_app_model(
    db: DBSession, unnamed_session: SessionRow, llm_calls: dict[str, Any]
) -> None:
    """Enabled: placeholder claimed, then the app model's title overwrites it."""
    claimed = asyncio.run(
        session_naming.maybe_name_session(
            db,
            unnamed_session.id,
            "",
            [Message(role="user", content="帮我写一份周报")],
            model_name="provider/app-model",
        )
    )
    asyncio.run(_drain_background())

    assert claimed is True
    assert llm_calls["calls"] == ["provider/app-model"]
    assert _reload(db, unnamed_session.id).name == "LLM 生成的标题"


def test_llm_failure_falls_back_to_default_model(
    db: DBSession, unnamed_session: SessionRow, llm_calls: dict[str, Any]
) -> None:
    """App-model failure retries once with the default model (议题 6 定案)."""
    llm_calls["fail_model_names"] = {"provider/app-model"}
    llm_calls["title"] = "默认模型标题"

    claimed = asyncio.run(
        session_naming.maybe_name_session(
            db,
            unnamed_session.id,
            "",
            [Message(role="user", content="hello")],
            model_name="provider/app-model",
        )
    )
    asyncio.run(_drain_background())

    assert claimed is True
    assert llm_calls["calls"] == ["provider/app-model", None]
    assert _reload(db, unnamed_session.id).name == "默认模型标题"


def test_llm_total_failure_keeps_placeholder(
    db: DBSession, unnamed_session: SessionRow, llm_calls: dict[str, Any]
) -> None:
    """Both attempts fail: placeholder stays, no exception escapes (§8.3)."""
    llm_calls["fail_with"] = RuntimeError("all models down")

    claimed = asyncio.run(
        session_naming.maybe_name_session(
            db,
            unnamed_session.id,
            "",
            [Message(role="user", content="   ")],  # blank -> placeholder 新会话
            model_name=None,
        )
    )
    asyncio.run(_drain_background())

    assert claimed is True
    assert llm_calls["calls"] == [None]
    assert _reload(db, unnamed_session.id).name == "新会话"
