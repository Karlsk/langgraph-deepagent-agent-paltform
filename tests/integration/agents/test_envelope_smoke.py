"""Envelope smoke assertions for representative main-API endpoints.

Guards the unified response contract ``{code, message, data}`` on a sample of
endpoint families: 200 read, 201 create (envelope ``code`` mirrors the HTTP
status), list projection and DELETE (null data). The exempt ``/health``
endpoint must stay unwrapped. Error-path envelopes (unknown route 404,
unauthenticated 403, invalid body 422) are asserted here against the
full-stack app fixture, which registers the five production handlers from
``app.main``; handler-level semantics (redaction, headers, catch-all) are
covered by ``tests/unit/api/test_envelope.py``.
"""

from typing import Any

import pytest
from fastapi.testclient import TestClient

from app.core.config import settings
from tests.conftest import unwrap

pytestmark = pytest.mark.integration

API = settings.API_V1_STR

ENVELOPE_KEYS = {"code", "message", "data"}


def _assert_envelope(body: Any, *, code: int) -> dict[str, Any]:
    """Assert the exact envelope shape and return its data payload."""
    assert isinstance(body, dict), f"envelope must be a JSON object, got: {body!r}"
    assert set(body) == ENVELOPE_KEYS, f"envelope keys must be exactly {ENVELOPE_KEYS}, got: {set(body)}"
    assert body["code"] == code
    assert body["message"]
    return body["data"]


def _management_headers(client: TestClient, user_headers: dict[str, str]) -> dict[str, str]:
    """Exchange a user token for a chat-session token (management APIs need it)."""
    response = client.post(f"{API}/auth/session", json={}, headers=user_headers)
    assert response.status_code == 200, response.text
    return {"Authorization": f"Bearer {unwrap(response)['token']['access_token']}"}


def test_create_endpoint_envelope_code_mirrors_201(client: TestClient, user_headers: dict[str, str]) -> None:
    """POST 201 endpoints keep HTTP 201 and carry code=201 inside the envelope."""
    headers = _management_headers(client, user_headers)

    created = client.post(f"{API}/apps", json={"name": "smoke-app", "system_prompt": "Smoke."}, headers=headers)
    assert created.status_code == 201
    data = _assert_envelope(created.json(), code=201)
    assert data["name"] == "smoke-app"
    assert isinstance(data["id"], int)


def test_read_list_and_delete_endpoints_carry_200_envelope(client: TestClient, user_headers: dict[str, str]) -> None:
    """GET single, GET list and DELETE all return code=200 envelopes (null data on delete)."""
    headers = _management_headers(client, user_headers)
    app_id = unwrap(
        client.post(f"{API}/apps", json={"name": "read-app", "system_prompt": "Read."}, headers=headers),
        expected_code=201,
    )["id"]

    single = client.get(f"{API}/apps/{app_id}", headers=headers)
    assert single.status_code == 200
    assert _assert_envelope(single.json(), code=200)["name"] == "read-app"

    listed = client.get(f"{API}/apps", headers=headers)
    assert listed.status_code == 200
    rows = _assert_envelope(listed.json(), code=200)
    assert isinstance(rows, list)
    assert {row["name"] for row in rows} >= {"read-app"}

    deleted = client.delete(f"{API}/apps/{app_id}", headers=headers)
    assert deleted.status_code == 200
    assert _assert_envelope(deleted.json(), code=200) is None


def test_auth_register_envelope_and_health_exemption(client: TestClient) -> None:
    """Auth register is enveloped; the exempt /health endpoint stays raw."""
    registered = client.post(
        f"{API}/auth/register",
        json={"email": "smoke@example.com", "password": "Passw0rd!Strong", "username": "smoke"},
    )
    assert registered.status_code == 200
    data = _assert_envelope(registered.json(), code=200)
    assert data["email"] == "smoke@example.com"

    health = client.get(f"{API}/health")
    assert health.status_code == 200
    body = health.json()
    assert body["status"] == "healthy"
    assert not ENVELOPE_KEYS <= set(body), "/health is exempt from the envelope contract"


def test_unknown_route_404_is_enveloped(client: TestClient) -> None:
    """Router-level 404 (starlette HTTPException) keeps the envelope shape."""
    response = client.get(f"{API}/no-such-route")
    assert response.status_code == 404

    body = response.json()
    assert set(body) == ENVELOPE_KEYS
    assert body["code"] == 404
    assert body["message"] == "Not Found"
    assert body["data"] is None


def test_unauthenticated_protected_endpoint_403_is_enveloped(client: TestClient) -> None:
    """Missing credentials on a protected endpoint: envelope code mirrors the HTTP status.

    HTTPBearer rejects a missing Authorization header with 403 "Not
    authenticated" (FastAPI semantics), so the envelope carries code=403.
    """
    response = client.get(f"{API}/apps")
    assert response.status_code == 403

    body = response.json()
    assert set(body) == ENVELOPE_KEYS
    assert body["code"] == 403
    assert body["message"] == "Not authenticated"
    assert body["data"] is None


def test_invalid_request_body_422_is_enveloped(client: TestClient) -> None:
    """An invalid body becomes {code:422, message:"Validation error", data:[...]}."""
    response = client.post(f"{API}/auth/register", json={})
    assert response.status_code == 422

    body = response.json()
    assert set(body) == ENVELOPE_KEYS
    assert body["code"] == 422
    assert body["message"] == "Validation error"
    assert isinstance(body["data"], list) and body["data"]
    assert {entry["field"] for entry in body["data"]} >= {"email", "password"}
    assert all(entry["message"] for entry in body["data"])
