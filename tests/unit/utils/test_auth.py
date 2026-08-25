"""Unit tests for the token helpers in ``app/utils/auth.py`` (Phase 1 G1).

Covers the TC-01 contract of the G1 Auth Phase 1 spec:
- ``create_access_token`` accepts int or str user ids (``sub`` is the stringified PK)
- default access-token lifetime is ``settings.JWT_ACCESS_TOKEN_EXPIRE_DAYS`` (7 days)
- ``create_refresh_token`` emits high-entropy url-safe base64 (64 chars)
- ``hash_refresh_token`` is a deterministic 64-char lowercase sha256 hex digest

Zero network / zero DB / zero LLM: everything is offline crypto.
"""

import re

from datetime import timedelta

import pytest
from jose import jwt

from app.core.config import settings
from app.utils.auth import (
    create_access_token,
    create_refresh_token,
    hash_refresh_token,
)

pytestmark = pytest.mark.unit

_BASE64_URLSAFE = re.compile(r"^[A-Za-z0-9_-]+$")
_SHA256_HEX = re.compile(r"^[0-9a-f]{64}$")


def _decode(token: str) -> dict[str, object]:
    """Decode a JWT with the app secret (signature + exp checked)."""
    payload: dict[str, object] = jwt.decode(token, settings.JWT_SECRET_KEY, algorithms=[settings.JWT_ALGORITHM])
    return payload


def test_create_access_token_accepts_int_subject() -> None:
    """An int user id is accepted and serialised into the ``sub`` claim."""
    token = create_access_token(42)
    payload = _decode(token.access_token)
    assert payload["sub"] == "42"


def test_create_access_token_accepts_str_subject() -> None:
    """A str user id is accepted unchanged in the ``sub`` claim."""
    token = create_access_token("42")
    payload = _decode(token.access_token)
    assert payload["sub"] == "42"


def test_create_access_token_default_expiry_is_7_days(monkeypatch: pytest.MonkeyPatch) -> None:
    """Default expiry mirrors settings.JWT_ACCESS_TOKEN_EXPIRE_DAYS (= 7)."""
    monkeypatch.setattr(settings, "JWT_ACCESS_TOKEN_EXPIRE_DAYS", 7)
    token = create_access_token(1)
    payload = _decode(token.access_token)
    iat = float(payload["iat"])  # pyright: ignore[reportArgumentType]
    lifetime = token.expires_at.timestamp() - iat
    assert abs(lifetime - 7 * 24 * 3600) < 60  # within a minute of exactly 7 days


def test_create_access_token_custom_expiry_overrides_default() -> None:
    """An explicit ``expires_delta`` wins over the configured default."""
    token = create_access_token(1, expires_delta=timedelta(hours=1))
    payload = _decode(token.access_token)
    iat = float(payload["iat"])  # pyright: ignore[reportArgumentType]
    lifetime = token.expires_at.timestamp() - iat
    assert abs(lifetime - 3600) < 60


def test_two_tokens_issued_same_second_stay_distinct() -> None:
    """Jti keeps same-second tokens distinct (rotation-friendly)."""
    first = create_access_token(7)
    second = create_access_token(7)
    assert first.access_token != second.access_token


def test_create_refresh_token_returns_64_char_base64() -> None:
    """Refresh tokens are 64-char url-safe base64 strings (>= 32 guaranteed)."""
    token = create_refresh_token()
    assert len(token) >= 32
    assert len(token) == 64
    assert _BASE64_URLSAFE.match(token) is not None


def test_create_refresh_token_unique_per_call() -> None:
    """Two successive issues never collide."""
    assert create_refresh_token() != create_refresh_token()


def test_hash_refresh_token_sha256_consistent() -> None:
    """The same raw token always hashes to the same digest."""
    raw = create_refresh_token()
    assert hash_refresh_token(raw) == hash_refresh_token(raw)


def test_hash_refresh_token_64_hex_chars() -> None:
    """Digests are exactly 64 lowercase hex characters."""
    digest = hash_refresh_token("some-raw-token-value")
    assert len(digest) == 64
    assert _SHA256_HEX.match(digest) is not None


def test_hash_refresh_token_never_exposes_raw() -> None:
    """The digest must not contain the raw token substring."""
    raw = create_refresh_token()
    assert raw not in hash_refresh_token(raw)
