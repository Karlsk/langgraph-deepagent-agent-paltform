"""G4 chat interaction API (spec-g4-chat §3/§10.3).

Eight endpoints behind the mandatory ``X-Session-Id`` header (interaction
surface; the management CRUD stays on ``/sessions/{sid}``). The router is
thin: auth + ownership 404 anti-enumeration, rate limits, the ApiResponse
envelope (auto-approve-limit responses carry the programmatic reason) and
the SSE ``StreamingResponse`` with anti-proxy headers. All orchestration
lives in ``chat_service``.

SSE primary + polling fallback: ``/chat/stream`` pushes SSE frames while
buffering events to Redis; on disconnect the agent keeps running and the
frontend recovers via ``/chat/updates`` and ``/chat/task_status`` using the
``task_id`` from the ``task_init`` frame. ``/chat/stream_async`` is a
pure-async entry that returns a ``task_id`` immediately.
"""

from typing import Annotated, Optional

from fastapi import APIRouter, Depends, Header, HTTPException, Request
from fastapi.responses import StreamingResponse
from sqlmodel import Session as DBSession

from app.api.v1.agent_assets_common import get_db_session
from app.api.v1.auth import get_current_user
from app.core.config import settings
from app.core.limiter import limiter
from app.models.session import Session as SessionRow
from app.models.user import User
from app.schemas.base import ApiResponse
from app.schemas.chat import (
    ChatRequest,
    ChatResponse,
    ChatTraceItem,
    MessagesResponse,
    RebuildResult,
    StreamAsyncResponse,
    TaskStatusResponse,
    TaskUpdatesResponse,
)
from app.services.agents import agent_apps_service, chat_service, sessions_service

router = APIRouter()

# Mandatory session addressing header (spec-g4-chat §3.1): missing → 422 via
# request validation, foreign/unknown → 404 (anti-enumeration).
SessionHeader = Annotated[str, Header(description="Chat session id the endpoint addresses")]


async def _resolve_session_by_header_or_404(db: DBSession, user: User, session_id: str) -> SessionRow:
    """Load the header-addressed session; ownership failures 404 (§3.1)."""
    target = await sessions_service.get_session(db, session_id)
    if target is None or target.user_id != user.id:
        raise HTTPException(status_code=404, detail="session not found")
    return target


@router.post("/chat", response_model=ApiResponse[ChatResponse])
@limiter.limit(settings.RATE_LIMIT_ENDPOINTS["chat"][0])
async def chat(
    request: Request,
    x_session_id: SessionHeader,
    body: ChatRequest,
    user: User = Depends(get_current_user),
    db: DBSession = Depends(get_db_session),
) -> ApiResponse[ChatResponse]:
    """Run one non-streaming turn with interrupt auto-approval (§4.4).

    Auto-approve-limit responses stay HTTP 200 — the envelope message
    ``auto_approve_limit_exceeded`` is the programmatic reason string (§4.5).
    """
    target = await _resolve_session_by_header_or_404(db, user, x_session_id)
    result = await chat_service.chat(db, target, body.messages, user_id=user.id, username=user.username)
    message = "auto_approve_limit_exceeded" if result.interrupt is not None else "success"
    return ApiResponse.success(result, message=message)


@router.post("/chat/stream")
@limiter.limit(settings.RATE_LIMIT_ENDPOINTS["chat_stream"][0])
async def chat_stream(
    request: Request,
    x_session_id: SessionHeader,
    body: ChatRequest,
    user: User = Depends(get_current_user),
    db: DBSession = Depends(get_db_session),
) -> StreamingResponse:
    """Stream one turn as SSE frames (§4.1).

    Envelope-exempt by design: the frame protocol replaces the ApiResponse
    envelope; anti-proxy headers keep intermediaries from buffering.
    """
    target = await _resolve_session_by_header_or_404(db, user, x_session_id)
    if target.agent_app_id is not None:
        app_status = await agent_apps_service.get_app_status(db, target.agent_app_id)
        if app_status != "published":
            raise HTTPException(
                status_code=422,
                detail=f"agent app is not published (status={app_status})",
            )
    generator = chat_service.chat_stream(db, target, body.messages, user_id=user.id, username=user.username)
    return StreamingResponse(
        generator,
        media_type="text/event-stream",
        headers={"Cache-Control": "no-cache", "X-Accel-Buffering": "no"},
    )


@router.get("/messages", response_model=ApiResponse[MessagesResponse])
@limiter.limit(settings.RATE_LIMIT_ENDPOINTS["messages"][0])
async def get_messages(
    request: Request,
    x_session_id: SessionHeader,
    user: User = Depends(get_current_user),
    db: DBSession = Depends(get_db_session),
) -> ApiResponse[MessagesResponse]:
    """L2 row history + pending interrupt pull-along (§5.3/§6.1)."""
    target = await _resolve_session_by_header_or_404(db, user, x_session_id)
    result = await chat_service.get_history(db, target, user_id=user.id)
    return ApiResponse.success(result)


@router.post("/rebuild", response_model=ApiResponse[RebuildResult])
@limiter.limit(settings.RATE_LIMIT_ENDPOINTS["rebuild"][0])
async def rebuild(
    request: Request,
    x_session_id: SessionHeader,
    user: User = Depends(get_current_user),
    db: DBSession = Depends(get_db_session),
) -> ApiResponse[RebuildResult]:
    """Disaster-rebuild the L1 checkpoint from the L2 rows (§6.2).

    Boundary mapping: no readable L2 rows → 422; thread paused on an
    interrupt → 409 (resolve it first).
    """
    target = await _resolve_session_by_header_or_404(db, user, x_session_id)
    try:
        result = await chat_service.rebuild(db, target, user_id=user.id)
    except chat_service.NothingToRebuildError as exc:
        raise HTTPException(status_code=422, detail=str(exc)) from exc
    except chat_service.InterruptPendingError as exc:
        raise HTTPException(status_code=409, detail=str(exc)) from exc
    return ApiResponse.success(result)


@router.get("/chat/traces", response_model=ApiResponse[list[ChatTraceItem]])
async def list_chat_traces(
    x_session_id: SessionHeader,
    user: User = Depends(get_current_user),
    db: DBSession = Depends(get_db_session),
    limit: Optional[int] = None,
) -> ApiResponse[list[ChatTraceItem]]:
    """List this session's chat trace rows, newest first (§7.3)."""
    target = await _resolve_session_by_header_or_404(db, user, x_session_id)
    result = await chat_service.get_traces(db, target, limit=limit or 100)
    return ApiResponse.success(result)


@router.post("/chat/stream_async", response_model=ApiResponse[StreamAsyncResponse])
@limiter.limit(settings.RATE_LIMIT_ENDPOINTS["chat_stream_async"][0])
async def chat_stream_async(
    request: Request,
    x_session_id: SessionHeader,
    body: ChatRequest,
    user: User = Depends(get_current_user),
    db: DBSession = Depends(get_db_session),
) -> ApiResponse[StreamAsyncResponse]:
    """Start a background streaming task and return its id immediately.

    The agent runs detached; the frontend polls ``/chat/updates`` for events.
    Same auth + app-status checks as the SSE ``/chat/stream``.
    """
    target = await _resolve_session_by_header_or_404(db, user, x_session_id)
    if target.agent_app_id is not None:
        app_status = await agent_apps_service.get_app_status(db, target.agent_app_id)
        if app_status != "published":
            raise HTTPException(
                status_code=422,
                detail=f"agent app is not published (status={app_status})",
            )
    task_id = await chat_service.start_background_stream(
        db, target, body.messages, user_id=user.id, username=user.username,
    )
    return ApiResponse.success(StreamAsyncResponse(task_id=task_id))


@router.get("/chat/updates", response_model=ApiResponse[TaskUpdatesResponse])
@limiter.limit(settings.RATE_LIMIT_ENDPOINTS["chat_updates"][0])
async def chat_updates(
    request: Request,
    x_session_id: SessionHeader,
    task_id: str,
    cursor: int = 0,
    user: User = Depends(get_current_user),
    db: DBSession = Depends(get_db_session),
) -> ApiResponse[TaskUpdatesResponse]:
    """Poll incremental events for a streaming task (cursor-based).

    Ownership is enforced: the task's ``session_id`` must match the
    header-addressed session (anti-enumeration → 404).
    """
    await _resolve_session_by_header_or_404(db, user, x_session_id)
    result = await chat_service.get_task_updates(task_id, cursor, session_id=x_session_id)
    if result is None:
        raise HTTPException(status_code=404, detail="task not found")
    return ApiResponse.success(result)


@router.get("/chat/task_status", response_model=ApiResponse[TaskStatusResponse])
@limiter.limit(settings.RATE_LIMIT_ENDPOINTS["chat_task_status"][0])
async def chat_task_status(
    request: Request,
    x_session_id: SessionHeader,
    task_id: str,
    user: User = Depends(get_current_user),
    db: DBSession = Depends(get_db_session),
) -> ApiResponse[TaskStatusResponse]:
    """Lightweight status check for a streaming task.

    Ownership enforced via session_id matching (anti-enumeration → 404).
    """
    await _resolve_session_by_header_or_404(db, user, x_session_id)
    result = await chat_service.get_task_status(task_id, session_id=x_session_id)
    if result is None:
        raise HTTPException(status_code=404, detail="task not found")
    return ApiResponse.success(result)
