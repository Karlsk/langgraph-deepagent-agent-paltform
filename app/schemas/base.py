"""Base response schemas shared across all endpoints."""

from typing import Any, Generic, TypeVar
from uuid import UUID, uuid4

from asgi_correlation_id import correlation_id
from pydantic import BaseModel, Field

T = TypeVar("T")


def _get_request_id() -> UUID:
    """Return the current request's correlation ID, or a fresh UUID as fallback."""
    value = correlation_id.get()
    return UUID(value) if value else uuid4()


class BaseResponse(BaseModel):
    """Base response model that all endpoint responses inherit from.

    request_id is auto-populated from the CorrelationIdMiddleware ContextVar —
    no endpoint needs to pass it explicitly.
    """

    request_id: UUID = Field(default_factory=_get_request_id, description="Unique identifier for this request")


class ApiResponse(BaseModel, Generic[T]):
    """Unified response envelope: {code, message, data}.

    ``code`` always mirrors the HTTP status code (design decision: no
    "HTTP 200 + business error code" split); request_id stays out of the
    envelope and travels via the X-Request-ID header instead.
    """

    code: int = Field(default=200, description="Business code, numerically identical to the HTTP status")
    message: str = Field(default="success", description="Human-readable summary of the outcome")
    data: T | None = Field(default=None, description="Payload on success, null on error")

    @classmethod
    def success(cls, data: T | None = None, code: int = 200, message: str = "success") -> "ApiResponse[T]":
        """Build a success envelope carrying the endpoint payload."""
        return cls(code=code, message=message, data=data)

    @classmethod
    def error(cls, code: int, message: str) -> "ApiResponse[Any]":
        """Build an error envelope with a null payload."""
        return cls(code=code, message=message, data=None)


class PageResult(BaseModel, Generic[T]):
    """Server-side pagination payload carried inside ``ApiResponse.data``.

    Serialized with a camelCase ``pageSize`` alias to match the frontend
    ``PageResult`` contract (items/total/page/pageSize); Python code keeps
    snake_case attribute access via ``page_size``.
    """

    items: list[T] = Field(default_factory=list, description="Rows of the current page")
    total: int = Field(description="Total number of rows matching the query")
    page: int = Field(description="1-based page number echoed from the request")
    page_size: int = Field(serialization_alias="pageSize", description="Page size echoed from the request")
