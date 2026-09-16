"""Dual-store persistence for workflow definitions (DB + YAML disk).

DB is the source of truth; disk is the runtime copy consumed by the engine.
Every save writes both stores; startup sync restores disk from DB when the
file is missing or stale (content_hash mismatch).

The YAML disk layer uses atomic write (tmp + os.replace) with filename
whitelist validation (path traversal prevention) and yaml.safe_dump (S16).
"""

from __future__ import annotations

import hashlib
import os
import re
import tempfile
from pathlib import Path
from typing import TYPE_CHECKING

import structlog
import yaml
from sqlmodel import Session, select

from app.models.workflow_definition import (
    WorkflowDefinitionAsset,
    WorkflowEdgeAsset,
    WorkflowNodeAsset,
)
from app.workflow.models import (
    EdgeDefinition,
    NodeDefinition,
    WorkflowDefinition,
)

if TYPE_CHECKING:
    pass

logger = structlog.get_logger(__name__)

_WORKFLOW_ID_RE = re.compile(r"^[A-Za-z0-9_-]{1,64}$")


def _validate_workflow_id(workflow_id: str) -> None:
    """Validate workflow_id against whitelist (path traversal guard)."""
    if not _WORKFLOW_ID_RE.match(workflow_id):
        msg = f"invalid workflow_id: {workflow_id!r} (must match {_WORKFLOW_ID_RE.pattern})"
        raise ValueError(msg)


def user_workflow_dir() -> Path:
    """Return the user workflow directory: app/workflow/config/user/."""
    return Path(__file__).parent / "config" / "user"


# ---------------------------------------------------------------------------
# Low-level utilities
# ---------------------------------------------------------------------------


def _sha256(content: str) -> str:
    """Return the sha256 hex digest of a UTF-8 encoded string."""
    return hashlib.sha256(content.encode("utf-8")).hexdigest()


def _atomic_write(path: Path, content: str) -> None:
    """Atomically write text content to ``path`` via temp file + os.replace."""
    path.parent.mkdir(parents=True, exist_ok=True)
    fd, tmp_path = tempfile.mkstemp(dir=path.parent, prefix=f".{path.name}.", suffix=".tmp")
    try:
        with os.fdopen(fd, "w", encoding="utf-8") as handle:
            handle.write(content)
            handle.flush()
            os.fsync(handle.fileno())
        os.replace(tmp_path, path)
    except OSError:
        try:
            os.unlink(tmp_path)
        except OSError:
            pass
        raise


def _render_yaml_body(definition: WorkflowDefinition) -> str:
    """Render the full YAML body from a WorkflowDefinition (same format as disk files)."""
    dump_dict = definition.model_dump(mode="json", exclude={"execution_history", "operator_logs"})
    return yaml.safe_dump(dump_dict, allow_unicode=True, sort_keys=False)


# ---------------------------------------------------------------------------
# Disk-only functions (original spec-17 API, kept for backward compat)
# ---------------------------------------------------------------------------


def save_definition_yaml(
    definition: WorkflowDefinition, *, session: Session | None = None
) -> Path:
    """Save workflow definition to YAML with atomic write + optional DB sync.

    When ``session`` is provided, the definition is also upserted into the DB
    (3-table normalized schema) in the same call.

    Args:
        definition: WorkflowDefinition to persist.
        session: Optional DB session for dual-write.

    Returns:
        Path to the written YAML file.

    Raises:
        ValueError: If workflow_id fails whitelist validation.
    """
    _validate_workflow_id(definition.workflow_id)

    body = _render_yaml_body(definition)
    target = user_workflow_dir() / f"{definition.workflow_id}.yaml"
    _atomic_write(target, body)

    if session is not None:
        save_definition_to_db(session, definition, body=body)

    return target


def delete_definition_yaml(workflow_id: str, *, session: Session | None = None) -> bool:
    """Delete workflow YAML file + optional DB row.

    Args:
        workflow_id: Workflow ID to delete.
        session: Optional DB session for dual-delete.

    Returns:
        True if the disk file existed and was deleted, False otherwise.

    Raises:
        ValueError: If workflow_id fails whitelist validation.
    """
    _validate_workflow_id(workflow_id)
    path = user_workflow_dir() / f"{workflow_id}.yaml"
    deleted = False
    if path.exists():
        path.unlink()
        deleted = True

    if session is not None:
        delete_definition_from_db(session, workflow_id)

    return deleted


# ---------------------------------------------------------------------------
# DB CRUD
# ---------------------------------------------------------------------------


def save_definition_to_db(
    session: Session,
    definition: WorkflowDefinition,
    *,
    body: str | None = None,
) -> WorkflowDefinitionAsset:
    """Upsert a workflow definition into the 3-table DB schema.

    Renders the YAML body (if not provided), computes content_hash, upserts
    the parent row, then delete/reinserts child node and edge rows.

    Args:
        session: SQLModel DB session.
        definition: The workflow definition to persist.
        body: Pre-rendered YAML body (avoids double rendering when called
              from save_definition_yaml which already rendered it).

    Returns:
        The upserted WorkflowDefinitionAsset row.
    """
    _validate_workflow_id(definition.workflow_id)

    if body is None:
        body = _render_yaml_body(definition)
    content_hash = _sha256(body)

    existing = session.get(WorkflowDefinitionAsset, definition.workflow_id)
    if existing is not None:
        existing.entry_point = definition.entry_point
        existing.allow_private_networks = definition.allow_private_networks
        existing.body = body
        existing.content_hash = content_hash
        existing.version += 1
        asset = existing
    else:
        asset = WorkflowDefinitionAsset(
            workflow_id=definition.workflow_id,
            entry_point=definition.entry_point,
            allow_private_networks=definition.allow_private_networks,
            body=body,
            content_hash=content_hash,
        )
        session.add(asset)

    session.flush()

    for old_node in list(asset.nodes):
        session.delete(old_node)
    session.flush()

    for node_def in definition.nodes:
        node_row = WorkflowNodeAsset(
            workflow_id=definition.workflow_id,
            name=node_def.name,
            type=node_def.type,
            config=node_def.config,
        )
        session.add(node_row)

    for old_edge in list(asset.edges):
        session.delete(old_edge)
    session.flush()

    for edge_def in definition.edges:
        edge_row = WorkflowEdgeAsset(
            workflow_id=definition.workflow_id,
            source=edge_def.source,
            target=edge_def.target,
            condition=edge_def.condition,
        )
        session.add(edge_row)

    session.commit()
    session.refresh(asset)
    logger.info("workflow_definition_saved_to_db", workflow_id=definition.workflow_id, version=asset.version)
    return asset


def load_definition_from_db(session: Session, workflow_id: str) -> WorkflowDefinition | None:
    """Load a workflow definition from DB, assembling the Pydantic model.

    Args:
        session: SQLModel DB session.
        workflow_id: Workflow ID to load.

    Returns:
        The reconstructed WorkflowDefinition, or None if not found.
    """
    asset = session.get(WorkflowDefinitionAsset, workflow_id)
    if asset is None:
        return None
    return _asset_to_definition(asset)


def load_all_definitions_from_db(session: Session) -> list[WorkflowDefinition]:
    """Load all workflow definitions from DB.

    Args:
        session: SQLModel DB session.

    Returns:
        List of reconstructed WorkflowDefinition instances.
    """
    assets = session.exec(select(WorkflowDefinitionAsset)).all()
    return [_asset_to_definition(asset) for asset in assets]


def delete_definition_from_db(session: Session, workflow_id: str) -> bool:
    """Delete a workflow definition from DB (CASCADE removes nodes/edges).

    Args:
        session: SQLModel DB session.
        workflow_id: Workflow ID to delete.

    Returns:
        True if the row existed and was deleted, False otherwise.
    """
    asset = session.get(WorkflowDefinitionAsset, workflow_id)
    if asset is None:
        return False
    session.delete(asset)
    session.commit()
    logger.info("workflow_definition_deleted_from_db", workflow_id=workflow_id)
    return True


def _asset_to_definition(asset: WorkflowDefinitionAsset) -> WorkflowDefinition:
    """Reconstruct a Pydantic WorkflowDefinition from a DB asset row."""
    nodes = [
        NodeDefinition(name=n.name, type=n.type, config=n.config or {})
        for n in sorted(asset.nodes, key=lambda n: n.id or 0)
    ]
    edges = [
        EdgeDefinition(source=e.source, target=e.target, condition=e.condition)
        for e in sorted(asset.edges, key=lambda e: e.id or 0)
    ]
    return WorkflowDefinition(
        workflow_id=asset.workflow_id,
        entry_point=asset.entry_point,
        nodes=nodes,
        edges=edges,
        state_schema={},
        allow_private_networks=asset.allow_private_networks,
    )


# ---------------------------------------------------------------------------
# Disk ↔ DB sync
# ---------------------------------------------------------------------------


def scan_user_workflow_dir() -> list[str]:
    """Return workflow_ids found as YAML files in the user workflow directory."""
    d = user_workflow_dir()
    if not d.is_dir():
        return []
    return sorted(
        p.stem
        for p in d.iterdir()
        if p.is_file() and p.suffix == ".yaml" and _WORKFLOW_ID_RE.match(p.stem)
    )


def refresh_disk_from_db(session: Session, workflow_id: str | None = None) -> int:
    """Sync DB → disk: rewrite YAML files whose content_hash mismatches.

    Args:
        session: SQLModel DB session.
        workflow_id: If provided, sync only this workflow; otherwise sync all.

    Returns:
        Number of files rewritten.
    """
    if workflow_id is not None:
        assets = []
        asset = session.get(WorkflowDefinitionAsset, workflow_id)
        if asset is not None:
            assets.append(asset)
    else:
        assets = list(session.exec(select(WorkflowDefinitionAsset)).all())

    rewritten = 0
    for asset in assets:
        if asset.body is None:
            continue
        target = user_workflow_dir() / f"{asset.workflow_id}.yaml"
        if target.exists():
            disk_hash = _sha256(target.read_text("utf-8"))
            if disk_hash == asset.content_hash:
                continue
        _atomic_write(target, asset.body)
        rewritten += 1

    if rewritten:
        logger.info("workflow_disk_synced_from_db", count=rewritten)
    return rewritten


def backfill_workflows_from_disk(session: Session) -> int:
    """Import orphan YAML files from disk into DB (one-shot startup bootstrap).

    Only imports files whose workflow_id does not already exist in DB.

    Args:
        session: SQLModel DB session.

    Returns:
        Number of workflows imported.
    """
    disk_ids = set(scan_user_workflow_dir())
    if not disk_ids:
        return 0

    db_ids = {
        row[0]
        for row in session.exec(select(WorkflowDefinitionAsset.workflow_id)).all()
    }
    orphans = disk_ids - db_ids

    imported = 0
    for wf_id in sorted(orphans):
        path = user_workflow_dir() / f"{wf_id}.yaml"
        try:
            with path.open(encoding="utf-8") as f:
                data = yaml.safe_load(f)
            if not isinstance(data, dict):
                continue
            definition = WorkflowDefinition.model_validate(data)
            save_definition_to_db(session, definition)
            imported += 1
        except Exception:  # noqa: BLE001
            logger.warning("workflow_backfill_skipped", workflow_id=wf_id, exc_info=True)

    if imported:
        logger.info("workflow_backfill_completed", imported=imported)
    return imported


def apply_workspace_sync(session: Session) -> dict[str, int]:
    """Bidirectional reconciliation: DB→disk + disk→DB.

    Returns:
        Dict with ``db_to_disk`` and ``disk_to_db`` counts.
    """
    db_to_disk = refresh_disk_from_db(session)
    disk_to_db = backfill_workflows_from_disk(session)
    return {"db_to_disk": db_to_disk, "disk_to_db": disk_to_db}
