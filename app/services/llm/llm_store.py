"""DB-backed LLM configuration resolution for the agent asset chain.

This module is the resolution seam between persisted ``LlmConfig`` rows and
the LangChain chat models used by ``assembly`` / ``test_runner``. The legacy
``LLMRegistry`` stays untouched: it continues to serve the system-level call
sites (session naming, skill drafts, LLMService circular fallback, evals),
while every AgentApp/SubAgent ``model`` field now references a ``LlmConfig``
name (NULL resolves to ``DEFAULT_LLM_CONFIG_NAME``).

Security note: ``api_key`` values are stored in plaintext by product
decision, but they must never appear in logs or API responses — structured
log events here only carry the config name / model_name / base_url.
"""

import hashlib
import json
from typing import Any

from langchain_openai import ChatOpenAI
from pydantic import SecretStr
from sqlmodel import Session, select

from app.core.config import settings
from app.core.logging import logger
from app.models.agent_assets import DEFAULT_LLM_CONFIG_NAME, LlmConfig

# Every effective field (including api_key) feeds the content hash so key
# rotation / endpoint changes always drift the compile fingerprint.
_HASH_FIELDS = ("model_name", "api_key", "base_url", "temperature", "max_tokens", "enabled", "description")


def compute_llm_config_hash(cfg: LlmConfig) -> str:
    """Compute the content hash over every effective field of a config.

    Args:
        cfg: The (possibly unpersisted) LLM configuration row.

    Returns:
        Hex sha256 over the canonical (sorted-keys, compact) JSON payload.
    """
    payload = {field: getattr(cfg, field) for field in _HASH_FIELDS}
    canonical = json.dumps(payload, sort_keys=True, separators=(",", ":"), ensure_ascii=False, default=str)
    return hashlib.sha256(canonical.encode("utf-8")).hexdigest()


def build_chat_model(cfg: LlmConfig) -> ChatOpenAI:
    """Build a fresh ChatOpenAI client from a persisted LlmConfig row.

    Optional fields (base_url/temperature/max_tokens) are only passed when
    set, so a None ``base_url`` keeps the SDK's environment fallback chain
    intact. Parameter mapping decisions:

    - ``cfg.max_tokens`` is forwarded as ``max_completion_tokens`` — OpenAI's
      unified token-budget parameter; the reasoning model family rejects the
      legacy ``max_tokens``.
    - ``max_retries`` is restored from ``settings.MAX_LLM_CALL_RETRIES`` so
      transient provider errors retry again (partial compensation for the
      tenacity wrapper the legacy chat path used).

    Instances are never cached: the assembly compile-cache LRU already
    covers the hot path.

    Args:
        cfg: The resolved LLM configuration row.

    Returns:
        A newly constructed ChatOpenAI instance.
    """
    kwargs: dict[str, Any] = {
        "model": cfg.model_name,
        "api_key": SecretStr(cfg.api_key),
        "max_retries": settings.MAX_LLM_CALL_RETRIES,
    }
    if cfg.base_url is not None:
        kwargs["base_url"] = cfg.base_url
    if cfg.temperature is not None:
        kwargs["temperature"] = cfg.temperature
    if cfg.max_tokens is not None:
        kwargs["max_completion_tokens"] = cfg.max_tokens

    logger.debug("llm_chat_model_built", name=cfg.name, model_name=cfg.model_name, base_url=cfg.base_url)
    return ChatOpenAI(**kwargs)


def load_llm_config(session: Session, name: str | None) -> LlmConfig:
    """Resolve an LlmConfig reference name to its persisted row.

    ``None`` resolves to ``DEFAULT_LLM_CONFIG_NAME``. No lazy seeding: the
    bootstrap path guarantees the default row exists, so a missing or
    disabled row is a configuration error surfaced fail-fast.

    Args:
        session: SQLModel database session.
        name: LlmConfig reference name (None = default config).

    Returns:
        The enabled LlmConfig row.

    Raises:
        ValueError: When the row is missing or disabled; the message lists
            every enabled config name (registry-style listing).
    """
    config_name = name or DEFAULT_LLM_CONFIG_NAME
    cfg = session.get(LlmConfig, config_name)
    if cfg is not None and cfg.enabled:
        return cfg

    rows = session.exec(select(LlmConfig)).all()
    available = ", ".join(sorted(row.name for row in rows if row.enabled))
    raise ValueError(f"llm config '{config_name}' not found or disabled. available configs: {available}")
