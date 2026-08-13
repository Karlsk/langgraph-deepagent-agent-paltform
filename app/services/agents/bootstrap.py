"""Bootstrap of the system-default AgentApp and legacy session backfill.

Data-consistency decision: ``sessions.agent_app_id`` stores ``str(AgentApp.id)``.
Rows created before the AgentApp feature existed were backfilled by alembic
with the ``"system-default"`` placeholder. ``ensure_default_agent_app`` runs
idempotently at startup:

1. Create the ``name="default"`` AgentApp (published, deepagents engine,
   **static** system prompt template, no tool whitelist) when it does not
   exist yet, snapshotting its config as ``published_hash`` via
   ``assembly.compute_fingerprint``. The persisted prompt is the static base
   template (``prompts.load_static_system_prompt``): username context,
   long-term memory and the current date/time are injected per model call by
   ``assembly.MemoryMiddleware`` so first-startup values never get frozen
   into the row. A legacy row still holding a frozen rendered prompt is
   migrated in place (content differs from the template -> UPDATE + new
   fingerprint).
2. Rewrite every session row whose ``agent_app_id`` is NULL or the
   ``"system-default"`` placeholder to ``str(default_app.id)`` with a single
   UPDATE (idempotent: repeat runs affect zero rows).

Multi-worker safety: the insert catches ``IntegrityError`` (unique-name race
between concurrent workers), rolls back and re-queries the winning row.

The system default ``LlmConfig`` row is seeded first by
``ensure_default_llm_config`` (insert-if-missing only — a pre-existing row
is never overwritten, protecting admin edits; the environment values are a
seed source, not a live config). Without an ``OPENAI_API_KEY`` the seed is
skipped entirely (warning logged; the next startup with a key seeds it),
and the agent-app fingerprint degrades to an empty llm fingerprint. Agent
asset ``model`` fields reference these rows, so the default config must
exist before any fingerprint is computed.
"""

import os
from typing import Any

from sqlalchemy import or_
from sqlalchemy.exc import IntegrityError
from sqlmodel import Session, col, select, update

from app.core.config import settings
from app.core.logging import logger
from app.core.prompts import load_static_system_prompt
from app.models.agent_assets import DEFAULT_AGENT_APP_ID, DEFAULT_LLM_CONFIG_NAME, AgentApp, LlmConfig
from app.models.session import Session as ChatSession
from app.services.agents import assembly
from app.services.llm.llm_store import compute_llm_config_hash

_DEFAULT_APP_NAME = "default"


async def ensure_default_llm_config(session: Session) -> LlmConfig | None:
    """Idempotently provision the ``name="default"`` LlmConfig from the env.

    Insert-if-missing only: a pre-existing row is returned untouched so
    admin edits (PATCH/DELETE + recreate) survive restarts. Seed sources:
    ``settings.OPENAI_API_KEY`` / ``OPENAI_BASE_URL`` env /
    ``settings.DEFAULT_LLM_MODEL`` / ``settings.DEFAULT_LLM_TEMPERATURE``.
    ``max_tokens`` is seeded as None on purpose: the process-level
    ``settings.MAX_TOKENS`` budget must never freeze into a persisted row
    (the provider default applies until an admin sets one). An empty
    ``OPENAI_API_KEY`` skips the seed (warning logged) so the next startup
    with a configured key provisions the row.

    Args:
        session: SQLModel database session.

    Returns:
        The ``name="default"`` LlmConfig row (created or pre-existing), or
        None when seeding was skipped because no API key is configured.

    Raises:
        RuntimeError: When the row is still unavailable after the insert
            race recovery (should never happen).
    """
    existing = session.get(LlmConfig, DEFAULT_LLM_CONFIG_NAME)
    if existing is not None:
        logger.debug("default_llm_config_existing", name=existing.name)
        return existing

    if not settings.OPENAI_API_KEY:
        logger.warning("default_llm_config_seed_skipped_no_api_key", name=DEFAULT_LLM_CONFIG_NAME)
        return None

    default_cfg = LlmConfig(
        name=DEFAULT_LLM_CONFIG_NAME,
        model_name=settings.DEFAULT_LLM_MODEL,
        api_key=settings.OPENAI_API_KEY,
        base_url=os.getenv("OPENAI_BASE_URL"),
        temperature=settings.DEFAULT_LLM_TEMPERATURE,
        max_tokens=None,
        description="System default LLM config seeded from environment variables",
        content_hash="",
    )
    default_cfg.content_hash = compute_llm_config_hash(default_cfg)
    session.add(default_cfg)
    try:
        session.commit()
        session.refresh(default_cfg)
        logger.info("default_llm_config_created", name=default_cfg.name, model_name=default_cfg.model_name)
    except IntegrityError:
        # Concurrent worker won the unique-name race: roll back the loser
        # row and adopt the already-persisted default config.
        session.rollback()
        default_cfg = session.get(LlmConfig, DEFAULT_LLM_CONFIG_NAME)
        logger.info("default_llm_config_created_by_concurrent_worker", name=DEFAULT_LLM_CONFIG_NAME)

    if default_cfg is None:
        msg = "default llm config unavailable after bootstrap (insert lost and re-query found nothing)"
        raise RuntimeError(msg)
    return default_cfg


def _backfill_legacy_sessions(session: Session, default_app: AgentApp) -> None:
    """Rewrite NULL / placeholder agent_app_id rows to the default app's id.

    Args:
        session: SQLModel database session.
        default_app: The system default AgentApp row (must have its id).
    """
    statement = (
        update(ChatSession)
        .where(or_(col(ChatSession.agent_app_id).is_(None), col(ChatSession.agent_app_id) == DEFAULT_AGENT_APP_ID))
        .values(agent_app_id=str(default_app.id))
    )
    result: Any = session.exec(statement)
    session.commit()
    logger.info(
        "sessions_backfilled_agent_app_id",
        default_app_id=default_app.id,
        rowcount=getattr(result, "rowcount", None),
    )


async def ensure_default_agent_app(session: Session) -> AgentApp:
    """Idempotently provision the system default AgentApp and backfill sessions.

    Args:
        session: SQLModel database session.

    Returns:
        The ``name="default"`` AgentApp row (created or pre-existing), with
        its persisted integer id populated.
    """
    # The default LlmConfig must exist before any fingerprint is computed
    # (the default app resolves model=None to the default config). A skipped
    # seed (no API key yet) degrades the llm fingerprint to "".
    default_llm = await ensure_default_llm_config(session)
    llm_fingerprint = f"{default_llm.name}:{default_llm.content_hash}" if default_llm is not None else ""

    default_app = session.exec(select(AgentApp).where(col(AgentApp.name) == _DEFAULT_APP_NAME)).first()

    if default_app is None:
        static_prompt = load_static_system_prompt()
        default_app = AgentApp(
            name=_DEFAULT_APP_NAME,
            system_prompt=static_prompt,
            allowed_tools=None,
            engine="deepagents",
            status="published",
        )
        default_app.published_hash = assembly.compute_fingerprint(default_app, [], {}, "", llm_fingerprint)
        session.add(default_app)
        try:
            session.commit()
            session.refresh(default_app)
            logger.info("default_agent_app_created", app_id=default_app.id, published_hash=default_app.published_hash)
        except IntegrityError:
            # Concurrent worker won the unique-name race: roll back the loser
            # row and adopt the already-persisted default app.
            session.rollback()
            default_app = session.exec(select(AgentApp).where(col(AgentApp.name) == _DEFAULT_APP_NAME)).first()
            logger.info("default_agent_app_created_by_concurrent_worker", app_id=getattr(default_app, "id", None))
    else:
        logger.debug("default_agent_app_existing", app_id=default_app.id)

    if default_app is None:
        msg = "default agent app unavailable after bootstrap (insert lost and re-query found nothing)"
        raise RuntimeError(msg)

    expected_prompt = load_static_system_prompt()
    if default_app.system_prompt != expected_prompt:
        # Legacy row holds a prompt rendered (frozen) at first startup; migrate
        # it to the static template so dynamic segments are injected per turn.
        default_app.system_prompt = expected_prompt
        default_app.published_hash = assembly.compute_fingerprint(default_app, [], {}, "", llm_fingerprint)
        session.commit()
        logger.info("default_agent_app_prompt_migrated", app_id=default_app.id)

    _backfill_legacy_sessions(session, default_app)
    return default_app
