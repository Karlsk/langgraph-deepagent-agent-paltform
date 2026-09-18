"""Redis-backed event buffer for async chat streaming tasks.

Stores SSE events in Redis Lists so a disconnected client can resume via
polling after a page refresh.  Task metadata lives in a Redis Hash.

Redis key layout::

    chat:events:{task_id}  → List<JSON StreamEvent>   (RPUSH / LRANGE)
    chat:task:{task_id}    → Hash {status, session_id, message_count,
                                  interrupted, created_at}

All functions are no-ops when the cache backend is unavailable so callers
never need to guard against Redis being down.
"""

from __future__ import annotations

import json
import time
from typing import Any, Optional

from app.core.cache import cache_service
from app.core.logging import logger

_EVENTS_PREFIX = "chat:events:"
_TASK_PREFIX = "chat:task:"
_DEFAULT_TTL = 300  # 5 minutes


def _events_key(task_id: str) -> str:
    return f"{_EVENTS_PREFIX}{task_id}"


def _task_key(task_id: str) -> str:
    return f"{_TASK_PREFIX}{task_id}"


async def create_task(task_id: str, session_id: str) -> bool:
    """Initialise a task entry in Redis.

    Returns ``False`` when the backend is unreachable so the caller can
    degrade gracefully (e.g. reject ``stream_async`` with 503).
    """
    try:
        await cache_service.hset(
            _task_key(task_id),
            {
                "status": "running",
                "session_id": session_id,
                "message_count": "0",
                "interrupted": "false",
                "created_at": str(time.time()),
            },
        )
        return True
    except Exception:  # noqa: BLE001
        logger.exception("task_buffer_create_failed", task_id=task_id)
        return False


async def push_event(task_id: str, event: dict[str, Any]) -> None:
    """Append one serialised event to the task's event list."""
    try:
        await cache_service.rpush(_events_key(task_id), json.dumps(event))
    except Exception:  # noqa: BLE001
        logger.exception("task_buffer_push_event_failed", task_id=task_id)


async def get_events(task_id: str, cursor: int) -> tuple[list[dict[str, Any]], int]:
    """Read events from *cursor* onward.

    Returns ``(events, next_cursor)`` where ``next_cursor`` is the index the
    caller should pass on the next poll.
    """
    try:
        raw = await cache_service.lrange(_events_key(task_id), cursor, -1)
    except Exception:  # noqa: BLE001
        logger.exception("task_buffer_get_events_failed", task_id=task_id)
        return [], cursor

    events: list[dict[str, Any]] = []
    for item in raw:
        try:
            events.append(json.loads(item))
        except (TypeError, ValueError):
            continue
    return events, cursor + len(raw)


async def set_task_status(
    task_id: str,
    *,
    status: Optional[str] = None,
    message_count: Optional[int] = None,
    interrupted: Optional[bool] = None,
) -> None:
    """Update one or more fields on the task metadata hash."""
    mapping: dict[str, str] = {}
    if status is not None:
        mapping["status"] = status
    if message_count is not None:
        mapping["message_count"] = str(message_count)
    if interrupted is not None:
        mapping["interrupted"] = str(interrupted).lower()
    if not mapping:
        return
    try:
        await cache_service.hset(_task_key(task_id), mapping)
    except Exception:  # noqa: BLE001
        logger.exception("task_buffer_set_status_failed", task_id=task_id)


async def get_task_status(task_id: str) -> Optional[dict[str, Any]]:
    """Read the full task metadata hash.  Returns ``None`` if the task doesn't exist."""
    try:
        raw = await cache_service.hgetall(_task_key(task_id))
    except Exception:  # noqa: BLE001
        logger.exception("task_buffer_get_status_failed", task_id=task_id)
        return None
    if not raw:
        return None
    return {
        "status": raw.get("status", "running"),
        "session_id": raw.get("session_id", ""),
        "message_count": int(raw.get("message_count", "0")),
        "interrupted": raw.get("interrupted", "false") == "true",
        "created_at": float(raw.get("created_at", "0")),
    }


async def finalize_task(task_id: str, ttl: int = _DEFAULT_TTL) -> None:
    """Set a TTL on both keys so they auto-expire after *ttl* seconds."""
    try:
        await cache_service.expire(_events_key(task_id), ttl)
        await cache_service.expire(_task_key(task_id), ttl)
    except Exception:  # noqa: BLE001
        logger.exception("task_buffer_finalize_failed", task_id=task_id)
