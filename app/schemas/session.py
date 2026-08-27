"""Session schemas for the G3 session CRUD API (spec-g3-session §11.4.6)."""

from datetime import datetime

from pydantic import BaseModel, Field

from app.schemas.base import BaseResponse


class SessionRead(BaseResponse):
    """GET /sessions list item / GET /sessions/{sid} detail payload."""

    session_id: str
    name: str = Field(default="", max_length=100)
    # G3 §11.4.2: int aligned with AgentApp.id (legacy str ids were migrated).
    agent_app_id: int | None = None
    created_at: datetime
    # New G3 column backing the rename timestamp.
    updated_at: datetime | None = None
    # Only filled by the detail endpoint (list stays None to avoid N+1).
    message_count: int | None = Field(default=None)


class SessionCreate(BaseModel):
    """POST /sessions request body."""

    agent_app_id: int = Field(
        ...,
        description="绑定的 AgentApp id；创建时自动 associate（幂等）",
    )
    name: str = Field(default="", max_length=100)


class SessionUpdate(BaseModel):
    """PATCH /sessions/{sid} request body (rename only)."""

    name: str = Field(..., min_length=1, max_length=100)
