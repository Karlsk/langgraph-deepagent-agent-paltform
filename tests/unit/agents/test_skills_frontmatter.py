"""Unit tests for SKILL.md YAML frontmatter rendering (disk format upgrade).

The runtime SkillsMiddleware (deepagents) skips any SKILL.md without valid
YAML frontmatter carrying ``name`` + ``description``. The DB keeps storing the
plain body; every disk write renders the frontmatter from the row's
``name``/``description``, and reads expose the plain body (frontmatter is a
disk-only concern). Zero real network / zero real LLM.
"""

import asyncio
import hashlib
from pathlib import Path
from typing import Any

import pytest
import yaml

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


def _global_skill_file(root: Path, name: str) -> Path:
    return root / "global" / "skills" / name / "SKILL.md"


def _frontmatter_of(text: str) -> dict[str, Any]:
    """Parse the YAML frontmatter block of a SKILL.md text."""
    match = skills_store._FRONTMATTER_PATTERN.match(text)
    assert match is not None, f"no frontmatter found in: {text[:80]!r}"
    return yaml.safe_load(match.group(1))


# ---------------------------------------------------------------------------
# Pure render / strip helpers
# ---------------------------------------------------------------------------


def test_render_and_strip_roundtrip() -> None:
    """strip_frontmatter(render_skill_md(...)) returns the original body."""
    body = "# greet\n\n## Steps\n1. say hello\n"
    rendered = skills_store.render_skill_md("greet", "greets people", body)

    assert rendered.startswith("---\n")
    meta = _frontmatter_of(rendered)
    assert meta["name"] == "greet"
    assert meta["description"] == "greets people"
    assert skills_store.strip_frontmatter(rendered) == body


def test_strip_frontmatter_noop_without_frontmatter() -> None:
    """Plain markdown (legacy files) passes through unchanged."""
    body = "# greet\n\nplain body, no frontmatter"
    assert skills_store.strip_frontmatter(body) == body


def test_render_escapes_yaml_special_characters() -> None:
    """Descriptions with colons / newlines survive a YAML round trip."""
    tricky = "step 1: run: fast\nsecond line with: colon"
    rendered = skills_store.render_skill_md("greet", tricky, "# greet\n")
    assert _frontmatter_of(rendered)["description"] == tricky
    assert skills_store.strip_frontmatter(rendered) == "# greet\n"


# ---------------------------------------------------------------------------
# Disk writes carry frontmatter; hash covers the rendered file
# ---------------------------------------------------------------------------


def test_create_global_writes_frontmatter_and_rendered_hash(
    data_root: Path, db: FakeDBSession
) -> None:
    """create_global renders frontmatter on disk and hashes the full file."""
    body = "# greet\n\nsay hello"
    asset = asyncio.run(
        skills_store.create_global(db, name="greet", description="greets", body=body)
    )

    text = _global_skill_file(data_root, "greet").read_text(encoding="utf-8")
    meta = _frontmatter_of(text)
    assert meta["name"] == "greet"  # frontmatter name == directory name
    assert meta["description"] == "greets"
    assert skills_store.strip_frontmatter(text) == body
    # DB row keeps the plain body (frontmatter is disk-only).
    assert asset.body == body
    assert asset.content_hash == hashlib.sha256(text.encode("utf-8")).hexdigest()


def test_update_global_description_only_rewrites_file(data_root: Path, db: FakeDBSession) -> None:
    """A description-only PATCH refreshes the on-disk frontmatter."""
    asyncio.run(skills_store.create_global(db, name="greet", description="old", body="# greet\n"))

    asset = asyncio.run(skills_store.update_global(db, name="greet", description="new"))

    text = _global_skill_file(data_root, "greet").read_text(encoding="utf-8")
    assert _frontmatter_of(text)["description"] == "new"
    assert skills_store.strip_frontmatter(text) == "# greet\n"
    assert asset.content_hash == hashlib.sha256(text.encode("utf-8")).hexdigest()


def test_read_global_returns_plain_body_from_frontmattered_file(
    data_root: Path, db: FakeDBSession
) -> None:
    """read_global strips the frontmatter: the API keeps serving the body."""
    body = "# greet\n\nsay hello"
    asyncio.run(skills_store.create_global(db, name="greet", description="greets", body=body))

    assert asyncio.run(skills_store.read_global(db, "greet")) == body


def test_read_global_selfheal_writes_frontmattered_file(data_root: Path, db: FakeDBSession) -> None:
    """Self-heal rebuilds the disk file WITH frontmatter from the DB row."""
    body = "# greet\n\nsay hello"
    asyncio.run(skills_store.create_global(db, name="greet", description="greets", body=body))
    _global_skill_file(data_root, "greet").unlink()

    assert asyncio.run(skills_store.read_global(db, "greet")) == body
    text = _global_skill_file(data_root, "greet").read_text(encoding="utf-8")
    assert _frontmatter_of(text)["name"] == "greet"


# ---------------------------------------------------------------------------
# Frontmatter propagates through every materialization layer
# ---------------------------------------------------------------------------


def test_materialize_for_user_copies_frontmatter(data_root: Path, db: FakeDBSession) -> None:
    """Top-level shared user copies keep the frontmatter."""
    asyncio.run(skills_store.create_global(db, name="greet", description="greets", body="v1"))

    asyncio.run(skills_store.materialize_for_user(db, "user-1", ["greet"]))

    copy = (data_root / "users" / "user-1" / "greet" / "SKILL.md").read_text(encoding="utf-8")
    assert _frontmatter_of(copy)["name"] == "greet"


def test_materialize_into_directory_copies_frontmatter(
    data_root: Path, db: FakeDBSession, tmp_path: Path
) -> None:
    """test_runner-style materialization keeps the frontmatter."""
    asyncio.run(skills_store.create_global(db, name="greet", description="greets", body="v1"))
    target = tmp_path / "tmp_skills"

    asyncio.run(skills_store.materialize_into_directory(db, target, ["greet"]))

    copy = (target / "greet" / "SKILL.md").read_text(encoding="utf-8")
    assert _frontmatter_of(copy)["name"] == "greet"


def test_materialize_for_agent_copies_frontmatter(data_root: Path, db: FakeDBSession) -> None:
    """The Agent-layer publish snapshot keeps the frontmatter."""
    asyncio.run(skills_store.create_global(db, name="greet", description="greets", body="v1"))

    asyncio.run(skills_store.materialize_for_agent(db, app_id=7, skill_names=["greet"]))

    copy = (
        (data_root / "agents" / "7" / "skills" / "greet" / "SKILL.md")
        .read_text(encoding="utf-8")
    )
    assert _frontmatter_of(copy)["name"] == "greet"


# ---------------------------------------------------------------------------
# refresh_disk_from_db migrates legacy body-only files
# ---------------------------------------------------------------------------


def test_refresh_upgrades_legacy_body_only_file(data_root: Path, db: FakeDBSession) -> None:
    """A legacy row (hash=sha256(body), body-only file) is rewritten with frontmatter."""
    asyncio.run(skills_store.create_global(db, name="greet", description="greets", body="v1"))
    path = _global_skill_file(data_root, "greet")
    # Simulate the pre-upgrade state: plain body file + body hash.
    path.write_text("v1", encoding="utf-8")
    db.rows["greet"].content_hash = hashlib.sha256(b"v1").hexdigest()

    report = asyncio.run(skills_store.refresh_disk_from_db(db))

    assert report == [{"name": "greet", "action": "rewritten"}]
    text = path.read_text(encoding="utf-8")
    assert _frontmatter_of(text)["name"] == "greet"
    # The hash is resynced to the rendered content so the next pass is stable.
    assert db.rows["greet"].content_hash == hashlib.sha256(text.encode("utf-8")).hexdigest()


def test_refresh_unchanged_after_upgrade_is_stable(data_root: Path, db: FakeDBSession) -> None:
    """After the first rewrite, a second refresh reports unchanged (idempotent)."""
    asyncio.run(skills_store.create_global(db, name="greet", description="greets", body="v1"))

    first = asyncio.run(skills_store.refresh_disk_from_db(db))
    second = asyncio.run(skills_store.refresh_disk_from_db(db))

    assert first == [{"name": "greet", "action": "unchanged"}]
    assert second == [{"name": "greet", "action": "unchanged"}]


def test_refresh_backfill_normalizes_legacy_null_body_row(
    data_root: Path, db: FakeDBSession
) -> None:
    """A body-NULL legacy row is backfilled and its file upgraded in place."""
    path = _global_skill_file(data_root, "legacy")
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text("# legacy\n\nold body", encoding="utf-8")
    row = SkillAsset(name="legacy", description="legacy skill", body=None, content_hash="")
    db.rows["legacy"] = row

    report = asyncio.run(skills_store.refresh_disk_from_db(db))

    assert report == [{"name": "legacy", "action": "backfilled"}]
    assert row.body == "# legacy\n\nold body"
    text = path.read_text(encoding="utf-8")
    assert _frontmatter_of(text)["name"] == "legacy"
    assert row.content_hash == hashlib.sha256(text.encode("utf-8")).hexdigest()
