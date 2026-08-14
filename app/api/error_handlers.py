"""Envelope exception handlers shared by the host composition root.

Extracted from ``app.main`` so test suites can reuse the production
handlers without importing the application entry point (and triggering
its module-level side effects: ``load_dotenv``, ``langfuse_init``,
registry build, FastAPI construction). This module holds only handler
definitions and pure imports — no initialization side effects.

Registration stays in the composition root (``app.main``); the handler
behaviors and wire output are unchanged:

- 429 keeps the retry-after / rate-limit headers slowapi injected;
- 4xx details are controlled business copy and stay untouched;
- 5xx details and unhandled exceptions are redacted (H6) before they
  reach the client; full details stay in the logs.
"""

from fastapi import Request, status
from fastapi.exceptions import RequestValidationError
from fastapi.responses import JSONResponse, Response
from slowapi.errors import RateLimitExceeded
from starlette.exceptions import HTTPException as StarletteHTTPException

from app.core.limiter import limiter
from app.core.logging import logger
from app.schemas.base import ApiResponse
from app.workflow.logging_conf import redact_processor


def _redacted_summary(message: str) -> str:
    """Redact secret-looking fragments from an error summary (H6).

    Mirrors ``app/workflow/api.py``; ``redact_processor`` is registered in
    the host composition root chain by ``app.core.logging`` (AD-02 v2).
    """
    return str(redact_processor(None, "error", {"event": message})["event"])


async def rate_limit_exceeded_handler(request: Request, exc: RateLimitExceeded) -> Response:
    """Envelope output for slowapi rate limiting (HTTP 429, retry headers kept)."""
    logger.warning("rate_limit_exceeded", path=request.url.path)
    response = JSONResponse(
        status_code=status.HTTP_429_TOO_MANY_REQUESTS,
        content=ApiResponse.error(
            code=status.HTTP_429_TOO_MANY_REQUESTS, message="Rate limit exceeded"
        ).model_dump(),
    )
    # Preserve the retry-after / rate-limit headers the original slowapi handler injected.
    view_rate_limit = getattr(request.state, "view_rate_limit", None)
    if view_rate_limit is not None:
        return limiter._inject_headers(response, view_rate_limit)  # noqa: SLF001 — slowapi internal API
    return response


async def http_exception_handler(request: Request, exc: StarletteHTTPException) -> JSONResponse:
    """Envelope output for explicit HTTP errors; keeps exc.headers (e.g. WWW-Authenticate).

    Typed against the Starlette base class so it can serve both
    ``fastapi.HTTPException`` (business errors) and router-level
    ``starlette.exceptions.HTTPException`` (unknown route 404 / method 405).

    5xx details are redacted: business endpoints re-raise raw internal
    errors as ``HTTPException(500, detail=str(e))`` (DB errors, SQL,
    paths), which must never reach the client verbatim (H6). 4xx details
    are controlled business copy and stay untouched.
    """
    message = str(exc.detail)
    if exc.status_code >= 500:
        message = _redacted_summary(message)
    return JSONResponse(
        status_code=exc.status_code,
        content=ApiResponse.error(code=exc.status_code, message=message).model_dump(),
        headers=exc.headers,
    )


async def unhandled_exception_handler(request: Request, exc: Exception) -> JSONResponse:
    """Global catch-all: full details stay in the logs; the client only sees a redacted summary."""
    logger.exception("unhandled_exception", path=request.url.path)
    message = _redacted_summary(f"Internal server error: {type(exc).__name__}: {exc}")
    return JSONResponse(
        status_code=status.HTTP_500_INTERNAL_SERVER_ERROR,
        content=ApiResponse.error(code=status.HTTP_500_INTERNAL_SERVER_ERROR, message=message).model_dump(),
    )


async def validation_exception_handler(request: Request, exc: RequestValidationError) -> JSONResponse:
    """Handle validation errors from request data.

    Args:
        request: The request that caused the validation error
        exc: The validation error

    Returns:
        JSONResponse: A formatted error response
    """
    # Log the validation error
    logger.error(
        "validation_error",
        client_host=request.client.host if request.client else "unknown",
        path=request.url.path,
        errors=str(exc.errors()),
    )

    # Format the errors to be more user-friendly
    formatted_errors = []
    for error in exc.errors():
        loc = " -> ".join([str(loc_part) for loc_part in error["loc"] if loc_part != "body"])
        formatted_errors.append({"field": loc, "message": error["msg"]})

    return JSONResponse(
        status_code=status.HTTP_422_UNPROCESSABLE_ENTITY,
        content=ApiResponse(
            code=status.HTTP_422_UNPROCESSABLE_ENTITY,
            message="Validation error",
            data=formatted_errors,
        ).model_dump(),
    )
