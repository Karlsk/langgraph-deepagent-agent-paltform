"""Unit tests for the ``RefreshToken`` ORM model (Phase 1 G1, TC-02).

Verifies the table projection (name / columns / unique token_hash) and a
full persist-reload roundtrip on an in-memory SQLite engine. Zero network,
zero LLM.
"""

from collections.abc import Generator
from datetime import UTC, datetime, timedelta

import pytest
from sqlalchemy.pool import StaticPool
from sqlmodel import SQLModel
from sqlmodel import Session as DBSession
from sqlmodel import create_engine

from app.models.refresh_token import RefreshToken
from app.models.user import User
from app.utils.auth import create_refresh_token, hash_refresh_token

pytestmark = pytest.mark.unit


def test_table_projection_matches_contract() -> None:
    """Table name, column set and token_hash uniqueness follow the contract."""
    table = RefreshToken.__table__
    assert table.name == "refresh_token"
    expected_columns = {
        "id",
        "user_id",
        "token_hash",
        "expires_at",
        "revoked",
        "last_used_at",
        "created_at",
    }
    assert expected_columns <= set(table.columns.keys())
    assert table.columns["token_hash"].unique is True  # raw token never stored twice


def test_token_hash_column_width_is_64() -> None:
    """The token_hash column stores exactly a 64-char sha256 hex digest."""
    column = RefreshToken.__table__.columns["token_hash"]
    assert column.type.length == 64  # sha256 hex digest fits exactly


@pytest.fixture
def session() -> Generator[DBSession, None, None]:
    """In-memory SQLite session with every table created (StaticPool)."""
    engine = create_engine(
        "sqlite://",
        connect_args={"check_same_thread": False},
        poolclass=StaticPool,
    )
    SQLModel.metadata.create_all(engine)
    db = DBSession(engine)
    yield db
    db.close()


def _seed_user(db: DBSession) -> User:
    user = User(
        email="model-test@example.com",
        hashed_password=User.hash_password("Passw0rd!Strong"),
        username="model-tester",
    )
    db.add(user)
    db.commit()
    db.refresh(user)
    return user


def test_persist_and_reload_roundtrip(session: DBSession) -> None:
    """A created row reloads with defaults (revoked=False, last_used_at=None)."""
    user = _seed_user(session)
    raw = create_refresh_token()
    row = RefreshToken(
        user_id=user.id,
        token_hash=hash_refresh_token(raw),
        expires_at=datetime.now(UTC) + timedelta(days=30),
    )
    session.add(row)
    session.commit()
    session.refresh(row)

    assert row.id is not None
    assert row.user_id == user.id
    assert row.token_hash == hash_refresh_token(raw)
    assert row.revoked is False
    assert row.last_used_at is None
    assert row.created_at is not None

    reloaded = session.get(RefreshToken, row.id)
    assert reloaded is not None
    assert reloaded.token_hash == row.token_hash


def test_revoked_and_last_used_at_are_mutable(session: DBSession) -> None:
    """Rotation/logout fields persist across commit + reload."""
    user = _seed_user(session)
    row = RefreshToken(
        user_id=user.id,
        token_hash=hash_refresh_token(create_refresh_token()),
        expires_at=datetime.now(UTC) + timedelta(days=30),
    )
    session.add(row)
    session.commit()
    session.refresh(row)

    row.revoked = True
    row.last_used_at = datetime.now(UTC)
    session.add(row)
    session.commit()

    reloaded = session.get(RefreshToken, row.id)
    assert reloaded is not None
    assert reloaded.revoked is True
    assert reloaded.last_used_at is not None
