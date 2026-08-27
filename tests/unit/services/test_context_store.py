"""Unit tests for the L2 context store (spec-g3-session §4.1.1).

Pure file-domain layer: JSONL append/read/rewrite/delete under
``{DATA_ROOT}/agents/<app_id>/users/<user_id>/sessions/<session_id>.jsonl``
with a per-file asyncio lock, corrupt-line tolerance and atomic rewrites.
"""

import asyncio
import json
from pathlib import Path

import pytest

from app.services.agents import context_store, skills_store

pytestmark = pytest.mark.unit


def _row(seq: int, *, type_: str = "message", **extra: object) -> dict:
    """Build a valid L2 row with the §4.1.1 required fields."""
    row: dict = {"seq": seq, "ts": "2026-09-02T10:00:00Z", "type": type_}
    row.update(extra)
    return row


@pytest.fixture()
def data_root(tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> Path:
    """Isolate the G2 workspace root in tmp_path."""
    monkeypatch.setattr(skills_store, "_data_root", lambda: tmp_path)
    return tmp_path


def test_session_file_path_layout(data_root: Path) -> None:
    """Path layout: agents/<app_id>/users/<user_id>/sessions/<sid>.jsonl (§4.1.1)."""
    path = context_store.session_file_path(app_id=7, user_id=3, session_id="s-abc")
    assert path == data_root / "agents" / "7" / "users" / "3" / "sessions" / "s-abc.jsonl"


def test_append_rows_creates_dirs_file_and_appends(data_root: Path) -> None:
    """append_rows creates the parent dirs and appends rows as JSONL lines."""
    path = context_store.session_file_path(7, 3, "s1")

    asyncio.run(context_store.append_rows(path, [_row(1, role="user", content="hi")]))
    asyncio.run(context_store.append_rows(path, [_row(2, role="assistant", content="yo")]))

    lines = path.read_text(encoding="utf-8").splitlines()
    assert len(lines) == 2
    assert json.loads(lines[0])["seq"] == 1
    assert json.loads(lines[1])["content"] == "yo"


def test_read_rows_parses_all_lines(data_root: Path) -> None:
    """read_rows returns every parsed row in file order."""
    path = context_store.session_file_path(7, 3, "s1")
    rows = [_row(1, role="user", content="hi"), _row(2, type_="tool_call", name="echo", summary="ran")]
    asyncio.run(context_store.append_rows(path, rows))

    loaded = asyncio.run(context_store.read_rows(path))
    assert loaded == rows


def test_read_rows_missing_file_returns_empty(data_root: Path) -> None:
    """A missing L2 file reads as an empty transcript."""
    path = context_store.session_file_path(7, 3, "never")
    assert asyncio.run(context_store.read_rows(path)) == []


def test_read_rows_skips_corrupt_lines(data_root: Path) -> None:
    """Corrupt JSONL lines are skipped (logged), valid lines survive."""
    path = context_store.session_file_path(7, 3, "s1")
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(
        json.dumps(_row(1, role="user", content="ok")) + "\n"
        "{not-json\n"
        + json.dumps(_row(2, role="assistant", content="fine")) + "\n",
        encoding="utf-8",
    )

    loaded = asyncio.run(context_store.read_rows(path))
    assert [row["seq"] for row in loaded] == [1, 2]


def test_next_seq_missing_file_returns_one(data_root: Path) -> None:
    """The first row of a fresh session gets seq=1."""
    assert asyncio.run(context_store.next_seq(context_store.session_file_path(7, 3, "new"))) == 1


def test_next_seq_returns_max_plus_one(data_root: Path) -> None:
    """next_seq = max(existing seq) + 1."""
    path = context_store.session_file_path(7, 3, "s1")
    asyncio.run(context_store.append_rows(path, [_row(1), _row(5), _row(3)]))
    assert asyncio.run(context_store.next_seq(path)) == 6


def test_rewrite_all_replaces_content_atomically(data_root: Path) -> None:
    """rewrite_all replaces the whole file (tmp + rename), no .tmp residue."""
    path = context_store.session_file_path(7, 3, "s1")
    asyncio.run(context_store.append_rows(path, [_row(1, role="user", content="old"), _row(2)]))

    replacement = [_row(1, type_="summary", content="compressed")]
    asyncio.run(context_store.rewrite_all(path, replacement))

    assert asyncio.run(context_store.read_rows(path)) == replacement
    assert list(path.parent.glob("*.tmp")) == []


def test_delete_session_file_is_idempotent(data_root: Path) -> None:
    """Deleting a missing file is a success, not an error."""
    path = context_store.session_file_path(7, 3, "s1")
    asyncio.run(context_store.append_rows(path, [_row(1)]))

    asyncio.run(context_store.delete_session_file(path))
    assert not path.exists()

    asyncio.run(context_store.delete_session_file(path))  # second delete: still fine


def test_concurrent_appends_keep_lines_intact(data_root: Path) -> None:
    """Per-file lock: parallel appends never interleave partial lines."""
    path = context_store.session_file_path(7, 3, "s1")

    async def run() -> None:
        await asyncio.gather(
            *(context_store.append_rows(path, [_row(i, role="user", content=f"m{i}")]) for i in range(1, 11))
        )

    asyncio.run(run())

    loaded = asyncio.run(context_store.read_rows(path))
    assert len(loaded) == 10
    assert sorted(row["seq"] for row in loaded) == list(range(1, 11))
