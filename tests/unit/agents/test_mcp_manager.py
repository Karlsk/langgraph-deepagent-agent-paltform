"""Unit tests for app.services.agents.mcp_manager (Task #6: T4 MCP 服务).

Zero real network / zero real MCP processes: the module-level
``MultiServerMCPClient`` symbol is replaced with an in-memory fake via
monkeypatch; tenacity backoff sleep is stubbed to a no-op.
"""

import asyncio
from collections.abc import Generator
from typing import Any
from types import SimpleNamespace

import pytest
from langchain_core.tools import BaseTool, StructuredTool

from app.core.langgraph.tools import tools as builtin_tools
from app.core.metrics import mcp_client_rebuild_total, mcp_tools_loaded_total
from app.models.agent_assets import McpServerConfig
from app.services.agents import mcp_manager

pytestmark = pytest.mark.unit


# ---------------------------------------------------------------------------
# Fakes & helpers
# ---------------------------------------------------------------------------


def _make_tool(name: str) -> BaseTool:
    """Build a real BaseTool instance carrying the given name."""

    def _noop() -> str:
        return "ok"

    return StructuredTool.from_function(func=_noop, name=name, description=f"fake tool {name}")


class FakeMCPClient:
    """In-memory stand-in for MultiServerMCPClient (zero network)."""

    created: list["FakeMCPClient"] = []

    def __init__(self, connections: dict[str, Any]) -> None:
        """Record the connection mapping for later assertions."""
        self.connections = connections
        self.get_tools_calls = 0
        FakeMCPClient.created.append(self)

    async def get_tools(self) -> list[BaseTool]:
        """Serve tools per the behavior table; raise while fail budget remains."""
        self.get_tools_calls += 1
        server_name = next(iter(self.connections))
        behavior = _BEHAVIORS.get(server_name, {})
        if self.get_tools_calls <= behavior.get("fail_times", 0):
            raise RuntimeError(f"fake mcp connection failure for {server_name}")
        return [_make_tool(tool_name) for tool_name in behavior.get("tool_names", [])]


_BEHAVIORS: dict[str, dict[str, Any]] = {}


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
def _patch_mcp_client(monkeypatch: pytest.MonkeyPatch) -> Generator[None, None, None]:
    """Replace MultiServerMCPClient with the fake and reset all module state."""
    FakeMCPClient.created = []
    _BEHAVIORS.clear()
    monkeypatch.setattr(mcp_manager, "MultiServerMCPClient", FakeMCPClient)

    async def _no_sleep(_seconds: float) -> None:
        return None

    monkeypatch.setattr(mcp_manager, "_retry_sleep", _no_sleep)
    _reset_state()
    yield
    _reset_state()


def _reset_state() -> None:
    """Clear every module-level cache kept by mcp_manager."""
    mcp_manager._clients.clear()  # noqa: SLF001 — test introspection
    mcp_manager._server_hashes.clear()  # noqa: SLF001 — test introspection
    mcp_manager._tool_cache.clear()  # noqa: SLF001 — test introspection
    mcp_manager._catalog_cache.clear()  # noqa: SLF001 — test introspection


# ---------------------------------------------------------------------------
# Tool catalog: builtin + mcp merge, source/server labels
# ---------------------------------------------------------------------------


def test_catalog_merges_builtin_and_mcp_with_labels() -> None:
    """Catalog lists builtin entries first, then per-server mcp entries with labels."""
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
        ("get_forecast", "weather"),
        ("add", "math"),
        ("multiply", "math"),
    }


def test_get_mcp_tools_flattens_all_servers() -> None:
    """get_mcp_tools returns the flat tool list across all enabled servers."""
    _BEHAVIORS.update({"weather": {"tool_names": ["get_forecast"]}, "math": {"tool_names": ["add"]}})
    session = FakeSession([_stdio_server("weather", "sha256:w1"), _stdio_server("math", "sha256:m1")])

    tools = asyncio.run(mcp_manager.get_mcp_tools(session))

    assert sorted(tool.name for tool in tools) == ["add", "get_forecast"]
    assert _counter_value(mcp_tools_loaded_total, server="weather", status="success") >= 1
    assert _counter_value(mcp_tools_loaded_total, server="math", status="success") >= 1


def test_disabled_servers_are_skipped() -> None:
    """Rows with enabled=False never reach the client layer."""
    disabled = _stdio_server("offline", "sha256:off1")
    disabled.enabled = False
    session = FakeSession([disabled])

    assert asyncio.run(mcp_manager.get_mcp_tools(session)) == []
    assert FakeMCPClient.created == []


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
    """A candidate colliding with an existing MCP server's tool is rejected."""
    _BEHAVIORS["weather"] = {"tool_names": ["get_forecast"]}
    session = FakeSession([_stdio_server("weather", "sha256:w1")])

    with pytest.raises(ValueError, match="get_forecast"):
        asyncio.run(mcp_manager.check_server_tool_collision(session, ["get_forecast"]))


# ---------------------------------------------------------------------------
# Per-server failure degradation; errors are never cached
# ---------------------------------------------------------------------------


def test_single_server_failure_degrades_without_blocking_others() -> None:
    """A failing server is excluded (after 3 attempts) while others stay loaded."""
    _BEHAVIORS.update({"broken": {"fail_times": 99}, "math": {"tool_names": ["add"]}})
    session = FakeSession([_stdio_server("broken", "sha256:b1"), _stdio_server("math", "sha256:m1")])

    tools = asyncio.run(mcp_manager.get_mcp_tools(session))

    assert [tool.name for tool in tools] == ["add"]
    broken_client = FakeMCPClient.created[0]
    assert broken_client.get_tools_calls == 3  # tenacity stop_after_attempt(3)
    assert _counter_value(mcp_tools_loaded_total, server="broken", status="error") >= 1


def test_tool_load_error_is_not_cached() -> None:
    """After a failed load, a recovered server is picked up on the next call."""
    _BEHAVIORS.update({"flaky": {"fail_times": 99}})
    server = _stdio_server("flaky", "sha256:f1")
    session = FakeSession([server])

    assert asyncio.run(mcp_manager.get_mcp_tools(session)) == []

    _BEHAVIORS["flaky"] = {"fail_times": 0, "tool_names": ["recover"]}
    tools = asyncio.run(mcp_manager.get_mcp_tools(session))

    assert [tool.name for tool in tools] == ["recover"]


def test_catalog_not_cached_while_a_server_fails() -> None:
    """An incomplete catalog (failed server) is not frozen into the catalog cache."""
    _BEHAVIORS.update({"flaky": {"fail_times": 99}})
    session = FakeSession([_stdio_server("flaky", "sha256:f1")])

    incomplete = asyncio.run(mcp_manager.build_tool_catalog(session))
    assert all(entry["source"] == "builtin" for entry in incomplete)

    _BEHAVIORS["flaky"] = {"fail_times": 0, "tool_names": ["recover"]}
    complete = asyncio.run(mcp_manager.build_tool_catalog(session))
    assert any(entry["name"] == "recover" and entry["server"] == "flaky" for entry in complete)


# ---------------------------------------------------------------------------
# ${ENV_VAR} placeholder resolution
# ---------------------------------------------------------------------------


def test_env_placeholder_resolved_from_environ(monkeypatch: pytest.MonkeyPatch) -> None:
    """Env placeholders in stdio env and http headers are expanded from os.environ."""
    monkeypatch.setenv("TEST_MCP_TOKEN", "sekrit")
    _BEHAVIORS.update({"local": {"tool_names": ["ls"]}, "remote": {"tool_names": ["ping"]}})
    session = FakeSession(
        [
            _stdio_server("local", "sha256:l1", env={"TOKEN": "${TEST_MCP_TOKEN}"}),
            _http_server("remote", "sha256:r1", headers={"Authorization": "Bearer ${TEST_MCP_TOKEN}"}),
        ]
    )

    tools = asyncio.run(mcp_manager.get_mcp_tools(session))

    assert sorted(tool.name for tool in tools) == ["ls", "ping"]
    local_conn = FakeMCPClient.created[0].connections["local"]
    remote_conn = FakeMCPClient.created[1].connections["remote"]
    assert local_conn["env"] == {"TOKEN": "sekrit"}
    assert remote_conn["headers"] == {"Authorization": "Bearer sekrit"}


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

    assert [tool.name for tool in tools] == ["add"]
    assert [next(iter(client.connections)) for client in FakeMCPClient.created] == ["math"]


# ---------------------------------------------------------------------------
# Client cache reuse, rebuild on config change, shutdown
# ---------------------------------------------------------------------------


def test_client_cached_by_content_hash() -> None:
    """Same (server, hash) reuses one client instance and cached tools."""
    _BEHAVIORS["weather"] = {"tool_names": ["get_forecast"]}
    session = FakeSession([_stdio_server("weather", "sha256:w1")])

    first = asyncio.run(mcp_manager.get_mcp_tools(session))
    second = asyncio.run(mcp_manager.get_mcp_tools(session))

    assert len(FakeMCPClient.created) == 1
    assert FakeMCPClient.created[0].get_tools_calls == 1  # second call served from tool cache
    assert [tool.name for tool in first] == [tool.name for tool in second] == ["get_forecast"]


def test_config_change_rebuilds_client_with_reason_counters() -> None:
    """A changed content_hash rebuilds the client and counts reason transitions."""
    _BEHAVIORS["weather"] = {"tool_names": ["get_forecast"]}
    new_before = _counter_value(mcp_client_rebuild_total, reason="new")
    changed_before = _counter_value(mcp_client_rebuild_total, reason="config_changed")

    asyncio.run(mcp_manager.get_mcp_tools(FakeSession([_stdio_server("weather", "sha256:w1")])))
    asyncio.run(mcp_manager.get_mcp_tools(FakeSession([_stdio_server("weather", "sha256:w2")])))

    assert len(FakeMCPClient.created) == 2
    assert FakeMCPClient.created[0] is not FakeMCPClient.created[1]
    assert _counter_value(mcp_client_rebuild_total, reason="new") == new_before + 1
    assert _counter_value(mcp_client_rebuild_total, reason="config_changed") == changed_before + 1


def test_shutdown_mcp_clients_clears_all_state() -> None:
    """shutdown_mcp_clients drops clients, hashes, tool cache and catalog cache."""
    _BEHAVIORS["weather"] = {"tool_names": ["get_forecast"]}
    session = FakeSession([_stdio_server("weather", "sha256:w1")])
    asyncio.run(mcp_manager.build_tool_catalog(session))

    asyncio.run(mcp_manager.shutdown_mcp_clients())

    assert mcp_manager._clients == {}  # noqa: SLF001 — test introspection
    assert mcp_manager._server_hashes == {}  # noqa: SLF001 — test introspection
    assert mcp_manager._tool_cache == {}  # noqa: SLF001 — test introspection
    assert mcp_manager._catalog_cache == {}  # noqa: SLF001 — test introspection

    # A subsequent load rebuilds the client from scratch.
    asyncio.run(mcp_manager.get_mcp_tools(session))
    assert len(FakeMCPClient.created) == 2
