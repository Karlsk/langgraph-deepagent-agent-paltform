"""Agent assets schema and session.agent_app_id backfill.

Revision ID: e4f1a8c2b9d3
Revises: b25d38b0cd7c
Create Date: 2026-08-12 10:00:00.000000

"""

from typing import Sequence, Union

import sqlalchemy as sa
import sqlmodel  # noqa: F401

from alembic import op

from app.models.agent_assets import DEFAULT_AGENT_APP_ID

# revision identifiers, used by Alembic.
revision: str = "e4f1a8c2b9d3"  # pragma: allowlist secret
down_revision: Union[str, Sequence[str], None] = "b25d38b0cd7c"
branch_labels: Union[str, Sequence[str], None] = None
depends_on: Union[str, Sequence[str], None] = None


def upgrade() -> None:
    """Upgrade schema."""
    op.create_table(
        "subagent_config",
        sa.Column("created_at", sa.DateTime(), nullable=False),
        sa.Column("name", sqlmodel.sql.sqltypes.AutoString(), nullable=False),
        sa.Column("description", sqlmodel.sql.sqltypes.AutoString(), nullable=False),
        sa.Column("when_to_use", sqlmodel.sql.sqltypes.AutoString(), nullable=False),
        sa.Column("system_prompt", sqlmodel.sql.sqltypes.AutoString(), nullable=False),
        sa.Column("allowed_tools", sa.JSON(), nullable=True),
        sa.Column("model", sqlmodel.sql.sqltypes.AutoString(), nullable=True),
        sa.Column("max_turns", sa.Integer(), nullable=True),
        sa.Column("content_hash", sqlmodel.sql.sqltypes.AutoString(), nullable=False),
        sa.Column("version", sa.Integer(), nullable=False),
        sa.Column("created_by", sqlmodel.sql.sqltypes.AutoString(), nullable=True),
        sa.PrimaryKeyConstraint("name"),
    )
    op.create_table(
        "skill_asset",
        sa.Column("created_at", sa.DateTime(), nullable=False),
        sa.Column("name", sqlmodel.sql.sqltypes.AutoString(), nullable=False),
        sa.Column("description", sqlmodel.sql.sqltypes.AutoString(), nullable=False),
        sa.Column("content_hash", sqlmodel.sql.sqltypes.AutoString(), nullable=False),
        sa.Column("created_by", sqlmodel.sql.sqltypes.AutoString(), nullable=True),
        sa.Column("version", sa.Integer(), nullable=False),
        sa.PrimaryKeyConstraint("name"),
    )
    op.create_table(
        "agent_app",
        sa.Column("created_at", sa.DateTime(), nullable=False),
        sa.Column("id", sa.Integer(), nullable=False),
        sa.Column("name", sqlmodel.sql.sqltypes.AutoString(), nullable=False),
        sa.Column("system_prompt", sqlmodel.sql.sqltypes.AutoString(), nullable=False),
        sa.Column("allowed_tools", sa.JSON(), nullable=True),
        sa.Column("model", sqlmodel.sql.sqltypes.AutoString(), nullable=True),
        sa.Column("skill_names", sa.JSON(), nullable=False),
        sa.Column("subagent_names", sa.JSON(), nullable=False),
        sa.Column("interrupt_on", sa.JSON(), nullable=False),
        sa.Column("engine", sqlmodel.sql.sqltypes.AutoString(), nullable=False),
        sa.Column("status", sqlmodel.sql.sqltypes.AutoString(), nullable=False),
        sa.Column("published_hash", sqlmodel.sql.sqltypes.AutoString(), nullable=True),
        sa.Column("version", sa.Integer(), nullable=False),
        sa.Column("created_by", sqlmodel.sql.sqltypes.AutoString(), nullable=True),
        sa.PrimaryKeyConstraint("id"),
    )
    op.create_index(op.f("ix_agent_app_name"), "agent_app", ["name"], unique=True)
    op.create_table(
        "mcp_server_config",
        sa.Column("created_at", sa.DateTime(), nullable=False),
        sa.Column("name", sqlmodel.sql.sqltypes.AutoString(), nullable=False),
        sa.Column("transport", sqlmodel.sql.sqltypes.AutoString(), nullable=False),
        sa.Column("command", sqlmodel.sql.sqltypes.AutoString(), nullable=True),
        sa.Column("args", sa.JSON(), nullable=False),
        sa.Column("env", sa.JSON(), nullable=False),
        sa.Column("url", sqlmodel.sql.sqltypes.AutoString(), nullable=True),
        sa.Column("headers", sa.JSON(), nullable=False),
        sa.Column("enabled", sa.Boolean(), nullable=False),
        sa.Column("description", sqlmodel.sql.sqltypes.AutoString(), nullable=False),
        sa.Column("content_hash", sqlmodel.sql.sqltypes.AutoString(), nullable=False),
        sa.Column("created_by", sqlmodel.sql.sqltypes.AutoString(), nullable=True),
        sa.PrimaryKeyConstraint("name"),
    )
    op.add_column("session", sa.Column("agent_app_id", sqlmodel.sql.sqltypes.AutoString(), nullable=True))
    op.create_index(op.f("ix_session_agent_app_id"), "session", ["agent_app_id"], unique=False)
    # Backfill: bind every pre-existing session to the shared default agent app.
    op.execute(
        sa.text("UPDATE session SET agent_app_id = :agent_app_id").bindparams(agent_app_id=DEFAULT_AGENT_APP_ID)
    )


def downgrade() -> None:
    """Downgrade schema."""
    op.drop_index(op.f("ix_session_agent_app_id"), table_name="session")
    op.drop_column("session", "agent_app_id")
    op.drop_table("mcp_server_config")
    op.drop_index(op.f("ix_agent_app_name"), table_name="agent_app")
    op.drop_table("agent_app")
    op.drop_table("skill_asset")
    op.drop_table("subagent_config")
