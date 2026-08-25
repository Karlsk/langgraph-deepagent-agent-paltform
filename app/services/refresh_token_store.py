"""RefreshTokenStore: CRUD + rotation + replay detection for refresh tokens.

Phase 1 G1 single-layer auth. Each issued refresh token is persisted as a
row whose ``token_hash`` is the sha256 hex digest of the raw value; the raw
token is only seen by the client. Operations are ``async`` (matching the
rest of the DB service layer) but execute plain SQLModel sync sessions.
"""

from datetime import (
    UTC,
    datetime,
    timedelta,
)

from sqlmodel import Session, col, func, select

from app.core.config import settings
from app.models.refresh_token import RefreshToken
from app.utils.auth import hash_refresh_token


def ensure_utc(value: datetime) -> datetime:
    """Return ``value`` guaranteed to carry UTC tzinfo.

    SQLite (the test engine) stores ``DateTime`` without timezone and hands
    back naive datetimes; PostgreSQL may do the same depending on the column
    type. Every comparison against ``datetime.now(UTC)`` must go through
    this helper to avoid naive/aware ``TypeError``.

    Args:
        value: A datetime that is either aware (any zone) or naive (UTC implied).

    Returns:
        datetime: The same instant normalised to UTC.
    """
    if value.tzinfo is None:
        return value.replace(tzinfo=UTC)
    return value.astimezone(UTC)


class RefreshTokenStore:
    """Stateless helper for refresh-token persistence.

    Methods:
        create: Persist a new (user_id, raw_token) pair.
        lookup: Resolve a raw token to its RefreshToken row (or None).
        rotate: Mark ``old`` as revoked and persist the new token sibling.
        revoke: Best-effort revoke of a single token (idempotent).
        revoke_all_for_user: Bulk-revoke every active token of a user
            (replay-detection response).
        count_active: Number of non-revoked, non-expired tokens (metrics).
    """

    @property
    def refresh_token_ttl(self) -> timedelta:
        """Refresh-token lifetime, driven by ``settings.JWT_REFRESH_TOKEN_EXPIRE_DAYS``."""
        return timedelta(days=settings.JWT_REFRESH_TOKEN_EXPIRE_DAYS)

    async def create(self, db: Session, *, user_id: int, raw_token: str) -> RefreshToken:
        """Persist a new refresh-token row for ``user_id``.

        Args:
            db: SQLModel session bound to the auth engine.
            user_id: Owning user primary key.
            raw_token: The raw refresh token string (will be hashed).

        Returns:
            RefreshToken: The persisted row (with ``id`` populated).
        """
        token_hash = hash_refresh_token(raw_token)
        expires_at = datetime.now(UTC) + self.refresh_token_ttl
        record = RefreshToken(user_id=user_id, token_hash=token_hash, expires_at=expires_at)
        db.add(record)
        db.commit()
        db.refresh(record)
        return record

    async def lookup(self, db: Session, raw_token: str) -> RefreshToken | None:
        """Return the matching row for ``raw_token`` (or None when unknown).

        Args:
            db: SQLModel session.
            raw_token: The raw refresh token value.

        Returns:
            RefreshToken | None: The matching row or None.
        """
        token_hash = hash_refresh_token(raw_token)
        return db.exec(select(RefreshToken).where(col(RefreshToken.token_hash) == token_hash)).first()

    async def rotate(self, db: Session, old: RefreshToken, *, new_raw: str) -> RefreshToken:
        """Mark ``old`` as revoked and persist the rotation sibling.

        Both writes share the same transaction so an unexpected commit failure
        leaves neither half-state. ``old.last_used_at`` records the rotation
        timestamp for forensic inspection.

        Args:
            db: SQLModel session.
            old: The refresh-token row being rotated.
            new_raw: The raw token value of the replacement.

        Returns:
            RefreshToken: The newly persisted row.
        """
        old.revoked = True
        old.last_used_at = datetime.now(UTC)
        db.add(old)
        new_record = await self.create(db, user_id=old.user_id, raw_token=new_raw)
        return new_record

    async def revoke(self, db: Session, raw_token: str) -> bool:
        """Best-effort revoke of a single token (idempotent).

        Args:
            db: SQLModel session.
            raw_token: The raw refresh token value to revoke.

        Returns:
            bool: True when an active row was flipped to revoked; False when
            the token was unknown or already revoked (idempotent).
        """
        existing = await self.lookup(db, raw_token)
        if existing is None or existing.revoked:
            return False
        existing.revoked = True
        existing.last_used_at = datetime.now(UTC)
        db.add(existing)
        db.commit()
        return True

    async def revoke_all_for_user(self, db: Session, user_id: int) -> int:
        """Bulk-revoke every non-revoked refresh token of ``user_id``.

        Used by the replay-detection branch in ``POST /auth/refresh``: when a
        revoked token is presented again, every active device for that user
        is forcibly logged out.

        Args:
            db: SQLModel session.
            user_id: Owning user primary key.

        Returns:
            int: Count of rows flipped from active to revoked.
        """
        tokens = db.exec(
            select(RefreshToken).where(
                col(RefreshToken.user_id) == user_id,
                col(RefreshToken.revoked) == False,  # noqa: E712 — explicit SQLModel bool filter
            )
        ).all()
        now = datetime.now(UTC)
        for token in tokens:
            token.revoked = True
            token.last_used_at = now
            db.add(token)
        db.commit()
        return len(tokens)

    async def count_active(self, db: Session) -> int:
        """Count non-revoked, non-expired refresh tokens (feeds the Gauge).

        Args:
            db: SQLModel session.

        Returns:
            int: Number of currently active refresh-token rows.
        """
        statement = (
            select(func.count())
            .select_from(RefreshToken)
            .where(
                col(RefreshToken.revoked) == False,  # noqa: E712 — explicit SQLModel bool filter
                col(RefreshToken.expires_at) > datetime.now(UTC),
            )
        )
        return int(db.exec(statement).one())


refresh_token_store = RefreshTokenStore()
