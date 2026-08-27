"""Upgrade/downgrade round-trip tests for the two G3 migrations.

g3a (``i4b6d8f0a2c5``): ``session.agent_app_id`` str -> int (legacy numeric
strings preserved), ``session.updated_at`` added, ``agent_app.context_size``
added (spec-g3-session §11.4.1/§11.4.2).

g3b (``j5c7e9a1b3d6``): ``subagent_test_trace`` -> ``subagent_trace`` table
rename with the created_at index recreated under the new table name
(spec-g3-session §11.4.3).
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
from sqlalchemy.pool import StaticPool

pytestmark = pytest.mark.unit

_ALEMBIC_DIR = Path(__file__).resolve().parents[3] / "alembic"
_PRE_REVISION = "h3a5c7e9b1d3"  # G2 agent-workspace head
_G3A_REVISION = "i4b6d8f0a2c5"  # G3 session/context columns
_HEAD_REVISION = "j5c7e9a1b3d6"  # G3 subagent_trace rename


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
    """SQLite engine upgraded to the pre-G3 revision with legacy seed rows."""
    engine = sa.create_engine("sqlite://", poolclass=StaticPool)
    script = _script()
    _upgrade_to(engine, script, _PRE_REVISION)
    with engine.begin() as conn:
        conn.execute(
            sa.text(
                "INSERT INTO user (created_at, id, email, hashed_password, username)"
                " VALUES ('2026-08-01 00:00:00', 1, 'a@t.com', 'x', 'alice')"
            )
        )
        conn.execute(
            sa.text(
                "INSERT INTO agent_app (created_at, id, name, system_prompt, allowed_tools, model,"
                " skill_names, subagent_names, interrupt_on, engine, status, published_hash,"
                " version, created_by) VALUES ('2026-08-01 00:00:00', 7, 'default', 'prompt',"
                " NULL, NULL, '[]', '[]', '{}', 'deepagents', 'published', NULL, 1, NULL)"
            )
        )
        # Legacy session row: agent_app_id stored as a numeric STRING (pre-G3 str column)
        conn.execute(
            sa.text(
                "INSERT INTO session (created_at, id, user_id, name, username, agent_app_id)"
                " VALUES ('2026-08-20 00:00:00', 'sess-legacy', 1, 'old', 'alice', '7')"
            )
        )
        conn.execute(
            sa.text(
                "INSERT INTO subagent_test_trace (created_at, id, name, status, prompt, model,"
                " turns, duration_seconds, final_message, events, error, created_by)"
                " VALUES ('2026-08-20 00:00:00', 1, 'researcher', 'success', 'hi', 'gpt', 1,"
                " 0.5, 'done', '[]', NULL, 'alice')"
            )
        )
    yield engine
    engine.dispose()


def test_upgrade_g3a_converts_agent_app_id_and_adds_columns(seeded_engine: Engine) -> None:
    """g3a: agent_app_id -> INTEGER (values preserved), updated_at + context_size added."""
    script = _script()
    _upgrade_to(seeded_engine, script, _G3A_REVISION)

    inspector = sa.inspect(seeded_engine)
    session_cols = {col["name"]: col for col in inspector.get_columns("session")}
    app_cols = {col["name"] for col in inspector.get_columns("agent_app")}

    assert "updated_at" in session_cols
    assert "context_size" in app_cols
    assert isinstance(session_cols["agent_app_id"]["type"], sa.Integer)

    with seeded_engine.connect() as conn:
        value = conn.execute(sa.text("SELECT agent_app_id FROM session WHERE id='sess-legacy'")).scalar_one()
        assert value == 7


def test_downgrade_g3a_restores_previous_schema(seeded_engine: Engine) -> None:
    """g3a downgrade: columns dropped, rows survive, agent_app_id back to str."""
    script = _script()
    _upgrade_to(seeded_engine, script, _G3A_REVISION)
    _downgrade_to(seeded_engine, script, _PRE_REVISION)

    inspector = sa.inspect(seeded_engine)
    session_cols = {col["name"] for col in inspector.get_columns("session")}
    app_cols = {col["name"] for col in inspector.get_columns("agent_app")}
    assert "updated_at" not in session_cols
    assert "context_size" not in app_cols

    with seeded_engine.connect() as conn:
        session_id = conn.execute(sa.text("SELECT id FROM session")).scalar_one()
        assert session_id == "sess-legacy"


def test_upgrade_g3b_renames_trace_table(seeded_engine: Engine) -> None:
    """g3b: table renamed, created_at index recreated, rows preserved."""
    script = _script()
    _upgrade_to(seeded_engine, script, _HEAD_REVISION)

    inspector = sa.inspect(seeded_engine)
    tables = inspector.get_table_names()
    assert "subagent_test_trace" not in tables
    assert "subagent_trace" in tables

    index_names = {idx["name"] for idx in inspector.get_indexes("subagent_trace")}
    assert "ix_subagent_trace_created_at" in index_names
    assert "ix_subagent_trace_name" in index_names

    with seeded_engine.connect() as conn:
        name = conn.execute(sa.text("SELECT name FROM subagent_trace")).scalar_one()
        assert name == "researcher"


def test_downgrade_g3b_renames_back(seeded_engine: Engine) -> None:
    """g3b downgrade: table and indexes renamed back to subagent_test_trace."""
    script = _script()
    _upgrade_to(seeded_engine, script, _HEAD_REVISION)
    _downgrade_to(seeded_engine, script, _G3A_REVISION)

    inspector = sa.inspect(seeded_engine)
    tables = inspector.get_table_names()
    assert "subagent_trace" not in tables
    assert "subagent_test_trace" in tables

    index_names = {idx["name"] for idx in inspector.get_indexes("subagent_test_trace")}
    assert "ix_subagent_test_trace_created_at" in index_names

    with seeded_engine.connect() as conn:
        trace_id = conn.execute(sa.text("SELECT id FROM subagent_test_trace")).scalar_one()
        assert trace_id == 1
