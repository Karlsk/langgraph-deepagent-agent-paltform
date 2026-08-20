"""Unit tests for app.core.mcp_client.

Zero real network / zero real MCP processes: the module-level
``create_session`` and ``load_mcp_tools`` symbols are replaced with in-memory
fakes via monkeypatch; tenacity backoff sleep is stubbed to a no-op.

Coverage: connection building (three transports + placeholder resolution),
the process-level per-server session pool (reuse / rebuild / invalidation /
idle TTL / singleflight / shutdown), namespaced wrappers with self-healing
retries, and the one-shot helpers (probe / list / call).
"""

import asyncio
import time
from collections.abc import Generator
from typing import Any, ClassVar

import pytest
from langchain_core.tools import BaseTool, StructuredTool
from pydantic import BaseModel

from app.core import mcp_client
from app.core.config import settings
from app.core.mcp_client import MCPServerSpec
from app.core.metrics import mcp_client_rebuild_total, mcp_tools_loaded_total

pytestmark = pytest.mark.unit


# ---------------------------------------------------------------------------
# Fakes & helpers
# ---------------------------------------------------------------------------


class _NoArgs(BaseModel):
    """Empty argument schema shared by the fake tools."""


# JSON-schema dict like the ones adapter tools carry (required-list tests).
_TEXT_SCHEMA: dict[str, Any] = {
    "type": "object",
    "properties": {"text": {"type": "string"}},
    "required": ["text"],
}


def _make_tool(server: str, name: str, session: "FakeClientSession | None" = None) -> BaseTool:
    """Build a coroutine-based fake tool whose behavior is table-driven.

    When a session is provided the tool is bound to it (like adapter tools):
    invoking it after the session closed raises ClosedResourceError-style,
    mirroring the real stdio/sse transport semantics. The optional behavior
    ``args_schema`` entry swaps in a stricter argument model (validation tests).
    """
    behavior = _BEHAVIORS.get(server, {})

    async def _acall(**_: Any) -> str:
        if session is not None and session.closed:
            raise RuntimeError(f"session closed before call of {server}/{name}")
        if behavior.get("call_fail_next"):
            behavior["call_fail_next"] = False
            raise RuntimeError(f"fake connection lost for {server}/{name}")
        return f"{server}::{name}"

    schema: type[BaseModel] = behavior.get("args_schema", _NoArgs)
    return StructuredTool(name=name, description=f"fake tool {name}", coroutine=_acall, args_schema=schema)


class FakeClientSession:
    """In-memory stand-in for mcp.ClientSession (records lifecycle)."""

    instances: ClassVar[list["FakeClientSession"]] = []

    def __init__(self) -> None:
        """Initialize lifecycle flags and register the instance."""
        self.initialized = False
        self.closed = False
        self.connection: dict[str, Any] | None = None
        FakeClientSession.instances.append(self)

    async def initialize(self) -> None:
        """Mark the session as initialized."""
        self.initialized = True


class FakeSessionCM:
    """Async-context-manager stand-in for the adapter session factory."""

    def __init__(self, connection: dict[str, Any]) -> None:
        """Create the fake session and remember the connection it serves."""
        self.connection = connection
        self.session = FakeClientSession()
        self.session.connection = connection

    async def __aenter__(self) -> FakeClientSession:
        """Return the wrapped fake session."""
        return self.session

    async def __aexit__(self, *exc_info: Any) -> bool:
        """Mark the session closed; mirror real transports on error paths.
    
        Real adapter session managers tear down an anyio task group on exit:
        exceptions raised *inside* the context get replaced by
        "unhandled errors in a TaskGroup" teardown errors. Reproducing that here
        guards request-level errors (422-class) from being swallowed into 502s.
        """
        self.session.closed = True
        if exc_info[0] is not None and exc_info[0] is not asyncio.CancelledError:
            raise RuntimeError("unhandled errors in a TaskGroup (1 sub-exception)") from exc_info[1]
        return False


def _fake_create_session(connection: dict[str, Any]) -> FakeSessionCM:
    """Session factory fake (mirrors langchain_mcp_adapters.sessions.create_session)."""
    return FakeSessionCM(connection)


# Per-server behavior table: tool_names / fail_times / delay / call_fail_next.
_BEHAVIORS: dict[str, dict[str, Any]] = {}
# Number of fake load_mcp_tools invocations per server name.
_load_calls: dict[str, int] = {}


async def _fake_load_mcp_tools(
    session: Any, server_name: str | None = None, handle_tool_errors: bool = True
) -> list[BaseTool]:
    """Serve session-bound fake tools per the behavior table; raise while the fail budget remains."""
    del handle_tool_errors
    assert server_name is not None
    count = _load_calls.get(server_name, 0) + 1
    _load_calls[server_name] = count
    behavior = _BEHAVIORS.get(server_name, {})
    if behavior.get("delay"):
        await asyncio.sleep(behavior["delay"])
    if count <= behavior.get("fail_times", 0):
        raise RuntimeError(f"fake mcp failure for {server_name}")
    return [_make_tool(server_name, tool_name, session=session) for tool_name in behavior.get("tool_names", [])]


@pytest.fixture(autouse=True)
def _patch_adapters(monkeypatch: pytest.MonkeyPatch) -> Generator[None, None, None]:
    """Replace the adapter entry points and reset all module/pool state."""
    FakeClientSession.instances = []
    _BEHAVIORS.clear()
    _load_calls.clear()
    monkeypatch.setattr(mcp_client, "create_session", _fake_create_session)
    monkeypatch.setattr(mcp_client, "load_mcp_tools", _fake_load_mcp_tools)

    async def _no_sleep(_seconds: float) -> None:
        return None

    monkeypatch.setattr(mcp_client, "_retry_sleep", _no_sleep)
    _reset_pool()
    yield
    _reset_pool()


def _reset_pool() -> None:
    """Clear every module-level pool state kept by mcp_client."""
    mcp_client._sessions.clear()  # noqa: SLF001 — test introspection
    mcp_client._server_hashes.clear()  # noqa: SLF001 — test introspection
    mcp_client._locks.clear()  # noqa: SLF001 — test introspection


def _stdio_spec(name: str = "srv", command: str = "python", args: list[str] | None = None, env: dict[str, str] | None = None) -> MCPServerSpec:
    """Build a stdio server spec."""
    return MCPServerSpec(name=name, transport="stdio", command=command, args=args or ["-m", name], env=env or {})


def _sse_spec(name: str = "srv", url: str = "https://sse.example.com/sse", headers: dict[str, str] | None = None) -> MCPServerSpec:
    """Build an sse server spec."""
    return MCPServerSpec(name=name, transport="sse", url=url, headers=headers or {})


def _http_spec(name: str = "srv", url: str = "https://api.example.com/mcp", headers: dict[str, str] | None = None) -> MCPServerSpec:
    """Build an http server spec."""
    return MCPServerSpec(name=name, transport="http", url=url, headers=headers or {})


def _counter_value(counter: Any, **labels: str) -> float:
    """Read the current value of a labeled prometheus counter."""
    return float(counter.labels(**labels)._value.get())  # noqa: SLF001 — test introspection


def _expire_idle(entry: Any) -> None:
    """Push a pooled entry's last_used beyond the idle TTL."""
    entry.last_used = time.monotonic() - (settings.MCP_SESSION_IDLE_TTL + 1.0)


# ---------------------------------------------------------------------------
# Connection building (pure function)
# ---------------------------------------------------------------------------


def test_build_connection_stdio_resolves_env_placeholders(monkeypatch: pytest.MonkeyPatch) -> None:
    """Stdio connections resolve ${ENV_VAR} env values from os.environ."""
    monkeypatch.setenv("TEST_MCP_TOKEN", "sekrit")
    connection = mcp_client.build_connection(_stdio_spec(env={"TOKEN": "${TEST_MCP_TOKEN}"}))
    assert connection is not None
    assert connection["transport"] == "stdio"
    assert connection["command"] == "python"
    assert connection["args"] == ["-m", "srv"]
    assert connection["env"] == {"TOKEN": "sekrit"}


def test_build_connection_sse_and_http_shapes(monkeypatch: pytest.MonkeyPatch) -> None:
    """Sse maps to the sse transport; http stays the streamable-http runtime alias."""
    monkeypatch.setenv("TEST_MCP_TOKEN", "sekrit")
    sse = mcp_client.build_connection(_sse_spec(headers={"Authorization": "Bearer ${TEST_MCP_TOKEN}"}))
    assert sse is not None
    assert sse["transport"] == "sse"
    assert sse["url"] == "https://sse.example.com/sse"
    assert sse["headers"] == {"Authorization": "Bearer sekrit"}

    http = mcp_client.build_connection(_http_spec())
    assert http is not None
    assert http["transport"] == "http"
    assert http["url"] == "https://api.example.com/mcp"


def test_build_connection_missing_placeholder_excludes_server(monkeypatch: pytest.MonkeyPatch) -> None:
    """A missing ${VAR} excludes the whole server (None)."""
    monkeypatch.delenv("TEST_MCP_MISSING_VAR", raising=False)
    assert mcp_client.build_connection(_stdio_spec(env={"TOKEN": "${TEST_MCP_MISSING_VAR}"})) is None
    assert mcp_client.build_connection(_sse_spec(headers={"X-Key": "${TEST_MCP_MISSING_VAR}"})) is None


def test_build_connection_missing_required_fields_returns_none() -> None:
    """Stdio without command and sse/http without url are excluded."""
    assert mcp_client.build_connection(MCPServerSpec(name="x", transport="stdio")) is None
    assert mcp_client.build_connection(MCPServerSpec(name="x", transport="sse")) is None
    assert mcp_client.build_connection(MCPServerSpec(name="x", transport="http")) is None
    assert mcp_client.build_connection(MCPServerSpec(name="x", transport="carrier-pigeon")) is None


def test_namespaced_tool_name_format() -> None:
    """Tool names use the double-underscore server namespace."""
    assert mcp_client.namespaced_tool_name("weather", "get_forecast") == "weather__get_forecast"


# ---------------------------------------------------------------------------
# Pooled loading: reuse, degradation, naming
# ---------------------------------------------------------------------------


def test_load_server_tools_namespaces_tools_and_reuses_session() -> None:
    """Two loads share one pooled session and one underlying load call."""
    _BEHAVIORS["srv"] = {"tool_names": ["alpha", "beta"]}

    first = asyncio.run(mcp_client.load_server_tools(_stdio_spec()))
    second = asyncio.run(mcp_client.load_server_tools(_stdio_spec()))

    assert [tool.name for tool in first] == ["srv__alpha", "srv__beta"]
    assert second is first  # same pooled entry => same tool list instance
    assert len(FakeClientSession.instances) == 1
    assert FakeClientSession.instances[0].initialized
    assert _load_calls["srv"] == 1
    assert _counter_value(mcp_tools_loaded_total, server="srv", status="success") >= 1


def test_load_failure_degrades_after_three_attempts() -> None:
    """A failing server degrades to None after exactly 3 tenacity attempts."""
    _BEHAVIORS["broken"] = {"fail_times": 99}
    assert asyncio.run(mcp_client.load_server_tools(_stdio_spec("broken"))) is None
    assert _load_calls["broken"] == 3
    assert _counter_value(mcp_tools_loaded_total, server="broken", status="error") >= 1
    assert FakeClientSession.instances[-1].closed  # failed attempts never leak sessions


def test_load_error_is_not_cached() -> None:
    """After a failed load, a recovered server is picked up on the next call."""
    _BEHAVIORS["flaky"] = {"fail_times": 99}
    assert asyncio.run(mcp_client.load_server_tools(_stdio_spec("flaky"))) is None

    _BEHAVIORS["flaky"] = {"fail_times": 0, "tool_names": ["recover"]}
    tools = asyncio.run(mcp_client.load_server_tools(_stdio_spec("flaky")))
    assert [tool.name for tool in tools] == ["flaky__recover"]


def test_unresolved_placeholder_load_returns_none_without_connecting(monkeypatch: pytest.MonkeyPatch) -> None:
    """An excluded config never opens a session."""
    monkeypatch.delenv("TEST_MCP_MISSING_VAR", raising=False)
    result = asyncio.run(mcp_client.load_server_tools(_stdio_spec(env={"TOKEN": "${TEST_MCP_MISSING_VAR}"})))
    assert result is None
    assert FakeClientSession.instances == []


# ---------------------------------------------------------------------------
# Pool lifecycle: rebuild on config change, idle TTL, shutdown, singleflight
# ---------------------------------------------------------------------------


def test_config_change_closes_old_session_and_rebuilds() -> None:
    """A changed connection projection closes the old session and rebuilds."""
    _BEHAVIORS["srv"] = {"tool_names": ["alpha"]}
    new_before = _counter_value(mcp_client_rebuild_total, reason="new")
    changed_before = _counter_value(mcp_client_rebuild_total, reason="config_changed")

    asyncio.run(mcp_client.load_server_tools(_stdio_spec(args=["-m", "v1"])))
    asyncio.run(mcp_client.load_server_tools(_stdio_spec(args=["-m", "v2"])))

    assert len(FakeClientSession.instances) == 2
    assert FakeClientSession.instances[0].closed
    assert not FakeClientSession.instances[1].closed
    assert _counter_value(mcp_client_rebuild_total, reason="new") == new_before + 1
    assert _counter_value(mcp_client_rebuild_total, reason="config_changed") == changed_before + 1


def test_idle_ttl_expiry_lazily_rebuilds() -> None:
    """An idle-expired entry is closed and rebuilt on the next acquire."""
    _BEHAVIORS["srv"] = {"tool_names": ["alpha"]}
    asyncio.run(mcp_client.load_server_tools(_stdio_spec()))
    entry = mcp_client._sessions["srv"]  # noqa: SLF001 — test introspection
    _expire_idle(entry)

    asyncio.run(mcp_client.load_server_tools(_stdio_spec()))

    assert len(FakeClientSession.instances) == 2
    assert FakeClientSession.instances[0].closed


def test_shutdown_mcp_sessions_closes_everything() -> None:
    """Shutdown closes every pooled session and clears pool state."""
    _BEHAVIORS["a"] = {"tool_names": ["t"]}
    _BEHAVIORS["b"] = {"tool_names": ["t"]}
    asyncio.run(mcp_client.load_server_tools(_stdio_spec("a")))
    asyncio.run(mcp_client.load_server_tools(_http_spec("b")))

    asyncio.run(mcp_client.shutdown_mcp_sessions())

    assert all(instance.closed for instance in FakeClientSession.instances)
    assert mcp_client._sessions == {}  # noqa: SLF001 — test introspection
    assert mcp_client._server_hashes == {}  # noqa: SLF001 — test introspection


def test_concurrent_loads_singleflight_to_one_session() -> None:
    """Concurrent cold-cache loads of one server build exactly one session."""
    _BEHAVIORS["srv"] = {"tool_names": ["alpha"], "delay": 0.05}

    async def _load_twice() -> tuple[Any, Any]:
        return await asyncio.gather(
            mcp_client.load_server_tools(_stdio_spec()),
            mcp_client.load_server_tools(_stdio_spec()),
        )

    first, second = asyncio.run(_load_twice())

    assert first is not None and second is not None
    assert first is second
    assert len(FakeClientSession.instances) == 1
    assert _load_calls["srv"] == 1


# ---------------------------------------------------------------------------
# Pooled wrappers: self-healing call path
# ---------------------------------------------------------------------------


def test_call_failure_invalidates_and_retries_on_rebuilt_session() -> None:
    """A transport-level call failure rebuilds the session and retries once."""
    _BEHAVIORS["srv"] = {"tool_names": ["alpha"]}
    tools = asyncio.run(mcp_client.load_server_tools(_stdio_spec()))
    assert tools is not None
    _BEHAVIORS["srv"]["call_fail_next"] = True

    result = asyncio.run(tools[0].ainvoke({}))

    assert result == "srv::alpha"
    assert len(FakeClientSession.instances) == 2  # invalidated + rebuilt
    assert FakeClientSession.instances[0].closed
    assert not FakeClientSession.instances[1].closed
    assert _load_calls["srv"] == 2  # tool cache refreshed with the rebuild


def test_concurrent_calls_share_one_session() -> None:
    """Two concurrent calls on the same pooled session both succeed."""
    _BEHAVIORS["srv"] = {"tool_names": ["alpha"]}
    tools = asyncio.run(mcp_client.load_server_tools(_stdio_spec()))
    assert tools is not None

    async def _call_twice() -> list[Any]:
        return await asyncio.gather(tools[0].ainvoke({}), tools[0].ainvoke({}))

    first, second = asyncio.run(_call_twice())

    assert sorted([first, second]) == ["srv::alpha", "srv::alpha"]
    assert len(FakeClientSession.instances) == 1


# ---------------------------------------------------------------------------
# One-shot helpers: probe / list_tools / call_tool (never touch the pool)
# ---------------------------------------------------------------------------


def test_probe_tools_returns_raw_names_without_pooling() -> None:
    """probe_tools returns raw (un-namespaced) names and leaves the pool empty."""
    _BEHAVIORS["srv"] = {"tool_names": ["alpha", "beta"]}
    names = asyncio.run(mcp_client.probe_tools(_stdio_spec(), timeout_seconds=5.0))
    assert names == ["alpha", "beta"]
    assert mcp_client._sessions == {}  # noqa: SLF001 — test introspection


def test_probe_tools_degrades_to_none_on_failure() -> None:
    """Probe failures (after retries inside the caller's budget) degrade to None."""
    _BEHAVIORS["broken"] = {"fail_times": 99}
    assert asyncio.run(mcp_client.probe_tools(_stdio_spec("broken"), timeout_seconds=5.0)) is None


def test_probe_tools_degrades_to_none_on_timeout() -> None:
    """Probe timeouts degrade to None."""
    _BEHAVIORS["slow"] = {"tool_names": ["alpha"], "delay": 0.5}
    assert asyncio.run(mcp_client.probe_tools(_stdio_spec("slow"), timeout_seconds=0.05)) is None


def test_list_tools_returns_summaries() -> None:
    """list_tools surfaces raw names, descriptions and JSON schemas."""
    _BEHAVIORS["srv"] = {"tool_names": ["alpha"]}
    summaries = asyncio.run(mcp_client.list_tools(_stdio_spec(), timeout_seconds=5.0))
    assert [summary.name for summary in summaries] == ["alpha"]
    assert summaries[0].description == "fake tool alpha"
    assert isinstance(summaries[0].args_schema, dict)


def test_list_tools_raises_valueerror_on_excluded_config(monkeypatch: pytest.MonkeyPatch) -> None:
    """An excluded config raises ValueError for the API layer to map to 422."""
    monkeypatch.delenv("TEST_MCP_MISSING_VAR", raising=False)
    with pytest.raises(ValueError, match="unresolved"):
        asyncio.run(mcp_client.list_tools(_stdio_spec(env={"TOKEN": "${TEST_MCP_MISSING_VAR}"}), timeout_seconds=5.0))


def test_list_tools_raises_mcpupstreamerror_on_failure() -> None:
    """Upstream failures surface as MCPUpstreamError (API maps to 502)."""
    _BEHAVIORS["broken"] = {"fail_times": 99}
    with pytest.raises(mcp_client.MCPUpstreamError):
        asyncio.run(mcp_client.list_tools(_stdio_spec("broken"), timeout_seconds=5.0))


def test_list_tools_raises_timeouterror_on_timeout() -> None:
    """Listing timeouts raise TimeoutError (API maps to 504)."""
    _BEHAVIORS["slow"] = {"tool_names": ["alpha"], "delay": 0.5}
    with pytest.raises(TimeoutError):
        asyncio.run(mcp_client.list_tools(_stdio_spec("slow"), timeout_seconds=0.05))


def test_call_tool_invokes_named_tool() -> None:
    """call_tool matches the raw tool name and returns its content."""
    _BEHAVIORS["srv"] = {"tool_names": ["alpha"]}
    result = asyncio.run(mcp_client.call_tool(_stdio_spec(), "alpha", {}, timeout_seconds=5.0))
    assert result == "srv::alpha"


def test_call_tool_unknown_tool_raises_valueerror() -> None:
    """Unknown tool names raise ValueError (API maps to 422).

    The error must survive the session exit: raising inside the ``async with``
    block gets replaced by task-group teardown errors on real transports
    (see FakeSessionCM.__aexit__), surfacing as 502 instead of 422.
    """
    _BEHAVIORS["srv"] = {"tool_names": ["alpha"]}
    with pytest.raises(ValueError, match="unknown tool 'nope'"):
        asyncio.run(mcp_client.call_tool(_stdio_spec(), "nope", {}, timeout_seconds=5.0))


def test_call_tool_missing_required_argument_raises_valueerror() -> None:
    """Missing required arguments raise ValueError client-side (API maps to 422).

    The guard reads the tool schema's ``required`` list before invoking and,
    like unknown-tool errors, must be re-raised after a clean session exit —
    not inside the context where teardown replaces the exception (502
    regression). Type validation stays authoritative on the server.
    """
    _BEHAVIORS["srv"] = {"tool_names": ["alpha"], "args_schema": _TEXT_SCHEMA}
    with pytest.raises(ValueError, match="missing required argument"):
        asyncio.run(mcp_client.call_tool(_stdio_spec(), "alpha", {}, timeout_seconds=5.0))


def test_call_tool_missing_argument_error_lists_argument_names() -> None:
    """The ValueError lists every missing required argument name."""
    _BEHAVIORS["srv"] = {"tool_names": ["alpha"], "args_schema": _TEXT_SCHEMA}
    with pytest.raises(ValueError, match="'text'"):
        asyncio.run(mcp_client.call_tool(_stdio_spec(), "alpha", {}, timeout_seconds=5.0))


def test_call_tool_failure_raises_mcpupstreamerror() -> None:
    """Tool execution failures surface as MCPUpstreamError (API maps to 502)."""
    _BEHAVIORS["srv"] = {"tool_names": ["alpha"], "call_fail_next": True}
    with pytest.raises(mcp_client.MCPUpstreamError):
        asyncio.run(mcp_client.call_tool(_stdio_spec(), "alpha", {}, timeout_seconds=5.0))


def test_call_tool_timeout_raises_timeouterror() -> None:
    """Call timeouts raise TimeoutError (API maps to 504)."""
    _BEHAVIORS["slow"] = {"tool_names": ["alpha"], "delay": 0.5}
    with pytest.raises(TimeoutError):
        asyncio.run(mcp_client.call_tool(_stdio_spec("slow"), "alpha", {}, timeout_seconds=0.05))
