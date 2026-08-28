"""Session auto-naming for the G4 chat layer (spec-g4-chat §8).

Two-level strategy on the first user turn of an unnamed session:

1. Immediate placeholder — whitespace-collapsed 20-char truncation with a
   ``新会话`` fallback — claimed atomically via ``claim_session_name``
   (UPDATE ... WHERE name = '') so concurrent callers never duplicate work.
2. Graceful LLM overwrite — a fire-and-forget task asks the app-resolved
   model (falling back to the default model on failure) for a structured
   title and renames the row; any failure keeps the placeholder, logs and
   bumps the error metric (§8.1/§8.3).
"""

from __future__ import annotations

import asyncio

from langchain_core.messages import HumanMessage, SystemMessage
from sqlmodel import Session as DBSession

from app.core.config import settings
from app.core.logging import logger
from app.core.metrics import session_names_generated_total
from app.core.prompts import SESSION_TITLE_PROMPT
from app.schemas.chat import Message, SessionTitle
from app.services.agents import sessions_service
from app.services.llm import llm_service

_PLACEHOLDER_MAX = 20
_FALLBACK_NAME = "新会话"

_background_tasks: set[asyncio.Task] = set()


def _build_placeholder(user_message: str) -> str:
    """Collapse whitespace, truncate to 20 chars, fall back to 新会话 (§8.1)."""
    cleaned = " ".join(user_message.split())
    return cleaned[:_PLACEHOLDER_MAX].rstrip() or _FALLBACK_NAME


def _first_user_message(messages: list[Message]) -> str | None:
    """Content of the first user turn, if any."""
    for message in messages:
        if message.role == "user":
            return message.content
    return None


async def _call_title_model(user_message: str, model_name: str | None) -> SessionTitle:
    """Ask the naming model for a short structured title (§8.2 prompt)."""
    return await llm_service.call(
        [
            SystemMessage(content=SESSION_TITLE_PROMPT),
            HumanMessage(content=user_message[:500]),
        ],
        model_name=model_name,
        response_format=SessionTitle,
        max_tokens=32,
        temperature=0.3,
    )


async def _persist_session_name(session_id: str, user_message: str, model_name: str | None) -> None:
    """LLM naming with app-model -> default-model fallback; never raises (§8.3)."""
    try:
        try:
            result = await _call_title_model(user_message, model_name)
        except Exception:
            if model_name is None:
                raise
            logger.warning("session_name_app_model_failed", session_id=session_id, model_name=model_name)
            result = await _call_title_model(user_message, None)
        title = result.title.strip()
        if not title:
            raise ValueError("empty title from naming model")
        from app.services.database import database_service

        with DBSession(database_service.engine) as db:
            await sessions_service.rename_session(db, session_id, title)
        session_names_generated_total.labels(status="success").inc()
        logger.info("session_name_generated", session_id=session_id, name=title)
    except Exception:  # noqa: BLE001 — fire-and-forget: log + metric, never raise (§8.3)
        session_names_generated_total.labels(status="error").inc()
        logger.exception("session_name_generation_failed", session_id=session_id)


async def maybe_name_session(
    db: DBSession,
    session_id: str,
    session_name: str,
    messages: list[Message],
    *,
    model_name: str | None = None,
) -> bool:
    """Claim + schedule auto-naming for an unnamed session (§8.1).

    Returns True iff this caller won the placeholder claim. Already-named
    sessions are skipped without any write or LLM call. The LLM overwrite
    only runs when ``SESSION_NAMING_ENABLED``; its background task never
    raises and never blocks the chat response.
    """
    if session_name:
        return False
    user_message = _first_user_message(messages)
    if user_message is None:
        return False
    placeholder = _build_placeholder(user_message)
    claimed = await sessions_service.claim_session_name(db, session_id, placeholder)
    if not claimed:
        return False
    if settings.SESSION_NAMING_ENABLED:
        task = asyncio.create_task(_persist_session_name(session_id, user_message, model_name))
        _background_tasks.add(task)
        task.add_done_callback(_background_tasks.discard)
    return True
