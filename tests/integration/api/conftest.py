"""Shared fixtures for provider hard-delete + trash integration tests.

Scope: full-stack TestClient wired through ``app.api.v1.api.api_router``
with the same five production exception handlers from ``app.api.error_handlers``
that ``app.main`` registers, so every error exit emits the production
envelope ``{code, message, data}``.

This module intentionally re-declares the DB-engine + auth + TestClient
fixtures (instead of importing them from ``tests/integration/agents/conftest.py``)
because pytest's conftest inheritance is strictly upward along the directory
path — sibling subdirectories are isolated scopes.
"""

from collections.abc import Generator
from typing import Any
from unittest.mock import AsyncMock

import pytest
from fastapi import FastAPI, HTTPException
from fastapi.exceptions import RequestValidationError
from fastapi.testclient import TestClient
from sqlalchemy.pool import StaticPool
from sqlmodel import SQLModel, create_engine
from starlette.exceptions import HTTPException as StarletteHTTPException
from slowapi.errors import RateLimitExceeded

from app.api.error_handlers import (
    http_exception_handler,
    rate_limit_exceeded_handler,
    unhandled_exception_handler,
    validation_exception_handler,
)
from app.api.v1 import auth as auth_module
from app.api.v1 import mcp_servers as mcp_servers_module
from app.api.v1.api import api_router
from app.core.config import settings
from app.core.limiter import limiter
from app.models.provider import DEFAULT_MODEL_NAME, DEFAULT_PROVIDER_NAME, ModelConfig, Provider
from app.models.user import User
from app.services.agents import mcp_manager
from app.services.database import database_service
from app.services.memory import memory_service
from app.utils.auth import create_access_token


# ---------------------------------------------------------------------------
# Rate-limiter reset (shared in-memory slowapi storage; isolation matters)
# ---------------------------------------------------------------------------


@pytest.fixture(autouse=True)
def reset_rate_limiter() -> Generator[None, None, None]:
    """Isolate slowapi counters between tests."""
    limiter.reset()
    yield


# ---------------------------------------------------------------------------
# Settings isolation (zero network side effects)
# ---------------------------------------------------------------------------


@pytest.fixture(autouse=True)
def settings_isolation(tmp_path: Any, monkeypatch: pytest.MonkeyPatch) -> Generator[None, None, None]:
    """Point mutable settings at test-local values (zero network side effects)."""
    monkeypatch.setattr(settings, "SESSION_NAMING_ENABLED", False)
    monkeypatch.setattr(settings, "LANGFUSE_TRACING_ENABLED", False)
    monkeypatch.setattr(settings, "SKILLS_ROOT", str(tmp_path / "skills"))
    yield


# ---------------------------------------------------------------------------
# In-memory database wired into every DB seam
# ---------------------------------------------------------------------------


@pytest.fixture
def db_engine(monkeypatch: pytest.MonkeyPatch) -> Generator[Any, None, None]:
    """In-memory SQLite engine shared by every database seam of the app.

    Seeds the ``default/default`` provider/model pair: custom apps with
    ``model=None`` resolve through the DB-backed seam without the bootstrap
    path running.
    """
    engine = create_engine(
        "sqlite://",
        connect_args={"check_same_thread": False},
        poolclass=StaticPool,
    )
    SQLModel.metadata.create_all(engine)
    from sqlmodel import Session as DBSession  # noqa: PLC0415 — fixture-local seam

    with DBSession(engine) as session:
        provider = Provider(
            name=DEFAULT_PROVIDER_NAME,
            type="OPENAI_COMPATIBLE",
            auth_config={"api_key": "sk-test-default"},
        )
        session.add(provider)
        session.commit()
        session.refresh(provider)
        session.add(
            ModelConfig(
                provider_id=provider.id,
                name=DEFAULT_MODEL_NAME,
                model_id=settings.DEFAULT_LLM_MODEL,
            )
        )
        session.commit()
    monkeypatch.setattr(database_service, "engine", engine)
    monkeypatch.setattr(auth_module.db_service, "engine", engine)
    yield engine
    engine.dispose()


# ---------------------------------------------------------------------------
# Authentication (real JWT flow, offline)
# ---------------------------------------------------------------------------


@pytest.fixture
def user(db_engine: Any) -> User:
    """Register one user directly in the in-memory database."""
    user = User(
        email="alice@example.com",
        hashed_password=User.hash_password("Passw0rd!Strong"),
        username="alice",
    )
    from sqlmodel import Session as DBSession  # noqa: PLC0415 — fixture-local seam

    with DBSession(db_engine) as session:
        session.add(user)
        session.commit()
        session.refresh(user)
    return user


@pytest.fixture
def user_headers(user: User) -> dict[str, str]:
    """Bearer headers carrying a user token (auth dependency path)."""
    token = create_access_token(str(user.id))
    return {"Authorization": f"Bearer {token.access_token}"}


# ---------------------------------------------------------------------------
# Neutralize memory + MCP IO (autouse; harmless for provider tests but keeps
# the fixture safe against shared singletons that other suites touch).
# ---------------------------------------------------------------------------


@pytest.fixture(autouse=True)
def fake_mcp(monkeypatch: pytest.MonkeyPatch) -> Generator[dict[str, list[Any]], None, None]:
    """Route every MCP client construction through a no-op stub."""

    class _StubMcpClient:
        def __init__(self, connections: dict[str, Any]) -> None:
            self.connections = connections

        async def get_tools(self) -> list[Any]:
            return []

    monkeypatch.setattr(mcp_manager, "MultiServerMCPClient", _StubMcpClient)
    monkeypatch.setattr(mcp_servers_module, "MultiServerMCPClient", _StubMcpClient)
    yield {}


@pytest.fixture(autouse=True)
def quiet_memory(monkeypatch: pytest.MonkeyPatch) -> AsyncMock:
    """Neutralize long-term memory IO (the service singleton is shared)."""
    add = AsyncMock(return_value=None)
    monkeypatch.setattr(memory_service, "search", AsyncMock(return_value=""))
    monkeypatch.setattr(memory_service, "add", add)
    return add


# ---------------------------------------------------------------------------
# Full-stack TestClient
# ---------------------------------------------------------------------------


@pytest.fixture
def client(db_engine: Any) -> Generator[TestClient, None, None]:
    """Full api_router under one TestClient with real dependency resolution."""
    app = FastAPI()
    app.state.limiter = limiter
    app.add_exception_handler(RateLimitExceeded, rate_limit_exceeded_handler)  # pyright: ignore[reportArgumentType]
    app.add_exception_handler(RequestValidationError, validation_exception_handler)
    app.add_exception_handler(HTTPException, http_exception_handler)  # pyright: ignore[reportArgumentType]
    app.add_exception_handler(StarletteHTTPException, http_exception_handler)  # pyright: ignore[reportArgumentType]
    app.add_exception_handler(Exception, unhandled_exception_handler)
    app.include_router(api_router, prefix=settings.API_V1_STR)
    with TestClient(app) as test_client:
        yield test_client


__all__: list[str] = []
