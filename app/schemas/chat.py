"""Chat schemas for the G4 chat interaction layer (spec-g4-chat §4.5/§6.1/§7.3/§10.4).

One file per domain aggregate (project convention). G4 adds the SSE frame
model (``StreamEvent``), the interrupt projection (``ActionRequest`` /
``InterruptPayload``), the L2 history projection (``HistoryItem`` /
``MessagesResponse``), the rebuild outcome (``RebuildResult``), the chat
trace row projection (``ChatTraceItem``) and restores ``SessionTitle`` from
the pre-G1 archive; the legacy flat ``StreamResponse`` is deleted (superseded
by ``StreamEvent``).
"""

import re
from typing import (
    Any,
    Dict,
    List,
    Literal,
    Optional,
)

from pydantic import (
    BaseModel,
    Field,
    field_validator,
)

from app.schemas.base import BaseResponse


class Message(BaseModel):
    """Message model for chat endpoint.

    Attributes:
        role: The role of the message sender (user or assistant).
        content: The content of the message.
    """

    model_config = {"extra": "ignore"}

    role: Literal["user", "assistant", "system"] = Field(..., description="The role of the message sender")
    content: str = Field(..., description="The content of the message", min_length=1, max_length=3000)

    @field_validator("content")
    @classmethod
    def validate_content(cls, v: str) -> str:
        """Validate the message content.

        Args:
            v: The content to validate

        Returns:
            str: The validated content

        Raises:
            ValueError: If the content contains disallowed patterns
        """
        # Check for potentially harmful content
        if re.search(r"<script.*?>.*?</script>", v, re.IGNORECASE | re.DOTALL):
            raise ValueError("Content contains potentially harmful script tags")

        # Check for null bytes
        if "\0" in v:
            raise ValueError("Content contains null bytes")

        return v


class ChatRequest(BaseModel):
    """Request model for chat endpoint.

    Attributes:
        messages: List of messages in the conversation.
    """

    messages: List[Message] = Field(
        ...,
        description="List of messages in the conversation",
        min_length=1,
    )


class ActionRequest(BaseModel):
    """One projected interrupt action (spec-g4-chat §4.2).

    Stable contract: only ``tool`` + ``args`` survive the projection so the
    frontend approval UI never depends on deepagents-internal fields.

    Attributes:
        tool: Tool name the agent asked to invoke.
        args: Tool arguments payload.
    """

    tool: str = Field(..., description="Tool name the agent asked to invoke")
    args: Dict[str, Any] = Field(default_factory=dict, description="Tool arguments payload")


class InterruptPayload(BaseModel):
    """Structured interrupt payload shared by SSE frames and responses (§4.2/§4.5).

    Attributes:
        action_requests: Interrupted actions awaiting approval, in order.
    """

    action_requests: List[ActionRequest] = Field(..., description="Interrupted actions awaiting approval")


class ChatResponse(BaseResponse):
    """Response model for the non-streaming chat endpoint (§4.5).

    Attributes:
        messages: Assistant replies of a normally completed turn (auto-approve
            success path accumulates every resumed segment).
        interrupt: Populated only when the auto-approve loop hit its round
            limit and the thread stays paused (caller-addressable reason).
    """

    messages: List[Message] = Field(..., description="Assistant replies of the completed turn")
    interrupt: Optional[InterruptPayload] = Field(
        default=None,
        description="Pending interrupt projection; non-empty only on auto-approve limit exceeded",
    )


class StreamEvent(BaseModel):
    """Single-schema SSE frame model (§4.1/§10.4).

    Serialised with ``model_dump(exclude_none=True)`` so each frame kind only
    carries its own payload fields. Frame kinds and payloads:

    * ``message``: ``{content, source}``
    * ``tool_call``: ``{name, content, source}``
    * ``interrupt``: ``{action_requests}``
    * ``summary``: ``{summary_text}``
    * ``error``: ``{message}``
    * ``done``: ``{message_count, compressed, interrupted}``
    """

    type: Literal["message", "tool_call", "interrupt", "summary", "error", "done"] = Field(
        ..., description="Frame kind discriminator"
    )
    content: Optional[str] = Field(default=None, description="Text fragment (message) or tool output (tool_call)")
    source: Optional[str] = Field(default=None, description="Origin tag: subagent name / coordinator / system")
    name: Optional[str] = Field(default=None, description="Tool name (tool_call frames)")
    action_requests: Optional[List[ActionRequest]] = Field(
        default=None, description="Projected interrupted actions (interrupt frames)"
    )
    summary_text: Optional[str] = Field(default=None, description="Compression summary text (summary frames)")
    message: Optional[str] = Field(default=None, description="Error text (error frames)")
    message_count: Optional[int] = Field(default=None, description="Turn message count (done frames)")
    compressed: Optional[bool] = Field(default=None, description="Whether this turn compressed context (done frames)")
    interrupted: Optional[bool] = Field(
        default=None, description="Whether the thread stays paused on an interrupt (done frames)"
    )


class HistoryItem(BaseModel):
    """One L2 JSONL row projected for history rendering (§6.1).

    Attributes:
        type: L2 row type (message | tool_call | summary, G3 §4.1.1).
        seq: Monotonic 1-based row sequence.
        ts: ISO8601 UTC timestamp.
        role: message rows: user | assistant.
        content: message / summary row text.
        name: tool_call rows: tool name.
        summary: tool_call rows: outcome summary.
        source: assistant message rows produced by a subagent carry the
            subagent name (display-only row; coordinator rows omit it).
    """

    type: Literal["message", "tool_call", "summary"] = Field(..., description="L2 row type")
    seq: int = Field(..., description="Monotonic 1-based row sequence")
    ts: str = Field(..., description="ISO8601 UTC timestamp")
    role: Optional[str] = Field(default=None, description="message rows: user | assistant")
    content: Optional[str] = Field(default=None, description="message / summary row text")
    name: Optional[str] = Field(default=None, description="tool_call rows: tool name")
    summary: Optional[str] = Field(default=None, description="tool_call rows: outcome summary")
    source: Optional[str] = Field(default=None, description="subagent name for display-only assistant rows")


class MessagesResponse(BaseResponse):
    """History endpoint payload: L2 rows + pending interrupt pull-along (§5.3/§6.1).

    Attributes:
        messages: L2 row projection (read_or_rebuild_l2 data source).
        pending_interrupt: Non-empty while the thread is paused on an
            interrupt so the frontend can rebuild the approval card.
    """

    messages: List[HistoryItem] = Field(..., description="L2 history rows")
    pending_interrupt: Optional[InterruptPayload] = Field(
        default=None, description="Pending interrupt projection when the thread is paused"
    )


class RebuildResult(BaseModel):
    """Disaster-rebuild outcome counts (§6.2).

    Attributes:
        rebuilt_messages: Rows actually re-injected (message + summary).
        skipped_tool_calls: tool_call rows skipped (tool_call_id pairing
            cannot be restored).
        skipped_subagent_messages: display-only subagent rows (non-null
            ``source``) skipped to keep the checkpoint context clean.
        l2_source_lines: Total L2 rows read as the rebuild source.
    """

    rebuilt_messages: int = Field(..., description="Rows re-injected into the checkpoint (message + summary)")
    skipped_tool_calls: int = Field(..., description="tool_call rows skipped during re-injection")
    skipped_subagent_messages: int = Field(
        ..., description="display-only subagent rows skipped during re-injection"
    )
    l2_source_lines: int = Field(..., description="Total L2 rows read as the rebuild source")


class ChatTraceItem(BaseModel):
    """One chat-sourced trace row projection (§7.3).

    Attributes:
        id: Trace row primary key (surfaced as ``trace_id``).
        status: success | error.
        turns: Model turns consumed.
        duration_seconds: Wall-clock duration of the chat round.
        error: Stringified failure reason (None on success).
        created_at: Row creation timestamp.
        events: Full event stream; every event carries the ``agent`` field
            distinguishing coordinator | subagent name (§7.1).
    """

    id: int = Field(..., description="Trace row primary key")
    status: str = Field(..., description="success | error")
    turns: int = Field(..., description="Model turns consumed")
    duration_seconds: float = Field(..., description="Wall-clock duration in seconds")
    error: Optional[str] = Field(default=None, description="Failure reason on error rows")
    created_at: str = Field(..., description="Row creation timestamp")
    events: List[Dict[str, Any]] = Field(default_factory=list, description="Full event stream with agent fields")


class SessionTitle(BaseModel):
    """Structured output schema for session title generation (§8.2, restored).

    Attributes:
        title: Generated session title.
    """

    title: str = Field(
        ...,
        min_length=1,
        max_length=60,
    )
