"""This file contains the authentication utilities for the application."""

import hashlib
import re
import secrets
from datetime import (
    UTC,
    datetime,
    timedelta,
)
from typing import Optional

from jose import (
    JWTError,
    jwt,
)

from app.core.config import settings
from app.core.logging import logger
from app.schemas.auth import Token
from app.utils.sanitization import sanitize_string


def create_access_token(subject: str | int, expires_delta: Optional[timedelta] = None) -> Token:
    """Create a new access token for a user.

    The ``sub`` JWT claim is the user primary key (serialised as a string),
    matching the single-layer Phase 1 auth contract. ``jti`` carries a unique
    token identifier so two access tokens issued in the same second stay
    distinct (rotated refresh tokens derive new access tokens).

    Args:
        subject: The user id (str or int) the token binds to.
        expires_delta: Optional expiration time delta; defaults to
            ``settings.JWT_ACCESS_TOKEN_EXPIRE_DAYS``.

    Returns:
        Token: The generated access token (encoded JWT + expiry timestamp).
    """
    if expires_delta:
        expire = datetime.now(UTC) + expires_delta
    else:
        expire = datetime.now(UTC) + timedelta(days=settings.JWT_ACCESS_TOKEN_EXPIRE_DAYS)

    subject_str = str(subject)
    to_encode = {
        "sub": subject_str,
        "exp": expire,
        "iat": datetime.now(UTC),
        "jti": sanitize_string(f"{subject_str}-{datetime.now(UTC).timestamp()}"),
    }

    encoded_jwt = jwt.encode(to_encode, settings.JWT_SECRET_KEY, algorithm=settings.JWT_ALGORITHM)

    logger.info("token_created", subject=subject_str, expires_at=expire.isoformat())

    return Token(access_token=encoded_jwt, expires_at=expire)


def verify_token(token: str) -> Optional[str]:
    """Verify a JWT token and return its ``sub`` claim.

    Args:
        token: The JWT token to verify.

    Returns:
        Optional[str]: The ``sub`` claim if token is valid, ``None`` otherwise.

    Raises:
        ValueError: If the token format is invalid
    """
    if not token or not isinstance(token, str):
        logger.warning("token_invalid_format")
        raise ValueError("Token must be a non-empty string")

    # Basic format validation before attempting decode
    # JWT tokens consist of 3 base64url-encoded segments separated by dots
    if not re.match(r"^[A-Za-z0-9-_]+\.[A-Za-z0-9-_]+\.[A-Za-z0-9-_]+$", token):
        logger.warning("token_suspicious_format")
        raise ValueError("Token format is invalid - expected JWT format")

    try:
        payload = jwt.decode(token, settings.JWT_SECRET_KEY, algorithms=[settings.JWT_ALGORITHM])
        subject: str | None = payload.get("sub")
        if subject is None:
            logger.warning("token_missing_subject")
            return None

        logger.info("token_verified", subject=subject)
        return subject

    except JWTError as e:
        logger.error("token_verification_failed", error=str(e))
        return None


def create_refresh_token() -> str:
    """Generate a high-entropy opaque refresh token (64-char url-safe base64).

    The raw token is only seen by the client; the database stores the sha256
    hex digest. ``secrets.token_urlsafe(48)`` produces a 64-character base64-
    url-safe string (48 raw bytes -> 384 bits of entropy).

    Returns:
        str: The opaque refresh token value to embed in ``LoginResponse``.
    """
    return secrets.token_urlsafe(48)


def hash_refresh_token(raw: str) -> str:
    """Return the SHA-256 hex digest of a raw refresh token.

    Used to look up the matching ``RefreshToken`` row without ever persisting
    the raw token. The digest is 64 hex characters and identical for the same
    input across runs.

    Args:
        raw: The raw refresh token value (as returned by ``create_refresh_token``).

    Returns:
        str: 64-character lowercase hex sha256 digest.
    """
    return hashlib.sha256(raw.encode("utf-8")).hexdigest()
