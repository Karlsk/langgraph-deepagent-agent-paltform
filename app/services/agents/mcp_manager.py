"""MCP server client management for agent tool catalogs.

Process-level lifecycle manager for ``MultiServerMCPClient`` instances keyed by
each server's ``content_hash``: clients are rebuilt (and counted) only when a
server configuration changes, tool lists are cached per ``(server, hash)`` and
never cached on error, and MCP tools are merged with builtin tools into a
catalog with fail-fast name-collision validation for CRUD operations.

Semantics follow ``docs/deepagents/06-MCP集成.md``: stateless sessions by
default (no persistent ``client.session(...)`` scopes), tenacity-wrapped
``get_tools()`` retries, and structlog structured logging throughout.
"""

import asyncio
import os
import re
from collections.abc import Sequence
from typing import NotRequired, TypedDict

from langchain_core.tools.base import BaseTool
from langchain_mcp_adapters.client import MultiServerMCPClient
from langchain_mcp_adapters.sessions import Connection, StdioConnection, StreamableHttpConnection
from sqlmodel import Session, col, select
from tenacity import AsyncRetrying, stop_after_attempt, wait_exponential

from app.core.langgraph.tools import tools as builtin_tools
from app.core.logging import logger
from app.core.metrics import mcp_client_rebuild_total, mcp_tools_loaded_total
from app.models.agent_assets import McpServerConfig

# Matches braced environment placeholders such as ${API_TOKEN}.
_ENV_PLACEHOLDER_PATTERN = re.compile(r"\$\{([A-Za-z_][A-Za-z0-9_]*)\}")


class ToolCatalogEntry(TypedDict):
    """One entry of the agent tool catalog.

    Attributes:
        name: Tool name as exposed to the agent.
        source: Origin of the tool ("builtin" or "mcp").
        server: MCP server name; present only for source="mcp".
    """

    name: str
    source: str
    server: NotRequired[str]


# Process-level client cache keyed by the server config content_hash.
_clients: dict[str, MultiServerMCPClient] = {}
# Last seen content_hash per server name (drives rebuild reasons).
_server_hashes: dict[str, str] = {}
# Successful tool lists keyed by (server name, content_hash); errors never cached.
_tool_cache: dict[tuple[str, str], list[BaseTool]] = {}
# Catalogs keyed by (frozenset of builtin names, mcp config fingerprint).
_catalog_cache: dict[tuple[frozenset[str], str], list[ToolCatalogEntry]] = {}


def load_mcp_servers(session: Session) -> list[McpServerConfig]:
    """Load all enabled MCP server configuration rows.

    Args:
        session: SQLModel database session.

    Returns:
        List of enabled McpServerConfig rows.
    """
    statement = select(McpServerConfig).where(col(McpServerConfig.enabled).is_(True))
    return list(session.exec(statement).all())


def _resolve_placeholders(server_name: str, key: str, value: str) -> str | None:
    """Expand ``${ENV_VAR}`` placeholders in a config value from os.environ.

    Args:
        server_name: Owning MCP server name (for logging).
        key: Config key the value belongs to (for logging).
        value: Raw config value possibly containing placeholders.

    Returns:
        The resolved value, or None when any referenced variable is missing.
    """
    missing: set[str] = set()

    def _replace(match: re.Match[str]) -> str:
        variable = match.group(1)
        resolved = os.environ.get(variable)
        if resolved is None:
            missing.add(variable)
            return match.group(0)
        return resolved

    resolved_value = _ENV_PLACEHOLDER_PATTERN.sub(_replace, value)
    if missing:
        logger.error(
            "mcp_env_placeholder_unresolved",
            server=server_name,
            key=key,
            variables=sorted(missing),
        )
        return None
    return resolved_value


def build_connection_config(server: McpServerConfig) -> Connection | None:
    """Build the MultiServerMCPClient connection mapping for one server.

    env (stdio) and headers (http) values support ``${ENV_VAR}`` placeholders
    resolved from os.environ; a missing variable excludes the whole server.

    Args:
        server: The MCP server configuration row.

    Returns:
        Connection config dict, or None when the server must be excluded.
    """
    if server.transport == "stdio":
        if not server.command:
            logger.error("mcp_server_config_invalid", server=server.name, detail="stdio transport requires command")
            return None
        env: dict[str, str] = {}
        for key, raw_value in server.env.items():
            resolved = _resolve_placeholders(server.name, f"env.{key}", str(raw_value))
            if resolved is None:
                return None
            env[key] = resolved
        stdio_connection: StdioConnection = {
            "transport": "stdio",
            "command": server.command,
            "args": list(server.args),
        }
        if env:
            stdio_connection["env"] = env
        return stdio_connection

    if server.transport == "http":
        if not server.url:
            logger.error("mcp_server_config_invalid", server=server.name, detail="http transport requires url")
            return None
        headers: dict[str, str] = {}
        for key, raw_value in server.headers.items():
            resolved = _resolve_placeholders(server.name, f"headers.{key}", str(raw_value))
            if resolved is None:
                return None
            headers[key] = resolved
        # "http" is accepted at runtime as an alias of "streamable_http" (sessions.py)
        http_connection: StreamableHttpConnection = {
            "transport": "http",  # pyright: ignore[reportAssignmentType] — runtime alias of streamable_http
            "url": server.url,
        }
        if headers:
            http_connection["headers"] = headers
        return http_connection

    logger.error("mcp_server_config_invalid", server=server.name, detail=f"unsupported transport {server.transport}")
    return None


def _get_client(server: McpServerConfig, connection: Connection) -> MultiServerMCPClient:
    """Return the cached client for the server's content_hash, rebuilding on change.

    Args:
        server: The MCP server configuration row.
        connection: Resolved connection mapping for the client constructor.

    Returns:
        The (possibly newly built) MultiServerMCPClient instance.
    """
    cached = _clients.get(server.content_hash)
    if cached is not None:
        _server_hashes[server.name] = server.content_hash
        return cached

    previous_hash = _server_hashes.get(server.name)
    reason = "config_changed" if previous_hash is not None else "new"
    client = MultiServerMCPClient({server.name: connection})
    _clients[server.content_hash] = client
    _server_hashes[server.name] = server.content_hash
    if previous_hash is not None and previous_hash != server.content_hash:
        # Drop stale state bound to the superseded configuration.
        _clients.pop(previous_hash, None)
        _tool_cache.pop((server.name, previous_hash), None)
    mcp_client_rebuild_total.labels(reason=reason).inc()
    logger.info("mcp_client_built", server=server.name, reason=reason, content_hash=server.content_hash)
    return client


async def _retry_sleep(seconds: float) -> None:
    """Awaitable backoff hook for tenacity (monkeypatched in unit tests).

    Args:
        seconds: Number of seconds to sleep before the next attempt.
    """
    await asyncio.sleep(seconds)


async def _load_tools_with_retry(server_name: str, client: MultiServerMCPClient) -> list[BaseTool]:
    """Call client.get_tools() with tenacity exponential backoff (AD-03).

    Args:
        server_name: MCP server name (kept for logging context).
        client: The client whose tools should be loaded.

    Returns:
        The list of tools exposed by the server.

    Raises:
        Exception: Re-raised after 3 failed attempts (reraise=True).
    """
    retrying = AsyncRetrying(
        stop=stop_after_attempt(3),
        wait=wait_exponential(multiplier=1, min=4, max=10),
        sleep=_retry_sleep,
        reraise=True,
    )
    return await retrying(client.get_tools)


async def _load_single_server_tools(server: McpServerConfig) -> list[BaseTool] | None:
    """Load (or serve from cache) the tools of one server.

    Args:
        server: The MCP server configuration row.

    Returns:
        Tool list on success (possibly empty), or None when the server was
        excluded by config resolution or failed after retries; errors are
        never cached.
    """
    connection = build_connection_config(server)
    if connection is None:
        mcp_tools_loaded_total.labels(server=server.name, status="error").inc()
        return None

    client = _get_client(server, connection)
    cache_key = (server.name, server.content_hash)
    cached = _tool_cache.get(cache_key)
    if cached is not None:
        return cached

    try:
        tools = await _load_tools_with_retry(server.name, client)
    except Exception:  # noqa: BLE001 — per-server degradation must not block the catalog
        logger.exception("mcp_tools_load_failed", server=server.name)
        mcp_tools_loaded_total.labels(server=server.name, status="error").inc()
        return None

    _tool_cache[cache_key] = tools
    mcp_tools_loaded_total.labels(server=server.name, status="success").inc()
    logger.info("mcp_tools_loaded", server=server.name, tool_count=len(tools))
    return tools


async def get_mcp_tools(session: Session) -> list[BaseTool]:
    """Load tools of every enabled MCP server, degrading per server on failure.

    Args:
        session: SQLModel database session.

    Returns:
        Flat list of tools from all successfully loaded servers.
    """
    result: list[BaseTool] = []
    for server in load_mcp_servers(session):
        tools = await _load_single_server_tools(server)
        if tools is not None:
            result.extend(tools)
    return result


def _mcp_config_fingerprint(servers: Sequence[McpServerConfig]) -> str:
    """Build a stable fingerprint of the enabled MCP configuration set.

    Args:
        servers: Enabled MCP server configuration rows.

    Returns:
        Deterministic string combining every server name and content_hash.
    """
    return "|".join(sorted(f"{server.name}:{server.content_hash}" for server in servers))


async def build_tool_catalog(session: Session) -> list[ToolCatalogEntry]:
    """Merge builtin tools and MCP tools into the agent tool catalog.

    The result is cached by (builtin name set, mcp config fingerprint), but
    never while any enabled server fails to load, so a recovered server is
    picked up on the next call.

    Args:
        session: SQLModel database session.

    Returns:
        Catalog entries with source="builtin" or source="mcp" (+ server).
    """
    servers = load_mcp_servers(session)
    builtin_names = frozenset(tool.name for tool in builtin_tools)
    cache_key = (builtin_names, _mcp_config_fingerprint(servers))
    cached = _catalog_cache.get(cache_key)
    if cached is not None:
        return cached

    catalog: list[ToolCatalogEntry] = [ToolCatalogEntry(name=tool.name, source="builtin") for tool in builtin_tools]
    all_loaded = True
    for server in servers:
        tools = await _load_single_server_tools(server)
        if tools is None:
            all_loaded = False
            continue
        catalog.extend(ToolCatalogEntry(name=tool.name, source="mcp", server=server.name) for tool in tools)

    if all_loaded:
        _catalog_cache[cache_key] = catalog
    logger.info("mcp_tool_catalog_built", entry_count=len(catalog), cached=all_loaded)
    return catalog


def validate_tool_names(catalog_names: Sequence[str], candidate_names: Sequence[str]) -> None:
    """Fail fast when candidate tool names collide with existing catalog names.

    Args:
        catalog_names: Tool names already present in the catalog.
        candidate_names: Tool names about to be added.

    Raises:
        ValueError: Listing every colliding tool name.
    """
    collisions = sorted(set(catalog_names) & set(candidate_names))
    if collisions:
        raise ValueError(f"tool_name_collision: {', '.join(collisions)}")


async def check_server_tool_collision(session: Session, new_server_tools_names: Sequence[str]) -> None:
    """Validate a new MCP server's tool names against the current catalog.

    Intended for CRUD fail-fast before persisting a new server configuration.

    Args:
        session: SQLModel database session.
        new_server_tools_names: Tool names exposed by the candidate server.

    Raises:
        ValueError: Listing every colliding tool name.
    """
    catalog = await build_tool_catalog(session)
    validate_tool_names([entry["name"] for entry in catalog], new_server_tools_names)


async def shutdown_mcp_clients() -> None:
    """Drop every cached client and cache entry (called from lifespan shutdown).

    Stateless mode holds no persistent MCP sessions, so clearing the caches is
    sufficient; stdio child processes exit with their client objects.
    """
    client_count = len(_clients)
    _clients.clear()
    _server_hashes.clear()
    _tool_cache.clear()
    _catalog_cache.clear()
    logger.info("mcp_clients_shutdown_complete", client_count=client_count)
