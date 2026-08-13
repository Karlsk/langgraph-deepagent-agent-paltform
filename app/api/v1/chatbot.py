"""Chatbot API endpoints for handling chat interactions.

This module provides endpoints for chat interactions, including regular chat,
streaming chat, message history management, and chat history clearing.
Every endpoint resolves the session's AgentApp runtime via ``get_runtime``
and talks to the unified ``AgentAppRuntime`` interface only.
"""

import json

from fastapi import (
    APIRouter,
    Depends,
    HTTPException,
    Request,
)
from fastapi.responses import StreamingResponse
from sqlmodel import Session as DBSession

from app.api.v1.auth import get_current_session
from app.core.config import settings
from app.core.limiter import limiter
from app.core.logging import logger
from app.core.metrics import llm_stream_duration_seconds
from app.models.session import Session
from app.schemas.chat import (
    ChatRequest,
    ChatResponse,
    StreamResponse,
)
from app.services.agents.runtime import get_runtime
from app.services.database import database_service
from app.services.session_naming import maybe_name_session

router = APIRouter()


def _resolve_stream_model(runtime_obj: object) -> str:
    """Pick the metric label for one stream run.

    Prefers the runtime's ``resolved_model_name`` — the real upstream model
    identifier of the bound LlmConfig row — over any reference name; falls
    back to the configured default model when unresolved.

    Args:
        runtime_obj: The resolved AgentAppRuntime instance.

    Returns:
        str: The model name used as the metric label.
    """
    resolved = getattr(runtime_obj, "resolved_model_name", None)
    return str(resolved) if resolved else settings.DEFAULT_LLM_MODEL


@router.post("/chat", response_model=ChatResponse)
@limiter.limit(settings.RATE_LIMIT_ENDPOINTS["chat"][0])
async def chat(
    request: Request,
    chat_request: ChatRequest,
    session: Session = Depends(get_current_session),
):
    """Process a chat request through the session's AgentApp runtime.

    Args:
        request: The FastAPI request object for rate limiting.
        chat_request: The chat request containing messages.
        session: The current session from the auth token.

    Returns:
        ChatResponse: The processed chat response.

    Raises:
        HTTPException: If there's an error processing the request.
    """
    try:
        logger.info(
            "chat_request_received",
            session_id=session.id,
            message_count=len(chat_request.messages),
        )

        if settings.SESSION_NAMING_ENABLED:
            maybe_name_session(session.id, session.name, chat_request.messages)

        with DBSession(database_service.engine) as db_session:
            runtime_obj = await get_runtime(db_session, session.agent_app_id)

        result = await runtime_obj.ainvoke(
            chat_request.messages, session_id=session.id, user_id=str(session.user_id), username=session.username
        )

        logger.info("chat_request_processed", session_id=session.id)

        return ChatResponse(messages=result)
    except Exception as e:
        logger.exception("chat_request_failed", session_id=session.id, error=str(e))
        raise HTTPException(status_code=500, detail=str(e))


@router.post("/chat/stream")
@limiter.limit(settings.RATE_LIMIT_ENDPOINTS["chat_stream"][0])
async def chat_stream(
    request: Request,
    chat_request: ChatRequest,
    session: Session = Depends(get_current_session),
):
    """Process a chat request through the session's AgentApp runtime with streaming.

    Args:
        request: The FastAPI request object for rate limiting.
        chat_request: The chat request containing messages.
        session: The current session from the auth token.

    Returns:
        StreamingResponse: A streaming response of the chat completion.

    Raises:
        HTTPException: If there's an error processing the request.
    """
    try:
        logger.info(
            "stream_chat_request_received",
            session_id=session.id,
            message_count=len(chat_request.messages),
        )

        if settings.SESSION_NAMING_ENABLED:
            maybe_name_session(session.id, session.name, chat_request.messages)

        with DBSession(database_service.engine) as db_session:
            runtime_obj = await get_runtime(db_session, session.agent_app_id)

        async def event_generator():
            """Generate streaming events.

            Yields:
                str: Server-sent events in JSON format.

            Raises:
                Exception: If there's an error during streaming.
            """
            try:
                with llm_stream_duration_seconds.labels(model=_resolve_stream_model(runtime_obj)).time():
                    async for chunk in runtime_obj.astream(
                        chat_request.messages,
                        session_id=session.id,
                        user_id=str(session.user_id),
                        username=session.username,
                    ):
                        response = StreamResponse(content=chunk.content, source=chunk.source, done=False)
                        yield f"data: {json.dumps(response.model_dump(mode='json'))}\n\n"

                # Send final message indicating completion
                final_response = StreamResponse(content="", done=True)
                yield f"data: {json.dumps(final_response.model_dump(mode='json'))}\n\n"

            except Exception as e:
                logger.exception(
                    "stream_chat_request_failed",
                    session_id=session.id,
                    error=str(e),
                )
                error_response = StreamResponse(content=str(e), done=True)
                yield f"data: {json.dumps(error_response.model_dump(mode='json'))}\n\n"

        return StreamingResponse(event_generator(), media_type="text/event-stream")

    except Exception as e:
        logger.exception(
            "stream_chat_request_failed",
            session_id=session.id,
            error=str(e),
        )
        raise HTTPException(status_code=500, detail=str(e))


@router.get("/messages", response_model=ChatResponse)
@limiter.limit(settings.RATE_LIMIT_ENDPOINTS["messages"][0])
async def get_session_messages(
    request: Request,
    session: Session = Depends(get_current_session),
):
    """Get all messages for a session.

    Args:
        request: The FastAPI request object for rate limiting.
        session: The current session from the auth token.

    Returns:
        ChatResponse: All messages in the session.

    Raises:
        HTTPException: If there's an error retrieving the messages.
    """
    try:
        with DBSession(database_service.engine) as db_session:
            runtime_obj = await get_runtime(db_session, session.agent_app_id)
        messages = await runtime_obj.get_chat_history(session.id)
        return ChatResponse(messages=messages)
    except Exception as e:
        logger.exception("get_messages_failed", session_id=session.id, error=str(e))
        raise HTTPException(status_code=500, detail=str(e))


@router.delete("/messages")
@limiter.limit(settings.RATE_LIMIT_ENDPOINTS["messages"][0])
async def clear_chat_history(
    request: Request,
    session: Session = Depends(get_current_session),
):
    """Clear all messages for a session.

    Args:
        request: The FastAPI request object for rate limiting.
        session: The current session from the auth token.

    Returns:
        dict: A message indicating the chat history was cleared.
    """
    try:
        with DBSession(database_service.engine) as db_session:
            runtime_obj = await get_runtime(db_session, session.agent_app_id)
        await runtime_obj.clear_chat_history(session.id)
        return {"message": "Chat history cleared successfully"}
    except Exception as e:
        logger.exception("clear_chat_history_failed", session_id=session.id, error=str(e))
        raise HTTPException(status_code=500, detail=str(e))
