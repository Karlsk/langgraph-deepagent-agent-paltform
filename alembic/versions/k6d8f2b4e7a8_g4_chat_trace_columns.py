"""G4 subagent_trace gains source + session_id columns (chat trace rows).

Revision ID: k6d8f2b4e7a8
Revises: j5c7e9a1b3d6
Create Date: 2026-08-27 19:40:00.000000

Phase G4 (spec-g4-chat §7.1): the trace table becomes the shared surface for
one-shot test runs AND chat rounds. ``source`` separates the two origins
(``test`` default keeps legacy rows intact) and ``session_id`` addresses the
chat round's session (chat rows only; indexed for the GET /chat/traces
session-scoped query).
"""

from typing import Sequence, Union

import sqlalchemy as sa
from alembic import op

# revision identifiers, used by Alembic.
revision: str = "k6d8f2b4e7a8"  # pragma: allowlist secret
down_revision: Union[str, Sequence[str], None] = "j5c7e9a1b3d6"
branch_labels: Union[str, Sequence[str], None] = None
depends_on: Union[str, Sequence[str], None] = None


def upgrade() -> None:
    """Upgrade schema."""
    op.add_column(
        "subagent_trace",
        sa.Column("source", sa.String(), nullable=False, server_default="test"),
    )
    op.add_column("subagent_trace", sa.Column("session_id", sa.String(), nullable=True))
    op.create_index(op.f("ix_subagent_trace_session_id"), "subagent_trace", ["session_id"], unique=False)


def downgrade() -> None:
    """Downgrade schema."""
    op.drop_index(op.f("ix_subagent_trace_session_id"), table_name="subagent_trace")
    op.drop_column("subagent_trace", "session_id")
    op.drop_column("subagent_trace", "source")
