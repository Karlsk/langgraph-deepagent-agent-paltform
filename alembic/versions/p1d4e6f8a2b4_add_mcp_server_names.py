"""Add mcp_server_names to agent_app and subagent_config.

Revision ID: p1d4e6f8a2b4
Revises: o9c3d5e7f1a2
Create Date: 2026-09-17 14:00:00.000000

Per-agent MCP server association: each agent/subagent declares which MCP
servers' tools are bulk-included.  ``allowed_tools=None`` semantics change
from "all catalog tools" to "no extra tools" (builtins + MCP only).
"""

from typing import Sequence, Union

import sqlalchemy as sa

from alembic import op

# revision identifiers, used by Alembic.
revision: str = "p1d4e6f8a2b4"  # pragma: allowlist secret
down_revision: Union[str, Sequence[str], None] = "o9c3d5e7f1a2"
branch_labels: Union[str, Sequence[str], None] = None
depends_on: Union[str, Sequence[str], None] = None


def upgrade() -> None:
    """Upgrade schema."""
    op.add_column("agent_app", sa.Column("mcp_server_names", sa.JSON(), nullable=False, server_default="[]"))
    op.add_column("subagent_config", sa.Column("mcp_server_names", sa.JSON(), nullable=False, server_default="[]"))


def downgrade() -> None:
    """Downgrade schema."""
    op.drop_column("subagent_config", "mcp_server_names")
    op.drop_column("agent_app", "mcp_server_names")
