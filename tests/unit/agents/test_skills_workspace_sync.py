"""Unit tests for the skill workspace-sync (directory reconciliation).

Mirrors the MCP stdio sync template: ``scan_global_dir`` degrades per-file
on broken inputs, ``plan_workspace_sync`` is a zero-write dry-run and
``apply_workspace_sync`` reconciles the DB against
``{DATA_ROOT}/global/skills/*/SKILL.md`` (DB is the source of truth; disk-
only files are imported). Zero real network / zero real LLM.
"""

import asyncio
import hashlib
from pathlib import Path
from typing import Any

import pytest

from app.core.config import settings
from app.models.agent_assets import SkillAsset
from app.services.agents import skills_store

pytestmark = pytest.mark.unit


class FakeResult:
    """Minimal stand-in for a sqlmodel ExecResult."""

    def __init__(self, items: list[SkillAsset]) -> None:
        """Store the rows this fake result should return."""
        self._items = items

    def all(self) -> list[SkillAsset]:
        """Return all rows."""
        return list(self._items)


class FakeDBSession:
    """In-memory fake of the DB session surface used by skills_store."""

    def __init__(self) -> None:
        """Start with an empty in-memory row store."""
        self.rows: dict[str, SkillAsset] = {}
        self.commits: int = 0

    def get(self, model: Any, key: str) -> SkillAsset | None:  # noqa: ARG002
        """Return the row stored under key."""
        return self.rows.get(key)

    def add(self, obj: SkillAsset) -> None:
        """Upsert a row keyed by its name."""
        self.rows[obj.name] = obj

    def delete(self, obj: SkillAsset) -> None:
        """Remove the row matching the given object."""
        self.rows.pop(obj.name, None)

    def commit(self) -> None:
        """Count a commit."""
        self.commits += 1

    def rollback(self) -> None:
        """No-op rollback (the fake keeps its state)."""
        return None

    def exec(self, statement: Any) -> FakeResult:  # noqa: ARG002
        """Return every stored row regardless of the statement."""
        return FakeResult(list(self.rows.values()))


@pytest.fixture
def data_root(tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> Path:
    """Redirect DATA_ROOT into a per-test temp directory."""
    root = tmp_path / "data"
    monkeypatch.setattr(settings, "DATA_ROOT", str(root))
    return root


@pytest.fixture
def db() -> FakeDBSession:
    """Fresh in-memory fake DB session per test."""
    return FakeDBSession()


def _skill_file(root: Path, name: str) -> Path:
    return root / "global" / "skills" / name / "SKILL.md"


def _seed(db: FakeDBSession, data_root: Path, name: str = "greet", body: str = "v1") -> None:
    """Create a DB-backed skill through the regular store path."""
    asyncio.run(
        skills_store.create_global(db, name=name, description=f"{name} desc", body=body)
    )


# ---------------------------------------------------------------------------
# Branch 1: DB row + matching file -> unchanged
# ---------------------------------------------------------------------------


def test_plan_reports_unchanged_when_db_and_disk_match(
    data_root: Path, db: FakeDBSession
) -> None:
    """A healthy DB row whose rendered file matches disk stays unchanged."""
    _seed(db, data_root)

    report = asyncio.run(skills_store.plan_workspace_sync(db))

    assert report["unchanged"] == ["greet"]
    assert report["rewritten"] == []
    assert report["imported"] == []
    assert report["invalid"] == []
    assert report["scanned"] == 1


# ---------------------------------------------------------------------------
# Branch 2: DB row + drifted file -> rewritten from the DB
# ---------------------------------------------------------------------------


def test_apply_rewrites_drifted_file_from_db(data_root: Path, db: FakeDBSession) -> None:
    """A drifted disk file is rewritten from the DB row (DB is truth)."""
    _seed(db, data_root)
    path = _skill_file(data_root, "greet")
    path.write_text("drifted content", encoding="utf-8")

    report = asyncio.run(skills_store.apply_workspace_sync(db))

    assert report["rewritten"] == ["greet"]
    asset = db.rows["greet"]
    expected = skills_store.render_skill_md("greet", asset.description, asset.body)
    assert path.read_text(encoding="utf-8") == expected
    assert asset.content_hash == hashlib.sha256(expected.encode("utf-8")).hexdigest()


# ---------------------------------------------------------------------------
# Branch 3: DB row + missing file -> rebuilt from the DB
# ---------------------------------------------------------------------------


def test_apply_rebuilds_missing_file_from_db(data_root: Path, db: FakeDBSession) -> None:
    """A lost disk file is rebuilt from the DB row (refresh direction)."""
    _seed(db, data_root)
    path = _skill_file(data_root, "greet")
    path.unlink()

    report = asyncio.run(skills_store.apply_workspace_sync(db))

    assert report["rewritten"] == ["greet"]
    assert path.exists()
    assert path.read_text(encoding="utf-8") == skills_store.render_skill_md(
        "greet", db.rows["greet"].description, db.rows["greet"].body
    )


# ---------------------------------------------------------------------------
# Branch 4: disk-only file -> imported into the DB
# ---------------------------------------------------------------------------


def test_apply_imports_disk_only_file_with_frontmatter(
    data_root: Path, db: FakeDBSession
) -> None:
    """A disk-only file with frontmatter is imported and normalized."""
    path = _skill_file(data_root, "manual")
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(
        "---\nname: manual\ndescription: imported skill\n---\n# manual\n\nsteps\n",
        encoding="utf-8",
    )

    report = asyncio.run(skills_store.apply_workspace_sync(db))

    assert report["imported"] == ["manual"]
    row = db.rows["manual"]
    assert row.description == "imported skill"
    assert row.body == "# manual\n\nsteps\n"
    assert row.created_by == "workspace-sync"
    expected = skills_store.render_skill_md("manual", "imported skill", "# manual\n\nsteps\n")
    assert row.content_hash == hashlib.sha256(expected.encode("utf-8")).hexdigest()
    # The file is rewritten to the normalized rendered format.
    assert path.read_text(encoding="utf-8") == expected


def test_apply_imports_legacy_file_without_frontmatter(
    data_root: Path, db: FakeDBSession
) -> None:
    """A legacy body-only file imports with dir-name + first prose line."""
    path = _skill_file(data_root, "legacy")
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text("# legacy\n\nFirst prose line of the body.\n", encoding="utf-8")

    report = asyncio.run(skills_store.apply_workspace_sync(db))

    assert report["imported"] == ["legacy"]
    row = db.rows["legacy"]
    assert row.name == "legacy"
    assert row.description == "First prose line of the body."
    assert row.body == "# legacy\n\nFirst prose line of the body.\n"


# ---------------------------------------------------------------------------
# Branch 5: invalid files degrade per-file without blocking the rest
# ---------------------------------------------------------------------------


def test_invalid_files_degrade_per_file(data_root: Path, db: FakeDBSession) -> None:
    """Broken files are recorded per-file; healthy imports still proceed."""
    skills_root = data_root / "global" / "skills"
    # frontmatter name conflicts with the directory name
    conflict = skills_root / "conflict" / "SKILL.md"
    conflict.parent.mkdir(parents=True)
    conflict.write_text("---\nname: other\ndescription: x\n---\nbody\n", encoding="utf-8")
    # broken YAML frontmatter
    broken = skills_root / "broken" / "SKILL.md"
    broken.parent.mkdir(parents=True)
    broken.write_text("---\nname: [unclosed\n---\nbody\n", encoding="utf-8")
    # one healthy disk-only file must still be imported
    good = skills_root / "good" / "SKILL.md"
    good.parent.mkdir(parents=True)
    good.write_text("# good\n\nprose line\n", encoding="utf-8")

    report = asyncio.run(skills_store.apply_workspace_sync(db))

    assert report["imported"] == ["good"]
    invalid_files = {entry["file"] for entry in report["invalid"]}
    assert invalid_files == {"conflict/SKILL.md", "broken/SKILL.md"}
    assert all(entry["reason"] for entry in report["invalid"])
    assert "good" in db.rows
    assert "conflict" not in db.rows


def test_scan_rejects_oversized_and_empty_files(data_root: Path) -> None:
    """Oversized and empty-bodied files degrade to invalid in the scan."""
    skills_root = data_root / "global" / "skills"
    huge = skills_root / "huge" / "SKILL.md"
    huge.parent.mkdir(parents=True)
    huge.write_text("# huge\n\n" + "x" * (skills_store._SYNC_MAX_FILE_BYTES + 1), encoding="utf-8")
    empty = skills_root / "empty" / "SKILL.md"
    empty.parent.mkdir(parents=True)
    empty.write_text("---\nname: empty\ndescription: x\n---\n\n", encoding="utf-8")

    scan = skills_store.scan_global_dir()

    assert scan["valid"] == {}
    assert {entry["file"] for entry in scan["invalid"]} == {"huge/SKILL.md", "empty/SKILL.md"}


# ---------------------------------------------------------------------------
# Dry-run zero side effects + idempotency
# ---------------------------------------------------------------------------


def test_plan_dry_run_has_no_side_effects(data_root: Path, db: FakeDBSession) -> None:
    """plan_workspace_sync never writes files or DB rows."""
    _seed(db, data_root)
    path = _skill_file(data_root, "greet")
    path.write_text("drifted", encoding="utf-8")
    stray = _skill_file(data_root, "stray")
    stray.parent.mkdir(parents=True)
    stray_text = "# stray\n\nstray prose\n"
    stray.write_text(stray_text, encoding="utf-8")

    commits_before = db.commits
    report = asyncio.run(skills_store.plan_workspace_sync(db))

    assert report["rewritten"] == ["greet"]
    assert report["imported"] == ["stray"]
    assert path.read_text(encoding="utf-8") == "drifted"
    assert stray.read_text(encoding="utf-8") == stray_text
    assert "stray" not in db.rows
    assert db.commits == commits_before


def test_apply_is_idempotent_second_pass_all_unchanged(
    data_root: Path, db: FakeDBSession
) -> None:
    """A second sync right after the first reports everything unchanged."""
    _seed(db, data_root)
    stray = _skill_file(data_root, "stray")
    stray.parent.mkdir(parents=True)
    stray.write_text("# stray\n\nstray prose\n", encoding="utf-8")

    first = asyncio.run(skills_store.apply_workspace_sync(db))
    second = asyncio.run(skills_store.apply_workspace_sync(db))

    assert sorted(first["rewritten"] + first["imported"]) == ["stray"]
    assert second["unchanged"] == ["greet", "stray"]
    assert second["rewritten"] == []
    assert second["imported"] == []
    assert second["invalid"] == []


def test_scan_missing_root_returns_empty(data_root: Path) -> None:
    """A missing global skills root degrades to an empty scan."""
    scan = skills_store.scan_global_dir()
    assert scan == {"valid": {}, "invalid": []}
