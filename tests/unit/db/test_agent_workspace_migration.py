"""Upgrade/downgrade round-trip test for the G2 agent-workspace migration.

Runs the real revision chain against an in-memory SQLite database, seeds
pre-migration ``skill_asset`` + ``agent_app`` rows, then asserts the upgrade
adds the four workspace columns plus the ``user_agent_app_association``
table (UNIQUE(user_id, agent_app_id), FK CASCADE) and backfills
``scope='global'`` / ``agent_workspace_status='pending'`` for existing rows,
and that the downgrade removes the whole schema slice.
"""

from collections.abc import Generator
from pathlib import Path

import pytest
import sqlalchemy as sa
from alembic.config import Config
from alembic.migration import MigrationContext
from alembic.operations import Operations
from alembic.script import ScriptDirectory
from sqlalchemy.engine import Engine
from sqlalchemy.exc import IntegrityError
from sqlalchemy.pool import StaticPool

pytestmark = pytest.mark.unit

_ALEMBIC_DIR = Path(__file__).resolve().parents[3] / "alembic"
_PRE_REVISION = "g1a2b3c4d5e6"
_HEAD_REVISION = "h3a5c7e9b1d3"  # G2 agent-workspace migration (spec v3.3 §3)


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
def seeded_engine() -> Generator[Engine, None, None]:
    """SQLite engine upgraded to the pre-G2 revision with legacy seed rows."""
    engine = sa.create_engine("sqlite://", poolclass=StaticPool)
    script = _script()
    _upgrade_to(engine, script, _PRE_REVISION)
    with engine.begin() as conn:
        conn.execute(
            sa.text(
                "INSERT INTO skill_asset (created_at, name, description, body, content_hash,"
                " version, created_by) VALUES ('2026-08-01 00:00:00', 'markdown-fix', 'desc',"
                " '# body', 'hash-1', 1, NULL)"
            )
        )
        conn.execute(
            sa.text(
                "INSERT INTO agent_app (created_at, id, name, system_prompt, allowed_tools, model,"
                " skill_names, subagent_names, interrupt_on, engine, status, published_hash,"
                " version, created_by) VALUES ('2026-08-01 00:00:00', 1, 'default', 'prompt',"
                " NULL, NULL, '[]', '[]', '{}', 'deepagents', 'published', NULL, 1, NULL)"
            )
        )
    yield engine
    engine.dispose()


def test_upgrade_adds_workspace_columns_and_backfills(seeded_engine: Engine) -> None:
    """Upgrade adds the 4 columns + association table and backfills defaults (D1/D2)."""
    script = _script()
    _upgrade_to(seeded_engine, script, _HEAD_REVISION)

    inspector = sa.inspect(seeded_engine)
    skill_cols = {col["name"] for col in inspector.get_columns("skill_asset")}
    app_cols = {col["name"] for col in inspector.get_columns("agent_app")}
    assert "scope" in skill_cols
    assert {"agent_dir", "workspace_hash", "agent_workspace_status"} <= app_cols

    assoc_cols = {col["name"] for col in inspector.get_columns("user_agent_app_association")}
    assert assoc_cols == {
        "id",
        "user_id",
        "agent_app_id",
        "last_synced_workspace_hash",
        "associated_at",
        "created_at",
    }

    with seeded_engine.connect() as conn:
        # Backfill: existing rows get scope='global' (D2 via server_default)
        scope = conn.execute(sa.text("SELECT scope FROM skill_asset WHERE name='markdown-fix'")).scalar_one()
        assert scope == "global"
        # Backfill: existing apps get agent_workspace_status='pending' (D2);
        # agent_dir backfill is deferred to bootstrap (spec §3.2 note)
        status, agent_dir, workspace_hash = conn.execute(
            sa.text("SELECT agent_workspace_status, agent_dir, workspace_hash FROM agent_app WHERE id=1")
        ).one()
        assert status == "pending"
        assert agent_dir is None
        assert workspace_hash is None


def test_upgrade_association_unique_constraint(seeded_engine: Engine) -> None:
    """UNIQUE(user_id, agent_app_id) rejects duplicate association rows."""
    script = _script()
    _upgrade_to(seeded_engine, script, _HEAD_REVISION)

    with seeded_engine.begin() as conn:
        conn.execute(
            sa.text(
                "INSERT INTO user_agent_app_association (created_at, user_id, agent_app_id,"
                " last_synced_workspace_hash, associated_at)"
                " VALUES ('2026-08-26 00:00:00', 1, 1, NULL, '2026-08-26 00:00:00')"
            )
        )
        with pytest.raises(IntegrityError):
            conn.execute(
                sa.text(
                    "INSERT INTO user_agent_app_association (created_at, user_id, agent_app_id,"
                    " last_synced_workspace_hash, associated_at)"
                    " VALUES ('2026-08-26 00:00:00', 1, 1, NULL, '2026-08-26 00:00:00')"
                )
            )


def test_downgrade_removes_workspace_schema(seeded_engine: Engine) -> None:
    """Downgrade drops the association table and the four workspace columns."""
    script = _script()
    _upgrade_to(seeded_engine, script, _HEAD_REVISION)
    _downgrade_to(seeded_engine, script, _PRE_REVISION)

    inspector = sa.inspect(seeded_engine)
    tables = inspector.get_table_names()
    assert "user_agent_app_association" not in tables

    skill_cols = {col["name"] for col in inspector.get_columns("skill_asset")}
    app_cols = {col["name"] for col in inspector.get_columns("agent_app")}
    assert "scope" not in skill_cols
    assert {"agent_dir", "workspace_hash", "agent_workspace_status"}.isdisjoint(app_cols)

    # Legacy seed rows survive the round-trip
    with seeded_engine.connect() as conn:
        name = conn.execute(sa.text("SELECT name FROM skill_asset")).scalar_one()
        assert name == "markdown-fix"
