"""YAML persistence for user workflows (spec-17).

Provides atomic write of workflow definitions to YAML files with filename
whitelist validation (path traversal prevention). Uses only yaml.safe_dump
(S16 hard gate).

Dependency red-line: this module imports ONLY app.workflow internals + stdlib + yaml.
"""

from __future__ import annotations

import os
import re
from pathlib import Path

import yaml

from app.workflow.models import WorkflowDefinition

_WORKFLOW_ID_RE = re.compile(r"^[A-Za-z0-9_-]{1,64}$")


def _validate_workflow_id(workflow_id: str) -> None:
    """Validate workflow_id against whitelist (path traversal guard)."""
    if not _WORKFLOW_ID_RE.match(workflow_id):
        msg = f"invalid workflow_id: {workflow_id!r} (must match {_WORKFLOW_ID_RE.pattern})"
        raise ValueError(msg)


def user_workflow_dir() -> Path:
    """Return the user workflow directory: app/workflow/config/user/."""
    return Path(__file__).parent / "config" / "user"


def save_definition_yaml(definition: WorkflowDefinition) -> Path:
    """Save workflow definition to YAML with atomic write.

    Validates workflow_id whitelist, dumps definition (excluding execution_history)
    via yaml.safe_dump, writes to tmp file then atomically replaces target.

    Args:
        definition: WorkflowDefinition to persist.

    Returns:
        Path to the written YAML file.

    Raises:
        ValueError: If workflow_id fails whitelist validation.
    """
    _validate_workflow_id(definition.workflow_id)

    dump_dict = definition.model_dump(mode="json", exclude={"execution_history"})
    target = user_workflow_dir() / f"{definition.workflow_id}.yaml"
    target.parent.mkdir(parents=True, exist_ok=True)

    tmp = target.with_suffix(".yaml.tmp")
    tmp.write_text(yaml.safe_dump(dump_dict, allow_unicode=True, sort_keys=False), encoding="utf-8")
    os.replace(tmp, target)
    return target


def delete_definition_yaml(workflow_id: str) -> bool:
    """Delete workflow YAML file.

    Args:
        workflow_id: Workflow ID to delete.

    Returns:
        True if file existed and was deleted, False otherwise.

    Raises:
        ValueError: If workflow_id fails whitelist validation.
    """
    _validate_workflow_id(workflow_id)
    path = user_workflow_dir() / f"{workflow_id}.yaml"
    if path.exists():
        path.unlink()
        return True
    return False
