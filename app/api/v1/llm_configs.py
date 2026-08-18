"""Admin API for LLM configuration assets (CRUD with api_key masking).

Phase-1 scope: all assets are globally shared (no per-user ownership checks);
every endpoint only authenticates via ``get_current_session`` and records the
creator in the audit-only ``created_by`` field.

Error semantics: missing resources return 404; name collisions, validation
failures and reference-protection violations return 422; unexpected failures
return 500 after ``logger.exception``. The raw ``api_key`` is physically
excluded from every response; only the masked projection is ever returned.
"""

from collections.abc import Mapping
from typing import Any

from fastapi import APIRouter, Depends, HTTPException, Query, Request
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
from app.models.agent_assets import DEFAULT_LLM_CONFIG_NAME, AgentApp, LlmConfig, SubAgentConfig
from app.models.session import Session as ChatSession
from app.schemas.agent_apps import LlmConfigCreate, LlmConfigRead, LlmConfigUpdate
from app.schemas.base import ApiResponse, PageResult
from app.services.llm.llm_store import compute_llm_config_hash

router = APIRouter()

# LlmConfig PATCH fields backed by NOT NULL columns: explicit JSON null is
# rejected (omit the field to keep it unchanged); base_url/temperature/
# max_tokens keep their explicit-null clear semantics.
_LLM_CONFIG_NOT_NULL_PATCH_FIELDS = frozenset({"model_name", "api_key", "enabled", "description"})


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


def _llm_config_read(cfg: LlmConfig) -> dict[str, Any]:
    """Project an LlmConfig row into its API response form.

    The raw ``api_key`` is physically excluded; only the masked projection
    ``api_key_masked`` is ever returned.
    """
    return {
        "name": cfg.name,
        "model_name": cfg.model_name,
        "api_key_masked": _mask_api_key(cfg.api_key),
        "base_url": cfg.base_url,
        "temperature": cfg.temperature,
        "max_tokens": cfg.max_tokens,
        "enabled": cfg.enabled,
        "description": cfg.description,
        "content_hash": cfg.content_hash,
        "created_by": cfg.created_by,
    }


def _llm_fingerprint(llm_configs: Mapping[str, LlmConfig]) -> str:
    """Fingerprint a set of LlmConfig rows (publish hash input)."""
    return "|".join(sorted(f"{cfg.name}:{cfg.content_hash}" for cfg in llm_configs.values()))


# ---------------------------------------------------------------------------
# LLM configs
# ---------------------------------------------------------------------------


@router.get("/llm-configs", response_model=ApiResponse[list[LlmConfigRead]])
@limiter.limit(settings.RATE_LIMIT_ENDPOINTS["llm_config"][0])
async def list_llm_configs(
    request: Request,
    db: DBSession = Depends(get_db_session),
    current_session: ChatSession = Depends(get_current_session),
) -> ApiResponse[list[dict[str, Any]]]:
    """List every stored LLM configuration (api_key always masked).

    Args:
        request: The FastAPI request object for rate limiting.
        db: Request-scoped DB session.
        current_session: Authenticated chat session.

    Returns:
        Envelope carrying all LLM config rows (masked projections) ordered by name.
    """
    try:
        rows = db.exec(select(LlmConfig).order_by(col(LlmConfig.name))).all()
        return ApiResponse.success([_llm_config_read(row) for row in rows])
    except Exception as exc:
        logger.exception("llm_config_list_failed")
        raise HTTPException(status_code=500, detail=str(exc)) from exc


@router.get("/llm-configs/page", response_model=ApiResponse[PageResult[LlmConfigRead]])
@limiter.limit(settings.RATE_LIMIT_ENDPOINTS["llm_config"][0])
async def list_llm_configs_page(
    request: Request,
    page: int = Query(1, ge=1),
    page_size: int = Query(10, ge=1, le=100, alias="pageSize"),
    keyword: str | None = Query(None, max_length=200),
    db: DBSession = Depends(get_db_session),
    current_session: ChatSession = Depends(get_current_session),
) -> ApiResponse[PageResult[dict[str, Any]]]:
    """List LLM configurations with server-side pagination (api_key masked).

    Args:
        request: The FastAPI request object for rate limiting.
        page: 1-based page number.
        page_size: Rows per page (exposed as ``pageSize``).
        keyword: Optional case-insensitive substring matched against name.
        db: Request-scoped DB session.
        current_session: Authenticated chat session.

    Returns:
        Envelope carrying a PageResult of masked LLM config projections
        ordered by name.
    """
    try:
        paged = paginate_by_name(
            db,
            LlmConfig,
            page=page,
            page_size=page_size,
            keyword=keyword,
            order_by=col(LlmConfig.name),
        )
        masked = PageResult[dict[str, Any]](
            items=[_llm_config_read(row) for row in paged.items],
            total=paged.total,
            page=paged.page,
            page_size=paged.page_size,
        )
        return ApiResponse.success(masked)
    except Exception as exc:
        logger.exception("llm_config_list_page_failed")
        raise HTTPException(status_code=500, detail=str(exc)) from exc


@router.post("/llm-configs", response_model=ApiResponse[LlmConfigRead], status_code=201)
@limiter.limit(settings.RATE_LIMIT_ENDPOINTS["llm_config"][0])
async def create_llm_config(
    request: Request,
    payload: LlmConfigCreate,
    db: DBSession = Depends(get_db_session),
    current_session: ChatSession = Depends(get_current_session),
) -> ApiResponse[dict[str, Any]]:
    """Create an LLM configuration referenced by agent asset ``model`` fields.

    Args:
        request: The FastAPI request object for rate limiting.
        payload: The LLM connection definition.
        db: Request-scoped DB session.
        current_session: Authenticated chat session (audit only).

    Returns:
        Envelope carrying the persisted LLM config row (masked projection).

    Raises:
        HTTPException: 422 when the name is already taken (pre-check or a
            lost unique-name race at commit time).
    """
    try:
        if db.get(LlmConfig, payload.name) is not None:
            logger.warning("llm_config_create_conflict", name=payload.name)
            raise HTTPException(status_code=422, detail=f"llm config '{payload.name}' already exists")

        config = LlmConfig(
            name=payload.name,
            model_name=payload.model_name,
            api_key=payload.api_key,
            base_url=payload.base_url,
            temperature=payload.temperature,
            max_tokens=payload.max_tokens,
            enabled=payload.enabled,
            description=payload.description,
            content_hash="",
            created_by=_creator(current_session),
        )
        config.content_hash = compute_llm_config_hash(config)
        db.add(config)
        try:
            db.commit()
        except IntegrityError as exc:
            # Concurrent create won the unique-name race between the pre-check
            # and the insert: degrade to the same 422 the pre-check returns.
            db.rollback()
            logger.warning("llm_config_create_conflict", name=payload.name)
            raise HTTPException(status_code=422, detail=f"llm config '{payload.name}' already exists") from exc
        db.refresh(config)
        logger.info("llm_config_created", name=payload.name, created_by=config.created_by)
        return ApiResponse.success(_llm_config_read(config), code=201)
    except HTTPException:
        raise
    except Exception as exc:
        logger.exception("llm_config_create_failed", name=payload.name)
        raise HTTPException(status_code=500, detail=str(exc)) from exc


@router.get("/llm-configs/{name}", response_model=ApiResponse[LlmConfigRead])
@limiter.limit(settings.RATE_LIMIT_ENDPOINTS["llm_config"][0])
async def get_llm_config(
    request: Request,
    name: str,
    db: DBSession = Depends(get_db_session),
    current_session: ChatSession = Depends(get_current_session),
) -> ApiResponse[dict[str, Any]]:
    """Fetch one LLM configuration by name (api_key always masked).

    Args:
        request: The FastAPI request object for rate limiting.
        name: LLM config primary key.
        db: Request-scoped DB session.
        current_session: Authenticated chat session.

    Returns:
        Envelope carrying the matching LLM config row (masked projection).

    Raises:
        HTTPException: 404 when the LLM config does not exist.
    """
    try:
        config = db.get(LlmConfig, name)
        if config is None:
            raise HTTPException(status_code=404, detail=f"llm config '{name}' not found")
        return ApiResponse.success(_llm_config_read(config))
    except HTTPException:
        raise
    except Exception as exc:
        logger.exception("llm_config_read_failed", name=name)
        raise HTTPException(status_code=500, detail=str(exc)) from exc


@router.patch("/llm-configs/{name}", response_model=ApiResponse[LlmConfigRead])
@limiter.limit(settings.RATE_LIMIT_ENDPOINTS["llm_config"][0])
async def update_llm_config(
    request: Request,
    name: str,
    db: DBSession = Depends(get_db_session),
    current_session: ChatSession = Depends(get_current_session),
) -> ApiResponse[dict[str, Any]]:
    """Partially update an LLM config (name is immutable).

    Omitting ``api_key`` keeps the stored key unchanged. Content edits only
    drift the compile fingerprint: published apps are never demoted.

    Args:
        request: The FastAPI request object for rate limiting.
        name: LLM config primary key.
        db: Request-scoped DB session.
        current_session: Authenticated chat session.

    Returns:
        Envelope carrying the updated LLM config row (masked projection)
        with refreshed hash.

    Raises:
        HTTPException: 404 when missing, 422 on name change, empty payload
            or explicit null on a NOT NULL field.
    """
    body = await _read_patch_body(request)
    payload = _validate_payload(LlmConfigUpdate, body)
    try:
        config = db.get(LlmConfig, name)
        if config is None:
            raise HTTPException(status_code=404, detail=f"llm config '{name}' not found")

        updates = payload.model_dump(exclude_unset=True)
        if not updates:
            raise HTTPException(status_code=422, detail="nothing to update")

        null_fields = sorted(
            field for field, value in updates.items() if value is None and field in _LLM_CONFIG_NOT_NULL_PATCH_FIELDS
        )
        if null_fields:
            logger.warning("llm_config_update_rejected_null", name=name, fields=null_fields)
            raise HTTPException(
                status_code=422,
                detail=f"{', '.join(null_fields)}: null is not allowed; omit the field to keep it unchanged",
            )

        for field, value in updates.items():
            setattr(config, field, value)
        config.content_hash = compute_llm_config_hash(config)
        db.add(config)
        db.commit()
        db.refresh(config)
        logger.info("llm_config_updated", name=name)
        return ApiResponse.success(_llm_config_read(config))
    except HTTPException:
        raise
    except Exception as exc:
        logger.exception("llm_config_update_failed", name=name)
        raise HTTPException(status_code=500, detail=str(exc)) from exc


@router.delete("/llm-configs/{name}", response_model=ApiResponse[None])
@limiter.limit(settings.RATE_LIMIT_ENDPOINTS["llm_config"][0])
async def delete_llm_config(
    request: Request,
    name: str,
    db: DBSession = Depends(get_db_session),
    current_session: ChatSession = Depends(get_current_session),
) -> ApiResponse[None]:
    """Delete an LLM configuration.

    Guards: the bootstrap-seeded ``default`` config is undeletable, and any
    config still referenced by an AgentApp or SubAgentConfig ``model`` field
    (explicitly or via the NULL->default resolution) is rejected with 422.

    Args:
        request: The FastAPI request object for rate limiting.
        name: LLM config primary key.
        db: Request-scoped DB session.
        current_session: Authenticated chat session.

    Returns:
        Envelope with null data on successful deletion.

    Raises:
        HTTPException: 404 when missing, 422 when protected or referenced.
    """
    try:
        config = db.get(LlmConfig, name)
        if config is None:
            raise HTTPException(status_code=404, detail=f"llm config '{name}' not found")
        if name == DEFAULT_LLM_CONFIG_NAME:
            logger.warning("llm_config_delete_rejected", name=name, reason="default_protected")
            raise HTTPException(status_code=422, detail=f"llm config '{DEFAULT_LLM_CONFIG_NAME}' cannot be deleted")

        referencing_apps = db.exec(select(AgentApp).where(col(AgentApp.model) == name)).all()
        referencing_subagents = db.exec(select(SubAgentConfig).where(col(SubAgentConfig.model) == name)).all()
        if referencing_apps or referencing_subagents:
            owners = sorted(
                [f"agent_app:{row.name}" for row in referencing_apps]
                + [f"subagent:{row.name}" for row in referencing_subagents]
            )
            logger.warning("llm_config_delete_rejected", name=name, reason="referenced")
            raise HTTPException(
                status_code=422,
                detail=f"llm config '{name}' is referenced by: {', '.join(owners)}",
            )

        db.delete(config)
        db.commit()
        logger.info("llm_config_deleted", name=name)
        return ApiResponse.success(None)
    except HTTPException:
        raise
    except Exception as exc:
        logger.exception("llm_config_delete_failed", name=name)
        raise HTTPException(status_code=500, detail=str(exc)) from exc
