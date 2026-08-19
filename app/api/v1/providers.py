"""Admin API for LLM provider and model config assets (soft-delete CRUD).

Phase-1 scope: all assets are globally shared (no per-user ownership checks);
every endpoint only authenticates via ``get_current_session`` and records the
creator in the audit-only ``created_by`` field.

Error semantics: missing resources return 404; name collisions, validation
failures and reference-protection violations return 422; unexpected failures
return 500 after ``logger.exception``. The raw auth secrets in
``auth_config`` are physically excluded from every response; only the masked
projection ``api_key_masked`` is ever returned.

Agent asset ``model`` fields reference ``"<provider>/<model>"`` pairs
(NULL resolves to ``default/default``); deletion guards mirror the retired
llm-config contract (default pair protected + 422 on live references).
"""

import time
from collections.abc import Mapping
from datetime import UTC, datetime
from typing import Any

from fastapi import APIRouter, Depends, Header, HTTPException, Query, Request
from openai import AsyncOpenAI
from sqlalchemy import func
from sqlalchemy.exc import IntegrityError
from sqlmodel import Session as DBSession
from sqlmodel import col, select

from app.api.v1.agent_assets_common import (
    _creator,
    _read_patch_body,
    _validate_payload,
    get_db_session,
    paginate_by_name,
)
from app.api.v1.auth import get_current_session
from app.core.config import settings
from app.core.limiter import limiter
from app.core.logging import logger
from app.models.agent_assets import AgentApp, SubAgentConfig
from app.models.provider import (
    DEFAULT_MODEL_NAME,
    DEFAULT_MODEL_REF,
    DEFAULT_PROVIDER_NAME,
    ModelConfig,
    Provider,
    ProviderHealth,
)
from app.models.session import Session as ChatSession
from app.schemas.base import ApiResponse, PageResult
from app.schemas.providers import (
    ConnectionTestResult,
    ModelConfigCreate,
    ModelConfigRead,
    ModelConfigUpdate,
    ProviderCreate,
    ProviderRead,
    ProviderRowWithMeta,
    ProviderUpdate,
    RemoteModelInfo,
)
from app.services.llm.discovery import discover_remote_models
from app.services.llm.llm_store import (
    compute_model_config_hash,
    get_deleted_provider,
    hard_delete_provider,
    list_deleted_providers,
    list_models_under_deleted_provider,
)

# Header literal required to confirm an irreversible hard delete (escape hatch).
HARD_DELETE_CONFIRM_HEADER = "X-Confirm-Hard-Delete"
HARD_DELETE_CONFIRM_VALUE = "true"

router = APIRouter()

# Provider PATCH fields backed by NOT NULL columns: explicit JSON null is
# rejected (omit the field to keep it unchanged).
_PROVIDER_NOT_NULL_PATCH_FIELDS = frozenset({"type", "base_url", "enabled", "auth_config"})
_MODEL_NOT_NULL_PATCH_FIELDS = frozenset({"model_id", "enabled", "extra_params"})


# ---------------------------------------------------------------------------
# Shared helpers
# ---------------------------------------------------------------------------


def _mask_api_key(api_key: str) -> str:
    """Project an API key into its masked form.

    Short keys (length <= 8) mask completely — exposing their tail would
    leak half or more of the secret. Longer keys keep the last four chars.
    """
    if len(api_key) <= 8:
        return "****"
    return "****" + api_key[-4:]


def _iso(value: datetime | None) -> str | None:
    """Project an optional timestamp as an ISO-8601 string."""
    return value.isoformat() if value is not None else None


def _provider_read(provider: Provider) -> dict[str, Any]:
    """Project a Provider row into its API response form.

    Auth secrets are physically excluded; only the masked projection
    ``api_key_masked`` is ever returned (empty string when no key stored).
    """
    api_key = provider.auth_config.get("api_key") if provider.auth_config else None
    return {
        "id": provider.id,
        "name": provider.name,
        "type": provider.type,
        "base_url": provider.base_url,
        "api_key_masked": _mask_api_key(api_key) if isinstance(api_key, str) and api_key else "",
        "enabled": provider.enabled,
        "created_by": provider.created_by,
        "created_at": _iso(provider.created_at),
        "updated_at": _iso(provider.updated_at),
    }


def _health_read(health: ProviderHealth | None) -> dict[str, Any]:
    """Project a health row (or the UNKNOWN default) into response form."""
    if health is None:
        return {
            "status": "UNKNOWN",
            "last_check_at": None,
            "last_success_at": None,
            "fail_count": 0,
            "latency_ms": None,
            "error_message": None,
        }
    return {
        "status": health.status,
        "last_check_at": _iso(health.last_check_at),
        "last_success_at": _iso(health.last_success_at),
        "fail_count": health.fail_count,
        "latency_ms": health.latency_ms,
        "error_message": health.error_message,
    }


def _model_read(model: ModelConfig, provider_name: str) -> dict[str, Any]:
    """Project a ModelConfig row into its API response form."""
    return {
        "id": model.id,
        "provider_name": provider_name,
        "name": model.name,
        "model_id": model.model_id,
        "ref": f"{provider_name}/{model.name}",
        "context_size": model.context_size,
        "extra_params": model.extra_params or {},
        "enabled": model.enabled,
        "created_by": model.created_by,
        "created_at": _iso(model.created_at),
        "updated_at": _iso(model.updated_at),
    }


def _provider_trash_read(provider: Provider) -> dict[str, Any]:
    """Project a Provider row for the trash view (auth secrets masked further).

    Soft-deleted rows carry an extra risk: they sit in the DB until they are
    hard-deleted. To prevent any leak through the trash list, this projection
    physically excludes the raw ``auth_config`` payload and only exposes
    ``api_key_masked``.
    """
    api_key = provider.auth_config.get("api_key") if provider.auth_config else None
    return {
        "id": provider.id,
        "name": provider.name,
        "type": provider.type,
        "base_url": provider.base_url,
        "api_key_masked": _mask_api_key(api_key) if isinstance(api_key, str) and api_key else "",
        "enabled": provider.enabled,
        "deleted": True,
        "created_by": provider.created_by,
        "created_at": _iso(provider.created_at),
        "updated_at": _iso(provider.updated_at),
    }


def _get_provider(db: DBSession, name: str) -> Provider | None:
    """Fetch a live (non-deleted) provider by unique name."""
    return db.exec(select(Provider).where(col(Provider.name) == name, col(Provider.deleted) == False)).first()  # noqa: E712


def _list_models(db: DBSession, provider_id: int) -> list[ModelConfig]:
    """List every live model config of a provider ordered by name."""
    return list(
        db.exec(
            select(ModelConfig)
            .where(col(ModelConfig.provider_id) == provider_id, col(ModelConfig.deleted) == False)  # noqa: E712
            .order_by(col(ModelConfig.name))
        ).all()
    )


def build_model_catalog(db: DBSession) -> dict[str, tuple[Provider, ModelConfig]]:
    """Build the ``"provider/model"`` reference catalog of live rows.

    Args:
        db: Request-scoped DB session.

    Returns:
        Mapping of reference string -> (provider, model config) pair over
        every non-deleted provider/model row (enabled flag preserved for
        validation callers).
    """
    providers = {row.id: row for row in db.exec(select(Provider).where(col(Provider.deleted) == False)).all()}  # noqa: E712
    catalog: dict[str, tuple[Provider, ModelConfig]] = {}
    for model in db.exec(select(ModelConfig).where(col(ModelConfig.deleted) == False)).all():  # noqa: E712
        provider = providers.get(model.provider_id)
        if provider is not None:
            catalog[f"{provider.name}/{model.name}"] = (provider, model)
    return catalog


def _model_fingerprint(model_catalog: Mapping[str, tuple[Provider, ModelConfig]]) -> str:
    """Fingerprint the model catalog (publish hash input)."""
    return "|".join(
        sorted(
            f"{ref}:{compute_model_config_hash(provider, model)}" for ref, (provider, model) in model_catalog.items()
        )
    )


def _referencing_owners(db: DBSession, ref: str) -> list[str]:
    """List asset owners whose ``model`` field equals the reference."""
    apps = db.exec(select(AgentApp).where(col(AgentApp.model) == ref)).all()
    subagents = db.exec(select(SubAgentConfig).where(col(SubAgentConfig.model) == ref)).all()
    return sorted([f"agent_app:{row.name}" for row in apps] + [f"subagent:{row.name}" for row in subagents])


# ---------------------------------------------------------------------------
# Providers
# ---------------------------------------------------------------------------


@router.get("/providers", response_model=ApiResponse[list[ProviderRead]])
@limiter.limit(settings.RATE_LIMIT_ENDPOINTS["provider"][0])
async def list_providers(
    request: Request,
    db: DBSession = Depends(get_db_session),
    current_session: ChatSession = Depends(get_current_session),
) -> ApiResponse[list[dict[str, Any]]]:
    """List every live provider (auth secrets always masked).

    Args:
        request: The FastAPI request object for rate limiting.
        db: Request-scoped DB session.
        current_session: Authenticated chat session.

    Returns:
        Envelope carrying all non-deleted provider rows ordered by name.
    """
    try:
        rows = db.exec(select(Provider).where(col(Provider.deleted) == False).order_by(col(Provider.name))).all()  # noqa: E712
        return ApiResponse.success([_provider_read(row) for row in rows])
    except Exception as exc:
        logger.exception("provider_list_failed")
        raise HTTPException(status_code=500, detail=str(exc)) from exc


@router.get("/providers/page", response_model=ApiResponse[PageResult[ProviderRowWithMeta]])
@limiter.limit(settings.RATE_LIMIT_ENDPOINTS["provider"][0])
async def list_providers_page(
    request: Request,
    page: int = Query(1, ge=1),
    page_size: int = Query(10, ge=1, le=100, alias="pageSize"),
    keyword: str | None = Query(None, max_length=200),
    db: DBSession = Depends(get_db_session),
    current_session: ChatSession = Depends(get_current_session),
) -> ApiResponse[PageResult[dict[str, Any]]]:
    """List providers with server-side pagination, model count and health.

    Args:
        request: The FastAPI request object for rate limiting.
        page: 1-based page number.
        page_size: Rows per page (exposed as ``pageSize``).
        keyword: Optional case-insensitive substring matched against name.
        db: Request-scoped DB session.
        current_session: Authenticated chat session.

    Returns:
        Envelope carrying a PageResult of provider rows enriched with the
        enabled model count and the latest health snapshot, ordered by name.
    """
    try:
        paged = paginate_by_name(
            db,
            Provider,
            page=page,
            page_size=page_size,
            keyword=keyword,
            order_by=col(Provider.name),
            extra_where=[col(Provider.deleted) == False],  # noqa: E712
        )
        items: list[dict[str, Any]] = []
        for provider in paged.items:
            model_count = int(
                db.exec(
                    select(func.count())
                    .select_from(ModelConfig)
                    .where(
                        col(ModelConfig.provider_id) == provider.id,
                        col(ModelConfig.enabled) == True,  # noqa: E712
                        col(ModelConfig.deleted) == False,  # noqa: E712
                    )
                ).one()
            )
            health = db.exec(select(ProviderHealth).where(col(ProviderHealth.provider_id) == provider.id)).first()
            items.append(
                {
                    "provider": _provider_read(provider),
                    "model_count": model_count,
                    "health": _health_read(health),
                }
            )
        enriched = PageResult[dict[str, Any]](
            items=items,
            total=paged.total,
            page=paged.page,
            page_size=paged.page_size,
        )
        return ApiResponse.success(enriched)
    except Exception as exc:
        logger.exception("provider_list_page_failed")
        raise HTTPException(status_code=500, detail=str(exc)) from exc


@router.post("/providers", response_model=ApiResponse[ProviderRead], status_code=201)
@limiter.limit(settings.RATE_LIMIT_ENDPOINTS["provider"][0])
async def create_provider(
    request: Request,
    payload: ProviderCreate,
    db: DBSession = Depends(get_db_session),
    current_session: ChatSession = Depends(get_current_session),
) -> ApiResponse[dict[str, Any]]:
    """Create a model provider referenced by asset ``model`` fields.

    Args:
        request: The FastAPI request object for rate limiting.
        payload: The provider connection definition.
        db: Request-scoped DB session.
        current_session: Authenticated chat session (audit only).

    Returns:
        Envelope carrying the persisted provider row (masked projection).

    Raises:
        HTTPException: 422 when the name is already taken (pre-check or a
            lost unique-name race at commit time).
    """
    try:
        if _get_provider(db, payload.name) is not None:
            logger.warning("provider_create_conflict", name=payload.name)
            raise HTTPException(status_code=422, detail=f"provider '{payload.name}' already exists")

        provider = Provider(
            name=payload.name,
            type=payload.type,
            base_url=payload.base_url,
            auth_config=payload.auth_config,
            enabled=payload.enabled,
            created_by=_creator(current_session),
        )
        db.add(provider)
        try:
            db.commit()
        except IntegrityError as exc:
            # Concurrent create won the unique-name race between the pre-check
            # and the insert: degrade to the same 422 the pre-check returns.
            db.rollback()
            logger.warning("provider_create_conflict", name=payload.name)
            raise HTTPException(status_code=422, detail=f"provider '{payload.name}' already exists") from exc
        db.refresh(provider)
        logger.info("provider_created", name=payload.name, created_by=provider.created_by)
        return ApiResponse.success(_provider_read(provider), code=201)
    except HTTPException:
        raise
    except Exception as exc:
        logger.exception("provider_create_failed", name=payload.name)
        raise HTTPException(status_code=500, detail=str(exc)) from exc


# ---------------------------------------------------------------------------
# Trash (soft-deleted) views
#
# IMPORTANT — route ordering: FastAPI matches routes in registration order.
# The literal paths ``/providers/deleted`` and ``/providers/deleted/{name}``
# MUST be registered BEFORE the parameterized ``/providers/{name}`` route so
# that a request for ``/providers/deleted`` is not silently captured by the
# generic provider handler with ``name="deleted"`` and returns 404.
# ---------------------------------------------------------------------------


@router.get("/providers/deleted", response_model=ApiResponse[list[dict[str, Any]]])
@limiter.limit(settings.RATE_LIMIT_ENDPOINTS["provider"][0])
async def list_deleted_providers_endpoint(
    request: Request,
    db: DBSession = Depends(get_db_session),
    current_session: ChatSession = Depends(get_current_session),
) -> ApiResponse[list[dict[str, Any]]]:
    """List every soft-deleted provider row ordered by ``updated_at`` desc.

    The projection never carries the raw ``auth_config`` payload; only the
    masked ``api_key_masked`` is returned, so trash rows leak no additional
    secrets versus live rows.

    Args:
        request: The FastAPI request object for rate limiting.
        db: Request-scoped DB session.
        current_session: Authenticated chat session.

    Returns:
        Envelope carrying the masked trash projection (possibly empty).
    """
    try:
        rows = list_deleted_providers(db)
        logger.info(
            "provider_trash_listed",
            count=len(rows),
            actor=_creator(current_session),
        )
        return ApiResponse.success([_provider_trash_read(row) for row in rows])
    except HTTPException:
        raise
    except Exception as exc:
        logger.exception("provider_trash_list_failed")
        raise HTTPException(status_code=500, detail=str(exc)) from exc


@router.get("/providers/deleted/{name}", response_model=ApiResponse[dict[str, Any]])
@limiter.limit(settings.RATE_LIMIT_ENDPOINTS["provider"][0])
async def get_deleted_provider_endpoint(
    request: Request,
    name: str,
    db: DBSession = Depends(get_db_session),
    current_session: ChatSession = Depends(get_current_session),
) -> ApiResponse[dict[str, Any]]:
    """Return one soft-deleted provider by name, or 404 when not in the trash.

    A live (non-deleted) provider with the same name is invisible to this
    endpoint so the trash view stays consistent with the list view.

    Args:
        request: The FastAPI request object for rate limiting.
        name: Provider unique name.
        db: Request-scoped DB session.
        current_session: Authenticated chat session.

    Returns:
        Envelope carrying the masked trash projection.

    Raises:
        HTTPException: 404 when no soft-deleted provider with that name exists.
    """
    try:
        provider = get_deleted_provider(db, name)
        if provider is None:
            raise HTTPException(status_code=404, detail=f"deleted provider '{name}' not found")
        logger.info("provider_trash_read", name=name, actor=_creator(current_session))
        return ApiResponse.success(_provider_trash_read(provider))
    except HTTPException:
        raise
    except Exception as exc:
        logger.exception("provider_trash_read_failed", name=name)
        raise HTTPException(status_code=500, detail=str(exc)) from exc


@router.get(
    "/providers/deleted/{name}/models",
    response_model=ApiResponse[list[dict[str, Any]]],
)
@limiter.limit(settings.RATE_LIMIT_ENDPOINTS["model_config"][0])
async def list_deleted_provider_models_endpoint(
    request: Request,
    name: str,
    db: DBSession = Depends(get_db_session),
    current_session: ChatSession = Depends(get_current_session),
) -> ApiResponse[list[dict[str, Any]]]:
    """List every model config of the soft-deleted provider named ``name``.

    The active-list endpoint cannot return these rows because the parent
    provider is tombstoned; the trash view is the only consumer that needs
    them (e.g. to confirm what's in the tombstone before hard-delete).

    Args:
        request: The FastAPI request object for rate limiting.
        name: Provider unique name.
        db: Request-scoped DB session.
        current_session: Authenticated chat session.

    Returns:
        Envelope carrying the masked model rows with their ``deleted`` flag.

    Raises:
        HTTPException: 404 when no soft-deleted provider with that name exists.
    """
    try:
        models = list_models_under_deleted_provider(db, name)
        if models is None:
            raise HTTPException(status_code=404, detail=f"deleted provider '{name}' not found")
        logger.info(
            "model_trash_listed",
            provider_name=name,
            count=len(models),
            actor=_creator(current_session),
        )
        rows: list[dict[str, Any]] = []
        for model in models:
            projection = _model_read(model, name)
            projection["deleted"] = model.deleted
            rows.append(projection)
        return ApiResponse.success(rows)
    except HTTPException:
        raise
    except Exception as exc:
        logger.exception("model_trash_list_failed", provider_name=name)
        raise HTTPException(status_code=500, detail=str(exc)) from exc


@router.get("/providers/{name}", response_model=ApiResponse[ProviderRead])
@limiter.limit(settings.RATE_LIMIT_ENDPOINTS["provider"][0])
async def get_provider(
    request: Request,
    name: str,
    db: DBSession = Depends(get_db_session),
    current_session: ChatSession = Depends(get_current_session),
) -> ApiResponse[dict[str, Any]]:
    """Fetch one provider by name (auth secrets always masked).

    Args:
        request: The FastAPI request object for rate limiting.
        name: Provider unique name.
        db: Request-scoped DB session.
        current_session: Authenticated chat session.

    Returns:
        Envelope carrying the matching provider row (masked projection).

    Raises:
        HTTPException: 404 when the provider does not exist or is deleted.
    """
    try:
        provider = _get_provider(db, name)
        if provider is None:
            raise HTTPException(status_code=404, detail=f"provider '{name}' not found")
        return ApiResponse.success(_provider_read(provider))
    except HTTPException:
        raise
    except Exception as exc:
        logger.exception("provider_read_failed", name=name)
        raise HTTPException(status_code=500, detail=str(exc)) from exc


@router.patch("/providers/{name}", response_model=ApiResponse[ProviderRead])
@limiter.limit(settings.RATE_LIMIT_ENDPOINTS["provider"][0])
async def update_provider(
    request: Request,
    name: str,
    db: DBSession = Depends(get_db_session),
    current_session: ChatSession = Depends(get_current_session),
) -> ApiResponse[dict[str, Any]]:
    """Partially update a provider (name is immutable).

    Omitting ``auth_config`` keeps the stored credentials unchanged.

    Args:
        request: The FastAPI request object for rate limiting.
        name: Provider unique name.
        db: Request-scoped DB session.
        current_session: Authenticated chat session.

    Returns:
        Envelope carrying the updated provider row (masked projection).

    Raises:
        HTTPException: 404 when missing, 422 on empty payload, explicit null
            on a NOT NULL field, or an auth_config missing api_key for a
            non-OLLAMA resulting type.
    """
    body = await _read_patch_body(request)
    payload = _validate_payload(ProviderUpdate, body)
    try:
        provider = _get_provider(db, name)
        if provider is None:
            raise HTTPException(status_code=404, detail=f"provider '{name}' not found")

        updates = payload.model_dump(exclude_unset=True)
        if not updates:
            raise HTTPException(status_code=422, detail="nothing to update")

        null_fields = sorted(
            field for field, value in updates.items() if value is None and field in _PROVIDER_NOT_NULL_PATCH_FIELDS
        )
        if null_fields:
            logger.warning("provider_update_rejected_null", name=name, fields=null_fields)
            raise HTTPException(
                status_code=422,
                detail=f"{', '.join(null_fields)}: null is not allowed; omit the field to keep it unchanged",
            )

        for field, value in updates.items():
            setattr(provider, field, value)

        # Re-validate the resulting type/auth_config pair.
        api_key = provider.auth_config.get("api_key") if provider.auth_config else None
        if provider.type != "OLLAMA" and not (isinstance(api_key, str) and api_key):
            raise HTTPException(status_code=422, detail="auth_config.api_key is required for non-OLLAMA providers")

        db.add(provider)
        db.commit()
        db.refresh(provider)
        logger.info("provider_updated", name=name)
        return ApiResponse.success(_provider_read(provider))
    except HTTPException:
        raise
    except Exception as exc:
        logger.exception("provider_update_failed", name=name)
        raise HTTPException(status_code=500, detail=str(exc)) from exc


@router.delete("/providers/{name}", response_model=ApiResponse[None])
@limiter.limit(settings.RATE_LIMIT_ENDPOINTS["provider"][0])
async def delete_provider(
    request: Request,
    name: str,
    hard: bool = Query(False, description="Physically delete the provider and its data (irreversible)"),
    x_confirm_hard_delete: str | None = Header(
        default=None,
        alias=HARD_DELETE_CONFIRM_HEADER,
        description="Required 'true' when hard=true; prevents accidental irreversible delete",
    ),
    db: DBSession = Depends(get_db_session),
    current_session: ChatSession = Depends(get_current_session),
) -> ApiResponse[None]:
    """Delete a provider: soft by default, hard when ``?hard=true`` is confirmed.

    The default branch soft-deletes the provider and cascades model rows;
    the provider's health row is dropped together with the provider.

    The hard branch requires the ``X-Confirm-Hard-Delete: true`` header so the
    operator must explicitly opt in to the irreversible action. All hard
    deletes fire the audit-only ``provider_hard_deleted`` warning event.

    Guards (default protection + reference check) fire before either branch
    is dispatched; even ``?hard=true`` cannot escape them.

    Args:
        request: The FastAPI request object for rate limiting.
        name: Provider unique name.
        hard: If true, physically delete the provider (irreversible).
        x_confirm_hard_delete: Header that must equal ``"true"`` when ``hard=true``.
        db: Request-scoped DB session.
        current_session: Authenticated chat session.

    Returns:
        Envelope with null data on successful deletion.

    Raises:
        HTTPException: 404 when missing, 422 when protected, referenced, or
            ``hard=true`` is sent without the confirmation header.
    """
    try:
        provider = _get_provider(db, name)
        if provider is None:
            raise HTTPException(status_code=404, detail=f"provider '{name}' not found")
        if name == DEFAULT_PROVIDER_NAME:
            logger.warning("provider_delete_rejected", name=name, reason="default_protected")
            raise HTTPException(status_code=422, detail=f"provider '{DEFAULT_PROVIDER_NAME}' cannot be deleted")
        if provider.id is None:
            raise HTTPException(status_code=500, detail=f"provider '{name}' row has no primary key")

        models = _list_models(db, provider.id)
        owners: list[str] = []
        for model in models:
            owners.extend(_referencing_owners(db, f"{provider.name}/{model.name}"))
        if owners:
            logger.warning("provider_delete_rejected", name=name, reason="referenced")
            raise HTTPException(
                status_code=422,
                detail=f"provider '{name}' is referenced by: {', '.join(sorted(owners))}",
            )

        if hard:
            if x_confirm_hard_delete != HARD_DELETE_CONFIRM_VALUE:
                logger.warning(
                    "provider_hard_delete_rejected",
                    name=name,
                    reason="missing_confirm_header",
                    header_value=x_confirm_hard_delete,
                )
                raise HTTPException(
                    status_code=422,
                    detail=(
                        f"{HARD_DELETE_CONFIRM_HEADER} header must be '{HARD_DELETE_CONFIRM_VALUE}' "
                        f"when hard=true (provider '{name}')"
                    ),
                )
            counts = hard_delete_provider(db, provider)
            logger.warning(
                "provider_hard_deleted",
                name=name,
                model_count=counts["models"],
                health_cleared=counts["health"],
                actor=_creator(current_session),
            )
            return ApiResponse.success(None)

        for model in models:
            model.deleted = True
            db.add(model)
        provider.deleted = True
        db.add(provider)
        health = db.exec(select(ProviderHealth).where(col(ProviderHealth.provider_id) == provider.id)).first()
        if health is not None:
            db.delete(health)
        db.commit()
        logger.info("provider_deleted", name=name, model_count=len(models))
        return ApiResponse.success(None)
    except HTTPException:
        raise
    except Exception as exc:
        logger.exception("provider_delete_failed", name=name)
        raise HTTPException(status_code=500, detail=str(exc)) from exc


@router.post("/providers/{name}/test", response_model=ApiResponse[ConnectionTestResult])
@limiter.limit(settings.RATE_LIMIT_ENDPOINTS["provider"][0])
async def test_provider_connection(
    request: Request,
    name: str,
    db: DBSession = Depends(get_db_session),
    current_session: ChatSession = Depends(get_current_session),
) -> ApiResponse[dict[str, Any]]:
    """Probe a provider on demand and persist the outcome into provider_health.

    The probe lists upstream models via the OpenAI-compatible API (zero
    inference cost). Success within the latency threshold records UP, a slow
    success records DEGRADED, and any failure records DOWN with the error
    message and an incremented fail counter.

    Args:
        request: The FastAPI request object for rate limiting.
        name: Provider unique name.
        db: Request-scoped DB session.
        current_session: Authenticated chat session.

    Returns:
        Envelope carrying the probe outcome (status, latency_ms, error).

    Raises:
        HTTPException: 404 when missing, 422 when the provider is disabled.
    """
    provider = _get_provider(db, name)
    if provider is None:
        raise HTTPException(status_code=404, detail=f"provider '{name}' not found")
    if not provider.enabled:
        raise HTTPException(status_code=422, detail=f"provider '{name}' is disabled")
    if provider.id is None:
        raise HTTPException(status_code=500, detail=f"provider '{name}' row has no primary key")

    api_key = provider.auth_config.get("api_key") if provider.auth_config else None
    client = AsyncOpenAI(
        api_key=api_key if isinstance(api_key, str) and api_key else "no-key",
        base_url=provider.base_url or None,
        timeout=settings.PROVIDER_TEST_TIMEOUT_SECONDS,
    )
    started = time.perf_counter()
    error_message: str | None = None
    try:
        await client.models.list()
        status = "UP"
    except Exception as exc:  # noqa: BLE001 - probe outcome is the contract
        status = "DOWN"
        error_message = str(exc)[:500]
    finally:
        await client.close()
    latency_ms = int((time.perf_counter() - started) * 1000)
    if status == "UP" and latency_ms > settings.PROVIDER_HEALTH_DEGRADED_MS:
        status = "DEGRADED"

    try:
        health = db.exec(select(ProviderHealth).where(col(ProviderHealth.provider_id) == provider.id)).first()
        if health is None:
            health = ProviderHealth(provider_id=provider.id)
        now = datetime.now(UTC)
        health.status = status
        health.last_check_at = now
        health.latency_ms = latency_ms
        health.error_message = error_message
        if status == "DOWN":
            health.fail_count += 1
        else:
            health.fail_count = 0
            health.last_success_at = now
        db.add(health)
        db.commit()
        logger.info("provider_connection_tested", name=name, status=status, latency_ms=latency_ms)
        return ApiResponse.success({"status": status, "latency_ms": latency_ms, "error_message": error_message})
    except Exception as exc:
        logger.exception("provider_connection_test_failed", name=name)
        raise HTTPException(status_code=500, detail=str(exc)) from exc


@router.post(
    "/providers/{name}/discover-models",
    response_model=ApiResponse[list[RemoteModelInfo]],
)
@limiter.limit(settings.RATE_LIMIT_ENDPOINTS["provider"][0])
async def discover_provider_models(
    request: Request,
    name: str,
    db: DBSession = Depends(get_db_session),
    current_session: ChatSession = Depends(get_current_session),
) -> ApiResponse[list[dict[str, Any]]]:
    """List the upstream ``/models`` of a stored provider (auth-free projection).

    Uses the provider's stored ``auth_config.api_key`` and ``base_url`` to
    call the upstream. The constructed client is closed in a finally block
    inside ``discover_remote_models`` so connections never leak.

    Args:
        request: The FastAPI request object for rate limiting.
        name: Provider unique name.
        db: Request-scoped DB session.
        current_session: Authenticated chat session.

    Returns:
        Envelope carrying the projected ``RemoteModelInfo`` rows from the
        upstream ``GET /models`` call.

    Raises:
        HTTPException: 404 when missing, 422 when the provider type is in
            ``UNSUPPORTED_TYPES`` (e.g. ANTHROPIC), 502 when the upstream
            call fails.
    """
    provider = _get_provider(db, name)
    if provider is None:
        raise HTTPException(status_code=404, detail=f"provider '{name}' not found")

    try:
        rows = await discover_remote_models(provider)
        return ApiResponse.success([row.model_dump() for row in rows])
    except ValueError as exc:
        # Unsupported family — synchronous rejection, no network attempted.
        raise HTTPException(status_code=422, detail=str(exc)) from exc
    except Exception as exc:
        logger.exception("provider_discover_failed", name=name)
        raise HTTPException(
            status_code=502,
            detail=f"upstream call failed: {str(exc)[:300]}",
        ) from exc


# ---------------------------------------------------------------------------
# Model configs (nested under providers)
# ---------------------------------------------------------------------------


@router.get("/providers/{name}/models", response_model=ApiResponse[list[ModelConfigRead]])
@limiter.limit(settings.RATE_LIMIT_ENDPOINTS["model_config"][0])
async def list_provider_models(
    request: Request,
    name: str,
    db: DBSession = Depends(get_db_session),
    current_session: ChatSession = Depends(get_current_session),
) -> ApiResponse[list[dict[str, Any]]]:
    """List every live model config of a provider ordered by name.

    Args:
        request: The FastAPI request object for rate limiting.
        name: Provider unique name.
        db: Request-scoped DB session.
        current_session: Authenticated chat session.

    Returns:
        Envelope carrying the provider's model config rows.

    Raises:
        HTTPException: 404 when the provider does not exist or is deleted.
    """
    try:
        provider = _get_provider(db, name)
        if provider is None:
            raise HTTPException(status_code=404, detail=f"provider '{name}' not found")
        if provider.id is None:
            raise HTTPException(status_code=500, detail=f"provider '{name}' row has no primary key")
        return ApiResponse.success([_model_read(model, provider.name) for model in _list_models(db, provider.id)])
    except HTTPException:
        raise
    except Exception as exc:
        logger.exception("model_config_list_failed", provider=name)
        raise HTTPException(status_code=500, detail=str(exc)) from exc


@router.post("/providers/{name}/models", response_model=ApiResponse[ModelConfigRead], status_code=201)
@limiter.limit(settings.RATE_LIMIT_ENDPOINTS["model_config"][0])
async def create_provider_model(
    request: Request,
    name: str,
    payload: ModelConfigCreate,
    db: DBSession = Depends(get_db_session),
    current_session: ChatSession = Depends(get_current_session),
) -> ApiResponse[dict[str, Any]]:
    """Create a model config under a provider.

    Args:
        request: The FastAPI request object for rate limiting.
        name: Provider unique name.
        payload: The model definition.
        db: Request-scoped DB session.
        current_session: Authenticated chat session (audit only).

    Returns:
        Envelope carrying the persisted model config row.

    Raises:
        HTTPException: 404 when the provider is missing, 422 on a duplicate
            (provider, name) or (provider, model_id) pair.
    """
    try:
        provider = _get_provider(db, name)
        if provider is None:
            raise HTTPException(status_code=404, detail=f"provider '{name}' not found")
        if provider.id is None:
            raise HTTPException(status_code=500, detail=f"provider '{name}' row has no primary key")

        model = ModelConfig(
            provider_id=provider.id,
            name=payload.name,
            model_id=payload.model_id,
            context_size=payload.context_size,
            extra_params=payload.extra_params,
            enabled=payload.enabled,
            created_by=_creator(current_session),
        )
        db.add(model)
        try:
            db.commit()
        except IntegrityError as exc:
            db.rollback()
            logger.warning("model_config_create_conflict", provider=name, model=payload.name)
            raise HTTPException(
                status_code=422,
                detail=f"model '{payload.name}' already exists under provider '{name}' (name or model_id clash)",
            ) from exc
        db.refresh(model)
        logger.info("model_config_created", provider=name, model=payload.name)
        return ApiResponse.success(_model_read(model, provider.name), code=201)
    except HTTPException:
        raise
    except Exception as exc:
        logger.exception("model_config_create_failed", provider=name, model=payload.name)
        raise HTTPException(status_code=500, detail=str(exc)) from exc


@router.patch("/providers/{name}/models/{model}", response_model=ApiResponse[ModelConfigRead])
@limiter.limit(settings.RATE_LIMIT_ENDPOINTS["model_config"][0])
async def update_provider_model(
    request: Request,
    name: str,
    model: str,
    db: DBSession = Depends(get_db_session),
    current_session: ChatSession = Depends(get_current_session),
) -> ApiResponse[dict[str, Any]]:
    """Partially update a model config (name is immutable).

    Args:
        request: The FastAPI request object for rate limiting.
        name: Provider unique name.
        model: Model config display name.
        db: Request-scoped DB session.
        current_session: Authenticated chat session.

    Returns:
        Envelope carrying the updated model config row.

    Raises:
        HTTPException: 404 when missing, 422 on empty payload or explicit
            null on a NOT NULL field.
    """
    body = await _read_patch_body(request)
    payload = _validate_payload(ModelConfigUpdate, body)
    try:
        provider = _get_provider(db, name)
        if provider is None:
            raise HTTPException(status_code=404, detail=f"provider '{name}' not found")
        model_cfg = db.exec(
            select(ModelConfig).where(
                col(ModelConfig.provider_id) == provider.id,
                col(ModelConfig.name) == model,
                col(ModelConfig.deleted) == False,  # noqa: E712
            )
        ).first()
        if model_cfg is None:
            raise HTTPException(status_code=404, detail=f"model '{name}/{model}' not found")

        updates = payload.model_dump(exclude_unset=True)
        if not updates:
            raise HTTPException(status_code=422, detail="nothing to update")

        null_fields = sorted(
            field for field, value in updates.items() if value is None and field in _MODEL_NOT_NULL_PATCH_FIELDS
        )
        if null_fields:
            logger.warning("model_config_update_rejected_null", provider=name, model=model, fields=null_fields)
            raise HTTPException(
                status_code=422,
                detail=f"{', '.join(null_fields)}: null is not allowed; omit the field to keep it unchanged",
            )

        for field, value in updates.items():
            setattr(model_cfg, field, value)
        db.add(model_cfg)
        db.commit()
        db.refresh(model_cfg)
        logger.info("model_config_updated", provider=name, model=model)
        return ApiResponse.success(_model_read(model_cfg, provider.name))
    except HTTPException:
        raise
    except Exception as exc:
        logger.exception("model_config_update_failed", provider=name, model=model)
        raise HTTPException(status_code=500, detail=str(exc)) from exc


@router.delete("/providers/{name}/models/{model}", response_model=ApiResponse[None])
@limiter.limit(settings.RATE_LIMIT_ENDPOINTS["model_config"][0])
async def delete_provider_model(
    request: Request,
    name: str,
    model: str,
    hard: bool = Query(False, description="Physically delete the model config row (irreversible)"),
    x_confirm_hard_delete: str | None = Header(
        default=None,
        alias=HARD_DELETE_CONFIRM_HEADER,
        description="Required 'true' when hard=true; prevents accidental irreversible delete",
    ),
    db: DBSession = Depends(get_db_session),
    current_session: ChatSession = Depends(get_current_session),
) -> ApiResponse[None]:
    """Delete a model config: soft by default, hard when ``?hard=true`` is confirmed.

    Guards: the bootstrap-seeded ``default/default`` pair is undeletable,
    and a model still referenced by an AgentApp or SubAgentConfig ``model``
    field is rejected with 422. Hard delete requires the
    ``X-Confirm-Hard-Delete: true`` confirmation header.

    Args:
        request: The FastAPI request object for rate limiting.
        name: Provider unique name.
        model: Model config display name.
        hard: If true, physically delete the model config row.
        x_confirm_hard_delete: Header that must equal ``"true"`` when ``hard=true``.
        db: Request-scoped DB session.
        current_session: Authenticated chat session.

    Returns:
        Envelope with null data on successful deletion.

    Raises:
        HTTPException: 404 when missing, 422 when protected, referenced, or
            ``hard=true`` is sent without the confirmation header.
    """
    try:
        provider = _get_provider(db, name)
        if provider is None:
            raise HTTPException(status_code=404, detail=f"provider '{name}' not found")
        model_cfg = db.exec(
            select(ModelConfig).where(
                col(ModelConfig.provider_id) == provider.id,
                col(ModelConfig.name) == model,
                col(ModelConfig.deleted) == False,  # noqa: E712
            )
        ).first()
        if model_cfg is None:
            raise HTTPException(status_code=404, detail=f"model '{name}/{model}' not found")

        ref = f"{name}/{model}"
        if name == DEFAULT_PROVIDER_NAME and model == DEFAULT_MODEL_NAME:
            logger.warning("model_config_delete_rejected", ref=ref, reason="default_protected")
            raise HTTPException(status_code=422, detail=f"model '{DEFAULT_MODEL_REF}' cannot be deleted")

        owners = _referencing_owners(db, ref)
        if owners:
            logger.warning("model_config_delete_rejected", ref=ref, reason="referenced")
            raise HTTPException(
                status_code=422,
                detail=f"model '{ref}' is referenced by: {', '.join(owners)}",
            )

        if hard:
            if x_confirm_hard_delete != HARD_DELETE_CONFIRM_VALUE:
                logger.warning(
                    "model_config_hard_delete_rejected",
                    ref=ref,
                    reason="missing_confirm_header",
                    header_value=x_confirm_hard_delete,
                )
                raise HTTPException(
                    status_code=422,
                    detail=(
                        f"{HARD_DELETE_CONFIRM_HEADER} header must be '{HARD_DELETE_CONFIRM_VALUE}' "
                        f"when hard=true (model '{ref}')"
                    ),
                )
            db.delete(model_cfg)
            db.commit()
            logger.warning(
                "model_config_hard_deleted",
                ref=ref,
                actor=_creator(current_session),
            )
            return ApiResponse.success(None)

        model_cfg.deleted = True
        db.add(model_cfg)
        db.commit()
        logger.info("model_config_deleted", ref=ref)
        return ApiResponse.success(None)
    except HTTPException:
        raise
    except Exception as exc:
        logger.exception("model_config_delete_failed", provider=name, model=model)
        raise HTTPException(status_code=500, detail=str(exc)) from exc
