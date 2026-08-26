"""Unit tests for ``app/services/db_service.py`` association CRUD (G2 Phase 2).

Covers the ``UserAgentAppAssociation`` helpers consumed by the agent-app
service layer: idempotent get-or-create (spec-g2-workspace v3.3 §9.5) and
the user-layer cache invalidation applied on publish / PATCH (spec §5.2).
All operations run against an in-memory SQLite engine (StaticPool); zero
network, zero LLM.
"""

import asyncio
from collections.abc import Generator

import pytest
from sqlalchemy.pool import StaticPool
from sqlmodel import SQLModel
from sqlmodel import Session as DBSession
from sqlmodel import create_engine, select

from app.models.agent_assets import AgentApp, UserAgentAppAssociation
from app.models.user import User
from app.services import db_service

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


@pytest.fixture
def user(db: DBSession) -> User:
    """One seeded end user eligible for app association."""
    row = User(
        email="assoc-test@example.com",
        hashed_password=User.hash_password("Passw0rd!Strong"),
        username="assoc-tester",
    )
    db.add(row)
    db.commit()
    db.refresh(row)
    return row


@pytest.fixture
def app_row(db: DBSession) -> AgentApp:
    """One seeded draft agent app."""
    row = AgentApp(name="assoc-app", system_prompt="x")
    db.add(row)
    db.commit()
    db.refresh(row)
    return row


def test_get_or_create_association_idempotent(db: DBSession, user: User, app_row: AgentApp) -> None:
    """Two get-or-create calls for the same (user, app) pair return one row."""
    first = asyncio.run(
        db_service._get_or_create_association(db, user_id=user.id, app_id=app_row.id)
    )
    second = asyncio.run(
        db_service._get_or_create_association(db, user_id=user.id, app_id=app_row.id)
    )

    assert first.id == second.id
    assert first.user_id == user.id
    assert first.agent_app_id == app_row.id

    rows = db.exec(select(UserAgentAppAssociation)).all()
    assert len(rows) == 1


def test_get_association_returns_none_for_missing_pair(db: DBSession, user: User, app_row: AgentApp) -> None:
    """An unassociated (user, app) pair resolves to None (lazy-skip signal)."""
    result = asyncio.run(
        db_service._get_association(db, user_id=user.id, app_id=app_row.id)
    )
    assert result is None


def test_invalidate_user_layer_cache_clears_hash(db: DBSession, user: User, app_row: AgentApp) -> None:
    """Invalidation NULLs last_synced_workspace_hash for every app association."""
    asyncio.run(
        db_service._get_or_create_association(db, user_id=user.id, app_id=app_row.id)
    )
    assoc = db.exec(select(UserAgentAppAssociation)).one()
    assoc.last_synced_workspace_hash = "stale-hash"
    db.add(assoc)
    db.commit()

    asyncio.run(db_service._invalidate_user_layer_cache(db, app_cfg=app_row))

    db.refresh(assoc)
    assert assoc.last_synced_workspace_hash is None
