"""Structured execution trace collector for sub-agent test runs.

``RunTracer`` is a plain LangChain ``BaseCallbackHandler`` attached alongside
the Langfuse handler in ``run_subagent_once``. It collects an ordered,
JSON-serialisable event stream (``llm_call`` / ``tool_call`` /
``run_finished``) in memory so a finished run can be persisted and later
parsed by scripts to decide whether a behavioural deviation came from an LLM
decision or a tool execution failure. Collection is pure in-memory appending;
nothing is written to the DB or the network from here.

Event shapes (all carry ``seq``, ``type`` and ``started_at``):

* ``llm_call``: input_messages, model, output_text, tool_calls, token_usage,
  duration_seconds, status (success|error), error.
* ``tool_call``: tool, arguments, output, duration_seconds, status, error.
* ``run_finished``: status, turns, duration_seconds, final_messages.
"""

import json
from datetime import UTC, datetime
from typing import Any, Literal, override
from uuid import UUID

from langchain_core.callbacks import BaseCallbackHandler
from langchain_core.messages import AIMessage, BaseMessage, ToolMessage
from langchain_core.outputs import ChatGeneration, LLMResult

# Guard against unbounded DB rows: any serialised string (message content,
# tool arguments/output) is clipped to this many characters.
MAX_FIELD_CHARS = 20_000


def _truncate(text: str) -> str:
    """Clip long serialised payloads so trace rows stay bounded."""
    if len(text) <= MAX_FIELD_CHARS:
        return text
    return text[:MAX_FIELD_CHARS] + f"...[truncated {len(text) - MAX_FIELD_CHARS} chars]"


def _flatten_content(content: Any) -> str:
    """Flatten a message content value (str or content-block list) to text."""
    if isinstance(content, str):
        return _truncate(content)
    if isinstance(content, list):
        merged = " ".join(str(block.get("text", "")) for block in content if isinstance(block, dict))
        return _truncate(merged.strip())
    return _truncate(str(content))


def _serialize_message(message: BaseMessage) -> dict[str, Any]:
    """Project a message into a JSON-friendly dict for the trace stream."""
    entry: dict[str, Any] = {"type": message.type, "content": _flatten_content(message.content)}
    if isinstance(message, AIMessage) and message.tool_calls:
        entry["tool_calls"] = [
            {"name": call.get("name"), "args": call.get("args"), "id": call.get("id")} for call in message.tool_calls
        ]
    if isinstance(message, ToolMessage):
        entry["tool_call_id"] = message.tool_call_id
        entry["name"] = message.name
    return entry


def _first_message(response: LLMResult) -> BaseMessage | None:
    """Extract the first generation's message from an LLMResult (None for non-chat)."""
    generations = response.generations
    if not generations or not generations[0]:
        return None
    first = generations[0][0]
    return first.message if isinstance(first, ChatGeneration) else None


def _normalize_token_usage(response: LLMResult) -> dict[str, int]:
    """Extract token usage from an LLMResult, defaulting every bucket to 0.

    Prefers the per-message ``usage_metadata`` (LangChain 1.x convention) and
    falls back to the provider-level ``llm_output["token_usage"]`` mapping.
    """
    usage: dict[str, int] = {"input_tokens": 0, "output_tokens": 0, "total_tokens": 0}
    metadata = getattr(_first_message(response), "usage_metadata", None)
    if metadata:
        usage["input_tokens"] = int(metadata.get("input_tokens", 0) or 0)
        usage["output_tokens"] = int(metadata.get("output_tokens", 0) or 0)
        usage["total_tokens"] = int(metadata.get("total_tokens", 0) or 0)
        return usage
    raw = (response.llm_output or {}).get("token_usage") or {}
    usage["input_tokens"] = int(raw.get("prompt_tokens", raw.get("input_tokens", 0)) or 0)
    usage["output_tokens"] = int(raw.get("completion_tokens", raw.get("output_tokens", 0)) or 0)
    usage["total_tokens"] = int(raw.get("total_tokens", 0) or 0)
    return usage


def _normalize_tool_arguments(inputs: dict[str, Any] | None, input_str: str) -> dict[str, Any]:
    """Coerce tool start payloads into a dict (structured first, string fallback)."""
    if isinstance(inputs, dict):
        return inputs
    try:
        parsed = json.loads(input_str)
    except (json.JSONDecodeError, TypeError):
        return {"input": _truncate(input_str)}
    return parsed if isinstance(parsed, dict) else {"input": parsed}


def _normalize_tool_output(output: Any) -> str:
    """Flatten a tool result (str / message-like / other) into trace text."""
    if isinstance(output, str):
        return _truncate(output)
    content = getattr(output, "content", None)
    if content is not None:
        return _flatten_content(content)
    return _truncate(str(output))


class RunTracer(BaseCallbackHandler):
    """In-memory collector of the structured trace event stream.

    Args:
        model_name: Upstream model id recorded on every ``llm_call`` event
            (supplied by the caller; not parsed out of ``serialized``).
    """

    def __init__(self, *, model_name: str) -> None:
        """Initialise an empty event stream keyed by callback run ids."""
        super().__init__()
        self.model_name = model_name
        self.events: list[dict[str, Any]] = []
        self._pending: dict[UUID, dict[str, Any]] = {}
        self._seq = 0

    # ------------------------------------------------------------------
    # Internals
    # ------------------------------------------------------------------

    def _start_event(self, event_type: str, **fields: Any) -> dict[str, Any]:
        """Open a new event, register it for completion and append it in order."""
        self._seq += 1
        event: dict[str, Any] = {
            "seq": self._seq,
            "type": event_type,
            "started_at": datetime.now(UTC).isoformat(),
            "duration_seconds": None,
            "status": "running",
            "error": None,
            **fields,
        }
        self.events.append(event)
        return event

    @staticmethod
    def _finish_event(event: dict[str, Any], *, status: Literal["success", "error"], **fields: Any) -> None:
        """Close an open event with its outcome and wall-clock duration."""
        started_at = datetime.fromisoformat(event["started_at"])
        event["duration_seconds"] = round((datetime.now(UTC) - started_at).total_seconds(), 6)
        event["status"] = status
        event.update(fields)

    # ------------------------------------------------------------------
    # LLM hooks
    # ------------------------------------------------------------------

    @override
    def on_chat_model_start(
        self,
        serialized: dict[str, Any],
        messages: list[list[BaseMessage]],
        *,
        run_id: UUID,
        parent_run_id: UUID | None = None,
        tags: list[str] | None = None,
        metadata: dict[str, Any] | None = None,
        **kwargs: Any,
    ) -> None:
        """Record one LLM request: the full input message list and model id."""
        flattened = [_serialize_message(message) for batch in messages for message in batch]
        self._pending[run_id] = self._start_event("llm_call", model=self.model_name, input_messages=flattened)

    @override
    def on_llm_end(
        self,
        response: LLMResult,
        *,
        run_id: UUID,
        parent_run_id: UUID | None = None,
        tags: list[str] | None = None,
        **kwargs: Any,
    ) -> None:
        """Record the LLM response: output text, tool calls and token usage."""
        event = self._pending.pop(run_id, None)
        if event is None:
            return
        output_message = _first_message(response)
        output_text = _flatten_content(output_message.content) if output_message is not None else ""
        tool_calls: list[dict[str, Any]] = []
        if isinstance(output_message, AIMessage):
            tool_calls = [
                {"name": call.get("name"), "args": call.get("args"), "id": call.get("id")}
                for call in output_message.tool_calls
            ]
        self._finish_event(
            event,
            status="success",
            output_text=output_text,
            tool_calls=tool_calls,
            token_usage=_normalize_token_usage(response),
        )

    @override
    def on_llm_error(
        self,
        error: BaseException,
        *,
        run_id: UUID,
        parent_run_id: UUID | None = None,
        tags: list[str] | None = None,
        **kwargs: Any,
    ) -> None:
        """Record an LLM failure on the matching open event."""
        event = self._pending.pop(run_id, None)
        if event is None:
            return
        self._finish_event(event, status="error", error=f"{type(error).__name__}: {error}")

    # ------------------------------------------------------------------
    # Tool hooks
    # ------------------------------------------------------------------

    @override
    def on_tool_start(
        self,
        serialized: dict[str, Any],
        input_str: str,
        *,
        run_id: UUID,
        parent_run_id: UUID | None = None,
        tags: list[str] | None = None,
        metadata: dict[str, Any] | None = None,
        inputs: dict[str, Any] | None = None,
        **kwargs: Any,
    ) -> None:
        """Record one tool invocation start: tool name and input arguments."""
        tool_name = str(serialized.get("name") or kwargs.get("name") or "unknown")
        self._pending[run_id] = self._start_event(
            "tool_call", tool=tool_name, arguments=_normalize_tool_arguments(inputs, input_str)
        )

    @override
    def on_tool_end(
        self,
        output: Any,
        *,
        run_id: UUID,
        parent_run_id: UUID | None = None,
        **kwargs: Any,
    ) -> None:
        """Record the tool result on the matching open event."""
        event = self._pending.pop(run_id, None)
        if event is None:
            return
        self._finish_event(event, status="success", output=_normalize_tool_output(output))

    @override
    def on_tool_error(
        self,
        error: BaseException,
        *,
        run_id: UUID,
        parent_run_id: UUID | None = None,
        **kwargs: Any,
    ) -> None:
        """Record a tool failure on the matching open event."""
        event = self._pending.pop(run_id, None)
        if event is None:
            return
        self._finish_event(event, status="error", error=f"{type(error).__name__}: {error}")

    # ------------------------------------------------------------------
    # Run completion
    # ------------------------------------------------------------------

    @property
    def llm_call_count(self) -> int:
        """Number of LLM calls observed so far (turn fallback on error paths)."""
        return sum(1 for event in self.events if event["type"] == "llm_call")

    def finish(
        self,
        status: Literal["success", "error"],
        final_messages: list[BaseMessage],
        *,
        turns: int,
        duration_seconds: float,
        error: str | None = None,
    ) -> list[dict[str, Any]]:
        """Append the terminal ``run_finished`` event and return the full stream."""
        self._seq += 1
        self.events.append(
            {
                "seq": self._seq,
                "type": "run_finished",
                "started_at": datetime.now(UTC).isoformat(),
                "status": status,
                "turns": turns,
                "duration_seconds": round(duration_seconds, 6),
                "error": error,
                "final_messages": [_serialize_message(message) for message in final_messages],
            }
        )
        return list(self.events)
