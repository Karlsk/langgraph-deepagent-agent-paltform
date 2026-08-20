"""Add skill_names column to subagent_config.

Revision ID: c1a7b9d3f204
Revises: a3f7e9b1c852
Create Date: 2026-08-22 10:00:00.000000

Symmetric to ``AgentApp.skill_names``. A NULL value means "inherit the parent
agent app's skill set"; an empty list means "explicitly bind no skills"; a
non-empty list is treated as a whitelist scoped to this sub-agent. The column
defaults to NULL on existing rows so the new field is backwards compatible.
"""

from typing import Sequence, Union

import sqlalchemy as sa

from alembic import op

# revision identifiers, used by Alembic.
revision: str = "c1a7b9d3f204"  # pragma: allowlist secret
down_revision: Union[str, Sequence[str], None] = "a3f7e9b1c852"
branch_labels: Union[str, Sequence[str], None] = None
depends_on: Union[str, Sequence[str], None] = None


def upgrade() -> None:
    """Upgrade schema."""
    op.add_column(
        "subagent_config",
        sa.Column("skill_names", sa.JSON(), nullable=True),
    )


def downgrade() -> None:
    """Downgrade schema."""
    op.drop_column("subagent_config", "skill_names")
