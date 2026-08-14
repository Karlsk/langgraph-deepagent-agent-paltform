"""Unit tests for the unified response envelope handlers in app.api.error_handlers.

Zero real network / zero real LLM / zero real DB: a minimal FastAPI app is
built per test and registers the exact handler functions imported from
``app.api.error_handlers``; only synthetic routes exercise the four error exits.

Envelope contract (design decision):
- shape is exactly {code, message, data}
- ``code`` mirrors the HTTP status code (HTTP status itself is unchanged)
"""

from collections.abc import Generator
from itertools import count
from typing import Any

import pytest
from fastapi import FastAPI, HTTPException, Request
from fastapi.exceptions import RequestValidationError
from fastapi.testclient import TestClient
from limits import parse
from pydantic import BaseModel
from slowapi.errors import RateLimitExceeded
from slowapi.wrappers import Limit
from starlette.exceptions import HTTPException as StarletteHTTPException

from app.api.error_handlers import (
    http_exception_handler,
    rate_limit_exceeded_handler,
    unhandled_exception_handler,
    validation_exception_handler,
)
from app.core.limiter import limiter

pytestmark = pytest.mark.unit

ENVELOPE_KEYS = {"code", "message", "data"}


class Item(BaseModel):
    """Minimal input schema to trigger request validation."""

    name: str


@pytest.fixture(autouse=True)
def reset_rate_limiter() -> Generator[None, None, None]:
    """Isolate slowapi counters between tests (shared in-memory storage)."""
    limiter.reset()
    yield


# Each test app evaluates its own rate-limit scope: slowapi keeps endpoint
# limits on the shared limiter across fixture runs, so a unique scope per
# test keeps the 1/minute counter uncontaminated.
_LIMITED_ROUTE_IDS = count()


@pytest.fixture
def client() -> Generator[tuple[TestClient, str], None, None]:
    """Minimal app registering the real app.api.error_handlers handlers on synthetic routes."""
    app = FastAPI()
    app.state.limiter = limiter
    app.add_exception_handler(RequestValidationError, validation_exception_handler)
    app.add_exception_handler(HTTPException, http_exception_handler)
    # Mirror app.main: the Starlette base-class handler catches router-level
    # errors (unknown route 404 / method 405) without shadowing the
    # fastapi.HTTPException registration (most-specific MRO class wins).
    app.add_exception_handler(StarletteHTTPException, http_exception_handler)
    app.add_exception_handler(RateLimitExceeded, rate_limit_exceeded_handler)
    app.add_exception_handler(Exception, unhandled_exception_handler)

    @app.post("/items")
    async def create_item(item: Item) -> dict[str, Any]:
        return {"name": item.name}

    @app.get("/missing")
    async def missing() -> None:
        raise HTTPException(status_code=404, detail="thing not found")

    @app.get("/protected")
    async def protected() -> None:
        raise HTTPException(
            status_code=401, detail="Not authenticated", headers={"WWW-Authenticate": "Bearer"}
        )

    @app.get("/boom")
    async def boom() -> None:
        raise RuntimeError("leaked api_key=sk-abc123secret")

    @app.get("/server-error")
    async def server_error() -> None:
        # Mirrors business endpoints re-raising raw internals as 500 details.
        raise HTTPException(status_code=500, detail="db insert failed password=xxx at /app/db.py")

    @app.get("/business-error")
    async def business_error() -> None:
        raise HTTPException(status_code=422, detail="llm config 'ghost-config' does not exist")

    limited_path = f"/limited-{next(_LIMITED_ROUTE_IDS)}"
    # Evaluates the real limiter (hit/test) exactly like slowapi's decorator
    # does, then raises the same RateLimitExceeded with the same
    # request.state.view_rate_limit bookkeeping the handler relies on.
    rate_limit = parse("1/minute")

    @app.get(limited_path)
    async def limited(request: Request) -> dict[str, str]:
        scope = f"envelope-test{limited_path}"
        args = ["testclient", scope]
        if not limiter.limiter.hit(rate_limit, *args):
            request.state.view_rate_limit = (rate_limit, args)
            raise RateLimitExceeded(
                Limit(
                    limit=rate_limit,
                    key_func=lambda: "testclient",
                    scope=scope,
                    per_method=False,
                    methods=None,
                    error_message=None,
                    exempt_when=None,
                    cost=1,
                    override_defaults=True,
                )
            )
        return {"status": "ok"}

    # raise_server_exceptions=False so the catch-all handler produces the 500 envelope.
    yield TestClient(app, raise_server_exceptions=False), limited_path


def test_validation_error_envelope_keeps_422_and_field_structure(
    client: tuple[TestClient, str],
) -> None:
    """422 validation output becomes {code:422, message, data:[{field,message}]}."""
    test_client, _ = client
    response = test_client.post("/items", json={})
    assert response.status_code == 422

    body = response.json()
    assert set(body) == ENVELOPE_KEYS
    assert body["code"] == 422
    assert body["message"] == "Validation error"
    assert body["data"][0]["field"] == "name"
    assert body["data"][0]["message"]


def test_http_exception_envelope_keeps_status_and_detail(client: tuple[TestClient, str]) -> None:
    """HTTPException output carries code=status_code, message=detail, data=null."""
    test_client, _ = client
    response = test_client.get("/missing")
    assert response.status_code == 404

    body = response.json()
    assert set(body) == ENVELOPE_KEYS
    assert body["code"] == 404
    assert body["message"] == "thing not found"
    assert body["data"] is None


def test_http_exception_envelope_preserves_headers(client: tuple[TestClient, str]) -> None:
    """exc.headers (e.g. WWW-Authenticate) survive the envelope transformation."""
    test_client, _ = client
    response = test_client.get("/protected")
    assert response.status_code == 401
    assert response.headers["WWW-Authenticate"] == "Bearer"
    assert response.json()["code"] == 401


def test_unknown_route_404_is_enveloped(client: tuple[TestClient, str]) -> None:
    """Router-level 404 (starlette HTTPException) gets the same envelope shape.

    Unmatched routes raise the Starlette base class, not fastapi.HTTPException;
    the base-class handler registered in app.main must still emit the envelope.
    """
    test_client, _ = client
    response = test_client.get("/no-such-route")
    assert response.status_code == 404

    body = response.json()
    assert set(body) == ENVELOPE_KEYS
    assert body["code"] == 404
    assert body["message"] == "Not Found"
    assert body["data"] is None


def test_unknown_route_method_405_is_enveloped(client: tuple[TestClient, str]) -> None:
    """Method-not-allowed on an existing route is also enveloped (405)."""
    test_client, _ = client
    response = test_client.delete("/missing")  # only GET is registered
    assert response.status_code == 405

    body = response.json()
    assert set(body) == ENVELOPE_KEYS
    assert body["code"] == 405
    assert body["data"] is None


def test_rate_limit_envelope_keeps_429_and_retry_headers(
    client: tuple[TestClient, str], monkeypatch: pytest.MonkeyPatch
) -> None:
    """The second hit on a 1/minute route returns the 429 envelope + retry headers."""
    test_client, limited_path = client
    # Same header-injection path as the original slowapi handler; enable the
    # limiter's header output so the preservation is observable.
    monkeypatch.setattr(limiter, "_headers_enabled", True)  # noqa: SLF001 — slowapi internal flag

    assert test_client.get(limited_path).status_code == 200

    response = test_client.get(limited_path)
    assert response.status_code == 429

    body = response.json()
    assert set(body) == ENVELOPE_KEYS
    assert body["code"] == 429
    assert body["message"] == "Rate limit exceeded"
    assert body["data"] is None
    # Headers injected by limiter._inject_headers (as the original handler did) survive.
    assert "Retry-After" in response.headers


def test_http_exception_500_message_is_redacted(client: tuple[TestClient, str]) -> None:
    """5xx HTTPException details (raw internal errors) are masked before reaching the client."""
    test_client, _ = client
    response = test_client.get("/server-error")
    assert response.status_code == 500

    body = response.json()
    assert set(body) == ENVELOPE_KEYS
    assert body["code"] == 500
    assert body["data"] is None
    # The secret-looking key=value fragment is masked by redact_processor (H6).
    assert "password=xxx" not in body["message"]
    assert "password=***" in body["message"]


def test_http_exception_4xx_message_kept_verbatim(client: tuple[TestClient, str]) -> None:
    """4xx HTTPException details are controlled business copy and stay unredacted."""
    test_client, _ = client
    response = test_client.get("/business-error")
    assert response.status_code == 422

    body = response.json()
    assert set(body) == ENVELOPE_KEYS
    assert body["code"] == 422
    assert body["message"] == "llm config 'ghost-config' does not exist"
    assert body["data"] is None


def test_unhandled_exception_envelope_is_500_and_redacted(client: tuple[TestClient, str]) -> None:
    """The catch-all returns HTTP 500 with a redacted message, never raw internals."""
    test_client, _ = client
    response = test_client.get("/boom")
    assert response.status_code == 500

    body = response.json()
    assert set(body) == ENVELOPE_KEYS
    assert body["code"] == 500
    assert body["data"] is None
    assert "RuntimeError" in body["message"]
    # The secret-looking fragment is masked by redact_processor (H6).
    assert "sk-abc123secret" not in body["message"]
    assert "api_key=***" in body["message"]
