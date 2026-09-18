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
from uuid import uuid4

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
    TaskStatusResponse,
    TaskUpdatesResponse,
)
from app.services.agents import runtime, session_naming, sessions_service
from app.services.agents import task_buffer
from app.services.agents.run_tracer import RunTracer
from app.services.database import database_service

# SSE heartbeat interval (spec-g4-chat §4.1): comment frames keep proxies
# from dropping an idle connection during minute-long tool executions.
_HEARTBEAT_SECONDS = 15.0

# Strong references to detached pump tasks so they aren't GC'd when the
# SSE generator exits.  Each task removes itself in its ``finally`` block.
_detached_tasks: set[asyncio.Task] = set()


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


def _new_assistant(reply: list[Message], pre_history: list[Message]) -> list[Message]:
    """Project THIS turn's new assistant replies from the full thread reply.

    ``ainvoke`` returns the whole projected thread (prior turns included,
    §4.5); the prefix aligned with the turn-start history is dropped so the
    frontend never double-renders earlier turns. A mid-turn compression
    rewrites the projection (history collapses into a summary message) so the
    prefix no longer matches — degrade to the trailing assistant reply,
    which after compression is exactly the turn's final answer.

    Args:
        reply: Full projected thread returned by ``ainvoke``.
        pre_history: Projected thread history captured before the invoke.

    Returns:
        The assistant Messages produced during this turn.
    """
    if reply[: len(pre_history)] == pre_history:
        return [m for m in reply[len(pre_history) :] if m.role == "assistant"]
    return [m for m in reply if m.role == "assistant"][-1:]


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
        pre_history = await rt.get_chat_history(target.id)
        reply = await rt.ainvoke(
            messages,
            session_id=target.id,
            user_id=str(user_id),
            username=username,
            extra_callbacks=[tracer],
        )
        pending = await rt.get_pending_interrupt(target.id)
        if pending is None:
            accumulated.extend(_new_assistant(reply, pre_history))
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
            pre_history = await rt.get_chat_history(target.id)
            reply = await rt.ainvoke(
                [resume],
                session_id=target.id,
                user_id=str(user_id),
                username=username,
                extra_callbacks=[tracer],
            )
            pending = await rt.get_pending_interrupt(target.id)
            if pending is None:
                accumulated.extend(_new_assistant(reply, pre_history))
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


def _process_chunk(chunk: Any, *, message_count: int, compressed: bool, interrupted: bool) -> Optional[StreamEvent]:
    """Convert one runtime chunk into a ``StreamEvent`` (shared by SSE + buffered paths)."""
    if chunk.type == "tool_call":
        return StreamEvent(type="tool_call", name=chunk.name, content=chunk.content, source=chunk.source)
    if chunk.type == "summary":
        return StreamEvent(type="summary", summary_text=chunk.content)
    if chunk.type == "interrupt":
        projection = _parse_interrupt_projection(chunk.content)
        if projection is not None:
            return StreamEvent(
                type="interrupt",
                action_requests=[ActionRequest(**action) for action in projection["action_requests"]],
            )
        return StreamEvent(type="error", message="unprojectable interrupt payload")
    return StreamEvent(type="message", content=chunk.content, source=chunk.source)


async def chat_stream(
    db: DBSession,
    target: Session,
    messages: list[Message],
    *,
    user_id: int,
    username: Optional[str],
) -> AsyncGenerator[str, None]:
    """Stream one turn as SSE frames with task_id + Redis buffering (§4.1).

    The first frame is always ``task_init`` carrying the task id so the
    frontend can resume via polling after a page refresh.  The runtime
    chunk stream is pumped through a queue so idle gaps longer than
    ``_HEARTBEAT_SECONDS`` emit ``: ping`` comment frames without
    cancelling the underlying generator.

    When the SSE client disconnects mid-stream the pump task is *detached*
    (not cancelled) so the agent keeps running.  Events continue to be
    buffered to Redis and the client can recover them via
    ``GET /chat/updates``.
    """
    if target.agent_app_id is None:
        yield _sse_frame(StreamEvent(type="error", message="session has no bound agent app"))
        yield _sse_frame(StreamEvent(type="done", message_count=0, compressed=False, interrupted=False))
        return
    try:
        rt = await runtime.get_runtime(db, target.agent_app_id, user_id=user_id)
    except Exception as exc:  # noqa: BLE001 — surfaced as SSE error frame
        yield _sse_frame(StreamEvent(type="error", message=str(exc)))
        yield _sse_frame(StreamEvent(type="done", message_count=0, compressed=False, interrupted=False))
        return
    app_row = db.get(AgentApp, target.agent_app_id) if target.agent_app_id is not None else None
    model_ref = app_row.model if app_row is not None else None
    await session_naming.maybe_name_session(db, target.id, target.name, messages, model_name=model_ref)

    model_name = rt._model_label()  # noqa: SLF001 — same package: resolved upstream model id
    app_name = _app_name(target, app_row)
    tracer = RunTracer(model_name=model_name)
    started = time.perf_counter()
    prompt = _last_user_content(messages)

    task_id = uuid4().hex
    await task_buffer.create_task(task_id, target.id)

    yield _sse_frame(StreamEvent(type="task_init", task_id=task_id))

    message_count = 0
    compressed = False
    interrupted = False
    stream_error: Optional[str] = None
    message_parts: list[str] = []

    queue: asyncio.Queue[Any] = asyncio.Queue()

    async def _pump() -> None:
        nonlocal message_count, compressed, interrupted, stream_error
        try:
            async for chunk in rt.astream(
                messages,
                session_id=target.id,
                user_id=str(user_id),
                username=username,
                extra_callbacks=[tracer],
            ):
                event = _process_chunk(
                    chunk,
                    message_count=message_count,
                    compressed=compressed,
                    interrupted=interrupted,
                )
                if event is not None:
                    if chunk.type == "message":
                        message_count += 1
                        message_parts.append(chunk.content)
                    elif chunk.type == "summary":
                        compressed = True
                    elif chunk.type == "interrupt":
                        interrupted = True
                    await queue.put(event)
                    await task_buffer.push_event(task_id, event.model_dump(exclude_none=True))
                    if chunk.type == "interrupt":
                        break
        except Exception as exc:  # noqa: BLE001
            stream_error = f"{type(exc).__name__}: {exc}"
            err_event = StreamEvent(type="error", message=str(exc))
            await queue.put(err_event)
            await task_buffer.push_event(task_id, err_event.model_dump(exclude_none=True))
        finally:
            done_event = StreamEvent(
                type="done",
                message_count=message_count,
                compressed=compressed,
                interrupted=interrupted,
            )
            await queue.put(done_event)
            await task_buffer.push_event(task_id, done_event.model_dump(exclude_none=True))
            await task_buffer.set_task_status(
                task_id,
                status="error" if stream_error is not None else "done",
                message_count=message_count,
                interrupted=interrupted,
            )
            await task_buffer.finalize_task(task_id)

    def _finish_trace(session: DBSession) -> None:
        _finish_round_trace(
            session,
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

    def _finish_trace_detached(task: asyncio.Task) -> None:
        # Request teardown closes ``db`` before a detached pump finishes, so
        # the trace needs its own session from the global engine.
        if task.cancelled():
            return
        own_db = DBSession(database_service.engine)
        try:
            _finish_trace(own_db)
        finally:
            own_db.close()

    pump_task = asyncio.create_task(_pump())
    try:
        while True:
            try:
                item = await asyncio.wait_for(queue.get(), timeout=_HEARTBEAT_SECONDS)
            except asyncio.TimeoutError:
                yield ": ping\n\n"
                continue
            if isinstance(item, StreamEvent):
                yield _sse_frame(item)
                if item.type == "done":
                    break
    except (asyncio.CancelledError, GeneratorExit):
        # SSE client went away (refresh / navigate / stop): leave the pump
        # running detached so the agent round completes and its events stay
        # recoverable via GET /chat/updates; the done-callback persists the
        # round trace after request teardown.
        if pump_task.done():
            if not pump_task.cancelled():
                _finish_trace(db)
        else:
            pump_task.add_done_callback(_finish_trace_detached)
        raise
    finally:
        if not pump_task.done():
            _detached_tasks.add(pump_task)
            pump_task.add_done_callback(_detached_tasks.discard)
            logger.info("chat_stream_pump_detached", task_id=task_id, session_id=target.id)

    _finish_trace(db)


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
                source=row.get("source"),
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
    tool_call_id pairing cannot be restored) and counted, and display-only
    subagent rows (non-null ``source``) are skipped to keep the checkpoint
    context clean.
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
    skipped_subagent_messages = 0
    for row in rows:
        row_type = row.get("type")
        content = str(row.get("content") or "")
        if row_type == "tool_call":
            skipped_tool_calls += 1
            continue
        if row.get("source"):
            # Display-only subagent row: never re-inject (§6.2).
            skipped_subagent_messages += 1
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
        skipped_subagent_messages=skipped_subagent_messages,
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


# ---------------------------------------------------------------------------
# D6: async streaming + polling support
# ---------------------------------------------------------------------------


async def start_background_stream(
    db: DBSession,
    target: Session,
    messages: list[Message],
    *,
    user_id: int,
    username: Optional[str],
) -> str:
    """Spawn a background agent task and return its *task_id*.

    The runtime setup happens synchronously (within the request) so
    validation errors surface as HTTP errors.  The actual agent execution
    is handed off to a detached task that buffers events to Redis.
    """
    if target.agent_app_id is None:
        raise ChatServiceError("session has no bound agent app")
    rt = await runtime.get_runtime(db, target.agent_app_id, user_id=user_id)
    app_row = db.get(AgentApp, target.agent_app_id) if target.agent_app_id is not None else None
    model_ref = app_row.model if app_row is not None else None
    await session_naming.maybe_name_session(db, target.id, target.name, messages, model_name=model_ref)

    model_name = rt._model_label()  # noqa: SLF001
    app_name = _app_name(target, app_row)
    tracer = RunTracer(model_name=model_name)
    started = time.perf_counter()
    prompt = _last_user_content(messages)

    task_id = uuid4().hex
    await task_buffer.create_task(task_id, target.id)

    async def _run() -> None:
        message_count = 0
        compressed = False
        interrupted = False
        stream_error: Optional[str] = None
        message_parts: list[str] = []
        own_db = DBSession(database_service.engine)
        try:
            async for chunk in rt.astream(
                messages,
                session_id=target.id,
                user_id=str(user_id),
                username=username,
                extra_callbacks=[tracer],
            ):
                event = _process_chunk(
                    chunk,
                    message_count=message_count,
                    compressed=compressed,
                    interrupted=interrupted,
                )
                if event is not None:
                    if chunk.type == "message":
                        message_count += 1
                        message_parts.append(chunk.content)
                    elif chunk.type == "summary":
                        compressed = True
                    elif chunk.type == "interrupt":
                        interrupted = True
                    await task_buffer.push_event(task_id, event.model_dump(exclude_none=True))
                    await task_buffer.set_task_status(
                        task_id, message_count=message_count, interrupted=interrupted,
                    )
                    if chunk.type == "interrupt":
                        break
        except Exception as exc:  # noqa: BLE001
            stream_error = f"{type(exc).__name__}: {exc}"
            err_event = StreamEvent(type="error", message=str(exc))
            await task_buffer.push_event(task_id, err_event.model_dump(exclude_none=True))
        finally:
            done_event = StreamEvent(
                type="done",
                message_count=message_count,
                compressed=compressed,
                interrupted=interrupted,
            )
            await task_buffer.push_event(task_id, done_event.model_dump(exclude_none=True))
            await task_buffer.set_task_status(
                task_id,
                status="error" if stream_error is not None else "done",
                message_count=message_count,
                interrupted=interrupted,
            )
            await task_buffer.finalize_task(task_id)
            _finish_round_trace(
                own_db,
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
            own_db.close()

    bg_task = asyncio.create_task(_run())
    _detached_tasks.add(bg_task)
    bg_task.add_done_callback(_detached_tasks.discard)
    return task_id


async def get_task_updates(
    task_id: str,
    cursor: int,
    session_id: str,
) -> TaskUpdatesResponse:
    """Read incremental events for a polling client."""
    status = await task_buffer.get_task_status(task_id)
    if status is None or status["session_id"] != session_id:
        raise ValueError("task not found")
    raw_events, next_cursor = await task_buffer.get_events(task_id, cursor)
    events = [StreamEvent(**e) for e in raw_events]
    return TaskUpdatesResponse(
        events=events,
        status=status["status"],  # pyright: ignore[reportArgumentType]
        next_cursor=next_cursor,
    )


async def get_task_status(
    task_id: str,
    session_id: str,
) -> TaskStatusResponse:
    """Lightweight status check for a polling client."""
    status = await task_buffer.get_task_status(task_id)
    if status is None or status["session_id"] != session_id:
        raise ValueError("task not found")
    return TaskStatusResponse(
        status=status["status"],  # pyright: ignore[reportArgumentType]
        message_count=status["message_count"],
        interrupted=status["interrupted"],
    )
