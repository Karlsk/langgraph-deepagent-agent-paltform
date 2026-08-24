"""Unit tests for the structured execution trace collector (RunTracer).

Zero LLM / zero network: the tracer's callback hooks are exercised directly
with hand-built langchain-core payloads, mirroring what the LangGraph
runtime emits during a sub-agent test run.
"""

import uuid

import pytest
from langchain_core.messages import AIMessage, HumanMessage, ToolMessage
from langchain_core.outputs import ChatGeneration, LLMResult

from app.services.agents.run_tracer import MAX_FIELD_CHARS, RunTracer

pytestmark = pytest.mark.unit


def _run_id() -> uuid.UUID:
    """Mint a fresh callback run id."""
    return uuid.uuid4()


def _llm_result(message: AIMessage, llm_output: dict | None = None) -> LLMResult:
    """Wrap one generation into an LLMResult as chat models emit it."""
    return LLMResult(generations=[[ChatGeneration(message=message)]], llm_output=llm_output)


# ---------------------------------------------------------------------------
# LLM call events
# ---------------------------------------------------------------------------


def test_llm_call_event_records_full_request_and_response() -> None:
    """on_chat_model_start + on_llm_end produce one complete llm_call event."""
    tracer = RunTracer(model_name="gpt-5-mini")
    run_id = _run_id()

    tracer.on_chat_model_start(
        {"name": "ChatOpenAI"}, [[HumanMessage(content="hello"), AIMessage(content="hi")]], run_id=run_id
    )
    tracer.on_llm_end(
        _llm_result(
            AIMessage(
                content="final answer", usage_metadata={"input_tokens": 3, "output_tokens": 2, "total_tokens": 5}
            )
        ),
        run_id=run_id,
    )

    assert len(tracer.events) == 1
    event = tracer.events[0]
    assert event["seq"] == 1
    assert event["type"] == "llm_call"
    assert event["model"] == "gpt-5-mini"
    assert event["status"] == "success"
    assert event["duration_seconds"] is not None
    assert [message["type"] for message in event["input_messages"]] == ["human", "ai"]
    assert event["input_messages"][0]["content"] == "hello"
    assert event["output_text"] == "final answer"
    assert event["tool_calls"] == []
    assert event["token_usage"] == {"input_tokens": 3, "output_tokens": 2, "total_tokens": 5}


def test_llm_call_event_captures_tool_calls_from_response() -> None:
    """Tool calls emitted by the model are projected into the event."""
    tracer = RunTracer(model_name="m")
    run_id = _run_id()
    tracer.on_chat_model_start({}, [[HumanMessage(content="go")]], run_id=run_id)
    reply = AIMessage(
        content="",
        tool_calls=[{"name": "echo", "args": {"text": "hi"}, "id": "tc-1", "type": "tool_call"}],
    )
    tracer.on_llm_end(_llm_result(reply), run_id=run_id)

    event = tracer.events[0]
    assert event["tool_calls"] == [{"name": "echo", "args": {"text": "hi"}, "id": "tc-1"}]
    # No usage metadata on the reply -> zero buckets, never missing keys.
    assert event["token_usage"] == {"input_tokens": 0, "output_tokens": 0, "total_tokens": 0}


def test_llm_call_event_falls_back_to_llm_output_token_usage() -> None:
    """Provider-level llm_output token_usage is used when usage_metadata is absent."""
    tracer = RunTracer(model_name="m")
    run_id = _run_id()
    tracer.on_chat_model_start({}, [[HumanMessage(content="go")]], run_id=run_id)
    tracer.on_llm_end(
        _llm_result(
            AIMessage(content="ok"),
            llm_output={"token_usage": {"prompt_tokens": 7, "completion_tokens": 4, "total_tokens": 11}},
        ),
        run_id=run_id,
    )

    assert tracer.events[0]["token_usage"] == {"input_tokens": 7, "output_tokens": 4, "total_tokens": 11}


def test_llm_error_marks_open_event_and_keeps_input() -> None:
    """on_llm_error closes the matching event with status=error and the reason."""
    tracer = RunTracer(model_name="m")
    run_id = _run_id()
    tracer.on_chat_model_start({}, [[HumanMessage(content="go")]], run_id=run_id)

    tracer.on_llm_error(RuntimeError("upstream 500"), run_id=run_id)

    event = tracer.events[0]
    assert event["status"] == "error"
    assert event["error"] == "RuntimeError: upstream 500"
    assert event["input_messages"][0]["content"] == "go"


# ---------------------------------------------------------------------------
# Tool call events
# ---------------------------------------------------------------------------


def test_tool_call_event_records_arguments_and_output() -> None:
    """on_tool_start + on_tool_end produce one complete tool_call event."""
    tracer = RunTracer(model_name="m")
    run_id = _run_id()

    tracer.on_tool_start({"name": "echo"}, '{"text": "hi"}', run_id=run_id, inputs={"text": "hi"})
    tracer.on_tool_end("echo: hi", run_id=run_id)

    event = tracer.events[0]
    assert event["type"] == "tool_call"
    assert event["tool"] == "echo"
    assert event["arguments"] == {"text": "hi"}
    assert event["output"] == "echo: hi"
    assert event["status"] == "success"
    assert event["duration_seconds"] is not None


def test_tool_start_parses_arguments_from_input_str_without_structured_inputs() -> None:
    """When no structured inputs arrive, the JSON input_str is parsed."""
    tracer = RunTracer(model_name="m")
    run_id = _run_id()

    tracer.on_tool_start({"name": "echo"}, '{"text": "raw"}', run_id=run_id)
    tracer.on_tool_end("ok", run_id=run_id)

    assert tracer.events[0]["arguments"] == {"text": "raw"}


def test_tool_start_wraps_unparseable_input_str() -> None:
    """Non-JSON input strings are preserved verbatim under the input key."""
    tracer = RunTracer(model_name="m")
    run_id = _run_id()

    tracer.on_tool_start({"name": "echo"}, "not-json", run_id=run_id)
    tracer.on_tool_end("ok", run_id=run_id)

    assert tracer.events[0]["arguments"] == {"input": "not-json"}


def test_tool_error_marks_open_event() -> None:
    """on_tool_error closes the matching event with status=error."""
    tracer = RunTracer(model_name="m")
    run_id = _run_id()
    tracer.on_tool_start({"name": "boom"}, "{}", run_id=run_id, inputs={})

    tracer.on_tool_error(ValueError("bad args"), run_id=run_id)

    event = tracer.events[0]
    assert event["status"] == "error"
    assert event["error"] == "ValueError: bad args"


def test_tool_end_flattens_message_like_output() -> None:
    """Message-shaped tool outputs (content attr) are flattened to text."""
    tracer = RunTracer(model_name="m")
    run_id = _run_id()
    tracer.on_tool_start({"name": "echo"}, "{}", run_id=run_id, inputs={})

    tracer.on_tool_end(ToolMessage(content="tool says hi", tool_call_id="tc-1"), run_id=run_id)

    assert tracer.events[0]["output"] == "tool says hi"


# ---------------------------------------------------------------------------
# Completion & guards
# ---------------------------------------------------------------------------


def test_finish_appends_run_finished_and_returns_stream() -> None:
    """finish() appends the terminal event and returns the full ordered stream."""
    tracer = RunTracer(model_name="m")
    run_id = _run_id()
    tracer.on_chat_model_start({}, [[HumanMessage(content="go")]], run_id=run_id)
    tracer.on_llm_end(_llm_result(AIMessage(content="done")), run_id=run_id)

    events = tracer.finish(
        "success", [HumanMessage(content="go"), AIMessage(content="done")], turns=1, duration_seconds=1.25
    )

    assert [event["type"] for event in events] == ["llm_call", "run_finished"]
    terminal = events[-1]
    assert terminal["status"] == "success"
    assert terminal["turns"] == 1
    assert terminal["duration_seconds"] == 1.25
    assert terminal["error"] is None
    assert [message["type"] for message in terminal["final_messages"]] == ["human", "ai"]
    assert tracer.llm_call_count == 1


def test_long_payloads_are_truncated() -> None:
    """Oversized message content is clipped to MAX_FIELD_CHARS with a marker."""
    tracer = RunTracer(model_name="m")
    run_id = _run_id()
    huge = "x" * (MAX_FIELD_CHARS + 500)

    tracer.on_chat_model_start({}, [[HumanMessage(content=huge)]], run_id=run_id)
    tracer.on_llm_end(_llm_result(AIMessage(content=huge)), run_id=run_id)

    event = tracer.events[0]
    content = event["input_messages"][0]["content"]
    assert len(content) < len(huge)
    assert content.startswith("x" * 100)
    assert "truncated" in content
    assert event["output_text"].endswith("]")


def test_orphan_end_events_are_ignored() -> None:
    """End/error hooks without a matching start never raise or append."""
    tracer = RunTracer(model_name="m")
    tracer.on_llm_end(_llm_result(AIMessage(content="x")), run_id=_run_id())
    tracer.on_tool_end("x", run_id=_run_id())
    tracer.on_tool_error(RuntimeError("x"), run_id=_run_id())
    assert tracer.events == []
