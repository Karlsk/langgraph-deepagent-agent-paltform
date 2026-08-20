"""MCP server DB adapter over the unified core client layer.

Thin translation layer between ``McpServerConfig`` rows and
``app.core.mcp_client``: rows are converted to transport-pure
``MCPServerSpec`` objects, tool loading/caching/retry/session pooling is
delegated to the core layer, and this module keeps the DB-facing catalog
merge (builtin + namespaced MCP tools with fail-fast collision validation
for CRUD operations).

Public signatures are unchanged (assembly/runtime/test_runner/main.py depend
on them): ``load_mcp_servers`` / ``get_mcp_tools`` / ``build_tool_catalog`` /
``validate_tool_names`` / ``check_server_tool_collision`` /
``shutdown_mcp_clients``.
"""

from collections.abc import Sequence
from typing import NotRequired, TypedDict

from langchain_core.tools.base import BaseTool
from sqlmodel import Session, col, select

from app.core.langgraph.tools import tools as builtin_tools
from app.core.logging import logger
from app.core.mcp_client import MCPServerSpec, load_server_tools, shutdown_mcp_sessions
from app.models.agent_assets import McpServerConfig


class ToolCatalogEntry(TypedDict):
    """One entry of the agent tool catalog.

    Attributes:
        name: Tool name as exposed to the agent (MCP tools are namespaced
            as ``{server}__{tool}``; builtin tools keep their bare names).
        source: Origin of the tool ("builtin" or "mcp").
        server: MCP server name; present only for source="mcp".
    """

    name: str
    source: str
    server: NotRequired[str]


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


def to_spec(server: McpServerConfig) -> MCPServerSpec:
    """Convert one configuration row into a transport-pure spec.

    Shared with the management API (probe / debug endpoints) so every caller
    builds specs from rows the exact same way.
    """
    return MCPServerSpec(
        name=server.name,
        transport=server.transport,
        command=server.command,
        args=list(server.args),
        env=dict(server.env),
        url=server.url,
        headers=dict(server.headers),
    )


async def get_mcp_tools(session: Session) -> list[BaseTool]:
    """Load tools of every enabled MCP server, degrading per server on failure.

    Args:
        session: SQLModel database session.

    Returns:
        Flat list of namespaced tools from all successfully loaded servers.
    """
    result: list[BaseTool] = []
    for server in load_mcp_servers(session):
        tools = await load_server_tools(to_spec(server))
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
        tools = await load_server_tools(to_spec(server))
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
    With the ``{server}__{tool}`` namespace, cross-server name twins no longer
    collide; callers pass namespaced names, so this is a safety net against
    collisions with builtin tools (and identical namespaces).

    Args:
        session: SQLModel database session.
        new_server_tools_names: Namespaced tool names of the candidate server.

    Raises:
        ValueError: Listing every colliding tool name.
    """
    catalog = await build_tool_catalog(session)
    validate_tool_names([entry["name"] for entry in catalog], new_server_tools_names)


async def shutdown_mcp_clients() -> None:
    """Drop every cached client and cache entry (called from lifespan shutdown).

    Closes all pooled MCP sessions in the core layer and clears the local
    catalog cache.
    """
    await shutdown_mcp_sessions()
    _catalog_cache.clear()
    logger.info("mcp_clients_shutdown_complete")
