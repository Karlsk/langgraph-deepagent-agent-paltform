"""Unit tests for scripts/migrate_workspace.py (spec-g2-workspace v3.3 §10.1, D25).

Zero real network / zero real DB: the session is an in-memory SQLite engine
and every path lives under tmp_path. The script module is loaded with
importlib because ``scripts/`` is not an importable package.
"""

import importlib.util
import os
import time
from collections.abc import Generator
from pathlib import Path
from typing import Any

import pytest
from sqlalchemy.pool import StaticPool
from sqlmodel import Session, SQLModel, create_engine

from app.models.agent_assets import AgentApp, UserAgentAppAssociation
from app.models.user import User  # noqa: F401 — registers the user table for FK resolution

pytestmark = pytest.mark.unit

_REPO_ROOT = Path(__file__).resolve().parents[3]
_SCRIPT_PATH = _REPO_ROOT / "scripts" / "migrate_workspace.py"


def _load_module() -> Any:
    """Load scripts/migrate_workspace.py as an isolated module object."""
    spec = importlib.util.spec_from_file_location("migrate_workspace_under_test", _SCRIPT_PATH)
    assert spec is not None and spec.loader is not None
    module = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(module)
    return module


@pytest.fixture
def migrate() -> Any:
    """The migrate_workspace script module."""
    return _load_module()


@pytest.fixture
def workspace_db() -> Generator[Session, None, None]:
    """In-memory SQLite with the G2 tables (real association remapping)."""
    engine = create_engine(
        "sqlite://",
        connect_args={"check_same_thread": False},
        poolclass=StaticPool,
    )
    SQLModel.metadata.create_all(engine)
    session = Session(engine)
    yield session
    session.close()


def _seed_legacy_two_layer(legacy_root: Path) -> None:
    """Create the pre-G2 on-disk layout: global skills + one user copy."""
    global_skill = legacy_root / "global" / "skills" / "greet"
    global_skill.mkdir(parents=True)
    (global_skill / "SKILL.md").write_text("# greet\n\nglobal body\n", encoding="utf-8")

    user_skill = legacy_root / "users" / "7" / "style-guide"
    user_skill.mkdir(parents=True)
    (user_skill / "SKILL.md").write_text("# style\n\nuser body\n", encoding="utf-8")


def test_dry_run_changes_nothing(workspace_db: Session, migrate: Any, tmp_path: Path) -> None:
    """--dry-run is the default: the legacy tree stays untouched."""
    legacy_root = tmp_path / "old-skills"
    data_root = tmp_path / "data"
    _seed_legacy_two_layer(legacy_root)
    before = sorted(p.relative_to(tmp_path).as_posix() for p in legacy_root.rglob("*"))

    summary = migrate.migrate_workspace(workspace_db, legacy_root=legacy_root, data_root=data_root)

    after = sorted(p.relative_to(tmp_path).as_posix() for p in legacy_root.rglob("*"))
    assert after == before  # nothing moved or copied
    assert not data_root.exists()
    assert summary["global_skills"] == 1
    assert summary["user_files"] == 1


def test_apply_migrates_global_and_associated_user_layer(
    workspace_db: Session, migrate: Any, tmp_path: Path
) -> None:
    """--apply moves Global to {DATA_ROOT}/global/skills and remaps the user layer."""
    legacy_root = tmp_path / "old-skills"
    data_root = tmp_path / "data"
    _seed_legacy_two_layer(legacy_root)
    app = AgentApp(name="demo", system_prompt="p", engine="deepagents", status="published")
    workspace_db.add(app)
    workspace_db.add(UserAgentAppAssociation(user_id=7, agent_app_id=1))
    workspace_db.commit()
    workspace_db.refresh(app)

    migrate.migrate_workspace(workspace_db, legacy_root=legacy_root, data_root=data_root, apply=True)

    new_global = data_root / "global" / "skills" / "greet" / "SKILL.md"
    assert new_global.read_text(encoding="utf-8") == "# greet\n\nglobal body\n"
    new_user = data_root / "agents" / str(app.id) / "users" / "7" / "skills" / "style-guide" / "SKILL.md"
    assert new_user.read_text(encoding="utf-8") == "# style\n\nuser body\n"


def test_apply_remaps_one_user_into_every_associated_app(
    workspace_db: Session, migrate: Any, tmp_path: Path
) -> None:
    """A user associated with two apps gets the copy under both workspaces."""
    legacy_root = tmp_path / "old-skills"
    data_root = tmp_path / "data"
    _seed_legacy_two_layer(legacy_root)
    app_a = AgentApp(name="app-a", system_prompt="p", engine="deepagents", status="published")
    app_b = AgentApp(name="app-b", system_prompt="p", engine="deepagents", status="published")
    workspace_db.add(app_a)
    workspace_db.add(app_b)
    workspace_db.commit()
    workspace_db.refresh(app_a)
    workspace_db.refresh(app_b)
    workspace_db.add(UserAgentAppAssociation(user_id=7, agent_app_id=app_a.id))
    workspace_db.add(UserAgentAppAssociation(user_id=7, agent_app_id=app_b.id))
    workspace_db.commit()

    summary = migrate.migrate_workspace(
        workspace_db, legacy_root=legacy_root, data_root=data_root, apply=True
    )

    for app in (app_a, app_b):
        copy = data_root / "agents" / str(app.id) / "users" / "7" / "skills" / "style-guide" / "SKILL.md"
        assert copy.exists()
    assert summary["remapped"] == 2  # one file copied into two associations


def test_orphan_user_dir_moves_to_fallback(workspace_db: Session, migrate: Any, tmp_path: Path) -> None:
    """A user without any association lands under {DATA_ROOT}/users/<uid>/."""
    legacy_root = tmp_path / "old-skills"
    data_root = tmp_path / "data"
    _seed_legacy_two_layer(legacy_root)  # user 7 has NO association rows

    migrate.migrate_workspace(workspace_db, legacy_root=legacy_root, data_root=data_root, apply=True)

    orphan = data_root / "users" / "7" / "style-guide" / "SKILL.md"
    assert orphan.read_text(encoding="utf-8") == "# style\n\nuser body\n"
    assert not list((data_root / "agents").glob("*/users/7/**/SKILL.md"))


def test_apply_is_idempotent(workspace_db: Session, migrate: Any, tmp_path: Path) -> None:
    """Running the migration twice never duplicates or corrupts content."""
    legacy_root = tmp_path / "old-skills"
    data_root = tmp_path / "data"
    _seed_legacy_two_layer(legacy_root)
    app = AgentApp(name="demo", system_prompt="p", engine="deepagents", status="published")
    workspace_db.add(app)
    workspace_db.add(UserAgentAppAssociation(user_id=7, agent_app_id=1))
    workspace_db.commit()

    migrate.migrate_workspace(workspace_db, legacy_root=legacy_root, data_root=data_root, apply=True)
    first = (data_root / "global" / "skills" / "greet" / "SKILL.md").read_text(encoding="utf-8")
    second = migrate.migrate_workspace(
        workspace_db, legacy_root=legacy_root, data_root=data_root, apply=True
    )

    assert (data_root / "global" / "skills" / "greet" / "SKILL.md").read_text(encoding="utf-8") == first
    assert second["global_skills"] == 1  # still exactly one global skill


def test_apply_backs_up_legacy_tree_into_archive(
    workspace_db: Session, migrate: Any, tmp_path: Path
) -> None:
    """Every applied migration archives the legacy tree under {DATA_ROOT}/archive/."""
    legacy_root = tmp_path / "old-skills"
    data_root = tmp_path / "data"
    _seed_legacy_two_layer(legacy_root)

    migrate.migrate_workspace(workspace_db, legacy_root=legacy_root, data_root=data_root, apply=True)

    archives = list((data_root / "archive").iterdir())
    assert len(archives) == 1
    assert archives[0].is_dir()
    assert (archives[0] / "global" / "skills" / "greet" / "SKILL.md").exists()


def test_cleanup_expired_archives_removes_only_old_entries(migrate: Any, tmp_path: Path) -> None:
    """Archives older than 7 days are cleaned; fresh archives survive."""
    archive_root = tmp_path / "data" / "archive"
    old = archive_root / "20200101-000000"
    fresh = archive_root / "29990101-000000"
    old.mkdir(parents=True)
    fresh.mkdir(parents=True)
    old_marker = old / "global"
    old_marker.mkdir()
    stale_past = time.time() - 8 * 86400
    os.utime(old, (stale_past, stale_past))
    os.utime(old_marker, (stale_past, stale_past))

    removed = migrate.cleanup_expired_archives(tmp_path / "data")

    assert removed == 1
    assert not old.exists()
    assert fresh.exists()


def test_backfill_sets_agent_workspace_fields(workspace_db: Session, migrate: Any, tmp_path: Path) -> None:
    """Apps with NULL G2 fields are backfilled (agent_dir / status / hash)."""
    legacy_root = tmp_path / "old-skills"
    data_root = tmp_path / "data"
    _seed_legacy_two_layer(legacy_root)
    app = AgentApp(
        name="demo",
        system_prompt="p",
        engine="deepagents",
        status="published",
        skill_names=["greet"],
    )
    workspace_db.add(app)
    workspace_db.commit()
    workspace_db.refresh(app)
    assert app.agent_dir is None
    assert app.agent_workspace_status == "pending"

    migrate.migrate_workspace(workspace_db, legacy_root=legacy_root, data_root=data_root, apply=True)

    workspace_db.refresh(app)
    assert app.agent_dir is not None and str(app.id) in app.agent_dir
    assert app.agent_workspace_status == "active"
    assert app.workspace_hash  # Agent layer (copied from Global) is fingerprinted
