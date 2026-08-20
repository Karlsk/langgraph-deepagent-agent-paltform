"""stdio MCP manifest directory registry (scan + dry-run + sync).

Each ``*.json`` file under ``settings.MCP_STDIO_ROOT`` describes one stdio MCP
server (``{name, command, args, env, enabled?, description?}``); Docker
deployments simply mount the host directory to ``/app/mcp-servers`` and call
``POST /mcp-servers/stdio-sync``. Scan reuses the same security policy as the
CRUD API (shell-interpreter blacklist, ``MCP_STDIO_ALLOWED_COMMANDS``
allowlist, inline-execution ban, placeholder-only secrets); broken files
degrade per file and never block the rest of the directory.

Sync semantics: manifests are upserted by name (``created_by`` =
``"stdio-registry"``; only content changes rewrite the row and refresh its
content_hash); rows absent from the directory are left untouched. New servers
are probed and collision-checked before insertion; failures skip that one
server and are recorded in the report.
"""

import hashlib
import json
import os
import re
from pathlib import Path
from typing import Any, TypedDict

from sqlmodel import Session

from app.core.config import settings
from app.core.logging import logger
from app.core.mcp_client import MCPServerSpec, namespaced_tool_name, probe_tools
from app.models.agent_assets import McpServerConfig
from app.schemas.agent_apps import NAME_PATTERN
from app.services.agents.mcp_manager import build_tool_catalog, validate_tool_names

# Probe of a candidate stdio server must not block the sync loop.
_SYNC_PROBE_TIMEOUT_SECONDS = 30.0

# Secrets may only be expressed as a single ${ENV_VAR} placeholder.
_PLACEHOLDER_ONLY_PATTERN = re.compile(r"\$\{[A-Za-z_][A-Za-z0-9_]*\}")

# Shell interpreters are always forbidden as stdio commands (unconditional
# blacklist; the configurable allowlist lives in settings).
_SHELL_INTERPRETERS = frozenset(
    {"sh", "bash", "zsh", "dash", "fish", "ksh", "csh", "tcsh", "cmd", "powershell", "pwsh"}
)
# Inline execution modes that turn a trusted interpreter into arbitrary code execution.
_PYTHON_INLINE_FLAGS = frozenset({"-c", "-m"})
_NODE_INLINE_FLAGS = frozenset({"-e", "--eval", "-p", "--print"})


class ManifestScan(TypedDict):
    """Result of scanning the manifest directory.

    Attributes:
        valid: Parsed manifest payloads keyed by server name.
        invalid: Broken files with the rejection reason.
    """

    valid: dict[str, dict[str, Any]]
    invalid: list[dict[str, str]]


class SyncReport(TypedDict):
    """Dry-run / sync report of the manifest directory against the database.

    Attributes:
        scanned: Number of JSON files found in the directory.
        created: Server names that would be / were created.
        updated: Server names that would be / were updated.
        unchanged: Server names with no effective content change.
        skipped: Valid manifests not applied, with the skip reason.
        invalid: Unparseable/policy-rejected files, with the reason.
    """

    scanned: int
    created: list[str]
    updated: list[str]
    unchanged: list[str]
    skipped: list[dict[str, str]]
    invalid: list[dict[str, str]]


def mcp_content_hash(
    transport: str,
    command: str | None,
    args: list[str],
    env: dict[str, str],
    url: str | None,
    headers: dict[str, str],
    enabled: bool,
    description: str,
) -> str:
    """Hash the canonical effective-configuration projection of a server.

    Single source for both the CRUD API and the manifest sync so the
    ``content_hash`` column and config fingerprints stay compatible.

    Returns:
        Hex sha256 over the canonical (sorted-keys, compact) JSON payload.
    """
    payload = {
        "transport": transport,
        "command": command,
        "args": args,
        "env": env,
        "url": url,
        "headers": headers,
        "enabled": enabled,
        "description": description,
    }
    canonical = json.dumps(payload, sort_keys=True, separators=(",", ":"), ensure_ascii=False, default=str)
    return hashlib.sha256(canonical.encode("utf-8")).hexdigest()


def validate_stdio_command(command: str, args: list[str]) -> None:
    """Constrain the stdio command surface (shared by CRUD API and manifests).

    Rejects shell interpreters outright, requires the executable basename to
    be in ``settings.MCP_STDIO_ALLOWED_COMMANDS``, and forbids inline
    execution modes (``python -c/-m``, ``node -e/--eval``).

    Raises:
        ValueError: When the command or its inline mode is forbidden.
    """
    base = os.path.basename(command.strip()).lower()
    if base.endswith(".exe"):
        base = base.removesuffix(".exe")
    if base in _SHELL_INTERPRETERS:
        raise ValueError(f"stdio command '{base}' is a forbidden shell interpreter")
    allowlist = {name.lower() for name in settings.MCP_STDIO_ALLOWED_COMMANDS}
    if base not in allowlist:
        raise ValueError(f"stdio command '{base}' is not in MCP_STDIO_ALLOWED_COMMANDS ({', '.join(sorted(allowlist))})")
    if base.startswith("python") and any(arg in _PYTHON_INLINE_FLAGS for arg in args):
        raise ValueError("stdio args must not use inline execution modes (-c/-m)")
    if base == "node" and any(arg in _NODE_INLINE_FLAGS for arg in args):
        raise ValueError("stdio args must not use inline execution modes (-e/--eval)")


def _validate_placeholder_only(values: dict[str, str]) -> None:
    """Reject plaintext secrets: values must be pure ``${ENV_VAR}`` placeholders.

    Raises:
        ValueError: When any value is not a single placeholder.
    """
    for key, value in values.items():
        if not _PLACEHOLDER_ONLY_PATTERN.fullmatch(str(value)):
            raise ValueError(f"env.{key} must be a ${{ENV_VAR}} placeholder; plaintext secrets are forbidden")


def _validate_manifest(name: str, manifest: dict[str, Any]) -> dict[str, Any]:
    """Validate and normalize one manifest payload.

    Returns:
        The normalized manifest (defaults applied).

    Raises:
        ValueError: On the first policy violation.
    """
    if not isinstance(manifest, dict):
        raise ValueError("manifest must be a JSON object")
    if not re.fullmatch(NAME_PATTERN, name):
        raise ValueError(f"name '{name}' violates the identifier pattern {NAME_PATTERN}")
    if "__" in name:
        raise ValueError("name must not contain '__' (reserved as the server__tool namespace separator)")
    command = manifest.get("command")
    if not isinstance(command, str) or not command:
        raise ValueError("command is required for stdio manifests")
    args = manifest.get("args", [])
    if not isinstance(args, list) or not all(isinstance(arg, str) for arg in args):
        raise ValueError("args must be a list of strings")
    env = manifest.get("env", {})
    if not isinstance(env, dict) or not all(isinstance(k, str) and isinstance(v, str) for k, v in env.items()):
        raise ValueError("env must be a string-to-string mapping")
    enabled = manifest.get("enabled", True)
    if not isinstance(enabled, bool):
        raise ValueError("enabled must be a boolean")
    description = manifest.get("description", "")
    if not isinstance(description, str):
        raise ValueError("description must be a string")

    validate_stdio_command(command, args)
    _validate_placeholder_only(env)
    return {"name": name, "command": command, "args": args, "env": env, "enabled": enabled, "description": description}


def scan_stdio_manifests() -> ManifestScan:
    """Scan ``{MCP_STDIO_ROOT}/*.json`` into valid manifests + invalid files.

    Broken or policy-rejected files degrade per file (recorded with the
    reason) and never block the rest of the directory.

    Returns:
        Mapping of valid manifests keyed by server name and the invalid list.
    """
    result: ManifestScan = {"valid": {}, "invalid": []}
    root = Path(settings.MCP_STDIO_ROOT)
    if not root.is_dir():
        logger.warning("mcp_stdio_root_missing", root=str(root))
        return result

    for path in sorted(root.glob("*.json")):
        try:
            raw = json.loads(path.read_text(encoding="utf-8"))
            name = str(raw.get("name", "")).strip() or path.stem
            manifest = _validate_manifest(name, raw)
        except Exception as exc:  # noqa: BLE001 — per-file degradation
            logger.warning("mcp_stdio_manifest_invalid", file=path.name, error=str(exc))
            result["invalid"].append({"file": path.name, "reason": str(exc)})
            continue
        server_name = manifest["name"]
        if server_name in result["valid"]:
            result["invalid"].append({"file": path.name, "reason": f"duplicate manifest name '{server_name}'"})
            continue
        result["valid"][server_name] = manifest
    return result


def _effective_hash(manifest: dict[str, Any]) -> str:
    """Content hash of a manifest's effective stdio configuration."""
    return mcp_content_hash(
        transport="stdio",
        command=manifest["command"],
        args=manifest["args"],
        env=manifest["env"],
        url=None,
        headers={},
        enabled=manifest["enabled"],
        description=manifest["description"],
    )


def plan_stdio_sync(db: Session) -> SyncReport:
    """Dry-run the manifest sync (no writes, no probes).

    Args:
        db: SQLModel database session.

    Returns:
        Report listing what a sync would create/update/leave/skip.
    """
    scan = scan_stdio_manifests()
    report: SyncReport = {
        "scanned": len(scan["valid"]) + len(scan["invalid"]),
        "created": [],
        "updated": [],
        "unchanged": [],
        "skipped": [],
        "invalid": scan["invalid"],
    }
    for name, manifest in scan["valid"].items():
        existing = db.get(McpServerConfig, name)
        if existing is None:
            report["created"].append(name)
        elif existing.content_hash == _effective_hash(manifest):
            report["unchanged"].append(name)
        else:
            report["updated"].append(name)
    return report


async def sync_stdio_manifests(db: Session) -> SyncReport:
    """Upsert the manifest directory into ``McpServerConfig`` rows.

    New servers are probed and collision-checked (namespaced names) before
    insertion — probe or collision failures skip that server and are recorded
    in the report; existing rows are updated in place only when their
    effective content hash changed. Rows absent from the directory are left
    untouched. The caller commits/invalidates caches.

    Args:
        db: SQLModel database session.

    Returns:
        The executed sync report.
    """
    scan = scan_stdio_manifests()
    report: SyncReport = {
        "scanned": len(scan["valid"]) + len(scan["invalid"]),
        "created": [],
        "updated": [],
        "unchanged": [],
        "skipped": [],
        "invalid": scan["invalid"],
    }

    catalog = None
    for name, manifest in scan["valid"].items():
        existing = db.get(McpServerConfig, name)
        effective_hash = _effective_hash(manifest)
        if existing is not None:
            if existing.content_hash == effective_hash:
                report["unchanged"].append(name)
                continue
            existing.command = manifest["command"]
            existing.args = manifest["args"]
            existing.env = manifest["env"]
            existing.enabled = manifest["enabled"]
            existing.description = manifest["description"]
            existing.content_hash = effective_hash
            db.add(existing)
            report["updated"].append(name)
            continue

        candidate = McpServerConfig(
            name=name,
            transport="stdio",
            command=manifest["command"],
            args=manifest["args"],
            env=manifest["env"],
            enabled=manifest["enabled"],
            description=manifest["description"],
            content_hash="",
            created_by="stdio-registry",
        )
        spec = MCPServerSpec(
            name=name,
            transport="stdio",
            command=manifest["command"],
            args=list(manifest["args"]),
            env=dict(manifest["env"]),
        )
        tool_names = await probe_tools(spec, _SYNC_PROBE_TIMEOUT_SECONDS)
        if tool_names is None:
            report["skipped"].append({"name": name, "reason": "probe_failed"})
            continue
        if tool_names:
            if catalog is None:
                catalog = await build_tool_catalog(db)
            candidates = [namespaced_tool_name(name, tool_name) for tool_name in tool_names]
            try:
                validate_tool_names([entry["name"] for entry in catalog], candidates)
            except ValueError as exc:
                report["skipped"].append({"name": name, "reason": str(exc)})
                continue

        candidate.content_hash = effective_hash
        db.add(candidate)
        report["created"].append(name)
        logger.info("mcp_stdio_manifest_registered", server=name)

    return report
