"""Add workflow_run table for persistent execution history.

Revision ID: m7a9b1c3d5e7
Revises: k6d8f2b4e7a8
Create Date: 2026-09-16 10:00:00.000000
"""

from typing import Sequence, Union

import sqlalchemy as sa
from alembic import op

revision: str = "m7a9b1c3d5e7"
down_revision: Union[str, None] = "k6d8f2b4e7a8"
branch_labels: Union[str, Sequence[str], None] = None
depends_on: Union[str, Sequence[str], None] = None


def upgrade() -> None:
    """Create workflow_run table for persistent execution history."""
    op.create_table(
        "workflow_run",
        sa.Column("id", sa.Integer(), primary_key=True, autoincrement=True),
        sa.Column("workflow_id", sa.String(), nullable=False),
        sa.Column("run_id", sa.String(), nullable=False),
        sa.Column("status", sa.String(), nullable=False),
        sa.Column("input_data", sa.JSON(), nullable=False, server_default="{}"),
        sa.Column("output_data", sa.JSON(), nullable=False, server_default="{}"),
        sa.Column("error_message", sa.Text(), nullable=True),
        sa.Column("execution_logs", sa.JSON(), nullable=False, server_default="[]"),
        sa.Column("duration_ms", sa.Float(), nullable=False, server_default="0.0"),
        sa.Column("node_count", sa.Integer(), nullable=False, server_default="0"),
        sa.Column("created_by", sa.String(), nullable=True),
        sa.Column("created_at", sa.DateTime(), nullable=False, server_default=sa.text("now()")),
    )
    op.create_index("ix_workflow_run_workflow_id", "workflow_run", ["workflow_id"])
    op.create_index("ix_workflow_run_run_id", "workflow_run", ["run_id"], unique=True)
    op.create_index("ix_workflow_run_created_at", "workflow_run", ["created_at"])


def downgrade() -> None:
    """Drop workflow_run table."""
    op.drop_index("ix_workflow_run_created_at", table_name="workflow_run")
    op.drop_index("ix_workflow_run_run_id", table_name="workflow_run")
    op.drop_index("ix_workflow_run_workflow_id", table_name="workflow_run")
    op.drop_table("workflow_run")
