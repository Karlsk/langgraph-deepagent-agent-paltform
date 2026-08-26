"""G2 agent workspace: workspace columns + user_agent_app_association table.

Revision ID: h3a5c7e9b1d3
Revises: g1a2b3c4d5e6
Create Date: 2026-08-26 10:00:00.000000

Phase G2: three-layer workspace infrastructure (spec-g2-workspace v3.3 §3):

- ``skill_asset.scope`` String(16) NOT NULL DEFAULT 'global' — existing rows
  are backfilled to 'global' via the server_default on ADD COLUMN.
- ``agent_app.agent_dir`` String(255) NULL — physical workspace base path
  (row-level backfill is deferred to bootstrap, see spec §3.2 note).
- ``agent_app.workspace_hash`` String(64) NULL — agent-layer content
  fingerprint set at publish time.
- ``agent_app.agent_workspace_status`` String(16) NOT NULL DEFAULT 'pending'
  ('pending' | 'active') — existing rows backfilled to 'pending'.
- ``user_agent_app_association`` — per-user workspace sync tracking with
  UNIQUE(user_id, agent_app_id) and FK CASCADE on both sides.

Indexes:
  - ``ix_skill_asset_scope``: scope-filtered skill listings
  - ``ix_user_agent_app_user_id`` / ``ix_user_agent_app_agent_app_id``:
    association lookups from either side
"""

from typing import Sequence, Union

import sqlalchemy as sa

from alembic import op

# revision identifiers, used by Alembic.
revision: str = "h3a5c7e9b1d3"  # pragma: allowlist secret
down_revision: Union[str, Sequence[str], None] = "g1a2b3c4d5e6"
branch_labels: Union[str, Sequence[str], None] = None
depends_on: Union[str, Sequence[str], None] = None


def upgrade() -> None:
    """Upgrade schema."""
    # --- SkillAsset.scope (backfill 'global' via server_default) ---
    op.add_column(
        "skill_asset",
        sa.Column("scope", sa.String(length=16), nullable=False, server_default="global"),
    )
    op.create_index("ix_skill_asset_scope", "skill_asset", ["scope"], unique=False)

    # --- AgentApp workspace fields (backfill 'pending' via server_default) ---
    op.add_column("agent_app", sa.Column("agent_dir", sa.String(length=255), nullable=True))
    op.add_column("agent_app", sa.Column("workspace_hash", sa.String(length=64), nullable=True))
    op.add_column(
        "agent_app",
        sa.Column(
            "agent_workspace_status",
            sa.String(length=16),
            nullable=False,
            server_default="pending",
        ),
    )

    # --- user_agent_app_association (G2 user-layer tracking, spec §3.4) ---
    op.create_table(
        "user_agent_app_association",
        sa.Column("created_at", sa.DateTime(), nullable=False),
        sa.Column("id", sa.Integer(), nullable=False),
        sa.Column("user_id", sa.Integer(), nullable=False),
        sa.Column("agent_app_id", sa.Integer(), nullable=False),
        sa.Column("last_synced_workspace_hash", sa.String(length=64), nullable=True),
        sa.Column("associated_at", sa.DateTime(), nullable=False, server_default=sa.func.now()),
        sa.ForeignKeyConstraint(["agent_app_id"], ["agent_app.id"], ondelete="CASCADE"),
        sa.ForeignKeyConstraint(["user_id"], ["user.id"], ondelete="CASCADE"),
        sa.PrimaryKeyConstraint("id"),
        sa.UniqueConstraint("user_id", "agent_app_id", name="uq_user_agent_app"),
    )
    op.create_index(
        "ix_user_agent_app_user_id", "user_agent_app_association", ["user_id"], unique=False
    )
    op.create_index(
        "ix_user_agent_app_agent_app_id",
        "user_agent_app_association",
        ["agent_app_id"],
        unique=False,
    )


def downgrade() -> None:
    """Downgrade schema."""
    op.drop_index("ix_user_agent_app_agent_app_id", table_name="user_agent_app_association")
    op.drop_index("ix_user_agent_app_user_id", table_name="user_agent_app_association")
    op.drop_table("user_agent_app_association")
    op.drop_column("agent_app", "agent_workspace_status")
    op.drop_column("agent_app", "workspace_hash")
    op.drop_column("agent_app", "agent_dir")
    op.drop_index("ix_skill_asset_scope", table_name="skill_asset")
    op.drop_column("skill_asset", "scope")
