"""AgentApp business orchestration (G2 service layer, spec-g2-workspace v3.3).

Implements the service contracts of spec §4 (copy-timing flows) and §9.3:
publish (two-stage validation + Global -> Agent materialization + workspace
hash), user association / disassociation ((Global + Agent) -> User layer),
the PATCH interpretation-B state machine (§5.1), delete with workspace
cascade (§3.4) and the v3.3 dynamic expected-fingerprint lazy check (§4.3).

Layering: the API layer only validates parameters and forwards here; this
module orchestrates multi-table state changes and filesystem access on top
of ``skills_store`` (copy/hash primitives), ``agents_service`` (sub-agent
resolution/visibility), ``db_service`` (association CRUD) and the existing
``assembly``/``runtime`` publish validation and fingerprint loaders.
"""

import asyncio
import shutil

from sqlmodel import Session, select

from app.api.v1.providers import build_model_catalog
from app.core.logging import logger
from app.models.agent_assets import (
    AgentApp,
    SkillAsset,
    SubAgentConfig,
    UserAgentAppAssociation,
)
from app.models.user import User
from app.schemas.agent_apps import AgentAppUpdate
from app.services import db_service
from app.services.agents import agents_service, assembly, runtime, skills_store
from app.services.agents.mcp_manager import build_tool_catalog

# System default AgentApp name (bootstrap-seeded; delete-protected like the
# default provider/model pair). Mirrors the API-layer constant; the API layer
# re-exports nothing from here yet (Phase 3 wires the endpoints up).
_DEFAULT_AGENT_APP_NAME = "default"


class AgentAppServiceError(Exception):
    """Base class for agent app service-layer business errors."""


class AgentAppNotFoundError(AgentAppServiceError):
    """The referenced AgentApp row does not exist (API: 404)."""


class AgentAppNotPublishedError(AgentAppServiceError):
    """The operation requires a published AgentApp (API: 422)."""


class UserNotFoundError(AgentAppServiceError):
    """The referenced user row does not exist (API: 404)."""


class AssociationNotFoundError(AgentAppServiceError):
    """The referenced (user, app) association does not exist (API: 404)."""


def _skill_content_hash(session: Session, skill_name: str) -> str:
    """Return the content hash of a SkillAsset, 422-style error when missing."""
    asset = session.get(SkillAsset, skill_name)
    if asset is None:
        raise ValueError(f"referenced skill '{skill_name}' does not exist")
    return asset.content_hash


async def _validate_publish_prerequisites(
    session: Session, app_cfg: AgentApp
) -> tuple[list[SubAgentConfig], dict[str, str]]:
    """Two-stage publish validation migrated from the API layer (spec §4.1).

    Stage 1 — referential integrity: every bound sub-agent and every skill
    referenced by the app or by an explicit sub-agent whitelist resolves to a
    real row.

    Stage 2 — live configuration: ``assembly.validate_publish`` checks the
    ``allowed_tools`` entries against the live tool catalog and the model
    references against the live provider/model catalog.

    Returns:
        The bound SubAgentConfig rows and the skill name -> content hash
        mapping (app skills plus sub-agent-only whitelist skills) that the
        publish fingerprint consumes.

    Raises:
        ValueError: On any dangling reference or catalog violation (API: 422).
    """
    subagent_cfgs: list[SubAgentConfig] = []
    for subagent_name in app_cfg.subagent_names:
        cfg = session.get(SubAgentConfig, subagent_name)
        if cfg is None:
            raise ValueError(f"referenced subagent '{subagent_name}' does not exist")
        subagent_cfgs.append(cfg)

    skill_hashes: dict[str, str] = {}
    for skill_name in app_cfg.skill_names:
        skill_hashes[skill_name] = _skill_content_hash(session, skill_name)

    # Sub-agent explicit whitelists (the inherit ``None`` case contributes
    # nothing because the sub-agent resolves to the app's set, already
    # covered above) also have to resolve to a real SkillAsset; otherwise a
    # dangling subagent-only skill would silently skip recompilation.
    await agents_service.validate_subagent_skill_visibility(
        session, app_cfg=app_cfg, subagent_cfgs=subagent_cfgs
    )
    for cfg in subagent_cfgs:
        for skill_name in cfg.skill_names or []:
            if skill_name not in skill_hashes:
                skill_hashes[skill_name] = _skill_content_hash(session, skill_name)

    catalog = await build_tool_catalog(session)
    model_catalog = build_model_catalog(session)
    assembly.validate_publish(app_cfg, subagent_cfgs, catalog, model_catalog)
    return subagent_cfgs, skill_hashes


async def publish_agent_app(
    session: Session, *, app_cfg: AgentApp, current_user_id: int
) -> AgentApp:
    """Publish an AgentApp (spec §4.1): validation + Global -> Agent + hashes.

    Orchestrates: two-stage validation, Global -> Agent materialization
    (hash-compared), workspace_hash computation over the Agent skills dir,
    skeleton ``agent_dir`` stamping, the preserved published fingerprint /
    status / version transition, and user-layer cache invalidation.

    Args:
        session: SQLModel database session.
        app_cfg: The AgentApp row to publish.
        current_user_id: Acting admin user id (audit context).

    Returns:
        The published AgentApp row.
    """
    subagent_cfgs, skill_hashes = await _validate_publish_prerequisites(session, app_cfg)

    # Global -> Agent copy (hash-compared, idempotent).
    if app_cfg.skill_names:
        await skills_store.materialize_for_agent(
            session,
            app_id=app_cfg.id,
            skill_names=list(app_cfg.skill_names),
        )

    # Workspace hash over the Agent skills dir (never _agent_dir: that would
    # include the nested users/ subtree) + skeleton dir stamping.
    agent_dir = skills_store._agent_dir(app_cfg.id)
    agent_dir.mkdir(parents=True, exist_ok=True)
    app_cfg.agent_dir = str(agent_dir)
    app_cfg.workspace_hash = skills_store.compute_workspace_hash(
        skills_store._agent_skill_dir(app_cfg.id)
    )
    app_cfg.agent_workspace_status = "active"

    # Preserved legacy transition: status + publish fingerprint + version.
    app_cfg.status = "published"
    mcp_fingerprint = await runtime._load_mcp_fingerprint(session)
    model_fingerprint, _ = await runtime._load_model_fingerprint(
        session, app_cfg, subagent_cfgs
    )
    app_cfg.published_hash = assembly.compute_fingerprint(
        app_cfg, subagent_cfgs, skill_hashes, mcp_fingerprint, model_fingerprint
    )
    app_cfg.version += 1

    # Invalidate every association's last-synced hash (spec §5.2).
    await db_service._invalidate_user_layer_cache(session, app_cfg=app_cfg)

    session.add(app_cfg)
    session.commit()
    session.refresh(app_cfg)
    logger.info(
        "agent_app_published",
        app_id=app_cfg.id,
        workspace_hash=app_cfg.workspace_hash,
        skill_count=len(app_cfg.skill_names or []),
        version=app_cfg.version,
    )
    return app_cfg


async def associate_user_with_app(
    session: Session, *, user_id: int, app_id: int, current_user_id: int
) -> None:
    """Associate a user with a published app (spec §4.2): combined User layer.

    Validates the app/user pair, upserts the association (idempotent — a
    repeated call refreshes the User layer) and materializes the combined
    (Global + Agent) -> User layer, then stamps the association's sync hash.

    Raises:
        AgentAppNotFoundError: The app does not exist.
        AgentAppNotPublishedError: The app is not in the published state.
        UserNotFoundError: The user does not exist.
    """
    app_cfg = session.get(AgentApp, app_id)
    if app_cfg is None:
        raise AgentAppNotFoundError(f"agent app '{app_id}' not found")
    if app_cfg.status != "published":
        raise AgentAppNotPublishedError(f"agent app '{app_id}' is not published")
    user = session.get(User, user_id)
    if user is None:
        raise UserNotFoundError(f"user '{user_id}' not found")

    assoc = await db_service._get_or_create_association(
        session, user_id=user_id, app_id=app_id
    )

    subagent_cfgs = await agents_service.list_subagent_cfgs(
        session, app_id=app_id, skill_names=app_cfg.skill_names or []
    )
    await skills_store.materialize_to_user_combined(
        session=session,
        app_cfg=app_cfg,
        user_id=user_id,
        subagent_cfgs=subagent_cfgs,
    )

    assoc.last_synced_workspace_hash = app_cfg.workspace_hash
    session.add(assoc)
    session.commit()

    logger.info(
        "user_app_associated",
        user_id=user_id,
        app_id=app_id,
        workspace_hash=app_cfg.workspace_hash,
    )


async def disassociate_user_from_app(
    session: Session, *, user_id: int, app_id: int, current_user_id: int
) -> None:
    """Disassociate a user: drop the association row + the User workspace dir.

    Raises:
        AssociationNotFoundError: No association exists for the pair.
    """
    assoc = await db_service._get_association(session, user_id=user_id, app_id=app_id)
    if assoc is None:
        raise AssociationNotFoundError(
            f"user '{user_id}' is not associated with app '{app_id}'"
        )

    session.delete(assoc)
    session.commit()

    user_dir = skills_store._agent_dir(app_id) / "users" / str(user_id)
    if user_dir.exists():
        await asyncio.to_thread(shutil.rmtree, user_dir)

    logger.info("user_app_disassociated", user_id=user_id, app_id=app_id)


async def patch_agent_app(
    session: Session,
    *,
    app_cfg: AgentApp,
    patch_data: AgentAppUpdate,
    current_user_id: int,
) -> AgentApp:
    """Apply a partial update under the PATCH interpretation-B machine (§5.1).

    Whole-replacement semantics per field; a published app demotes to draft
    (re-publish required to go live again), the workspace hash is NULLed,
    the workspace status returns to pending and every association's sync
    hash is invalidated — the four P0-3/R3 steps.

    Args:
        session: SQLModel database session.
        app_cfg: The AgentApp row to update.
        patch_data: Validated partial update (current Pydantic schema).
        current_user_id: Acting user id (audit context).

    Returns:
        The updated AgentApp row with bumped version.

    Raises:
        ValueError: On an empty payload or a null list/dict field.
    """
    updates = patch_data.model_dump(exclude_unset=True)
    if not updates:
        raise ValueError("nothing to update")

    for field in ("skill_names", "subagent_names", "interrupt_on"):
        if field in updates and updates[field] is None:
            kind = "dict" if field == "interrupt_on" else "list"
            raise ValueError(
                f"{field} must not be null; pass an empty {kind} to clear it"
            )

    for field, value in updates.items():
        setattr(app_cfg, field, value)

    if app_cfg.status == "published":
        # Content edits invalidate the published fingerprint: demote back to
        # draft so a broken config cannot keep serving live sessions.
        app_cfg.status = "draft"
        logger.info("agent_app_unpublished_on_edit", app_id=app_cfg.id)

    app_cfg.workspace_hash = None
    app_cfg.agent_workspace_status = "pending"
    await db_service._invalidate_user_layer_cache(session, app_cfg=app_cfg)

    app_cfg.version += 1
    session.add(app_cfg)
    session.commit()
    session.refresh(app_cfg)
    logger.info("agent_app_updated", app_id=app_cfg.id, version=app_cfg.version)
    return app_cfg


async def delete_agent_app(
    session: Session, *, app_id: int, current_user_id: int
) -> None:
    """Delete an AgentApp: DB row + associations + whole workspace dir (§3.4).

    The association rows are deleted explicitly (the FK CASCADE is the
    production safety net; explicit deletes keep the behaviour identical on
    SQLite, where foreign-key enforcement is off by default). Removing the
    agent dir cascades the nested User layers with it.

    Raises:
        AgentAppNotFoundError: The app does not exist.
        ValueError: When targeting the delete-protected system default app.
    """
    app_cfg = session.get(AgentApp, app_id)
    if app_cfg is None:
        raise AgentAppNotFoundError(f"agent app '{app_id}' not found")
    if app_cfg.name == _DEFAULT_AGENT_APP_NAME:
        logger.warning(
            "agent_app_delete_rejected", app_id=app_id, reason="default_protected"
        )
        raise ValueError(
            "the system default agent app is protected and cannot be deleted"
        )

    for assoc in session.exec(
        select(UserAgentAppAssociation).where(
            UserAgentAppAssociation.agent_app_id == app_id
        )
    ).all():
        session.delete(assoc)
    session.delete(app_cfg)
    session.commit()

    agent_dir = skills_store._agent_dir(app_id)
    if agent_dir.exists():
        await asyncio.to_thread(shutil.rmtree, agent_dir)

    logger.info("agent_app_deleted", app_id=app_id)


async def ensure_user_workspace_up_to_date(
    session: Session, *, user_id: int, app_id: int
) -> bool:
    """Lazy workspace check with the v3.3 dynamic expected fingerprint (§4.3).

    Computes the expected fingerprint by resolving each effective skill name
    against the Agent layer first (falling back to Global) — the User file
    set is the effective union (app + sub-agents) and can be larger than the
    Agent-layer snapshot, so the stored ``workspace_hash`` is never compared
    directly. A mismatch triggers an idempotent incremental re-materialization.

    Args:
        session: SQLModel database session.
        user_id: End user id (from the API layer's get_current_user).
        app_id: AgentApp primary key.

    Returns:
        True when a re-materialization ran; False on hash hit or when the
        app/association is missing (silent skip, never raises).
    """
    app_cfg = session.get(AgentApp, app_id)
    if app_cfg is None:
        logger.warning("lazy_validate_app_not_found", app_id=app_id)
        return False

    assoc = await db_service._get_association(session, user_id=user_id, app_id=app_id)
    if assoc is None:
        # Unassociated user: no User layer exists yet — the first copy is the
        # associate-user endpoint's job.
        logger.debug("lazy_validate_assoc_not_found", user_id=user_id, app_id=app_id)
        return False

    subagent_cfgs = await agents_service.list_subagent_cfgs(
        session, app_id=app_id, skill_names=app_cfg.skill_names or []
    )
    effective_skill_names = sorted(
        set(app_cfg.skill_names or [])
        | {n for cfg in subagent_cfgs for n in (cfg.skill_names or [])}
    )

    expected_hash = skills_store._compute_effective_workspace_hash(
        app_id, effective_skill_names
    )
    current_hash = skills_store._compute_user_workspace_hash(
        skills_store._user_skill_dir(app_id, user_id)
    )

    if current_hash == expected_hash:
        return False

    # Drifted: incremental re-sync (idempotent — hash-compared writes plus
    # pruning of stale subdirectories).
    await skills_store.materialize_to_user_combined(
        session=session,
        app_cfg=app_cfg,
        user_id=user_id,
        subagent_cfgs=subagent_cfgs,
    )
    assoc.last_synced_workspace_hash = app_cfg.workspace_hash
    session.add(assoc)
    session.commit()
    logger.info(
        "user_workspace_lazy_synced",
        user_id=user_id,
        app_id=app_id,
        workspace_hash=app_cfg.workspace_hash,
    )
    return True
