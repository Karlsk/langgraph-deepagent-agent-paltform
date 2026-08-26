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
from app.core.config import settings
from app.core.limiter import limiter
from app.core.logging import logger
from app.models.agent_assets import AgentApp
from app.models.user import User
from app.schemas.agent_apps import AgentAppCreate, AgentAppRead, AgentAppUpdate
from app.schemas.base import ApiResponse, PageResult
from app.services.agents import agent_apps_service

router = APIRouter()

# Service-layer not-found errors map to 404; the remaining AgentAppServiceError
# subclasses (not-published, ...) map to 422 like the ValueError validations.
_NOT_FOUND_ERRORS = (
    agent_apps_service.AgentAppNotFoundError,
    agent_apps_service.UserNotFoundError,
    agent_apps_service.AssociationNotFoundError,
)


def _service_error_to_http(exc: agent_apps_service.AgentAppServiceError) -> HTTPException:
    """Translate a service-layer business error into its HTTP counterpart."""
    status = 404 if isinstance(exc, _NOT_FOUND_ERRORS) else 422
    return HTTPException(status_code=status, detail=str(exc))


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

    API layer: body parsing + schema validation only; the interpretation-B
    state machine (published -> draft, workspace hash invalidation) lives in
    ``agent_apps_service.patch_agent_app``.

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

        updated = await agent_apps_service.patch_agent_app(
            db, app_cfg=app_cfg, patch_data=payload, current_user_id=user.id
        )
        return ApiResponse.success(updated)
    except HTTPException:
        raise
    except agent_apps_service.AgentAppServiceError as exc:
        raise _service_error_to_http(exc) from exc
    except ValueError as exc:
        raise HTTPException(status_code=422, detail=str(exc)) from exc
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
    """Delete an agent application (DB row + workspace cascade).

    API layer: parameter validation only; the default-app protection and
    the agent-workspace directory cascade live in
    ``agent_apps_service.delete_agent_app``.

    Args:
        request: The FastAPI request object for rate limiting.
        app_id: Agent app primary key.
        db: Request-scoped DB session.
        user: Authenticated user resolved from the user access token.

    Returns:
        Envelope with null data on successful deletion.

    Raises:
        HTTPException: 404 when the agent app does not exist, 422 when it is
            the delete-protected system default app.
    """
    try:
        await agent_apps_service.delete_agent_app(
            db, app_id=app_id, current_user_id=user.id
        )
        return ApiResponse.success(None)
    except agent_apps_service.AgentAppServiceError as exc:
        raise _service_error_to_http(exc) from exc
    except ValueError as exc:
        raise HTTPException(status_code=422, detail=str(exc)) from exc
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

    API layer: parameter validation only; the two-stage referential
    validation, Global -> Agent materialization, workspace_hash stamping and
    user-layer cache invalidation live in
    ``agent_apps_service.publish_agent_app``.

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

        published = await agent_apps_service.publish_agent_app(
            db, app_cfg=app_cfg, current_user_id=user.id
        )
        return ApiResponse.success(published)
    except HTTPException:
        raise
    except agent_apps_service.AgentAppServiceError as exc:
        raise _service_error_to_http(exc) from exc
    except ValueError as exc:
        logger.warning("agent_app_publish_validation_failed", app_id=app_id, error=str(exc))
        raise HTTPException(status_code=422, detail=str(exc)) from exc
    except Exception as exc:
        logger.exception("agent_app_publish_failed", app_id=app_id)
        raise HTTPException(status_code=500, detail=str(exc)) from exc


@router.post("/apps/{app_id}/associate-user/{user_id}", response_model=ApiResponse[None])
@limiter.limit(settings.RATE_LIMIT_ENDPOINTS["agent_app"][0])
async def associate_user_with_app(
    request: Request,
    app_id: int,
    user_id: int,
    db: DBSession = Depends(get_db_session),
    user: User = Depends(get_current_user),
) -> ApiResponse[None]:
    """Associate a user with a published app (materialize the User layer).

    API layer: parameter validation only; the published-status check, user
    lookup, association upsert and the combined (Global + Agent) -> User
    materialization live in ``agent_apps_service.associate_user_with_app``.

    Args:
        request: The FastAPI request object for rate limiting.
        app_id: Agent app primary key.
        user_id: Target user primary key.
        db: Request-scoped DB session.
        user: Authenticated user resolved from the user access token.

    Returns:
        Envelope with null data once the user layer is materialized.

    Raises:
        HTTPException: 404 when the app or user does not exist, 422 when
            the app is not published.
    """
    try:
        await agent_apps_service.associate_user_with_app(
            db, user_id=user_id, app_id=app_id, current_user_id=user.id
        )
        return ApiResponse.success(None)
    except agent_apps_service.AgentAppServiceError as exc:
        raise _service_error_to_http(exc) from exc
    except ValueError as exc:
        raise HTTPException(status_code=422, detail=str(exc)) from exc
    except Exception as exc:
        logger.exception("user_app_associate_failed", app_id=app_id, user_id=user_id)
        raise HTTPException(status_code=500, detail=str(exc)) from exc


@router.delete("/apps/{app_id}/associate-user/{user_id}", response_model=ApiResponse[None])
@limiter.limit(settings.RATE_LIMIT_ENDPOINTS["agent_app"][0])
async def disassociate_user_from_app(
    request: Request,
    app_id: int,
    user_id: int,
    db: DBSession = Depends(get_db_session),
    user: User = Depends(get_current_user),
) -> ApiResponse[None]:
    """Remove a user association and clean the User workspace directory.

    API layer: parameter validation only; the association lookup and the
    user-layer directory cleanup live in
    ``agent_apps_service.disassociate_user_from_app``.

    Args:
        request: The FastAPI request object for rate limiting.
        app_id: Agent app primary key.
        user_id: Target user primary key.
        db: Request-scoped DB session.
        user: Authenticated user resolved from the user access token.

    Returns:
        Envelope with null data once the association is removed.

    Raises:
        HTTPException: 404 when the association does not exist.
    """
    try:
        await agent_apps_service.disassociate_user_from_app(
            db, user_id=user_id, app_id=app_id, current_user_id=user.id
        )
        return ApiResponse.success(None)
    except agent_apps_service.AgentAppServiceError as exc:
        raise _service_error_to_http(exc) from exc
    except ValueError as exc:
        raise HTTPException(status_code=422, detail=str(exc)) from exc
    except Exception as exc:
        logger.exception("user_app_disassociate_failed", app_id=app_id, user_id=user_id)
        raise HTTPException(status_code=500, detail=str(exc)) from exc
