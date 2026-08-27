"""Session model for storing chat sessions."""

from datetime import (
    UTC,
    datetime,
)
from typing import (
    TYPE_CHECKING,
    Optional,
)

from sqlmodel import (
    Field,
    Relationship,
)

from app.models.base import BaseModel

if TYPE_CHECKING:
    from app.models.user import User


class Session(BaseModel, table=True):
    """Session model for storing chat sessions.

    Attributes:
        id: The primary key
        user_id: Foreign key to the user
        name: Name of the session (defaults to empty string)
        username: Display name copied from the user at session creation
        created_at: When the session was created
        updated_at: When the session was last updated (set on PATCH rename;
            G3 §11.4.1 — onupdate stamps every subsequent UPDATE)
        agent_app_id: Id of the agent app bound to this session (no FK constraint;
            G3 §11.4.1 — int aligned with AgentApp.id)
        messages: Relationship to session messages
        user: Relationship to the session owner
    """

    id: str = Field(primary_key=True)
    user_id: int = Field(foreign_key="user.id")
    name: str = Field(default="")
    username: Optional[str] = Field(default=None)
    agent_app_id: Optional[int] = Field(default=None, index=True)
    updated_at: Optional[datetime] = Field(
        default_factory=lambda: datetime.now(UTC),
        sa_column_kwargs={"onupdate": lambda: datetime.now(UTC)},
    )
    user: "User" = Relationship(back_populates="sessions")
