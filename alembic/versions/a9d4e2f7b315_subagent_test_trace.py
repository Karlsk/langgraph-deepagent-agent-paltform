"""Add subagent_test_trace table (one-shot test run traces).

Revision ID: a9d4e2f7b315
Revises: d8b2e4f6a913
Create Date: 2026-08-24 12:00:00.000000

Every ``run_subagent_once`` execution (success or failure) is recorded as a
row carrying the structured event stream captured by ``RunTracer`` (LLM
calls, tool calls, run outcome). The ``events`` JSON column is the single
source of truth for offline behaviour verification and scripted comparison.
"""

from typing import Sequence, Union

import sqlalchemy as sa
import sqlmodel  # noqa: F401

from alembic import op

# revision identifiers, used by Alembic.
revision: str = "a9d4e2f7b315"  # pragma: allowlist secret
down_revision: Union[str, Sequence[str], None] = "d8b2e4f6a913"
branch_labels: Union[str, Sequence[str], None] = None
depends_on: Union[str, Sequence[str], None] = None


def upgrade() -> None:
    """Upgrade schema."""
    op.create_table(
        "subagent_test_trace",
        sa.Column("created_at", sa.DateTime(), nullable=False),
        sa.Column("id", sa.Integer(), nullable=False),
        sa.Column("name", sqlmodel.sql.sqltypes.AutoString(), nullable=False),
        sa.Column("status", sqlmodel.sql.sqltypes.AutoString(), nullable=False),
        sa.Column("prompt", sa.Text(), nullable=False),
        sa.Column("model", sqlmodel.sql.sqltypes.AutoString(), nullable=False),
        sa.Column("turns", sa.Integer(), nullable=False),
        sa.Column("duration_seconds", sa.Float(), nullable=False),
        sa.Column("final_message", sa.Text(), nullable=False),
        sa.Column("events", sa.JSON(), nullable=False),
        sa.Column("error", sa.Text(), nullable=True),
        sa.Column("created_by", sqlmodel.sql.sqltypes.AutoString(), nullable=True),
        sa.PrimaryKeyConstraint("id"),
    )
    op.create_index(op.f("ix_subagent_test_trace_name"), "subagent_test_trace", ["name"], unique=False)
    op.create_index(op.f("ix_subagent_test_trace_created_at"), "subagent_test_trace", ["created_at"], unique=False)


def downgrade() -> None:
    """Downgrade schema."""
    op.drop_index(op.f("ix_subagent_test_trace_created_at"), table_name="subagent_test_trace")
    op.drop_index(op.f("ix_subagent_test_trace_name"), table_name="subagent_test_trace")
    op.drop_table("subagent_test_trace")
