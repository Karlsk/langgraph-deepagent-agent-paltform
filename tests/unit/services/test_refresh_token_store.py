"""Unit tests for ``RefreshTokenStore`` (Phase 1 G1, TC-04).

Covers create/lookup, rotation, single revoke (idempotent), bulk revoke for
replay detection, settings-driven TTL, and the active-count gauge feed. All
operations run against an in-memory SQLite engine (StaticPool); zero network,
zero LLM.
"""

import asyncio
from collections.abc import Generator
from datetime import UTC, datetime, timedelta

import pytest
from sqlalchemy.pool import StaticPool
from sqlmodel import SQLModel
from sqlmodel import Session as DBSession
from sqlmodel import create_engine

from app.core.config import settings
from app.models.refresh_token import RefreshToken
from app.models.user import User
from app.services.refresh_token_store import RefreshTokenStore, ensure_utc
from app.utils.auth import create_refresh_token, hash_refresh_token

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
    """One seeded user owning the refresh tokens under test."""
    row = User(
        email="store-test@example.com",
        hashed_password=User.hash_password("Passw0rd!Strong"),
        username="store-tester",
    )
    db.add(row)
    db.commit()
    db.refresh(row)
    return row


@pytest.fixture
def store() -> RefreshTokenStore:
    """A fresh store instance (module singleton stays untouched)."""
    return RefreshTokenStore()


def test_create_and_lookup(db: DBSession, user: User, store: RefreshTokenStore) -> None:
    """Created tokens are retrievable by raw value with a matching hash."""
    raw = create_refresh_token()
    created = asyncio.run(store.create(db, user_id=user.id, raw_token=raw))

    assert created.id is not None
    assert created.user_id == user.id
    assert created.revoked is False
    assert created.token_hash == hash_refresh_token(raw)

    found = asyncio.run(store.lookup(db, raw))
    assert found is not None
    assert found.id == created.id
    assert found.token_hash == created.token_hash


def test_create_respects_settings_ttl(
    db: DBSession, user: User, store: RefreshTokenStore, monkeypatch: pytest.MonkeyPatch
) -> None:
    """Issued expiry follows settings.JWT_REFRESH_TOKEN_EXPIRE_DAYS."""
    monkeypatch.setattr(settings, "JWT_REFRESH_TOKEN_EXPIRE_DAYS", 1)
    before = datetime.now(UTC)
    created = asyncio.run(store.create(db, user_id=user.id, raw_token=create_refresh_token()))

    # SQLite hands back naive datetimes; ensure_utc normalises before subtracting.
    delta = ensure_utc(created.expires_at) - before
    assert timedelta(hours=23) < delta < timedelta(days=1, hours=1)


def test_lookup_returns_none_for_unknown(db: DBSession, store: RefreshTokenStore) -> None:
    """Unknown raw tokens resolve to None."""
    assert asyncio.run(store.lookup(db, create_refresh_token())) is None


def test_rotate_revokes_old(db: DBSession, user: User, store: RefreshTokenStore) -> None:
    """Rotation revokes the old row and persists a usable successor."""
    old_raw = create_refresh_token()
    old = asyncio.run(store.create(db, user_id=user.id, raw_token=old_raw))

    new_raw = create_refresh_token()
    new = asyncio.run(store.rotate(db, old, new_raw=new_raw))

    assert new.id != old.id
    assert new.revoked is False

    stale = asyncio.run(store.lookup(db, old_raw))
    assert stale is not None
    assert stale.revoked is True
    assert stale.last_used_at is not None

    fresh = asyncio.run(store.lookup(db, new_raw))
    assert fresh is not None
    assert fresh.revoked is False


def test_revoke(db: DBSession, user: User, store: RefreshTokenStore) -> None:
    """Revoke flips an active row to revoked."""
    raw = create_refresh_token()
    asyncio.run(store.create(db, user_id=user.id, raw_token=raw))

    assert asyncio.run(store.revoke(db, raw)) is True
    found = asyncio.run(store.lookup(db, raw))
    assert found is not None
    assert found.revoked is True


def test_revoke_returns_false_for_unknown(db: DBSession, store: RefreshTokenStore) -> None:
    """Revoke returns False (no exception) for unknown tokens."""
    assert asyncio.run(store.revoke(db, create_refresh_token())) is False


def test_revoke_idempotent(db: DBSession, user: User, store: RefreshTokenStore) -> None:
    """Repeating revoke is a no-op returning False."""
    raw = create_refresh_token()
    asyncio.run(store.create(db, user_id=user.id, raw_token=raw))

    assert asyncio.run(store.revoke(db, raw)) is True
    assert asyncio.run(store.revoke(db, raw)) is False  # already revoked, no error


def test_revoke_all_for_user(db: DBSession, user: User, store: RefreshTokenStore) -> None:
    """Bulk revoke hits only the target user's active tokens."""
    raws = [create_refresh_token() for _ in range(3)]
    for raw in raws:
        asyncio.run(store.create(db, user_id=user.id, raw_token=raw))

    other = User(
        email="other@example.com",
        hashed_password=User.hash_password("Passw0rd!Strong"),
        username="other",
    )
    db.add(other)
    db.commit()
    db.refresh(other)
    other_raw = create_refresh_token()
    asyncio.run(store.create(db, user_id=other.id, raw_token=other_raw))

    revoked_count = asyncio.run(store.revoke_all_for_user(db, user.id))
    assert revoked_count == 3
    for raw in raws:
        found = asyncio.run(store.lookup(db, raw))
        assert found is not None
        assert found.revoked is True

    untouched = asyncio.run(store.lookup(db, other_raw))
    assert untouched is not None
    assert untouched.revoked is False


def test_count_active(db: DBSession, user: User, store: RefreshTokenStore) -> None:
    """Active count tracks create/revoke transitions."""
    assert asyncio.run(store.count_active(db)) == 0

    kept = create_refresh_token()
    revoked = create_refresh_token()
    asyncio.run(store.create(db, user_id=user.id, raw_token=kept))
    asyncio.run(store.create(db, user_id=user.id, raw_token=revoked))
    assert asyncio.run(store.count_active(db)) == 2

    asyncio.run(store.revoke(db, revoked))
    assert asyncio.run(store.count_active(db)) == 1


def test_count_active_excludes_expired(db: DBSession, user: User, store: RefreshTokenStore) -> None:
    """Expired rows are excluded from the active count."""
    expired = RefreshToken(
        user_id=user.id,
        token_hash=hash_refresh_token(create_refresh_token()),
        expires_at=datetime.now(UTC) - timedelta(days=1),
        revoked=False,
    )
    db.add(expired)
    db.commit()

    assert asyncio.run(store.count_active(db)) == 0
