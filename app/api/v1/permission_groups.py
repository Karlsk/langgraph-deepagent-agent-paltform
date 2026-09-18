"""Admin API for permission group assets (CRUD).

Phase-1 scope: all assets are globally shared (no per-user ownership checks);
every endpoint only authenticates via ``get_current_user`` and records the
creator in the audit-only ``created_by`` field.

Error semantics: missing resources return 404; name collisions return 422;
unexpected failures return 500 after ``logger.exception``.
"""

from typing import Any

from fastapi import APIRouter, Depends, HTTPException, Query, Request
from sqlmodel import Session as DBSession
from sqlmodel import col, or_, select

from app.api.v1.agent_assets_common import (
    _read_patch_body,
    _validate_payload,
    get_db_session,
)
from app.api.v1.auth import get_current_user
from app.core.config import settings
from app.core.limiter import limiter
from app.core.logging import logger
from app.models.agent_assets import PermissionGroup
from app.models.user import User
from app.schemas.agent_apps import (
    PermissionGroupCreate,
    PermissionGroupRead,
    PermissionGroupUpdate,
)
from app.schemas.base import ApiResponse, PageResult

router = APIRouter()


def _get_group_or_404(db: DBSession, group_id: int) -> PermissionGroup:
    """Fetch one permission group row or raise 404."""
    group = db.get(PermissionGroup, group_id)
    if group is None:
        raise HTTPException(status_code=404, detail=f"permission group '{group_id}' not found")
    return group


@router.get("/permission-groups", response_model=ApiResponse[list[PermissionGroupRead]])
@limiter.limit(settings.RATE_LIMIT_ENDPOINTS["agent_app"][0])
async def list_permission_groups(
    request: Request,
    db: DBSession = Depends(get_db_session),
    user: User = Depends(get_current_user),
) -> ApiResponse[Any]:
    """List every stored permission group.

    Args:
        request: The FastAPI request object for rate limiting.
        db: Request-scoped DB session.
        user: Authenticated user resolved from the user access token.

    Returns:
        Envelope carrying all permission group rows ordered by name.
    """
    try:
        return ApiResponse.success(list(db.exec(select(PermissionGroup).order_by(col(PermissionGroup.name))).all()))
    except Exception as exc:
        logger.exception("permission_group_list_failed")
        raise HTTPException(status_code=500, detail=str(exc)) from exc


@router.post("/permission-groups", response_model=ApiResponse[PermissionGroupRead], status_code=201)
@limiter.limit(settings.RATE_LIMIT_ENDPOINTS["agent_app"][0])
async def create_permission_group(
    request: Request,
    payload: PermissionGroupCreate,
    db: DBSession = Depends(get_db_session),
    user: User = Depends(get_current_user),
) -> ApiResponse[Any]:
    """Create a named permission group.

    Args:
        request: The FastAPI request object for rate limiting.
        payload: The permission group definition.
        db: Request-scoped DB session.
        user: Authenticated user used for audit attribution.

    Returns:
        Envelope carrying the persisted permission group row.

    Raises:
        HTTPException: 422 when the name is already taken.
    """
    try:
        existing = db.exec(
            select(PermissionGroup).where(col(PermissionGroup.name) == payload.name)
        ).first()
        if existing is not None:
            logger.warning("permission_group_create_conflict", name=payload.name)
            raise HTTPException(status_code=422, detail=f"permission group '{payload.name}' already exists")

        group = PermissionGroup(
            name=payload.name,
            tool_names=payload.tool_names,
            description=payload.description,
            created_by=user.username or str(user.id),
        )
        db.add(group)
        db.commit()
        db.refresh(group)
        logger.info("permission_group_created", group_id=group.id, name=payload.name)
        return ApiResponse.success(group, code=201)
    except HTTPException:
        raise
    except Exception as exc:
        logger.exception("permission_group_create_failed", name=payload.name)
        raise HTTPException(status_code=500, detail=str(exc)) from exc


@router.get("/permission-groups/page", response_model=ApiResponse[PageResult[PermissionGroupRead]])
@limiter.limit(settings.RATE_LIMIT_ENDPOINTS["agent_app"][0])
async def list_permission_groups_page(
    request: Request,
    page: int = Query(1, ge=1),
    page_size: int = Query(10, ge=1, le=100, alias="pageSize"),
    keyword: str | None = Query(None, max_length=200),
    db: DBSession = Depends(get_db_session),
    user: User = Depends(get_current_user),
) -> ApiResponse[Any]:
    """List permission groups with server-side pagination.

    Args:
        request: The FastAPI request object for rate limiting.
        page: 1-based page number.
        page_size: Rows per page (exposed as ``pageSize``).
        keyword: Optional case-insensitive substring matched against name or description.
        db: Request-scoped DB session.
        user: Authenticated user resolved from the user access token.

    Returns:
        Envelope carrying a PageResult of permission group rows ordered by name.
    """
    try:
        stmt = select(PermissionGroup)
        count_stmt = select(PermissionGroup)
        if keyword:
            like = f"%{keyword}%"
            predicate = or_(
                col(PermissionGroup.name).ilike(like),
                col(PermissionGroup.description).ilike(like),
            )
            stmt = stmt.where(predicate)
            count_stmt = count_stmt.where(predicate)

        total = len(db.exec(count_stmt).all())
        rows = db.exec(stmt.order_by(col(PermissionGroup.name)).offset((page - 1) * page_size).limit(page_size)).all()

        return ApiResponse.success(
            PageResult(items=list(rows), total=total, page=page, page_size=page_size)
        )
    except Exception as exc:
        logger.exception("permission_group_list_page_failed")
        raise HTTPException(status_code=500, detail=str(exc)) from exc


@router.get("/permission-groups/{group_id}", response_model=ApiResponse[PermissionGroupRead])
@limiter.limit(settings.RATE_LIMIT_ENDPOINTS["agent_app"][0])
async def get_permission_group(
    request: Request,
    group_id: int,
    db: DBSession = Depends(get_db_session),
    user: User = Depends(get_current_user),
) -> ApiResponse[Any]:
    """Fetch one permission group by id.

    Args:
        request: The FastAPI request object for rate limiting.
        group_id: Permission group primary key.
        db: Request-scoped DB session.
        user: Authenticated user resolved from the user access token.

    Returns:
        Envelope carrying the matching permission group row.

    Raises:
        HTTPException: 404 when the permission group does not exist.
    """
    try:
        return ApiResponse.success(_get_group_or_404(db, group_id))
    except HTTPException:
        raise
    except Exception as exc:
        logger.exception("permission_group_read_failed", group_id=group_id)
        raise HTTPException(status_code=500, detail=str(exc)) from exc


@router.patch("/permission-groups/{group_id}", response_model=ApiResponse[PermissionGroupRead])
@limiter.limit(settings.RATE_LIMIT_ENDPOINTS["agent_app"][0])
async def update_permission_group(
    request: Request,
    group_id: int,
    db: DBSession = Depends(get_db_session),
    user: User = Depends(get_current_user),
) -> ApiResponse[Any]:
    """Partially update a permission group (name is immutable).

    Args:
        request: The FastAPI request object for rate limiting.
        group_id: Permission group primary key.
        db: Request-scoped DB session.
        user: Authenticated user resolved from the user access token.

    Returns:
        Envelope carrying the updated permission group row.

    Raises:
        HTTPException: 404 when missing, 422 on empty payload.
    """
    body = await _read_patch_body(request)
    payload = _validate_payload(PermissionGroupUpdate, body)
    try:
        group = db.get(PermissionGroup, group_id)
        if group is None:
            raise HTTPException(status_code=404, detail=f"permission group '{group_id}' not found")

        updates = payload.model_dump(exclude_unset=True)
        if not updates:
            raise HTTPException(status_code=422, detail="nothing to update")

        for field, value in updates.items():
            setattr(group, field, value)

        db.add(group)
        db.commit()
        db.refresh(group)
        logger.info("permission_group_updated", group_id=group_id)
        return ApiResponse.success(group)
    except HTTPException:
        raise
    except Exception as exc:
        logger.exception("permission_group_update_failed", group_id=group_id)
        raise HTTPException(status_code=500, detail=str(exc)) from exc


@router.delete("/permission-groups/{group_id}", response_model=ApiResponse[None])
@limiter.limit(settings.RATE_LIMIT_ENDPOINTS["agent_app"][0])
async def delete_permission_group(
    request: Request,
    group_id: int,
    db: DBSession = Depends(get_db_session),
    user: User = Depends(get_current_user),
) -> ApiResponse[None]:
    """Delete a permission group.

    Args:
        request: The FastAPI request object for rate limiting.
        group_id: Permission group primary key.
        db: Request-scoped DB session.
        user: Authenticated user resolved from the user access token.

    Returns:
        Envelope with null data on successful deletion.

    Raises:
        HTTPException: 404 when the permission group does not exist.
    """
    try:
        group = _get_group_or_404(db, group_id)
        db.delete(group)
        db.commit()
        logger.info("permission_group_deleted", group_id=group_id)
        return ApiResponse.success(None)
    except HTTPException:
        raise
    except Exception as exc:
        logger.exception("permission_group_delete_failed", group_id=group_id)
        raise HTTPException(status_code=500, detail=str(exc)) from exc
