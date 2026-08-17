"""Admin API for MCP server assets (CRUD + security validation) and the tool catalog.

Phase-1 scope: all assets are globally shared (no per-user ownership checks);
every endpoint only authenticates via ``get_current_session`` and records the
creator in the audit-only ``created_by`` field.

Error semantics: missing resources return 404; name collisions, validation
failures and dangling tool references return 422; unexpected failures return
500 after ``logger.exception``. MCP server secrets may only be expressed as
``${ENV_VAR}`` placeholders — plaintext secret values are rejected at the
interface layer.
"""

import asyncio
import os
import re
from collections.abc import Mapping
from typing import Any

from fastapi import APIRouter, Depends, HTTPException, Request
from langchain_mcp_adapters.client import MultiServerMCPClient
from sqlmodel import Session as DBSession
from sqlmodel import col, select

from app.api.v1.agent_assets_common import (
    _canonical_sha256,
    _creator,
    _read_patch_body,
    _validate_payload,
    get_db_session,
)
from app.api.v1.auth import get_current_session
from app.core.config import settings
from app.core.limiter import limiter
from app.core.logging import logger
from app.models.agent_assets import McpServerConfig
from app.models.session import Session as ChatSession
from app.schemas.agent_apps import (
    McpServerCreate,
    McpServerRead,
    McpServerUpdate,
    ToolCatalogEntry,
)
from app.schemas.base import ApiResponse
from app.services.agents.mcp_manager import (
    build_connection_config,
    build_tool_catalog,
    check_server_tool_collision,
    load_mcp_servers,
    shutdown_mcp_clients,
    validate_tool_names,
)

router = APIRouter()

# Secrets may only be expressed as a single ${ENV_VAR} placeholder.
_PLACEHOLDER_ONLY_PATTERN = re.compile(r"\$\{[A-Za-z_][A-Za-z0-9_]*\}")

# Probe of a candidate MCP server must not block the management API.
_MCP_PROBE_TIMEOUT_SECONDS = 30.0

# Shell interpreters are always forbidden as stdio commands (unconditional
# blacklist; the configurable allowlist lives in settings).
_SHELL_INTERPRETERS = frozenset(
    {"sh", "bash", "zsh", "dash", "fish", "ksh", "csh", "tcsh", "cmd", "powershell", "pwsh"}
)
# Inline execution modes that turn a trusted interpreter into arbitrary code execution.
_PYTHON_INLINE_FLAGS = frozenset({"-c", "-m"})
_NODE_INLINE_FLAGS = frozenset({"-e", "--eval", "-p", "--print"})


# ---------------------------------------------------------------------------
# Shared helpers
# ---------------------------------------------------------------------------


def _mcp_content_hash(cfg: McpServerConfig) -> str:
    """Compute the content hash of the effective MCP server configuration."""
    return _canonical_sha256(
        {
            "transport": cfg.transport,
            "command": cfg.command,
            "args": cfg.args,
            "env": cfg.env,
            "url": cfg.url,
            "headers": cfg.headers,
            "enabled": cfg.enabled,
            "description": cfg.description,
        }
    )


def _mcp_fingerprint(db: DBSession) -> str:
    """Fingerprint the enabled MCP configuration set (publish hash input)."""
    servers = load_mcp_servers(db)
    return "|".join(sorted(f"{server.name}:{server.content_hash}" for server in servers))


def _ensure_placeholder_secrets(values: Mapping[str, str], *, section: str, server_name: str) -> None:
    """Reject plaintext secrets: values must be pure ``${ENV_VAR}`` placeholders.

    Args:
        values: The env (stdio) or headers (http) mapping to validate.
        section: Config section name used in the error detail.
        server_name: Owning MCP server name (logging context).

    Raises:
        HTTPException: 422 when any value is not a single placeholder.
    """
    for key, value in values.items():
        if not _PLACEHOLDER_ONLY_PATTERN.fullmatch(str(value)):
            logger.warning("mcp_secret_placeholder_violation", server=server_name, section=section, key=key)
            raise HTTPException(
                status_code=422,
                detail=f"{section}.{key} must be a ${{ENV_VAR}} placeholder; plaintext secrets are forbidden",
            )


def _validate_transport_pairing(transport: str, command: str | None, url: str | None) -> None:
    """Validate the merged transport/command/url combination of an MCP server.

    Args:
        transport: Effective transport backend (stdio|http).
        command: Effective stdio command.
        url: Effective http endpoint URL.

    Raises:
        HTTPException: 422 when required fields are missing or forbidden.
    """
    if transport == "stdio":
        if not command:
            raise HTTPException(status_code=422, detail="command is required for stdio transport")
        if url is not None:
            raise HTTPException(status_code=422, detail="url must not be set for stdio transport")
        return
    if not url:
        raise HTTPException(status_code=422, detail="url is required for http transport")
    if command is not None:
        raise HTTPException(status_code=422, detail="command must not be set for http transport")


def _validate_stdio_command(command: str, args: list[str], server_name: str) -> None:
    """Constrain the stdio command surface (phase-1 shared deployment, no isolation).

    Rejects shell interpreters outright, requires the executable basename to be
    in ``settings.MCP_STDIO_ALLOWED_COMMANDS``, and forbids inline execution
    modes (``python -c/-m``, ``node -e/--eval``) that amount to arbitrary code.

    Args:
        command: The stdio executable.
        args: The argument list passed to the executable.
        server_name: Owning MCP server name (logging context).

    Raises:
        HTTPException: 422 when the command or its inline mode is forbidden.
    """
    base = os.path.basename(command.strip()).lower()
    if base.endswith(".exe"):
        base = base.removesuffix(".exe")
    if base in _SHELL_INTERPRETERS:
        logger.warning("mcp_stdio_command_rejected", server=server_name, reason="shell_interpreter", command=base)
        raise HTTPException(status_code=422, detail=f"stdio command '{base}' is a forbidden shell interpreter")
    allowlist = {name.lower() for name in settings.MCP_STDIO_ALLOWED_COMMANDS}
    if base not in allowlist:
        logger.warning("mcp_stdio_command_rejected", server=server_name, reason="not_allowlisted", command=base)
        raise HTTPException(
            status_code=422,
            detail=f"stdio command '{base}' is not in MCP_STDIO_ALLOWED_COMMANDS ({', '.join(sorted(allowlist))})",
        )
    if base.startswith("python") and any(arg in _PYTHON_INLINE_FLAGS for arg in args):
        logger.warning("mcp_stdio_command_rejected", server=server_name, reason="inline_execution", command=base)
        raise HTTPException(status_code=422, detail="stdio args must not use inline execution modes (-c/-m)")
    if base == "node" and any(arg in _NODE_INLINE_FLAGS for arg in args):
        logger.warning("mcp_stdio_command_rejected", server=server_name, reason="inline_execution", command=base)
        raise HTTPException(status_code=422, detail="stdio args must not use inline execution modes (-e/--eval)")


async def _probe_server_tool_names(server: McpServerConfig) -> list[str] | None:
    """Load the tool names of a candidate server via an ephemeral client.

    Used for fail-fast collision validation before the configuration is
    persisted. Probe failures and timeouts (``_MCP_PROBE_TIMEOUT_SECONDS``)
    degrade to ``None`` (skip the collision check), mirroring mcp_manager's
    per-server degradation policy.

    Args:
        server: Candidate (possibly unpersisted) MCP server configuration.

    Returns:
        The tool names exposed by the server, or None when they could not be
        loaded (excluded config, connection failure or timeout).
    """
    connection = build_connection_config(server)
    if connection is None:
        return None

    async def _load() -> list[str]:
        client = MultiServerMCPClient({server.name: connection})
        tools = await client.get_tools()
        return [tool.name for tool in tools]

    try:
        return await asyncio.wait_for(_load(), timeout=_MCP_PROBE_TIMEOUT_SECONDS)
    except TimeoutError:
        logger.warning("mcp_server_tool_probe_timeout", server=server.name, timeout_seconds=_MCP_PROBE_TIMEOUT_SECONDS)
        return None
    except Exception:  # noqa: BLE001 — probe degradation must not block CRUD
        logger.exception("mcp_server_tool_probe_failed", server=server.name)
        return None


# ---------------------------------------------------------------------------
# MCP servers
# ---------------------------------------------------------------------------


@router.get("/mcp-servers", response_model=ApiResponse[list[McpServerRead]])
@limiter.limit(settings.RATE_LIMIT_ENDPOINTS["mcp_server"][0])
async def list_mcp_servers(
    request: Request,
    db: DBSession = Depends(get_db_session),
    current_session: ChatSession = Depends(get_current_session),
) -> ApiResponse[Any]:
    """List every stored MCP server configuration.

    Args:
        request: The FastAPI request object for rate limiting.
        db: Request-scoped DB session.
        current_session: Authenticated chat session.

    Returns:
        Envelope carrying all MCP server rows ordered by name.
    """
    try:
        return ApiResponse.success(list(db.exec(select(McpServerConfig).order_by(col(McpServerConfig.name))).all()))
    except Exception as exc:
        logger.exception("mcp_server_list_failed")
        raise HTTPException(status_code=500, detail=str(exc)) from exc


@router.post("/mcp-servers", response_model=ApiResponse[McpServerRead], status_code=201)
@limiter.limit(settings.RATE_LIMIT_ENDPOINTS["mcp_server"][0])
async def create_mcp_server(
    request: Request,
    payload: McpServerCreate,
    db: DBSession = Depends(get_db_session),
    current_session: ChatSession = Depends(get_current_session),
) -> ApiResponse[Any]:
    """Register an MCP server with fail-fast tool-name collision validation.

    Args:
        request: The FastAPI request object for rate limiting.
        payload: The MCP server connection definition.
        db: Request-scoped DB session.
        current_session: Authenticated chat session (audit only).

    Returns:
        Envelope carrying the persisted MCP server row.

    Raises:
        HTTPException: 422 on name conflict, plaintext secrets or tool
            name collisions with the existing catalog.
    """
    try:
        _ensure_placeholder_secrets(payload.env, section="env", server_name=payload.name)
        _ensure_placeholder_secrets(payload.headers, section="headers", server_name=payload.name)
        if payload.transport == "stdio":
            _validate_stdio_command(payload.command or "", payload.args, payload.name)
        if db.get(McpServerConfig, payload.name) is not None:
            logger.warning("mcp_server_create_conflict", name=payload.name)
            raise HTTPException(status_code=422, detail=f"mcp server '{payload.name}' already exists")

        server = McpServerConfig(
            name=payload.name,
            transport=payload.transport,
            command=payload.command,
            args=payload.args,
            env=payload.env,
            url=payload.url,
            headers=payload.headers,
            enabled=payload.enabled,
            description=payload.description,
            content_hash="",
            created_by=_creator(current_session),
        )
        server.content_hash = _mcp_content_hash(server)

        tool_names = await _probe_server_tool_names(server)
        if tool_names:
            try:
                await check_server_tool_collision(db, tool_names)
            except ValueError as exc:
                logger.warning("mcp_server_collision_rejected", name=payload.name, error=str(exc))
                raise HTTPException(status_code=422, detail=str(exc)) from exc

        db.add(server)
        db.commit()
        db.refresh(server)
        await shutdown_mcp_clients()
        logger.info("mcp_server_created", name=payload.name)
        return ApiResponse.success(server, code=201)
    except HTTPException:
        raise
    except Exception as exc:
        logger.exception("mcp_server_create_failed", name=payload.name)
        raise HTTPException(status_code=500, detail=str(exc)) from exc


@router.get("/mcp-servers/{name}", response_model=ApiResponse[McpServerRead])
@limiter.limit(settings.RATE_LIMIT_ENDPOINTS["mcp_server"][0])
async def get_mcp_server(
    request: Request,
    name: str,
    db: DBSession = Depends(get_db_session),
    current_session: ChatSession = Depends(get_current_session),
) -> ApiResponse[Any]:
    """Fetch one MCP server configuration by name.

    Args:
        request: The FastAPI request object for rate limiting.
        name: MCP server primary key.
        db: Request-scoped DB session.
        current_session: Authenticated chat session.

    Returns:
        Envelope carrying the matching MCP server row.

    Raises:
        HTTPException: 404 when the MCP server does not exist.
    """
    try:
        server = db.get(McpServerConfig, name)
        if server is None:
            raise HTTPException(status_code=404, detail=f"mcp server '{name}' not found")
        return ApiResponse.success(server)
    except HTTPException:
        raise
    except Exception as exc:
        logger.exception("mcp_server_read_failed", name=name)
        raise HTTPException(status_code=500, detail=str(exc)) from exc


@router.patch("/mcp-servers/{name}", response_model=ApiResponse[McpServerRead])
@limiter.limit(settings.RATE_LIMIT_ENDPOINTS["mcp_server"][0])
async def update_mcp_server(
    request: Request,
    name: str,
    db: DBSession = Depends(get_db_session),
    current_session: ChatSession = Depends(get_current_session),
) -> ApiResponse[Any]:
    """Partially update an MCP server (name is immutable).

    An explicit transport switch rebuilds command/url from the payload alone;
    otherwise fields merge over the stored row. The merged configuration is
    validated (transport pairing, placeholder-only secrets, tool collisions)
    before it is persisted, and the MCP client cache is invalidated after.

    Args:
        request: The FastAPI request object for rate limiting.
        name: MCP server primary key.
        db: Request-scoped DB session.
        current_session: Authenticated chat session.

    Returns:
        Envelope carrying the updated MCP server row with refreshed hash.

    Raises:
        HTTPException: 404 when missing, 422 on name change, invalid merged
            transport fields, plaintext secrets or tool collisions.
    """
    body = await _read_patch_body(request)
    payload = _validate_payload(McpServerUpdate, body)
    try:
        server = db.get(McpServerConfig, name)
        if server is None:
            raise HTTPException(status_code=404, detail=f"mcp server '{name}' not found")
        if payload.model_dump(exclude_unset=True) == {}:
            raise HTTPException(status_code=422, detail="nothing to update")

        switching = payload.transport is not None and payload.transport != server.transport
        merged_transport = payload.transport if payload.transport is not None else server.transport
        if switching:
            # Explicit transport switch: forbidden fields are dropped unless
            # the payload itself re-supplies them (schema already guards).
            merged_command = payload.command
            merged_url = payload.url
        else:
            merged_command = payload.command if payload.command is not None else server.command
            merged_url = payload.url if payload.url is not None else server.url
        merged_args = payload.args if payload.args is not None else list(server.args)
        merged_env = payload.env if payload.env is not None else dict(server.env)
        merged_headers = payload.headers if payload.headers is not None else dict(server.headers)
        merged_enabled = payload.enabled if payload.enabled is not None else server.enabled
        merged_description = payload.description if payload.description is not None else server.description

        _validate_transport_pairing(merged_transport, merged_command, merged_url)
        if merged_transport == "stdio" and (payload.command is not None or payload.args is not None or switching):
            _validate_stdio_command(merged_command or "", merged_args, name)
        _ensure_placeholder_secrets(merged_env, section="env", server_name=name)
        _ensure_placeholder_secrets(merged_headers, section="headers", server_name=name)

        candidate = McpServerConfig(
            name=name,
            transport=merged_transport,
            command=merged_command,
            args=merged_args,
            env=merged_env,
            url=merged_url,
            headers=merged_headers,
            enabled=merged_enabled,
            description=merged_description,
            content_hash="",
        )
        candidate.content_hash = _mcp_content_hash(candidate)

        tool_names = await _probe_server_tool_names(candidate)
        if tool_names:
            catalog = await build_tool_catalog(db)
            other_names = [entry["name"] for entry in catalog if entry.get("server") != name]
            try:
                validate_tool_names(other_names, tool_names)
            except ValueError as exc:
                logger.warning("mcp_server_collision_rejected", name=name, error=str(exc))
                raise HTTPException(status_code=422, detail=str(exc)) from exc

        server.transport = merged_transport
        server.command = merged_command
        server.url = merged_url
        server.args = merged_args
        server.env = merged_env
        server.headers = merged_headers
        server.enabled = merged_enabled
        server.description = merged_description
        server.content_hash = candidate.content_hash
        db.add(server)
        db.commit()
        db.refresh(server)
        await shutdown_mcp_clients()
        logger.info("mcp_server_updated", name=name, transport=merged_transport)
        return ApiResponse.success(server)
    except HTTPException:
        raise
    except Exception as exc:
        logger.exception("mcp_server_update_failed", name=name)
        raise HTTPException(status_code=500, detail=str(exc)) from exc


@router.delete("/mcp-servers/{name}", response_model=ApiResponse[None])
@limiter.limit(settings.RATE_LIMIT_ENDPOINTS["mcp_server"][0])
async def delete_mcp_server(
    request: Request,
    name: str,
    db: DBSession = Depends(get_db_session),
    current_session: ChatSession = Depends(get_current_session),
) -> ApiResponse[None]:
    """Delete an MCP server and invalidate the MCP client cache.

    Args:
        request: The FastAPI request object for rate limiting.
        name: MCP server primary key.
        db: Request-scoped DB session.
        current_session: Authenticated chat session.

    Returns:
        Envelope with null data on successful deletion.

    Raises:
        HTTPException: 404 when the MCP server does not exist.
    """
    try:
        server = db.get(McpServerConfig, name)
        if server is None:
            raise HTTPException(status_code=404, detail=f"mcp server '{name}' not found")
        db.delete(server)
        db.commit()
        await shutdown_mcp_clients()
        logger.info("mcp_server_deleted", name=name)
        return ApiResponse.success(None)
    except HTTPException:
        raise
    except Exception as exc:
        logger.exception("mcp_server_delete_failed", name=name)
        raise HTTPException(status_code=500, detail=str(exc)) from exc


# ---------------------------------------------------------------------------
# Tool catalog
# ---------------------------------------------------------------------------


@router.get("/tools/catalog", response_model=ApiResponse[list[ToolCatalogEntry]])
@limiter.limit(settings.RATE_LIMIT_ENDPOINTS["tools_catalog"][0])
async def get_tool_catalog(
    request: Request,
    db: DBSession = Depends(get_db_session),
    current_session: ChatSession = Depends(get_current_session),
) -> ApiResponse[list[dict[str, Any]]]:
    """Expose the merged tool catalog (builtin + MCP with server attribution).

    Args:
        request: The FastAPI request object for rate limiting.
        db: Request-scoped DB session.
        current_session: Authenticated chat session.

    Returns:
        Envelope carrying catalog entries with source labels for form
        checkbox rendering.
    """
    try:
        entries = await build_tool_catalog(db)
        return ApiResponse.success([dict(entry) for entry in entries])
    except Exception as exc:
        logger.exception("tool_catalog_read_failed")
        raise HTTPException(status_code=500, detail=str(exc)) from exc
