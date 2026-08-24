"""Add body column to skill_asset (dual-store source of truth).

Revision ID: d8b2e4f6a913
Revises: c1a7b9d3f204
Create Date: 2026-08-24 10:00:00.000000

Dual-store architecture: the full SKILL.md body moves into the DB row
(``SkillAsset.body``, source of truth) while the disk file under
``{SKILLS_ROOT}/global/<name>/SKILL.md`` stays as the runtime copy consumed
by ``FilesystemBackend``. The column is nullable so pre-dual-store rows keep
working; ``POST /api/v1/skills/refresh`` backfills them from their disk files
(the only surviving copy) and resyncs ``content_hash``.
"""

from typing import Sequence, Union

import sqlalchemy as sa

from alembic import op

# revision identifiers, used by Alembic.
revision: str = "d8b2e4f6a913"  # pragma: allowlist secret
down_revision: Union[str, Sequence[str], None] = "c1a7b9d3f204"
branch_labels: Union[str, Sequence[str], None] = None
depends_on: Union[str, Sequence[str], None] = None


def upgrade() -> None:
    """Upgrade schema."""
    op.add_column(
        "skill_asset",
        sa.Column("body", sa.Text(), nullable=True),
    )


def downgrade() -> None:
    """Downgrade schema."""
    op.drop_column("skill_asset", "body")
