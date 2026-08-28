"""Chat service for the G4 interaction layer (spec-g4-chat §10.1).

Function-style service (same-package sibling of ``sessions_service``) owning
the five chat responsibilities: the non-streaming auto-approve loop with
RunTracer persistence and the naming hook (§4.4/§7.2/§8), the SSE stream
generator with typed frames and heartbeat comments (§4.1), the L2 row
history projection with pending-interrupt pull-along (§5.3/§6.1), the
disaster rebuild orchestration (§6.2) and the chat trace query (§7.3).
Dependency direction is one-way:
``api -> chat_service -> (runtime / context_store-via-sessions_service /
session_naming / run_tracer)``.
"""

from __future__ import annotations

import asyncio
import json
import time
from collections.abc import AsyncGenerator
from typing import Any, Optional

from langchain_core.messages import AIMessage, BaseMessage, HumanMessage
from sqlmodel import Session as DBSession
from sqlmodel import col, select

from app.core.config import settings
from app.core.logging import logger
from app.models.agent_assets import AgentApp
from app.models.session import Session
from app.models.subagent_trace import SubAgentTrace
from app.schemas.chat import (
    ActionRequest,
    ChatResponse,
    ChatTraceItem,
    HistoryItem,
    InterruptPayload,
    Message,
    MessagesResponse,
    RebuildResult,
    StreamEvent,
)
from app.services.agents import runtime, session_naming, sessions_service
from app.services.agents.run_tracer import RunTracer

# SSE heartbeat interval (spec-g4-chat §4.1): comment frames keep proxies
# from dropping an idle connection during minute-long tool executions.
_HEARTBEAT_SECONDS = 15.0

# Sentinel telling the stream loop the pump task has ended.
_STREAM_SENTINEL = object()


class ChatServiceError(ValueError):
    """Base class for chat business errors the API layer maps to HTTP codes."""


class NothingToRebuildError(ChatServiceError):
    """L2 has no readable rows to rebuild from (API maps to 422)."""


class InterruptPendingError(ChatServiceError):
    """Thread paused on an interrupt; resolve it before rebuilding (API: 409)."""


def _last_user_content(messages: list[Message]) -> str:
    """Content of the last user turn (trace ``prompt`` field)."""
    for message in reversed(messages):
        if message.role == "user":
            return message.content
    return ""


def _app_name(target: Session, app_row: Optional[AgentApp]) -> str:
    """AgentApp name for the trace row; degenerates safely for orphan rows."""
    if app_row is not None:
        return app_row.name
    return f"app-{target.agent_app_id}"


def _persist_chat_trace(
    db: DBSession,
    *,
    session_id: str,
    name: str,
    model_name: str,
    status: str,
    prompt: str,
    turns: int,
    duration_seconds: float,
    final_message: str,
    events: list[dict[str, Any]],
    error: Optional[str],
    created_by: Optional[str],
) -> None:
    """Persist one chat-round trace row behind ``CHAT_TRACE_ENABLED`` (§7.2).

    Never raises: a broken trace write must not mask the chat outcome.
    """
    if not settings.CHAT_TRACE_ENABLED:
        return
    try:
        row = SubAgentTrace(
            name=name,
            status=status,
            prompt=prompt,
            model=model_name,
            turns=turns,
            duration_seconds=duration_seconds,
            final_message=final_message,
            events=events,
            error=error,
            created_by=created_by,
            source="chat",
            session_id=session_id,
        )
        db.add(row)
        db.commit()
        logger.info(
            "chat_trace_persisted",
            session_id=session_id,
            status=status,
            trace_id=row.id,
            event_count=len(events),
        )
    except Exception:  # noqa: BLE001 — trace persistence must never mask the outcome
        logger.exception("chat_trace_persist_failed", session_id=session_id, status=status)
        try:
            db.rollback()
        except Exception:  # noqa: BLE001, S110 — best-effort rollback
            pass


def _finish_round_trace(
    db: DBSession,
    *,
    tracer: RunTracer,
    session_id: str,
    name: str,
    model_name: str,
    status: str,
    prompt: str,
    duration_seconds: float,
    final_message: str,
    error: Optional[str],
    created_by: Optional[str],
) -> None:
    """Close the tracer's event stream and persist the round trace row."""
    turns = tracer.llm_call_count
    events = tracer.finish(
        "success" if status == "success" else "error",
        [],
        turns=turns,
        duration_seconds=duration_seconds,
        error=error,
    )
    _persist_chat_trace(
        db,
        session_id=session_id,
        name=name,
        model_name=model_name,
        status=status,
        prompt=prompt,
        turns=turns,
        duration_seconds=duration_seconds,
        final_message=final_message,
        events=events,
        error=error,
        created_by=created_by,
    )


# ---------------------------------------------------------------------------
# D1: chat() non-streaming with auto-approve (§4.4)
# ---------------------------------------------------------------------------


async def chat(
    db: DBSession,
    target: Session,
    messages: list[Message],
    *,
    user_id: int,
    username: Optional[str],
) -> ChatResponse:
    """Run one non-streaming turn with interrupt auto-approval (§4.4).

    Every interrupt is resumed with all-approve decisions until the turn
    completes or ``CHAT_AUTO_APPROVE_MAX_ROUNDS`` is hit — in the limit case
    the response carries the interrupt projection and the thread stays
    paused. Each round mounts a shared RunTracer (§7.2) and lands a trace
    row; the naming hook fires before execution (§8).
    """
    if target.agent_app_id is None:
        raise ChatServiceError("session has no bound agent app")
    rt = await runtime.get_runtime(db, target.agent_app_id, user_id=user_id)
    app_row = db.get(AgentApp, target.agent_app_id) if target.agent_app_id is not None else None
    model_ref = app_row.model if app_row is not None else None
    await session_naming.maybe_name_session(db, target.id, target.name, messages, model_name=model_ref)

    model_name = rt._model_label()  # noqa: SLF001 — same package: resolved upstream model id
    app_name = _app_name(target, app_row)
    tracer = RunTracer(model_name=model_name)
    started = time.perf_counter()
    prompt = _last_user_content(messages)

    accumulated: list[Message] = []
    interrupt: Optional[InterruptPayload] = None
    try:
        reply = await rt.ainvoke(
            messages,
            session_id=target.id,
            user_id=str(user_id),
            username=username,
            extra_callbacks=[tracer],
        )
        pending = await rt.get_pending_interrupt(target.id)
        if pending is None:
            accumulated.extend(reply)
        rounds = 0
        while pending is not None:
            rounds += 1
            if rounds > settings.CHAT_AUTO_APPROVE_MAX_ROUNDS:
                interrupt = InterruptPayload(**pending)
                logger.warning(
                    "chat_auto_approve_limit_exceeded",
                    session_id=target.id,
                    rounds=rounds,
                )
                break
            decisions = [{"type": "approve"} for _ in pending.get("action_requests", [])]
            resume = Message(role="user", content=json.dumps({"decisions": decisions}))
            reply = await rt.ainvoke(
                [resume],
                session_id=target.id,
                user_id=str(user_id),
                username=username,
                extra_callbacks=[tracer],
            )
            pending = await rt.get_pending_interrupt(target.id)
            if pending is None:
                accumulated.extend(reply)
    except Exception as exc:
        error = f"{type(exc).__name__}: {exc}"
        _finish_round_trace(
            db,
            tracer=tracer,
            session_id=target.id,
            name=app_name,
            model_name=model_name,
            status="error",
            prompt=prompt,
            duration_seconds=time.perf_counter() - started,
            final_message="",
            error=error,
            created_by=username,
        )
        raise

    final_message = accumulated[-1].content if accumulated else ""
    _finish_round_trace(
        db,
        tracer=tracer,
        session_id=target.id,
        name=app_name,
        model_name=model_name,
        status="success",
        prompt=prompt,
        duration_seconds=time.perf_counter() - started,
        final_message=final_message,
        error=None,
        created_by=username,
    )
    return ChatResponse(messages=accumulated, interrupt=interrupt)


# ---------------------------------------------------------------------------
# D2: chat_stream() SSE generator (§4.1)
# ---------------------------------------------------------------------------


def _sse_frame(event: StreamEvent) -> str:
    """Serialise one StreamEvent into an SSE data frame."""
    return f"data: {json.dumps(event.model_dump(exclude_none=True))}\n\n"


def _parse_interrupt_projection(content: str) -> Optional[dict[str, Any]]:
    """Parse the interrupt chunk payload back into the §4.2 projection."""
    try:
        parsed = json.loads(content)
    except (TypeError, ValueError):
        return None
    if isinstance(parsed, dict) and isinstance(parsed.get("action_requests"), list) and parsed["action_requests"]:
        return parsed
    return None


async def chat_stream(
    db: DBSession,
    target: Session,
    messages: list[Message],
    *,
    user_id: int,
    username: Optional[str],
) -> AsyncGenerator[str, None]:
    """Stream one turn as SSE frames: typed chunks + heartbeat + done (§4.1).

    The runtime chunk stream is pumped through a queue so idle gaps longer
    than ``_HEARTBEAT_SECONDS`` emit ``: ping`` comment frames without
    cancelling the underlying generator. An interrupt tail chunk maps to a
    structured ``interrupt`` frame followed by ``done(interrupted=true)``;
    failures emit an ``error`` frame and still emit ``done``.
    """
    if target.agent_app_id is None:
        raise ChatServiceError("session has no bound agent app")
    rt = await runtime.get_runtime(db, target.agent_app_id, user_id=user_id)
    app_row = db.get(AgentApp, target.agent_app_id) if target.agent_app_id is not None else None
    model_ref = app_row.model if app_row is not None else None
    await session_naming.maybe_name_session(db, target.id, target.name, messages, model_name=model_ref)

    model_name = rt._model_label()  # noqa: SLF001 — same package: resolved upstream model id
    app_name = _app_name(target, app_row)
    tracer = RunTracer(model_name=model_name)
    started = time.perf_counter()
    prompt = _last_user_content(messages)

    message_count = 0
    compressed = False
    interrupted = False
    stream_error: Optional[str] = None
    message_parts: list[str] = []

    queue: asyncio.Queue[Any] = asyncio.Queue()

    async def _pump() -> None:
        try:
            async for chunk in rt.astream(
                messages,
                session_id=target.id,
                user_id=str(user_id),
                username=username,
                extra_callbacks=[tracer],
            ):
                await queue.put(chunk)
        except Exception as exc:  # noqa: BLE001 — surfaced as the error frame
            await queue.put(exc)
        finally:
            await queue.put(_STREAM_SENTINEL)

    pump_task = asyncio.create_task(_pump())
    try:
        while True:
            try:
                item = await asyncio.wait_for(queue.get(), timeout=_HEARTBEAT_SECONDS)
            except asyncio.TimeoutError:
                yield ": ping\n\n"
                continue
            if item is _STREAM_SENTINEL:
                break
            if isinstance(item, Exception):
                stream_error = f"{type(item).__name__}: {item}"
                yield _sse_frame(StreamEvent(type="error", message=str(item)))
                break
            chunk = item
            if chunk.type == "tool_call":
                yield _sse_frame(
                    StreamEvent(type="tool_call", name=chunk.name, content=chunk.content, source=chunk.source)
                )
            elif chunk.type == "summary":
                compressed = True
                yield _sse_frame(StreamEvent(type="summary", summary_text=chunk.content))
            elif chunk.type == "interrupt":
                interrupted = True
                projection = _parse_interrupt_projection(chunk.content)
                if projection is not None:
                    yield _sse_frame(
                        StreamEvent(
                            type="interrupt",
                            action_requests=[ActionRequest(**action) for action in projection["action_requests"]],
                        )
                    )
                else:
                    yield _sse_frame(StreamEvent(type="error", message="unprojectable interrupt payload"))
            else:
                message_count += 1
                message_parts.append(chunk.content)
                yield _sse_frame(StreamEvent(type="message", content=chunk.content, source=chunk.source))
            if interrupted:
                break
    finally:
        pump_task.cancel()

    yield _sse_frame(
        StreamEvent(
            type="done",
            message_count=message_count,
            compressed=compressed,
            interrupted=interrupted,
        )
    )

    _finish_round_trace(
        db,
        tracer=tracer,
        session_id=target.id,
        name=app_name,
        model_name=model_name,
        status="error" if stream_error is not None else "success",
        prompt=prompt,
        duration_seconds=time.perf_counter() - started,
        final_message="".join(message_parts),
        error=stream_error,
        created_by=username,
    )


# ---------------------------------------------------------------------------
# D3: get_history() L2 row projection (§5.3/§6.1)
# ---------------------------------------------------------------------------


async def get_history(db: DBSession, target: Session, *, user_id: int) -> MessagesResponse:
    """Project the L2 rows for history rendering + pull the pending interrupt."""
    rows = await sessions_service.read_or_rebuild_l2(target)
    items = []
    for index, row in enumerate(rows):
        row_type = row.get("type")
        if row_type not in ("message", "tool_call", "summary"):
            row_type = "message"  # unknown future row kinds degrade to plain text
        items.append(
            HistoryItem(
                type=row_type,  # pyright: ignore[reportArgumentType]
                seq=int(row.get("seq") or index + 1),
                ts=str(row.get("ts") or ""),
                role=row.get("role"),
                content=row.get("content"),
                name=row.get("name"),
                summary=row.get("summary"),
            )
        )

    pending_interrupt: Optional[InterruptPayload] = None
    if target.agent_app_id is not None:
        try:
            rt = await runtime.get_runtime(db, target.agent_app_id, user_id=user_id)
            projection = await rt.get_pending_interrupt(target.id)
            if projection is not None:
                pending_interrupt = InterruptPayload(**projection)
        except ValueError:
            # App deleted/unpublished: history still renders, interrupt state
            # is unreachable (§6.1 degrade).
            logger.info("chat_history_runtime_unavailable", session_id=target.id)
    return MessagesResponse(messages=items, pending_interrupt=pending_interrupt)


# ---------------------------------------------------------------------------
# D4: rebuild() disaster recovery (§6.2)
# ---------------------------------------------------------------------------


async def rebuild(db: DBSession, target: Session, *, user_id: int) -> RebuildResult:
    """Rehydrate the L1 checkpoint from the L2 rows (§6.2).

    Boundary: no readable L2 rows → ``NothingToRebuildError`` (API 422);
    thread paused on an interrupt → ``InterruptPendingError`` (API 409).
    Message rows re-inject verbatim (user→Human, assistant→AI), summary
    rows re-inject as HumanMessage, tool_call rows are skipped (their
    tool_call_id pairing cannot be restored) and counted.
    """
    if target.agent_app_id is None:
        raise ChatServiceError("session has no bound agent app")
    rows = await sessions_service.read_or_rebuild_l2(target)
    if not rows:
        raise NothingToRebuildError("no readable L2 rows to rebuild from")

    rt = await runtime.get_runtime(db, target.agent_app_id, user_id=user_id)
    pending = await rt.get_pending_interrupt(target.id)
    if pending is not None:
        raise InterruptPendingError("thread is paused on an interrupt; resolve it before rebuilding")

    rebuilt: list[BaseMessage] = []
    skipped_tool_calls = 0
    for row in rows:
        row_type = row.get("type")
        content = str(row.get("content") or "")
        if row_type == "tool_call":
            skipped_tool_calls += 1
            continue
        if row_type == "summary":
            # SummarizationMiddleware's summary_message shape (§6.2)
            rebuilt.append(HumanMessage(content=content))
        elif row.get("role") == "assistant":
            rebuilt.append(AIMessage(content=content))
        else:
            rebuilt.append(HumanMessage(content=content))

    await runtime.delete_thread_checkpoint(target.id)
    await rt.rebuild_thread(target.id, rebuilt)
    return RebuildResult(
        rebuilt_messages=len(rebuilt),
        skipped_tool_calls=skipped_tool_calls,
        l2_source_lines=len(rows),
    )


# ---------------------------------------------------------------------------
# D5: get_traces() chat trace query (§7.3)
# ---------------------------------------------------------------------------


async def get_traces(db: DBSession, target: Session, *, limit: int = 100) -> list[ChatTraceItem]:
    """List this session's chat trace rows, newest first (§7.3)."""
    stmt = (
        select(SubAgentTrace)
        .where(col(SubAgentTrace.source) == "chat", col(SubAgentTrace.session_id) == target.id)
        .order_by(col(SubAgentTrace.created_at).desc())
        .limit(limit)
    )
    rows = db.exec(stmt).all()
    return [
        ChatTraceItem(
            id=row.id,
            status=row.status,
            turns=row.turns,
            duration_seconds=row.duration_seconds,
            error=row.error,
            created_at=str(row.created_at),
            events=row.events,
        )
        for row in rows
    ]
