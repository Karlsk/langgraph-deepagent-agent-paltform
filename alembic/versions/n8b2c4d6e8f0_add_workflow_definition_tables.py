"""Add workflow_definition, workflow_node, workflow_edge tables.

Revision ID: n8b2c4d6e8f0
Revises: m7a9b1c3d5e7
Create Date: 2026-09-16 14:00:00.000000
"""

from typing import Sequence, Union

import sqlalchemy as sa
from alembic import op

revision: str = "n8b2c4d6e8f0"
down_revision: Union[str, None] = "m7a9b1c3d5e7"
branch_labels: Union[str, Sequence[str], None] = None
depends_on: Union[str, Sequence[str], None] = None


def upgrade() -> None:
    """Create 3-table normalized workflow definition schema."""
    op.create_table(
        "workflow_definition",
        sa.Column("workflow_id", sa.String(64), primary_key=True),
        sa.Column("description", sa.String(), nullable=False, server_default=""),
        sa.Column("entry_point", sa.String(), nullable=False),
        sa.Column("allow_private_networks", sa.Boolean(), nullable=False, server_default=sa.text("true")),
        sa.Column("body", sa.Text(), nullable=True),
        sa.Column("content_hash", sa.String(64), nullable=False, server_default=""),
        sa.Column("version", sa.Integer(), nullable=False, server_default="1"),
        sa.Column("scope", sa.String(16), nullable=False, server_default="user"),
        sa.Column("created_at", sa.DateTime(), nullable=False, server_default=sa.text("now()")),
        sa.Column("updated_at", sa.DateTime(timezone=True), nullable=False, server_default=sa.text("now()")),
    )
    op.create_index("ix_workflow_definition_scope", "workflow_definition", ["scope"])

    op.create_table(
        "workflow_node",
        sa.Column("id", sa.Integer(), primary_key=True, autoincrement=True),
        sa.Column(
            "workflow_id",
            sa.String(64),
            sa.ForeignKey("workflow_definition.workflow_id", ondelete="CASCADE"),
            nullable=False,
        ),
        sa.Column("name", sa.String(128), nullable=False),
        sa.Column("type", sa.String(64), nullable=False),
        sa.Column("config", sa.JSON(), nullable=False, server_default="{}"),
        sa.Column("state_keys", sa.JSON(), nullable=True),
    )
    op.create_index("ix_workflow_node_workflow_id", "workflow_node", ["workflow_id"])

    op.create_table(
        "workflow_edge",
        sa.Column("id", sa.Integer(), primary_key=True, autoincrement=True),
        sa.Column(
            "workflow_id",
            sa.String(64),
            sa.ForeignKey("workflow_definition.workflow_id", ondelete="CASCADE"),
            nullable=False,
        ),
        sa.Column("source", sa.String(128), nullable=False),
        sa.Column("target", sa.String(128), nullable=False),
        sa.Column("condition", sa.String(512), nullable=True),
    )
    op.create_index("ix_workflow_edge_workflow_id", "workflow_edge", ["workflow_id"])


def downgrade() -> None:
    """Drop workflow_definition, workflow_node, workflow_edge tables."""
    op.drop_index("ix_workflow_edge_workflow_id", table_name="workflow_edge")
    op.drop_table("workflow_edge")
    op.drop_index("ix_workflow_node_workflow_id", table_name="workflow_node")
    op.drop_table("workflow_node")
    op.drop_index("ix_workflow_definition_scope", table_name="workflow_definition")
    op.drop_table("workflow_definition")
