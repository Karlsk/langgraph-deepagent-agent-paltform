"""DB-backed model config resolution for the agent asset chain.

This module is the resolution seam between persisted ``Provider`` /
``ModelConfig`` rows and the LangChain chat models used by ``assembly`` /
``test_runner``. The legacy ``LLMRegistry`` stays untouched: it continues to
serve the system-level call sites (session naming, skill drafts, LLMService
circular fallback, evals), while every AgentApp/SubAgent ``model`` field now
references a model config as ``"<provider name>/<model name>"`` (NULL
resolves to ``DEFAULT_MODEL_REF``).

Security note: ``auth_config`` values (including ``api_key``) are stored in
plaintext by product decision, but they must never appear in logs or API
responses — structured log events here only carry provider/model names and
the base_url.
"""

import hashlib
import json

from langchain_openai import ChatOpenAI
from pydantic import SecretStr
from sqlmodel import Session, col, select

from app.core.config import settings
from app.core.logging import logger
from app.models.provider import DEFAULT_MODEL_REF, ModelConfig, Provider, ProviderHealth

# Every effective field (including auth_config secrets) feeds the content
# hash so key rotation / endpoint changes always drift the compile
# fingerprint.
_PROVIDER_HASH_FIELDS = ("type", "base_url", "auth_config", "enabled")
_MODEL_HASH_FIELDS = ("name", "model_id", "context_size", "extra_params", "enabled")


def parse_model_ref(reference: str) -> tuple[str, str]:
    """Split a ``provider/model`` reference into its two name segments.

    Args:
        reference: Model reference string.

    Returns:
        Tuple of (provider name, model name).

    Raises:
        ValueError: When the reference lacks exactly one non-empty segment
            on each side of the first slash.
    """
    provider_name, sep, model_name = reference.partition("/")
    if not sep or not provider_name or not model_name:
        raise ValueError(f"invalid model reference '{reference}': expected '<provider>/<model>'")
    return provider_name, model_name


def compute_model_config_hash(provider: Provider, model: ModelConfig) -> str:
    """Compute the content hash over every effective field of a model config.

    Args:
        provider: The owning provider row (endpoint + auth material).
        model: The model config row.

    Returns:
        Hex sha256 over the canonical (sorted-keys, compact) JSON payload.
    """
    payload = {
        **{f"provider.{field}": getattr(provider, field) for field in _PROVIDER_HASH_FIELDS},
        **{f"model.{field}": getattr(model, field) for field in _MODEL_HASH_FIELDS},
    }
    canonical = json.dumps(payload, sort_keys=True, separators=(",", ":"), ensure_ascii=False, default=str)
    return hashlib.sha256(canonical.encode("utf-8")).hexdigest()


def build_chat_model(provider: Provider, model: ModelConfig) -> ChatOpenAI:
    """Build a fresh ChatOpenAI client from a resolved provider/model pair.

    Every provider type currently resolves through the OpenAI-compatible
    path (``type`` only distinguishes the auth_config shape and the UI).
    An empty ``base_url`` keeps the SDK's environment fallback chain intact.
    Parameter mapping decisions:

    - ``extra_params["max_tokens"]`` is forwarded as
      ``max_completion_tokens`` — OpenAI's unified token-budget parameter;
      the reasoning model family rejects the legacy ``max_tokens``.
    - ``max_retries`` is restored from ``settings.MAX_LLM_CALL_RETRIES`` so
      transient provider errors retry again (partial compensation for the
      tenacity wrapper the legacy chat path used).

    Instances are never cached: the assembly compile-cache LRU already
    covers the hot path.

    Args:
        provider: The resolved provider row (endpoint + auth material).
        model: The resolved model config row.

    Returns:
        A newly constructed ChatOpenAI instance.
    """
    extra = model.extra_params or {}
    kwargs: dict[str, object] = {
        "model": model.model_id,
        "api_key": SecretStr(str(provider.auth_config.get("api_key", ""))),
        "max_retries": settings.MAX_LLM_CALL_RETRIES,
    }
    if provider.base_url:
        kwargs["base_url"] = provider.base_url
    if extra.get("temperature") is not None:
        kwargs["temperature"] = extra["temperature"]
    if extra.get("max_tokens") is not None:
        kwargs["max_completion_tokens"] = extra["max_tokens"]

    logger.debug(
        "llm_chat_model_built",
        provider=provider.name,
        model=model.name,
        model_id=model.model_id,
        base_url=provider.base_url,
    )
    return ChatOpenAI(**kwargs)  # pyright: ignore[reportArgumentType]


def _available_refs(session: Session) -> str:
    """List every enabled, non-deleted ``provider/model`` reference."""
    providers = {
        row.id: row.name
        for row in session.exec(select(Provider).where(col(Provider.deleted) == False)).all()  # noqa: E712
    }
    models = session.exec(select(ModelConfig).where(col(ModelConfig.deleted) == False)).all()  # noqa: E712
    refs = sorted(
        f"{providers[row.provider_id]}/{row.name}" for row in models if row.enabled and row.provider_id in providers
    )
    return ", ".join(refs)


def load_model_config(session: Session, reference: str | None) -> tuple[Provider, ModelConfig]:
    """Resolve a ``provider/model`` reference to its persisted rows.

    ``None`` resolves to ``DEFAULT_MODEL_REF``. No lazy seeding: the
    bootstrap path guarantees the default provider/model pair exists, so a
    missing or disabled pair is a configuration error surfaced fail-fast.

    Args:
        session: SQLModel database session.
        reference: Model reference (``provider/model``; None = default pair).

    Returns:
        The enabled (provider, model config) row pair.

    Raises:
        ValueError: When the reference is malformed or the pair is missing,
            soft-deleted or disabled; the message lists every available
            reference (registry-style listing).
    """
    ref = reference or DEFAULT_MODEL_REF
    provider_name, model_name = parse_model_ref(ref)

    provider = session.exec(
        select(Provider).where(col(Provider.name) == provider_name, col(Provider.deleted) == False)  # noqa: E712
    ).first()
    model = (
        session.exec(
            select(ModelConfig).where(
                col(ModelConfig.provider_id) == provider.id,
                col(ModelConfig.name) == model_name,
                col(ModelConfig.deleted) == False,  # noqa: E712
            )
        ).first()
        if provider is not None
        else None
    )
    if provider is None or model is None or not provider.enabled or not model.enabled:
        available = _available_refs(session)
        raise ValueError(f"model config '{ref}' not found or disabled. available models: {available}")
    return provider, model


# ---------------------------------------------------------------------------
# Hard-delete escape hatch + trash read helpers
# ---------------------------------------------------------------------------


def hard_delete_provider(db: Session, provider: Provider) -> dict[str, int]:
    """Physically delete a provider row plus every owned model_config and the health row.

    All deletes happen in one transaction. Callers MUST have already verified
    that the provider is allowed to be deleted (default protection + reference
    check); this helper does not repeat those guards.

    Args:
        db: SQLModel database session.
        provider: The provider row to delete (already validated).

    Returns:
        Counts of the rows actually removed: ``{"models": int, "health": int}``
        where ``health`` is 1 if a health row existed and 0 otherwise. The
        counts feed the audit log event emitted by the caller.
    """
    models = list(db.exec(select(ModelConfig).where(col(ModelConfig.provider_id) == provider.id)).all())
    for model in models:
        db.delete(model)
    health = db.exec(select(ProviderHealth).where(col(ProviderHealth.provider_id) == provider.id)).first()
    if health is not None:
        db.delete(health)
    db.delete(provider)
    db.commit()
    return {"models": len(models), "health": 1 if health is not None else 0}


def list_deleted_providers(db: Session) -> list[Provider]:
    """List every soft-deleted provider row ordered by ``updated_at`` desc.

    The freshest tombstone comes first so operators can triage the most
    recent soft-delete first.
    """
    return list(
        db.exec(
            select(Provider)
            .where(col(Provider.deleted) == True)  # noqa: E712 — SQLAlchemy == False restriction
            .order_by(col(Provider.updated_at).desc())
        ).all()
    )


def get_deleted_provider(db: Session, name: str) -> Provider | None:
    """Return the soft-deleted provider named ``name``, or ``None`` when absent.

    Under the unique-name constraint there is at most one row per name;
    ``ORDER BY updated_at DESC LIMIT 1`` is a defensive safety net.
    """
    return db.exec(
        select(Provider)
        .where(col(Provider.name) == name, col(Provider.deleted) == True)  # noqa: E712
        .order_by(col(Provider.updated_at).desc())
        .limit(1)
    ).first()


def list_models_under_deleted_provider(db: Session, name: str) -> list[ModelConfig] | None:
    """Return every model config of the soft-deleted provider named ``name``.

    Returns ``None`` when no soft-deleted provider with that name exists so the
    caller can emit a 404. The list preserves the original ``name`` ordering so
    the trash view is stable.
    """
    provider = get_deleted_provider(db, name)
    if provider is None or provider.id is None:
        return None
    return list(
        db.exec(
            select(ModelConfig).where(col(ModelConfig.provider_id) == provider.id).order_by(col(ModelConfig.name))
        ).all()
    )
