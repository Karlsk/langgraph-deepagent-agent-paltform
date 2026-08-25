"""Unit tests for the /auth endpoints (Phase 1 G1, TC-05).

Full login/register/refresh/logout contract against the real auth router on
an in-memory SQLite engine (zero network, zero LLM):

- login & register return LoginResponse{access_token, refresh_token, ...}
- refresh rotates the pair; replaying a rotated token bulk-revokes the user
- unknown / expired refresh tokens fail with 401 INVALID_REFRESH_TOKEN
- logout is best-effort and idempotent
"""

import asyncio
from collections.abc import Generator
from datetime import UTC, datetime, timedelta
from typing import Any

import pytest
from fastapi import FastAPI, HTTPException
from fastapi.exceptions import RequestValidationError
from fastapi.testclient import TestClient
from slowapi.errors import RateLimitExceeded
from sqlalchemy.pool import StaticPool
from sqlmodel import SQLModel
from sqlmodel import Session as DBSession
from sqlmodel import create_engine
from starlette.exceptions import HTTPException as StarletteHTTPException

from app.api.error_handlers import (
    http_exception_handler,
    rate_limit_exceeded_handler,
    unhandled_exception_handler,
    validation_exception_handler,
)
from app.api.v1 import auth as auth_module
from app.api.v1.auth import router as auth_router
from app.core.config import settings
from app.core.limiter import limiter
from app.models.refresh_token import RefreshToken
from app.models.user import User
from app.utils.auth import create_refresh_token, hash_refresh_token
from tests.conftest import unwrap

pytestmark = pytest.mark.unit

API = settings.API_V1_STR
EMAIL = "auth-unit@example.com"
PASSWORD = "Passw0rd!Strong"  # noqa: S105 — test constant, not a credential


# ---------------------------------------------------------------------------
# Fixtures
# ---------------------------------------------------------------------------


@pytest.fixture(autouse=True)
def reset_rate_limiter() -> Generator[None, None, None]:
    """Isolate slowapi counters between tests (shared in-memory storage)."""
    limiter.reset()
    yield


@pytest.fixture
def db_engine(monkeypatch: pytest.MonkeyPatch) -> Generator[Any, None, None]:
    """In-memory SQLite engine wired into the auth module's DB seam."""
    engine = create_engine(
        "sqlite://",
        connect_args={"check_same_thread": False},
        poolclass=StaticPool,
    )
    SQLModel.metadata.create_all(engine)
    monkeypatch.setattr(auth_module.db_service, "engine", engine)
    yield engine
    engine.dispose()


@pytest.fixture
def user(db_engine: Any) -> User:
    """One registered user (seeded directly in the DB)."""
    row = User(
        email=EMAIL,
        hashed_password=User.hash_password(PASSWORD),
        username="auth-unit",
    )
    with DBSession(db_engine) as session:
        session.add(row)
        session.commit()
        session.refresh(row)
    return row


@pytest.fixture
def client(db_engine: Any) -> Generator[TestClient, None, None]:
    """Auth router under a minimal app with the production error handlers."""
    app = FastAPI()
    app.state.limiter = limiter
    app.add_exception_handler(RateLimitExceeded, rate_limit_exceeded_handler)  # pyright: ignore[reportArgumentType]
    app.add_exception_handler(RequestValidationError, validation_exception_handler)
    app.add_exception_handler(HTTPException, http_exception_handler)  # pyright: ignore[reportArgumentType]
    app.add_exception_handler(StarletteHTTPException, http_exception_handler)  # pyright: ignore[reportArgumentType]
    app.add_exception_handler(Exception, unhandled_exception_handler)
    app.include_router(auth_router, prefix=f"{API}/auth")
    with TestClient(app) as test_client:
        yield test_client


def _login(client: TestClient, email: str = EMAIL, password: str = PASSWORD) -> dict[str, Any]:
    """POST /auth/login and return the unwrapped LoginResponse payload."""
    response = client.post(
        f"{API}/auth/login",
        data={"email": email, "password": password, "grant_type": "password"},
    )
    assert response.status_code == 200, response.text
    return unwrap(response, expected_code=200)


def _refresh_rows(db_engine: Any, user_id: int) -> list[RefreshToken]:
    with DBSession(db_engine) as session:
        rows = asyncio.run(_select_rows(session, user_id))
        session.expunge_all()
        return rows


async def _select_rows(session: DBSession, user_id: int) -> list[RefreshToken]:
    from sqlmodel import col, select  # noqa: PLC0415 — test-local seam

    return list(session.exec(select(RefreshToken).where(col(RefreshToken.user_id) == user_id)).all())


# ---------------------------------------------------------------------------
# register / login
# ---------------------------------------------------------------------------


def test_register_returns_login_response_with_refresh_token(client: TestClient) -> None:
    """Register issues both access and refresh tokens in a LoginResponse."""
    response = client.post(
        f"{API}/auth/register",
        json={"email": "fresh@example.com", "password": PASSWORD, "username": "fresh"},
    )
    assert response.status_code == 200, response.text
    data = unwrap(response, expected_code=200)
    assert data["access_token"]
    assert len(data["refresh_token"]) >= 32
    assert data["token_type"] == "bearer"  # noqa: S105 — test constant, not a credential
    assert data["expires_at"]


def test_login_returns_login_response_with_refresh_token(client: TestClient, user: User) -> None:
    """Login issues both access and refresh tokens in a LoginResponse."""
    data = _login(client)
    assert data["access_token"]
    assert len(data["refresh_token"]) >= 32
    assert data["token_type"] == "bearer"  # noqa: S105 — test constant, not a credential
    assert data["expires_at"]


def test_login_wrong_password_returns_401(client: TestClient, user: User) -> None:
    """Wrong credentials fail with a 401 envelope."""
    response = client.post(
        f"{API}/auth/login",
        data={"email": EMAIL, "password": "TotallyWrong!42", "grant_type": "password"},
    )
    assert response.status_code == 401
    assert response.json()["code"] == 401


# ---------------------------------------------------------------------------
# refresh
# ---------------------------------------------------------------------------


def test_refresh_rotates_refresh_token(client: TestClient, user: User) -> None:
    """Refresh returns a brand-new access + refresh pair."""
    first = _login(client)

    response = client.post(f"{API}/auth/refresh", json={"refresh_token": first["refresh_token"]})
    assert response.status_code == 200, response.text
    second = unwrap(response, expected_code=200)

    assert second["access_token"]
    assert second["refresh_token"] != first["refresh_token"]
    assert second["token_type"] == "bearer"  # noqa: S105 — test constant, not a credential


def test_refresh_revokes_old_refresh_token(client: TestClient, user: User, db_engine: Any) -> None:
    """Replaying a rotated token 401s with REFRESH_TOKEN_REPLAY and bulk-revokes."""
    """Replaying a rotated token 401s with REFRESH_TOKEN_REPLAY and bulk-revokes."""
    first = _login(client)
    second_device = _login(client)  # a second active token for the same user

    # Rotate device one's token once, then replay the same token again.
    assert client.post(f"{API}/auth/refresh", json={"refresh_token": first["refresh_token"]}).status_code == 200
    replay = client.post(f"{API}/auth/refresh", json={"refresh_token": first["refresh_token"]})
    assert replay.status_code == 401
    body = replay.json()
    assert body["code"] == 401
    assert "REFRESH_TOKEN_REPLAY" in body["message"]

    # Replay detection revoked every token of this user, including device two.
    other = client.post(f"{API}/auth/refresh", json={"refresh_token": second_device["refresh_token"]})
    assert other.status_code == 401
    rows = _refresh_rows(db_engine, user.id)
    assert rows, "refresh-token rows must exist"
    assert all(row.revoked for row in rows)


def test_refresh_invalid_token_returns_401(client: TestClient, user: User) -> None:
    """Unknown refresh tokens fail with 401 INVALID_REFRESH_TOKEN."""
    response = client.post(f"{API}/auth/refresh", json={"refresh_token": create_refresh_token()})
    assert response.status_code == 401
    body = response.json()
    assert body["code"] == 401
    assert "INVALID_REFRESH_TOKEN" in body["message"]


def test_refresh_expired_token_returns_401(client: TestClient, user: User, db_engine: Any) -> None:
    """Expired refresh tokens fail with 401 INVALID_REFRESH_TOKEN."""
    raw = create_refresh_token()
    expired = RefreshToken(
        user_id=user.id,
        token_hash=hash_refresh_token(raw),
        expires_at=datetime.now(UTC) - timedelta(days=1),
        revoked=False,
    )
    with DBSession(db_engine) as session:
        session.add(expired)
        session.commit()

    response = client.post(f"{API}/auth/refresh", json={"refresh_token": raw})
    assert response.status_code == 401
    body = response.json()
    assert body["code"] == 401
    assert "INVALID_REFRESH_TOKEN" in body["message"]


# ---------------------------------------------------------------------------
# logout
# ---------------------------------------------------------------------------


def test_logout_revokes_refresh_token(client: TestClient, user: User) -> None:
    """Logout revokes the token so a later refresh 401s."""
    data = _login(client)

    response = client.post(f"{API}/auth/logout", json={"refresh_token": data["refresh_token"]})
    assert response.status_code == 200, response.text
    assert unwrap(response, expected_code=200) is None

    after_logout = client.post(f"{API}/auth/refresh", json={"refresh_token": data["refresh_token"]})
    assert after_logout.status_code == 401


def test_logout_unknown_token_is_idempotent(client: TestClient, user: User) -> None:
    """Logout of an unknown token still returns 200 (best-effort)."""
    response = client.post(f"{API}/auth/logout", json={"refresh_token": create_refresh_token()})
    assert response.status_code == 200, response.text
    assert unwrap(response, expected_code=200) is None
