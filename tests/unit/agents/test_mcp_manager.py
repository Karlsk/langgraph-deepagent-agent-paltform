"""Unit tests for app.services.agents.mcp_manager (DB adapter over core mcp_client).

Zero real network / zero real MCP processes: the adapter entry points
(``create_session`` / ``load_mcp_tools``) inside ``app.core.mcp_client`` are
replaced with in-memory fakes via monkeypatch; tenacity backoff sleep is
stubbed to a no-op. MCP tool names are asserted in their namespaced
``{server}__{tool}`` form.
"""

import asyncio
from collections.abc import Generator
from types import SimpleNamespace
from typing import Any, ClassVar

import pytest
from langchain_core.tools import BaseTool, StructuredTool
from pydantic import BaseModel

from app.core import mcp_client
from app.core.langgraph.tools import tools as builtin_tools
from app.core.metrics import mcp_client_rebuild_total, mcp_tools_loaded_total
from app.models.agent_assets import McpServerConfig
from app.services.agents import mcp_manager

pytestmark = pytest.mark.unit


# ---------------------------------------------------------------------------
# Fakes & helpers
# ---------------------------------------------------------------------------


class _NoArgs(BaseModel):
    """Empty argument schema shared by the fake tools."""


def _make_tool(server: str, name: str) -> BaseTool:
    """Build a coroutine-based fake tool whose behavior is table-driven."""

    async def _acall(**_: Any) -> str:
        return f"{server}::{name}"

    return StructuredTool(name=name, description=f"fake tool {name}", coroutine=_acall, args_schema=_NoArgs)


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
        """Mark the session closed; never suppress exceptions."""
        self.session.closed = True
        return False


def _fake_create_session(connection: dict[str, Any]) -> FakeSessionCM:
    """Session factory fake (mirrors langchain_mcp_adapters.sessions.create_session)."""
    return FakeSessionCM(connection)


# Per-server behavior table: tool_names / fail_times.
_BEHAVIORS: dict[str, dict[str, Any]] = {}
# Number of fake load_mcp_tools invocations per server name.
_load_calls: dict[str, int] = {}


async def _fake_load_mcp_tools(
    session: Any, server_name: str | None = None, handle_tool_errors: bool = True
) -> list[BaseTool]:
    """Serve fake tools per the behavior table; raise while the fail budget remains."""
    del session, handle_tool_errors
    assert server_name is not None
    count = _load_calls.get(server_name, 0) + 1
    _load_calls[server_name] = count
    behavior = _BEHAVIORS.get(server_name, {})
    if count <= behavior.get("fail_times", 0):
        raise RuntimeError(f"fake mcp failure for {server_name}")
    return [_make_tool(server_name, tool_name) for tool_name in behavior.get("tool_names", [])]


class FakeSession:
    """Stand-in for a SQLModel session: returns the configured enabled servers."""

    def __init__(self, servers: list[McpServerConfig]) -> None:
        """Bind the server rows served by this fake session."""
        self._servers = servers

    def exec(self, _statement: Any) -> Any:
        """Return a result object whose .all() lists enabled servers."""
        enabled = [server for server in self._servers if server.enabled]
        return SimpleNamespace(all=lambda: list(enabled))


def _stdio_server(name: str, content_hash: str, env: dict[str, str] | None = None) -> McpServerConfig:
    """Build an enabled stdio McpServerConfig row."""
    return McpServerConfig(
        name=name,
        transport="stdio",
        command="python",
        args=["-m", name],
        env=env or {},
        content_hash=content_hash,
    )


def _sse_server(name: str, content_hash: str, headers: dict[str, str] | None = None) -> McpServerConfig:
    """Build an enabled sse McpServerConfig row."""
    return McpServerConfig(
        name=name,
        transport="sse",
        url=f"https://{name}.example.com/sse",
        headers=headers or {},
        content_hash=content_hash,
    )


def _http_server(name: str, content_hash: str, headers: dict[str, str] | None = None) -> McpServerConfig:
    """Build an enabled http McpServerConfig row."""
    return McpServerConfig(
        name=name,
        transport="http",
        url=f"https://{name}.example.com/mcp",
        headers=headers or {},
        content_hash=content_hash,
    )


def _counter_value(counter: Any, **labels: str) -> float:
    """Read the current value of a labeled prometheus counter."""
    return float(counter.labels(**labels)._value.get())  # noqa: SLF001 — test introspection


@pytest.fixture(autouse=True)
def _patch_core_client(monkeypatch: pytest.MonkeyPatch) -> Generator[None, None, None]:
    """Replace the core-layer adapter entry points and reset all state."""
    FakeClientSession.instances = []
    _BEHAVIORS.clear()
    _load_calls.clear()
    monkeypatch.setattr(mcp_client, "create_session", _fake_create_session)
    monkeypatch.setattr(mcp_client, "load_mcp_tools", _fake_load_mcp_tools)

    async def _no_sleep(_seconds: float) -> None:
        return None

    monkeypatch.setattr(mcp_client, "_retry_sleep", _no_sleep)
    _reset_state()
    yield
    _reset_state()


def _reset_state() -> None:
    """Clear every module-level cache kept by mcp_manager and the core pool."""
    mcp_client._sessions.clear()  # noqa: SLF001 — test introspection
    mcp_client._server_hashes.clear()  # noqa: SLF001 — test introspection
    mcp_client._locks.clear()  # noqa: SLF001 — test introspection
    mcp_manager._catalog_cache.clear()  # noqa: SLF001 — test introspection


# ---------------------------------------------------------------------------
# Tool catalog: builtin + mcp merge, source/server labels, namespaced names
# ---------------------------------------------------------------------------


def test_catalog_merges_builtin_and_mcp_with_labels() -> None:
    """Catalog lists builtin entries first, then namespaced mcp entries with labels."""
    _BEHAVIORS.update(
        {
            "weather": {"tool_names": ["get_forecast"]},
            "math": {"tool_names": ["add", "multiply"]},
        }
    )
    session = FakeSession([_stdio_server("weather", "sha256:w1"), _stdio_server("math", "sha256:m1")])

    catalog = asyncio.run(mcp_manager.build_tool_catalog(session))

    builtin_entries = [entry for entry in catalog if entry["source"] == "builtin"]
    mcp_entries = [entry for entry in catalog if entry["source"] == "mcp"]
    assert [entry["name"] for entry in builtin_entries] == [tool.name for tool in builtin_tools]
    assert all("server" not in entry for entry in builtin_entries)
    assert {(entry["name"], entry["server"]) for entry in mcp_entries} == {
        ("weather__get_forecast", "weather"),
        ("math__add", "math"),
        ("math__multiply", "math"),
    }


def test_get_mcp_tools_flattens_all_servers_with_namespace() -> None:
    """get_mcp_tools returns the flat namespaced tool list across all enabled servers."""
    _BEHAVIORS.update({"weather": {"tool_names": ["get_forecast"]}, "math": {"tool_names": ["add"]}})
    session = FakeSession([_stdio_server("weather", "sha256:w1"), _stdio_server("math", "sha256:m1")])

    tools = asyncio.run(mcp_manager.get_mcp_tools(session))

    assert sorted(tool.name for tool in tools) == ["math__add", "weather__get_forecast"]
    assert _counter_value(mcp_tools_loaded_total, server="weather", status="success") >= 1
    assert _counter_value(mcp_tools_loaded_total, server="math", status="success") >= 1


def test_sse_server_tools_loaded_with_namespaced_names() -> None:
    """Sse transport rows load through an sse connection and namespaced tools."""
    _BEHAVIORS["feeds"] = {"tool_names": ["subscribe"]}
    session = FakeSession([_sse_server("feeds", "sha256:s1")])

    tools = asyncio.run(mcp_manager.get_mcp_tools(session))

    assert [tool.name for tool in tools] == ["feeds__subscribe"]
    connection = FakeClientSession.instances[0].connection
    assert connection is not None
    assert connection["transport"] == "sse"
    assert connection["url"] == "https://feeds.example.com/sse"


def test_same_raw_tool_name_across_servers_coexist() -> None:
    """Identical raw tool names on two servers coexist under their namespaces."""
    _BEHAVIORS.update({"alpha": {"tool_names": ["search"]}, "beta": {"tool_names": ["search"]}})
    session = FakeSession([_stdio_server("alpha", "sha256:a1"), _http_server("beta", "sha256:b1")])

    tools = asyncio.run(mcp_manager.get_mcp_tools(session))

    assert sorted(tool.name for tool in tools) == ["alpha__search", "beta__search"]


def test_disabled_servers_are_skipped() -> None:
    """Rows with enabled=False never reach the client layer."""
    disabled = _stdio_server("offline", "sha256:off1")
    disabled.enabled = False
    session = FakeSession([disabled])

    assert asyncio.run(mcp_manager.get_mcp_tools(session)) == []
    assert FakeClientSession.instances == []


# ---------------------------------------------------------------------------
# Name collision fail-fast
# ---------------------------------------------------------------------------


def test_validate_tool_names_raises_on_collision() -> None:
    """validate_tool_names raises ValueError naming every colliding tool."""
    with pytest.raises(ValueError, match="beta"):
        mcp_manager.validate_tool_names(["alpha", "beta"], ["beta", "gamma"])


def test_validate_tool_names_passes_when_disjoint() -> None:
    """Disjoint name sets validate without error."""
    mcp_manager.validate_tool_names(["alpha"], ["beta"])


def test_check_server_tool_collision_fails_fast_on_builtin_name() -> None:
    """A candidate colliding with a builtin tool name is rejected before insert."""
    session = FakeSession([])
    builtin_name = builtin_tools[0].name

    with pytest.raises(ValueError, match=builtin_name):
        asyncio.run(mcp_manager.check_server_tool_collision(session, [builtin_name, "fresh_tool"]))


def test_check_server_tool_collision_fails_fast_on_existing_mcp_name() -> None:
    """A candidate colliding with an existing namespaced MCP tool is rejected."""
    _BEHAVIORS["weather"] = {"tool_names": ["get_forecast"]}
    session = FakeSession([_stdio_server("weather", "sha256:w1")])

    with pytest.raises(ValueError, match="weather__get_forecast"):
        asyncio.run(mcp_manager.check_server_tool_collision(session, ["weather__get_forecast"]))


def test_check_server_tool_collision_allows_cross_server_twin() -> None:
    """A namespaced candidate never collides with another server's twin tool."""
    _BEHAVIORS["alpha"] = {"tool_names": ["search"]}
    session = FakeSession([_stdio_server("alpha", "sha256:a1")])

    asyncio.run(mcp_manager.check_server_tool_collision(session, ["beta__search"]))


# ---------------------------------------------------------------------------
# Per-server failure degradation; errors are never cached
# ---------------------------------------------------------------------------


def test_single_server_failure_degrades_without_blocking_others() -> None:
    """A failing server is excluded (after 3 attempts) while others stay loaded."""
    _BEHAVIORS.update({"broken": {"fail_times": 99}, "math": {"tool_names": ["add"]}})
    session = FakeSession([_stdio_server("broken", "sha256:b1"), _stdio_server("math", "sha256:m1")])

    tools = asyncio.run(mcp_manager.get_mcp_tools(session))

    assert [tool.name for tool in tools] == ["math__add"]
    assert _load_calls["broken"] == 3  # tenacity stop_after_attempt(3)
    assert _counter_value(mcp_tools_loaded_total, server="broken", status="error") >= 1


def test_tool_load_error_is_not_cached() -> None:
    """After a failed load, a recovered server is picked up on the next call."""
    _BEHAVIORS.update({"flaky": {"fail_times": 99}})
    server = _stdio_server("flaky", "sha256:f1")
    session = FakeSession([server])

    assert asyncio.run(mcp_manager.get_mcp_tools(session)) == []

    _BEHAVIORS["flaky"] = {"fail_times": 0, "tool_names": ["recover"]}
    tools = asyncio.run(mcp_manager.get_mcp_tools(session))

    assert [tool.name for tool in tools] == ["flaky__recover"]


def test_catalog_not_cached_while_a_server_fails() -> None:
    """An incomplete catalog (failed server) is not frozen into the catalog cache."""
    _BEHAVIORS.update({"flaky": {"fail_times": 99}})
    session = FakeSession([_stdio_server("flaky", "sha256:f1")])

    incomplete = asyncio.run(mcp_manager.build_tool_catalog(session))
    assert all(entry["source"] == "builtin" for entry in incomplete)

    _BEHAVIORS["flaky"] = {"fail_times": 0, "tool_names": ["recover"]}
    complete = asyncio.run(mcp_manager.build_tool_catalog(session))
    assert any(entry["name"] == "flaky__recover" and entry["server"] == "flaky" for entry in complete)


# ---------------------------------------------------------------------------
# ${ENV_VAR} placeholder resolution
# ---------------------------------------------------------------------------


def test_env_placeholder_resolved_from_environ(monkeypatch: pytest.MonkeyPatch) -> None:
    """Env placeholders in stdio env and sse/http headers are expanded from os.environ."""
    monkeypatch.setenv("TEST_MCP_TOKEN", "sekrit")
    _BEHAVIORS.update({"local": {"tool_names": ["ls"]}, "remote": {"tool_names": ["ping"]}})
    session = FakeSession(
        [
            _stdio_server("local", "sha256:l1", env={"TOKEN": "${TEST_MCP_TOKEN}"}),
            _http_server("remote", "sha256:r1", headers={"Authorization": "Bearer ${TEST_MCP_TOKEN}"}),
        ]
    )

    tools = asyncio.run(mcp_manager.get_mcp_tools(session))

    assert sorted(tool.name for tool in tools) == ["local__ls", "remote__ping"]
    local_conn = FakeClientSession.instances[0].connection
    remote_conn = FakeClientSession.instances[1].connection
    assert local_conn is not None and local_conn["env"] == {"TOKEN": "sekrit"}
    assert remote_conn is not None and remote_conn["headers"] == {"Authorization": "Bearer sekrit"}


def test_missing_env_placeholder_excludes_server(monkeypatch: pytest.MonkeyPatch) -> None:
    """A missing ${VAR} excludes the server (logged, never raised, never cached)."""
    monkeypatch.delenv("TEST_MCP_MISSING_VAR", raising=False)
    _BEHAVIORS.update({"broken-env": {"tool_names": ["never"]}, "math": {"tool_names": ["add"]}})
    session = FakeSession(
        [
            _stdio_server("broken-env", "sha256:be1", env={"TOKEN": "${TEST_MCP_MISSING_VAR}"}),
            _stdio_server("math", "sha256:m1"),
        ]
    )

    tools = asyncio.run(mcp_manager.get_mcp_tools(session))

    assert [tool.name for tool in tools] == ["math__add"]
    # Only the math server ever opened a session (broken-env was excluded).
    assert [instance.connection["args"] for instance in FakeClientSession.instances] == [["-m", "math"]]


# ---------------------------------------------------------------------------
# Session pool reuse, rebuild on config change, shutdown
# ---------------------------------------------------------------------------


def test_session_pool_reuses_one_session_across_loads() -> None:
    """Same effective config reuses one pooled session and one underlying load."""
    _BEHAVIORS["weather"] = {"tool_names": ["get_forecast"]}
    session = FakeSession([_stdio_server("weather", "sha256:w1")])

    first = asyncio.run(mcp_manager.get_mcp_tools(session))
    second = asyncio.run(mcp_manager.get_mcp_tools(session))

    assert len(FakeClientSession.instances) == 1
    assert _load_calls["weather"] == 1  # second call served from the pooled session
    assert [tool.name for tool in first] == [tool.name for tool in second] == ["weather__get_forecast"]


def test_config_change_rebuilds_session_with_reason_counters() -> None:
    """A changed connection config rebuilds the session and counts reason transitions."""
    _BEHAVIORS["weather"] = {"tool_names": ["get_forecast"]}
    new_before = _counter_value(mcp_client_rebuild_total, reason="new")
    changed_before = _counter_value(mcp_client_rebuild_total, reason="config_changed")

    asyncio.run(mcp_manager.get_mcp_tools(FakeSession([_stdio_server("weather", "sha256:w1")])))
    changed = _stdio_server("weather", "sha256:w2")
    changed.args = ["-m", "weather", "--verbose"]
    asyncio.run(mcp_manager.get_mcp_tools(FakeSession([changed])))

    assert len(FakeClientSession.instances) == 2
    assert FakeClientSession.instances[0].closed
    assert _counter_value(mcp_client_rebuild_total, reason="new") == new_before + 1
    assert _counter_value(mcp_client_rebuild_total, reason="config_changed") == changed_before + 1


def test_shutdown_mcp_clients_clears_all_state() -> None:
    """shutdown_mcp_clients closes pooled sessions and clears the catalog cache."""
    _BEHAVIORS["weather"] = {"tool_names": ["get_forecast"]}
    session = FakeSession([_stdio_server("weather", "sha256:w1")])
    asyncio.run(mcp_manager.build_tool_catalog(session))

    asyncio.run(mcp_manager.shutdown_mcp_clients())

    assert mcp_client._sessions == {}  # noqa: SLF001 — test introspection
    assert mcp_client._server_hashes == {}  # noqa: SLF001 — test introspection
    assert mcp_manager._catalog_cache == {}  # noqa: SLF001 — test introspection
    assert all(instance.closed for instance in FakeClientSession.instances)

    # A subsequent load rebuilds the session from scratch.
    asyncio.run(mcp_manager.get_mcp_tools(session))
    assert len(FakeClientSession.instances) == 2
