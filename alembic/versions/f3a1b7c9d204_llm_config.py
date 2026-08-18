"""LLM config asset table and model-reference backfill.

Revision ID: f3a1b7c9d204
Revises: e4f1a8c2b9d3
Create Date: 2026-08-13 10:00:00.000000

The ``agent_app.model`` / ``subagent_config.model`` columns switch semantics
from registry model names to ``llm_config`` reference names: every non-NULL
value is backfilled to the bootstrap-seeded ``"default"`` config (NULL rows
already resolve to ``default``). No data row is seeded here — the default
config is created by ``bootstrap.ensure_default_llm_config`` so migrations
stay environment-independent.
"""

from typing import Sequence, Union

import sqlalchemy as sa
import sqlmodel  # noqa: F401

from alembic import op

# Inlined: the retired ``LlmConfig`` model (and its constant) no longer
# exists in ``app.models``; migrations must stay importable from scratch.
DEFAULT_LLM_CONFIG_NAME = "default"

# revision identifiers, used by Alembic.
revision: str = "f3a1b7c9d204"  # pragma: allowlist secret
down_revision: Union[str, Sequence[str], None] = "e4f1a8c2b9d3"
branch_labels: Union[str, Sequence[str], None] = None
depends_on: Union[str, Sequence[str], None] = None


def upgrade() -> None:
    """Upgrade schema."""
    op.create_table(
        "llm_config",
        sa.Column("created_at", sa.DateTime(), nullable=False),
        sa.Column("name", sqlmodel.sql.sqltypes.AutoString(), nullable=False),
        sa.Column("model_name", sqlmodel.sql.sqltypes.AutoString(), nullable=False),
        sa.Column("api_key", sqlmodel.sql.sqltypes.AutoString(), nullable=False),
        sa.Column("base_url", sqlmodel.sql.sqltypes.AutoString(), nullable=True),
        sa.Column("temperature", sa.Float(), nullable=True),
        sa.Column("max_tokens", sa.Integer(), nullable=True),
        sa.Column("enabled", sa.Boolean(), nullable=False),
        sa.Column("description", sqlmodel.sql.sqltypes.AutoString(), nullable=False),
        sa.Column("content_hash", sqlmodel.sql.sqltypes.AutoString(), nullable=False),
        sa.Column("created_by", sqlmodel.sql.sqltypes.AutoString(), nullable=True),
        sa.PrimaryKeyConstraint("name"),
    )
    # Backfill: existing non-NULL model overrides become references to the
    # bootstrap-seeded default LLM config (old registry names are obsolete).
    op.execute(
        sa.text("UPDATE agent_app SET model = :model WHERE model IS NOT NULL").bindparams(
            model=DEFAULT_LLM_CONFIG_NAME
        )
    )
    op.execute(
        sa.text("UPDATE subagent_config SET model = :model WHERE model IS NOT NULL").bindparams(
            model=DEFAULT_LLM_CONFIG_NAME
        )
    )


def downgrade() -> None:
    """Downgrade schema."""
    # Drop the reference semantics: NULL rows fall back to the env registry.
    op.execute(sa.text("UPDATE agent_app SET model = NULL WHERE model IS NOT NULL"))
    op.execute(sa.text("UPDATE subagent_config SET model = NULL WHERE model IS NOT NULL"))
    op.drop_table("llm_config")
