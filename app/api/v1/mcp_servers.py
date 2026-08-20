"""Admin API for MCP server assets and tool operations.

CRUD + security validation, the tool catalog, stdio manifest discovery and
tool debug endpoints.

Phase-1 scope: all assets are globally shared (no per-user ownership checks);
every endpoint only authenticates via ``get_current_session`` and records the
creator in the audit-only ``created_by`` field.

Error semantics: missing resources return 404; name collisions, validation
failures and dangling tool references return 422; unexpected failures return
500 after ``logger.exception``. MCP server secrets may only be expressed as
``${ENV_VAR}`` placeholders — plaintext secret values are rejected at the
interface layer. Debug endpoints surface upstream failures as 502 and
timeouts as 504.

Transport backends: ``stdio`` (command), ``sse`` and ``http`` (url; ``http``
is the streamable-http runtime alias). MCP tools are catalogued under the
``{server}__{tool}`` namespace; collision checks compare namespaced names.
"""

import re
from collections.abc import Mapping
from typing import Any

from fastapi import APIRouter, Depends, HTTPException, Query, Request
from pydantic import ValidationError
from sqlmodel import Session as DBSession
from sqlmodel import col, select

from app.api.v1.agent_assets_common import (
    _creator,
    _read_patch_body,
    _validate_payload,
    get_db_session,
    paginate_by_name,
)
from app.api.v1.auth import get_current_session
from app.core import mcp_client
from app.core.config import settings
from app.core.limiter import limiter
from app.core.logging import logger
from app.models.agent_assets import McpServerConfig
from app.models.session import Session as ChatSession
from app.schemas.agent_apps import (
    McpServerCreate,
    McpServerRead,
    McpServerUpdate,
    McpToolCallRequest,
    McpToolCallResult,
    McpToolInfo,
    ToolCatalogEntry,
)
from app.schemas.base import ApiResponse, PageResult
from app.services.agents.mcp_manager import (
    build_tool_catalog,
    check_server_tool_collision,
    load_mcp_servers,
    shutdown_mcp_clients,
    to_spec,
    validate_tool_names,
)
from app.services.agents.mcp_stdio_registry import (
    mcp_content_hash,
    plan_stdio_sync,
    sync_stdio_manifests,
    validate_stdio_command,
)

router = APIRouter()

# Secrets may only be expressed as a single ${ENV_VAR} placeholder.
_PLACEHOLDER_ONLY_PATTERN = re.compile(r"\$\{[A-Za-z_][A-Za-z0-9_]*\}")

# Debug endpoints must not block the management API; tool execution may be slow.
_MCP_LIST_TIMEOUT_SECONDS = 30.0
_MCP_CALL_TIMEOUT_SECONDS = 60.0


# ---------------------------------------------------------------------------
# Shared helpers
# ---------------------------------------------------------------------------


def _mcp_content_hash(cfg: McpServerConfig) -> str:
    """Compute the content hash of the effective MCP server configuration."""
    return mcp_content_hash(
        transport=cfg.transport,
        command=cfg.command,
        args=list(cfg.args),
        env=dict(cfg.env),
        url=cfg.url,
        headers=dict(cfg.headers),
        enabled=cfg.enabled,
        description=cfg.description,
    )


def _mcp_fingerprint(db: DBSession) -> str:
    """Fingerprint the enabled MCP configuration set (publish hash input)."""
    servers = load_mcp_servers(db)
    return "|".join(sorted(f"{server.name}:{server.content_hash}" for server in servers))


def _ensure_placeholder_secrets(values: Mapping[str, str], *, section: str, server_name: str) -> None:
    """Reject plaintext secrets: values must be pure ``${ENV_VAR}`` placeholders.

    Args:
        values: The env (stdio) or headers (sse/http) mapping to validate.
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
        transport: Effective transport backend (stdio|sse|http).
        command: Effective stdio command.
        url: Effective sse/http endpoint URL.

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
        raise HTTPException(status_code=422, detail=f"url is required for {transport} transport")
    if command is not None:
        raise HTTPException(status_code=422, detail=f"command must not be set for {transport} transport")


def _validate_stdio_command(command: str, args: list[str], server_name: str) -> None:
    """Constrain the stdio command surface (policy shared with manifest sync).

    Raises:
        HTTPException: 422 when the command or its inline mode is forbidden.
    """
    try:
        validate_stdio_command(command, args)
    except ValueError as exc:
        logger.warning("mcp_stdio_command_rejected", server=server_name, command=command, error=str(exc))
        raise HTTPException(status_code=422, detail=str(exc)) from exc


def _get_server_or_404(db: DBSession, name: str) -> McpServerConfig:
    """Fetch one MCP server row or raise 404."""
    server = db.get(McpServerConfig, name)
    if server is None:
        raise HTTPException(status_code=404, detail=f"mcp server '{name}' not found")
    return server


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


@router.get("/mcp-servers/page", response_model=ApiResponse[PageResult[McpServerRead]])
@limiter.limit(settings.RATE_LIMIT_ENDPOINTS["mcp_server"][0])
async def list_mcp_servers_page(
    request: Request,
    page: int = Query(1, ge=1),
    page_size: int = Query(10, ge=1, le=100, alias="pageSize"),
    keyword: str | None = Query(None, max_length=200),
    db: DBSession = Depends(get_db_session),
    current_session: ChatSession = Depends(get_current_session),
) -> ApiResponse[Any]:
    """List MCP server configurations with server-side pagination.

    Args:
        request: The FastAPI request object for rate limiting.
        page: 1-based page number.
        page_size: Rows per page (exposed as ``pageSize``).
        keyword: Optional case-insensitive substring matched against name.
        db: Request-scoped DB session.
        current_session: Authenticated chat session.

    Returns:
        Envelope carrying a PageResult of MCP server rows ordered by name.
    """
    try:
        return ApiResponse.success(
            paginate_by_name(
                db,
                McpServerConfig,
                page=page,
                page_size=page_size,
                keyword=keyword,
                order_by=col(McpServerConfig.name),
            )
        )
    except Exception as exc:
        logger.exception("mcp_server_list_page_failed")
        raise HTTPException(status_code=500, detail=str(exc)) from exc


@router.get("/mcp-servers/stdio-manifests", response_model=ApiResponse[dict[str, Any]])
@limiter.limit(settings.RATE_LIMIT_ENDPOINTS["mcp_server"][0])
async def preview_stdio_manifests(
    request: Request,
    db: DBSession = Depends(get_db_session),
    current_session: ChatSession = Depends(get_current_session),
) -> ApiResponse[Any]:
    """Dry-run the stdio manifest directory sync (no writes, no probes).

    Literal path registered before ``/mcp-servers/{name}`` so it is never
    captured as a server name.

    Args:
        request: The FastAPI request object for rate limiting.
        db: Request-scoped DB session.
        current_session: Authenticated chat session.

    Returns:
        Envelope carrying the planned create/update/unchanged/skip report.
    """
    try:
        return ApiResponse.success(plan_stdio_sync(db))
    except Exception as exc:
        logger.exception("mcp_stdio_manifests_preview_failed")
        raise HTTPException(status_code=500, detail=str(exc)) from exc


@router.post("/mcp-servers/stdio-sync", response_model=ApiResponse[dict[str, Any]])
@limiter.limit(settings.RATE_LIMIT_ENDPOINTS["mcp_server"][0])
async def apply_stdio_sync(
    request: Request,
    db: DBSession = Depends(get_db_session),
    current_session: ChatSession = Depends(get_current_session),
) -> ApiResponse[Any]:
    """Execute the stdio manifest directory sync (upsert by name).

    New servers are probed and collision-checked; failures skip that server
    and are recorded in the report. Pooled MCP sessions are invalidated when
    anything changed.

    Args:
        request: The FastAPI request object for rate limiting.
        db: Request-scoped DB session.
        current_session: Authenticated chat session.

    Returns:
        Envelope carrying the executed sync report.
    """
    try:
        report = await sync_stdio_manifests(db)
        db.commit()
        if report["created"] or report["updated"]:
            await shutdown_mcp_clients()
        logger.info(
            "mcp_stdio_sync_completed",
            created=len(report["created"]),
            updated=len(report["updated"]),
            skipped=len(report["skipped"]),
            invalid=len(report["invalid"]),
        )
        return ApiResponse.success(report)
    except HTTPException:
        raise
    except Exception as exc:
        logger.exception("mcp_stdio_sync_failed")
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

        raw_tool_names = await mcp_client.probe_tools(to_spec(server), _MCP_LIST_TIMEOUT_SECONDS)
        if raw_tool_names:
            namespaced = [mcp_client.namespaced_tool_name(payload.name, name) for name in raw_tool_names]
            try:
                await check_server_tool_collision(db, namespaced)
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
        return ApiResponse.success(_get_server_or_404(db, name))
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

        raw_tool_names = await mcp_client.probe_tools(to_spec(candidate), _MCP_LIST_TIMEOUT_SECONDS)
        if raw_tool_names:
            namespaced = [mcp_client.namespaced_tool_name(name, tool_name) for tool_name in raw_tool_names]
            catalog = await build_tool_catalog(db)
            other_names = [entry["name"] for entry in catalog if entry.get("server") != name]
            try:
                validate_tool_names(other_names, namespaced)
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
        server = _get_server_or_404(db, name)
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
# Tool debug endpoints (live inspection; never touch the pooled sessions)
# ---------------------------------------------------------------------------


@router.get("/mcp-servers/{name}/tools", response_model=ApiResponse[list[McpToolInfo]])
@limiter.limit(settings.RATE_LIMIT_ENDPOINTS["mcp_tools_debug"][0])
async def list_mcp_server_tools(
    request: Request,
    name: str,
    db: DBSession = Depends(get_db_session),
    current_session: ChatSession = Depends(get_current_session),
) -> ApiResponse[Any]:
    """Live-list the current tools of one MCP server via an ephemeral session.

    Reads the server's up-to-date tool list (cache bypass); disabled servers
    are listable too (read-only debugging).

    Args:
        request: The FastAPI request object for rate limiting.
        name: MCP server primary key.
        db: Request-scoped DB session.
        current_session: Authenticated chat session.

    Returns:
        Envelope carrying raw tool names, descriptions and JSON schemas.

    Raises:
        HTTPException: 404 unknown server; 422 unresolved config; 502
            upstream failure; 504 listing timeout.
    """
    try:
        server = _get_server_or_404(db, name)
        try:
            summaries = await mcp_client.list_tools(to_spec(server), _MCP_LIST_TIMEOUT_SECONDS)
        except ValueError as exc:
            raise HTTPException(status_code=422, detail=str(exc)) from exc
        except TimeoutError as exc:
            raise HTTPException(status_code=504, detail=f"mcp server '{name}' timed out listing tools") from exc
        except mcp_client.MCPUpstreamError as exc:
            raise HTTPException(status_code=502, detail=str(exc)) from exc
        return ApiResponse.success(
            [
                McpToolInfo(name=summary.name, description=summary.description, args_schema=summary.args_schema)
                for summary in summaries
            ]
        )
    except HTTPException:
        raise
    except Exception as exc:
        logger.exception("mcp_server_tools_list_failed", name=name)
        raise HTTPException(status_code=500, detail=str(exc)) from exc


@router.post("/mcp-servers/{name}/call-tool", response_model=ApiResponse[McpToolCallResult])
@limiter.limit(settings.RATE_LIMIT_ENDPOINTS["mcp_tools_debug"][0])
async def call_mcp_server_tool(
    request: Request,
    name: str,
    payload: McpToolCallRequest,
    db: DBSession = Depends(get_db_session),
    current_session: ChatSession = Depends(get_current_session),
) -> ApiResponse[Any]:
    """Invoke one tool of an MCP server via an ephemeral session (debug).

    Args:
        request: The FastAPI request object for rate limiting.
        name: MCP server primary key.
        payload: Raw tool name and arguments.
        db: Request-scoped DB session.
        current_session: Authenticated chat session.

    Returns:
        Envelope carrying the tool output content.

    Raises:
        HTTPException: 404 unknown server; 422 unknown tool or missing
            required arguments (client-side guards; type validation stays
            authoritative on the server); 502 tool/server failure; 504 call
            timeout.
    """
    try:
        server = _get_server_or_404(db, name)
        try:
            result = await mcp_client.call_tool(
                to_spec(server), payload.tool_name, payload.arguments, _MCP_CALL_TIMEOUT_SECONDS
            )
        except (ValueError, ValidationError) as exc:
            raise HTTPException(status_code=422, detail=str(exc)) from exc
        except TimeoutError as exc:
            raise HTTPException(status_code=504, detail=f"mcp tool '{payload.tool_name}' timed out") from exc
        except mcp_client.MCPUpstreamError as exc:
            raise HTTPException(status_code=502, detail=str(exc)) from exc
        logger.info("mcp_tool_debug_called", server=name, tool=payload.tool_name)
        return ApiResponse.success(McpToolCallResult(server=name, tool_name=payload.tool_name, result=result))
    except HTTPException:
        raise
    except Exception as exc:
        logger.exception("mcp_server_tool_call_failed", name=name, tool=payload.tool_name)
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
