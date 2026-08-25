"""Authentication and authorization endpoints for the API.

Phase 1 G1: single-layer user token + refresh tokens. The legacy
``get_current_session`` dependency and ``/auth/session*`` chat-session
endpoints were retired together with the chatbot runtime; see
``docs/authentication.md`` for the contract.
"""

from datetime import UTC, datetime, timedelta

from fastapi import (
    APIRouter,
    Depends,
    Form,
    HTTPException,
    Request,
)
from fastapi.security import (
    HTTPAuthorizationCredentials,
    HTTPBearer,
)
from sqlmodel import Session as DBSession

from app.core.config import settings
from app.core.limiter import limiter
from app.core.logging import (
    bind_context,
    logger,
)
from app.core.metrics import (
    auth_logout_total,
    auth_refresh_replay_total,
    auth_refresh_total,
    refresh_token_active_count,
)
from app.models.user import User
from app.schemas.auth import (
    LoginResponse,
    LogoutRequest,
    RefreshTokenRequest,
    UserCreate,
)
from app.schemas.base import ApiResponse
from app.services.database import DatabaseService
from app.services.refresh_token_store import ensure_utc, refresh_token_store
from app.utils.auth import (
    create_access_token,
    create_refresh_token,
    verify_token,
)
from app.utils.sanitization import (
    sanitize_email,
    sanitize_string,
    validate_password_strength,
)

router = APIRouter()
security = HTTPBearer()
db_service = DatabaseService()


# ---------------------------------------------------------------------------
# Chat session helpers — RETIRED in Phase 1 G1.
#
# ``_validate_agent_app_binding`` and any chat-session creation endpoint
# supported the deprecated ``POST /auth/session`` flow used by ``chatbot.py``.
# Both are gone in Phase 1 G1; the chat runtime will be redesigned in
# Phase 2/3 on top of the new single-layer user token.
# ---------------------------------------------------------------------------


async def _issue_login_response(user: User) -> LoginResponse:
    """Build a ``LoginResponse`` for ``user`` (access + refresh + persistence).

    Args:
        user: The authenticated user row (already loaded).

    Returns:
        LoginResponse: The envelope data carrying access_token (JWT),
        refresh_token (raw, opaque), token_type, and access_token expiry.
    """
    access_token = create_access_token(user.id, expires_delta=timedelta(days=settings.JWT_ACCESS_TOKEN_EXPIRE_DAYS))
    refresh_raw = create_refresh_token()
    # Persist via the async refresh-token store. The store operates on a sync
    # SQLModel session but exposes ``async`` so it composes with the rest of
    # the DB service layer.
    with DBSession(db_service.engine) as session:
        session_user = session.get(User, user.id)
        if session_user is None:
            raise HTTPException(status_code=500, detail="User not found after authentication")
        await refresh_token_store.create(db=session, user_id=session_user.id, raw_token=refresh_raw)
        refresh_token_active_count.set(await refresh_token_store.count_active(session))
    return LoginResponse(
        access_token=access_token.access_token,
        refresh_token=refresh_raw,
        token_type="bearer",
        expires_at=access_token.expires_at,
    )


async def get_current_user(
    credentials: HTTPAuthorizationCredentials = Depends(security),
) -> User:
    """Get the current user from the bearer token.

    Args:
        credentials: The HTTP authorization credentials containing the JWT token.

    Returns:
        User: The user extracted from the token.

    Raises:
        HTTPException: If the token is invalid or missing.
    """
    try:
        # Sanitize token
        token = sanitize_string(credentials.credentials)

        user_id_str = verify_token(token)
        if user_id_str is None:
            logger.error("invalid_token", token_part=token[:10] + "...")
            raise HTTPException(
                status_code=401,
                detail="Invalid authentication credentials",
                headers={"WWW-Authenticate": "Bearer"},
            )

        # Verify user exists in database
        user_id = int(user_id_str)
        user = await db_service.get_user(user_id)
        if user is None:
            logger.error("user_not_found", user_id=user_id)
            raise HTTPException(
                status_code=404,
                detail="User not found",
                headers={"WWW-Authenticate": "Bearer"},
            )

        # Bind user_id to logging context for all subsequent logs in this request
        bind_context(user_id=user_id)

        return user
    except ValueError as ve:
        logger.exception("token_validation_failed", error=str(ve))
        raise HTTPException(
            status_code=422,
            detail="Invalid token format",
            headers={"WWW-Authenticate": "Bearer"},
        )


@router.post("/register", response_model=ApiResponse[LoginResponse])
@limiter.limit(settings.RATE_LIMIT_ENDPOINTS["register"][0])
async def register_user(request: Request, user_data: UserCreate) -> ApiResponse[LoginResponse]:
    """Register a new user and issue access + refresh tokens.

    Phase 1 G1: register aligns with login and issues a fresh ``LoginResponse``
    so the user can land directly into the authenticated UI without an
    extra round-trip.

    Args:
        request: The FastAPI request object for rate limiting.
        user_data: User registration data.

    Returns:
        ApiResponse[LoginResponse]: Envelope carrying the access + refresh
        tokens plus the user profile.
    """
    try:
        # Sanitize email
        sanitized_email = sanitize_email(user_data.email)

        # Extract and validate password
        password = user_data.password.get_secret_value()
        validate_password_strength(password)

        # Check if user exists
        if await db_service.get_user_by_email(sanitized_email):
            raise HTTPException(status_code=400, detail="Email already registered")

        # Sanitize optional username
        sanitized_username = sanitize_string(user_data.username) if user_data.username else None

        # Create user
        user = await db_service.create_user(
            email=sanitized_email,
            password=User.hash_password(password),
            username=sanitized_username,
        )

        logger.info("user_registered", user_id=user.id, email=sanitized_email)
        bind_context(user_id=user.id)
        return ApiResponse.success(await _issue_login_response(user))
    except ValueError as ve:
        logger.exception("user_registration_validation_failed", error=str(ve))
        raise HTTPException(status_code=422, detail=str(ve))


@router.post("/login", response_model=ApiResponse[LoginResponse])
@limiter.limit(settings.RATE_LIMIT_ENDPOINTS["login"][0])
async def login(
    request: Request, email: str = Form(...), password: str = Form(...), grant_type: str = Form(default="password")
) -> ApiResponse[LoginResponse]:
    """Login a user and issue access + refresh tokens.

    Args:
        request: The FastAPI request object for rate limiting.
        email: User's email
        password: User's password
        grant_type: Must be "password"

    Returns:
        ApiResponse[LoginResponse]: Envelope carrying the access + refresh tokens.

    Raises:
        HTTPException: If credentials are invalid
    """
    try:
        # Sanitize inputs
        email = sanitize_string(email)
        password = sanitize_string(password)
        grant_type = sanitize_string(grant_type)

        # Verify grant type
        if grant_type != "password":
            raise HTTPException(
                status_code=400,
                detail="Unsupported grant type. Must be 'password'",
            )

        user = await db_service.get_user_by_email(email)
        if not user or not user.verify_password(password):
            raise HTTPException(
                status_code=401,
                detail="Incorrect email or password",
                headers={"WWW-Authenticate": "Bearer"},
            )

        bind_context(user_id=user.id)
        return ApiResponse.success(await _issue_login_response(user))
    except ValueError as ve:
        logger.exception("login_validation_failed", error=str(ve))
        raise HTTPException(status_code=422, detail=str(ve))


@router.post("/refresh", response_model=ApiResponse[LoginResponse])
@limiter.limit(settings.RATE_LIMIT_ENDPOINTS["auth_refresh"][0])
async def refresh_token(request: Request, req: RefreshTokenRequest) -> ApiResponse[LoginResponse]:
    """Rotate a refresh token and issue a fresh access + refresh pair.

    Behaviour:
      - Unknown or expired refresh token -> 401 ``INVALID_REFRESH_TOKEN``.
      - Already-revoked refresh token -> bulk-revoke every active token of
        the owning user, then 401 ``REFRESH_TOKEN_REPLAY`` (defence in depth:
        a stolen token cannot be replayed without locking out the legitimate
        device too — the user re-logs in on next access).
      - Otherwise: revoke old, issue new, return a fresh ``LoginResponse``.

    Args:
        request: The FastAPI request object for rate limiting.
        req: Body carrying the refresh_token to rotate.

    Returns:
        ApiResponse[LoginResponse]: Envelope carrying the rotated tokens.

    Raises:
        HTTPException: 401 on invalid / replayed refresh tokens.
    """
    with DBSession(db_service.engine) as session:
        existing = await refresh_token_store.lookup(session, req.refresh_token)
        if existing is None:
            auth_refresh_total.labels(status="invalid").inc()
            raise HTTPException(status_code=401, detail="INVALID_REFRESH_TOKEN")

        now = datetime.now(UTC)
        if ensure_utc(existing.expires_at) < now:
            auth_refresh_total.labels(status="expired").inc()
            raise HTTPException(status_code=401, detail="INVALID_REFRESH_TOKEN")

        if existing.revoked:
            # Replay detected: bulk-revoke every active token of this user
            # (the legitimate device will have to re-login too — defence in depth).
            revoked = await refresh_token_store.revoke_all_for_user(session, existing.user_id)
            auth_refresh_replay_total.inc()
            auth_refresh_total.labels(status="replay_detected").inc()
            logger.warning(
                "refresh_token_replay_detected",
                user_id=existing.user_id,
                revoked_count=revoked,
            )
            raise HTTPException(status_code=401, detail="REFRESH_TOKEN_REPLAY")

        # Rotate: revoke old + persist new in one transaction (single engine session).
        new_raw = create_refresh_token()
        user = session.get(User, existing.user_id)
        if user is None:
            auth_refresh_total.labels(status="invalid").inc()
            raise HTTPException(status_code=401, detail="INVALID_REFRESH_TOKEN")
        # Mark old as revoked directly so we can persist new in the same session.
        existing.revoked = True
        existing.last_used_at = now
        session.add(existing)
        await refresh_token_store.create(session, user_id=user.id, raw_token=new_raw)

        # Issue a fresh access token tied to the same user.
        access_token = create_access_token(
            user.id, expires_delta=timedelta(days=settings.JWT_ACCESS_TOKEN_EXPIRE_DAYS)
        )
        refresh_token_active_count.set(await refresh_token_store.count_active(session))

    auth_refresh_total.labels(status="success").inc()
    logger.info("refresh_token_rotated", user_id=user.id)
    bind_context(user_id=user.id)
    return ApiResponse.success(
        LoginResponse(
            access_token=access_token.access_token,
            refresh_token=new_raw,
            token_type="bearer",
            expires_at=access_token.expires_at,
        )
    )


@router.post("/logout", response_model=ApiResponse[None])
@limiter.limit(settings.RATE_LIMIT_ENDPOINTS["auth_logout"][0])
async def logout(request: Request, req: LogoutRequest) -> ApiResponse[None]:
    """Revoke a single refresh token (best-effort, idempotent).

    Args:
        request: The FastAPI request object for rate limiting.
        req: Body carrying the refresh_token to revoke.

    Returns:
        ApiResponse[None]: Envelope with a null data payload.
    """
    with DBSession(db_service.engine) as session:
        await refresh_token_store.revoke(session, req.refresh_token)
        refresh_token_active_count.set(await refresh_token_store.count_active(session))
    auth_logout_total.inc()
    logger.info("auth_logout")
    return ApiResponse.success(None)


# ---------------------------------------------------------------------------
# Chat session endpoints — RETIRED in Phase 1 G1.
#
# Per spec-g1-auth.md §1.2 / D5, the chatbot session concept (POST /auth/session,
# PATCH /auth/session/{id}/name, DELETE /auth/session/{id}, GET /auth/sessions)
# is deprecated together with the broader ChatSession model. The endpoints
# and their supporting ``_session_for_session_token`` / ``get_current_session``
# dependencies are gone; ``chatbot.py`` is shipped as a stub (see that file).
# Do not re-enable them — Phase 2/3 will redesign chat runtime on top of the
# new single-layer user token. See git history for the original implementations.
# ---------------------------------------------------------------------------
