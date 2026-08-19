"""Upgrade/downgrade round-trip test for the provider-models migration.

Runs the real revision chain against an in-memory SQLite database (the
migration data copy is dialect-neutral by design), seeds retired
``llm_config`` rows plus asset ``model`` references, then asserts the
upgrade split (provider + model_config + ``X/X`` references) and the
downgrade reconstruction of ``llm_config``.
"""

import json
from collections.abc import Generator
from pathlib import Path

import pytest
import sqlalchemy as sa
from alembic.migration import MigrationContext
from alembic.operations import Operations
from alembic.script import ScriptDirectory
from sqlalchemy.engine import Engine
from sqlalchemy.pool import StaticPool

from alembic.config import Config

pytestmark = pytest.mark.unit

_ALEMBIC_DIR = Path(__file__).resolve().parents[3] / "alembic"
_PRE_REVISION = "f3a1b7c9d204"
_HEAD_REVISION = "a3f7e9b1c852"


def _script() -> ScriptDirectory:
    """Load the real revision graph without touching env.py (no live DB URL)."""
    cfg = Config()
    cfg.set_main_option("script_location", str(_ALEMBIC_DIR))
    return ScriptDirectory.from_config(cfg)


def _upgrade_to(engine: Engine, script: ScriptDirectory, target: str) -> None:
    """Run every pending upgrade step up to ``target`` on the engine."""
    with engine.begin() as conn:

        def run(rev: str, context: MigrationContext) -> list:
            return script._upgrade_revs(target, rev)  # noqa: SLF001 — alembic internal step API

        ctx = MigrationContext.configure(conn, opts={"fn": run})
        # env.py normally installs the op proxy via EnvironmentContext; the
        # low-level API needs it explicitly around the step execution.
        with Operations.context(ctx):
            ctx.run_migrations()


def _downgrade_to(engine: Engine, script: ScriptDirectory, target: str) -> None:
    """Run every pending downgrade step down to ``target`` on the engine."""
    with engine.begin() as conn:

        def run(rev: str, context: MigrationContext) -> list:
            return script._downgrade_revs(target, rev)  # noqa: SLF001 — alembic internal step API

        ctx = MigrationContext.configure(conn, opts={"fn": run})
        with Operations.context(ctx):
            ctx.run_migrations()


@pytest.fixture()
def migrated_engine() -> Generator[Engine, None, None]:
    """SQLite engine upgraded to the pre-provider revision with seed data."""
    engine = sa.create_engine("sqlite://", poolclass=StaticPool)
    script = _script()
    _upgrade_to(engine, script, _PRE_REVISION)
    with engine.begin() as conn:
        conn.execute(
            sa.text(
                "INSERT INTO llm_config (created_at, name, model_name, api_key, base_url, temperature,"
                " max_tokens, enabled, description, content_hash, created_by) VALUES"
                " ('2026-08-01 00:00:00', :name, :model_name, :api_key, :base_url, :temperature,"
                " :max_tokens, :enabled, :description, :content_hash, :created_by)"
            ),
            {
                "name": "default",
                "model_name": "gpt-4o-mini",
                "api_key": "sk-test-1234567890",
                "base_url": None,
                "temperature": 0.7,
                "max_tokens": 1024,
                "enabled": 1,
                "description": "seeded default",
                "content_hash": "legacy-hash",
                "created_by": "bootstrap",
            },
        )
        conn.execute(
            sa.text(
                "INSERT INTO agent_app (created_at, id, name, system_prompt, allowed_tools, model,"
                " skill_names, subagent_names, interrupt_on, engine, status, published_hash, version,"
                " created_by) VALUES"
                " ('2026-08-01 00:00:00', 1, 'default', 'prompt', NULL, :model, '[]', '[]', '{}',"
                " 'deepagents', 'published', NULL, 1, NULL)"
            ),
            {"model": "default"},
        )
        conn.execute(
            sa.text(
                "INSERT INTO subagent_config (created_at, name, description, when_to_use, system_prompt,"
                " allowed_tools, model, max_turns, content_hash, version, created_by) VALUES"
                " ('2026-08-01 00:00:00', 'worker', 'desc', 'when', 'prompt', NULL, NULL, NULL,"
                " 'hash', 1, NULL)"
            )
        )
    yield engine
    engine.dispose()


def test_upgrade_splits_llm_config_into_provider_and_model(migrated_engine: Engine) -> None:
    """Upgrade migrates every llm_config row into a provider/model pair."""
    script = _script()
    _upgrade_to(migrated_engine, script, _HEAD_REVISION)

    with migrated_engine.connect() as conn:
        tables = sa.inspect(migrated_engine).get_table_names()
        assert "llm_config" not in tables
        assert {"provider", "model_config", "provider_health"} <= set(tables)

        provider = conn.execute(sa.text("SELECT * FROM provider")).mappings().one()
        assert provider["name"] == "default"
        assert provider["type"] == "OPENAI_COMPATIBLE"
        assert provider["base_url"] == ""
        assert provider["enabled"] == 1
        assert provider["deleted"] == 0
        assert provider["created_by"] == "bootstrap"
        auth_config = (
            json.loads(provider["auth_config"])
            if isinstance(provider["auth_config"], str)
            else provider["auth_config"]
        )
        assert auth_config == {"api_key": "sk-test-1234567890"}

        model = conn.execute(sa.text("SELECT * FROM model_config")).mappings().one()
        assert model["provider_id"] == provider["id"]
        assert model["name"] == "default"
        assert model["model_id"] == "gpt-4o-mini"
        assert model["context_size"] is None
        extra = json.loads(model["extra_params"]) if isinstance(model["extra_params"], str) else model["extra_params"]
        assert extra == {"temperature": 0.7, "max_tokens": 1024}

        app_model = conn.execute(sa.text("SELECT model FROM agent_app WHERE name = 'default'")).scalar_one()
        assert app_model == "default/default"
        sub_model = conn.execute(sa.text("SELECT model FROM subagent_config WHERE name = 'worker'")).scalar_one()
        assert sub_model is None  # NULL references stay NULL


def test_downgrade_rebuilds_llm_config(migrated_engine: Engine) -> None:
    """Downgrade reconstructs llm_config rows from the provider/model split."""
    script = _script()
    _upgrade_to(migrated_engine, script, _HEAD_REVISION)
    _downgrade_to(migrated_engine, script, _PRE_REVISION)

    with migrated_engine.connect() as conn:
        tables = sa.inspect(migrated_engine).get_table_names()
        assert "llm_config" in tables
        assert {"provider", "model_config", "provider_health"}.isdisjoint(tables)

        cfg = conn.execute(sa.text("SELECT * FROM llm_config")).mappings().one()
        # The reconstructed name is the pair reference; the legacy loader
        # resolves it by plain primary-key lookup, so references stay valid.
        assert cfg["name"] == "default/default"
        assert cfg["model_name"] == "gpt-4o-mini"
        assert cfg["api_key"] == "sk-test-1234567890"
        assert cfg["base_url"] is None
        assert cfg["temperature"] == pytest.approx(0.7)
        assert cfg["max_tokens"] == 1024
        assert cfg["enabled"] == 1
        assert cfg["description"] == ""
        assert cfg["created_by"] == "bootstrap"
        assert len(cfg["content_hash"]) == 64  # rebuilt sha256, not the stale legacy value

        # Asset references stay resolvable under the legacy semantics.
        app_model = conn.execute(sa.text("SELECT model FROM agent_app WHERE name = 'default'")).scalar_one()
        assert (
            conn.execute(sa.text("SELECT name FROM llm_config WHERE name = :name"), {"name": app_model}).scalar_one()
            == app_model
        )
