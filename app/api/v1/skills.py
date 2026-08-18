"""Admin API for global skill assets (CRUD + LLM draft generation).

Phase-1 scope: all assets are globally shared (no per-user ownership checks);
every endpoint only authenticates via ``get_current_session`` and records the
creator in the audit-only ``created_by`` field.

Error semantics: missing resources return 404; name collisions and validation
failures return 422; unexpected failures return 500 after ``logger.exception``.
"""

from typing import Any

from fastapi import APIRouter, Depends, HTTPException, Query, Request
from sqlmodel import Session as DBSession

from app.api.v1.agent_assets_common import (
    _creator,
    _read_patch_body,
    _validate_payload,
    get_db_session,
)
from app.api.v1.auth import get_current_session
from app.core.config import settings
from app.core.limiter import limiter
from app.core.logging import logger
from app.models.agent_assets import SkillAsset
from app.models.session import Session as ChatSession
from app.schemas.agent_apps import (
    SkillContentRead,
    SkillCreate,
    SkillGenerateRequest,
    SkillGenerateResponse,
    SkillRead,
    SkillUpdate,
)
from app.schemas.base import ApiResponse, PageResult
from app.services.agents import skills_store

router = APIRouter()


# ---------------------------------------------------------------------------
# Skills
# ---------------------------------------------------------------------------


@router.get("/skills", response_model=ApiResponse[list[SkillRead]])
@limiter.limit(settings.RATE_LIMIT_ENDPOINTS["skill"][0])
async def list_skills(
    request: Request,
    db: DBSession = Depends(get_db_session),
    current_session: ChatSession = Depends(get_current_session),
) -> ApiResponse[Any]:
    """List metadata of every global skill.

    Args:
        request: The FastAPI request object for rate limiting.
        db: Request-scoped DB session.
        current_session: Authenticated chat session.

    Returns:
        Envelope carrying skill metadata rows
        (name/description/content_hash/version/created_by).
    """
    try:
        return ApiResponse.success(await skills_store.list_global(db))
    except Exception as exc:
        logger.exception("skill_list_failed")
        raise HTTPException(status_code=500, detail=str(exc)) from exc


@router.get("/skills/page", response_model=ApiResponse[PageResult[SkillRead]])
@limiter.limit(settings.RATE_LIMIT_ENDPOINTS["skill"][0])
async def list_skills_page(
    request: Request,
    page: int = Query(1, ge=1),
    page_size: int = Query(10, ge=1, le=100, alias="pageSize"),
    keyword: str | None = Query(None, max_length=200),
    db: DBSession = Depends(get_db_session),
    current_session: ChatSession = Depends(get_current_session),
) -> ApiResponse[Any]:
    """List metadata of global skills with server-side pagination.

    Args:
        request: The FastAPI request object for rate limiting.
        page: 1-based page number.
        page_size: Rows per page (exposed as ``pageSize``).
        keyword: Optional case-insensitive substring matched against name.
        db: Request-scoped DB session.
        current_session: Authenticated chat session.

    Returns:
        Envelope carrying a PageResult of skill metadata rows
        (name/description/content_hash/version/created_by).
    """
    try:
        return ApiResponse.success(
            await skills_store.list_global_page(db, page=page, page_size=page_size, keyword=keyword)
        )
    except Exception as exc:
        logger.exception("skill_list_page_failed")
        raise HTTPException(status_code=500, detail=str(exc)) from exc


@router.post("/skills", response_model=ApiResponse[SkillRead], status_code=201)
@limiter.limit(settings.RATE_LIMIT_ENDPOINTS["skill"][0])
async def create_skill(
    request: Request,
    payload: SkillCreate,
    db: DBSession = Depends(get_db_session),
    current_session: ChatSession = Depends(get_current_session),
) -> ApiResponse[Any]:
    """Create a global skill from direct input (atomic file write + DB row).

    Args:
        request: The FastAPI request object for rate limiting.
        payload: The skill definition (name, description, SKILL.md body).
        db: Request-scoped DB session.
        current_session: Authenticated chat session (audit only).

    Returns:
        Envelope carrying the persisted skill asset row.

    Raises:
        HTTPException: 422 when the name is invalid or already taken.
    """
    try:
        created = await skills_store.create_global(
            db,
            name=payload.name,
            description=payload.description,
            body=payload.body,
            created_by=_creator(current_session),
        )
        return ApiResponse.success(created, code=201)
    except ValueError as exc:
        logger.warning("skill_create_rejected", name=payload.name, error=str(exc))
        raise HTTPException(status_code=422, detail=str(exc)) from exc
    except Exception as exc:
        logger.exception("skill_create_failed", name=payload.name)
        raise HTTPException(status_code=500, detail=str(exc)) from exc


@router.post("/skills/generate", response_model=ApiResponse[SkillGenerateResponse])
@limiter.limit(settings.RATE_LIMIT_ENDPOINTS["skill_generate"][0])
async def generate_skill(
    request: Request,
    payload: SkillGenerateRequest,
    current_session: ChatSession = Depends(get_current_session),
) -> ApiResponse[SkillGenerateResponse]:
    """Generate a SKILL.md draft via the LLM (draft only, nothing persisted).

    Args:
        request: The FastAPI request object for rate limiting.
        payload: Draft generation guidance.
        current_session: Authenticated chat session.

    Returns:
        Envelope carrying the generated draft content.

    Raises:
        HTTPException: 500 when the LLM fails after retries.
    """
    try:
        logger.info("skill_generate_requested", user_id=current_session.user_id)
        draft = await skills_store.generate_skill_draft(description=payload.description, hint=payload.hint)
        return ApiResponse.success(SkillGenerateResponse(draft=draft))
    except Exception as exc:
        logger.exception("skill_generate_failed")
        raise HTTPException(status_code=500, detail=str(exc)) from exc


@router.get("/skills/{name}", response_model=ApiResponse[SkillRead])
@limiter.limit(settings.RATE_LIMIT_ENDPOINTS["skill"][0])
async def get_skill(
    request: Request,
    name: str,
    db: DBSession = Depends(get_db_session),
    current_session: ChatSession = Depends(get_current_session),
) -> ApiResponse[Any]:
    """Fetch one skill asset's metadata by name.

    Args:
        request: The FastAPI request object for rate limiting.
        name: Skill primary key.
        db: Request-scoped DB session.
        current_session: Authenticated chat session.

    Returns:
        Envelope carrying the matching skill asset row.

    Raises:
        HTTPException: 404 when the skill does not exist.
    """
    try:
        asset = db.get(SkillAsset, name)
        if asset is None:
            raise HTTPException(status_code=404, detail=f"skill '{name}' not found")
        return ApiResponse.success(asset)
    except HTTPException:
        raise
    except Exception as exc:
        logger.exception("skill_read_failed", name=name)
        raise HTTPException(status_code=500, detail=str(exc)) from exc


@router.get("/skills/{name}/content", response_model=ApiResponse[SkillContentRead])
@limiter.limit(settings.RATE_LIMIT_ENDPOINTS["skill"][0])
async def get_skill_content(
    request: Request,
    name: str,
    current_session: ChatSession = Depends(get_current_session),
) -> ApiResponse[Any]:
    """Fetch the raw SKILL.md body of a global skill by name.

    Args:
        request: The FastAPI request object for rate limiting.
        name: Skill primary key.
        current_session: Authenticated chat session.

    Returns:
        Envelope carrying the skill name and its full SKILL.md content.

    Raises:
        HTTPException: 404 when the skill does not exist.
    """
    try:
        content = await skills_store.read_global(name)
    except ValueError as exc:
        raise HTTPException(status_code=404, detail=f"skill '{name}' not found") from exc
    except Exception as exc:
        logger.exception("skill_content_read_failed", name=name)
        raise HTTPException(status_code=500, detail=str(exc)) from exc
    return ApiResponse.success({"name": name, "content": content})


@router.patch("/skills/{name}", response_model=ApiResponse[SkillRead])
@limiter.limit(settings.RATE_LIMIT_ENDPOINTS["skill"][0])
async def update_skill(
    request: Request,
    name: str,
    db: DBSession = Depends(get_db_session),
    current_session: ChatSession = Depends(get_current_session),
) -> ApiResponse[Any]:
    """Partially update a skill (name is immutable).

    Args:
        request: The FastAPI request object for rate limiting.
        name: Skill primary key.
        db: Request-scoped DB session.
        current_session: Authenticated chat session.

    Returns:
        Envelope carrying the updated skill asset row with refreshed hash
        and bumped version.

    Raises:
        HTTPException: 404 when missing, 422 on name change or empty payload.
    """
    body = await _read_patch_body(request)
    payload = _validate_payload(SkillUpdate, body)
    try:
        if db.get(SkillAsset, name) is None:
            raise HTTPException(status_code=404, detail=f"skill '{name}' not found")
        if payload.description is None and payload.body is None:
            raise HTTPException(status_code=422, detail="nothing to update: provide description and/or body")

        updated = await skills_store.update_global(db, name=name, description=payload.description, body=payload.body)
        return ApiResponse.success(updated)
    except HTTPException:
        raise
    except ValueError as exc:
        logger.warning("skill_update_rejected", name=name, error=str(exc))
        raise HTTPException(status_code=422, detail=str(exc)) from exc
    except Exception as exc:
        logger.exception("skill_update_failed", name=name)
        raise HTTPException(status_code=500, detail=str(exc)) from exc


@router.delete("/skills/{name}", response_model=ApiResponse[None])
@limiter.limit(settings.RATE_LIMIT_ENDPOINTS["skill"][0])
async def delete_skill(
    request: Request,
    name: str,
    db: DBSession = Depends(get_db_session),
    current_session: ChatSession = Depends(get_current_session),
) -> ApiResponse[None]:
    """Delete a global skill (cascades to per-user copies).

    Args:
        request: The FastAPI request object for rate limiting.
        name: Skill primary key.
        db: Request-scoped DB session.
        current_session: Authenticated chat session.

    Returns:
        Envelope with null data on successful deletion.

    Raises:
        HTTPException: 404 when the skill does not exist.
    """
    try:
        if db.get(SkillAsset, name) is None:
            raise HTTPException(status_code=404, detail=f"skill '{name}' not found")
        await skills_store.delete_global(db, name=name)
        logger.info("skill_deleted", name=name)
        return ApiResponse.success(None)
    except HTTPException:
        raise
    except ValueError as exc:
        raise HTTPException(status_code=422, detail=str(exc)) from exc
    except Exception as exc:
        logger.exception("skill_delete_failed", name=name)
        raise HTTPException(status_code=500, detail=str(exc)) from exc
