"""Provider / model_config / provider_health tables replacing llm_config.

Revision ID: a3f7e9b1c852
Revises: f3a1b7c9d204
Create Date: 2026-08-17 10:00:00.000000

The retired ``llm_config`` asset is split into a ``provider`` row (endpoint +
auth material) plus one ``model_config`` row per offered model; ``provider_health``
stores the latest on-demand connectivity probe result. Data migration:

- every ``llm_config`` row becomes a provider (type ``OPENAI_COMPATIBLE``,
  ``auth_config={"api_key": ...}``) plus one model_config (``name`` kept,
  ``model_id`` = old ``model_name``, temperature/max_tokens folded into
  ``extra_params``);
- ``agent_app.model`` / ``subagent_config.model`` references switch from the
  llm_config name ``X`` to the pair reference ``X/X`` (NULL stays NULL);
- ``llm_config`` is dropped afterwards.

The data copy runs through dialect-neutral SQLAlchemy core statements (no
vendor JSON builders) so the upgrade/downgrade round-trip is testable on
SQLite; no row is seeded here — the default provider/model pair is created by
``bootstrap.ensure_default_provider_and_model`` so migrations stay
environment-independent.
"""

import hashlib
import json
from datetime import UTC, datetime
from typing import Any, Sequence, Union

import sqlalchemy as sa
import sqlmodel  # noqa: F401

from alembic import op

# revision identifiers, used by Alembic.
revision: str = "a3f7e9b1c852"  # pragma: allowlist secret
down_revision: Union[str, Sequence[str], None] = "f3a1b7c9d204"
branch_labels: Union[str, Sequence[str], None] = None
depends_on: Union[str, Sequence[str], None] = None

# Retired LlmConfig hash contract (inlined: the model class no longer exists,
# but downgrade must rebuild rows with a stable content_hash).
_OLD_HASH_FIELDS = ("model_name", "api_key", "base_url", "temperature", "max_tokens", "enabled", "description")


def _old_llm_config_hash(payload: dict[str, Any]) -> str:
    """Replicate the retired ``compute_llm_config_hash`` over a field dict."""
    canonical = json.dumps(
        {field: payload.get(field) for field in _OLD_HASH_FIELDS},
        sort_keys=True,
        separators=(",", ":"),
        ensure_ascii=False,
        default=str,
    )
    return hashlib.sha256(canonical.encode("utf-8")).hexdigest()  # noqa: S324 — content fingerprint, not security


def _as_datetime(value: Any) -> Any:
    """Coerce a DateTime column value across dialects.

    PostgreSQL returns native datetimes; SQLite returns serialized strings.
    """
    if isinstance(value, str):
        return datetime.fromisoformat(value)
    return value


def upgrade() -> None:
    """Upgrade schema."""
    op.create_table(
        "provider",
        sa.Column("created_at", sa.DateTime(), nullable=False),
        sa.Column("id", sa.Integer(), nullable=False),
        sa.Column("name", sqlmodel.sql.sqltypes.AutoString(), nullable=False),
        sa.Column("type", sqlmodel.sql.sqltypes.AutoString(), nullable=False),
        sa.Column("base_url", sqlmodel.sql.sqltypes.AutoString(), nullable=False),
        sa.Column("auth_config", sa.JSON(), nullable=False),
        sa.Column("enabled", sa.Boolean(), nullable=False),
        sa.Column("deleted", sa.Boolean(), nullable=False),
        sa.Column("created_by", sqlmodel.sql.sqltypes.AutoString(), nullable=True),
        sa.Column("updated_at", sa.DateTime(), nullable=False),
        sa.PrimaryKeyConstraint("id"),
    )
    op.create_index(op.f("ix_provider_name"), "provider", ["name"], unique=True)
    op.create_table(
        "model_config",
        sa.Column("created_at", sa.DateTime(), nullable=False),
        sa.Column("id", sa.Integer(), nullable=False),
        sa.Column("provider_id", sa.Integer(), nullable=False),
        sa.Column("name", sqlmodel.sql.sqltypes.AutoString(), nullable=False),
        sa.Column("model_id", sqlmodel.sql.sqltypes.AutoString(), nullable=False),
        sa.Column("context_size", sa.Integer(), nullable=True),
        sa.Column("extra_params", sa.JSON(), nullable=False),
        sa.Column("enabled", sa.Boolean(), nullable=False),
        sa.Column("deleted", sa.Boolean(), nullable=False),
        sa.Column("created_by", sqlmodel.sql.sqltypes.AutoString(), nullable=True),
        sa.Column("updated_at", sa.DateTime(), nullable=False),
        sa.PrimaryKeyConstraint("id"),
        sa.UniqueConstraint("provider_id", "name", name="uq_model_config_provider_name"),
        sa.UniqueConstraint("provider_id", "model_id", name="uq_model_config_provider_model_id"),
    )
    op.create_index(op.f("ix_model_config_provider_id"), "model_config", ["provider_id"], unique=False)
    op.create_table(
        "provider_health",
        sa.Column("created_at", sa.DateTime(), nullable=False),
        sa.Column("id", sa.Integer(), nullable=False),
        sa.Column("provider_id", sa.Integer(), nullable=False),
        sa.Column("status", sqlmodel.sql.sqltypes.AutoString(), nullable=False),
        sa.Column("last_check_at", sa.DateTime(), nullable=True),
        sa.Column("last_success_at", sa.DateTime(), nullable=True),
        sa.Column("fail_count", sa.Integer(), nullable=False),
        sa.Column("latency_ms", sa.Integer(), nullable=True),
        sa.Column("error_message", sqlmodel.sql.sqltypes.AutoString(length=500), nullable=True),
        sa.Column("updated_at", sa.DateTime(), nullable=False),
        sa.PrimaryKeyConstraint("id"),
    )
    op.create_index(op.f("ix_provider_health_provider_id"), "provider_health", ["provider_id"], unique=True)

    conn = op.get_bind()
    provider_table = sa.table(
        "provider",
        sa.column("created_at", sa.DateTime()),
        sa.column("name", sa.String()),
        sa.column("type", sa.String()),
        sa.column("base_url", sa.String()),
        sa.column("auth_config", sa.JSON()),
        sa.column("enabled", sa.Boolean()),
        sa.column("deleted", sa.Boolean()),
        sa.column("created_by", sa.String()),
        sa.column("updated_at", sa.DateTime()),
    )
    # Data migration: each retired llm_config row becomes one provider row
    # (type OPENAI_COMPATIBLE, api_key folded into auth_config).
    legacy = conn.execute(
        sa.text(
            "SELECT created_at, name, model_name, api_key, base_url, temperature, max_tokens, enabled, created_by FROM llm_config"
        )
    ).fetchall()
    provider_ids: dict[str, int] = {}
    for row in legacy:
        conn.execute(
            provider_table.insert().values(
                created_at=_as_datetime(row.created_at),
                name=row.name,
                type="OPENAI_COMPATIBLE",
                base_url=row.base_url or "",
                auth_config={"api_key": row.api_key},
                enabled=bool(row.enabled),
                deleted=False,
                created_by=row.created_by,
                updated_at=datetime.now(UTC),
            )
        )
        # Lookup instead of ``inserted_primary_key``: implicit returning is
        # not available on every dialect (SQLite) in this execution path.
        provider_ids[row.name] = conn.execute(
            sa.text("SELECT id FROM provider WHERE name = :name"), {"name": row.name}
        ).scalar_one()

    # The matching model_config row keeps the display name; the upstream
    # model_name becomes model_id and temperature/max_tokens move into
    # extra_params (drop the keys that were never set).
    model_table = sa.table(
        "model_config",
        sa.column("created_at", sa.DateTime()),
        sa.column("provider_id", sa.Integer()),
        sa.column("name", sa.String()),
        sa.column("model_id", sa.String()),
        sa.column("context_size", sa.Integer()),
        sa.column("extra_params", sa.JSON()),
        sa.column("enabled", sa.Boolean()),
        sa.column("deleted", sa.Boolean()),
        sa.column("created_by", sa.String()),
        sa.column("updated_at", sa.DateTime()),
    )
    for row in legacy:
        extra_params = {"temperature": row.temperature, "max_tokens": row.max_tokens}
        conn.execute(
            model_table.insert().values(
                created_at=_as_datetime(row.created_at),
                provider_id=provider_ids[row.name],
                name=row.name,
                model_id=row.model_name,
                context_size=None,
                extra_params={key: value for key, value in extra_params.items() if value is not None},
                enabled=bool(row.enabled),
                deleted=False,
                created_by=row.created_by,
                updated_at=datetime.now(UTC),
            )
        )

    # Asset references switch semantics: llm_config name X -> pair ref X/X.
    op.execute(sa.text("UPDATE agent_app SET model = model || '/' || model WHERE model IS NOT NULL"))
    op.execute(sa.text("UPDATE subagent_config SET model = model || '/' || model WHERE model IS NOT NULL"))
    op.drop_table("llm_config")


def downgrade() -> None:
    """Downgrade schema."""
    conn = op.get_bind()
    # Reverse-map provider/model pairs back into retired llm_config rows.
    # Asset references stay compatible: pair ref "X/X" resolves as llm_config
    # name "X/X" (the legacy loader performs a plain primary-key lookup).
    pairs = conn.execute(
        sa.text(
            "SELECT p.name AS provider_name, p.base_url AS base_url, p.auth_config AS auth_config, "
            "p.enabled AS provider_enabled, p.created_by AS created_by, p.created_at AS created_at, "
            "m.name AS model_name_, m.model_id AS model_id, m.extra_params AS extra_params, m.enabled AS model_enabled "
            "FROM provider p JOIN model_config m ON m.provider_id = p.id "
            "WHERE p.deleted = false AND m.deleted = false"
        )
    ).fetchall()

    llm_table = sa.table(
        "llm_config",
        sa.column("created_at", sa.DateTime()),
        sa.column("name", sa.String()),
        sa.column("model_name", sa.String()),
        sa.column("api_key", sa.String()),
        sa.column("base_url", sa.String()),
        sa.column("temperature", sa.Float()),
        sa.column("max_tokens", sa.Integer()),
        sa.column("enabled", sa.Boolean()),
        sa.column("description", sa.String()),
        sa.column("content_hash", sa.String()),
        sa.column("created_by", sa.String()),
    )
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

    def _as_dict(value: Any) -> dict[str, Any]:
        """Coerce a JSON column value to a dict across dialects.

        PostgreSQL returns native dicts; SQLite returns the serialized text.
        """
        if isinstance(value, dict):
            return value
        if isinstance(value, str):
            loaded = json.loads(value)
            return loaded if isinstance(loaded, dict) else {}
        return {}

    for pair in pairs:
        auth_config = _as_dict(pair.auth_config)
        extra_params = _as_dict(pair.extra_params)
        name = f"{pair.provider_name}/{pair.model_name_}"
        fields = {
            "model_name": pair.model_id,
            "api_key": str(auth_config.get("api_key", "")),
            "base_url": pair.base_url or None,
            "temperature": extra_params.get("temperature"),
            "max_tokens": extra_params.get("max_tokens"),
            "enabled": bool(pair.provider_enabled) and bool(pair.model_enabled),
            "description": "",
        }
        conn.execute(
            llm_table.insert().values(
                created_at=_as_datetime(pair.created_at),
                name=name,
                content_hash=_old_llm_config_hash(fields),
                created_by=pair.created_by,
                **fields,
            )
        )

    op.drop_index(op.f("ix_provider_health_provider_id"), table_name="provider_health")
    op.drop_table("provider_health")
    op.drop_index(op.f("ix_model_config_provider_id"), table_name="model_config")
    op.drop_table("model_config")
    op.drop_index(op.f("ix_provider_name"), table_name="provider")
    op.drop_table("provider")
