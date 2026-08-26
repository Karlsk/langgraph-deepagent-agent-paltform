"""Unit tests for the agent skills store service (SKILL.md file service).

Zero real network / zero real LLM: DB sessions are fakes, llm_service is
monkeypatched, and the skills root is redirected into pytest's tmp_path.
"""

import asyncio
import hashlib
from pathlib import Path
from typing import Any

import pytest

from app.core.config import settings
from app.models.agent_assets import AgentApp, SkillAsset, SubAgentConfig
from app.services.agents import skills_store

pytestmark = pytest.mark.unit


class FakeResult:
    """Minimal stand-in for a sqlmodel ExecResult."""

    def __init__(self, items: list[SkillAsset]) -> None:
        """Store the rows this fake result should return."""
        self._items = items

    def first(self) -> SkillAsset | None:
        """Return the first row or None."""
        return self._items[0] if self._items else None

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


class FakeMessage:
    """Minimal BaseMessage stand-in carrying text content."""

    def __init__(self, content: str) -> None:
        """Carry the canned text content."""
        self.content = content


class FakeLLMService:
    """Records calls and returns a canned draft (no network)."""

    def __init__(self, draft: str = "# Draft Skill\n\nmocked draft body") -> None:
        """Store the canned draft returned by call()."""
        self.draft = draft
        self.calls: list[tuple[Any, dict[str, Any]]] = []

    async def call(self, messages: Any, **kwargs: Any) -> FakeMessage:
        """Record the call and return the canned draft."""
        self.calls.append((messages, kwargs))
        return FakeMessage(self.draft)


@pytest.fixture
def data_root(tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> Path:
    """Redirect settings.DATA_ROOT into an isolated tmp directory (G2 v3 layout)."""
    root = tmp_path / "data"
    monkeypatch.setattr(settings, "DATA_ROOT", str(root))
    return root


@pytest.fixture
def db() -> FakeDBSession:
    """Fresh in-memory fake DB session per test."""
    return FakeDBSession()


def _global_skill_file(root: Path, name: str) -> Path:
    return root / "global" / "skills" / name / "SKILL.md"


def _user_skill_file(root: Path, user_id: str, name: str) -> Path:
    return root / "users" / user_id / name / "SKILL.md"


# ---------------------------------------------------------------------------
# create_global
# ---------------------------------------------------------------------------


def test_create_global_writes_file_and_db_row(data_root: Path, db: FakeDBSession) -> None:
    """create_global atomically writes SKILL.md and inserts a hashed DB row."""
    body = "# greet\n\nsay hello"
    asset = asyncio.run(
        skills_store.create_global(db, name="greet", description="greets", body=body, created_by="alice")
    )

    file_path = _global_skill_file(data_root, "greet")
    assert file_path.read_text(encoding="utf-8") == body
    assert asset.name == "greet"
    assert asset.content_hash == hashlib.sha256(body.encode("utf-8")).hexdigest()
    assert asset.version == 1
    assert asset.created_by == "alice"
    assert db.rows["greet"] is asset
    assert db.commits == 1


def test_create_global_duplicate_raises(data_root: Path, db: FakeDBSession) -> None:
    """Creating an already-existing skill raises and leaves the original intact."""
    asyncio.run(skills_store.create_global(db, name="greet", description="greets", body="v1"))

    with pytest.raises(ValueError, match="already exists"):
        asyncio.run(skills_store.create_global(db, name="greet", description="again", body="v2"))

    # original content untouched
    assert _global_skill_file(data_root, "greet").read_text(encoding="utf-8") == "v1"


def test_create_global_db_conflict_writes_no_file(data_root: Path, db: FakeDBSession) -> None:
    """DB-first: a concurrent-insert IntegrityError fails before any disk write."""
    from sqlalchemy.exc import IntegrityError

    class RacingSession(FakeDBSession):
        def commit(self) -> None:
            self.rows = {}  # rollback semantics: the pending row is discarded
            raise IntegrityError("insert", {}, Exception("duplicate key"))

        def rollback(self) -> None:
            self.rows = {}

    racing = RacingSession()
    with pytest.raises(ValueError, match="already exists"):
        asyncio.run(skills_store.create_global(racing, name="greet", description="d", body="v1"))

    assert not any(data_root.rglob("SKILL.md"))  # no orphaned file on disk
    assert racing.rows == {}  # rolled back, nothing persisted


def test_create_global_file_write_failure_compensates_db_row(
    data_root: Path, db: FakeDBSession, monkeypatch: pytest.MonkeyPatch
) -> None:
    """A failed disk write after the DB commit removes the orphaned row."""

    def failing_write(path: Any, content: str) -> None:
        raise OSError("disk full")

    monkeypatch.setattr(skills_store, "_atomic_write", failing_write)

    with pytest.raises(OSError, match="disk full"):
        asyncio.run(skills_store.create_global(db, name="greet", description="d", body="v1"))

    assert db.rows == {}  # compensation deleted the orphaned row
    assert not any(data_root.rglob("SKILL.md"))


# ---------------------------------------------------------------------------
# update_global
# ---------------------------------------------------------------------------


def test_update_global_rewrites_file_and_bumps_version(data_root: Path, db: FakeDBSession) -> None:
    """update_global rewrites the file, refreshes the hash and bumps version."""
    asyncio.run(skills_store.create_global(db, name="greet", description="greets", body="v1"))

    asset = asyncio.run(skills_store.update_global(db, name="greet", description="greets v2", body="v2"))

    assert _global_skill_file(data_root, "greet").read_text(encoding="utf-8") == "v2"
    assert asset.version == 2
    assert asset.description == "greets v2"
    assert asset.content_hash == hashlib.sha256(b"v2").hexdigest()


def test_update_global_missing_skill_raises(data_root: Path, db: FakeDBSession) -> None:
    """Updating an unknown skill raises a business error."""
    with pytest.raises(ValueError, match="not found"):
        asyncio.run(skills_store.update_global(db, name="ghost", body="v1"))


def test_update_global_file_write_failure_reverts_row(
    data_root: Path, db: FakeDBSession, monkeypatch: pytest.MonkeyPatch
) -> None:
    """A failed disk write reverts hash/version/description to the pre-update row."""
    asset = asyncio.run(skills_store.create_global(db, name="greet", description="greets", body="v1"))
    original_hash = asset.content_hash

    def failing_write(path: Any, content: str) -> None:
        raise OSError("disk full")

    monkeypatch.setattr(skills_store, "_atomic_write", failing_write)

    with pytest.raises(OSError, match="disk full"):
        asyncio.run(skills_store.update_global(db, name="greet", description="greets v2", body="v2"))

    # Disk still holds v1 and the DB row matches it (no hash drift).
    assert _global_skill_file(data_root, "greet").read_text(encoding="utf-8") == "v1"
    reverted = db.rows["greet"]
    assert reverted.content_hash == original_hash
    assert reverted.version == 1
    assert reverted.description == "greets"


# ---------------------------------------------------------------------------
# delete_global
# ---------------------------------------------------------------------------


def test_delete_global_cascades_user_copies(data_root: Path, db: FakeDBSession) -> None:
    """delete_global removes the global dir, every user copy and the DB row."""
    asyncio.run(skills_store.create_global(db, name="greet", description="greets", body="v1"))
    asyncio.run(skills_store.materialize_for_user(db, "user-1", ["greet"]))
    asyncio.run(skills_store.materialize_for_user(db, "user-2", ["greet"]))

    asyncio.run(skills_store.delete_global(db, name="greet"))

    assert not (data_root / "global" / "skills" / "greet").exists()
    assert not _user_skill_file(data_root, "user-1", "greet").exists()
    assert not _user_skill_file(data_root, "user-2", "greet").exists()
    assert "greet" not in db.rows
    # unrelated user directories survive
    assert (data_root / "users" / "user-1").exists()


def test_delete_global_missing_skill_raises(data_root: Path, db: FakeDBSession) -> None:
    """Deleting an unknown skill raises a business error."""
    with pytest.raises(ValueError, match="not found"):
        asyncio.run(skills_store.delete_global(db, name="ghost"))


# ---------------------------------------------------------------------------
# list_global / read_global
# ---------------------------------------------------------------------------


def test_list_global_returns_metadata(data_root: Path, db: FakeDBSession) -> None:
    """list_global returns name/description/content_hash/version/created_by dicts."""
    asyncio.run(skills_store.create_global(db, name="alpha", description="d1", body="a", created_by="bob"))
    asyncio.run(skills_store.create_global(db, name="beta", description="d2", body="b"))

    items = asyncio.run(skills_store.list_global(db))

    assert {i["name"] for i in items} == {"alpha", "beta"}
    alpha = next(i for i in items if i["name"] == "alpha")
    assert alpha["description"] == "d1"
    assert alpha["created_by"] == "bob"
    assert alpha["version"] == 1
    assert alpha["content_hash"] == hashlib.sha256(b"a").hexdigest()


def test_list_global_page_paginates_and_filters(data_root: Path, db: FakeDBSession) -> None:
    """list_global_page returns a PageResult honoring page/pageSize/keyword."""
    asyncio.run(skills_store.create_global(db, name="alpha", description="d1", body="a"))
    asyncio.run(skills_store.create_global(db, name="beta", description="d2", body="b"))
    asyncio.run(skills_store.create_global(db, name="gamma", description="d3", body="c"))

    page_one = asyncio.run(skills_store.list_global_page(db, page=1, page_size=2))
    assert page_one.total == 3
    assert page_one.page == 1
    assert page_one.page_size == 2
    assert [i["name"] for i in page_one.items] == ["alpha", "beta"]

    page_two = asyncio.run(skills_store.list_global_page(db, page=2, page_size=2))
    assert [i["name"] for i in page_two.items] == ["gamma"]

    filtered = asyncio.run(skills_store.list_global_page(db, keyword="BET"))
    assert filtered.total == 1
    assert [i["name"] for i in filtered.items] == ["beta"]

    beyond = asyncio.run(skills_store.list_global_page(db, page=5))
    assert beyond.items == []
    assert beyond.total == 3


def test_read_global_returns_body(data_root: Path, db: FakeDBSession) -> None:
    """read_global returns the raw SKILL.md body."""
    asyncio.run(skills_store.create_global(db, name="greet", description="greets", body="hello body"))

    assert asyncio.run(skills_store.read_global(db, "greet")) == "hello body"


def test_read_global_missing_raises(data_root: Path, db: FakeDBSession) -> None:
    """Reading a skill missing from both disk and DB raises a business error."""
    with pytest.raises(ValueError, match="not found"):
        asyncio.run(skills_store.read_global(db, "ghost"))


# ---------------------------------------------------------------------------
# materialize_for_user / sync_user_skills
# ---------------------------------------------------------------------------


def test_materialize_overwrites_stale_user_copy(data_root: Path, db: FakeDBSession) -> None:
    """materialize_for_user overwrites an outdated user copy with fresh content."""
    asyncio.run(skills_store.create_global(db, name="greet", description="greets", body="v1"))
    asyncio.run(skills_store.materialize_for_user(db, "user-1", ["greet"]))

    asyncio.run(skills_store.update_global(db, name="greet", body="v2"))
    asyncio.run(skills_store.materialize_for_user(db, "user-1", ["greet"]))

    assert _user_skill_file(data_root, "user-1", "greet").read_text(encoding="utf-8") == "v2"


def test_materialize_missing_global_skill_raises(data_root: Path, db: FakeDBSession) -> None:
    """Materializing an unknown global skill raises a business error."""
    with pytest.raises(ValueError, match="not found"):
        asyncio.run(skills_store.materialize_for_user(db, "user-1", ["ghost"]))


def test_sync_user_skills_refreshes_and_prunes_stale(data_root: Path, db: FakeDBSession) -> None:
    """sync_user_skills re-copies associated skills and removes leftover dirs."""
    asyncio.run(skills_store.create_global(db, name="keep", description="k", body="keep-body"))
    asyncio.run(skills_store.create_global(db, name="fresh", description="f", body="fresh-v2"))
    # seed a stale user copy of "keep" plus a now-unassociated leftover "old"
    asyncio.run(skills_store.materialize_for_user(db, "user-1", ["keep", "fresh"]))
    leftover_dir = data_root / "users" / "user-1" / "old"
    leftover_dir.mkdir(parents=True)
    (leftover_dir / "SKILL.md").write_text("stale", encoding="utf-8")
    asyncio.run(skills_store.update_global(db, name="fresh", body="fresh-v3"))

    asyncio.run(skills_store.sync_user_skills(db, "user-1", ["keep", "fresh"]))

    assert _user_skill_file(data_root, "user-1", "fresh").read_text(encoding="utf-8") == "fresh-v3"
    assert _user_skill_file(data_root, "user-1", "keep").read_text(encoding="utf-8") == "keep-body"
    assert not leftover_dir.exists()


def test_sync_user_skills_empty_set_clears_all(data_root: Path, db: FakeDBSession) -> None:
    """Syncing with an empty association set clears every user skill copy."""
    asyncio.run(skills_store.create_global(db, name="keep", description="k", body="keep-body"))
    asyncio.run(skills_store.materialize_for_user(db, "user-1", ["keep"]))

    asyncio.run(skills_store.sync_user_skills(db, "user-1", []))

    assert not (data_root / "users" / "user-1" / "keep").exists()


# ---------------------------------------------------------------------------
# Dual-store: DB body persistence + disk self-heal + refresh_disk_from_db
# ---------------------------------------------------------------------------


def test_create_global_persists_body_in_db(data_root: Path, db: FakeDBSession) -> None:
    """create_global stores the full body in the DB row (source of truth)."""
    asset = asyncio.run(
        skills_store.create_global(db, name="greet", description="greets", body="# hello\n\nbody")
    )

    assert asset.body == "# hello\n\nbody"
    assert db.rows["greet"].body == "# hello\n\nbody"


def test_update_global_persists_body_in_db(data_root: Path, db: FakeDBSession) -> None:
    """update_global refreshes the DB-stored body alongside the disk file."""
    asyncio.run(skills_store.create_global(db, name="greet", description="greets", body="v1"))

    asset = asyncio.run(skills_store.update_global(db, name="greet", body="v2"))

    assert asset.body == "v2"
    assert db.rows["greet"].body == "v2"


def test_read_global_selfheals_disk_from_db(data_root: Path, db: FakeDBSession) -> None:
    """A lost disk file is rebuilt from the DB body (container-rebuild scenario)."""
    asyncio.run(skills_store.create_global(db, name="greet", description="greets", body="heal me"))
    _global_skill_file(data_root, "greet").unlink()

    assert asyncio.run(skills_store.read_global(db, "greet")) == "heal me"
    assert _global_skill_file(data_root, "greet").read_text(encoding="utf-8") == "heal me"


def test_read_global_legacy_row_without_body_and_disk_raises(
    data_root: Path, db: FakeDBSession
) -> None:
    """A legacy NULL-body row whose disk file is gone is a genuine not-found."""
    db.rows["legacy"] = SkillAsset(
        name="legacy", description="d", content_hash="deadbeef", created_by=None, version=1
    )

    with pytest.raises(ValueError, match="not found"):
        asyncio.run(skills_store.read_global(db, "legacy"))


def test_materialize_into_directory_selfheals_from_db(data_root: Path, db: FakeDBSession, tmp_path: Path) -> None:
    """materialize_into_directory survives a lost global disk copy via the DB body."""
    asyncio.run(skills_store.create_global(db, name="greet", description="greets", body="v1"))
    _global_skill_file(data_root, "greet").unlink()

    target = tmp_path / "standalone-skills"
    asyncio.run(skills_store.materialize_into_directory(db, target, ["greet"]))

    assert (target / "greet" / "SKILL.md").read_text(encoding="utf-8") == "v1"


# ---------------------------------------------------------------------------
# refresh_disk_from_db
# ---------------------------------------------------------------------------


def test_refresh_rewrites_stale_disk_file(data_root: Path, db: FakeDBSession) -> None:
    """Refresh rewrites the disk file when its hash differs from the DB hash."""
    asyncio.run(skills_store.create_global(db, name="greet", description="greets", body="db body"))
    _global_skill_file(data_root, "greet").write_text("drifted", encoding="utf-8")

    report = asyncio.run(skills_store.refresh_disk_from_db(db))

    assert report == [{"name": "greet", "action": "rewritten"}]
    assert _global_skill_file(data_root, "greet").read_text(encoding="utf-8") == "db body"


def test_refresh_rewrites_missing_disk_file(data_root: Path, db: FakeDBSession) -> None:
    """Refresh rebuilds a lost disk file from the DB body."""
    asyncio.run(skills_store.create_global(db, name="greet", description="greets", body="db body"))
    _global_skill_file(data_root, "greet").unlink()

    report = asyncio.run(skills_store.refresh_disk_from_db(db, name="greet"))

    assert report == [{"name": "greet", "action": "rewritten"}]
    assert _global_skill_file(data_root, "greet").read_text(encoding="utf-8") == "db body"


def test_refresh_skips_unchanged_file(data_root: Path, db: FakeDBSession) -> None:
    """A disk file already matching the DB hash is left untouched (unchanged)."""
    asyncio.run(skills_store.create_global(db, name="greet", description="greets", body="same"))
    before = _global_skill_file(data_root, "greet").stat().st_mtime_ns

    report = asyncio.run(skills_store.refresh_disk_from_db(db))

    assert report == [{"name": "greet", "action": "unchanged"}]
    assert _global_skill_file(data_root, "greet").stat().st_mtime_ns == before


def test_refresh_backfills_legacy_row_from_disk(data_root: Path, db: FakeDBSession) -> None:
    """A legacy NULL-body row is backfilled from its disk file (hash resynced)."""
    skill_dir = data_root / "global" / "skills" / "legacy"
    skill_dir.mkdir(parents=True)
    (skill_dir / "SKILL.md").write_text("# legacy\n\non disk only", encoding="utf-8")
    db.rows["legacy"] = SkillAsset(
        name="legacy", description="d", content_hash="stale-hash", created_by=None, version=3
    )

    report = asyncio.run(skills_store.refresh_disk_from_db(db))

    assert report == [{"name": "legacy", "action": "backfilled"}]
    assert db.rows["legacy"].body == "# legacy\n\non disk only"
    assert db.rows["legacy"].content_hash == hashlib.sha256("# legacy\n\non disk only".encode()).hexdigest()
    assert db.rows["legacy"].version == 3


def test_refresh_reports_missing_when_body_and_disk_lost(data_root: Path, db: FakeDBSession) -> None:
    """A legacy NULL-body row with no disk file is reported missing (unrecoverable)."""
    db.rows["ghost"] = SkillAsset(
        name="ghost", description="d", content_hash="deadbeef", created_by=None, version=1
    )

    report = asyncio.run(skills_store.refresh_disk_from_db(db))

    assert report == [{"name": "ghost", "action": "missing"}]


def test_refresh_single_unknown_name_raises(data_root: Path, db: FakeDBSession) -> None:
    """Refreshing a single skill that has no DB row raises a business error."""
    with pytest.raises(ValueError, match="not found"):
        asyncio.run(skills_store.refresh_disk_from_db(db, name="ghost"))


# ---------------------------------------------------------------------------
# G2 three-layer path helpers (spec v3.3 §2.1/§4.3)
# ---------------------------------------------------------------------------


def test_three_layer_path_helpers(data_root: Path) -> None:
    """Agent/User layer path templates match the G2 v3 layout (spec §2.1)."""
    assert skills_store._agent_dir(7) == data_root / "agents" / "7"
    assert skills_store._agent_skill_dir(7) == data_root / "agents" / "7" / "skills"
    assert skills_store._agent_skill_file(7, "greet") == (
        data_root / "agents" / "7" / "skills" / "greet" / "SKILL.md"
    )
    assert skills_store._user_skill_dir(7, 42) == (
        data_root / "agents" / "7" / "users" / "42" / "skills"
    )
    assert skills_store._user_skill_file(7, 42, "greet") == (
        data_root / "agents" / "7" / "users" / "42" / "skills" / "greet" / "SKILL.md"
    )


def test_global_layer_moves_under_data_root(data_root: Path) -> None:
    """The Global layer now lives at {DATA_ROOT}/global/skills/ (spec §2.1)."""
    assert skills_store._skills_root() == data_root / "global" / "skills"
    assert skills_store._global_skill_file("greet") == (
        data_root / "global" / "skills" / "greet" / "SKILL.md"
    )


def test_shared_user_copies_move_to_top_level_users(data_root: Path, db: FakeDBSession) -> None:
    """materialize_for_user (no Agent context) writes {DATA_ROOT}/users/<uid>/ (spec §2.1)."""
    asyncio.run(skills_store.create_global(db, name="greet", description="d", body="v1"))

    asyncio.run(skills_store.materialize_for_user(db, "user-1", ["greet"]))

    assert (data_root / "users" / "user-1" / "greet" / "SKILL.md").read_text(
        encoding="utf-8"
    ) == "v1"


# ---------------------------------------------------------------------------
# G2 hash utilities (spec v3.3 §4.3)
# ---------------------------------------------------------------------------


def test_hash_compare_or_write_no_op_when_match(data_root: Path) -> None:
    """An identical existing file is left untouched (mtime preserved)."""
    target = data_root / "agents" / "1" / "skills" / "greet" / "SKILL.md"
    target.parent.mkdir(parents=True)
    target.write_text("v1", encoding="utf-8")
    before = target.stat().st_mtime_ns

    written = asyncio.run(skills_store._hash_compare_or_write(target, "v1"))

    assert written is False
    assert target.stat().st_mtime_ns == before


def test_hash_compare_or_write_writes_when_diff_or_missing(data_root: Path) -> None:
    """A missing or drifted target is (re)written atomically."""
    missing = data_root / "agents" / "1" / "skills" / "new" / "SKILL.md"
    assert asyncio.run(skills_store._hash_compare_or_write(missing, "v1")) is True
    assert missing.read_text(encoding="utf-8") == "v1"

    assert asyncio.run(skills_store._hash_compare_or_write(missing, "v2")) is True
    assert missing.read_text(encoding="utf-8") == "v2"


def test_compute_workspace_hash_stable(data_root: Path) -> None:
    """The Agent-layer fingerprint is stable across recomputation."""
    skills_dir = data_root / "agents" / "1" / "skills"
    for name, body in (("alpha", "a-body"), ("beta", "b-body")):
        path = skills_dir / name / "SKILL.md"
        path.parent.mkdir(parents=True, exist_ok=True)
        path.write_text(body, encoding="utf-8")

    first = skills_store.compute_workspace_hash(skills_dir)
    second = skills_store.compute_workspace_hash(skills_dir)
    assert first == second
    assert len(first) == 64  # sha256 hex


def test_compute_workspace_hash_ignores_nested_user_layer(data_root: Path) -> None:
    """The glob (non-recursive) must not include the nested users/ subdirectory."""
    skills_dir = data_root / "agents" / "1" / "skills"
    agent_file = skills_dir / "greet" / "SKILL.md"
    agent_file.parent.mkdir(parents=True)
    agent_file.write_text("agent-copy", encoding="utf-8")
    user_file = skills_dir / "users" / "7" / "skills" / "greet" / "SKILL.md"
    user_file.parent.mkdir(parents=True)
    user_file.write_text("user-copy", encoding="utf-8")

    with_agent = skills_store.compute_workspace_hash(skills_dir)
    agent_file.unlink()
    without_agent = skills_store.compute_workspace_hash(skills_dir)

    assert with_agent != without_agent  # the user-layer copy must not keep the hash up


def test_compute_workspace_hash_different_for_diff_content(data_root: Path) -> None:
    """Different content produces a different fingerprint."""
    skills_dir = data_root / "agents" / "1" / "skills"
    path = skills_dir / "greet" / "SKILL.md"
    path.parent.mkdir(parents=True)
    path.write_text("v1", encoding="utf-8")
    hash_v1 = skills_store.compute_workspace_hash(skills_dir)

    path.write_text("v2", encoding="utf-8")
    assert skills_store.compute_workspace_hash(skills_dir) != hash_v1


def test_compute_user_workspace_hash_includes_nested_files(data_root: Path) -> None:
    """The User-layer fingerprint uses rglob (any depth below the dir)."""
    user_dir = data_root / "agents" / "1" / "users" / "7" / "skills"
    deep = user_dir / "greet" / "SKILL.md"
    deep.parent.mkdir(parents=True)
    deep.write_text("body", encoding="utf-8")

    with_file = skills_store._compute_user_workspace_hash(user_dir)
    deep.unlink()
    without_file = skills_store._compute_user_workspace_hash(user_dir)

    assert with_file != without_file


def test_compute_effective_workspace_hash_agent_overrides_global(data_root: Path) -> None:
    """Expected fingerprint resolves the Agent layer first, then Global."""
    global_file = data_root / "global" / "skills" / "greet" / "SKILL.md"
    global_file.parent.mkdir(parents=True)
    global_file.write_text("global-body", encoding="utf-8")

    from_global = skills_store._compute_effective_workspace_hash(1, ["greet"])

    agent_file = data_root / "agents" / "1" / "skills" / "greet" / "SKILL.md"
    agent_file.parent.mkdir(parents=True)
    agent_file.write_text("agent-body", encoding="utf-8")

    from_agent = skills_store._compute_effective_workspace_hash(1, ["greet"])

    assert from_global != from_agent  # the Agent copy changes the expectation


def test_compute_effective_workspace_hash_missing_source_empty_slot(data_root: Path) -> None:
    """A name with no source contributes an empty slot (position-stable)."""
    global_file = data_root / "global" / "skills" / "greet" / "SKILL.md"
    global_file.parent.mkdir(parents=True)
    global_file.write_text("body", encoding="utf-8")

    only_greet = skills_store._compute_effective_workspace_hash(1, ["greet"])
    greet_plus_ghost = skills_store._compute_effective_workspace_hash(1, ["greet", "ghost"])

    assert only_greet != greet_plus_ghost  # ghost's empty slot shifts the aggregate


# ---------------------------------------------------------------------------
# G2 materialize_for_agent (publish-time Global -> Agent copy, spec §4.1/§4.3)
# ---------------------------------------------------------------------------


def test_materialize_for_agent_creates_files(data_root: Path, db: FakeDBSession) -> None:
    """The publish-time copy lands under {DATA_ROOT}/agents/<app_id>/skills/."""
    asyncio.run(skills_store.create_global(db, name="greet", description="d", body="hello"))

    asyncio.run(skills_store.materialize_for_agent(db, app_id=3, skill_names=["greet"]))

    agent_file = data_root / "agents" / "3" / "skills" / "greet" / "SKILL.md"
    assert agent_file.read_text(encoding="utf-8") == "hello"


def test_materialize_for_agent_hash_skip(data_root: Path, db: FakeDBSession) -> None:
    """An up-to-date Agent-layer copy is left untouched (no rewrite)."""
    asyncio.run(skills_store.create_global(db, name="greet", description="d", body="v1"))
    asyncio.run(skills_store.materialize_for_agent(db, app_id=3, skill_names=["greet"]))
    agent_file = data_root / "agents" / "3" / "skills" / "greet" / "SKILL.md"
    before = agent_file.stat().st_mtime_ns

    asyncio.run(skills_store.materialize_for_agent(db, app_id=3, skill_names=["greet"]))

    assert agent_file.stat().st_mtime_ns == before


# ---------------------------------------------------------------------------
# G2 User-layer combined materialization (spec v3.3 §4.2/§4.3)
# ---------------------------------------------------------------------------


def _make_app(app_id: int, skill_names: list[str]) -> AgentApp:
    """Build a detached AgentApp row for combined-materialization tests."""
    return AgentApp(id=app_id, name=f"app-{app_id}", system_prompt="x", skill_names=skill_names)


def _make_subagent(name: str, skill_names: list[str]) -> SubAgentConfig:
    """Build a detached SubAgentConfig row for combined-materialization tests."""
    return SubAgentConfig(
        name=name,
        description=f"{name} sub",
        when_to_use="always",
        system_prompt="x",
        content_hash="hash",
        skill_names=skill_names,
    )


def test_materialize_to_user_combined_aggregates_global_and_agent(
    data_root: Path, db: FakeDBSession
) -> None:
    """App + SubAgent skill sets are unioned (deduped) into the User layer."""
    asyncio.run(skills_store.create_global(db, name="shared", description="d", body="shared-body"))
    asyncio.run(skills_store.create_global(db, name="sub-only", description="d", body="sub-body"))
    app_cfg = _make_app(1, ["shared"])
    subagents = [_make_subagent("helper", ["sub-only"])]

    asyncio.run(
        skills_store.materialize_to_user_combined(
            db, app_cfg=app_cfg, user_id=7, subagent_cfgs=subagents
        )
    )

    user_dir = data_root / "agents" / "1" / "users" / "7" / "skills"
    assert (user_dir / "shared" / "SKILL.md").read_text(encoding="utf-8") == "shared-body"
    assert (user_dir / "sub-only" / "SKILL.md").read_text(encoding="utf-8") == "sub-body"


def test_agent_skill_overrides_global_in_combined(data_root: Path, db: FakeDBSession) -> None:
    """A skill present in both layers resolves to the Agent-layer copy."""
    asyncio.run(skills_store.create_global(db, name="greet", description="d", body="global-body"))
    agent_file = data_root / "agents" / "1" / "skills" / "greet" / "SKILL.md"
    agent_file.parent.mkdir(parents=True)
    agent_file.write_text("agent-body", encoding="utf-8")
    app_cfg = _make_app(1, ["greet"])

    asyncio.run(
        skills_store.materialize_to_user_combined(db, app_cfg=app_cfg, user_id=7, subagent_cfgs=[])
    )

    user_file = data_root / "agents" / "1" / "users" / "7" / "skills" / "greet" / "SKILL.md"
    assert user_file.read_text(encoding="utf-8") == "agent-body"


def test_materialize_to_user_combined_prunes_stale(data_root: Path, db: FakeDBSession) -> None:
    """Skill dirs outside the effective set are pruned from the User layer."""
    asyncio.run(skills_store.create_global(db, name="keep", description="d", body="v1"))
    stale_dir = data_root / "agents" / "1" / "users" / "7" / "skills" / "stale"
    stale_dir.mkdir(parents=True)
    (stale_dir / "SKILL.md").write_text("stale", encoding="utf-8")
    app_cfg = _make_app(1, ["keep"])

    asyncio.run(
        skills_store.materialize_to_user_combined(db, app_cfg=app_cfg, user_id=7, subagent_cfgs=[])
    )

    assert not stale_dir.exists()
    assert (data_root / "agents" / "1" / "users" / "7" / "skills" / "keep" / "SKILL.md").exists()


def test_prune_stale_user_skills(data_root: Path) -> None:
    """_prune_stale_user_skills(target_dir, keep) removes non-kept skill dirs."""
    target = data_root / "agents" / "1" / "users" / "7" / "skills"
    keep_dir = target / "keep-1"
    stale_dir = target / "old"
    keep_dir.mkdir(parents=True)
    stale_dir.mkdir()

    skills_store._prune_stale_user_skills(target, {"keep-1"})

    assert keep_dir.is_dir()
    assert not stale_dir.exists()


def test_materialize_into_combined_directory_resolves_layers(
    data_root: Path, db: FakeDBSession, tmp_path: Path
) -> None:
    """test_runner helper copies Agent-first with Global fallback into a tmp dir."""
    asyncio.run(skills_store.create_global(db, name="greet", description="d", body="global-body"))
    asyncio.run(skills_store.create_global(db, name="solo", description="d", body="solo-body"))
    agent_file = data_root / "agents" / "1" / "skills" / "greet" / "SKILL.md"
    agent_file.parent.mkdir(parents=True)
    agent_file.write_text("agent-body", encoding="utf-8")
    target = tmp_path / "standalone-combined"

    asyncio.run(
        skills_store.materialize_into_combined_directory(
            db, target, app_id=1, skill_names=["greet", "solo"]
        )
    )

    assert (target / "greet" / "SKILL.md").read_text(encoding="utf-8") == "agent-body"
    assert (target / "solo" / "SKILL.md").read_text(encoding="utf-8") == "solo-body"


# ---------------------------------------------------------------------------
# name validation (path traversal protection)
# ---------------------------------------------------------------------------


@pytest.mark.parametrize(
    "bad_name",
    ["../evil", "..", "evil/../x", "Evil", "-lead-dash", "_lead-underscore", "has space", "", "a/b"],
)
def test_invalid_skill_name_rejected(data_root: Path, db: FakeDBSession, bad_name: str) -> None:
    """Names violating the safe pattern (incl. traversal) are rejected."""
    with pytest.raises(ValueError, match="invalid skill name"):
        asyncio.run(skills_store.create_global(db, name=bad_name, description="x", body="x"))

    assert not any(data_root.rglob("SKILL.md"))


def test_invalid_user_id_rejected(data_root: Path, db: FakeDBSession) -> None:
    """User ids that could escape the user directory are rejected."""
    asyncio.run(skills_store.create_global(db, name="greet", description="greets", body="v1"))

    with pytest.raises(ValueError, match="invalid user id"):
        asyncio.run(skills_store.materialize_for_user(db, "../escape", ["greet"]))


# ---------------------------------------------------------------------------
# generate_skill_draft
# ---------------------------------------------------------------------------


def test_generate_skill_draft_returns_mocked_draft(monkeypatch: pytest.MonkeyPatch) -> None:
    """generate_skill_draft returns the mocked LLM draft and embeds inputs in the prompt."""
    fake = FakeLLMService(draft="# deploy-db\n\n## When to use\n\n...")
    monkeypatch.setattr(skills_store, "llm_service", fake)

    draft = asyncio.run(skills_store.generate_skill_draft(description="deploy db", hint="use alembic"))

    assert draft == fake.draft
    assert len(fake.calls) == 1
    messages, _kwargs = fake.calls[0]
    prompt_text = "\n".join(m.content for m in messages)
    assert "deploy db" in prompt_text
    assert "use alembic" in prompt_text


def test_generate_skill_draft_llm_failure_propagates_without_extra_retries(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    """A terminal LLM failure surfaces after exactly one call (no outer retry layer)."""

    class FailingLLMService:
        """Counts calls and always fails like an exhausted fallback chain."""

        def __init__(self) -> None:
            """Start the call counter."""
            self.calls = 0

        async def call(self, messages: Any, **kwargs: Any) -> Any:  # noqa: ARG002
            """Record the call and raise the terminal fallback error."""
            self.calls += 1
            raise RuntimeError("failed to get response from llm after trying 2 models")

    fake = FailingLLMService()
    monkeypatch.setattr(skills_store, "llm_service", fake)

    with pytest.raises(RuntimeError, match="failed to get response from llm"):
        asyncio.run(skills_store.generate_skill_draft(description="d", hint="h"))

    assert fake.calls == 1
