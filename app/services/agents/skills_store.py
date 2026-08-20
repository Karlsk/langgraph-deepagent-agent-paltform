"""Skill file service: manages SKILL.md assets on disk plus their DB metadata.

Directory conventions (rooted at ``settings.SKILLS_ROOT``):

- Global skills: ``{SKILLS_ROOT}/global/<name>/SKILL.md``
- Per-user copies: ``{SKILLS_ROOT}/users/<user_id>/<name>/SKILL.md``

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
from app.models.agent_assets import SkillAsset
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


def _skills_root() -> Path:
    """Return the configured skills storage root."""
    return Path(settings.SKILLS_ROOT)


def _global_skill_dir(name: str) -> Path:
    """Return the global directory of a skill: ``{root}/global/<name>``."""
    return _skills_root() / "global" / _validate_skill_name(name)


def _global_skill_file(name: str) -> Path:
    """Return the global SKILL.md path of a skill."""
    return _global_skill_dir(name) / _SKILL_FILE_NAME


def _user_skill_file(user_id: str, name: str) -> Path:
    """Return the per-user SKILL.md path: ``{root}/users/<user_id>/<name>/SKILL.md``."""
    return _skills_root() / "users" / _validate_user_id(user_id) / _validate_skill_name(name) / _SKILL_FILE_NAME


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

    users_root = _skills_root() / "users"
    if not users_root.is_dir():
        return
    for user_dir in users_root.iterdir():
        copy_dir = user_dir / name
        if user_dir.is_dir() and copy_dir.is_dir():
            shutil.rmtree(copy_dir)


def _prune_stale_user_skills(user_id: str, keep: set[str]) -> None:
    """Delete skill directories under a user root that are not in ``keep``.

    Args:
        user_id: Validated user identifier.
        keep: Set of skill names that must survive.
    """
    user_root = _skills_root() / "users" / user_id
    if not user_root.is_dir():
        return
    for entry in user_root.iterdir():
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
    # leave an orphaned SKILL.md behind a lost insert race.
    asset = SkillAsset(name=name, description=description, content_hash=_sha256(body), created_by=created_by)
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

    # DB-first ordering (mirrors create_global): the new hash/version is
    # committed before the file rewrite; on disk failure the row is reverted
    # so ``content_hash`` never drifts from the on-disk content.
    previous_hash = asset.content_hash
    previous_version = asset.version
    previous_description = asset.description
    if body is not None:
        asset.content_hash = _sha256(body)
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


async def read_global(name: str) -> str:
    """Read the raw SKILL.md body of a global skill.

    Args:
        name: Skill name.

    Returns:
        The markdown body.

    Raises:
        ValueError: If the name is invalid or the skill file does not exist.
    """
    path = _global_skill_file(name)
    if not path.exists():
        raise ValueError(f"skill {name!r} not found")
    return await asyncio.to_thread(path.read_text, "utf-8")


async def materialize_for_user(user_id: str, skill_names: Sequence[str]) -> None:
    """Copy the given global skills into a user's skill directory.

    Existing user copies are overwritten with the fresh global content.

    Args:
        user_id: Target user identifier.
        skill_names: Names of global skills to copy.

    Raises:
        ValueError: If the user id is invalid or a skill does not exist.
    """
    uid = _validate_user_id(user_id)
    names = list(skill_names)
    for name in names:
        body = await read_global(name)
        await asyncio.to_thread(_atomic_write, _user_skill_file(uid, name), body)
    logger.info("skills_materialized_for_user", user_id=uid, skill_count=len(names))


async def materialize_into_directory(target_dir: Path, skill_names: Sequence[str]) -> None:
    """Copy the given global skills into ``<target_dir>/<name>/SKILL.md``.

    Unlike :func:`materialize_for_user`, the destination directory is supplied
    by the caller (typically a ``tmp_path`` fixture in tests). The layout
    matches what ``FilesystemBackend`` expects, so a compiled standalone
    sub-agent graph can read the skills with no further setup.

    Existing files are overwritten. The target directory and skill
    sub-directories are created on demand.

    Args:
        target_dir: Destination root directory; created when missing.
        skill_names: Names of global skills to copy.

    Raises:
        ValueError: If any skill name is invalid or the global file is
            missing.
    """
    for name in skill_names:
        _validate_skill_name(name)
        body = await read_global(name)
        await asyncio.to_thread(_atomic_write, target_dir / name / _SKILL_FILE_NAME, body)
    logger.info(
        "skills_materialized_into_directory",
        target_dir=str(target_dir),
        skill_count=len(list(skill_names)),
    )


async def sync_user_skills(user_id: str, associated_names: Sequence[str]) -> None:
    """Reassemble a user's skill directory to match the association set.

    Semantics: every associated skill is re-copied from global (freshness
    guarantee) and any leftover skill directory outside the set is deleted.
    Success/failure is counted in ``skill_sync_total{result}``.

    Args:
        user_id: Target user identifier.
        associated_names: The full desired set of skill names.

    Raises:
        ValueError: If the user id or any skill name is invalid.
        OSError: If filesystem operations fail.
    """
    uid = _validate_user_id(user_id)
    names = list(associated_names)
    try:
        await materialize_for_user(uid, names)
        await asyncio.to_thread(_prune_stale_user_skills, uid, set(names))
    except (OSError, ValueError):
        skill_sync_total.labels(result="error").inc()
        logger.exception("user_skill_sync_failed", user_id=uid, skill_count=len(names))
        raise
    skill_sync_total.labels(result="success").inc()
    logger.info("user_skill_sync_succeeded", user_id=uid, skill_count=len(names))


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
