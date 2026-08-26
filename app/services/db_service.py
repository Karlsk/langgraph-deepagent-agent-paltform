"""Single-table CRUD helpers for the user-agent-app association store (G2).

Service-layer counterpart of spec-g2-workspace v3.3 §9.5: idempotent
association lookup/creation, plain lookup (lazy-check skip signal) and the
publish/PATCH cache invalidation of ``last_synced_workspace_hash`` (spec §5.2).
Pure data access — no business orchestration, no filesystem access.
"""

from sqlmodel import Session, select

from app.core.logging import logger
from app.models.agent_assets import AgentApp, UserAgentAppAssociation


async def _get_or_create_association(
    session: Session, *, user_id: int, app_id: int
) -> UserAgentAppAssociation:
    """Return the association row for (user, app), creating it when missing.

    Idempotent: repeated calls for the same pair yield the same row (the
    UNIQUE(user_id, agent_app_id) constraint backs this up at the DB level).
    """
    existing = await _get_association(session, user_id=user_id, app_id=app_id)
    if existing is not None:
        return existing
    assoc = UserAgentAppAssociation(user_id=user_id, agent_app_id=app_id)
    session.add(assoc)
    session.commit()
    session.refresh(assoc)
    return assoc


async def _get_association(
    session: Session, *, user_id: int, app_id: int
) -> UserAgentAppAssociation | None:
    """Return the association row for (user, app) or None when absent."""
    statement = select(UserAgentAppAssociation).where(
        UserAgentAppAssociation.user_id == user_id,
        UserAgentAppAssociation.agent_app_id == app_id,
    )
    return session.exec(statement).first()


async def _invalidate_user_layer_cache(
    session: Session, *, app_cfg: AgentApp
) -> None:
    """NULL ``last_synced_workspace_hash`` for every association of the app.

    Applied on publish and PATCH (spec §5.1 P0-3): the recorded sync hash is
    observation-only in v3.3 — drift detection itself is carried by the
    dynamic expected-fingerprint lazy check (spec §4.3) — so invalidation
    simply marks every user layer as needing re-observation.
    """
    rows = session.exec(
        select(UserAgentAppAssociation).where(
            UserAgentAppAssociation.agent_app_id == app_cfg.id
        )
    ).all()
    if not rows:
        return
    for row in rows:
        row.last_synced_workspace_hash = None
        session.add(row)
    session.commit()
    logger.debug(
        "user_layer_cache_invalidated",
        app_id=app_cfg.id,
        associations=len(rows),
    )
