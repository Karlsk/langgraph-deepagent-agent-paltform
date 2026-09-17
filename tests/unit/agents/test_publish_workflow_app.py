"""Unit tests for the engine-aware publish path of workflow AgentApps (Phase 4, D7).

Zero network / zero LLM / zero real workflow engine: the registry is a fake
exposing only ``has_workflow``, installed through the host-level bridge, and
the workflow content hash comes from a seeded ``WorkflowDefinitionAsset`` row.
A workflow app publishes without any skills workspace materialization, while
the deepagents path keeps its existing behaviour (regression guard).
"""

import asyncio
import hashlib
from collections.abc import Generator
from pathlib import Path
from typing import Any

import pytest
from sqlalchemy.pool import StaticPool
from sqlmodel import SQLModel
from sqlmodel import Session as DBSession
from sqlmodel import create_engine, select

from app.core.config import settings
from app.models.agent_assets import AgentApp, UserAgentAppAssociation
from app.models.provider import ModelConfig, Provider
from app.models.user import User
from app.models.workflow_definition import WorkflowDefinitionAsset
from app.services.agents import agent_apps_service, skills_store
from app.services.agents.workflow_bridge import (
    reset_workflow_registry,
    set_workflow_registry,
)

pytestmark = pytest.mark.unit


class FakeRegistry:
    """Minimal registry stub: only ``has_workflow`` is consulted on publish."""

    def __init__(self, registered: set[str]) -> None:
        """Bind the set of workflow ids treated as registered."""
        self._registered = registered

    def has_workflow(self, workflow_id: str) -> bool:
        return workflow_id in self._registered


@pytest.fixture
def data_root(tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> Path:
    """Redirect settings.DATA_ROOT into an isolated tmp directory."""
    root = tmp_path / "data"
    monkeypatch.setattr(settings, "DATA_ROOT", str(root))
    return root


@pytest.fixture
def db() -> Generator[DBSession, None, None]:
    """In-memory SQLite session with every table created (StaticPool)."""
    engine = create_engine("sqlite://", connect_args={"check_same_thread": False}, poolclass=StaticPool)
    SQLModel.metadata.create_all(engine)
    session = DBSession(engine)
    yield session
    session.close()


@pytest.fixture(autouse=True)
def reset_bridge() -> Generator[None, None, None]:
    """Isolate the module-level registry bridge between tests."""
    reset_workflow_registry()
    yield
    reset_workflow_registry()


@pytest.fixture
def default_pair(db: DBSession) -> None:
    """Seed the default provider/model pair (deepagents regression path)."""
    provider = Provider(name="default", type="OPENAI_COMPATIBLE", auth_config={"api_key": "sk-test"})
    db.add(provider)
    db.commit()
    db.refresh(provider)
    db.add(ModelConfig(provider_id=provider.id, name="default", model_id="MiniMax-M3"))
    db.commit()


@pytest.fixture
def owner(db: DBSession) -> User:
    """Admin user acting as the current_user_id audit source."""
    row = User(
        email="wf-owner@example.com",
        hashed_password=User.hash_password("Passw0rd!Strong"),
        username="wf-owner",
    )
    db.add(row)
    db.commit()
    db.refresh(row)
    return row


@pytest.fixture
def member(db: DBSession) -> User:
    """End user being associated with apps under test."""
    row = User(
        email="wf-member@example.com",
        hashed_password=User.hash_password("Passw0rd!Strong"),
        username="wf-member",
    )
    db.add(row)
    db.commit()
    db.refresh(row)
    return row


def _seed_workflow_asset(db: DBSession, workflow_id: str, body: str) -> WorkflowDefinitionAsset:
    """Insert one workflow definition row with a content_hash."""
    row = WorkflowDefinitionAsset(
        workflow_id=workflow_id,
        entry_point="start",
        body=body,
        content_hash=hashlib.sha256(body.encode("utf-8")).hexdigest(),
    )
    db.add(row)
    db.commit()
    db.refresh(row)
    return row


def _seed_workflow_app(
    db: DBSession, *, workflow_id: str | None, status: str = "draft", name: str = "chatflow-app"
) -> AgentApp:
    """Insert one engine='workflow' AgentApp row."""
    row = AgentApp(
        name=name,
        system_prompt="",
        engine="workflow",
        workflow_id=workflow_id,
        skill_names=[],
        subagent_names=[],
        status=status,
    )
    db.add(row)
    db.commit()
    db.refresh(row)
    return row


# ---------------------------------------------------------------------------
# publish — workflow engine branch (D7)
# ---------------------------------------------------------------------------


def test_publish_workflow_app_skips_workspace(db: DBSession, data_root: Path, owner: User) -> None:
    """A registered workflow publishes with no agent_dir / workspace_hash."""
    _seed_workflow_asset(db, "wf-1", "nodes: []")
    app_row = _seed_workflow_app(db, workflow_id="wf-1")
    set_workflow_registry(FakeRegistry({"wf-1"}))  # type: ignore[arg-type]

    result = asyncio.run(agent_apps_service.publish_agent_app(db, app_cfg=app_row, current_user_id=owner.id))

    assert result.status == "published"
    assert result.version == 2
    assert result.agent_dir is None
    assert result.workspace_hash is None
    assert result.published_hash
    # No skills workspace was materialized on disk.
    assert not skills_store._agent_dir(app_row.id).exists()


def test_publish_workflow_app_hash_tracks_content(db: DBSession, data_root: Path, owner: User) -> None:
    """The published hash differs when the bound workflow content differs."""
    _seed_workflow_asset(db, "wf-a", "body-a")
    app_a = _seed_workflow_app(db, workflow_id="wf-a", name="chatflow-a")
    set_workflow_registry(FakeRegistry({"wf-a", "wf-b"}))  # type: ignore[arg-type]
    hash_a = asyncio.run(agent_apps_service.publish_agent_app(db, app_cfg=app_a, current_user_id=owner.id)).published_hash

    _seed_workflow_asset(db, "wf-b", "body-b")
    app_b = _seed_workflow_app(db, workflow_id="wf-b", name="chatflow-b")
    hash_b = asyncio.run(agent_apps_service.publish_agent_app(db, app_cfg=app_b, current_user_id=owner.id)).published_hash

    assert hash_a != hash_b


def test_publish_workflow_app_unregistered_raises(db: DBSession, data_root: Path, owner: User) -> None:
    """Binding a workflow absent from the registry is rejected."""
    app_row = _seed_workflow_app(db, workflow_id="ghost")
    set_workflow_registry(FakeRegistry(set()))  # type: ignore[arg-type]

    with pytest.raises(agent_apps_service.AgentAppNotPublishedError, match="not registered"):
        asyncio.run(agent_apps_service.publish_agent_app(db, app_cfg=app_row, current_user_id=owner.id))

    assert app_row.status == "draft"


def test_publish_workflow_app_missing_binding_raises(db: DBSession, data_root: Path, owner: User) -> None:
    """A workflow app without a workflow_id cannot publish."""
    app_row = _seed_workflow_app(db, workflow_id=None)
    set_workflow_registry(FakeRegistry({"wf-1"}))  # type: ignore[arg-type]

    with pytest.raises(agent_apps_service.AgentAppNotPublishedError, match="workflow_id"):
        asyncio.run(agent_apps_service.publish_agent_app(db, app_cfg=app_row, current_user_id=owner.id))


# ---------------------------------------------------------------------------
# associate — workflow engine skips materialization
# ---------------------------------------------------------------------------


def test_associate_workflow_app_skips_materialize(
    db: DBSession, data_root: Path, owner: User, member: User
) -> None:
    """Associating a user with a workflow app creates no User skills layer."""
    app_row = _seed_workflow_app(db, workflow_id="wf-1", status="published")

    called: list[dict[str, Any]] = []

    async def spy_materialize(session: Any, *, app_cfg: Any, user_id: int, subagent_cfgs: Any) -> None:
        called.append({"user_id": user_id})

    mp = pytest.MonkeyPatch()
    mp.setattr(skills_store, "materialize_to_user_combined", spy_materialize)
    try:
        asyncio.run(
            agent_apps_service.associate_user_with_app(
                db, user_id=member.id, app_id=app_row.id, current_user_id=owner.id
            )
        )
    finally:
        mp.undo()

    assert called == []  # never materialized
    assoc = db.exec(select(UserAgentAppAssociation)).one()
    assert assoc.user_id == member.id


# ---------------------------------------------------------------------------
# deepagents regression
# ---------------------------------------------------------------------------


def test_publish_deepagents_app_unchanged(
    db: DBSession, data_root: Path, default_pair: None, owner: User
) -> None:
    """The deepagents publish path still stamps agent_dir + workspace_hash."""
    app_row = AgentApp(name="deep-app", system_prompt="x", engine="deepagents", skill_names=[], status="draft")
    db.add(app_row)
    db.commit()
    db.refresh(app_row)

    result = asyncio.run(agent_apps_service.publish_agent_app(db, app_cfg=app_row, current_user_id=owner.id))

    assert result.status == "published"
    assert result.agent_workspace_status == "active"
    assert result.agent_dir == str(skills_store._agent_dir(app_row.id))
    assert result.workspace_hash == hashlib.sha256(b"").hexdigest()
