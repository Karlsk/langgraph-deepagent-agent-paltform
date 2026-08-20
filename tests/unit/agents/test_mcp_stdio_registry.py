"""Unit tests for app.services.agents.mcp_stdio_registry.

Manifest scanning/validation uses a tmp_path directory mounted over
``settings.MCP_STDIO_ROOT``; the DB is an in-memory SQLite session; probes and
catalog loads are monkeypatched (zero real processes / network).
"""

import asyncio
import json
from collections.abc import Generator
from pathlib import Path
from typing import Any

import pytest
from sqlmodel import Session, create_engine

from app.core.config import settings
from app.models.agent_assets import McpServerConfig
from app.services.agents import mcp_stdio_registry
from app.services.agents.mcp_stdio_registry import (
    mcp_content_hash,
    plan_stdio_sync,
    scan_stdio_manifests,
    sync_stdio_manifests,
    validate_stdio_command,
)

pytestmark = pytest.mark.unit


@pytest.fixture()
def db_session() -> Generator[Session, None, None]:
    """Provide an isolated in-memory SQLite session with all tables created."""
    engine = create_engine("sqlite://")
    from sqlmodel import SQLModel  # local import keeps module import order stable

    SQLModel.metadata.create_all(engine)
    with Session(engine) as session:
        yield session


@pytest.fixture()
def manifest_root(tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> Path:
    """Point MCP_STDIO_ROOT at a temporary directory."""
    monkeypatch.setattr(settings, "MCP_STDIO_ROOT", str(tmp_path))
    return tmp_path


def _write_manifest(root: Path, file_name: str, payload: Any) -> None:
    """Write one manifest JSON file into the root."""
    (root / file_name).write_text(json.dumps(payload), encoding="utf-8")


def _manifest(name: str = "", **overrides: Any) -> dict[str, Any]:
    """Build a valid stdio manifest payload with optional overrides."""
    payload: dict[str, Any] = {"name": name, "command": "uvx", "args": [f"{name or 'srv'}-mcp@latest"]}
    payload.update(overrides)
    return payload


@pytest.fixture(autouse=True)
def _patch_probe_and_catalog(monkeypatch: pytest.MonkeyPatch) -> Generator[None, None, None]:
    """Stub the probe call and the catalog builder used by the sync loop."""

    async def _fake_probe(spec: Any, timeout_seconds: float) -> list[str] | None:
        del timeout_seconds
        return _PROBE_RESULTS.get(spec.name, [])

    async def _fake_catalog(db: Any) -> list[dict[str, Any]]:
        del db
        return [dict(entry) for entry in _CATALOG]

    monkeypatch.setattr(mcp_stdio_registry, "probe_tools", _fake_probe)
    monkeypatch.setattr(mcp_stdio_registry, "build_tool_catalog", _fake_catalog)
    _PROBE_RESULTS.clear()
    _CATALOG.clear()
    yield
    _PROBE_RESULTS.clear()
    _CATALOG.clear()


# Per-server probe results faked for the sync loop (None = probe failure).
_PROBE_RESULTS: dict[str, list[str] | None] = {}
# Catalog entries served to the collision check.
_CATALOG: list[dict[str, Any]] = []


# ---------------------------------------------------------------------------
# Command policy (shared with the CRUD API)
# ---------------------------------------------------------------------------


def test_validate_stdio_command_rejects_shell_and_unknown() -> None:
    """Shell interpreters and non-allowlisted commands are rejected."""
    with pytest.raises(ValueError, match="shell interpreter"):
        validate_stdio_command("bash", [])
    with pytest.raises(ValueError, match="not in MCP_STDIO_ALLOWED_COMMANDS"):
        validate_stdio_command("curl", ["https://evil.example"])


def test_validate_stdio_command_rejects_inline_execution() -> None:
    """Python -c/-m and node -e/--eval inline modes are rejected."""
    with pytest.raises(ValueError, match="inline execution"):
        validate_stdio_command("python", ["-c", "import os"])
    with pytest.raises(ValueError, match="inline execution"):
        validate_stdio_command("node", ["-e", "process.exit(1)"])


def test_validate_stdio_command_accepts_allowlisted_entrypoints() -> None:
    """uvx/npx server entrypoints pass the policy."""
    validate_stdio_command("uvx", ["some-mcp@latest"])
    validate_stdio_command("npx", ["-y", "some-mcp"])


# ---------------------------------------------------------------------------
# Scanning: valid manifests, per-file degradation
# ---------------------------------------------------------------------------


def test_scan_parses_valid_manifests_with_defaults(manifest_root: Path) -> None:
    """Valid manifests parse with defaults; name falls back to the file stem."""
    _write_manifest(manifest_root, "weather.json", _manifest("weather"))
    _write_manifest(manifest_root, "stub.json", {"command": "npx", "args": ["-y", "stub-mcp"], "enabled": False})

    scan = scan_stdio_manifests()

    assert set(scan["valid"]) == {"weather", "stub"}
    assert scan["invalid"] == []
    assert scan["valid"]["weather"] == {
        "name": "weather",
        "command": "uvx",
        "args": ["weather-mcp@latest"],
        "env": {},
        "enabled": True,
        "description": "",
    }
    assert scan["valid"]["stub"]["enabled"] is False


def test_scan_degrades_per_file_without_blocking(manifest_root: Path) -> None:
    """Broken or policy-rejected files are recorded individually; the rest scans."""
    (manifest_root / "broken.json").write_text("{not json", encoding="utf-8")
    _write_manifest(manifest_root, "shell.json", _manifest("shelly", command="bash"))
    _write_manifest(manifest_root, "plain-secret.json", _manifest("leaky", env={"TOKEN": "plaintext"}))
    _write_manifest(manifest_root, "underscored.json", _manifest("bad__name"))
    _write_manifest(manifest_root, "no-command.json", {"name": "cmdless"})
    _write_manifest(manifest_root, "good.json", _manifest("good"))

    scan = scan_stdio_manifests()

    assert set(scan["valid"]) == {"good"}
    invalid_files = {entry["file"]: entry["reason"] for entry in scan["invalid"]}
    assert set(invalid_files) == {"broken.json", "shell.json", "plain-secret.json", "underscored.json", "no-command.json"}
    assert "shell interpreter" in invalid_files["shell.json"]
    assert "plaintext" in invalid_files["plain-secret.json"]
    assert "__" in invalid_files["underscored.json"]


def test_scan_reports_duplicate_manifest_names(manifest_root: Path) -> None:
    """Two manifests claiming the same name: the second one is rejected."""
    _write_manifest(manifest_root, "one.json", _manifest("twin"))
    _write_manifest(manifest_root, "two.json", _manifest("twin"))

    scan = scan_stdio_manifests()

    assert set(scan["valid"]) == {"twin"}
    assert scan["invalid"] == [{"file": "two.json", "reason": "duplicate manifest name 'twin'"}]


def test_scan_missing_root_returns_empty(monkeypatch: pytest.MonkeyPatch, tmp_path: Path) -> None:
    """A missing manifest directory scans to an empty result (warned)."""
    monkeypatch.setattr(settings, "MCP_STDIO_ROOT", str(tmp_path / "does-not-exist"))
    assert scan_stdio_manifests() == {"valid": {}, "invalid": []}


# ---------------------------------------------------------------------------
# Dry-run plan vs. database
# ---------------------------------------------------------------------------


def test_plan_stdio_sync_classifies_rows(db_session: Session, manifest_root: Path) -> None:
    """Plan reports created / updated / unchanged without writing or probing."""
    _write_manifest(manifest_root, "fresh.json", _manifest("fresh"))
    _write_manifest(manifest_root, "same.json", _manifest("same"))
    _write_manifest(manifest_root, "drifted.json", _manifest("drifted", args=["drifted-mcp@v2"]))
    unchanged_hash = mcp_content_hash(
        transport="stdio",
        command="uvx",
        args=["same-mcp@latest"],
        env={},
        url=None,
        headers={},
        enabled=True,
        description="",
    )
    db_session.add(McpServerConfig(name="same", transport="stdio", command="uvx", args=["same-mcp@latest"], enabled=True, content_hash=unchanged_hash, created_by="api"))
    db_session.add(McpServerConfig(name="drifted", transport="stdio", command="uvx", args=["drifted-mcp@v1"], enabled=True, content_hash="stale", created_by="api"))
    db_session.commit()

    report = plan_stdio_sync(db_session)

    assert report["scanned"] == 3
    assert report["created"] == ["fresh"]
    assert report["updated"] == ["drifted"]
    assert report["unchanged"] == ["same"]
    assert report["invalid"] == []
    assert db_session.get(McpServerConfig, "fresh") is None  # dry-run never writes


# ---------------------------------------------------------------------------
# Sync: upsert, probe skip, collision skip
# ---------------------------------------------------------------------------


def test_sync_creates_new_servers_with_registry_attribution(db_session: Session, manifest_root: Path) -> None:
    """Valid manifests are probed, collision-checked and inserted."""
    _write_manifest(manifest_root, "weather.json", _manifest("weather"))
    _PROBE_RESULTS["weather"] = ["get_forecast"]

    report = asyncio.run(sync_stdio_manifests(db_session))

    assert report["created"] == ["weather"]
    assert report["skipped"] == []
    row = db_session.get(McpServerConfig, "weather")
    assert row is not None
    assert row.transport == "stdio"
    assert row.command == "uvx"
    assert row.created_by == "stdio-registry"
    assert row.content_hash  # effective hash persisted


def test_sync_skips_new_server_when_probe_fails(db_session: Session, manifest_root: Path) -> None:
    """A failing probe skips that server without blocking the rest."""
    _write_manifest(manifest_root, "broken.json", _manifest("broken"))
    _write_manifest(manifest_root, "healthy.json", _manifest("healthy"))
    _PROBE_RESULTS["broken"] = None
    _PROBE_RESULTS["healthy"] = ["ping"]

    report = asyncio.run(sync_stdio_manifests(db_session))

    assert report["created"] == ["healthy"]
    assert report["skipped"] == [{"name": "broken", "reason": "probe_failed"}]
    assert db_session.get(McpServerConfig, "broken") is None


def test_sync_skips_on_catalog_collision(db_session: Session, manifest_root: Path) -> None:
    """A candidate whose namespaced tool collides with the catalog is skipped."""
    _write_manifest(manifest_root, "twin.json", _manifest("twin"))
    _PROBE_RESULTS["twin"] = ["search"]
    _CATALOG.append({"name": "twin__search", "source": "mcp", "server": "other"})

    report = asyncio.run(sync_stdio_manifests(db_session))

    assert report["created"] == []
    assert report["skipped"] == [{"name": "twin", "reason": "tool_name_collision: twin__search"}]
    assert db_session.get(McpServerConfig, "twin") is None


def test_sync_updates_only_when_content_changes(db_session: Session, manifest_root: Path) -> None:
    """Unchanged rows stay untouched; changed rows are updated with a fresh hash."""
    _write_manifest(manifest_root, "stable.json", _manifest("stable"))
    _write_manifest(manifest_root, "moving.json", _manifest("moving", args=["moving-mcp@v2"]))
    stable_hash = mcp_content_hash(
        transport="stdio",
        command="uvx",
        args=["stable-mcp@latest"],
        env={},
        url=None,
        headers={},
        enabled=True,
        description="",
    )
    db_session.add(McpServerConfig(name="stable", transport="stdio", command="uvx", args=["stable-mcp@latest"], enabled=True, content_hash=stable_hash, created_by="api"))
    db_session.add(McpServerConfig(name="moving", transport="stdio", command="uvx", args=["moving-mcp@v1"], enabled=True, content_hash="stale", created_by="api"))
    db_session.commit()

    report = asyncio.run(sync_stdio_manifests(db_session))

    assert report["created"] == []
    assert report["updated"] == ["moving"]
    assert report["unchanged"] == ["stable"]
    moving = db_session.get(McpServerConfig, "moving")
    assert moving is not None
    assert moving.args == ["moving-mcp@v2"]
    assert moving.content_hash != "stale"
    stable = db_session.get(McpServerConfig, "stable")
    assert stable is not None
    assert stable.content_hash == stable_hash


def test_sync_leaves_rows_absent_from_directory(db_session: Session, manifest_root: Path) -> None:
    """Rows registered by other means (api) survive when not in the directory."""
    db_session.add(
        McpServerConfig(name="api-only", transport="http", url="https://api.example.com/mcp", enabled=True, content_hash="h1", created_by="api")
    )
    db_session.commit()

    report = asyncio.run(sync_stdio_manifests(db_session))

    assert report["scanned"] == 0
    assert db_session.get(McpServerConfig, "api-only") is not None


def test_sync_reports_invalid_files_without_blocking(db_session: Session, manifest_root: Path) -> None:
    """Invalid manifest files land in the report; valid ones still sync."""
    (manifest_root / "broken.json").write_text("{oops", encoding="utf-8")
    _write_manifest(manifest_root, "good.json", _manifest("good"))
    _PROBE_RESULTS["good"] = ["ping"]

    report = asyncio.run(sync_stdio_manifests(db_session))

    assert report["created"] == ["good"]
    assert report["invalid"] == [{"file": "broken.json", "reason": report["invalid"][0]["reason"]}]
    assert "Expecting" in report["invalid"][0]["reason"]


# ---------------------------------------------------------------------------
# Content hash compatibility
# ---------------------------------------------------------------------------


def test_mcp_content_hash_is_canonical() -> None:
    """Key order and non-string values do not change the canonical hash."""
    first = mcp_content_hash("stdio", "uvx", ["a"], {"K": "v"}, None, {}, True, "d")
    second = mcp_content_hash("stdio", "uvx", ["a"], {"K": "v"}, None, {}, True, "d")
    assert first == second
    assert len(first) == 64  # sha256 hex
