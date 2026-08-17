"""Lifespan smoke: MCP degradation must not block startup; ``import app.main`` works.

The real lifespan warm-up runs against the in-memory database: default
AgentApp bootstrap, MCP tool pre-warm (failing servers must degrade instead
of blocking), and the compile of every published app. All external services
(cache, long-term memory, checkpointer, LLM, MCP) are faked or neutralized.
"""

import asyncio
import importlib
import sys
from typing import Any
from unittest.mock import AsyncMock

import pytest
from fastapi.testclient import TestClient
from sqlmodel import Session as DBSession
from sqlmodel import col, select

from app.core.cache import cache_service
from app.core.config import settings
from app.models.agent_assets import AgentApp, McpServerConfig
from app.services.agents import mcp_manager
from app.services.agents import runtime as runtime_module
from app.services.memory import memory_service

from .conftest import FakeMcpClient

pytestmark = pytest.mark.integration

API = settings.API_V1_STR


def test_lifespan_warmup_degrades_without_external_services(
    db_engine: Any, scripted_model: Any, monkeypatch: pytest.MonkeyPatch
) -> None:
    """Startup completes with every external service faked or unavailable."""
    monkeypatch.setattr(cache_service, "initialize", AsyncMock(return_value=None))
    monkeypatch.setattr(cache_service, "close", AsyncMock(return_value=None))
    monkeypatch.setattr(memory_service, "initialize", AsyncMock(return_value=None))

    async def instant_sleep(seconds: float) -> None:
        """Skip tenacity backoff waits so the failing server degrades fast."""

    monkeypatch.setattr(mcp_manager, "_retry_sleep", instant_sleep)

    async def no_checkpointer() -> None:
        return None

    monkeypatch.setattr(runtime_module, "_build_checkpointer", no_checkpointer)

    # A failing MCP server is already persisted: the pre-warm must degrade
    # instead of blocking startup.
    broken = McpServerConfig(name="broken-server", transport="http", url="https://broken.example.com", content_hash="")
    broken.content_hash = "broken-hash"
    with DBSession(db_engine) as db_session:
        db_session.add(broken)
        db_session.commit()
    FakeMcpClient.fail_servers = {"broken-server"}

    # Import (or reuse) the real application; the module-level langfuse init
    # already observed LANGFUSE_TRACING_ENABLED=False from settings isolation.
    main_module = importlib.import_module("app.main")
    assert main_module.app is not None

    with TestClient(main_module.app) as test_client:
        health = test_client.get("/health")
        assert health.status_code == 200
        assert health.json()["status"] == "healthy"

    # The warm-up bootstrapped the default AgentApp into the in-memory DB.
    with DBSession(db_engine) as db_session:
        default_app = db_session.exec(select(AgentApp).where(col(AgentApp.name) == "default")).first()
    assert default_app is not None
    assert default_app.status == "published"


def test_import_app_main_smoke() -> None:
    """The application module imports cleanly (no side-effect failures)."""
    if "app.main" in sys.modules:
        main_module = sys.modules["app.main"]
    else:
        main_module = importlib.import_module("app.main")
    assert main_module.app is not None
    routes = {getattr(route, "path", None) for route in main_module.app.routes}
    assert f"{API}/chatbot/chat" in routes
    assert f"{API}/apps/published" in routes


def test_lifespan_shutdown_closes_shared_checkpoint_pool(monkeypatch: pytest.MonkeyPatch) -> None:
    """Shutdown closes the shared checkpoint connection pool (old lifespan contract)."""
    monkeypatch.setattr(cache_service, "initialize", AsyncMock(return_value=None))
    monkeypatch.setattr(cache_service, "close", AsyncMock(return_value=None))
    monkeypatch.setattr(memory_service, "initialize", AsyncMock(return_value=None))

    main_module = importlib.import_module("app.main")
    monkeypatch.setattr(main_module, "_warm_agent_apps", AsyncMock(return_value=None))
    monkeypatch.setattr(main_module, "shutdown_mcp_clients", AsyncMock(return_value=None))

    pool_mock = AsyncMock()
    monkeypatch.setattr(main_module, "get_shared_connection_pool", AsyncMock(return_value=pool_mock))

    with TestClient(main_module.app):
        pass  # startup + shutdown run the real lifespan

    pool_mock.close.assert_awaited_once()


def test_lifespan_shutdown_survives_missing_pool(db_engine: Any, monkeypatch: pytest.MonkeyPatch) -> None:
    """Shutdown degrades cleanly when no shared pool was ever created."""
    monkeypatch.setattr(cache_service, "initialize", AsyncMock(return_value=None))
    monkeypatch.setattr(cache_service, "close", AsyncMock(return_value=None))
    monkeypatch.setattr(memory_service, "initialize", AsyncMock(return_value=None))

    main_module = importlib.import_module("app.main")
    monkeypatch.setattr(main_module, "_warm_agent_apps", AsyncMock(return_value=None))
    monkeypatch.setattr(main_module, "shutdown_mcp_clients", AsyncMock(return_value=None))
    monkeypatch.setattr(main_module, "get_shared_connection_pool", AsyncMock(return_value=None))

    with TestClient(main_module.app) as test_client:
        assert test_client.get("/health").status_code == 200


def test_warm_agent_apps_uses_independent_sessions(db_engine: Any, monkeypatch: pytest.MonkeyPatch) -> None:
    """Each concurrent warm task owns its own DBSession (no shared session across awaits)."""
    rows = [
        AgentApp(name=f"warm-{i}", system_prompt="prompt", engine="deepagents", status="published") for i in range(2)
    ]
    with DBSession(db_engine) as db_session:
        for row in rows:
            db_session.add(row)
        db_session.commit()

    main_module = importlib.import_module("app.main")
    monkeypatch.setattr(main_module, "ensure_default_agent_app", AsyncMock(return_value=None))
    monkeypatch.setattr(main_module, "get_mcp_tools", AsyncMock(return_value=[]))

    captured: list[Any] = []

    async def fake_get_runtime(session: Any, app_id: str) -> None:
        captured.append(session)

    monkeypatch.setattr(main_module, "get_runtime", fake_get_runtime)

    asyncio.run(main_module._warm_agent_apps())  # noqa: SLF001 — unit under test

    assert len(captured) == 2
    assert captured[0] is not captured[1]  # no shared Session across tasks
