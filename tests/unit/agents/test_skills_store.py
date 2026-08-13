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
from app.models.agent_assets import SkillAsset
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
def skills_root(tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> Path:
    """Redirect settings.SKILLS_ROOT into an isolated tmp directory."""
    root = tmp_path / "skills"
    monkeypatch.setattr(settings, "SKILLS_ROOT", str(root))
    return root


@pytest.fixture
def db() -> FakeDBSession:
    """Fresh in-memory fake DB session per test."""
    return FakeDBSession()


def _global_skill_file(root: Path, name: str) -> Path:
    return root / "global" / name / "SKILL.md"


def _user_skill_file(root: Path, user_id: str, name: str) -> Path:
    return root / "users" / user_id / name / "SKILL.md"


# ---------------------------------------------------------------------------
# create_global
# ---------------------------------------------------------------------------


def test_create_global_writes_file_and_db_row(skills_root: Path, db: FakeDBSession) -> None:
    """create_global atomically writes SKILL.md and inserts a hashed DB row."""
    body = "# greet\n\nsay hello"
    asset = asyncio.run(
        skills_store.create_global(db, name="greet", description="greets", body=body, created_by="alice")
    )

    file_path = _global_skill_file(skills_root, "greet")
    assert file_path.read_text(encoding="utf-8") == body
    assert asset.name == "greet"
    assert asset.content_hash == hashlib.sha256(body.encode("utf-8")).hexdigest()
    assert asset.version == 1
    assert asset.created_by == "alice"
    assert db.rows["greet"] is asset
    assert db.commits == 1


def test_create_global_duplicate_raises(skills_root: Path, db: FakeDBSession) -> None:
    """Creating an already-existing skill raises and leaves the original intact."""
    asyncio.run(skills_store.create_global(db, name="greet", description="greets", body="v1"))

    with pytest.raises(ValueError, match="already exists"):
        asyncio.run(skills_store.create_global(db, name="greet", description="again", body="v2"))

    # original content untouched
    assert _global_skill_file(skills_root, "greet").read_text(encoding="utf-8") == "v1"


def test_create_global_db_conflict_writes_no_file(skills_root: Path, db: FakeDBSession) -> None:
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

    assert not any(skills_root.rglob("SKILL.md"))  # no orphaned file on disk
    assert racing.rows == {}  # rolled back, nothing persisted


def test_create_global_file_write_failure_compensates_db_row(
    skills_root: Path, db: FakeDBSession, monkeypatch: pytest.MonkeyPatch
) -> None:
    """A failed disk write after the DB commit removes the orphaned row."""

    def failing_write(path: Any, content: str) -> None:
        raise OSError("disk full")

    monkeypatch.setattr(skills_store, "_atomic_write", failing_write)

    with pytest.raises(OSError, match="disk full"):
        asyncio.run(skills_store.create_global(db, name="greet", description="d", body="v1"))

    assert db.rows == {}  # compensation deleted the orphaned row
    assert not any(skills_root.rglob("SKILL.md"))


# ---------------------------------------------------------------------------
# update_global
# ---------------------------------------------------------------------------


def test_update_global_rewrites_file_and_bumps_version(skills_root: Path, db: FakeDBSession) -> None:
    """update_global rewrites the file, refreshes the hash and bumps version."""
    asyncio.run(skills_store.create_global(db, name="greet", description="greets", body="v1"))

    asset = asyncio.run(skills_store.update_global(db, name="greet", description="greets v2", body="v2"))

    assert _global_skill_file(skills_root, "greet").read_text(encoding="utf-8") == "v2"
    assert asset.version == 2
    assert asset.description == "greets v2"
    assert asset.content_hash == hashlib.sha256(b"v2").hexdigest()


def test_update_global_missing_skill_raises(skills_root: Path, db: FakeDBSession) -> None:
    """Updating an unknown skill raises a business error."""
    with pytest.raises(ValueError, match="not found"):
        asyncio.run(skills_store.update_global(db, name="ghost", body="v1"))


def test_update_global_file_write_failure_reverts_row(
    skills_root: Path, db: FakeDBSession, monkeypatch: pytest.MonkeyPatch
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
    assert _global_skill_file(skills_root, "greet").read_text(encoding="utf-8") == "v1"
    reverted = db.rows["greet"]
    assert reverted.content_hash == original_hash
    assert reverted.version == 1
    assert reverted.description == "greets"


# ---------------------------------------------------------------------------
# delete_global
# ---------------------------------------------------------------------------


def test_delete_global_cascades_user_copies(skills_root: Path, db: FakeDBSession) -> None:
    """delete_global removes the global dir, every user copy and the DB row."""
    asyncio.run(skills_store.create_global(db, name="greet", description="greets", body="v1"))
    asyncio.run(skills_store.materialize_for_user("user-1", ["greet"]))
    asyncio.run(skills_store.materialize_for_user("user-2", ["greet"]))

    asyncio.run(skills_store.delete_global(db, name="greet"))

    assert not (skills_root / "global" / "greet").exists()
    assert not _user_skill_file(skills_root, "user-1", "greet").exists()
    assert not _user_skill_file(skills_root, "user-2", "greet").exists()
    assert "greet" not in db.rows
    # unrelated user directories survive
    assert (skills_root / "users" / "user-1").exists()


def test_delete_global_missing_skill_raises(skills_root: Path, db: FakeDBSession) -> None:
    """Deleting an unknown skill raises a business error."""
    with pytest.raises(ValueError, match="not found"):
        asyncio.run(skills_store.delete_global(db, name="ghost"))


# ---------------------------------------------------------------------------
# list_global / read_global
# ---------------------------------------------------------------------------


def test_list_global_returns_metadata(skills_root: Path, db: FakeDBSession) -> None:
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


def test_read_global_returns_body(skills_root: Path, db: FakeDBSession) -> None:
    """read_global returns the raw SKILL.md body."""
    asyncio.run(skills_store.create_global(db, name="greet", description="greets", body="hello body"))

    assert asyncio.run(skills_store.read_global("greet")) == "hello body"


def test_read_global_missing_raises(skills_root: Path) -> None:
    """Reading an unknown skill raises a business error."""
    with pytest.raises(ValueError, match="not found"):
        asyncio.run(skills_store.read_global("ghost"))


# ---------------------------------------------------------------------------
# materialize_for_user / sync_user_skills
# ---------------------------------------------------------------------------


def test_materialize_overwrites_stale_user_copy(skills_root: Path, db: FakeDBSession) -> None:
    """materialize_for_user overwrites an outdated user copy with fresh content."""
    asyncio.run(skills_store.create_global(db, name="greet", description="greets", body="v1"))
    asyncio.run(skills_store.materialize_for_user("user-1", ["greet"]))

    asyncio.run(skills_store.update_global(db, name="greet", body="v2"))
    asyncio.run(skills_store.materialize_for_user("user-1", ["greet"]))

    assert _user_skill_file(skills_root, "user-1", "greet").read_text(encoding="utf-8") == "v2"


def test_materialize_missing_global_skill_raises(skills_root: Path) -> None:
    """Materializing an unknown global skill raises a business error."""
    with pytest.raises(ValueError, match="not found"):
        asyncio.run(skills_store.materialize_for_user("user-1", ["ghost"]))


def test_sync_user_skills_refreshes_and_prunes_stale(skills_root: Path, db: FakeDBSession) -> None:
    """sync_user_skills re-copies associated skills and removes leftover dirs."""
    asyncio.run(skills_store.create_global(db, name="keep", description="k", body="keep-body"))
    asyncio.run(skills_store.create_global(db, name="fresh", description="f", body="fresh-v2"))
    # seed a stale user copy of "keep" plus a now-unassociated leftover "old"
    asyncio.run(skills_store.materialize_for_user("user-1", ["keep", "fresh"]))
    leftover_dir = skills_root / "users" / "user-1" / "old"
    leftover_dir.mkdir(parents=True)
    (leftover_dir / "SKILL.md").write_text("stale", encoding="utf-8")
    asyncio.run(skills_store.update_global(db, name="fresh", body="fresh-v3"))

    asyncio.run(skills_store.sync_user_skills("user-1", ["keep", "fresh"]))

    assert _user_skill_file(skills_root, "user-1", "fresh").read_text(encoding="utf-8") == "fresh-v3"
    assert _user_skill_file(skills_root, "user-1", "keep").read_text(encoding="utf-8") == "keep-body"
    assert not leftover_dir.exists()


def test_sync_user_skills_empty_set_clears_all(skills_root: Path, db: FakeDBSession) -> None:
    """Syncing with an empty association set clears every user skill copy."""
    asyncio.run(skills_store.create_global(db, name="keep", description="k", body="keep-body"))
    asyncio.run(skills_store.materialize_for_user("user-1", ["keep"]))

    asyncio.run(skills_store.sync_user_skills("user-1", []))

    assert not (skills_root / "users" / "user-1" / "keep").exists()


# ---------------------------------------------------------------------------
# name validation (path traversal protection)
# ---------------------------------------------------------------------------


@pytest.mark.parametrize(
    "bad_name",
    ["../evil", "..", "evil/../x", "Evil", "-lead-dash", "_lead-underscore", "has space", "", "a/b"],
)
def test_invalid_skill_name_rejected(skills_root: Path, db: FakeDBSession, bad_name: str) -> None:
    """Names violating the safe pattern (incl. traversal) are rejected."""
    with pytest.raises(ValueError, match="invalid skill name"):
        asyncio.run(skills_store.create_global(db, name=bad_name, description="x", body="x"))

    assert not any(skills_root.rglob("SKILL.md"))


def test_invalid_user_id_rejected(skills_root: Path, db: FakeDBSession) -> None:
    """User ids that could escape the user directory are rejected."""
    asyncio.run(skills_store.create_global(db, name="greet", description="greets", body="v1"))

    with pytest.raises(ValueError, match="invalid user id"):
        asyncio.run(skills_store.materialize_for_user("../escape", ["greet"]))


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
