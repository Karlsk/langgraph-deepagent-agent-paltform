"""G3 session-layer business orchestration (spec-g3-session §11.5.5/§11.7).

Function-style service (same-package sibling of ``agent_apps_service``)
owning the Session CRUD, the L1->L2->L0 delete cascade, and the
L2-first + L1-fallback read path. Dependency direction is one-way:
``api -> sessions_service -> (context_store / runtime)``.
"""

from __future__ import annotations

import uuid
from dataclasses import dataclass
from datetime import UTC, datetime
from typing import Any

from sqlmodel import Session as DBSession
from sqlmodel import col, func, select, update

from app.core.logging import logger
from app.models.session import Session
from app.schemas.base import PageResult
from app.schemas.session import SessionRead
from app.services.agents import context_store, runtime


@dataclass
class CascadeResult:
    """Best-effort outcome flags of the three-layer delete (§11.5.1).

    A ``False`` layer never blocks the others — the endpoint logs and
    reports the real outcome instead of assuming success.
    """

    checkpoint_cleaned: bool
    jsonl_cleaned: bool


def _utc_now_iso() -> str:
    """Return the current UTC time as an ISO-8601 string (L2 ``ts``)."""
    return datetime.now(UTC).isoformat()


def _open_runtime_db_session() -> DBSession:
    """Open a short-lived DB session for the L1 fallback path.

    Lazy import keeps module import free of any engine construction; the
    fallback only runs when an L2 file is missing and the app is alive.
    """
    from app.services.database import database_service

    return DBSession(database_service.engine)


async def list_user_sessions(
    session: DBSession,
    *,
    user_id: int,
    agent_app_id: int | None = None,
    page: int,
    page_size: int,
) -> PageResult[Session]:
    """List one user's sessions (created_at desc + optional app filter)."""
    stmt = select(Session).where(col(Session.user_id) == user_id)
    count_stmt = select(func.count()).select_from(Session).where(col(Session.user_id) == user_id)
    if agent_app_id is not None:
        stmt = stmt.where(col(Session.agent_app_id) == agent_app_id)
        count_stmt = count_stmt.where(col(Session.agent_app_id) == agent_app_id)

    total = int(session.exec(count_stmt).one())
    rows = session.exec(
        stmt.order_by(col(Session.created_at).desc()).offset((page - 1) * page_size).limit(page_size)
    ).all()
    return PageResult(items=list(rows), total=total, page=page, page_size=page_size)


async def get_session(session: DBSession, session_id: str) -> Session | None:
    """Fetch by primary key; ownership checks stay with the caller (404)."""
    return session.get(Session, session_id)


async def create_session(
    session: DBSession,
    *,
    user_id: int,
    username: str | None,
    agent_app_id: int,
    name: str,
) -> Session:
    """Create a session row (server-generated UUID id, §11.7)."""
    row = Session(
        id=str(uuid.uuid4()),
        user_id=user_id,
        username=username,
        agent_app_id=agent_app_id,
        name=name,
    )
    session.add(row)
    session.commit()
    session.refresh(row)
    return row


async def rename_session(session: DBSession, session_id: str, new_name: str) -> Session | None:
    """Rename one session and stamp ``updated_at`` (§11.4.1)."""
    row = session.get(Session, session_id)
    if row is None:
        return None
    row.name = new_name
    row.updated_at = datetime.now(UTC)
    session.add(row)
    session.commit()
    session.refresh(row)
    return row


async def claim_session_name(session: DBSession, session_id: str, placeholder: str) -> bool:
    """Atomically claim an unnamed session row (G4 §8.1).

    ``UPDATE ... WHERE name = ''`` in one round-trip — concurrent callers
    race safely: exactly one receives rowcount == 1 and wins the claim.
    """
    stmt = update(Session).where(col(Session.id) == session_id, col(Session.name) == "").values(name=placeholder)
    result = session.exec(stmt)
    session.commit()
    return (result.rowcount or 0) == 1


async def delete_session_cascade(session: DBSession, target: Session) -> CascadeResult:
    """Cascade delete L1 checkpoint -> L2 JSONL -> L0 PG row (§11.5.1).

    L1/L2 are best effort (log + flag False, never block); the L0 delete
    is the completion signal — once gone, repeated DELETEs 404.
    """
    checkpoint_cleaned = False
    try:
        await runtime.delete_thread_checkpoint(target.id)
        checkpoint_cleaned = True
    except Exception:  # noqa: BLE001 — best-effort L1: log + flag, never block (§11.5.1)
        logger.exception("session_cascade_checkpoint_failed", session_id=target.id)

    jsonl_cleaned = False
    try:
        if target.agent_app_id is not None:
            path = context_store.session_file_path(target.agent_app_id, target.user_id, target.id)
            await context_store.delete_session_file(path)  # missing file = success
        jsonl_cleaned = True
    except Exception:  # noqa: BLE001 — best-effort L2: log + flag, never block (§11.5.1)
        logger.exception("session_cascade_jsonl_failed", session_id=target.id)

    row = session.get(Session, target.id)
    if row is not None:
        session.delete(row)
        session.commit()
    return CascadeResult(checkpoint_cleaned=checkpoint_cleaned, jsonl_cleaned=jsonl_cleaned)


async def _rebuild_rows_from_l1(target: Session) -> list[dict[str, Any]]:
    """Rebuild L2 rows from the checkpoint history via ``get_runtime``.

    Raises:
        ValueError: When the app is gone or unpublished (get_runtime
            contract) — callers degrade to the L2 leftovers.
    """
    with _open_runtime_db_session() as db:
        rt = await runtime.get_runtime(db, target.agent_app_id, user_id=target.user_id)  # pyright: ignore[reportArgumentType]
        history = await rt.get_chat_history(target.id)
    ts = _utc_now_iso()
    return [
        {
            "seq": index,
            "ts": ts,
            "type": "message",
            "role": message.role,
            "content": message.content,
        }
        for index, message in enumerate(history, start=1)
    ]


async def read_or_rebuild_l2(target: Session) -> list[dict[str, Any]]:
    """Unified read entry: L2 first, L1 fallback with self-heal (§11.5.3).

    An existing parseable L2 file is returned verbatim (no checkpoint
    access). A missing file triggers a checkpoint rebuild that is written
    back atomically (tmp + rename). Orphan sessions — NULL agent_app_id or
    a deleted/unpublished app — degrade to the current L2 leftovers.
    """
    if target.agent_app_id is None:
        return []

    path = context_store.session_file_path(target.agent_app_id, target.user_id, target.id)
    if path.exists():
        return await context_store.read_rows(path)

    try:
        rows = await _rebuild_rows_from_l1(target)
    except ValueError:
        # App deleted/unpublished: no fallback entry point — keep the
        # current L2 content (here: nothing) without raising (§11.5.3).
        return []
    if rows:
        await context_store.rewrite_all(path, rows)
    return rows


async def count_messages(target: Session) -> int | None:
    """Message count via the same read entry (L2 rows, L1 fallback)."""
    rows = await read_or_rebuild_l2(target)
    return len(rows)


def to_read(row: Session, *, message_count: int | None = None) -> SessionRead:
    """Project an ORM row into the API response schema."""
    return SessionRead(
        session_id=row.id,
        name=row.name,
        agent_app_id=row.agent_app_id,
        created_at=row.created_at,
        updated_at=row.updated_at,
        message_count=message_count,
    )
