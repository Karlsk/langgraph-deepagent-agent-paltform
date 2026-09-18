"""Add permission groups and permission preset to agent_app.

Revision ID: q2e5f7g9h3i4
Revises: p1d4e6f8a2b4
Create Date: 2026-09-18 10:00:00.000000

Two-layer permission system:
- permission_preset: enum-like field (none/strict/destructive_only) for built-in presets
- permission_group_id: FK to permission_group table for custom reusable tool lists

Resolution priority: permission_group_id > permission_preset > interrupt_on (legacy).
"""

from typing import Sequence, Union

import sqlalchemy as sa

from alembic import op

# revision identifiers, used by Alembic.
revision: str = "q2e5f7g9h3i4"  # pragma: allowlist secret
down_revision: Union[str, Sequence[str], None] = "p1d4e6f8a2b4"
branch_labels: Union[str, Sequence[str], None] = None
depends_on: Union[str, Sequence[str], None] = None


def upgrade() -> None:
    """Upgrade schema."""
    op.create_table(
        "permission_group",
        sa.Column("id", sa.Integer(), nullable=False),
        sa.Column("name", sa.String(length=64), nullable=False),
        sa.Column("tool_names", sa.JSON(), nullable=False, server_default="[]"),
        sa.Column("description", sa.String(), nullable=False, server_default=""),
        sa.Column("created_by", sa.String(), nullable=True),
        sa.Column("created_at", sa.DateTime(timezone=True), nullable=False, server_default=sa.func.now()),
        sa.PrimaryKeyConstraint("id"),
    )
    op.create_index(op.f("ix_permission_group_name"), "permission_group", ["name"], unique=True)

    op.add_column("agent_app", sa.Column("permission_preset", sa.String(length=32), nullable=True))
    op.add_column("agent_app", sa.Column("permission_group_id", sa.Integer(), nullable=True))
    op.create_index(op.f("ix_agent_app_permission_group_id"), "agent_app", ["permission_group_id"], unique=False)


def downgrade() -> None:
    """Downgrade schema."""
    op.drop_index(op.f("ix_agent_app_permission_group_id"), table_name="agent_app")
    op.drop_column("agent_app", "permission_group_id")
    op.drop_column("agent_app", "permission_preset")
    op.drop_index(op.f("ix_permission_group_name"), table_name="permission_group")
    op.drop_table("permission_group")
