"""L2 context store: product-grade JSONL transcripts for agent sessions.

Pure file-domain layer (spec-g3-session §4.1.1) — zero DB access, zero
runtime imports. Every session owns an append-only JSONL file under the G2
user workspace::

    {DATA_ROOT}/agents/<app_id>/users/<user_id>/sessions/<session_id>.jsonl

Row schema (§4.1.1): ``seq`` (monotonic, 1-based), ``ts`` (ISO8601 UTC),
``type`` (message | tool_call | summary), plus per-type fields
(``role``/``content``/``name``/``summary``) and a reserved ``metadata``
object. Callers (runtime hooks, sessions_service) own row construction and
seq allocation via :func:`next_seq`; this module only persists bytes.

Write strategy: append under a process-wide per-file ``asyncio.Lock``;
full rewrites (L1 self-heal) go through tmp + rename so readers never see
a half-written file. Blocking IO runs in ``asyncio.to_thread`` following
the skills_store convention.
"""

import asyncio
import json
from collections.abc import Sequence
from pathlib import Path

from app.core.logging import logger
from app.services.agents import skills_store

_FILE_LOCKS: dict[Path, asyncio.Lock] = {}
"""Per-file lock registry: serialises append/rewrite within this process."""


def _lock_for(path: Path) -> asyncio.Lock:
    """Return (creating on first use) the per-file lock for ``path``."""
    lock = _FILE_LOCKS.get(path)
    if lock is None:
        lock = asyncio.Lock()
        _FILE_LOCKS[path] = lock
    return lock


def session_file_path(app_id: int, user_id: int, session_id: str) -> Path:
    """Return the L2 JSONL path for one (app, user, session) triple."""
    # Same-package private helper reuse follows the assembly.py precedent.
    return skills_store._user_dir(app_id, user_id) / "sessions" / f"{session_id}.jsonl"  # noqa: SLF001


def _write_append(path: Path, rows: Sequence[dict]) -> None:
    """Blocking append: ensure dirs exist, then append one JSONL line per row."""
    path.parent.mkdir(parents=True, exist_ok=True)
    with path.open("a", encoding="utf-8") as handle:
        for row in rows:
            handle.write(json.dumps(row, ensure_ascii=False) + "\n")


def _read_rows_blocking(path: Path) -> list[dict]:
    """Parse every valid JSONL line; corrupt lines are logged and skipped."""
    if not path.exists():
        return []
    rows: list[dict] = []
    with path.open(encoding="utf-8") as handle:
        for line_no, line in enumerate(handle, start=1):
            stripped = line.strip()
            if not stripped:
                continue
            try:
                rows.append(json.loads(stripped))
            except json.JSONDecodeError:
                logger.warning(
                    "context_store_corrupt_line_skipped", file=str(path), line_no=line_no
                )
    return rows


def _rewrite_all_blocking(path: Path, rows: Sequence[dict]) -> None:
    """Atomic full rewrite: write a tmp sibling, then rename over the target."""
    path.parent.mkdir(parents=True, exist_ok=True)
    tmp_path = path.with_name(path.name + ".tmp")
    with tmp_path.open("w", encoding="utf-8") as handle:
        for row in rows:
            handle.write(json.dumps(row, ensure_ascii=False) + "\n")
    tmp_path.replace(path)


async def append_rows(path: Path, rows: Sequence[dict]) -> None:
    """Append rows as JSONL lines under the per-file lock."""
    lock = _lock_for(path)
    async with lock:
        await asyncio.to_thread(_write_append, path, rows)


async def read_rows(path: Path) -> list[dict]:
    """Return every parsed row; a missing file reads as an empty transcript."""
    return await asyncio.to_thread(_read_rows_blocking, path)


async def next_seq(path: Path) -> int:
    """Return the next seq value: max(existing seq) + 1, or 1 when empty."""
    rows = await asyncio.to_thread(_read_rows_blocking, path)
    seqs = [row["seq"] for row in rows if isinstance(row.get("seq"), int)]
    return max(seqs) + 1 if seqs else 1


async def rewrite_all(path: Path, rows: Sequence[dict]) -> None:
    """Atomically replace the file content (tmp + rename) under the lock."""
    lock = _lock_for(path)
    async with lock:
        await asyncio.to_thread(_rewrite_all_blocking, path, rows)


async def delete_session_file(path: Path) -> None:
    """Delete the L2 file; a missing file counts as success."""
    lock = _lock_for(path)
    async with lock:
        await asyncio.to_thread(lambda: path.unlink(missing_ok=True))
