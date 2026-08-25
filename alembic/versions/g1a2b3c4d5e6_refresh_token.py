"""refresh_token table for G1 Refresh Token mechanism.

Revision ID: g1a2b3c4d5e6
Revises: a9d4e2f7b315
Create Date: 2026-08-25 10:00:00.000000

Phase 1 G1: introduces the ``refresh_token`` table backing the new
``POST /auth/refresh`` + ``POST /auth/logout`` endpoints. The raw refresh
token never leaves the client; the row stores its SHA-256 hex digest and
supports rotation (flip ``revoked`` + sibling insert) and replay detection
(bulk-revoke every active token of the owning user).

Indexes:
  - ``ix_refresh_token_user_id``: per-user revocation lookups
  - ``ix_refresh_token_expires_at``: cleanup-window scans
  - ``uq_refresh_token_token_hash``: hash uniqueness via the unique constraint
"""

from typing import Sequence, Union

import sqlalchemy as sa
import sqlmodel  # noqa: F401

from alembic import op

# revision identifiers, used by Alembic.
revision: str = "g1a2b3c4d5e6"  # pragma: allowlist secret
down_revision: Union[str, Sequence[str], None] = "a9d4e2f7b315"
branch_labels: Union[str, Sequence[str], None] = None
depends_on: Union[str, Sequence[str], None] = None


def upgrade() -> None:
    """Upgrade schema."""
    op.create_table(
        "refresh_token",
        sa.Column("created_at", sa.DateTime(), nullable=False),
        sa.Column("id", sa.Integer(), nullable=False),
        sa.Column("user_id", sa.Integer(), nullable=False),
        sa.Column("token_hash", sqlmodel.sql.sqltypes.AutoString(length=64), nullable=False),
        sa.Column("expires_at", sa.DateTime(), nullable=False),
        sa.Column("revoked", sa.Boolean(), nullable=False, server_default=sa.text("false")),
        sa.Column("last_used_at", sa.DateTime(), nullable=True),
        sa.ForeignKeyConstraint(["user_id"], ["user.id"]),
        sa.PrimaryKeyConstraint("id"),
        sa.UniqueConstraint("token_hash", name="uq_refresh_token_token_hash"),
    )
    op.create_index(op.f("ix_refresh_token_user_id"), "refresh_token", ["user_id"], unique=False)
    op.create_index(op.f("ix_refresh_token_expires_at"), "refresh_token", ["expires_at"], unique=False)


def downgrade() -> None:
    """Downgrade schema."""
    op.drop_index(op.f("ix_refresh_token_expires_at"), table_name="refresh_token")
    op.drop_index(op.f("ix_refresh_token_user_id"), table_name="refresh_token")
    op.drop_table("refresh_token")
