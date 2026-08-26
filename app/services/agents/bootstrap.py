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

The system default provider/model pair is seeded first by
``ensure_default_provider_and_model`` (insert-if-missing only — pre-existing
rows are never overwritten, protecting admin edits; the environment values
are a seed source, not a live config). Without an ``OPENAI_API_KEY`` the
seed is skipped entirely (warning logged; the next startup with a key seeds
it), and the agent-app fingerprint degrades to an empty model fingerprint.
Agent asset ``model`` fields reference ``"<provider>/<model>"`` pairs, so
the default pair must exist before any fingerprint is computed.
"""

import os
from typing import Any

from sqlalchemy import or_
from sqlalchemy.exc import IntegrityError
from sqlmodel import Session, col, select, update

from app.core.config import settings
from app.core.logging import logger
from app.core.prompts import load_static_system_prompt
from app.models.agent_assets import DEFAULT_AGENT_APP_ID, AgentApp
from app.models.provider import DEFAULT_MODEL_NAME, DEFAULT_MODEL_REF, DEFAULT_PROVIDER_NAME, ModelConfig, Provider
from app.models.session import Session as ChatSession
from app.services.agents import assembly, skills_store
from app.services.llm.llm_store import compute_model_config_hash

_DEFAULT_APP_NAME = "default"


async def ensure_default_provider_and_model(session: Session) -> tuple[Provider, ModelConfig] | None:
    """Idempotently provision the default provider and model pair from the env.

    Insert-if-missing only: pre-existing rows are returned untouched so
    admin edits (PATCH/DELETE + recreate) survive restarts. Seed sources:
    ``settings.OPENAI_API_KEY`` / ``OPENAI_BASE_URL`` env /
    ``settings.DEFAULT_LLM_MODEL`` / ``settings.DEFAULT_LLM_TEMPERATURE``
    (temperature lands in ``extra_params``; ``max_tokens`` is deliberately
    omitted so the process-level ``settings.MAX_TOKENS`` budget never
    freezes into a persisted row). An empty ``OPENAI_API_KEY`` skips the
    seed (warning logged) so the next startup with a configured key
    provisions the pair.

    Args:
        session: SQLModel database session.

    Returns:
        The default (provider, model config) row pair (created or
        pre-existing), or None when seeding was skipped because no API key
        is configured.

    Raises:
        RuntimeError: When the pair is still unavailable after the insert
            race recovery (should never happen).
    """
    provider = session.exec(
        select(Provider).where(col(Provider.name) == DEFAULT_PROVIDER_NAME, col(Provider.deleted) == False)  # noqa: E712
    ).first()

    if provider is None:
        if not settings.OPENAI_API_KEY:
            logger.warning("default_provider_seed_skipped_no_api_key", name=DEFAULT_PROVIDER_NAME)
            return None

        provider = Provider(
            name=DEFAULT_PROVIDER_NAME,
            type="OPENAI_COMPATIBLE",
            base_url=os.getenv("OPENAI_BASE_URL") or "",
            auth_config={"api_key": settings.OPENAI_API_KEY},
            created_by="bootstrap",
        )
        session.add(provider)
        try:
            session.commit()
            session.refresh(provider)
            logger.info("default_provider_created", name=provider.name)
        except IntegrityError:
            # Concurrent worker won the unique-name race: roll back the loser
            # row and adopt the already-persisted default provider.
            session.rollback()
            provider = session.exec(
                select(Provider).where(col(Provider.name) == DEFAULT_PROVIDER_NAME, col(Provider.deleted) == False)  # noqa: E712
            ).first()
            logger.info("default_provider_created_by_concurrent_worker", name=DEFAULT_PROVIDER_NAME)

    if provider is None:
        msg = "default provider unavailable after bootstrap (insert lost and re-query found nothing)"
        raise RuntimeError(msg)
    if provider.id is None:
        msg = "default provider row has no primary key after bootstrap"
        raise RuntimeError(msg)

    model = session.exec(
        select(ModelConfig).where(
            col(ModelConfig.provider_id) == provider.id,
            col(ModelConfig.name) == DEFAULT_MODEL_NAME,
            col(ModelConfig.deleted) == False,  # noqa: E712
        )
    ).first()

    if model is None:
        extra_params: dict[str, Any] = {}
        if settings.DEFAULT_LLM_TEMPERATURE is not None:
            extra_params["temperature"] = settings.DEFAULT_LLM_TEMPERATURE
        model = ModelConfig(
            provider_id=provider.id,
            name=DEFAULT_MODEL_NAME,
            model_id=settings.DEFAULT_LLM_MODEL,
            extra_params=extra_params,
            created_by="bootstrap",
        )
        session.add(model)
        try:
            session.commit()
            session.refresh(model)
            logger.info("default_model_created", ref=DEFAULT_MODEL_REF, model_id=model.model_id)
        except IntegrityError:
            # Concurrent worker won the unique (provider, name) race.
            session.rollback()
            model = session.exec(
                select(ModelConfig).where(
                    col(ModelConfig.provider_id) == provider.id,
                    col(ModelConfig.name) == DEFAULT_MODEL_NAME,
                    col(ModelConfig.deleted) == False,  # noqa: E712
                )
            ).first()
            logger.info("default_model_created_by_concurrent_worker", ref=DEFAULT_MODEL_REF)

    if model is None:
        msg = "default model config unavailable after bootstrap (insert lost and re-query found nothing)"
        raise RuntimeError(msg)
    return provider, model


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
    # The default provider/model pair must exist before any fingerprint
    # is computed (the default app resolves model=None to the default pair).
    # A skipped seed (no API key yet) degrades the model fingerprint to "".
    default_pair = await ensure_default_provider_and_model(session)
    model_fingerprint = (
        f"{DEFAULT_MODEL_REF}:{compute_model_config_hash(*default_pair)}" if default_pair is not None else ""
    )

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
        default_app.published_hash = assembly.compute_fingerprint(default_app, [], {}, "", model_fingerprint)
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
        default_app.published_hash = assembly.compute_fingerprint(default_app, [], {}, "", model_fingerprint)
        session.commit()
        logger.info("default_agent_app_prompt_migrated", app_id=default_app.id)

    _backfill_legacy_sessions(session, default_app)
    return default_app


async def ensure_all_agent_workspaces(session: Session) -> None:
    """Startup repair: every AgentApp's Agent layer + workspace hash are sound.

    G2 v3.3 (spec-g2-workspace §5.4), run once in the FastAPI lifespan:

    - draft apps keep their pending status and only get the empty skeleton
      directory (skipped count);
    - published apps with a missing/empty Agent layer are re-materialized
      from the Global layer (only when ``skill_names`` is non-empty);
    - active apps re-verify ``workspace_hash`` against the directory content
      and repair drift (warning ``agent_workspace_hash_drift``);
    - non-draft apps are then promoted to ``active``.

    A single app's failure is isolated (logged as
    ``agent_workspace_bootstrap_failed``) so the remaining apps still
    bootstrap. The whole pass commits once at the end.

    Args:
        session: SQLModel database session.
    """
    apps = list(session.exec(select(AgentApp)).all())
    active_count = 0
    skipped_count = 0

    for app in apps:
        try:
            agent_dir = skills_store._agent_skill_dir(app.id)  # noqa: SLF001 — same-package path helper

            if app.status == "draft":
                # Draft stays pending: only the empty skeleton directory exists.
                agent_dir.mkdir(parents=True, exist_ok=True)
                skipped_count += 1
                continue

            # Published/active: rebuild the Agent layer when it went missing.
            if not agent_dir.exists() or not any(agent_dir.iterdir()):
                if app.skill_names:
                    await skills_store.materialize_for_agent(
                        session,
                        app_id=app.id,
                        skill_names=list(app.skill_names),
                    )

            # Active apps re-verify the stored hash (directory-loss guard).
            if app.agent_workspace_status == "active":
                expected_hash = skills_store.compute_workspace_hash(agent_dir)
                if app.workspace_hash != expected_hash:
                    logger.warning(
                        "agent_workspace_hash_drift",
                        app_id=app.id,
                        stored=app.workspace_hash,
                        expected=expected_hash,
                    )
                    app.workspace_hash = expected_hash

            app.agent_workspace_status = "active"
            active_count += 1
        except Exception as exc:  # noqa: BLE001 — spec §5.4 single-app isolation
            # Single-app isolation: one failure never blocks the rest.
            logger.exception(
                "agent_workspace_bootstrap_failed",
                app_id=app.id,
                error=str(exc),
            )
            continue

    session.commit()
    logger.info(
        "agent_workspace_bootstrap_completed",
        total=len(apps),
        active=active_count,
        skipped=skipped_count,
    )
