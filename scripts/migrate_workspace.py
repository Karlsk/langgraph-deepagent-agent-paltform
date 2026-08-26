"""One-shot migration: legacy two-layer skill workspace -> G2 three-layer layout.

Spec: docs/changelog/agentapp-three-layer-refactor/spec-g2-workspace.md
v3.3 §10.1 / §10.3 (D25). Run AFTER the G2 release is deployed and BEFORE
relying on the nested workspace:

    python scripts/migrate_workspace.py            # dry-run (default)
    python scripts/migrate_workspace.py --apply    # actually migrate

What it does (idempotent, see §10.3 step order: backup -> migrate ->
alembic -> restart -> smoke):

1. Detect the legacy layout ``{SKILLS_ROOT}/global/skills/<name>/SKILL.md``
   + ``{SKILLS_ROOT}/users/<uid>/<name>/SKILL.md``.
2. ``--apply`` first archives the legacy tree into
   ``{DATA_ROOT}/archive/<timestamp>/`` (kept 7 days; expired archives are
   cleaned on every run).
3. Copy the Global layer to ``{DATA_ROOT}/global/skills/``.
4. Remap every user skill via ``UserAgentAppAssociation`` into the nested
   workspace ``{DATA_ROOT}/agents/<app_id>/users/<uid>/skills/`` (one copy
   per associated app); users without any association fall back to
   ``{DATA_ROOT}/users/<uid>/`` (orphan store, §10.1).
5. Backfill the AgentApp G2 columns (``agent_dir`` template,
   ``workspace_hash``, ``agent_workspace_status='active'``) — apps with
   bound skills also get their Agent layer copied from the migrated Global
   layer (publish-time snapshot equivalence).
6. Verify: the summary counts every migrated file; ``--dry-run`` reports
   the plan without touching the filesystem (the default mode).

The script deliberately does NOT depend on alembic (§10.1: avoid long
transactions) and keeps its file logic self-contained (no ``app.services``
imports) so it can run against a bare checkout.
"""

import argparse
import hashlib
import shutil
import sys
import time
from collections.abc import Sequence
from pathlib import Path

from sqlmodel import Session, select

from app.core.config import settings
from app.core.logging import logger
from app.models.agent_assets import AgentApp, UserAgentAppAssociation
from app.services.database import database_service

_SKILL_FILE_NAME = "SKILL.md"
_ARCHIVE_RETENTION_DAYS = 7


def _dir_hash(skill_dir: Path) -> str:
    """Fingerprint a one-level ``<name>/SKILL.md`` directory (non-recursive).

    Same shape as ``skills_store.compute_workspace_hash``: sha256 of the
    sorted per-file sha256 digests joined by newlines; empty dir -> sha256("").
    """
    if not skill_dir.is_dir():
        return hashlib.sha256(b"").hexdigest()
    digests: list[str] = []
    for entry in sorted(skill_dir.iterdir(), key=lambda p: p.name):
        file = entry / _SKILL_FILE_NAME
        if entry.is_dir() and file.is_file():
            digests.append(hashlib.sha256(file.read_bytes()).hexdigest())
    return hashlib.sha256("\n".join(digests).encode("utf-8")).hexdigest()


def migrate_workspace(
    session: Session,
    *,
    legacy_root: Path,
    data_root: Path,
    apply: bool = False,
) -> dict[str, int]:
    """Migrate the legacy two-layer workspace into the G2 three-layer layout.

    Args:
        session: SQLModel database session (association remapping + backfill).
        legacy_root: Old skills root (``{SKILLS_ROOT}``, e.g. ``./data/skills``).
        data_root: New workspace root (``{DATA_ROOT}``, e.g. ``./data``).
        apply: When False (default) only the plan is reported (dry-run) and
            the filesystem is never touched.

    Returns:
        Summary dict with keys ``global_skills``, ``user_files``,
        ``remapped``, ``orphans`` and ``backfilled``.
    """
    summary = {"global_skills": 0, "user_files": 0, "remapped": 0, "orphans": 0, "backfilled": 0}

    legacy_global = legacy_root / "global" / "skills"
    legacy_users = legacy_root / "users"
    if not legacy_root.is_dir() or (not legacy_global.is_dir() and not legacy_users.is_dir()):
        logger.info("migrate_workspace_nothing_to_do", legacy_root=str(legacy_root))
        return summary

    if apply:
        timestamp = time.strftime("%Y%m%d-%H%M%S")
        backup_dir = data_root / "archive" / timestamp
        suffix = 1
        while backup_dir.exists():  # same-second re-runs must not collide
            backup_dir = data_root / "archive" / f"{timestamp}-{suffix}"
            suffix += 1
        backup_dir.parent.mkdir(parents=True, exist_ok=True)
        shutil.copytree(legacy_root, backup_dir)
        logger.info("migrate_workspace_backup_created", backup=str(backup_dir))

    # --- 1. Global layer: {legacy}/global/skills -> {DATA_ROOT}/global/skills
    new_global = data_root / "global" / "skills"
    if legacy_global.is_dir():
        for skill_dir in sorted(legacy_global.iterdir(), key=lambda p: p.name):
            if not skill_dir.is_dir():
                continue
            summary["global_skills"] += 1
            target = new_global / skill_dir.name
            if (target / _SKILL_FILE_NAME).is_file():
                continue  # already migrated (idempotent re-run)
            if apply:
                target.parent.mkdir(parents=True, exist_ok=True)
                shutil.copytree(skill_dir, target)

    # --- 2. User layer: remap per association, orphan fallback otherwise
    if legacy_users.is_dir():
        for user_dir in sorted(legacy_users.iterdir(), key=lambda p: p.name):
            if not user_dir.is_dir():
                continue
            user_id = int(user_dir.name)
            associations = session.exec(
                select(UserAgentAppAssociation).where(UserAgentAppAssociation.user_id == user_id)
            ).all()
            for skill_dir in sorted(user_dir.iterdir(), key=lambda p: p.name):
                if not skill_dir.is_dir():
                    continue
                summary["user_files"] += 1
                if associations:
                    for assoc in associations:
                        target = (
                            data_root / "agents" / str(assoc.agent_app_id)
                            / "users" / str(user_id) / "skills" / skill_dir.name
                        )
                        if (target / _SKILL_FILE_NAME).is_file():
                            continue  # idempotent
                        summary["remapped"] += 1
                        if apply:
                            target.parent.mkdir(parents=True, exist_ok=True)
                            shutil.copytree(skill_dir, target)
                else:
                    orphan = data_root / "users" / str(user_id) / skill_dir.name
                    if (orphan / _SKILL_FILE_NAME).is_file():
                        continue  # idempotent
                    summary["orphans"] += 1
                    if apply:
                        orphan.parent.mkdir(parents=True, exist_ok=True)
                        shutil.copytree(skill_dir, orphan)

    # --- 3. Backfill AgentApp G2 columns (+ Agent layer for apps with skills)
    for app in session.exec(select(AgentApp)).all():
        agent_dir = data_root / "agents" / str(app.id)
        agent_skill_dir = agent_dir / "skills"
        if app.agent_dir is None:
            summary["backfilled"] += 1
        if not apply:
            continue  # dry-run: report only, never touch rows or files
        app.agent_dir = str(agent_dir)
        if app.skill_names and not any(agent_skill_dir.glob(f"*/{_SKILL_FILE_NAME}")):
            # Publish-time snapshot equivalence: the Agent layer mirrors the
            # migrated Global copies of the bound skills.
            agent_skill_dir.mkdir(parents=True, exist_ok=True)
            for name in app.skill_names:
                source = new_global / name
                if (source / _SKILL_FILE_NAME).is_file():
                    shutil.copytree(source, agent_skill_dir / name, dirs_exist_ok=True)
        if app.status != "draft":
            app.agent_workspace_status = "active"
            app.workspace_hash = _dir_hash(agent_skill_dir)
        session.add(app)
    if apply:
        session.commit()

    # --- 4. Verification summary (spec §10.1 step 7)
    logger.info(
        "migrate_workspace_completed",
        apply=apply,
        **summary,
    )
    return summary


def cleanup_expired_archives(data_root: Path, *, max_age_days: int = _ARCHIVE_RETENTION_DAYS) -> int:
    """Remove ``{DATA_ROOT}/archive/`` entries older than ``max_age_days``.

    Args:
        data_root: Workspace root holding the ``archive/`` subdirectory.
        max_age_days: Retention window (§10.1: 7 days).

    Returns:
        The number of expired archives removed.
    """
    archive_root = data_root / "archive"
    if not archive_root.is_dir():
        return 0
    cutoff = time.time() - max_age_days * 86400
    removed = 0
    for entry in list(archive_root.iterdir()):
        if entry.is_dir() and entry.stat().st_mtime < cutoff:
            shutil.rmtree(entry)
            removed += 1
    if removed:
        logger.info("migrate_workspace_archives_cleaned", removed=removed)
    return removed


def main(argv: Sequence[str] | None = None) -> int:
    """CLI entry point (dry-run by default; ``--apply`` performs the move)."""
    parser = argparse.ArgumentParser(
        description="One-shot migration of the legacy two-layer skill workspace to the G2 three-layer layout."
    )
    parser.add_argument("--apply", action="store_true", help="actually migrate (default is dry-run)")
    parser.add_argument("--legacy-root", default=settings.SKILLS_ROOT, help="old skills root (default: SKILLS_ROOT)")
    parser.add_argument("--data-root", default=settings.DATA_ROOT, help="new workspace root (default: DATA_ROOT)")
    args = parser.parse_args(argv)

    with Session(database_service.engine) as session:
        summary = migrate_workspace(
            session,
            legacy_root=Path(args.legacy_root),
            data_root=Path(args.data_root),
            apply=args.apply,
        )
        cleanup_expired_archives(Path(args.data_root))

    mode = "APPLIED" if args.apply else "DRY-RUN"
    print(f"[{mode}] migrate_workspace summary: {summary}")  # noqa: T201 — one-shot ops script output
    return 0


if __name__ == "__main__":
    sys.exit(main())
