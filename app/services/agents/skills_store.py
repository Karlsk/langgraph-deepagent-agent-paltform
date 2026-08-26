"""Skill file service: manages SKILL.md assets on disk plus their DB metadata.

Dual-store architecture (DB = source of truth, disk = runtime copy):

- The full SKILL.md body lives in ``SkillAsset.body`` (DB) and in the
  ``{DATA_ROOT}/global/skills/<name>/SKILL.md`` file. Writes go to both;
  reads are disk-first with a DB self-heal fallback (``read_global``), so a
  lost disk copy (e.g. container rebuild without a ``./data`` bind mount)
  is recovered transparently from the DB.
- ``SkillAsset.content_hash`` is the rewrite trigger for
  ``refresh_disk_from_db``: a disk file whose sha256 matches the row hash is
  left untouched, anything else is rewritten from the DB body. Legacy rows
  with ``body IS NULL`` are backfilled from disk (their only copy).

Directory conventions (G2 three-layer workspace, rooted at
``settings.DATA_ROOT``; spec-g2-workspace v3.3 §2.1):

- Global layer (DB truth + disk runtime copy):
  ``{DATA_ROOT}/global/skills/<name>/SKILL.md``
- Agent layer (publish-time snapshot):
  ``{DATA_ROOT}/agents/<app_id>/skills/<name>/SKILL.md``
- User layer (per-(app, user) combined workspace):
  ``{DATA_ROOT}/agents/<app_id>/users/<user_id>/skills/<name>/SKILL.md``
- Top-level shared user copies (legacy two-layer path, kept for the
  context-free ``materialize_for_user``):
  ``{DATA_ROOT}/users/<user_id>/<name>/SKILL.md``

All blocking file IO is wrapped in ``asyncio.to_thread`` so callers never
stall the event loop. Skill names are validated against a strict pattern to
prevent path traversal.
"""

import asyncio
import contextlib
import hashlib
import os
import re
import shutil
import tempfile
from collections.abc import Sequence
from pathlib import Path
from typing import Any

from langchain_core.messages import HumanMessage, SystemMessage
from sqlalchemy.exc import IntegrityError
from sqlmodel import Session, select

from app.core.config import settings
from app.core.logging import logger
from app.core.metrics import skill_sync_total
from app.schemas.base import PageResult
from app.core.observability import langfuse_callback_handler
from app.models.agent_assets import AgentApp, SkillAsset, SubAgentConfig
from app.services.llm import llm_service

_SKILL_FILE_NAME = "SKILL.md"
_NAME_PATTERN = re.compile(r"^[a-z0-9][a-z0-9_-]*$")
_USER_ID_PATTERN = re.compile(r"^[A-Za-z0-9][A-Za-z0-9_-]*$")

_DRAFT_SYSTEM_PROMPT = """\
You author SKILL.md files for an AI agent platform. Produce ONLY the raw
markdown content of one SKILL.md file (no code fences, no commentary).

Required structure:
1. A top-level title heading (`# <skill-name>`) summarising the skill.
2. A "## When to use" section describing the trigger conditions.
3. A "## Steps" section with an ordered, actionable step list.

Keep it concise, concrete and directly executable by an agent.
"""


# ---------------------------------------------------------------------------
# Validation helpers
# ---------------------------------------------------------------------------


def _validate_skill_name(name: str) -> str:
    """Validate a skill name against the safe pattern (blocks path traversal).

    Args:
        name: Candidate skill name.

    Returns:
        The validated name.

    Raises:
        ValueError: If the name does not match ``^[a-z0-9][a-z0-9_-]*$``.
    """
    if not _NAME_PATTERN.match(name):
        raise ValueError(f"invalid skill name: {name!r}")
    return name


def _validate_user_id(user_id: str) -> str:
    """Validate a user id used in filesystem paths (blocks path traversal).

    Args:
        user_id: Candidate user identifier.

    Returns:
        The validated user id as string.

    Raises:
        ValueError: If the id does not match ``^[A-Za-z0-9][A-Za-z0-9_-]*$``.
    """
    uid = str(user_id)
    if not _USER_ID_PATTERN.match(uid):
        raise ValueError(f"invalid user id: {uid!r}")
    return uid


# ---------------------------------------------------------------------------
# Path helpers
# ---------------------------------------------------------------------------


def _data_root() -> Path:
    """Return the G2 workspace root: ``{DATA_ROOT}``."""
    return Path(settings.DATA_ROOT)


def _skills_root() -> Path:
    """Return the Global skills root: ``{DATA_ROOT}/global/skills`` (G2 v3)."""
    return _data_root() / "global" / "skills"


def _global_skill_dir(name: str) -> Path:
    """Return the global directory of a skill: ``{root}/global/skills/<name>``."""
    return _skills_root() / _validate_skill_name(name)


def _global_skill_file(name: str) -> Path:
    """Return the global SKILL.md path of a skill."""
    return _global_skill_dir(name) / _SKILL_FILE_NAME


def _shared_user_skill_dir(user_id: str) -> Path:
    """Return the top-level shared user dir: ``{DATA_ROOT}/users/<user_id>``."""
    return _data_root() / "users" / _validate_user_id(user_id)


def _shared_user_skill_file(user_id: str, name: str) -> Path:
    """Return the shared-user SKILL.md path: ``{DATA_ROOT}/users/<uid>/<name>/SKILL.md``."""
    return _shared_user_skill_dir(user_id) / _validate_skill_name(name) / _SKILL_FILE_NAME


# ---------------------------------------------------------------------------
# G2 three-layer path helpers (spec v3.3 §4.3)
# ---------------------------------------------------------------------------


def _agent_dir(app_id: int) -> Path:
    """Return the AgentApp private workspace root: ``{DATA_ROOT}/agents/<app_id>``."""
    return _data_root() / "agents" / str(app_id)


def _agent_skill_dir(app_id: int) -> Path:
    """Return the Agent-layer skills dir: ``{DATA_ROOT}/agents/<app_id>/skills``."""
    return _agent_dir(app_id) / "skills"


def _agent_skill_file(app_id: int, name: str) -> Path:
    """Return the Agent-layer SKILL.md path."""
    return _agent_skill_dir(app_id) / _validate_skill_name(name) / _SKILL_FILE_NAME


def _user_skill_dir(app_id: int, user_id: int) -> Path:
    """Return the User-layer skills dir: ``{DATA_ROOT}/agents/<app_id>/users/<user_id>/skills``."""
    return _agent_dir(app_id) / "users" / str(user_id) / "skills"


def _user_skill_file(app_id: int, user_id: int, name: str) -> Path:
    """Return the User-layer SKILL.md path."""
    return _user_skill_dir(app_id, user_id) / _validate_skill_name(name) / _SKILL_FILE_NAME


# ---------------------------------------------------------------------------
# Low-level utilities
# ---------------------------------------------------------------------------


def _sha256(content: str) -> str:
    """Return the sha256 hex digest of a UTF-8 encoded string."""
    return hashlib.sha256(content.encode("utf-8")).hexdigest()


def _atomic_write(path: Path, content: str) -> None:
    """Atomically write text content to ``path`` via temp file + os.replace.

    The temporary file is created in the target directory so the final
    ``os.replace`` is guaranteed to be atomic on the same filesystem.

    Args:
        path: Destination file path.
        content: Text content to write.
    """
    path.parent.mkdir(parents=True, exist_ok=True)
    fd, tmp_path = tempfile.mkstemp(dir=path.parent, prefix=f".{path.name}.", suffix=".tmp")
    try:
        with os.fdopen(fd, "w", encoding="utf-8") as handle:
            handle.write(content)
            handle.flush()
            os.fsync(handle.fileno())
        os.replace(tmp_path, path)
    except OSError:
        with contextlib.suppress(OSError):
            os.unlink(tmp_path)
        raise


def _remove_skill_dirs(name: str) -> None:
    """Remove a skill's global directory and every per-user copy directory.

    Args:
        name: Validated skill name.
    """
    global_dir = _global_skill_dir(name)
    if global_dir.is_dir():
        shutil.rmtree(global_dir)

    users_root = _data_root() / "users"
    if not users_root.is_dir():
        return
    for user_dir in users_root.iterdir():
        copy_dir = user_dir / name
        if user_dir.is_dir() and copy_dir.is_dir():
            shutil.rmtree(copy_dir)


def _prune_stale_user_skills(target_dir: Path, keep: set[str]) -> None:
    """Delete skill directories under ``target_dir`` that are not in ``keep``.

    G2 v3 (D7): the function takes the directory to prune directly (the
    User-layer combined dir or the top-level shared user dir) instead of
    deriving it from a user id.

    Args:
        target_dir: Skills directory to prune (e.g. ``_user_skill_dir(app_id, user_id)``).
        keep: Set of skill names that must survive.
    """
    if not target_dir.is_dir():
        return
    for entry in target_dir.iterdir():
        if entry.is_dir() and entry.name not in keep:
            shutil.rmtree(entry)


# ---------------------------------------------------------------------------
# Public async API
# ---------------------------------------------------------------------------


async def create_global(
    session: Session, *, name: str, description: str, body: str, created_by: str | None = None
) -> SkillAsset:
    """Create a global skill: atomic file write, sha256 hash, DB insert.

    Args:
        session: SQLModel DB session.
        name: Globally unique skill name.
        description: Human-readable description stored in the DB row.
        body: Full SKILL.md markdown content.
        created_by: Optional audit-only creator identifier.

    Returns:
        The inserted SkillAsset row.

    Raises:
        ValueError: If the name is invalid or the skill already exists.
    """
    _validate_skill_name(name)
    if session.get(SkillAsset, name) is not None:
        raise ValueError(f"skill {name!r} already exists")

    # DB-first ordering: the metadata row is committed before any disk write,
    # so a uniqueness conflict (concurrent create of the same name) can never
    # leave an orphaned SKILL.md behind a lost insert race. The full body is
    # stored in the row (dual-store source of truth); the disk file is the
    # runtime copy consumed by FilesystemBackend.
    asset = SkillAsset(
        name=name,
        description=description,
        body=body,
        content_hash=_sha256(body),
        created_by=created_by,
    )
    session.add(asset)
    try:
        session.commit()
    except IntegrityError:
        session.rollback()
        raise ValueError(f"skill {name!r} already exists") from None

    try:
        await asyncio.to_thread(_atomic_write, _global_skill_file(name), body)
    except OSError:
        # Compensate the orphaned row so DB and disk stay consistent.
        session.delete(asset)
        session.commit()
        raise
    logger.info("global_skill_created", name=name, created_by=created_by)
    return asset


async def update_global(
    session: Session, *, name: str, description: str | None = None, body: str | None = None
) -> SkillAsset:
    """Update a global skill: rewrite the file, refresh the hash, bump version.

    Args:
        session: SQLModel DB session.
        name: Skill name to update.
        description: Optional new description.
        body: Optional new SKILL.md content (file is rewritten when provided).

    Returns:
        The updated SkillAsset row.

    Raises:
        ValueError: If the skill does not exist or nothing is provided.
    """
    _validate_skill_name(name)
    asset = session.get(SkillAsset, name)
    if asset is None:
        raise ValueError(f"skill {name!r} not found")
    if description is None and body is None:
        raise ValueError("nothing to update: provide description and/or body")

    # DB-first ordering (mirrors create_global): the new hash/version/body is
    # committed before the file rewrite; on disk failure the row is reverted
    # so ``content_hash``/``body`` never drift from the on-disk content.
    previous_hash = asset.content_hash
    previous_version = asset.version
    previous_description = asset.description
    previous_body = asset.body
    if body is not None:
        asset.content_hash = _sha256(body)
        asset.body = body
    if description is not None:
        asset.description = description
    asset.version += 1
    session.add(asset)
    session.commit()

    if body is not None:
        try:
            await asyncio.to_thread(_atomic_write, _global_skill_file(name), body)
        except OSError:
            asset.content_hash = previous_hash
            asset.version = previous_version
            asset.description = previous_description
            asset.body = previous_body
            session.add(asset)
            session.commit()
            raise
    logger.info("global_skill_updated", name=name, version=asset.version)
    return asset


async def delete_global(session: Session, *, name: str) -> None:
    """Delete a global skill, cascade-delete all user copies and the DB row.

    Args:
        session: SQLModel DB session.
        name: Skill name to delete.

    Raises:
        ValueError: If the skill does not exist in the DB.
    """
    _validate_skill_name(name)
    asset = session.get(SkillAsset, name)
    if asset is None:
        raise ValueError(f"skill {name!r} not found")

    await asyncio.to_thread(_remove_skill_dirs, name)
    session.delete(asset)
    session.commit()
    logger.info("global_skill_deleted", name=name)


def _global_skill_metadata(asset: SkillAsset) -> dict[str, Any]:
    """Project a skill row into its list-view metadata dict."""
    return {
        "name": asset.name,
        "description": asset.description,
        "content_hash": asset.content_hash,
        "version": asset.version,
        "created_by": asset.created_by,
    }


async def list_global(session: Session) -> list[dict[str, Any]]:
    """List metadata of all global skills.

    Args:
        session: SQLModel DB session.

    Returns:
        List of dicts with name/description/content_hash/version/created_by.
    """
    assets = session.exec(select(SkillAsset)).all()
    return [_global_skill_metadata(asset) for asset in assets]


async def list_global_page(
    session: Session,
    *,
    page: int = 1,
    page_size: int = 10,
    keyword: str | None = None,
) -> PageResult[dict[str, Any]]:
    """List metadata of global skills with server-side pagination.

    Args:
        session: SQLModel DB session.
        page: 1-based page number (bounds validated by the API layer).
        page_size: Rows per page (bounds validated by the API layer).
        keyword: Optional case-insensitive substring matched against name.

    Returns:
        PageResult carrying metadata dicts of the requested page, the
        filtered total and the echoed page/pageSize values.
    """
    assets = list(session.exec(select(SkillAsset)).all())
    if keyword:
        needle = keyword.lower()
        assets = [asset for asset in assets if needle in asset.name.lower()]
    total = len(assets)
    start = (page - 1) * page_size
    return PageResult(
        items=[_global_skill_metadata(asset) for asset in assets[start : start + page_size]],
        total=total,
        page=page,
        page_size=page_size,
    )


async def read_global(session: Session, name: str) -> str:
    """Read the raw SKILL.md body of a global skill (disk-first, DB self-heal).

    Dual-store read path: the disk file is the fast path; when it is missing
    (e.g. lost on a container rebuild) the body is recovered from the DB row
    and the disk file is rewritten so subsequent reads hit the fast path
    again. Only when both copies are gone (or the row predates dual-store
    with ``body IS NULL``) is the skill genuinely not found.

    Args:
        session: SQLModel DB session (self-heal fallback source).
        name: Skill name.

    Returns:
        The markdown body.

    Raises:
        ValueError: If the name is invalid or the skill exists neither on
            disk nor as a DB row with a body.
    """
    path = _global_skill_file(name)
    if path.exists():
        return await asyncio.to_thread(path.read_text, "utf-8")

    asset = session.get(SkillAsset, name)
    if asset is not None and asset.body is not None:
        await asyncio.to_thread(_atomic_write, path, asset.body)
        logger.warning("skill_disk_selfhealed_from_db", name=name)
        return asset.body
    raise ValueError(f"skill {name!r} not found")


async def materialize_for_user(session: Session, user_id: str, skill_names: Sequence[str]) -> None:
    """Copy the given global skills into a user's skill directory.

    Existing user copies are overwritten with the fresh global content.

    Args:
        session: SQLModel DB session (passed through to ``read_global``).
        user_id: Target user identifier.
        skill_names: Names of global skills to copy.

    Raises:
        ValueError: If the user id is invalid or a skill does not exist.
    """
    uid = _validate_user_id(user_id)
    names = list(skill_names)
    for name in names:
        body = await read_global(session, name)
        await asyncio.to_thread(_atomic_write, _shared_user_skill_file(uid, name), body)
    logger.info("skills_materialized_for_user", user_id=uid, skill_count=len(names))


async def materialize_into_directory(
    session: Session, target_dir: Path, skill_names: Sequence[str]
) -> None:
    """Copy the given global skills into ``<target_dir>/<name>/SKILL.md``.

    Unlike :func:`materialize_for_user`, the destination directory is supplied
    by the caller (typically a ``tmp_path`` fixture in tests). The layout
    matches what ``FilesystemBackend`` expects, so a compiled standalone
    sub-agent graph can read the skills with no further setup.

    Existing files are overwritten. The target directory and skill
    sub-directories are created on demand.

    Args:
        session: SQLModel DB session (passed through to ``read_global``).
        target_dir: Destination root directory; created when missing.
        skill_names: Names of global skills to copy.

    Raises:
        ValueError: If any skill name is invalid or the skill exists neither
            on disk nor in the DB (dual-store miss).
    """
    for name in skill_names:
        _validate_skill_name(name)
        body = await read_global(session, name)
        await asyncio.to_thread(_atomic_write, target_dir / name / _SKILL_FILE_NAME, body)
    logger.info(
        "skills_materialized_into_directory",
        target_dir=str(target_dir),
        skill_count=len(list(skill_names)),
    )


# ---------------------------------------------------------------------------
# G2 copy + hash utilities (spec v3.3 §4.3)
# ---------------------------------------------------------------------------


async def _hash_compare_or_write(target: Path, body: str) -> bool:
    """Hash-compare and write only on mismatch (spec v3.3 §4.3).

    Args:
        target: Destination SKILL.md path.
        body: Fresh content to compare against / write.

    Returns:
        True when the file was (re)written; False when the existing copy
        already matches and was left untouched.
    """
    new_hash = hashlib.sha256(body.encode("utf-8")).hexdigest()
    if target.exists():
        existing_hash = hashlib.sha256(await asyncio.to_thread(target.read_bytes)).hexdigest()
        if existing_hash == new_hash:
            return False
    target.parent.mkdir(parents=True, exist_ok=True)
    await asyncio.to_thread(_atomic_write, target, body)
    return True


async def materialize_for_agent(
    session: Session, *, app_id: int, skill_names: Sequence[str]
) -> None:
    """Copy the given global skills into the Agent layer (publish time).

    Idempotent: an Agent-layer copy whose sha256 already matches the fresh
    Global body is left untouched (hash-compare write, spec §4.1).

    Args:
        session: SQLModel DB session (passed through to ``read_global``).
        app_id: Target AgentApp id.
        skill_names: Names of global skills to snapshot.

    Raises:
        ValueError: If any skill name is invalid or the skill exists neither
            on disk nor in the DB (dual-store miss).
    """
    target_dir = _agent_skill_dir(app_id)
    target_dir.mkdir(parents=True, exist_ok=True)
    written = 0
    for name in skill_names:
        _validate_skill_name(name)
        body = await read_global(session, name)
        target = target_dir / name / _SKILL_FILE_NAME
        if await _hash_compare_or_write(target, body):
            written += 1
    logger.info(
        "agent_skills_materialized",
        app_id=app_id,
        skill_count=len(list(skill_names)),
        files_written=written,
    )


def compute_workspace_hash(agent_skill_dir: Path) -> str:
    """Compute the content fingerprint of an Agent-layer skills directory.

    Only the one-level ``<name>/SKILL.md`` files are hashed (non-recursive
    glob) so the nested ``users/`` workspace never leaks into the
    Agent-layer fingerprint.

    Args:
        agent_skill_dir: Must be ``_agent_skill_dir(app_id)`` — NOT
            ``_agent_dir(app_id)`` (which would include the users/ subtree).

    Returns:
        sha256 hex of the sorted per-file sha256 digests joined by newlines.
    """
    if not agent_skill_dir.is_dir():
        return hashlib.sha256(b"").hexdigest()
    file_hashes: list[str] = []
    for path in sorted(agent_skill_dir.glob(f"*/{_SKILL_FILE_NAME}")):
        file_hashes.append(hashlib.sha256(path.read_bytes()).hexdigest())
    return hashlib.sha256("\n".join(file_hashes).encode("utf-8")).hexdigest()


def _compute_user_workspace_hash(user_dir: Path) -> str:
    """Compute the User-layer content fingerprint (lazy validation input).

    Uses rglob so every SKILL.md at any depth below ``user_dir`` counts.
    """
    if not user_dir.is_dir():
        return hashlib.sha256(b"").hexdigest()
    file_hashes: list[str] = []
    for path in sorted(user_dir.rglob(_SKILL_FILE_NAME)):
        file_hashes.append(hashlib.sha256(path.read_bytes()).hexdigest())
    return hashlib.sha256("\n".join(file_hashes).encode("utf-8")).hexdigest()


def _compute_effective_workspace_hash(app_id: int, effective_skill_names: Sequence[str]) -> str:
    """Compute the expected User-layer fingerprint (v3.3 dynamic algorithm).

    Source resolution mirrors :func:`materialize_to_user_combined` exactly
    (Agent layer first, Global fallback); a name with no source anywhere
    contributes an empty slot so positions stay stable and a missing skill
    remains distinguishable. This is why the lazy check cannot simply compare
    against ``AgentApp.workspace_hash``: the effective set is the union of
    App + SubAgent skills and can be larger than the Agent-layer snapshot.
    """
    file_hashes: list[str] = []
    for name in sorted(effective_skill_names):
        _validate_skill_name(name)
        agent_path = _agent_skill_file(app_id, name)
        source = agent_path if agent_path.exists() else _global_skill_file(name)
        if source.exists():
            file_hashes.append(hashlib.sha256(source.read_bytes()).hexdigest())
        else:
            file_hashes.append("")
    return hashlib.sha256("\n".join(file_hashes).encode("utf-8")).hexdigest()


async def materialize_to_user_combined(
    session: Session,
    *,
    app_cfg: AgentApp,
    user_id: int,
    subagent_cfgs: Sequence[SubAgentConfig],
) -> None:
    """Aggregate (Global + Agent) into the per-(app, user) workspace (v3).

    Merge semantics: the effective set is the union of the App's and every
    SubAgent's ``skill_names`` (deduped); each name resolves Agent-layer
    first with a Global fallback; copies are hash-compared so unchanged
    files are left untouched; directories outside the effective set are
    pruned.

    Args:
        session: SQLModel DB session (kept in the v3 signature for parity).
        app_cfg: AgentApp configuration (id drives the workspace path).
        user_id: Target user id (from the API layer's authenticated user).
        subagent_cfgs: SubAgentConfig rows bound to the app.
    """
    effective_skill_names = sorted(
        set(app_cfg.skill_names or [])
        | {n for cfg in subagent_cfgs for n in (cfg.skill_names or [])}
    )
    target_dir = _user_skill_dir(app_cfg.id, user_id)
    target_dir.mkdir(parents=True, exist_ok=True)

    written = 0
    for name in effective_skill_names:
        agent_path = _agent_skill_file(app_cfg.id, name)
        source = agent_path if agent_path.exists() else _global_skill_file(name)
        if source.exists():
            body = await asyncio.to_thread(source.read_text, "utf-8")
            target = target_dir / name / _SKILL_FILE_NAME
            if await _hash_compare_or_write(target, body):
                written += 1
        else:
            logger.warning(
                "user_materialize_source_missing",
                source=str(source),
                app_id=app_cfg.id,
                user_id=user_id,
            )

    # Prune skill dirs that are no longer part of the effective set.
    await asyncio.to_thread(_prune_stale_user_skills, target_dir, set(effective_skill_names))

    logger.info(
        "user_workspace_materialized_combined",
        user_id=user_id,
        app_id=app_cfg.id,
        skill_count=len(effective_skill_names),
        files_written=written,
    )


async def materialize_into_combined_directory(
    session: Session,
    target_dir: Path,
    *,
    app_id: int,
    skill_names: Sequence[str],
) -> None:
    """Aggregate (Global + Agent) into a caller-supplied directory.

    Standalone test_runner helper (spec §4.3): resolves each name
    Agent-layer first with a Global fallback and writes into
    ``<target_dir>/<name>/SKILL.md`` (FilesystemBackend layout). MVP keeps
    this Global-only in practice — real combined usage arrives with G3+.

    Args:
        session: SQLModel DB session (kept in the v3 signature for parity).
        target_dir: Destination root; created when missing.
        app_id: AgentApp id whose Agent layer is consulted first.
        skill_names: Skill names to materialize.
    """
    target_dir.mkdir(parents=True, exist_ok=True)
    for name in skill_names:
        _validate_skill_name(name)
        agent_path = _agent_skill_file(app_id, name)
        source = agent_path if agent_path.exists() else _global_skill_file(name)
        if source.exists():
            body = await asyncio.to_thread(source.read_text, "utf-8")
            await asyncio.to_thread(_atomic_write, target_dir / name / _SKILL_FILE_NAME, body)


async def sync_user_skills(session: Session, user_id: str, associated_names: Sequence[str]) -> None:
    """Reassemble a user's skill directory to match the association set.

    Semantics: every associated skill is re-copied from global (freshness
    guarantee) and any leftover skill directory outside the set is deleted.
    Success/failure is counted in ``skill_sync_total{result}``.

    Args:
        session: SQLModel DB session (passed through to ``read_global``).
        user_id: Target user identifier.
        associated_names: The full desired set of skill names.

    Raises:
        ValueError: If the user id or any skill name is invalid.
        OSError: If filesystem operations fail.
    """
    uid = _validate_user_id(user_id)
    names = list(associated_names)
    try:
        await materialize_for_user(session, uid, names)
        await asyncio.to_thread(
            _prune_stale_user_skills, _shared_user_skill_dir(uid), set(names)
        )
    except (OSError, ValueError):
        skill_sync_total.labels(result="error").inc()
        logger.exception("user_skill_sync_failed", user_id=uid, skill_count=len(names))
        raise
    skill_sync_total.labels(result="success").inc()
    logger.info("user_skill_sync_succeeded", user_id=uid, skill_count=len(names))


async def refresh_disk_from_db(session: Session, name: str | None = None) -> list[dict[str, str]]:
    """Refresh the disk SKILL.md copies from the DB bodies (DB is truth).

    ``content_hash`` is the rewrite trigger: a disk file whose sha256 equals
    the row's ``content_hash`` is left untouched (``unchanged``); anything
    else (missing or drifted) is rewritten from the DB body
    (``rewritten``). Legacy rows with ``body IS NULL`` (created before
    dual-store) are backfilled from their disk file — the only surviving
    copy — and their ``content_hash`` is resynced to it (``backfilled``);
    when both copies are gone the entry is reported ``missing``.

    Args:
        session: SQLModel DB session.
        name: Refresh only this skill; ``None`` refreshes every row.

    Returns:
        Per-skill report entries ``{"name": ..., "action": ...}`` with action
        one of ``rewritten`` / ``unchanged`` / ``backfilled`` / ``missing``.

    Raises:
        ValueError: When ``name`` is given but no DB row exists for it.
    """
    if name is not None:
        _validate_skill_name(name)
        asset = session.get(SkillAsset, name)
        if asset is None:
            raise ValueError(f"skill {name!r} not found")
        assets = [asset]
    else:
        assets = list(session.exec(select(SkillAsset)).all())

    report: list[dict[str, str]] = []
    for asset in assets:
        path = _global_skill_file(asset.name)
        if asset.body is None:
            # Legacy pre-dual-store row: the disk file is the only copy.
            if path.exists():
                disk_body = await asyncio.to_thread(path.read_text, "utf-8")
                resynced_hash = _sha256(disk_body)
                if resynced_hash != asset.content_hash:
                    logger.warning(
                        "skill_legacy_hash_resynced_from_disk",
                        name=asset.name,
                        old_hash=asset.content_hash,
                    )
                    asset.content_hash = resynced_hash
                asset.body = disk_body
                session.add(asset)
                session.commit()
                report.append({"name": asset.name, "action": "backfilled"})
            else:
                logger.error("skill_unrecoverable_body_and_disk_lost", name=asset.name)
                report.append({"name": asset.name, "action": "missing"})
            continue

        disk_hash: str | None = None
        if path.exists():
            disk_body = await asyncio.to_thread(path.read_text, "utf-8")
            disk_hash = _sha256(disk_body)
        if disk_hash == asset.content_hash:
            report.append({"name": asset.name, "action": "unchanged"})
        else:
            await asyncio.to_thread(_atomic_write, path, asset.body)
            logger.info("skill_disk_refreshed_from_db", name=asset.name)
            report.append({"name": asset.name, "action": "rewritten"})

    logger.info(
        "skills_disk_refresh_completed",
        total=len(report),
        rewritten=sum(1 for entry in report if entry["action"] == "rewritten"),
        unchanged=sum(1 for entry in report if entry["action"] == "unchanged"),
        backfilled=sum(1 for entry in report if entry["action"] == "backfilled"),
        missing=sum(1 for entry in report if entry["action"] == "missing"),
    )
    return report


async def generate_skill_draft(*, description: str, hint: str) -> str:
    """Generate a SKILL.md draft via the LLM (draft only — no disk/DB writes).

    Retries are intentionally not layered here: ``llm_service.call`` already
    applies per-model retries, model fallback and a total timeout budget, so
    an outer retry would only multiply the request count on deterministic
    failures.

    Args:
        description: What the skill should do.
        hint: Extra authoring guidance for the draft.

    Returns:
        The drafted markdown string.

    Raises:
        RuntimeError: If the LLM fails after retries and model fallback.
    """
    messages = [
        SystemMessage(content=_DRAFT_SYSTEM_PROMPT),
        HumanMessage(content=f"Skill description: {description}\nExtra hint: {hint}"),
    ]
    call_kwargs: dict[str, Any] = {"temperature": 0.4}
    if settings.LANGFUSE_TRACING_ENABLED:
        call_kwargs["callbacks"] = [langfuse_callback_handler]

    result = await llm_service.call(messages, **call_kwargs)
    content = result.content
    draft = content if isinstance(content, str) else str(content)
    logger.info("skill_draft_generated", description=description)
    return draft
