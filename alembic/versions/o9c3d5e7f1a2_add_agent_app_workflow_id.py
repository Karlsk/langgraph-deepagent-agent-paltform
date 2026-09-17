"""Add agent_app.workflow_id (chatflow binding for engine='workflow').

Revision ID: o9c3d5e7f1a2
Revises: n8b2c4d6e8f0
Create Date: 2026-09-17 10:00:00.000000

spec-01 (chatflow via AgentApp engine="workflow") Phase 1: an AgentApp of
``engine="workflow"`` binds a registered workflow by id. The column is
nullable — deepagents apps leave it NULL.
"""

from typing import Sequence, Union

import sqlalchemy as sa

from alembic import op

# revision identifiers, used by Alembic.
revision: str = "o9c3d5e7f1a2"  # pragma: allowlist secret
down_revision: Union[str, Sequence[str], None] = "n8b2c4d6e8f0"
branch_labels: Union[str, Sequence[str], None] = None
depends_on: Union[str, Sequence[str], None] = None


def upgrade() -> None:
    """Upgrade schema."""
    op.add_column("agent_app", sa.Column("workflow_id", sa.String(length=64), nullable=True))


def downgrade() -> None:
    """Downgrade schema."""
    op.drop_column("agent_app", "workflow_id")
