"""This file contains the authentication schema for the application."""

import re
from datetime import datetime

from pydantic import (
    BaseModel,
    EmailStr,
    Field,
    SecretStr,
    field_validator,
)

from app.schemas.base import BaseResponse


class Token(BaseModel):
    """Token model for authentication.

    Attributes:
        access_token: The JWT access token.
        token_type: The type of token (always "bearer").
        expires_at: The token expiration timestamp.
    """

    access_token: str = Field(..., description="The JWT access token")
    token_type: str = Field(default="bearer", description="The type of token")
    expires_at: datetime = Field(..., description="The token expiration timestamp")


class TokenResponse(BaseResponse):
    """Legacy access-token-only response (kept for backward compatibility).

    Attributes:
        access_token: The JWT access token
        token_type: The type of token (always "bearer")
        expires_at: When the token expires
    """

    access_token: str = Field(..., description="The JWT access token")
    token_type: str = Field(default="bearer", description="The type of token")
    expires_at: datetime = Field(..., description="When the token expires")


class LoginResponse(BaseResponse):
    """Response model for /auth/login and /auth/register (Phase 1 G1).

    Replaces the legacy ``TokenResponse`` shape with both an access_token
    (7-day lifetime) and a refresh_token (30-day lifetime). The refresh
    token is persisted as a sha256 hash; the raw value is only ever seen by
    the client.

    Attributes:
        access_token: Short-lived JWT (7 days by default).
        refresh_token: Long-lived opaque token (30 days by default);
            sha256-hashed in the DB.
        token_type: Always "bearer".
        expires_at: Expiry timestamp of the access_token.
    """

    access_token: str = Field(..., description="Short-lived JWT (7 days)")
    refresh_token: str = Field(
        ...,
        min_length=32,
        max_length=128,
        description="Long-lived opaque token (30 days); sha256 hashed in DB",
    )
    token_type: str = Field(default="bearer")
    expires_at: datetime = Field(..., description="access_token expiry")


class RefreshTokenRequest(BaseModel):
    """Request body for POST /auth/refresh."""

    refresh_token: str = Field(
        ...,
        min_length=32,
        max_length=128,
        description="Refresh token previously returned by /auth/login or /auth/register",
    )


class LogoutRequest(BaseModel):
    """Request body for POST /auth/logout."""

    refresh_token: str = Field(
        ...,
        min_length=32,
        max_length=128,
        description="Refresh token to revoke (best-effort; idempotent)",
    )


class UserCreate(BaseModel):
    """Request model for user registration.

    Attributes:
        email: User's email address
        password: User's password
        username: Optional display name
    """

    email: EmailStr = Field(..., description="User's email address")
    password: SecretStr = Field(..., description="User's password", min_length=8, max_length=64)
    username: str | None = Field(default=None, description="Optional display name", max_length=50)

    @field_validator("password")
    @classmethod
    def validate_password(cls, v: SecretStr) -> SecretStr:
        """Validate password strength.

        Args:
            v: The password to validate

        Returns:
            SecretStr: The validated password

        Raises:
            ValueError: If the password is not strong enough
        """
        password = v.get_secret_value()

        # Check for common password requirements
        if len(password) < 8:
            raise ValueError("Password must be at least 8 characters long")

        if not re.search(r"[A-Z]", password):
            raise ValueError("Password must contain at least one uppercase letter")

        if not re.search(r"[a-z]", password):
            raise ValueError("Password must contain at least one lowercase letter")

        if not re.search(r"[0-9]", password):
            raise ValueError("Password must contain at least one number")

        if not re.search(r'[!@#$%^&*(),.?":{}|<>]', password):
            raise ValueError("Password must contain at least one special character")

        return v


class UserResponse(BaseResponse):
    """Response model for user operations (Phase 1 G1: no nested token).

    Attributes:
        id: User's ID
        email: User's email address
        username: Optional display name
    """

    id: int = Field(..., description="User's ID")
    email: str = Field(..., description="User's email address")
    username: str | None = Field(default=None, description="Optional display name")


class SessionCreate(BaseModel):
    """Request model for session creation.

    Attributes:
        agent_app_id: Optional AgentApp primary key to bind the new session
            to; omitted (None) leaves the session unbound so the runtime
            falls back to the system default AgentApp.
    """

    agent_app_id: int | None = Field(
        default=None,
        description="Optional AgentApp id to bind the session to (must be published)",
    )


class SessionResponse(BaseResponse):
    """Response model for session creation.

    Attributes:
        session_id: The unique identifier for the chat session
        name: Name of the session (defaults to empty string)
        token: The authentication token for the session
    """

    session_id: str = Field(..., description="The unique identifier for the chat session")
    name: str = Field(default="", description="Name of the session", max_length=100)
    token: Token = Field(..., description="The authentication token for the session")

    @field_validator("name")
    @classmethod
    def sanitize_name(cls, v: str) -> str:
        """Sanitize the session name.

        Args:
            v: The name to sanitize

        Returns:
            str: The sanitized name
        """
        # Remove any potentially harmful characters
        sanitized = re.sub(r'[<>{}[\]()\'"`]', "", v)
        return sanitized
