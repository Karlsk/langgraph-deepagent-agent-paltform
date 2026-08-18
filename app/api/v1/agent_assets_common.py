"""Shared helpers for the agent-asset admin API modules.

Phase-1 scope: all assets are globally shared (no per-user ownership checks);
every endpoint only authenticates via ``get_current_session`` and records the
creator in the audit-only ``created_by`` field.

Error semantics: missing resources return 404; name collisions, validation
failures and dangling skill/subagent/tool references return 422; unexpected
failures return 500 after ``logger.exception``.
"""

import hashlib
import json
from collections.abc import Generator, Sequence
from typing import Any, TypeVar

from fastapi import HTTPException, Request
from pydantic import BaseModel as PydanticBaseModel
from pydantic import ValidationError
from sqlalchemy import func
from sqlmodel import Session as DBSession
from sqlmodel import col, select

from app.core.logging import logger
from app.models.session import Session as ChatSession
from app.schemas.base import PageResult
from app.services.database import database_service

_ModelT = TypeVar("_ModelT", bound=PydanticBaseModel)


# ---------------------------------------------------------------------------
# DB session dependency
# ---------------------------------------------------------------------------


def get_db_session() -> Generator[DBSession, Any, None]:
    """Yield a request-scoped SQLModel session bound to the shared engine.

    Yields:
        DBSession: A SQLModel session closed automatically on teardown.
    """
    with DBSession(database_service.engine) as session:
        yield session


# ---------------------------------------------------------------------------
# Shared helpers
# ---------------------------------------------------------------------------


def _creator(current_session: ChatSession) -> str:
    """Derive the audit-only creator identifier from the chat session."""
    return current_session.username or str(current_session.user_id)


def _canonical_sha256(payload: dict[str, Any]) -> str:
    """Hash a canonical (sorted-keys, compact) JSON projection of the payload."""
    canonical = json.dumps(payload, sort_keys=True, separators=(",", ":"), ensure_ascii=False, default=str)
    return hashlib.sha256(canonical.encode("utf-8")).hexdigest()


async def _read_patch_body(request: Request) -> dict[str, Any]:
    """Parse a PATCH JSON body, defending the immutable ``name`` field.

    Args:
        request: The incoming FastAPI request.

    Returns:
        The parsed JSON object body.

    Raises:
        HTTPException: 422 when the body is not a JSON object or tries to
            modify the immutable ``name`` field.
    """
    try:
        body = await request.json()
    except json.JSONDecodeError as exc:
        raise HTTPException(status_code=422, detail="request body must be valid JSON") from exc
    if not isinstance(body, dict):
        raise HTTPException(status_code=422, detail="request body must be a JSON object")
    if "name" in body:
        raise HTTPException(status_code=422, detail="name is immutable and cannot be changed")
    return body


def _validate_payload(model_type: type[_ModelT], body: dict[str, Any]) -> _ModelT:
    """Validate a manually parsed body against a schema, mapping errors to 422.

    Args:
        model_type: The Pydantic schema to validate against.
        body: The parsed JSON object body.

    Returns:
        The validated schema instance.

    Raises:
        HTTPException: 422 when schema validation fails.
    """
    try:
        return model_type.model_validate(body)
    except ValidationError as exc:
        logger.warning("agent_apps_payload_invalid", model=model_type.__name__, error=str(exc))
        raise HTTPException(status_code=422, detail=str(exc)) from exc


def paginate_by_name(
    db: DBSession,
    model: type[Any],
    *,
    page: int,
    page_size: int,
    keyword: str | None,
    order_by: Any,
    extra_where: Sequence[Any] | None = None,
) -> PageResult[Any]:
    """Run a name-filtered, ordered, server-side paginated list query.

    Args:
        db: Request-scoped DB session.
        model: SQLModel table class carrying a unique ``name`` column.
        page: 1-based page number (validated by the endpoint's Query bounds).
        page_size: Rows per page (validated by the endpoint's Query bounds).
        keyword: Optional case-insensitive substring matched against ``name``.
        order_by: SQLAlchemy order expression preserving the module's sort.
        extra_where: Optional additional filter expressions applied to both
            the row query and the total count (e.g. soft-delete markers).

    Returns:
        PageResult carrying the page rows, the filtered total and the echoed
        page/pageSize values.
    """
    name_col = col(model.name)
    stmt = select(model)
    count_stmt = select(func.count()).select_from(model)
    if keyword:
        pattern = f"%{keyword}%"
        stmt = stmt.where(name_col.ilike(pattern))
        count_stmt = count_stmt.where(name_col.ilike(pattern))
    if extra_where:
        stmt = stmt.where(*extra_where)
        count_stmt = count_stmt.where(*extra_where)
    total = int(db.exec(count_stmt).one())
    rows = db.exec(stmt.order_by(order_by).offset((page - 1) * page_size).limit(page_size)).all()
    return PageResult(items=list(rows), total=total, page=page, page_size=page_size)
