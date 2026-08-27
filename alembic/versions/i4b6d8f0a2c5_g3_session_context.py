"""G3 session layer: session.agent_app_id str->int, updated_at, context_size.

Revision ID: i4b6d8f0a2c5
Revises: h3a5c7e9b1d3
Create Date: 2026-09-02 10:00:00.000000

Phase G3: session three-layer lifecycle base (spec-g3-session §11.4.1/§11.4.2):

- ``session.agent_app_id`` str -> Integer — legacy numeric strings are
  preserved via ``USING agent_app_id::int`` on PostgreSQL; on SQLite the
  batch table-rebuild performs the same affinity conversion ('7' -> 7).
- ``session.updated_at`` DateTime NULL — PATCH rename visibility; new rows
  are stamped by the ORM (``sa_column_kwargs={"onupdate": ...}``), existing
  rows stay NULL until first update.
- ``agent_app.context_size`` Integer NULL — per-app compression threshold
  consumed by the summarization middleware wiring (spec §4.2).

``ix_session_agent_app_id`` is dropped before and re-created after the
column conversion so the batch reflection path never sees a stale index.
"""

from typing import Sequence, Union

import sqlalchemy as sa
import sqlmodel  # noqa: F401

from alembic import op

# revision identifiers, used by Alembic.
revision: str = "i4b6d8f0a2c5"  # pragma: allowlist secret
down_revision: Union[str, Sequence[str], None] = "h3a5c7e9b1d3"
branch_labels: Union[str, Sequence[str], None] = None
depends_on: Union[str, Sequence[str], None] = None


def upgrade() -> None:
    """Upgrade schema."""
    # --- agent_app.context_size (per-app compression threshold, spec §11.4.2) ---
    op.add_column("agent_app", sa.Column("context_size", sa.Integer(), nullable=True))

    # --- session.agent_app_id str -> int + updated_at (spec §11.4.1) ---
    op.drop_index(op.f("ix_session_agent_app_id"), table_name="session")
    with op.batch_alter_table("session") as batch_op:
        batch_op.alter_column(
            "agent_app_id",
            existing_type=sqlmodel.sql.sqltypes.AutoString(),
            type_=sa.Integer(),
            postgresql_using="agent_app_id::int",
        )
        batch_op.add_column(sa.Column("updated_at", sa.DateTime(), nullable=True))
    op.create_index(op.f("ix_session_agent_app_id"), "session", ["agent_app_id"], unique=False)


def downgrade() -> None:
    """Downgrade schema."""
    op.drop_index(op.f("ix_session_agent_app_id"), table_name="session")
    with op.batch_alter_table("session") as batch_op:
        batch_op.drop_column("updated_at")
        batch_op.alter_column(
            "agent_app_id",
            existing_type=sa.Integer(),
            type_=sqlmodel.sql.sqltypes.AutoString(),
            postgresql_using="agent_app_id::text",
        )
    op.create_index(op.f("ix_session_agent_app_id"), "session", ["agent_app_id"], unique=False)
    op.drop_column("agent_app", "context_size")
