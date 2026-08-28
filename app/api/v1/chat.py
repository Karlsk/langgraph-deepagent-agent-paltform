"""G4 chat interaction API (spec-g4-chat §3/§10.3).

Five endpoints behind the mandatory ``X-Session-Id`` header (interaction
surface; the management CRUD stays on ``/sessions/{sid}``). The router is
thin: auth + ownership 404 anti-enumeration, rate limits, the ApiResponse
envelope (auto-approve-limit responses carry the programmatic reason) and
the SSE ``StreamingResponse`` with anti-proxy headers. All orchestration
lives in ``chat_service``.
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
)
from app.services.agents import chat_service, sessions_service

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
