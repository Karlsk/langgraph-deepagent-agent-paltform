"""Admin API for global skill assets (CRUD + LLM draft generation).

Phase-1 scope: all assets are globally shared (no per-user ownership checks);
every endpoint only authenticates via ``get_current_user`` and records the
creator in the audit-only ``created_by`` field.

Error semantics: missing resources return 404; name collisions and validation
failures return 422; unexpected failures return 500 after ``logger.exception``.
"""

from typing import Any, cast

from fastapi import APIRouter, Depends, HTTPException, Query, Request
from sqlmodel import Session as DBSession

from app.api.v1.agent_assets_common import (
    _read_patch_body,
    _skill_owners,
    _validate_payload,
    get_db_session,
)
from app.api.v1.auth import get_current_user
from app.core.config import settings
from app.core.limiter import limiter
from app.core.logging import logger
from app.models.agent_assets import SkillAsset
from app.models.user import User
from app.schemas.agent_apps import (
    SkillContentRead,
    SkillCreate,
    SkillGenerateRequest,
    SkillGenerateResponse,
    SkillRead,
    SkillRefreshEntry,
    SkillRefreshReport,
    SkillSyncEntry,
    SkillSyncReport,
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
    user: User = Depends(get_current_user),
) -> ApiResponse[Any]:
    """List metadata of every global skill.

    Args:
        request: The FastAPI request object for rate limiting.
        db: Request-scoped DB session.
        user: Authenticated user resolved from the user access token.

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
    user: User = Depends(get_current_user),
) -> ApiResponse[Any]:
    """List metadata of global skills with server-side pagination.

    Args:
        request: The FastAPI request object for rate limiting.
        page: 1-based page number.
        page_size: Rows per page (exposed as ``pageSize``).
        keyword: Optional case-insensitive substring matched against name.
        db: Request-scoped DB session.
        user: Authenticated user resolved from the user access token.

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
    user: User = Depends(get_current_user),
) -> ApiResponse[Any]:
    """Create a global skill from direct input (atomic file write + DB row).

    Args:
        request: The FastAPI request object for rate limiting.
        payload: The skill definition (name, description, SKILL.md body).
        db: Request-scoped DB session.
        user: Authenticated user used for audit attribution.

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
            created_by=user.username or str(user.id),
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
    user: User = Depends(get_current_user),
) -> ApiResponse[SkillGenerateResponse]:
    """Generate a SKILL.md draft via the LLM (draft only, nothing persisted).

    Args:
        request: The FastAPI request object for rate limiting.
        payload: Draft generation guidance.
        user: Authenticated user resolved from the user access token.

    Returns:
        Envelope carrying the generated draft content.

    Raises:
        HTTPException: 500 when the LLM fails after retries.
    """
    try:
        logger.info("skill_generate_requested", user_id=user.id)
        draft = await skills_store.generate_skill_draft(description=payload.description, hint=payload.hint)
        return ApiResponse.success(SkillGenerateResponse(draft=draft))
    except Exception as exc:
        logger.exception("skill_generate_failed")
        raise HTTPException(status_code=500, detail=str(exc)) from exc


@router.post("/skills/refresh", response_model=ApiResponse[SkillRefreshReport])
@limiter.limit(settings.RATE_LIMIT_ENDPOINTS["skill"][0])
async def refresh_all_skills(
    request: Request,
    db: DBSession = Depends(get_db_session),
    user: User = Depends(get_current_user),
) -> ApiResponse[SkillRefreshReport]:
    """Refresh every skill's disk SKILL.md copy from its DB body.

    ``content_hash`` is the rewrite trigger: a disk file whose sha256 matches
    the row hash is left untouched; missing or drifted files are rewritten
    from the DB body (DB is the source of truth). Legacy rows created before
    dual-store (``body IS NULL``) are backfilled from their disk file.

    Args:
        request: The FastAPI request object for rate limiting.
        db: Request-scoped DB session.
        user: Authenticated user resolved from the user access token.

    Returns:
        Envelope carrying a per-skill refresh report.
    """
    try:
        entries = await skills_store.refresh_disk_from_db(db)
        return ApiResponse.success(_build_refresh_report(entries))
    except Exception as exc:
        logger.exception("skill_disk_refresh_all_failed")
        raise HTTPException(status_code=500, detail=str(exc)) from exc


@router.post("/skills/{name}/refresh", response_model=ApiResponse[SkillRefreshReport])
@limiter.limit(settings.RATE_LIMIT_ENDPOINTS["skill"][0])
async def refresh_skill(
    request: Request,
    name: str,
    db: DBSession = Depends(get_db_session),
    user: User = Depends(get_current_user),
) -> ApiResponse[SkillRefreshReport]:
    """Refresh one skill's disk SKILL.md copy from its DB body.

    Args:
        request: The FastAPI request object for rate limiting.
        name: Skill primary key.
        db: Request-scoped DB session.
        user: Authenticated user resolved from the user access token.

    Returns:
        Envelope carrying a single-entry refresh report.

    Raises:
        HTTPException: 404 when the skill has no DB row.
    """
    try:
        entries = await skills_store.refresh_disk_from_db(db, name=name)
        return ApiResponse.success(_build_refresh_report(entries))
    except ValueError as exc:
        logger.warning("skill_disk_refresh_rejected", name=name, error=str(exc))
        raise HTTPException(status_code=404, detail=f"skill '{name}' not found") from exc
    except Exception as exc:
        logger.exception("skill_disk_refresh_failed", name=name)
        raise HTTPException(status_code=500, detail=str(exc)) from exc


def _build_refresh_report(entries: list[dict[str, str]]) -> SkillRefreshReport:
    """Aggregate raw refresh entries into the API report model.

    ``action`` values come from ``refresh_disk_from_db`` which only ever
    emits the four literal outcomes, so the str -> Literal narrowing is safe.
    """
    items = [SkillRefreshEntry(name=entry["name"], action=cast(Any, entry["action"])) for entry in entries]
    return SkillRefreshReport(
        items=items,
        total=len(items),
        rewritten=sum(1 for item in items if item.action == "rewritten"),
        unchanged=sum(1 for item in items if item.action == "unchanged"),
        backfilled=sum(1 for item in items if item.action == "backfilled"),
        missing=sum(1 for item in items if item.action == "missing"),
    )


# Registered BEFORE the ``/skills/{name}`` routes: FastAPI matches in
# registration order, so ``workspace-sync`` would otherwise be captured by
# the ``{name}`` path parameter.


@router.get("/skills/workspace-sync", response_model=ApiResponse[SkillSyncReport])
@limiter.limit(settings.RATE_LIMIT_ENDPOINTS["skill"][0])
async def plan_skill_workspace_sync(
    request: Request,
    db: DBSession = Depends(get_db_session),
    user: User = Depends(get_current_user),
) -> ApiResponse[SkillSyncReport]:
    """Dry-run the workspace sync (zero writes).

    Reconciles DB rows against ``{DATA_ROOT}/global/skills/*/SKILL.md``:
    matching files stay ``unchanged``; drifted/missing files would be
    ``rewritten`` from the DB (DB is truth); disk-only files would be
    ``imported``; broken files degrade per-file to ``invalid``.

    Args:
        request: The FastAPI request object for rate limiting.
        db: Request-scoped DB session.
        user: Authenticated user resolved from the user access token.

    Returns:
        Envelope carrying the planned sync report.
    """
    try:
        report = await skills_store.plan_workspace_sync(db)
        return ApiResponse.success(_build_sync_report(report))
    except Exception as exc:
        logger.exception("skill_workspace_sync_preview_failed")
        raise HTTPException(status_code=500, detail=str(exc)) from exc


@router.post("/skills/workspace-sync", response_model=ApiResponse[SkillSyncReport])
@limiter.limit(settings.RATE_LIMIT_ENDPOINTS["skill"][0])
async def apply_skill_workspace_sync(
    request: Request,
    db: DBSession = Depends(get_db_session),
    user: User = Depends(get_current_user),
) -> ApiResponse[SkillSyncReport]:
    """Execute the workspace sync (directory reconciliation).

    Drifted/missing files are rewritten from the DB row; disk-only files are
    imported as new rows (``created_by="workspace-sync"``) and normalized;
    invalid files degrade per-file without blocking the rest. Idempotent:
    an immediate second call reports everything ``unchanged``.

    Args:
        request: The FastAPI request object for rate limiting.
        db: Request-scoped DB session.
        user: Authenticated user resolved from the user access token.

    Returns:
        Envelope carrying the executed sync report.
    """
    try:
        report = await skills_store.apply_workspace_sync(db)
        return ApiResponse.success(_build_sync_report(report))
    except Exception as exc:
        logger.exception("skill_workspace_sync_failed")
        raise HTTPException(status_code=500, detail=str(exc)) from exc


def _build_sync_report(report: dict[str, Any]) -> SkillSyncReport:
    """Aggregate the service-layer sync dict into the API report model."""
    items = [SkillSyncEntry(name=name, action="unchanged") for name in report["unchanged"]]
    items += [SkillSyncEntry(name=name, action="rewritten") for name in report["rewritten"]]
    items += [SkillSyncEntry(name=name, action="imported") for name in report["imported"]]
    items += [
        SkillSyncEntry(name=entry["file"], action="invalid", reason=entry["reason"])
        for entry in report["invalid"]
    ]
    return SkillSyncReport(
        items=items,
        scanned=report["scanned"],
        unchanged=len(report["unchanged"]),
        rewritten=len(report["rewritten"]),
        imported=len(report["imported"]),
        invalid=len(report["invalid"]),
    )


@router.get("/skills/{name}", response_model=ApiResponse[SkillRead])
@limiter.limit(settings.RATE_LIMIT_ENDPOINTS["skill"][0])
async def get_skill(
    request: Request,
    name: str,
    db: DBSession = Depends(get_db_session),
    user: User = Depends(get_current_user),
) -> ApiResponse[Any]:
    """Fetch one skill asset's metadata by name.

    Args:
        request: The FastAPI request object for rate limiting.
        name: Skill primary key.
        db: Request-scoped DB session.
        user: Authenticated user resolved from the user access token.

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
    db: DBSession = Depends(get_db_session),
    user: User = Depends(get_current_user),
) -> ApiResponse[Any]:
    """Fetch the raw SKILL.md body of a global skill by name.

    Dual-store read: the disk file is served directly; when it is missing
    (e.g. lost on a container rebuild) the body is recovered from the DB row
    and the disk file is rewritten on the fly.

    Args:
        request: The FastAPI request object for rate limiting.
        name: Skill primary key.
        db: Request-scoped DB session (self-heal fallback source).
        user: Authenticated user resolved from the user access token.

    Returns:
        Envelope carrying the skill name and its full SKILL.md content.

    Raises:
        HTTPException: 404 when the skill does not exist.
    """
    try:
        content = await skills_store.read_global(db, name)
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
    user: User = Depends(get_current_user),
) -> ApiResponse[Any]:
    """Partially update a skill (name is immutable).

    Args:
        request: The FastAPI request object for rate limiting.
        name: Skill primary key.
        db: Request-scoped DB session.
        user: Authenticated user resolved from the user access token.

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
    user: User = Depends(get_current_user),
) -> ApiResponse[None]:
    """Delete a global skill (cascades to per-user copies).

    Args:
        request: The FastAPI request object for rate limiting.
        name: Skill primary key.
        db: Request-scoped DB session.
        user: Authenticated user resolved from the user access token.

    Returns:
        Envelope with null data on successful deletion.

    Raises:
        HTTPException: 404 when the skill does not exist.
    """
    try:
        if db.get(SkillAsset, name) is None:
            raise HTTPException(status_code=404, detail=f"skill '{name}' not found")
        owners = _skill_owners(db, name)
        if owners:
            logger.warning("skill_delete_rejected", name=name, reason="referenced", owners=owners)
            raise HTTPException(
                status_code=422,
                detail=f"skill '{name}' is referenced by: {', '.join(owners)}",
            )
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
