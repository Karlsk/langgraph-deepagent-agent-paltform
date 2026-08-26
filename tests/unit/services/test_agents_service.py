"""Unit tests for ``app/services/agents/agents_service.py`` (G2 Phase 2).

Covers the SubAgent collection used by publish / associate / lazy-check
orchestration (spec-g2-workspace v3.3 §9.4): the effective SubAgentConfig
set resolution for an app and the sub-agent skill visibility validation.
All operations run against an in-memory SQLite engine (StaticPool); zero
network, zero LLM.
"""

import asyncio
from collections.abc import Generator

import pytest
from sqlalchemy.pool import StaticPool
from sqlmodel import SQLModel
from sqlmodel import Session as DBSession
from sqlmodel import create_engine

from app.models.agent_assets import AgentApp, SkillAsset, SubAgentConfig
from app.services.agents import agents_service

pytestmark = pytest.mark.unit


@pytest.fixture
def db() -> Generator[DBSession, None, None]:
    """In-memory SQLite session with every table created (StaticPool)."""
    engine = create_engine(
        "sqlite://",
        connect_args={"check_same_thread": False},
        poolclass=StaticPool,
    )
    SQLModel.metadata.create_all(engine)
    session = DBSession(engine)
    yield session
    session.close()


def _seed_skill(db: DBSession, name: str) -> SkillAsset:
    """Insert one global SkillAsset row and return it."""
    row = SkillAsset(name=name, description=f"{name} skill", body=f"# {name}", content_hash=f"hash-{name}")
    db.add(row)
    db.commit()
    db.refresh(row)
    return row


def _seed_subagent(db: DBSession, name: str, skill_names: list[str]) -> SubAgentConfig:
    """Insert one SubAgentConfig row with an explicit skill whitelist."""
    row = SubAgentConfig(
        name=name,
        description=f"{name} sub",
        when_to_use="always",
        system_prompt="x",
        content_hash=f"hash-{name}",
        skill_names=skill_names,
    )
    db.add(row)
    db.commit()
    db.refresh(row)
    return row


def test_list_subagent_cfgs_returns_effective_set(db: DBSession) -> None:
    """Only SubAgentConfig rows that actually exist are returned, in bind order."""
    app_row = AgentApp(name="list-app", system_prompt="x", subagent_names=["sa-a", "sa-missing"])
    db.add(app_row)
    db.commit()
    db.refresh(app_row)
    seeded = _seed_subagent(db, "sa-a", ["skl-own"])

    cfgs = asyncio.run(
        agents_service.list_subagent_cfgs(
            db, app_id=app_row.id, skill_names=app_row.skill_names or []
        )
    )

    assert [cfg.name for cfg in cfgs] == [seeded.name]
    assert cfgs[0].skill_names == ["skl-own"]


def test_list_subagent_cfgs_returns_empty_for_missing_app(db: DBSession) -> None:
    """A missing app id resolves to an empty list (query semantics)."""
    cfgs = asyncio.run(
        agents_service.list_subagent_cfgs(db, app_id=404, skill_names=[])
    )
    assert cfgs == []


def test_validate_subagent_skill_visibility(db: DBSession) -> None:
    """Explicit sub-agent whitelists must resolve to real SkillAsset rows."""
    app_row = AgentApp(name="vis-app", system_prompt="x", skill_names=["skl-app"])
    db.add(app_row)
    db.commit()
    db.refresh(app_row)
    _seed_skill(db, "skl-app")
    _seed_skill(db, "skl-sub")
    good = _seed_subagent(db, "sa-good", ["skl-app", "skl-sub"])
    dangling = _seed_subagent(db, "sa-bad", ["skl-ghost"])

    # Existing whitelist entries pass (inherit ``None`` contributes nothing).
    asyncio.run(
        agents_service.validate_subagent_skill_visibility(
            db, app_cfg=app_row, subagent_cfgs=[good]
        )
    )

    # A dangling subagent-only skill is rejected with the owning subagent named.
    with pytest.raises(ValueError, match=r"sa-bad"):
        asyncio.run(
            agents_service.validate_subagent_skill_visibility(
                db, app_cfg=app_row, subagent_cfgs=[good, dangling]
            )
        )
