"""Unified MCP client layer (core): connections, pooled sessions, tool loading.

Single place that talks to ``langchain_mcp_adapters``:

- ``MCPServerSpec``: transport-pure server configuration (no ORM coupling).
- ``build_connection``: spec -> adapter ``Connection`` (stdio | sse | http)
  with ``${ENV_VAR}`` placeholder resolution; unresolved variables exclude the
  whole server (existing semantics).
- Process-level per-server session pool with a worker ownership model: every
  pooled session is owned by one long-lived ``mcp-session-{server}`` worker
  task that enters AND exits the adapter session context manager, because the
  underlying anyio cancel scopes / task groups must never cross task
  boundaries ("Attempted to exit cancel scope in a different task"). All close
  paths — idle TTL recycle (``MCP_SESSION_IDLE_TTL``), connection-hash change,
  transport-failure invalidation and process shutdown — merely set the
  worker's stop event (bounded by ``MCP_SESSION_STOP_TIMEOUT`` with a
  force-cancel fallback that still fires inside the worker) and let it unwind
  the context itself. Tool invocations stay on the calling task: JSON-RPC
  calls over an open session are task-agnostic, so no proxying layer is
  needed. Cold-cache loads are singleflighted per server via ``asyncio.Lock``
  and survive waiter cancellations (a finished build is adopted into the pool
  by its waiter, or by a finalizer task when every waiter vanished).
- Pooled tools are exposed under the ``{server}__{tool}`` namespace so tools
  of different servers never collide; builtin tools keep their bare names.
- One-shot helpers for the management/debug API: ``probe_tools`` (silent
  degradation) and ``list_tools`` / ``call_tool`` (explicit ``ValueError`` /
  ``MCPUpstreamError`` / ``TimeoutError`` for the API layer to map to
  422/502/504). One-shot helpers never touch the pool.

Dependency direction: this module imports only langchain_mcp_adapters,
tenacity and ``app.core.{config,logging,metrics}`` — never app.services /
app.models / app.api.
"""

from __future__ import annotations

import asyncio
import hashlib
import json
import os
import re
import time
from contextlib import AbstractAsyncContextManager
from dataclasses import dataclass, field
from typing import Any

from langchain_core.tools import BaseTool, StructuredTool
from langchain_mcp_adapters.sessions import (
    Connection,
    SSEConnection,
    StdioConnection,
    StreamableHttpConnection,
    create_session,
)
from langchain_mcp_adapters.tools import load_mcp_tools
from mcp import ClientSession
from pydantic import ValidationError
from tenacity import AsyncRetrying, stop_after_attempt, wait_exponential

from app.core.config import settings
from app.core.logging import logger
from app.core.metrics import mcp_client_rebuild_total, mcp_session_stop_total, mcp_tools_loaded_total

# Matches braced environment placeholders such as ${API_TOKEN}.
_ENV_PLACEHOLDER_PATTERN = re.compile(r"\$\{([A-Za-z_][A-Za-z0-9_]*)\}")

# Namespace separator between MCP server name and raw tool name.
TOOL_NAMESPACE_SEPARATOR = "__"


class MCPUpstreamError(Exception):
    """An MCP server failed while serving a debug request (API maps to 502)."""


@dataclass(frozen=True)
class MCPServerSpec:
    """Transport-pure description of one MCP server (no ORM coupling).

    Attributes:
        name: Server name (pool key; also the tool namespace prefix).
        transport: Transport backend (stdio | sse | http).
        command: Executable command for stdio transport.
        args: Argument list for the stdio command.
        env: Extra environment variables for the stdio process.
        url: Endpoint URL for sse/http transports.
        headers: Extra HTTP headers for sse/http transports.
    """

    name: str
    transport: str
    command: str | None = None
    args: list[str] = field(default_factory=list)
    env: dict[str, str] = field(default_factory=dict)
    url: str | None = None
    headers: dict[str, str] = field(default_factory=dict)

    def connection_hash(self) -> str:
        """Hash the connection-relevant projection (name excluded by design).

        Returns:
            Hex sha256 over the canonical (sorted-keys, compact) JSON payload.
        """
        payload = {
            "transport": self.transport,
            "command": self.command,
            "args": list(self.args),
            "env": dict(self.env),
            "url": self.url,
            "headers": dict(self.headers),
        }
        canonical = json.dumps(payload, sort_keys=True, separators=(",", ":"), ensure_ascii=False, default=str)
        return hashlib.sha256(canonical.encode("utf-8")).hexdigest()


@dataclass
class ToolSummary:
    """One entry of a debug tool listing.

    Attributes:
        name: Raw tool name as exposed by the server (un-namespaced).
        description: Tool description.
        args_schema: JSON-schema dict of the tool arguments.
    """

    name: str
    description: str
    args_schema: dict[str, Any]


def namespaced_tool_name(server_name: str, tool_name: str) -> str:
    """Build the ``{server}__{tool}`` catalog/tool name."""
    return f"{server_name}{TOOL_NAMESPACE_SEPARATOR}{tool_name}"


# ---------------------------------------------------------------------------
# Connection building (${ENV_VAR} placeholder resolution)
# ---------------------------------------------------------------------------


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


def build_connection(spec: MCPServerSpec) -> Connection | None:
    """Build the adapter connection mapping for one server spec.

    env (stdio) and headers (sse/http) values support ``${ENV_VAR}``
    placeholders resolved from os.environ; a missing variable excludes the
    whole server (None).

    Args:
        spec: The transport-pure server configuration.

    Returns:
        Connection config dict, or None when the server must be excluded.
    """
    if spec.transport == "stdio":
        if not spec.command:
            logger.error("mcp_server_config_invalid", server=spec.name, detail="stdio transport requires command")
            return None
        env: dict[str, str] = {}
        for key, raw_value in spec.env.items():
            resolved = _resolve_placeholders(spec.name, f"env.{key}", str(raw_value))
            if resolved is None:
                return None
            env[key] = resolved
        stdio_connection: StdioConnection = {
            "transport": "stdio",
            "command": spec.command,
            "args": list(spec.args),
        }
        if env:
            stdio_connection["env"] = env
        return stdio_connection

    if spec.transport in ("sse", "http"):
        if not spec.url:
            logger.error("mcp_server_config_invalid", server=spec.name, detail=f"{spec.transport} transport requires url")
            return None
        headers: dict[str, str] = {}
        for key, raw_value in spec.headers.items():
            resolved = _resolve_placeholders(spec.name, f"headers.{key}", str(raw_value))
            if resolved is None:
                return None
            headers[key] = resolved
        if spec.transport == "sse":
            sse_connection: SSEConnection = {"transport": "sse", "url": spec.url}
            if headers:
                sse_connection["headers"] = headers
            return sse_connection
        # "http" is accepted at runtime as an alias of "streamable_http" (sessions.py)
        http_connection: StreamableHttpConnection = {
            "transport": "http",  # pyright: ignore[reportAssignmentType] — runtime alias of streamable_http
            "url": spec.url,
        }
        if headers:
            http_connection["headers"] = headers
        return http_connection

    logger.error("mcp_server_config_invalid", server=spec.name, detail=f"unsupported transport {spec.transport}")
    return None


# ---------------------------------------------------------------------------
# Process-level per-server session pool (worker ownership model)
# ---------------------------------------------------------------------------


@dataclass
class _PooledSession:
    """One pooled MCP session and its (raw + namespaced) tool instances."""

    server_name: str
    spec_hash: str
    worker: asyncio.Task[None]
    stop: asyncio.Event
    session: ClientSession
    raw_tools: list[BaseTool]
    raw_by_name: dict[str, BaseTool]
    tools: list[BaseTool]
    last_used: float


@dataclass
class _SessionBuild:
    """One in-flight (or parked) session build owned by its worker task.

    Attributes:
        spec: Transport-pure server spec (namespace + dynamic re-resolution).
        spec_hash: Connection hash the build was started for.
        reason: Rebuild reason metric label (new | config_changed | recovered).
        worker: Long-lived task owning the session CM (enter + exit inside).
        stop: Set to ask the worker to close its session and exit gracefully.
        ready: Resolves with (session, raw_tools) once initialized, or with
            the open failure after retries (consumed even without waiters).
    """

    spec: MCPServerSpec
    spec_hash: str
    reason: str
    worker: asyncio.Task[None]
    stop: asyncio.Event
    ready: asyncio.Future[tuple[ClientSession, list[BaseTool]]]

    @property
    def server_name(self) -> str:
        """Server name of the build (pool key + tool namespace)."""
        return self.spec.name


# Process-level pool: one long-lived session per server name. All agents in
# this process share the same session (single worker deployment => the whole
# application shares it); the pool is keyed by server name and invalidated on
# connection-hash change, transport failure or idle TTL expiry.
_sessions: dict[str, _PooledSession] = {}
# Last seen connection hash per server name (drives rebuild reasons).
_server_hashes: dict[str, str] = {}
# Singleflight lock per server name: concurrent cold-cache loads dedup to one.
_locks: dict[str, asyncio.Lock] = {}
# Live session builds per server name: registered at start, removed when the
# worker task exits (an adopted build stays registered while its worker is
# parked — the registry tracks exactly the live workers).
_building: dict[str, _SessionBuild] = {}
# Strong references to fire-and-forget finalize tasks (asyncio keeps none).
_finalize_tasks: set[asyncio.Task[None]] = set()


def _lock_for(server_name: str) -> asyncio.Lock:
    """Return the per-server singleflight lock, creating it on first use."""
    lock = _locks.get(server_name)
    if lock is None:
        lock = asyncio.Lock()
        _locks[server_name] = lock
    return lock


async def _retry_sleep(seconds: float) -> None:
    """Awaitable backoff hook for tenacity (monkeypatched in unit tests).

    Args:
        seconds: Number of seconds to sleep before the next attempt.
    """
    await asyncio.sleep(seconds)


async def _await_worker_exit(worker: asyncio.Task[None], server_name: str) -> str:
    """Wait for a session worker to exit, cancelling after the stop timeout.

    Args:
        worker: The worker task to await (must belong to the running loop).
        server_name: Owning server name (for logging).

    Returns:
        The stop outcome: "graceful", "cancelled" (ended cancelled without
        our fallback), "crashed" (ended with an exception), "timeout_cancelled"
        (force-cancelled after ``MCP_SESSION_STOP_TIMEOUT`` — the cancel is
        still delivered inside the worker, preserving task affinity) or
        "foreign_loop" (worker of a dead/foreign loop: dropped, not awaited).
    """
    if worker.get_loop() is not asyncio.get_running_loop():
        logger.warning("mcp_session_worker_foreign_loop_dropped", server=server_name)
        return "foreign_loop"
    done, pending = await asyncio.wait({worker}, timeout=settings.MCP_SESSION_STOP_TIMEOUT)
    if pending:
        worker.cancel()
        await asyncio.gather(worker, return_exceptions=True)
        logger.warning(
            "mcp_session_stop_timeout",
            server=server_name,
            timeout_seconds=settings.MCP_SESSION_STOP_TIMEOUT,
        )
        return "timeout_cancelled"
    if worker.cancelled():
        return "cancelled"
    if worker.exception() is not None:
        return "crashed"
    return "graceful"


async def _stop_build(build: _SessionBuild) -> None:
    """Stop a build's worker: pre-arm stop, cancel if still opening, await bounded."""
    if build.worker.get_loop() is asyncio.get_running_loop():
        build.stop.set()
        if not build.ready.done():
            build.worker.cancel()
    await _await_worker_exit(build.worker, build.server_name)


async def _close_session(entry: _PooledSession) -> None:
    """Close the pooled session worker, logging (never raising) teardown errors.

    The worker unwinds the session context manager itself (task affinity);
    close-phase failures are logged by the worker as ``mcp_session_close_failed``.
    """
    if entry.worker.get_loop() is asyncio.get_running_loop():
        entry.stop.set()
    outcome = await _await_worker_exit(entry.worker, entry.server_name)
    mcp_session_stop_total.labels(outcome=outcome).inc()


async def _invalidate(server_name: str) -> None:
    """Drop and close the pooled session of a server (broken connection)."""
    entry = _sessions.pop(server_name, None)
    if entry is not None:
        await _close_session(entry)


def _entry_is_current(entry: _PooledSession, spec_hash: str) -> bool:
    """Return True when the entry matches the config hash and is not idle-expired."""
    return entry.spec_hash == spec_hash and (time.monotonic() - entry.last_used) <= settings.MCP_SESSION_IDLE_TTL


async def _open_attempt(
    spec: MCPServerSpec, connection: Connection
) -> tuple[AbstractAsyncContextManager[ClientSession], ClientSession, list[BaseTool]]:
    """Open + initialize one session attempt, keeping the context entered.

    Each tenacity attempt opens a fresh session context manager. On success
    the context stays entered — ownership transfers to the caller (the
    session worker), which exits it later in this same task. On failure the
    context is unwound right here with the REAL exception, so the adapter
    transports' anyio task groups never exit in a foreign task and a
    transport-level cancel surfaces as its root-cause ``ExceptionGroup`` (an
    Exception subclass -> retryable / degradable), while a genuine external
    cancellation propagates unchanged. Failed attempts never leak transports
    or stdio child processes.

    Returns:
        The entered context manager, its session and the raw tools.

    Raises:
        Exception: Whatever the transport, initialize or tool listing raised
            (a transport-level cancel surfaces as its root-cause group).
    """
    cm = create_session(connection)
    session = await cm.__aenter__()
    try:
        await session.initialize()
        raw_tools = await load_mcp_tools(session, server_name=spec.name)
    except BaseException as exc:
        # Unwind in-task with the real exception; the teardown may itself
        # raise the root-cause ExceptionGroup, which replaces this exception.
        await cm.__aexit__(type(exc), exc, exc.__traceback__)
        raise
    return cm, session, raw_tools


async def _open_with_retry(
    spec: MCPServerSpec, connection: Connection
) -> tuple[AbstractAsyncContextManager[ClientSession], ClientSession, list[BaseTool]]:
    """Open a session with tenacity exponential backoff (3 attempts)."""
    retrying = AsyncRetrying(
        stop=stop_after_attempt(3),
        wait=wait_exponential(multiplier=1, min=4, max=10),
        sleep=_retry_sleep,
        reraise=True,
    )
    return await retrying(_open_attempt, spec, connection)


async def _session_worker(
    spec: MCPServerSpec,
    connection: Connection,
    ready: asyncio.Future[tuple[ClientSession, list[BaseTool]]],
    stop: asyncio.Event,
) -> None:
    """Own one pooled session for its whole lifetime (worker ownership model).

    The session context manager is entered AND exited inside this task only:
    anyio cancel scopes / task groups (which the adapter transports and
    ClientSession wrap) must never cross task boundaries, so every close path
    — idle TTL recycle, config change, transport failure or process shutdown —
    just sets ``stop`` and lets the worker unwind the context itself.

    Lifecycle: open with retries (the context stays entered on success) ->
    publish (session, raw_tools) on ``ready`` -> park on ``stop`` -> exit the
    context manager gracefully in this task. On cancellation or an open-phase
    failure the context (when entered) is unwound here with the real
    exception before the worker ends.
    """
    cm: AbstractAsyncContextManager[ClientSession] | None = None
    try:
        cm, session, raw_tools = await _open_with_retry(spec, connection)
        if not ready.done():
            ready.set_result((session, raw_tools))
        await stop.wait()
        closing, cm = cm, None
        await closing.__aexit__(None, None, None)
    except asyncio.CancelledError as exc:
        # Stop-timeout fallback cancel or an external event-loop cancel; the
        # context (when entered) unwinds in-task with the real cancellation.
        if cm is not None:
            try:
                await cm.__aexit__(asyncio.CancelledError, exc, exc.__traceback__)
            except asyncio.CancelledError:
                raise
            except BaseException:  # noqa: BLE001 — best-effort close, cancel still propagates
                logger.exception("mcp_session_close_failed", server=spec.name)
        # When the session never opened, waiters must see the genuine cancellation.
        if not ready.done():
            ready.set_exception(exc)
        logger.warning("mcp_session_worker_cancelled", server=spec.name)
        raise
    except BaseException as exc:  # noqa: BLE001 — logged, never raised to callers
        if cm is not None:
            try:
                await cm.__aexit__(type(exc), exc, exc.__traceback__)
            except BaseException:  # noqa: BLE001 — best-effort close, original error wins
                logger.exception("mcp_session_close_failed", server=spec.name)
        opening = not ready.done()
        if opening:
            ready.set_exception(exc)
        logger.exception(
            "mcp_session_open_failed" if opening else "mcp_session_close_failed",
            server=spec.name,
        )


def _pool_build_locked(build: _SessionBuild) -> _PooledSession | None:
    """Register a finished build as the pooled entry (idempotent by worker).

    The caller must hold the per-server pool lock and ``build.ready`` must
    hold a successful result. Emits the rebuild metric + build log exactly
    once per build (worker-identity check). Returns None when the build was
    stopped (or its worker died) while finishing — a stopped build must
    never (re-)enter the pool, its stopper owns its teardown.
    """
    existing = _sessions.get(build.server_name)
    if existing is not None and existing.worker is build.worker:
        return existing
    if build.stop.is_set() or build.worker.done():
        return None
    session, raw_tools = build.ready.result()
    raw_by_name = {tool.name: tool for tool in raw_tools}
    entry = _PooledSession(
        server_name=build.server_name,
        spec_hash=build.spec_hash,
        worker=build.worker,
        stop=build.stop,
        session=session,
        raw_tools=raw_tools,
        raw_by_name=raw_by_name,
        tools=[],
        last_used=time.monotonic(),
    )
    entry.tools = [_wrap_pooled_tool(build.spec, raw_tool) for raw_tool in raw_tools]
    _sessions[build.server_name] = entry
    _server_hashes[build.server_name] = build.spec_hash
    mcp_client_rebuild_total.labels(reason=build.reason).inc()
    logger.info(
        "mcp_client_built",
        server=build.server_name,
        reason=build.reason,
        spec_hash=build.spec_hash,
        tool_count=len(entry.tools),
    )
    return entry


async def _finalize_build(build: _SessionBuild) -> None:
    """Adopt a finished build into the pool even when every waiter vanished.

    Guarantees no orphaned worker: a successful build is always pooled either
    by its waiter or here — whichever gets the per-server lock first. Builds
    that were stopped while finishing (e.g. a concurrent shutdown) are never
    pooled; their teardown is idempotently confirmed here.
    """
    async with _lock_for(build.server_name):
        existing = _sessions.get(build.server_name)
        if existing is not None and existing.worker is build.worker:
            return
        if _building.get(build.server_name) is not build:
            # Superseded by a newer build/entry while finishing: stop the
            # leftover worker instead of pooling a stale session.
            await _stop_build(build)
            return
        if _pool_build_locked(build) is None:
            await _stop_build(build)


def _start_build(spec: MCPServerSpec, connection: Connection, spec_hash: str, reason: str) -> _SessionBuild:
    """Spawn the session worker for one server and register the build.

    Args:
        spec: Transport-pure server configuration.
        connection: Pre-built adapter connection for the spec.
        spec_hash: Connection hash the build is started for.
        reason: Rebuild reason metric label (new | config_changed | recovered).

    Returns:
        The registered build (worker, stop event, ready future).
    """
    loop = asyncio.get_running_loop()
    ready: asyncio.Future[tuple[ClientSession, list[BaseTool]]] = loop.create_future()
    stop = asyncio.Event()
    worker = asyncio.create_task(_session_worker(spec, connection, ready, stop), name=f"mcp-session-{spec.name}")
    build = _SessionBuild(spec=spec, spec_hash=spec_hash, reason=reason, worker=worker, stop=stop, ready=ready)
    _building[spec.name] = build

    def _on_worker_done(_task: asyncio.Task[None]) -> None:
        if _building.get(spec.name) is build:
            del _building[spec.name]

    def _on_ready(future: asyncio.Future[tuple[ClientSession, list[BaseTool]]]) -> None:
        if future.cancelled():
            return
        if future.exception() is not None:
            return  # failed build: the worker exits on its own; nothing to adopt
        finalize = loop.create_task(_finalize_build(build), name=f"mcp-session-finalize-{spec.name}")
        _finalize_tasks.add(finalize)
        finalize.add_done_callback(_finalize_tasks.discard)

    worker.add_done_callback(_on_worker_done)
    ready.add_done_callback(_on_ready)
    return build


async def _ensure_session(spec: MCPServerSpec, connection: Connection | None = None) -> _PooledSession:
    """Return the current pooled session for the spec, rebuilding when stale.

    Fast path (no lock): a pooled entry matching the spec hash, inside the
    idle TTL and owned by a worker of this event loop is served directly.
    Slow path: per-server singleflight lock, re-check, close the superseded
    entry, reuse an in-flight build or start one, then wait for its ``ready``
    (shielded — a cancelled waiter never kills the build) and adopt it into
    the pool. Concurrent cold-cache callers therefore share one session build
    (singleflight), across waiter cancellations too.

    Args:
        spec: The transport-pure server configuration.
        connection: Pre-built connection (optional; built from the spec when
            omitted — used by the pooled tool wrapper path).

    Returns:
        The current pooled session entry.

    Raises:
        RuntimeError: When the config is excluded (unresolved placeholder) or
            the session cannot be established after retries.
    """
    spec_hash = spec.connection_hash()
    loop = asyncio.get_running_loop()

    def _serviceable(entry: _PooledSession | None) -> _PooledSession | None:
        """Return the entry when current and owned by a live worker of this loop."""
        if (
            entry is not None
            and _entry_is_current(entry, spec_hash)
            and entry.worker.get_loop() is loop
            and not entry.worker.done()
        ):
            return entry
        return None

    while True:
        entry = _serviceable(_sessions.get(spec.name))
        if entry is not None:
            entry.last_used = time.monotonic()
            return entry

        async with _lock_for(spec.name):
            entry = _serviceable(_sessions.get(spec.name))
            if entry is not None:
                entry.last_used = time.monotonic()
                return entry

            if connection is None:
                connection = build_connection(spec)
                if connection is None:
                    raise RuntimeError(f"mcp server '{spec.name}' is excluded: unresolved ${{ENV_VAR}} placeholder or invalid config")

            # Superseded entry (idle-expired / hash change / foreign loop): pop it
            # and close it BEFORE building the replacement ("close old, build new").
            previous = _sessions.pop(spec.name, None)
            previous_hash = previous.spec_hash if previous is not None else _server_hashes.get(spec.name)
            if previous_hash is None:
                reason = "new"
            elif previous_hash != spec_hash:
                reason = "config_changed"
            else:
                reason = "recovered"
            if previous is not None:
                await _close_session(previous)

            build = _building.get(spec.name)
            # Reuse only an OPENING build of this loop with a matching hash; a
            # ready build's session is the (unserviceable) pooled one just closed.
            if (
                build is None
                or build.ready.done()
                or build.spec_hash != spec_hash
                or build.worker.get_loop() is not loop
            ):
                if build is not None:
                    await _stop_build(build)
                build = _start_build(spec, connection, spec_hash, reason)

            # Shielded wait: the build outlives cancelled waiters (a finalizer
            # adopts finished builds into the pool when every waiter vanished).
            await asyncio.shield(build.ready)
            entry = _pool_build_locked(build)
            if entry is not None:
                entry.last_used = time.monotonic()
                return entry
            # The build was stopped while we waited (e.g. a concurrent
            # shutdown): loop and retry with a fresh build.


async def _invoke_raw(tool: BaseTool, arguments: dict[str, Any]) -> Any:
    """Invoke an adapter-built tool, mirroring its coroutine/output contract.

    Adapter tools are coroutine-based ``content_and_artifact`` tools: calling
    the coroutine directly preserves the (content, artifact) tuple contract.
    Sync-only tools (unit-test fakes) fall back to ``ainvoke``.
    """
    coroutine = getattr(tool, "coroutine", None)
    if coroutine is not None:
        return await coroutine(**arguments)
    return await tool.ainvoke(arguments)


def _wrap_pooled_tool(spec: MCPServerSpec, raw_tool: BaseTool) -> BaseTool:
    """Build the namespaced wrapper tool bound to the session pool.

    Every invoke resolves the *current* pooled session dynamically: after a
    transport-level failure the session is invalidated, rebuilt once and the
    call retried — so tool instances held by cached compiled graphs self-heal
    instead of failing until the next config change.

    Args:
        spec: Owning server spec (namespace + dynamic session resolution).
        raw_tool: Adapter-built session-bound tool (raw, un-namespaced name).

    Returns:
        A StructuredTool named ``{server}__{tool}`` with the same schema,
        response format and error handling as the raw tool.
    """
    raw_name = raw_tool.name

    async def _pooled_coroutine(**kwargs: Any) -> Any:
        entry = await _ensure_session(spec)
        current = entry.raw_by_name.get(raw_name)
        if current is None:
            raise RuntimeError(f"tool '{raw_name}' is no longer exposed by mcp server '{spec.name}'")
        try:
            return await _invoke_raw(current, kwargs)
        except Exception:  # noqa: BLE001 — invalidate + one rebuild + one retry
            logger.warning("mcp_pooled_session_invalidated", server=spec.name, tool=raw_name)
            await _invalidate(spec.name)
            retried_entry = await _ensure_session(spec)
            replacement = retried_entry.raw_by_name.get(raw_name)
            if replacement is None:
                raise
            return await _invoke_raw(replacement, kwargs)

    wrapper_kwargs: dict[str, Any] = {}
    if raw_tool.args_schema is not None:
        # Copy the schema verbatim; when absent, StructuredTool infers one
        # from the coroutine signature (no-arg tools).
        wrapper_kwargs["args_schema"] = raw_tool.args_schema
    return StructuredTool(
        name=namespaced_tool_name(spec.name, raw_name),
        description=raw_tool.description,
        coroutine=_pooled_coroutine,
        response_format=raw_tool.response_format,
        handle_tool_error=raw_tool.handle_tool_error,
        metadata=raw_tool.metadata,
        **wrapper_kwargs,
    )


async def load_server_tools(spec: MCPServerSpec) -> list[BaseTool] | None:
    """Load the namespaced tools of one server through the pooled session.

    Args:
        spec: The transport-pure server configuration.

    Returns:
        Namespaced tool list on success (possibly empty), or None when the
        server was excluded by config resolution or failed after retries;
        errors are never cached (a recovered server is picked up next call).
    """
    connection = build_connection(spec)
    if connection is None:
        mcp_tools_loaded_total.labels(server=spec.name, status="error").inc()
        return None

    try:
        entry = await _ensure_session(spec, connection)
    except Exception:  # noqa: BLE001 — per-server degradation must not block the catalog
        logger.exception("mcp_tools_load_failed", server=spec.name)
        mcp_tools_loaded_total.labels(server=spec.name, status="error").inc()
        return None

    mcp_tools_loaded_total.labels(server=spec.name, status="success").inc()
    logger.info("mcp_tools_loaded", server=spec.name, tool_count=len(entry.tools))
    return entry.tools


async def shutdown_mcp_sessions() -> None:
    """Close every pooled session and in-flight build, clear all pool state.

    Workers stop on their own stop event (graceful, task-affinity safe); the
    bounded wait force-cancels stragglers after ``MCP_SESSION_STOP_TIMEOUT``.
    """
    entries = list(_sessions.values())
    _sessions.clear()
    _server_hashes.clear()
    builds = list(_building.values())
    loop = asyncio.get_running_loop()
    for entry in entries:
        if entry.worker.get_loop() is loop:
            entry.stop.set()
    for build in builds:
        if build.worker.get_loop() is loop:
            build.stop.set()
            if not build.ready.done():
                build.worker.cancel()
    # Adopted builds appear in both registries; dedupe workers so each stop is
    # awaited — and counted — exactly once.
    workers: dict[asyncio.Task[None], str] = {entry.worker: entry.server_name for entry in entries}
    for build in builds:
        workers.setdefault(build.worker, build.server_name)
    outcomes = await asyncio.gather(*(_await_worker_exit(worker, name) for worker, name in workers.items()))
    for outcome in outcomes:
        mcp_session_stop_total.labels(outcome=outcome).inc()
    logger.info("mcp_sessions_shutdown_complete", session_count=len(entries))


# ---------------------------------------------------------------------------
# One-shot helpers (management/debug API; never touch the pool)
# ---------------------------------------------------------------------------


async def _one_shot_load(
    spec: MCPServerSpec, connection: Connection, *, handle_tool_errors: bool = True
) -> list[BaseTool]:
    """Open an ephemeral session, initialize it and load the raw tools.

    Only safe for metadata reads (names/descriptions/schemas): the returned
    tools are bound to the session and stop working once its context manager
    exits — invocations must go through ``_one_shot_call`` instead.

    Raises:
        Exception: Whatever the transport, initialize or tool listing raised.
    """
    async with create_session(connection) as session:
        await session.initialize()
        return await load_mcp_tools(session, server_name=spec.name, handle_tool_errors=handle_tool_errors)


async def _one_shot_call(
    spec: MCPServerSpec,
    connection: Connection,
    tool_name: str,
    arguments: dict[str, Any],
) -> Any:
    """Invoke one tool inside a dedicated ephemeral session.

    The whole call (list + match + invoke) must happen while the session's
    transport is still open — adapter tools are bound to their session, so
    invoking after the context exits fails with ClosedResourceError.

    Request-level errors (unknown tool, missing required arguments) are
    stashed and re-raised only after the context exits cleanly: raising inside
    the ``async with`` block makes the session's task-group teardown replace
    the exception, which would surface 422-class failures as 502. Type checks
    stay authoritative on the server: its rejections come back as MCP
    ``isError`` results and surface as upstream failures (502).

    Raises:
        ValueError: When no tool with the requested name is exposed or a
            required argument is missing.
        ValidationError: When the arguments do not match the tool schema.
        Exception: Whatever the transport or the tool raised.
    """
    result: Any = None
    known: list[str] = []
    request_error: ValidationError | None = None
    missing_args: list[str] = []
    found = False
    async with create_session(connection) as session:
        await session.initialize()
        tools = await load_mcp_tools(session, server_name=spec.name, handle_tool_errors=False)
        match = next((tool for tool in tools if tool.name == tool_name), None)
        if match is None:
            known = sorted(tool.name for tool in tools)
        else:
            found = True
            schema = match.args_schema
            if isinstance(schema, dict):
                missing_args = [key for key in schema.get("required", []) if key not in arguments]
            if not missing_args:
                # Surface argument validation errors instead of the default
                # handle_validation_error conversion into error content.
                match.handle_validation_error = False  # pyright: ignore[reportAttributeAccessIssue]
                try:
                    result = await match.ainvoke(arguments)
                except ValidationError as exc:
                    request_error = exc
    if not found:
        raise ValueError(
            f"unknown tool '{tool_name}' on mcp server '{spec.name}'; known tools: {', '.join(known)}"
        )
    if missing_args:
        raise ValueError(
            f"missing required argument(s) for tool '{tool_name}' on mcp server '{spec.name}': "
            f"{', '.join(repr(key) for key in missing_args)}"
        )
    if request_error is not None:
        raise request_error
    return result


async def probe_tools(spec: MCPServerSpec, timeout_seconds: float) -> list[str] | None:
    """Probe the raw tool names of a candidate server (silent degradation).

    Used for fail-fast collision validation before a configuration is
    persisted; timeouts and failures degrade to None, mirroring the
    per-server degradation policy of ``load_server_tools``.

    Args:
        spec: Candidate (possibly unpersisted) server spec.
        timeout_seconds: Probe budget.

    Returns:
        The raw tool names exposed by the server, or None when they could not
        be loaded (excluded config, connection failure or timeout).
    """
    connection = build_connection(spec)
    if connection is None:
        return None

    async def _load() -> list[str]:
        tools = await _one_shot_load(spec, connection)
        return [tool.name for tool in tools]

    try:
        return await asyncio.wait_for(_load(), timeout=timeout_seconds)
    except TimeoutError:
        logger.warning("mcp_server_tool_probe_timeout", server=spec.name, timeout_seconds=timeout_seconds)
        return None
    except Exception:  # noqa: BLE001 — probe degradation must not block CRUD
        logger.exception("mcp_server_tool_probe_failed", server=spec.name)
        return None


def _args_schema_dict(tool: BaseTool) -> dict[str, Any]:
    """Extract the JSON-schema dict of a tool's arguments."""
    schema = getattr(tool, "args_schema", None)
    if isinstance(schema, dict):
        return schema
    model = getattr(tool, "tool_call_schema", None)
    if model is not None:
        return model.model_json_schema()
    return {}


async def list_tools(spec: MCPServerSpec, timeout_seconds: float) -> list[ToolSummary]:
    """List the current raw tools of a server via an ephemeral session.

    Unlike ``probe_tools`` failures are explicit (debug endpoint semantics).

    Args:
        spec: Server spec to inspect (enabled or not).
        timeout_seconds: Listing budget.

    Returns:
        Tool summaries (raw names, descriptions, JSON schemas).

    Raises:
        ValueError: The server config is excluded (unresolved placeholder).
        TimeoutError: The server did not answer within the budget.
        MCPUpstreamError: The server failed while listing tools.
    """
    connection = build_connection(spec)
    if connection is None:
        raise ValueError(f"mcp server '{spec.name}' has an unresolved ${{ENV_VAR}} placeholder or invalid config")

    async def _load() -> list[ToolSummary]:
        tools = await _one_shot_load(spec, connection)
        return [
            ToolSummary(name=tool.name, description=tool.description or "", args_schema=_args_schema_dict(tool))
            for tool in tools
        ]

    try:
        return await asyncio.wait_for(_load(), timeout=timeout_seconds)
    except TimeoutError:
        logger.warning("mcp_server_tool_list_timeout", server=spec.name, timeout_seconds=timeout_seconds)
        raise
    except ValueError:
        raise
    except Exception as exc:  # noqa: BLE001 — surfaced as 502 by the API layer
        raise MCPUpstreamError(f"mcp server '{spec.name}' failed to list tools: {exc}") from exc


async def call_tool(
    spec: MCPServerSpec,
    tool_name: str,
    arguments: dict[str, Any],
    timeout_seconds: float,
) -> Any:
    """Invoke one tool of a server via an ephemeral session (debug endpoint).

    Args:
        spec: Server spec hosting the tool.
        tool_name: Raw (un-namespaced) tool name.
        arguments: Tool arguments validated against the tool schema.
        timeout_seconds: Call budget.

    Returns:
        The tool content (str or content-block list).

    Raises:
        ValueError: Excluded config, unknown tool name or invalid arguments.
        TimeoutError: The call exceeded the budget.
        MCPUpstreamError: The tool executed with an error or the server failed.
    """
    connection = build_connection(spec)
    if connection is None:
        raise ValueError(f"mcp server '{spec.name}' has an unresolved ${{ENV_VAR}} placeholder or invalid config")

    async def _run() -> Any:
        return await _one_shot_call(spec, connection, tool_name, arguments)

    try:
        return await asyncio.wait_for(_run(), timeout=timeout_seconds)
    except (TimeoutError, ValueError, ValidationError):
        raise
    except Exception as exc:  # noqa: BLE001 — surfaced as 502 by the API layer
        raise MCPUpstreamError(f"mcp tool '{namespaced_tool_name(spec.name, tool_name)}' failed: {exc}") from exc
