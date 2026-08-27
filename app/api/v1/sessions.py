"""G3 session CRUD + export API (spec-g3-session §11.5/§11.5.3).

Six endpoints over ``sessions_service``: paginated list, metadata detail
(with ``message_count``), create (auto-associate), rename, three-layer
cascade delete, and the history export. The export is the project's first
file-download endpoint: it bypasses the ApiResponse envelope and streams
with ``Content-Disposition: attachment`` instead (§11.5.3 sets the
precedent for future downloads).

Error semantics mirror the other routers: foreign or missing sessions
404 (anti-enumeration — ownership never yields 403), unknown apps 404,
unpublished apps 422.
"""

import json
from collections.abc import Iterator
from datetime import UTC, datetime
from typing import Any, Optional

from fastapi import APIRouter, Depends, HTTPException, Query, Request
from fastapi.responses import JSONResponse, StreamingResponse
from sqlmodel import Session as DBSession

from app.api.v1.agent_assets_common import get_db_session
from app.api.v1.auth import get_current_user
from app.core.config import settings
from app.core.limiter import limiter
from app.core.logging import logger
from app.models.session import Session as SessionRow
from app.models.user import User
from app.schemas.base import ApiResponse, PageResult
from app.schemas.session import SessionCreate, SessionRead, SessionUpdate
from app.services.agents import agent_apps_service, sessions_service

router = APIRouter()


async def _resolve_session_or_404(
    db: DBSession, user: User, session_id: str
) -> SessionRow:
    """Load one session owned by ``user``; anything else is a 404."""
    target = await sessions_service.get_session(db, session_id)
    if target is None or target.user_id != user.id:
        raise HTTPException(status_code=404, detail="session not found")
    return target


@router.get("/sessions", response_model=ApiResponse[PageResult[SessionRead]])
@limiter.limit(settings.RATE_LIMIT_ENDPOINTS["sessions"][0])
async def list_sessions(
    request: Request,
    user: User = Depends(get_current_user),
    db: DBSession = Depends(get_db_session),
    agent_app_id: Optional[int] = Query(default=None),
    page: int = Query(default=1, ge=1),
    page_size: int = Query(default=20, ge=1, le=100, alias="pageSize"),
) -> ApiResponse[PageResult[SessionRead]]:
    """List the caller's sessions (created_at desc + optional app filter).

    ``message_count`` stays None here — filling it would deserialize one
    checkpoint state per row (N+1); only the detail endpoint pays it.
    """
    result = await sessions_service.list_user_sessions(
        db, user_id=user.id, agent_app_id=agent_app_id, page=page, page_size=page_size
    )
    payload = PageResult(
        items=[sessions_service.to_read(row) for row in result.items],
        total=result.total,
        page=result.page,
        page_size=result.page_size,
    )
    return ApiResponse.success(payload)


@router.get("/sessions/{session_id}", response_model=ApiResponse[SessionRead])
@limiter.limit(settings.RATE_LIMIT_ENDPOINTS["sessions"][0])
async def get_session(
    request: Request,
    session_id: str,
    user: User = Depends(get_current_user),
    db: DBSession = Depends(get_db_session),
) -> ApiResponse[SessionRead]:
    """Session metadata with ``message_count`` (L2 rows, L1 fallback).

    A pure metadata read: no lazy workspace validation — chatting or
    exporting through ``get_runtime`` triggers it naturally (§12.2.2).
    """
    target = await _resolve_session_or_404(db, user, session_id)
    message_count = await sessions_service.count_messages(target)
    return ApiResponse.success(sessions_service.to_read(target, message_count=message_count))


@router.post("/sessions", response_model=ApiResponse[SessionRead], status_code=201)
@limiter.limit(settings.RATE_LIMIT_ENDPOINTS["sessions"][0])
async def create_session(
    request: Request,
    body: SessionCreate,
    user: User = Depends(get_current_user),
    db: DBSession = Depends(get_db_session),
) -> ApiResponse[SessionRead]:
    """Create a session, auto-associating the user with the app first.

    "User opens a chat on a published app" is implicit authorization: the
    idempotent associate validates publication, upserts the association
    and materializes the User layer; unknown apps 404, unpublished 422.
    """
    try:
        await agent_apps_service.associate_user_with_app(
            db, user_id=user.id, app_id=body.agent_app_id, current_user_id=user.id
        )
    except agent_apps_service.AgentAppNotFoundError as exc:
        raise HTTPException(status_code=404, detail="agent_app not found") from exc
    except agent_apps_service.AgentAppNotPublishedError as exc:
        raise HTTPException(status_code=422, detail="agent_app is not published") from exc

    new_session = await sessions_service.create_session(
        db, user_id=user.id, username=user.username,
        agent_app_id=body.agent_app_id, name=body.name,
    )
    logger.info(
        "session_created",
        session_id=new_session.id,
        user_id=user.id,
        agent_app_id=body.agent_app_id,
        auto_associated=True,
    )
    return ApiResponse.success(sessions_service.to_read(new_session), code=201)


@router.patch("/sessions/{session_id}", response_model=ApiResponse[SessionRead])
@limiter.limit(settings.RATE_LIMIT_ENDPOINTS["sessions"][0])
async def update_session(
    request: Request,
    session_id: str,
    body: SessionUpdate,
    user: User = Depends(get_current_user),
    db: DBSession = Depends(get_db_session),
) -> ApiResponse[SessionRead]:
    """Rename one session (the only mutable field)."""
    target = await _resolve_session_or_404(db, user, session_id)
    updated = await sessions_service.rename_session(db, target.id, body.name)
    if updated is None:  # pragma: no cover — race with a concurrent delete
        raise HTTPException(status_code=404, detail="session not found")
    logger.info(
        "session_renamed",
        session_id=session_id,
        user_id=user.id,
        new_name=body.name,
    )
    return ApiResponse.success(sessions_service.to_read(updated))


@router.delete("/sessions/{session_id}", response_model=ApiResponse[None])
@limiter.limit(settings.RATE_LIMIT_ENDPOINTS["sessions"][0])
async def delete_session(
    request: Request,
    session_id: str,
    user: User = Depends(get_current_user),
    db: DBSession = Depends(get_db_session),
) -> ApiResponse[None]:
    """Cascade-delete L1 checkpoint -> L2 JSONL -> L0 row (§11.5.1).

    L1/L2 are best effort; the L0 delete is the completion signal. The
    envelope convention matches every other DELETE here (200 + empty
    data, not 204) so the frontend unwraps uniformly.
    """
    target = await _resolve_session_or_404(db, user, session_id)
    result = await sessions_service.delete_session_cascade(db, target)
    logger.info(
        "session_deleted",
        session_id=session_id,
        user_id=user.id,
        checkpoint_cleaned=result.checkpoint_cleaned,
        jsonl_cleaned=result.jsonl_cleaned,
    )
    return ApiResponse.success(None)


def _iter_jsonl(rows: list[dict[str, Any]]) -> Iterator[str]:
    """Yield one JSON line per L2 row (newline-delimited payload)."""
    for row in rows:
        yield json.dumps(row, ensure_ascii=False) + "\n"


@router.get("/sessions/{session_id}/export")
@limiter.limit(settings.RATE_LIMIT_ENDPOINTS["sessions"][0])
async def export_session(
    request: Request,
    session_id: str,
    format: str = Query(default="json", pattern="^(json|jsonl)$"),
    user: User = Depends(get_current_user),
    db: DBSession = Depends(get_db_session),
) -> Any:
    """Export the session transcript as a file download (§11.5.3).

    Non-envelope by design: json returns a single document with a
    metadata header + the message rows; jsonl streams one row per line
    as ``application/x-ndjson``. An existing session always exports —
    an empty transcript is a valid payload, not a 404.
    """
    target = await _resolve_session_or_404(db, user, session_id)
    rows = await sessions_service.read_or_rebuild_l2(target)
    if format == "jsonl":
        return StreamingResponse(
            _iter_jsonl(rows),
            media_type="application/x-ndjson",
            headers={"Content-Disposition": f'attachment; filename="{session_id}.jsonl"'},
        )
    payload = {
        "session_id": target.id,
        "name": target.name,
        "agent_app_id": target.agent_app_id,
        "created_at": target.created_at.isoformat(),
        "exported_at": datetime.now(UTC).isoformat(),
        "message_count": len(rows),
        "messages": rows,
    }
    return JSONResponse(
        payload,
        headers={"Content-Disposition": f'attachment; filename="{session_id}.json"'},
    )
