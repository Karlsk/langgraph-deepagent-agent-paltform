"""Admin API for sub-agent assets (CRUD + one-shot test).

Phase-1 scope: all assets are globally shared (no per-user ownership checks);
every endpoint only authenticates via ``get_current_session`` and records the
creator in the audit-only ``created_by`` field.

Error semantics: missing resources return 404; name collisions and validation
failures return 422; unexpected failures return 500 after ``logger.exception``.
"""

from typing import Any

from fastapi import APIRouter, Depends, HTTPException, Request
from sqlmodel import Session as DBSession
from sqlmodel import col, select

from app.api.v1.agent_assets_common import (
    _canonical_sha256,
    _creator,
    _read_patch_body,
    _validate_payload,
    get_db_session,
)
from app.api.v1.auth import get_current_session
from app.core.config import settings
from app.core.limiter import limiter
from app.core.logging import logger
from app.models.agent_assets import SubAgentConfig
from app.models.session import Session as ChatSession
from app.schemas.agent_apps import (
    SubAgentCreate,
    SubAgentRead,
    SubAgentTestRequest,
    SubAgentTestResult,
    SubAgentUpdate,
)
from app.schemas.base import ApiResponse
from app.services.agents.test_runner import run_subagent_once

router = APIRouter()


# ---------------------------------------------------------------------------
# Shared helpers
# ---------------------------------------------------------------------------


def _subagent_content_hash(cfg: SubAgentConfig) -> str:
    """Compute the content hash of the effective sub-agent configuration."""
    return _canonical_sha256(
        {
            "description": cfg.description,
            "when_to_use": cfg.when_to_use,
            "system_prompt": cfg.system_prompt,
            "allowed_tools": cfg.allowed_tools,
            "model": cfg.model,
            "max_turns": cfg.max_turns,
        }
    )


# ---------------------------------------------------------------------------
# Sub-agents
# ---------------------------------------------------------------------------


@router.get("/subagents", response_model=ApiResponse[list[SubAgentRead]])
@limiter.limit(settings.RATE_LIMIT_ENDPOINTS["subagent"][0])
async def list_subagents(
    request: Request,
    db: DBSession = Depends(get_db_session),
    current_session: ChatSession = Depends(get_current_session),
) -> ApiResponse[Any]:
    """List every stored sub-agent configuration.

    Args:
        request: The FastAPI request object for rate limiting.
        db: Request-scoped DB session.
        current_session: Authenticated chat session.

    Returns:
        Envelope carrying all sub-agent rows ordered by name.
    """
    try:
        return ApiResponse.success(list(db.exec(select(SubAgentConfig).order_by(col(SubAgentConfig.name))).all()))
    except Exception as exc:
        logger.exception("subagent_list_failed")
        raise HTTPException(status_code=500, detail=str(exc)) from exc


@router.post("/subagents", response_model=ApiResponse[SubAgentRead], status_code=201)
@limiter.limit(settings.RATE_LIMIT_ENDPOINTS["subagent"][0])
async def create_subagent(
    request: Request,
    payload: SubAgentCreate,
    db: DBSession = Depends(get_db_session),
    current_session: ChatSession = Depends(get_current_session),
) -> ApiResponse[Any]:
    """Create a reusable sub-agent configuration.

    Args:
        request: The FastAPI request object for rate limiting.
        payload: The sub-agent definition.
        db: Request-scoped DB session.
        current_session: Authenticated chat session (audit only).

    Returns:
        Envelope carrying the persisted sub-agent row.

    Raises:
        HTTPException: 422 when the name is already taken.
    """
    try:
        if db.get(SubAgentConfig, payload.name) is not None:
            logger.warning("subagent_create_conflict", name=payload.name)
            raise HTTPException(status_code=422, detail=f"subagent '{payload.name}' already exists")

        subagent = SubAgentConfig(
            name=payload.name,
            description=payload.description,
            when_to_use=payload.when_to_use,
            system_prompt=payload.system_prompt,
            allowed_tools=payload.allowed_tools,
            model=payload.model,
            max_turns=payload.max_turns,
            content_hash="",
            created_by=_creator(current_session),
        )
        subagent.content_hash = _subagent_content_hash(subagent)
        db.add(subagent)
        db.commit()
        db.refresh(subagent)
        logger.info("subagent_created", name=payload.name, created_by=subagent.created_by)
        return ApiResponse.success(subagent, code=201)
    except HTTPException:
        raise
    except Exception as exc:
        logger.exception("subagent_create_failed", name=payload.name)
        raise HTTPException(status_code=500, detail=str(exc)) from exc


@router.get("/subagents/{name}", response_model=ApiResponse[SubAgentRead])
@limiter.limit(settings.RATE_LIMIT_ENDPOINTS["subagent"][0])
async def get_subagent(
    request: Request,
    name: str,
    db: DBSession = Depends(get_db_session),
    current_session: ChatSession = Depends(get_current_session),
) -> ApiResponse[Any]:
    """Fetch one sub-agent configuration by name.

    Args:
        request: The FastAPI request object for rate limiting.
        name: Sub-agent primary key.
        db: Request-scoped DB session.
        current_session: Authenticated chat session.

    Returns:
        Envelope carrying the matching sub-agent row.

    Raises:
        HTTPException: 404 when the sub-agent does not exist.
    """
    try:
        subagent = db.get(SubAgentConfig, name)
        if subagent is None:
            raise HTTPException(status_code=404, detail=f"subagent '{name}' not found")
        return ApiResponse.success(subagent)
    except HTTPException:
        raise
    except Exception as exc:
        logger.exception("subagent_read_failed", name=name)
        raise HTTPException(status_code=500, detail=str(exc)) from exc


@router.patch("/subagents/{name}", response_model=ApiResponse[SubAgentRead])
@limiter.limit(settings.RATE_LIMIT_ENDPOINTS["subagent"][0])
async def update_subagent(
    request: Request,
    name: str,
    db: DBSession = Depends(get_db_session),
    current_session: ChatSession = Depends(get_current_session),
) -> ApiResponse[Any]:
    """Partially update a sub-agent (name is immutable).

    Args:
        request: The FastAPI request object for rate limiting.
        name: Sub-agent primary key.
        db: Request-scoped DB session.
        current_session: Authenticated chat session.

    Returns:
        Envelope carrying the updated sub-agent row (refreshed hash, bumped version).

    Raises:
        HTTPException: 404 when missing, 422 on name change or empty payload.
    """
    body = await _read_patch_body(request)
    payload = _validate_payload(SubAgentUpdate, body)
    try:
        subagent = db.get(SubAgentConfig, name)
        if subagent is None:
            raise HTTPException(status_code=404, detail=f"subagent '{name}' not found")

        updates = payload.model_dump(exclude_unset=True)
        if not updates:
            raise HTTPException(status_code=422, detail="nothing to update")

        for field, value in updates.items():
            setattr(subagent, field, value)
        subagent.content_hash = _subagent_content_hash(subagent)
        subagent.version += 1
        db.add(subagent)
        db.commit()
        db.refresh(subagent)
        logger.info("subagent_updated", name=name, version=subagent.version)
        return ApiResponse.success(subagent)
    except HTTPException:
        raise
    except Exception as exc:
        logger.exception("subagent_update_failed", name=name)
        raise HTTPException(status_code=500, detail=str(exc)) from exc


@router.delete("/subagents/{name}", response_model=ApiResponse[None])
@limiter.limit(settings.RATE_LIMIT_ENDPOINTS["subagent"][0])
async def delete_subagent(
    request: Request,
    name: str,
    db: DBSession = Depends(get_db_session),
    current_session: ChatSession = Depends(get_current_session),
) -> ApiResponse[None]:
    """Delete a sub-agent configuration.

    Args:
        request: The FastAPI request object for rate limiting.
        name: Sub-agent primary key.
        db: Request-scoped DB session.
        current_session: Authenticated chat session.

    Returns:
        Envelope with a null data payload.

    Raises:
        HTTPException: 404 when the sub-agent does not exist.
    """
    try:
        subagent = db.get(SubAgentConfig, name)
        if subagent is None:
            raise HTTPException(status_code=404, detail=f"subagent '{name}' not found")
        db.delete(subagent)
        db.commit()
        logger.info("subagent_deleted", name=name)
        return ApiResponse.success(None)
    except HTTPException:
        raise
    except Exception as exc:
        logger.exception("subagent_delete_failed", name=name)
        raise HTTPException(status_code=500, detail=str(exc)) from exc


@router.post("/subagents/{name}/test", response_model=ApiResponse[SubAgentTestResult])
@limiter.limit(settings.RATE_LIMIT_ENDPOINTS["subagent_test"][0])
async def test_subagent(
    request: Request,
    name: str,
    payload: SubAgentTestRequest,
    db: DBSession = Depends(get_db_session),
    current_session: ChatSession = Depends(get_current_session),
) -> ApiResponse[SubAgentTestResult]:
    """Run one isolated one-shot test of a sub-agent configuration.

    Args:
        request: The FastAPI request object for rate limiting.
        name: Sub-agent primary key.
        payload: The test prompt.
        db: Request-scoped DB session.
        current_session: Authenticated chat session.

    Returns:
        Envelope carrying the test run result (final message, turns, duration, model).

    Raises:
        HTTPException: 404 when the sub-agent does not exist, 500 on run failure.
    """
    try:
        if db.get(SubAgentConfig, name) is None:
            raise HTTPException(status_code=404, detail=f"subagent '{name}' not found")
        logger.info("subagent_test_requested", name=name, user_id=current_session.user_id)
        return ApiResponse.success(await run_subagent_once(session=db, name=name, prompt=payload.prompt))
    except HTTPException:
        raise
    except ValueError as exc:
        raise HTTPException(status_code=404, detail=str(exc)) from exc
    except Exception as exc:
        logger.exception("subagent_test_failed", name=name)
        raise HTTPException(status_code=500, detail=str(exc)) from exc
