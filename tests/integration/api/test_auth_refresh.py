"""Integration tests for the full refresh-token lifecycle (Phase 1 G1, §10.2).

Two scenarios from the G1 spec:

- ``test_full_login_refresh_business``: register → login → business endpoint →
  force-expire the access token → refresh → replay the business endpoint → 200.
- ``test_replay_detection_clears_user_tokens``: replaying a rotated token
  bulk-revokes every token of the user; only a fresh login recovers access.

Runs against the full ``api_router`` on an in-memory SQLite engine with zero
real network and zero real LLM calls (fixtures from the sibling conftest).
"""

from datetime import timedelta
from typing import Any

import pytest
from fastapi.testclient import TestClient

from app.core.config import settings
from app.utils.auth import create_access_token

pytestmark = pytest.mark.integration

API = settings.API_V1_STR
EMAIL = "refresh-flow@example.com"
PASSWORD = "Passw0rd!Strong"  # noqa: S105 — test constant, not a credential


def _register(client: TestClient) -> dict[str, str]:
    response = client.post(
        f"{API}/auth/register",
        json={"email": EMAIL, "password": PASSWORD, "username": "refresh-flow"},
    )
    assert response.status_code == 200, response.text
    body = response.json()
    assert body["code"] == 200
    data = body["data"]
    assert data["access_token"]
    assert data["refresh_token"]
    return data


def _bearer(token: str) -> dict[str, str]:
    return {"Authorization": f"Bearer {token}"}


def test_full_login_refresh_business(client: TestClient, db_engine: Any) -> None:
    """Register/login/refresh chain keeps business endpoints reachable end to end."""
    register_data = _register(client)

    # Fresh access token unlocks the business endpoint.
    business = client.get(f"{API}/subagents", headers=_bearer(register_data["access_token"]))
    assert business.status_code == 200, business.text

    # Log in again to prove the login path also issues both tokens.
    login = client.post(
        f"{API}/auth/login",
        data={"email": EMAIL, "password": PASSWORD, "grant_type": "password"},
    )
    assert login.status_code == 200, login.text
    login_data = login.json()["data"]
    assert login_data["access_token"]
    assert login_data["refresh_token"]

    # Force-expire the access token (offline issue bound to the real user id),
    # then the business call 401s — mirroring the frontend 401 → refresh retry.
    expired_token = create_access_token(1, expires_delta=timedelta(seconds=-10)).access_token
    stale = client.get(f"{API}/subagents", headers=_bearer(expired_token))
    assert stale.status_code == 401, stale.text

    # Rotate via /auth/refresh and replay the business endpoint with the new pair.
    refreshed = client.post(f"{API}/auth/refresh", json={"refresh_token": login_data["refresh_token"]})
    assert refreshed.status_code == 200, refreshed.text
    refreshed_data = refreshed.json()["data"]
    assert refreshed_data["access_token"] != login_data["access_token"]
    assert refreshed_data["refresh_token"] != login_data["refresh_token"]

    replay_business = client.get(f"{API}/subagents", headers=_bearer(refreshed_data["access_token"]))
    assert replay_business.status_code == 200, replay_business.text


def test_replay_detection_clears_user_tokens(client: TestClient, db_engine: Any) -> None:
    """Replay detection bulk-revokes every token; only a fresh login recovers."""
    register_data = _register(client)
    first_refresh = register_data["refresh_token"]

    # A second device logs in too (independent active token, D4).
    second_login = client.post(
        f"{API}/auth/login",
        data={"email": EMAIL, "password": PASSWORD, "grant_type": "password"},
    )
    assert second_login.status_code == 200, second_login.text
    second_refresh = second_login.json()["data"]["refresh_token"]

    # Rotate device one's token, then replay the stale token -> replay detection.
    rotated = client.post(f"{API}/auth/refresh", json={"refresh_token": first_refresh})
    assert rotated.status_code == 200, rotated.text

    replayed = client.post(f"{API}/auth/refresh", json={"refresh_token": first_refresh})
    assert replayed.status_code == 401, replayed.text
    body = replayed.json()
    assert body["code"] == 401
    assert "REFRESH_TOKEN_REPLAY" in body["message"]

    # Defence in depth: device two's token was bulk-revoked as well.
    second_replay = client.post(f"{API}/auth/refresh", json={"refresh_token": second_refresh})
    assert second_replay.status_code == 401, second_replay.text

    # Only a fresh login restores access.
    recovered = client.post(
        f"{API}/auth/login",
        data={"email": EMAIL, "password": PASSWORD, "grant_type": "password"},
    )
    assert recovered.status_code == 200, recovered.text
    new_refresh = recovered.json()["data"]["refresh_token"]
    final = client.post(f"{API}/auth/refresh", json={"refresh_token": new_refresh})
    assert final.status_code == 200, final.text


def test_logout_then_refresh_returns_401(client: TestClient, db_engine: Any) -> None:
    """A logged-out refresh token can never be refreshed again."""
    register_data = _register(client)

    logout = client.post(f"{API}/auth/logout", json={"refresh_token": register_data["refresh_token"]})
    assert logout.status_code == 200, logout.text

    denied = client.post(f"{API}/auth/refresh", json={"refresh_token": register_data["refresh_token"]})
    assert denied.status_code == 401, denied.text
    body = denied.json()
    assert body["code"] == 401
    # Revoked-but-known token trips replay detection on re-presentation.
    assert "REFRESH_TOKEN_REPLAY" in body["message"]
