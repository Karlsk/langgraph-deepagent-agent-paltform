"""RefreshToken model — stores SHA-256 hashes of issued refresh tokens.

Phase 1 G1 single-layer auth: each issued refresh token is persisted as a row
whose ``token_hash`` is the sha256 hex digest of the raw value. The raw token
is only seen by the client; rotation is recorded by flipping ``revoked``
and creating a sibling row, replay detection by traversing revoked rows back
to the owning ``user_id`` and bulk-revoking every active token of that user.
"""

from datetime import datetime

from sqlmodel import Field

from app.models.base import BaseModel


class RefreshToken(BaseModel, table=True):
    """Refresh-token persistence (sha256 hash + lifecycle metadata).

    Attributes:
        id: Primary key.
        user_id: Owning user foreign key.
        token_hash: Sha256 hex digest of the raw refresh token (64 chars, unique).
        expires_at: Absolute UTC expiration timestamp (driven by
            ``settings.JWT_REFRESH_TOKEN_EXPIRE_DAYS`` at issue time).
        revoked: True once the row has been rotated or explicitly logged out.
        last_used_at: UTC timestamp of the last rotation against this row (or None).
        created_at: Inherited from ``BaseModel`` (issued-at).
    """

    __tablename__ = "refresh_token"  # pyright: ignore[reportAssignmentType]

    id: int | None = Field(default=None, primary_key=True)
    user_id: int = Field(foreign_key="user.id", index=True)
    token_hash: str = Field(unique=True, index=True, max_length=64)
    expires_at: datetime = Field(index=True)
    revoked: bool = Field(default=False)
    last_used_at: datetime | None = Field(default=None)


__all__ = ["RefreshToken"]
