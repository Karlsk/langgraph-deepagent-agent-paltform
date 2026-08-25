"""Phase 1 G1 auth round trips + chatbot/session-route retirement assertions.

The chatbot runtime (``POST /chatbot/chat``, ``/chatbot/chat/stream``,
``GET|DELETE /chatbot/messages``) and the chat-session token endpoints
(``POST /auth/session``, ``GET /auth/sessions``) were retired together with
``get_current_session``: clients calling those paths now receive a 404
envelope. What remains verifiable end-to-end here:

- register -> login round trip issuing the single-layer ``LoginResponse``
  (access_token + refresh_token);
- refresh rotation + replay detection + logout through the real endpoints;
- 404 retirement assertions for every retired route.
"""

from typing import Any

import pytest
from fastapi.testclient import TestClient

from app.core.config import settings
from tests.conftest import unwrap

from .conftest import assert_error_envelope

pytestmark = pytest.mark.integration

API = settings.API_V1_STR


def test_register_login_refresh_logout_round_trip(client: TestClient) -> None:
    """Full single-layer auth round trip: register -> login -> refresh -> logout."""
    registered = client.post(
        f"{API}/auth/register",
        json={"email": "bob@example.com", "password": "Passw0rd!Strong", "username": "bob"},
    )
    assert registered.status_code == 200, registered.text
    registered_payload = unwrap(registered)
    assert registered_payload["access_token"]
    assert registered_payload["refresh_token"]
    assert registered_payload["token_type"] == "bearer"  # noqa: S105 — test constant, not a credential
    assert registered_payload["expires_at"]

    # Duplicate registration is rejected with an error envelope.
    duplicate = client.post(
        f"{API}/auth/register",
        json={"email": "bob@example.com", "password": "Passw0rd!Strong", "username": "bob"},
    )
    assert_error_envelope(duplicate, code=400, message="Email already registered")

    # Login with the real credentials issues the same LoginResponse shape.
    login = client.post(
        f"{API}/auth/login",
        data={"email": "bob@example.com", "password": "Passw0rd!Strong", "grant_type": "password"},
    )
    assert login.status_code == 200, login.text
    login_payload = unwrap(login)
    access_token = login_payload["access_token"]
    refresh_token = login_payload["refresh_token"]

    bad_login = client.post(
        f"{API}/auth/login",
        data={"email": "bob@example.com", "password": "WrongPassword1!", "grant_type": "password"},
    )
    assert_error_envelope(bad_login, code=401, message="Incorrect email or password")

    # The user access token authenticates management APIs directly.
    user_headers = {"Authorization": f"Bearer {access_token}"}
    listed = client.get(f"{API}/apps", headers=user_headers)
    assert listed.status_code == 200, listed.text

    # Refresh rotates the token pair.
    refreshed = client.post(f"{API}/auth/refresh", json={"refresh_token": refresh_token})
    assert refreshed.status_code == 200, refreshed.text
    rotated = unwrap(refreshed)
    assert rotated["access_token"]
    assert rotated["refresh_token"] != refresh_token

    # Replaying the rotated (now revoked) token is detected and bulk-revokes.
    replay = client.post(f"{API}/auth/refresh", json={"refresh_token": refresh_token})
    assert_error_envelope(replay, code=401, message="REFRESH_TOKEN_REPLAY")

    # The replayed family is fully revoked: even the rotated token is dead.
    dead = client.post(f"{API}/auth/refresh", json={"refresh_token": rotated["refresh_token"]})
    assert_error_envelope(dead, code=401, message="REFRESH_TOKEN_REPLAY")

    # Logout is best-effort idempotent on unknown tokens.
    logout = client.post(f"{API}/auth/logout", json={"refresh_token": rotated["refresh_token"]})
    assert logout.status_code == 200, logout.text
    assert unwrap(logout) is None
    unknown_logout = client.post(f"{API}/auth/logout", json={"refresh_token": "x" * 64})
    assert unknown_logout.status_code == 200, unknown_logout.text


def test_refresh_unknown_and_logout_unknown_are_safe(client: TestClient) -> None:
    """Unknown refresh tokens 401 with INVALID_REFRESH_TOKEN; logout stays 200."""
    unknown = client.post(f"{API}/auth/refresh", json={"refresh_token": "y" * 64})
    assert_error_envelope(unknown, code=401, message="INVALID_REFRESH_TOKEN")

    logout = client.post(f"{API}/auth/logout", json={"refresh_token": "z" * 64})
    assert logout.status_code == 200, logout.text


def test_retired_chatbot_and_session_routes_return_404(
    client: TestClient,
    user_headers: dict[str, str],
) -> None:
    """Every retired chatbot/session route must now 404 (retired, not 401/403)."""
    retired_gets = [
        f"{API}/chatbot/messages",
        f"{API}/auth/sessions",
    ]
    for path in retired_gets:
        response = client.get(path, headers=user_headers)
        assert_error_envelope(response, code=404)

    retired_posts: list[tuple[str, dict[str, Any]]] = [
        (f"{API}/auth/session", {}),
        (f"{API}/chatbot/chat", {"messages": [{"role": "user", "content": "hi"}]}),
    ]
    for path, body in retired_posts:
        response = client.post(path, json=body, headers=user_headers)
        assert_error_envelope(response, code=404)

    retired_delete = client.delete(f"{API}/chatbot/messages", headers=user_headers)
    assert_error_envelope(retired_delete, code=404)
