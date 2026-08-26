"""Shared fixtures for AgentApp full-chain integration tests.

Zero real network / zero real LLM / zero real MCP by construction:

- the DB layer runs on an in-memory SQLite engine injected into every DB
  seam (``database_service``, the auth module's private ``db_service``);
- authentication flows through the real JWT path (offline token issuance);
- LLM calls are served by scripted ``BaseChatModel`` substitutes (every
  replayed AIMessage gets a fresh id so the deepagents messages reducer
  never deduplicates);
- MCP sessions are in-memory fakes at the core adapter seam
  (``create_session`` / ``load_mcp_tools``) returning constructed
  ``BaseTool`` instances, registered per server via the ``fake_mcp`` fixture;
- the checkpointer is a shared in-memory ``MemorySaver``.
"""

import uuid
from collections.abc import Generator
from typing import Any
from unittest.mock import AsyncMock

import pytest
from fastapi import FastAPI, HTTPException
from fastapi.exceptions import RequestValidationError
from fastapi.testclient import TestClient
from langchain_core.language_models.chat_models import BaseChatModel
from langchain_core.messages import AIMessage, BaseMessage
from langchain_core.outputs import ChatGeneration, ChatResult
from langchain_core.tools import StructuredTool
from langgraph.checkpoint.memory import MemorySaver
from slowapi.errors import RateLimitExceeded
from sqlalchemy.pool import StaticPool
from sqlmodel import SQLModel, create_engine
from starlette.exceptions import HTTPException as StarletteHTTPException

from app.api.error_handlers import (
    http_exception_handler,
    rate_limit_exceeded_handler,
    unhandled_exception_handler,
    validation_exception_handler,
)
from app.api.v1 import auth as auth_module
from app.api.v1.api import api_router
from app.core import mcp_client
from app.core.config import settings
from app.core.limiter import limiter
from app.models.provider import DEFAULT_MODEL_NAME, DEFAULT_PROVIDER_NAME, ModelConfig, Provider
from app.models.user import User
from app.services.agents import assembly
from app.services.agents import mcp_manager
from app.services.agents import runtime as runtime_module
from app.services.agents import test_runner as test_runner_module
from app.services.database import database_service
from app.services.memory import memory_service
from app.utils.auth import create_access_token


# ---------------------------------------------------------------------------
# Scripted LLM substitute
# ---------------------------------------------------------------------------


class ScriptedChatModel(BaseChatModel):
    """Deterministic chat model replaying canned AIMessages (zero network).

    Every replayed message gets a fresh ``id`` (and fresh tool-call ids) so
    the deepagents messages reducer treats each turn as a distinct message.
    """

    responses: list[AIMessage]
    calls: list[list[BaseMessage]] = []
    n: int = 0

    @property
    def _llm_type(self) -> str:
        return "scripted"

    def bind_tools(self, tools: Any, **kwargs: Any) -> "ScriptedChatModel":
        """Tools are irrelevant for scripted replies; return self."""
        return self

    def _generate(
        self,
        messages: list[BaseMessage],
        stop: Any = None,
        run_manager: Any = None,
        **kwargs: Any,
    ) -> ChatResult:
        """Record the call and replay the next canned response."""
        self.n += 1
        self.calls.append(list(messages))
        message = self.responses[(self.n - 1) % len(self.responses)].model_copy(deep=True)
        message.id = str(uuid.uuid4())
        for index, tool_call in enumerate(message.tool_calls):
            tool_call["id"] = f"tc-{self.n}-{index}"
        return ChatResult(generations=[ChatGeneration(message=message)])


# ---------------------------------------------------------------------------
# Fake MCP adapter seam (core layer)
# ---------------------------------------------------------------------------


def make_mcp_tool(name: str, reply: str = "mcp-ok") -> StructuredTool:
    """Construct a deterministic BaseTool standing in for an MCP tool."""
    return StructuredTool.from_function(func=lambda: reply, name=name, description=f"fake mcp tool {name}")


class FakeMcpState:
    """Per-test registry of the fake MCP adapter seam.

    ``tools_by_server`` maps server names to raw (un-namespaced) tool lists;
    ``fail_servers`` marks servers whose session open/load always fails (the
    core layer retries three times, then degrades that server).
    """

    tools_by_server: dict[str, list[Any]] = {}
    fail_servers: set[str] = set()


class _FakeMcpSession:
    """In-process stand-in for an mcp.ClientSession."""

    def __init__(self, connection: dict[str, Any]) -> None:
        """Store the connection the session was opened for."""
        self.connection = connection

    async def initialize(self) -> None:
        """Initialization is a no-op."""
        return None


class _FakeMcpSessionCM:
    """Async-context-manager stand-in for create_session."""

    def __init__(self, connection: dict[str, Any]) -> None:
        """Create the fake session for the requested connection."""
        self.session = _FakeMcpSession(connection)

    async def __aenter__(self) -> _FakeMcpSession:
        """Return the wrapped fake session."""
        return self.session

    async def __aexit__(self, *exc_info: Any) -> bool:
        """Teardown is a no-op; never suppress exceptions."""
        return False


async def _fake_load_mcp_tools(
    session: Any, server_name: str | None = None, handle_tool_errors: bool = True
) -> list[Any]:
    """Serve the registered fake tools of one server (or fail the open)."""
    del session, handle_tool_errors
    assert server_name is not None
    if server_name in FakeMcpState.fail_servers:
        raise ConnectionError(f"fake mcp connection failed for {server_name}")
    return list(FakeMcpState.tools_by_server.get(server_name, []))


# ---------------------------------------------------------------------------
# Environment / cache isolation
# ---------------------------------------------------------------------------


@pytest.fixture(autouse=True)
def reset_rate_limiter() -> Generator[None, None, None]:
    """Isolate slowapi counters between tests (shared in-memory storage)."""
    limiter.reset()
    yield


@pytest.fixture(autouse=True)
def clean_process_caches() -> Generator[None, None, None]:
    """Isolate every process-level cache between tests."""
    assembly.clear_compile_cache()
    runtime_module.clear_runtime_cache()
    yield
    assembly.clear_compile_cache()
    runtime_module.clear_runtime_cache()


@pytest.fixture(autouse=True)
def settings_isolation(tmp_path: Any, monkeypatch: pytest.MonkeyPatch) -> Generator[None, None, None]:
    """Point mutable settings at test-local values (zero network side effects)."""
    monkeypatch.setattr(settings, "SESSION_NAMING_ENABLED", False)
    monkeypatch.setattr(settings, "LANGFUSE_TRACING_ENABLED", False)
    monkeypatch.setattr(settings, "DATA_ROOT", str(tmp_path / "data"))
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
# Fake LLM + fake MCP + memory seams
# ---------------------------------------------------------------------------


@pytest.fixture
def scripted_model(monkeypatch: pytest.MonkeyPatch) -> ScriptedChatModel:
    """Serve every chat-model construction from a scripted model (tests append responses).

    The DB-backed resolution seam (``load_model_config``) stays real; only the
    ChatOpenAI construction point is redirected to the scripted substitute.
    """
    model = ScriptedChatModel(responses=[AIMessage(content="default-reply")])
    monkeypatch.setattr(assembly, "build_chat_model", lambda provider, model_cfg: model)
    monkeypatch.setattr(test_runner_module, "build_chat_model", lambda provider, model_cfg: model)
    return model


@pytest.fixture(autouse=True)
def fake_mcp(monkeypatch: pytest.MonkeyPatch) -> Generator[dict[str, list[Any]], None, None]:
    """Route every MCP session through the in-memory adapter fakes."""
    FakeMcpState.tools_by_server = {}
    FakeMcpState.fail_servers = set()

    async def _no_sleep(_seconds: float) -> None:
        return None

    monkeypatch.setattr(mcp_client, "create_session", lambda connection: _FakeMcpSessionCM(connection))
    monkeypatch.setattr(mcp_client, "load_mcp_tools", _fake_load_mcp_tools)
    monkeypatch.setattr(mcp_client, "_retry_sleep", _no_sleep)
    yield FakeMcpState.tools_by_server
    mcp_client._sessions.clear()  # noqa: SLF001 — process cache hygiene
    mcp_client._server_hashes.clear()  # noqa: SLF001
    mcp_client._locks.clear()  # noqa: SLF001
    mcp_client._building.clear()  # noqa: SLF001
    mcp_client._finalize_tasks.clear()  # noqa: SLF001
    mcp_manager._catalog_cache.clear()  # noqa: SLF001


@pytest.fixture(autouse=True)
def quiet_memory(monkeypatch: pytest.MonkeyPatch) -> AsyncMock:
    """Neutralize long-term memory IO (the service singleton is shared)."""
    add = AsyncMock(return_value=None)
    monkeypatch.setattr(memory_service, "search", AsyncMock(return_value=""))
    monkeypatch.setattr(memory_service, "add", add)
    return add


@pytest.fixture
def memory_checkpointer(monkeypatch: pytest.MonkeyPatch) -> MemorySaver:
    """Attach a shared in-memory checkpointer to every runtime build."""
    saver = MemorySaver()

    async def fake_build_checkpointer() -> MemorySaver:
        return saver

    monkeypatch.setattr(runtime_module, "_build_checkpointer", fake_build_checkpointer)
    return saver


# ---------------------------------------------------------------------------
# Full-stack TestClient
# ---------------------------------------------------------------------------


@pytest.fixture
def client(db_engine: Any) -> Generator[TestClient, None, None]:
    """Full api_router under one TestClient with real dependency resolution.

    Exception handlers (from ``app.api.error_handlers``) mirror the
    ``app.main`` registrations verbatim (same five registrations, same
    order) so every error exit emits the production envelope
    ``{code, message, data}`` instead of FastAPI's default ``{detail}``.
    """
    app = FastAPI()
    app.state.limiter = limiter
    # Production 429 handler from app.api.error_handlers (envelope output),
    # not slowapi's default {detail} handler — keeps the fixture aligned
    # with production wiring.
    app.add_exception_handler(RateLimitExceeded, rate_limit_exceeded_handler)  # pyright: ignore[reportArgumentType]
    app.add_exception_handler(RequestValidationError, validation_exception_handler)
    # Same dual registration as app.main: fastapi.HTTPException wins for
    # business errors (most-specific MRO class); the Starlette base-class
    # entry catches router-level errors (unknown route 404, method 405).
    app.add_exception_handler(HTTPException, http_exception_handler)  # pyright: ignore[reportArgumentType]
    app.add_exception_handler(StarletteHTTPException, http_exception_handler)  # pyright: ignore[reportArgumentType]
    app.add_exception_handler(Exception, unhandled_exception_handler)
    app.include_router(api_router, prefix=settings.API_V1_STR)
    with TestClient(app) as test_client:
        yield test_client


# ---------------------------------------------------------------------------
# Helper utilities
# ---------------------------------------------------------------------------


def assert_error_envelope(response: Any, *, code: int, message: str | None = None) -> Any:
    """Assert the production error envelope ``{code, message, data}``.

    Guards both the HTTP status and the envelope contract (code mirrors the
    status); returns the ``data`` payload. ``message=None`` skips the exact
    message check (callers may assert substrings on the returned body).
    """
    assert response.status_code == code, response.text
    body = response.json()
    assert isinstance(body, dict), f"envelope must be a JSON object, got: {body!r}"
    assert set(body) == {"code", "message", "data"}, f"unexpected envelope keys: {set(body)}"
    assert body["code"] == code
    assert body["message"]
    if message is not None:
        assert body["message"] == message
    return body["data"]


async def collect_stream(runtime_obj: Any, *args: Any, **kwargs: Any) -> list[Any]:
    """Drain an astream generator into a list."""
    chunks: list[Any] = []
    async for chunk in runtime_obj.astream(*args, **kwargs):
        chunks.append(chunk)
    return chunks


__all__ = [
    "FakeMcpState",
    "ScriptedChatModel",
    "assert_error_envelope",
    "collect_stream",
    "make_mcp_tool",
]
