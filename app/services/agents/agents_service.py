"""SubAgent collection and skill-visibility business rules (G2 service layer).

Service-layer counterpart of spec-g2-workspace v3.3 §9.4: resolving the
effective SubAgentConfig set bound to an AgentApp (consumed by publish,
associate and the lazy workspace check) and validating that every explicit
sub-agent skill whitelist entry resolves to a real SkillAsset row.
"""

from collections.abc import Sequence

from sqlmodel import Session

from app.core.logging import logger
from app.models.agent_assets import AgentApp, SkillAsset, SubAgentConfig


async def list_subagent_cfgs(
    session: Session, *, app_id: int, skill_names: Sequence[str]
) -> list[SubAgentConfig]:
    """Resolve the effective SubAgentConfig set bound to an app, in bind order.

    Rows whose SubAgentConfig entry no longer exists are skipped with a
    warning: referential integrity is enforced by the publish prerequisites;
    the aggregation callers here (associate / lazy check) only need the rows
    that actually exist.

    Args:
        session: SQLModel database session.
        app_id: AgentApp primary key whose ``subagent_names`` are resolved.
        skill_names: The parent app's ``skill_names`` (spec v3.3 §9.4
            signature parity; inherit-resolution never consults it here
            because ``None`` whitelists contribute nothing to the union).

    Returns:
        The existing SubAgentConfig rows in ``subagent_names`` order; an
        empty list when the app itself is missing.
    """
    app_cfg = session.get(AgentApp, app_id)
    if app_cfg is None:
        logger.debug("list_subagent_cfgs_app_missing", app_id=app_id)
        return []
    cfgs: list[SubAgentConfig] = []
    for name in app_cfg.subagent_names or []:
        cfg = session.get(SubAgentConfig, name)
        if cfg is not None:
            cfgs.append(cfg)
        else:
            logger.warning(
                "list_subagent_cfgs_row_missing", app_id=app_id, subagent=name
            )
    return cfgs


async def validate_subagent_skill_visibility(
    session: Session, *, app_cfg: AgentApp, subagent_cfgs: Sequence[SubAgentConfig]
) -> None:
    """Check that explicit sub-agent skill whitelists resolve to real rows.

    The inherit ``None`` case contributes nothing (the sub-agent resolves to
    the app's set, which the publish prerequisites validate separately); only
    explicit whitelists are checked here. A dangling subagent-only skill
    would otherwise silently skip recompilation downstream.

    Args:
        session: SQLModel database session.
        app_cfg: The AgentApp being validated (context for callers).
        subagent_cfgs: Bound SubAgentConfig rows.

    Raises:
        ValueError: Naming the first dangling skill and its owning sub-agent.
    """
    for cfg in subagent_cfgs:
        for skill_name in cfg.skill_names or []:
            if session.get(SkillAsset, skill_name) is None:
                raise ValueError(
                    f"referenced skill '{skill_name}' (subagent '{cfg.name}') does not exist"
                )
