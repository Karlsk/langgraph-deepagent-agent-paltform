"""Unit tests for POST /session with the optional agent_app_id binding.

Zero real network / zero real LLM / zero real DB: the auth module's
``db_service`` is replaced with an in-memory double, and the auth
dependency is overridden with a fake User row.
"""

from collections.abc import Generator
from typing import Any

import pytest
from fastapi import FastAPI
from fastapi.testclient import TestClient
from slowapi import _rate_limit_exceeded_handler
from slowapi.errors import RateLimitExceeded

from app.api.v1 import auth as auth_module
from app.api.v1.auth import router as auth_router
from app.core.limiter import limiter
from app.models.agent_assets import AgentApp
from app.models.session import Session as ChatSession
from app.models.user import User

pytestmark = pytest.mark.unit


class FakeDBSession:
    """In-memory SQLModel Session double scoped to AgentApp lookups."""

    def __init__(self, app_row: AgentApp | None) -> None:
        """Configure the canned AgentApp row returned by primary-key get()."""
        self._app_row = app_row

    def get(self, model: Any, pk: Any) -> AgentApp | None:
        """Return the canned AgentApp row regardless of the requested key."""
        return self._app_row

    def close(self) -> None:
        """No-op close."""
        return None


class FakeDatabaseService:
    """Records create_session calls and serves canned AgentApp rows."""

    def __init__(self, app_row: AgentApp | None) -> None:
        """Configure the canned AgentApp row."""
        self._app_row = app_row
        self.create_session_calls: list[dict[str, Any]] = []

    def get_session_maker(self) -> FakeDBSession:
        """Serve the canned AgentApp row to validation lookups."""
        return FakeDBSession(self._app_row)

    async def create_session(
        self,
        session_id: str,
        user_id: int,
        name: str = "",
        username: str | None = None,
        agent_app_id: str | None = None,
    ) -> ChatSession:
        """Record the call and return a detached session row."""
        self.create_session_calls.append(
            {
                "session_id": session_id,
                "user_id": user_id,
                "name": name,
                "username": username,
                "agent_app_id": agent_app_id,
            }
        )
        return ChatSession(
            id=session_id, user_id=user_id, name=name, username=username, agent_app_id=agent_app_id
        )


def _make_app_row(status: str = "published") -> AgentApp:
    """Build a detached AgentApp row with a persisted id."""
    app_row = AgentApp(name="demo-app", system_prompt="You are demo.", status=status)
    app_row.id = 5
    return app_row


@pytest.fixture
def fake_user() -> User:
    """A detached User row standing in for get_current_user."""
    return User(id=1, email="ann@example.com", username="ann", hashed_password="x")  # noqa: S106 — test double, not a real credential


@pytest.fixture
def client(fake_user: User) -> Generator[TestClient, None, None]:
    """Minimal app wiring the auth router with limiter state + user override."""
    app = FastAPI()
    app.state.limiter = limiter
    app.add_exception_handler(RateLimitExceeded, _rate_limit_exceeded_handler)  # pyright: ignore[reportArgumentType]
    app.include_router(auth_router)
    app.dependency_overrides[auth_module.get_current_user] = lambda: fake_user
    yield TestClient(app)
    app.dependency_overrides.clear()


def _patch_db_service(monkeypatch: pytest.MonkeyPatch, app_row: AgentApp | None) -> FakeDatabaseService:
    """Install the recording DatabaseService double on the auth module."""
    fake_service = FakeDatabaseService(app_row)
    monkeypatch.setattr(auth_module, "db_service", fake_service)
    return fake_service


# ---------------------------------------------------------------------------
# agent_app_id omitted -> default AgentApp fallback
# ---------------------------------------------------------------------------


def test_create_session_without_agent_app_id_leaves_binding_empty(
    client: TestClient, monkeypatch: pytest.MonkeyPatch
) -> None:
    """No body keeps agent_app_id NULL so get_runtime falls back to the default."""
    fake_service = _patch_db_service(monkeypatch, app_row=None)

    response = client.post("/session")
    assert response.status_code == 200
    assert fake_service.create_session_calls[0]["agent_app_id"] is None
    assert response.json()["session_id"]


def test_create_session_with_empty_body_leaves_binding_empty(
    client: TestClient, monkeypatch: pytest.MonkeyPatch
) -> None:
    """An empty JSON object behaves like omitting agent_app_id."""
    fake_service = _patch_db_service(monkeypatch, app_row=None)

    response = client.post("/session", json={})
    assert response.status_code == 200
    assert fake_service.create_session_calls[0]["agent_app_id"] is None


# ---------------------------------------------------------------------------
# agent_app_id provided -> existence + published validation
# ---------------------------------------------------------------------------


def test_create_session_with_published_agent_app_stores_string_id(
    client: TestClient, monkeypatch: pytest.MonkeyPatch
) -> None:
    """A published AgentApp is accepted and stored as str(AgentApp.id)."""
    fake_service = _patch_db_service(monkeypatch, app_row=_make_app_row(status="published"))

    response = client.post("/session", json={"agent_app_id": 5})
    assert response.status_code == 200
    assert fake_service.create_session_calls[0]["agent_app_id"] == "5"


def test_create_session_with_draft_agent_app_rejected(client: TestClient, monkeypatch: pytest.MonkeyPatch) -> None:
    """A draft AgentApp is rejected with 422 and the session is not created."""
    fake_service = _patch_db_service(monkeypatch, app_row=_make_app_row(status="draft"))

    response = client.post("/session", json={"agent_app_id": 5})
    assert response.status_code == 422
    assert fake_service.create_session_calls == []


def test_create_session_with_missing_agent_app_rejected(client: TestClient, monkeypatch: pytest.MonkeyPatch) -> None:
    """An unknown AgentApp id is rejected with 404 and no session is created."""
    fake_service = _patch_db_service(monkeypatch, app_row=None)

    response = client.post("/session", json={"agent_app_id": 99})
    assert response.status_code == 404
    assert fake_service.create_session_calls == []
