"""Unit tests for ``app/services/agents/agent_apps_service.py`` (G2 Phase 2).

Covers the agent-app service orchestration (spec-g2-workspace v3.3 §4/§9.3):
publish (Global -> Agent materialization + workspace_hash), user association
(combined User-layer materialization), PATCH state machine (interpretation B),
delete workspace cascade, disassociation cleanup and the v3.3 dynamic expected
-fingerprint lazy check. All operations run against an in-memory SQLite engine
(StaticPool) with DATA_ROOT redirected into tmp_path; zero network, zero LLM.
"""

import asyncio
import hashlib
from collections.abc import Generator
from pathlib import Path

import pytest
from sqlalchemy.pool import StaticPool
from sqlmodel import SQLModel
from sqlmodel import Session as DBSession
from sqlmodel import create_engine, select

from app.core.config import settings
from app.models.agent_assets import AgentApp, SkillAsset, UserAgentAppAssociation
from app.models.provider import ModelConfig, Provider
from app.models.user import User
from app.schemas.agent_apps import AgentAppUpdate
from app.services.agents import agent_apps_service, skills_store

pytestmark = pytest.mark.unit


@pytest.fixture
def data_root(tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> Path:
    """Redirect settings.DATA_ROOT into an isolated tmp directory (G2 v3 layout)."""
    root = tmp_path / "data"
    monkeypatch.setattr(settings, "DATA_ROOT", str(root))
    return root


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


@pytest.fixture
def default_pair(db: DBSession) -> None:
    """Seed the default provider/model pair that NULL model references resolve to."""
    provider = Provider(
        name="default",
        type="OPENAI_COMPATIBLE",
        auth_config={"api_key": "sk-test-default"},
    )
    db.add(provider)
    db.commit()
    db.refresh(provider)
    db.add(ModelConfig(provider_id=provider.id, name="default", model_id="MiniMax-M3"))
    db.commit()


@pytest.fixture
def owner(db: DBSession) -> User:
    """Admin-ish user acting as the current_user_id audit source."""
    row = User(
        email="svc-owner@example.com",
        hashed_password=User.hash_password("Passw0rd!Strong"),
        username="svc-owner",
    )
    db.add(row)
    db.commit()
    db.refresh(row)
    return row


@pytest.fixture
def member(db: DBSession) -> User:
    """End user being associated with apps under test."""
    row = User(
        email="svc-member@example.com",
        hashed_password=User.hash_password("Passw0rd!Strong"),
        username="svc-member",
    )
    db.add(row)
    db.commit()
    db.refresh(row)
    return row


def _seed_skill(db: DBSession, name: str, body: str) -> SkillAsset:
    """Insert one global SkillAsset row and return it."""
    row = SkillAsset(
        name=name,
        description=f"{name} skill",
        body=body,
        content_hash=hashlib.sha256(body.encode("utf-8")).hexdigest(),
    )
    db.add(row)
    db.commit()
    db.refresh(row)
    return row


def _seed_app(db: DBSession, *, name: str, skill_names: list[str], status: str = "draft") -> AgentApp:
    """Insert one AgentApp row and return it."""
    row = AgentApp(name=name, system_prompt="x", skill_names=skill_names, status=status)
    db.add(row)
    db.commit()
    db.refresh(row)
    return row


def _seed_association(db: DBSession, *, user_id: int, app_id: int, synced: str | None) -> UserAgentAppAssociation:
    """Insert one association row with an explicit synced hash."""
    row = UserAgentAppAssociation(
        user_id=user_id, agent_app_id=app_id, last_synced_workspace_hash=synced
    )
    db.add(row)
    db.commit()
    db.refresh(row)
    return row


# ---------------------------------------------------------------------------
# publish (spec §4.1)
# ---------------------------------------------------------------------------


def test_publish_calls_materialize_for_agent(
    db: DBSession, data_root: Path, default_pair: None, owner: User
) -> None:
    """Publish materializes Global -> Agent and stamps the workspace hash."""
    _seed_skill(db, "alpha", "# alpha")
    app_row = _seed_app(db, name="publish-app", skill_names=["alpha"])
    _seed_association(db, user_id=owner.id, app_id=app_row.id, synced="stale")

    calls: list[dict[str, object]] = []

    async def spy_materialize(session: DBSession, *, app_id: int, skill_names: list[str]) -> None:
        calls.append({"app_id": app_id, "skill_names": list(skill_names)})

    monkeypatch = pytest.MonkeyPatch()
    monkeypatch.setattr(skills_store, "materialize_for_agent", spy_materialize)
    try:
        result = asyncio.run(
            agent_apps_service.publish_agent_app(
                db, app_cfg=app_row, current_user_id=owner.id
            )
        )
    finally:
        monkeypatch.undo()

    assert calls == [{"app_id": app_row.id, "skill_names": ["alpha"]}]
    assert result.status == "published"
    assert result.agent_workspace_status == "active"
    # The spied materialization wrote no files, so the Agent layer is empty:
    # the workspace hash degenerates to the empty-content hash.
    assert result.workspace_hash == hashlib.sha256(b"").hexdigest()
    assert result.agent_dir == str(skills_store._agent_dir(app_row.id))
    assert result.published_hash is not None
    assert result.version == 2

    assoc = db.exec(select(UserAgentAppAssociation)).one()
    assert assoc.last_synced_workspace_hash is None


# ---------------------------------------------------------------------------
# associate / disassociate (spec §4.2)
# ---------------------------------------------------------------------------


def test_associate_user_calls_materialize_to_user_combined(
    db: DBSession, data_root: Path, owner: User, member: User
) -> None:
    """Association triggers combined User-layer materialization and hash stamping."""
    app_row = _seed_app(db, name="assoc-app", skill_names=[], status="published")
    app_row.workspace_hash = "wh-1"
    db.add(app_row)
    db.commit()

    calls: list[dict[str, object]] = []

    async def spy_materialize(
        session: DBSession,
        *,
        app_cfg: AgentApp,
        user_id: int,
        subagent_cfgs: list[object],
    ) -> None:
        calls.append({"app_cfg": app_cfg, "user_id": user_id, "subagent_cfgs": list(subagent_cfgs)})

    monkeypatch = pytest.MonkeyPatch()
    monkeypatch.setattr(skills_store, "materialize_to_user_combined", spy_materialize)
    try:
        asyncio.run(
            agent_apps_service.associate_user_with_app(
                db, user_id=member.id, app_id=app_row.id, current_user_id=owner.id
            )
        )
    finally:
        monkeypatch.undo()

    assert len(calls) == 1
    assert calls[0]["app_cfg"] is app_row
    assert calls[0]["user_id"] == member.id
    assert calls[0]["subagent_cfgs"] == []

    assoc = db.exec(select(UserAgentAppAssociation)).one()
    assert assoc.last_synced_workspace_hash == "wh-1"


def test_disassociate_user_from_app_removes_user_layer(
    db: DBSession, data_root: Path, owner: User, member: User
) -> None:
    """Disassociation drops the association row and the user workspace directory."""
    app_row = _seed_app(db, name="disassoc-app", skill_names=[], status="published")
    _seed_association(db, user_id=member.id, app_id=app_row.id, synced="wh-1")
    user_skill_file = skills_store._user_skill_file(app_row.id, member.id, "alpha")
    user_skill_file.parent.mkdir(parents=True, exist_ok=True)
    user_skill_file.write_text("# alpha", encoding="utf-8")
    assert user_skill_file.exists()

    asyncio.run(
        agent_apps_service.disassociate_user_from_app(
            db, user_id=member.id, app_id=app_row.id, current_user_id=owner.id
        )
    )

    assert db.exec(select(UserAgentAppAssociation)).all() == []
    assert not user_skill_file.parent.parent.exists()


# ---------------------------------------------------------------------------
# patch state machine (spec §5.1, interpretation B)
# ---------------------------------------------------------------------------


def test_patch_transitions_to_draft(
    db: DBSession, data_root: Path, owner: User
) -> None:
    """PATCH applies all four interpretation-B steps and bumps the version."""
    app_row = _seed_app(db, name="patch-app", skill_names=["alpha"], status="published")
    app_row.workspace_hash = "wh-1"
    app_row.agent_workspace_status = "active"
    db.add(app_row)
    db.commit()
    assoc = _seed_association(db, user_id=owner.id, app_id=app_row.id, synced="wh-1")

    result = asyncio.run(
        agent_apps_service.patch_agent_app(
            db,
            app_cfg=app_row,
            patch_data=AgentAppUpdate(system_prompt="revised prompt"),
            current_user_id=owner.id,
        )
    )

    assert result.status == "draft"
    assert result.workspace_hash is None
    assert result.agent_workspace_status == "pending"
    assert result.version == 2
    assert result.system_prompt == "revised prompt"
    db.refresh(assoc)
    assert assoc.last_synced_workspace_hash is None


# ---------------------------------------------------------------------------
# delete cascade (spec §3.4)
# ---------------------------------------------------------------------------


def test_delete_cascades_user_layer(
    db: DBSession, data_root: Path, owner: User, member: User
) -> None:
    """Delete removes the DB row, associations and the whole agent workspace dir."""
    app_row = _seed_app(db, name="doomed-app", skill_names=["alpha"], status="published")
    _seed_association(db, user_id=member.id, app_id=app_row.id, synced="wh-1")
    agent_skill_file = skills_store._agent_skill_file(app_row.id, "alpha")
    agent_skill_file.parent.mkdir(parents=True, exist_ok=True)
    agent_skill_file.write_text("# alpha", encoding="utf-8")
    user_skill_file = skills_store._user_skill_file(app_row.id, member.id, "alpha")
    user_skill_file.parent.mkdir(parents=True, exist_ok=True)
    user_skill_file.write_text("# alpha", encoding="utf-8")

    asyncio.run(
        agent_apps_service.delete_agent_app(db, app_id=app_row.id, current_user_id=owner.id)
    )

    assert db.get(AgentApp, app_row.id) is None
    assert db.exec(select(UserAgentAppAssociation)).all() == []
    assert not skills_store._agent_dir(app_row.id).exists()


# ---------------------------------------------------------------------------
# lazy workspace check (spec §4.3 v3.3)
# ---------------------------------------------------------------------------


def test_ensure_user_workspace_up_to_date_skips_when_matched(
    db: DBSession, data_root: Path, owner: User
) -> None:
    """A User layer matching the dynamic expected fingerprint skips re-sync."""
    _seed_skill(db, "alpha", "# alpha")
    app_row = _seed_app(db, name="lazy-app", skill_names=["alpha"], status="published")
    app_row.workspace_hash = "wh-1"
    db.add(app_row)
    db.commit()
    _seed_association(db, user_id=owner.id, app_id=app_row.id, synced="wh-1")
    global_file = skills_store._global_skill_file("alpha")
    global_file.parent.mkdir(parents=True, exist_ok=True)
    global_file.write_text("# alpha", encoding="utf-8")
    user_file = skills_store._user_skill_file(app_row.id, owner.id, "alpha")
    user_file.parent.mkdir(parents=True, exist_ok=True)
    user_file.write_text("# alpha", encoding="utf-8")

    calls: list[object] = []

    async def spy_materialize(*args: object, **kwargs: object) -> None:
        calls.append(kwargs)

    monkeypatch = pytest.MonkeyPatch()
    monkeypatch.setattr(skills_store, "materialize_to_user_combined", spy_materialize)
    try:
        result = asyncio.run(
            agent_apps_service.ensure_user_workspace_up_to_date(
                db, user_id=owner.id, app_id=app_row.id
            )
        )
    finally:
        monkeypatch.undo()

    assert result is False
    assert calls == []


def test_ensure_user_workspace_up_to_date_resyncs_when_drifted(
    db: DBSession, data_root: Path, owner: User
) -> None:
    """A drifted User layer is re-materialized and the sync hash is stamped."""
    _seed_skill(db, "alpha", "# alpha")
    app_row = _seed_app(db, name="drift-app", skill_names=["alpha"], status="published")
    app_row.workspace_hash = "wh-2"
    db.add(app_row)
    db.commit()
    assoc = _seed_association(db, user_id=owner.id, app_id=app_row.id, synced="wh-1")
    global_file = skills_store._global_skill_file("alpha")
    global_file.parent.mkdir(parents=True, exist_ok=True)
    global_file.write_text("# alpha", encoding="utf-8")
    user_file = skills_store._user_skill_file(app_row.id, owner.id, "alpha")
    user_file.parent.mkdir(parents=True, exist_ok=True)
    user_file.write_text("# stale content", encoding="utf-8")

    result = asyncio.run(
        agent_apps_service.ensure_user_workspace_up_to_date(
            db, user_id=owner.id, app_id=app_row.id
        )
    )

    assert result is True
    assert user_file.read_text(encoding="utf-8") == "# alpha"
    db.refresh(assoc)
    assert assoc.last_synced_workspace_hash == "wh-2"


def test_ensure_user_workspace_up_to_date_silent_false_when_missing(
    db: DBSession, data_root: Path, owner: User
) -> None:
    """Missing app or association silently resolve to False (spec §4.3)."""
    missing_app = asyncio.run(
        agent_apps_service.ensure_user_workspace_up_to_date(
            db, user_id=owner.id, app_id=999
        )
    )
    assert missing_app is False

    app_row = _seed_app(db, name="quiet-app", skill_names=[], status="published")
    missing_assoc = asyncio.run(
        agent_apps_service.ensure_user_workspace_up_to_date(
            db, user_id=owner.id, app_id=app_row.id
        )
    )
    assert missing_assoc is False
