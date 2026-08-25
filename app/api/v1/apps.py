"""Admin API for agent application assets (CRUD + publish pipeline).

Phase-1 scope: all assets are globally shared (no per-user ownership checks);
every endpoint only authenticates via ``get_current_user`` and records the
creator in the audit-only ``created_by`` field.

Error semantics: missing resources return 404; name collisions, validation
failures and dangling skill/subagent/tool references return 422; unexpected
failures return 500 after ``logger.exception``.
"""

from typing import Any

from fastapi import APIRouter, Depends, HTTPException, Query, Request
from sqlmodel import Session as DBSession
from sqlmodel import col, select

from app.api.v1.agent_assets_common import (
    _read_patch_body,
    _validate_payload,
    get_db_session,
    paginate_by_name,
)
from app.api.v1.auth import get_current_user
from app.api.v1.mcp_servers import _mcp_fingerprint
from app.api.v1.providers import _model_fingerprint, build_model_catalog
from app.core.config import settings
from app.core.limiter import limiter
from app.core.logging import logger
from app.models.agent_assets import AgentApp, SkillAsset, SubAgentConfig
from app.models.provider import DEFAULT_MODEL_REF
from app.models.user import User
from app.schemas.agent_apps import AgentAppCreate, AgentAppRead, AgentAppUpdate
from app.schemas.base import ApiResponse, PageResult
from app.services.agents import assembly
from app.services.agents.mcp_manager import build_tool_catalog

router = APIRouter()

# System default AgentApp name (bootstrap-seeded; delete-protected like the
# default provider/model pair).
_DEFAULT_AGENT_APP_NAME = "default"


# ---------------------------------------------------------------------------
# Agent apps
# ---------------------------------------------------------------------------


@router.get("/apps", response_model=ApiResponse[list[AgentAppRead]])
@limiter.limit(settings.RATE_LIMIT_ENDPOINTS["agent_app"][0])
async def list_agent_apps(
    request: Request,
    db: DBSession = Depends(get_db_session),
    user: User = Depends(get_current_user),
) -> ApiResponse[Any]:
    """List every stored agent application.

    Args:
        request: The FastAPI request object for rate limiting.
        db: Request-scoped DB session.
        user: Authenticated user resolved from the user access token.

    Returns:
        Envelope carrying all agent app rows ordered by id.
    """
    try:
        return ApiResponse.success(list(db.exec(select(AgentApp).order_by(col(AgentApp.id))).all()))
    except Exception as exc:
        logger.exception("agent_app_list_failed")
        raise HTTPException(status_code=500, detail=str(exc)) from exc


@router.get("/apps/page", response_model=ApiResponse[PageResult[AgentAppRead]])
@limiter.limit(settings.RATE_LIMIT_ENDPOINTS["agent_app"][0])
async def list_agent_apps_page(
    request: Request,
    page: int = Query(1, ge=1),
    page_size: int = Query(10, ge=1, le=100, alias="pageSize"),
    keyword: str | None = Query(None, max_length=200),
    db: DBSession = Depends(get_db_session),
    user: User = Depends(get_current_user),
) -> ApiResponse[Any]:
    """List agent applications with server-side pagination.

    Args:
        request: The FastAPI request object for rate limiting.
        page: 1-based page number.
        page_size: Rows per page (exposed as ``pageSize``).
        keyword: Optional case-insensitive substring matched against name.
        db: Request-scoped DB session.
        user: Authenticated user resolved from the user access token.

    Returns:
        Envelope carrying a PageResult of agent app rows ordered by id.
    """
    try:
        return ApiResponse.success(
            paginate_by_name(
                db,
                AgentApp,
                page=page,
                page_size=page_size,
                keyword=keyword,
                order_by=col(AgentApp.id),
            )
        )
    except Exception as exc:
        logger.exception("agent_app_list_page_failed")
        raise HTTPException(status_code=500, detail=str(exc)) from exc


@router.post("/apps", response_model=ApiResponse[AgentAppRead], status_code=201)
@limiter.limit(settings.RATE_LIMIT_ENDPOINTS["agent_app"][0])
async def create_agent_app(
    request: Request,
    payload: AgentAppCreate,
    db: DBSession = Depends(get_db_session),
    user: User = Depends(get_current_user),
) -> ApiResponse[Any]:
    """Create a draft agent application.

    Args:
        request: The FastAPI request object for rate limiting.
        payload: The agent app definition.
        db: Request-scoped DB session.
        user: Authenticated user used for audit attribution.

    Returns:
        Envelope carrying the persisted agent app row
        (status=draft, engine=deepagents).

    Raises:
        HTTPException: 422 when the name is already taken.
    """
    try:
        existing = db.exec(select(AgentApp).where(col(AgentApp.name) == payload.name)).first()
        if existing is not None:
            logger.warning("agent_app_create_conflict", name=payload.name)
            raise HTTPException(status_code=422, detail=f"agent app '{payload.name}' already exists")

        app_cfg = AgentApp(
            name=payload.name,
            system_prompt=payload.system_prompt,
            allowed_tools=payload.allowed_tools,
            model=payload.model,
            skill_names=payload.skill_names,
            subagent_names=payload.subagent_names,
            interrupt_on=payload.interrupt_on,
            created_by=user.username or str(user.id),
        )
        db.add(app_cfg)
        db.commit()
        db.refresh(app_cfg)
        logger.info("agent_app_created", app_id=app_cfg.id, name=payload.name)
        return ApiResponse.success(app_cfg, code=201)
    except HTTPException:
        raise
    except Exception as exc:
        logger.exception("agent_app_create_failed", name=payload.name)
        raise HTTPException(status_code=500, detail=str(exc)) from exc


@router.get("/apps/published", response_model=ApiResponse[list[AgentAppRead]])
@limiter.limit(settings.RATE_LIMIT_ENDPOINTS["agent_app"][0])
async def list_published_agent_apps(
    request: Request,
    db: DBSession = Depends(get_db_session),
    user: User = Depends(get_current_user),
) -> ApiResponse[Any]:
    """List published agent applications (assistant picker for chat).

    Registered before ``/apps/{app_id}`` so the literal path wins routing.

    Args:
        request: The FastAPI request object for rate limiting.
        db: Request-scoped DB session.
        user: Authenticated user resolved from the user access token.

    Returns:
        Envelope carrying all agent app rows with status=published ordered by id.
    """
    try:
        statement = select(AgentApp).where(col(AgentApp.status) == "published").order_by(col(AgentApp.id))
        return ApiResponse.success(list(db.exec(statement).all()))
    except Exception as exc:
        logger.exception("agent_app_published_list_failed")
        raise HTTPException(status_code=500, detail=str(exc)) from exc


@router.get("/apps/{app_id}", response_model=ApiResponse[AgentAppRead])
@limiter.limit(settings.RATE_LIMIT_ENDPOINTS["agent_app"][0])
async def get_agent_app(
    request: Request,
    app_id: int,
    db: DBSession = Depends(get_db_session),
    user: User = Depends(get_current_user),
) -> ApiResponse[Any]:
    """Fetch one agent application by id.

    Args:
        request: The FastAPI request object for rate limiting.
        app_id: Agent app primary key.
        db: Request-scoped DB session.
        user: Authenticated user resolved from the user access token.

    Returns:
        Envelope carrying the matching agent app row.

    Raises:
        HTTPException: 404 when the agent app does not exist.
    """
    try:
        app_cfg = db.get(AgentApp, app_id)
        if app_cfg is None:
            raise HTTPException(status_code=404, detail=f"agent app '{app_id}' not found")
        return ApiResponse.success(app_cfg)
    except HTTPException:
        raise
    except Exception as exc:
        logger.exception("agent_app_read_failed", app_id=app_id)
        raise HTTPException(status_code=500, detail=str(exc)) from exc


@router.patch("/apps/{app_id}", response_model=ApiResponse[AgentAppRead])
@limiter.limit(settings.RATE_LIMIT_ENDPOINTS["agent_app"][0])
async def update_agent_app(
    request: Request,
    app_id: int,
    db: DBSession = Depends(get_db_session),
    user: User = Depends(get_current_user),
) -> ApiResponse[Any]:
    """Partially update an agent app (name is immutable; lists replace wholesale).

    Args:
        request: The FastAPI request object for rate limiting.
        app_id: Agent app primary key.
        db: Request-scoped DB session.
        user: Authenticated user resolved from the user access token.

    Returns:
        Envelope carrying the updated agent app row with bumped version.

    Raises:
        HTTPException: 404 when missing, 422 on name change or empty payload.
    """
    body = await _read_patch_body(request)
    payload = _validate_payload(AgentAppUpdate, body)
    try:
        app_cfg = db.get(AgentApp, app_id)
        if app_cfg is None:
            raise HTTPException(status_code=404, detail=f"agent app '{app_id}' not found")

        updates = payload.model_dump(exclude_unset=True)
        if not updates:
            raise HTTPException(status_code=422, detail="nothing to update")

        for field in ("skill_names", "subagent_names", "interrupt_on"):
            if field in updates and updates[field] is None:
                raise HTTPException(
                    status_code=422,
                    detail=f"{field} must not be null; pass an empty {'list' if field != 'interrupt_on' else 'dict'} to clear it",
                )

        for field, value in updates.items():
            setattr(app_cfg, field, value)
        if app_cfg.status == "published":
            # Content edits invalidate the published fingerprint: demote back
            # to draft so a broken config cannot keep serving live sessions.
            app_cfg.status = "draft"
            logger.info("agent_app_unpublished_on_edit", app_id=app_id)
        app_cfg.version += 1
        db.add(app_cfg)
        db.commit()
        db.refresh(app_cfg)
        logger.info("agent_app_updated", app_id=app_id, version=app_cfg.version)
        return ApiResponse.success(app_cfg)
    except HTTPException:
        raise
    except Exception as exc:
        logger.exception("agent_app_update_failed", app_id=app_id)
        raise HTTPException(status_code=500, detail=str(exc)) from exc


@router.delete("/apps/{app_id}", response_model=ApiResponse[None])
@limiter.limit(settings.RATE_LIMIT_ENDPOINTS["agent_app"][0])
async def delete_agent_app(
    request: Request,
    app_id: int,
    db: DBSession = Depends(get_db_session),
    user: User = Depends(get_current_user),
) -> ApiResponse[None]:
    """Delete an agent application.

    Args:
        request: The FastAPI request object for rate limiting.
        app_id: Agent app primary key.
        db: Request-scoped DB session.
        user: Authenticated user resolved from the user access token.

    Returns:
        Envelope with null data on successful deletion.

    Raises:
        HTTPException: 404 when the agent app does not exist.
    """
    try:
        app_cfg = db.get(AgentApp, app_id)
        if app_cfg is None:
            raise HTTPException(status_code=404, detail=f"agent app '{app_id}' not found")
        if app_cfg.name == _DEFAULT_AGENT_APP_NAME:
            logger.warning("agent_app_delete_rejected", app_id=app_id, reason="default_protected")
            raise HTTPException(
                status_code=422,
                detail="the system default agent app is protected and cannot be deleted",
            )
        db.delete(app_cfg)
        db.commit()
        logger.info("agent_app_deleted", app_id=app_id)
        return ApiResponse.success(None)
    except HTTPException:
        raise
    except Exception as exc:
        logger.exception("agent_app_delete_failed", app_id=app_id)
        raise HTTPException(status_code=500, detail=str(exc)) from exc


@router.post("/apps/{app_id}/publish", response_model=ApiResponse[AgentAppRead])
@limiter.limit(settings.RATE_LIMIT_ENDPOINTS["agent_app"][0])
async def publish_agent_app(
    request: Request,
    app_id: int,
    db: DBSession = Depends(get_db_session),
    user: User = Depends(get_current_user),
) -> ApiResponse[Any]:
    """Publish an agent app after referential + tool-whitelist validation.

    Validation order: skill/subagent reference existence (422) ->
    ``assembly.validate_publish`` against the live tool catalog (422) ->
    stamp status=published, published_hash (config fingerprint) and bump
    the version.

    Args:
        request: The FastAPI request object for rate limiting.
        app_id: Agent app primary key.
        db: Request-scoped DB session.
        user: Authenticated user resolved from the user access token.

    Returns:
        Envelope carrying the published agent app row.

    Raises:
        HTTPException: 404 when missing, 422 on dangling references or
            allowed_tools outside the tool catalog.
    """
    try:
        app_cfg = db.get(AgentApp, app_id)
        if app_cfg is None:
            raise HTTPException(status_code=404, detail=f"agent app '{app_id}' not found")

        subagent_cfgs: list[SubAgentConfig] = []
        for subagent_name in app_cfg.subagent_names:
            cfg = db.get(SubAgentConfig, subagent_name)
            if cfg is None:
                raise HTTPException(status_code=422, detail=f"referenced subagent '{subagent_name}' does not exist")
            subagent_cfgs.append(cfg)

        skill_hashes: dict[str, str] = {}
        for skill_name in app_cfg.skill_names:
            asset = db.get(SkillAsset, skill_name)
            if asset is None:
                raise HTTPException(status_code=422, detail=f"referenced skill '{skill_name}' does not exist")
            skill_hashes[skill_name] = asset.content_hash
        # Sub-agent explicit whitelists (the inherit ``None`` case contributes
        # nothing because the sub-agent resolves to the app's set, already
        # covered above) also have to resolve to a real SkillAsset; otherwise
        # a dangling subagent-only skill would silently skip recompilation.
        for cfg in subagent_cfgs:
            for skill_name in cfg.skill_names or []:
                if skill_name in skill_hashes:
                    continue
                asset = db.get(SkillAsset, skill_name)
                if asset is None:
                    raise HTTPException(
                        status_code=422,
                        detail=f"referenced skill '{skill_name}' (subagent '{cfg.name}') does not exist",
                    )
                skill_hashes[skill_name] = asset.content_hash

        catalog = await build_tool_catalog(db)
        model_catalog = build_model_catalog(db)
        try:
            assembly.validate_publish(app_cfg, subagent_cfgs, catalog, model_catalog)
        except ValueError as exc:
            logger.warning("agent_app_publish_validation_failed", app_id=app_id, error=str(exc))
            raise HTTPException(status_code=422, detail=str(exc)) from exc

        reference_names = {app_cfg.model or DEFAULT_MODEL_REF}
        reference_names.update(cfg.model or DEFAULT_MODEL_REF for cfg in subagent_cfgs)
        referenced = {name: model_catalog[name] for name in reference_names}

        app_cfg.status = "published"
        app_cfg.published_hash = assembly.compute_fingerprint(
            app_cfg, subagent_cfgs, skill_hashes, _mcp_fingerprint(db), _model_fingerprint(referenced)
        )
        app_cfg.version += 1
        db.add(app_cfg)
        db.commit()
        db.refresh(app_cfg)
        logger.info(
            "agent_app_published",
            app_id=app_id,
            version=app_cfg.version,
            skill_count=len(skill_hashes),
            subagent_count=len(subagent_cfgs),
        )
        return ApiResponse.success(app_cfg)
    except HTTPException:
        raise
    except Exception as exc:
        logger.exception("agent_app_publish_failed", app_id=app_id)
        raise HTTPException(status_code=500, detail=str(exc)) from exc
