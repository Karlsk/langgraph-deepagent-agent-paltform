"""G3 rename subagent_test_trace -> subagent_trace (generic trace surface).

Revision ID: j5c7e9a1b3d6
Revises: i4b6d8f0a2c5
Create Date: 2026-09-02 10:05:00.000000

Phase G3: the trace table no longer only serves one-shot test runs — it is
the shared observability surface for subagent executions (spec-g3-session
§11.4.3). The table is renamed ``subagent_test_trace`` -> ``subagent_trace``
and both indexes are re-created under the new prefix. Rows survive as-is;
``ALTER TABLE ... RENAME`` keeps index names on every dialect, so the
indexes are dropped before and re-created after the rename.
"""

from typing import Sequence, Union

from alembic import op

# revision identifiers, used by Alembic.
revision: str = "j5c7e9a1b3d6"  # pragma: allowlist secret
down_revision: Union[str, Sequence[str], None] = "i4b6d8f0a2c5"
branch_labels: Union[str, Sequence[str], None] = None
depends_on: Union[str, Sequence[str], None] = None


def upgrade() -> None:
    """Upgrade schema."""
    op.drop_index(op.f("ix_subagent_test_trace_created_at"), table_name="subagent_test_trace")
    op.drop_index(op.f("ix_subagent_test_trace_name"), table_name="subagent_test_trace")
    op.rename_table("subagent_test_trace", "subagent_trace")
    op.create_index(op.f("ix_subagent_trace_name"), "subagent_trace", ["name"], unique=False)
    op.create_index(
        op.f("ix_subagent_trace_created_at"), "subagent_trace", ["created_at"], unique=False
    )


def downgrade() -> None:
    """Downgrade schema."""
    op.drop_index(op.f("ix_subagent_trace_created_at"), table_name="subagent_trace")
    op.drop_index(op.f("ix_subagent_trace_name"), table_name="subagent_trace")
    op.rename_table("subagent_trace", "subagent_test_trace")
    op.create_index(
        op.f("ix_subagent_test_trace_name"), "subagent_test_trace", ["name"], unique=False
    )
    op.create_index(
        op.f("ix_subagent_test_trace_created_at"),
        "subagent_test_trace",
        ["created_at"],
        unique=False,
    )
