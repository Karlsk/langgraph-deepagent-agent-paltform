"""Unit tests for the G4 chat schema reorganisation (spec-g4-chat §4.5/§6.1/§7.3/§10.4).

Covers the seven new schemas (ActionRequest / InterruptPayload / StreamEvent /
HistoryItem / MessagesResponse / RebuildResult / ChatTraceItem), the restored
SessionTitle, the extended ChatResponse (interrupt field) and the removal of
the legacy flat StreamResponse.
"""

import pytest
from pydantic import ValidationError

from app.schemas.base import BaseResponse
from app.schemas.chat import (
    ActionRequest,
    ChatResponse,
    ChatTraceItem,
    HistoryItem,
    InterruptPayload,
    Message,
    MessagesResponse,
    RebuildResult,
    SessionTitle,
    StreamEvent,
)

pytestmark = pytest.mark.unit


class TestActionRequest:
    """ActionRequest carries the projected interrupt action (tool + args, §4.2)."""

    def test_holds_tool_and_args(self) -> None:
        """ActionRequest round-trips tool and args."""
        request = ActionRequest(tool="write_file", args={"path": "a.txt", "content": "x"})
        assert request.tool == "write_file"
        assert request.args == {"path": "a.txt", "content": "x"}

    def test_rejects_missing_tool(self) -> None:
        """A missing tool name fails validation."""
        with pytest.raises(ValidationError):
            ActionRequest(args={})  # pyright: ignore[reportCallIssue]


class TestInterruptPayload:
    """InterruptPayload mirrors the SSE interrupt frame schema (§4.2/§4.5)."""

    def test_carries_action_requests(self) -> None:
        """InterruptPayload wraps the projected action list."""
        payload = InterruptPayload(action_requests=[ActionRequest(tool="write_file", args={"path": "a.txt"})])
        assert payload.action_requests[0].tool == "write_file"


class TestStreamEvent:
    """StreamEvent is the single-schema SSE frame model (§4.1/§10.4)."""

    def test_message_frame_serializes_without_none_fields(self) -> None:
        """exclude_none=True keeps a message frame to its own payload keys."""
        event = StreamEvent(type="message", content="hello", source="coordinator")
        dumped = event.model_dump(exclude_none=True)
        assert dumped == {"type": "message", "content": "hello", "source": "coordinator"}

    def test_tool_call_frame_carries_name(self) -> None:
        """tool_call frames keep the tool name in the dump."""
        event = StreamEvent(type="tool_call", name="write_file", content="ok", source="coordinator")
        dumped = event.model_dump(exclude_none=True)
        assert dumped["name"] == "write_file"
        assert dumped["type"] == "tool_call"

    def test_interrupt_frame_carries_action_requests(self) -> None:
        """Interrupt frames carry the projected action list."""
        event = StreamEvent(
            type="interrupt",
            action_requests=[{"tool": "write_file", "args": {"path": "a.txt"}}],
        )
        assert event.action_requests is not None
        assert event.action_requests[0].tool == "write_file"

    def test_summary_frame(self) -> None:
        """Summary frames serialise to type + summary_text only."""
        event = StreamEvent(type="summary", summary_text="earlier turns condensed")
        assert event.model_dump(exclude_none=True) == {
            "type": "summary",
            "summary_text": "earlier turns condensed",
        }

    def test_error_frame(self) -> None:
        """Error frames serialise to type + message only."""
        event = StreamEvent(type="error", message="boom")
        assert event.model_dump(exclude_none=True) == {"type": "error", "message": "boom"}

    def test_done_frame_with_compression_and_interrupt_flags(self) -> None:
        """Done frames carry count/compressed/interrupted metadata."""
        event = StreamEvent(type="done", message_count=4, compressed=False, interrupted=True)
        dumped = event.model_dump(exclude_none=True)
        assert dumped == {
            "type": "done",
            "message_count": 4,
            "compressed": False,
            "interrupted": True,
        }

    def test_type_is_constrained_to_six_frame_kinds(self) -> None:
        """An unknown frame kind is rejected."""
        with pytest.raises(ValidationError):
            StreamEvent(type="unknown")  # pyright: ignore[reportCallIssue]


class TestHistoryItem:
    """HistoryItem projects one L2 JSONL row (§6.1)."""

    def test_message_row(self) -> None:
        """Message rows keep role/content, no tool fields."""
        item = HistoryItem(type="message", seq=1, ts="2026-08-27T00:00:00Z", role="user", content="hi")
        assert item.role == "user"
        assert item.name is None

    def test_tool_call_row(self) -> None:
        """tool_call rows keep name/summary, no content."""
        item = HistoryItem(
            type="tool_call", seq=2, ts="2026-08-27T00:00:01Z", name="write_file", summary="wrote a.txt"
        )
        assert item.content is None
        assert item.name == "write_file"

    def test_summary_row(self) -> None:
        """Summary rows keep the condensed text."""
        item = HistoryItem(type="summary", seq=3, ts="2026-08-27T00:00:02Z", content="condensed")
        assert item.type == "summary"

    def test_subagent_source_row(self) -> None:
        """Display-only assistant rows carry the subagent name; others default None."""
        item = HistoryItem(
            type="message", seq=2, ts="t", role="assistant", content="研究中…", source="researcher"
        )
        assert item.source == "researcher"
        plain = HistoryItem(type="message", seq=1, ts="t", role="assistant", content="done")
        assert plain.source is None

    def test_type_is_constrained(self) -> None:
        """An unknown L2 row type is rejected."""
        with pytest.raises(ValidationError):
            HistoryItem(type="other", seq=1, ts="t")  # pyright: ignore[reportCallIssue]


class TestMessagesResponse:
    """MessagesResponse = L2 rows + optional pending interrupt (§5.3/§6.1)."""

    def test_defaults_pending_interrupt_to_none(self) -> None:
        """pending_interrupt defaults to None for a live thread."""
        response = MessagesResponse(messages=[])
        assert response.pending_interrupt is None

    def test_carries_rows_and_pending_interrupt(self) -> None:
        """Rows and a pending interrupt travel together on refresh recovery."""
        response = MessagesResponse(
            messages=[HistoryItem(type="message", seq=1, ts="t", role="user", content="hi")],
            pending_interrupt=InterruptPayload(action_requests=[ActionRequest(tool="write_file", args={})]),
        )
        assert isinstance(response, BaseResponse)
        assert response.messages[0].type == "message"
        assert response.pending_interrupt is not None
        assert response.pending_interrupt.action_requests[0].tool == "write_file"


class TestRebuildResult:
    """RebuildResult reports the rebuild outcome counts (§6.2)."""

    def test_holds_four_counters(self) -> None:
        """RebuildResult exposes the four outcome counters."""
        result = RebuildResult(
            rebuilt_messages=7, skipped_tool_calls=3, skipped_subagent_messages=2, l2_source_lines=12
        )
        assert result.rebuilt_messages == 7
        assert result.skipped_tool_calls == 3
        assert result.skipped_subagent_messages == 2
        assert result.l2_source_lines == 12


class TestChatTraceItem:
    """ChatTraceItem is one chat-sourced trace row projection (§7.3)."""

    def test_holds_full_projection(self) -> None:
        """ChatTraceItem carries the full row projection incl. events."""
        item = ChatTraceItem(
            id=1,
            status="success",
            turns=2,
            duration_seconds=1.5,
            error=None,
            created_at="2026-08-27T00:00:00Z",
            events=[{"seq": 1, "type": "llm_call", "agent": "coordinator"}],
        )
        assert item.events[0]["agent"] == "coordinator"
        assert item.error is None


class TestSessionTitle:
    """SessionTitle is the restored structured-output schema (§8.2)."""

    def test_accepts_valid_title(self) -> None:
        """A non-empty title within bounds is accepted."""
        assert SessionTitle(title="周报整理").title == "周报整理"

    def test_rejects_empty_title(self) -> None:
        """An empty title fails validation."""
        with pytest.raises(ValidationError):
            SessionTitle(title="")

    def test_rejects_overlong_title(self) -> None:
        """A title over 60 chars fails validation."""
        with pytest.raises(ValidationError):
            SessionTitle(title="x" * 61)


class TestChatResponseExtension:
    """ChatResponse gains the optional interrupt field (§4.5)."""

    def test_interrupt_defaults_to_none(self) -> None:
        """Interrupt stays None on the normal completion path."""
        response = ChatResponse(messages=[Message(role="assistant", content="ok")])
        assert response.interrupt is None

    def test_carries_interrupt_on_limit_exceeded(self) -> None:
        """Interrupt carries the projection on auto-approve limit exceeded."""
        response = ChatResponse(
            messages=[],
            interrupt=InterruptPayload(action_requests=[ActionRequest(tool="bash", args={"cmd": "ls"})]),
        )
        assert response.interrupt is not None
        assert response.interrupt.action_requests[0].tool == "bash"


def test_stream_response_is_removed() -> None:
    """The legacy flat StreamResponse must not survive the G4 reorganisation (§10.4)."""
    import app.schemas.chat as chat_schemas

    assert not hasattr(chat_schemas, "StreamResponse")
