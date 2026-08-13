"""Unit tests for the chatbot endpoints on top of the AgentApp runtime.

Zero real network / zero real LLM / zero real MCP: ``get_runtime`` is
monkeypatched with a scripted fake runtime, the auth dependency is
overridden with a fake chat Session row, and no database is contacted.
"""

import json
from collections.abc import AsyncGenerator, Generator
from typing import Any

import pytest
from fastapi import FastAPI
from fastapi.testclient import TestClient
from slowapi import _rate_limit_exceeded_handler
from slowapi.errors import RateLimitExceeded

from app.api.v1 import auth as auth_module
from app.api.v1 import chatbot as chatbot_module
from app.api.v1.chatbot import router as chatbot_router
from app.core.config import settings
from app.core.limiter import limiter
from app.models.session import Session as ChatSession
from app.schemas.chat import ChatRequest, Message
from app.services.agents.runtime import StreamChunk

pytestmark = pytest.mark.unit


class FakeRuntime:
    """Scripted AgentAppRuntime double recording every unified-interface call."""

    def __init__(
        self,
        invoke_result: list[Message] | None = None,
        stream_chunks: list[tuple[str, str | None]] | None = None,
        history: list[Message] | None = None,
        invoke_error: Exception | None = None,
        stream_error: Exception | None = None,
    ) -> None:
        """Configure canned replies and optional scripted failures."""
        self.invoke_result = invoke_result or [Message(role="assistant", content="ok")]
        self.stream_chunks = stream_chunks or []
        self.history = history or []
        self.invoke_error = invoke_error
        self.stream_error = stream_error
        self.invoke_calls: list[dict[str, Any]] = []
        self.stream_calls: list[dict[str, Any]] = []
        self.history_calls: list[str] = []
        self.clear_calls: list[str] = []

    async def ainvoke(
        self,
        messages: list[Message],
        *,
        session_id: str,
        user_id: str | None = None,
        username: str | None = None,
    ) -> list[Message]:
        """Record the call, then replay the canned reply (or raise)."""
        self.invoke_calls.append(
            {"messages": messages, "session_id": session_id, "user_id": user_id, "username": username}
        )
        if self.invoke_error is not None:
            raise self.invoke_error
        return self.invoke_result

    def astream(
        self,
        messages: list[Message],
        *,
        session_id: str,
        user_id: str | None = None,
        username: str | None = None,
    ) -> AsyncGenerator[Any, None]:
        """Return a scripted chunk generator (or a failing one)."""
        self.stream_calls.append(
            {"messages": messages, "session_id": session_id, "user_id": user_id, "username": username}
        )
        chunks = list(self.stream_chunks)
        error = self.stream_error

        async def generate() -> AsyncGenerator[Any, None]:
            for content, source in chunks:
                yield StreamChunk(content=content, source=source)
            if error is not None:
                raise error

        return generate()

    async def get_chat_history(self, session_id: str) -> list[Message]:
        """Record the lookup and replay the canned history."""
        self.history_calls.append(session_id)
        return self.history

    async def clear_chat_history(self, session_id: str) -> None:
        """Record the clear request."""
        self.clear_calls.append(session_id)


@pytest.fixture
def fake_session() -> ChatSession:
    """A detached chat Session row standing in for get_current_session."""
    return ChatSession(id="sess-1", user_id=7, name="", username="ann", agent_app_id="42")


@pytest.fixture
def client(fake_session: ChatSession) -> Generator[TestClient, None, None]:
    """Minimal app wiring the chatbot router with limiter state + auth override."""
    app = FastAPI()
    app.state.limiter = limiter
    app.add_exception_handler(RateLimitExceeded, _rate_limit_exceeded_handler)  # pyright: ignore[reportArgumentType]
    app.include_router(chatbot_router)
    app.dependency_overrides[auth_module.get_current_session] = lambda: fake_session
    yield TestClient(app)
    app.dependency_overrides.clear()


@pytest.fixture
def patch_runtime(monkeypatch: pytest.MonkeyPatch) -> Any:
    """Install a scripted fake runtime behind chatbot.get_runtime."""
    fake = FakeRuntime()

    async def fake_get_runtime(db_session: Any, agent_app_id: str | None) -> FakeRuntime:
        fake.resolved_agent_app_id = agent_app_id
        return fake

    monkeypatch.setattr(chatbot_module, "get_runtime", fake_get_runtime)
    return fake


@pytest.fixture(autouse=True)
def disable_session_naming(monkeypatch: pytest.MonkeyPatch) -> None:
    """Keep the session-naming side feature out of these tests by default."""
    monkeypatch.setattr(settings, "SESSION_NAMING_ENABLED", False)


def _chat_body(content: str = "hello") -> dict[str, Any]:
    """Build a minimal ChatRequest JSON body."""
    return {"messages": [{"role": "user", "content": content}]}


def _sse_frames(response_text: str) -> list[dict[str, Any]]:
    """Parse the SSE payload into its JSON frames."""
    return [json.loads(line[len("data: ") :]) for line in response_text.splitlines() if line.startswith("data: ")]


# ---------------------------------------------------------------------------
# POST /chat
# ---------------------------------------------------------------------------


def test_chat_invokes_runtime_with_session_context(client: TestClient, patch_runtime: FakeRuntime) -> None:
    """The runtime receives the request messages plus session-derived context."""
    response = client.post("/chat", json=_chat_body())
    assert response.status_code == 200
    assert response.json()["messages"][-1] == {"role": "assistant", "content": "ok"}

    assert patch_runtime.resolved_agent_app_id == "42"
    call = patch_runtime.invoke_calls[0]
    assert call["session_id"] == "sess-1"
    assert call["user_id"] == "7"
    assert call["username"] == "ann"
    assert [message.content for message in call["messages"]] == ["hello"]


def test_chat_runtime_failure_returns_500(client: TestClient, patch_runtime: FakeRuntime) -> None:
    """Runtime errors surface as a 500 with the original detail."""
    patch_runtime.invoke_error = RuntimeError("boom")
    response = client.post("/chat", json=_chat_body())
    assert response.status_code == 500
    assert response.json()["detail"] == "boom"


def test_chat_triggers_session_naming_when_enabled(
    client: TestClient, patch_runtime: FakeRuntime, fake_session: ChatSession, monkeypatch: pytest.MonkeyPatch
) -> None:
    """maybe_name_session is invoked with the session id/name/messages."""
    monkeypatch.setattr(settings, "SESSION_NAMING_ENABLED", True)
    recorded: list[tuple[Any, ...]] = []
    monkeypatch.setattr(chatbot_module, "maybe_name_session", lambda *args: recorded.append(args))

    response = client.post("/chat", json=_chat_body())
    assert response.status_code == 200
    assert len(recorded) == 1
    session_id, session_name, messages = recorded[0]
    assert (session_id, session_name) == (fake_session.id, fake_session.name)
    assert isinstance(messages[0], Message)


# ---------------------------------------------------------------------------
# POST /chat/stream
# ---------------------------------------------------------------------------


def test_chat_stream_frames_carry_source_and_done_terminator(
    client: TestClient, patch_runtime: FakeRuntime
) -> None:
    """Each chunk becomes one frame with its source; the last frame is done."""
    patch_runtime.stream_chunks = [("hello ", "sub-a"), ("world", "coordinator")]

    response = client.post("/chat/stream", json=_chat_body())
    assert response.status_code == 200

    frames = _sse_frames(response.text)
    assert frames == [
        {"content": "hello ", "done": False, "source": "sub-a", "request_id": frames[0]["request_id"]},
        {"content": "world", "done": False, "source": "coordinator", "request_id": frames[1]["request_id"]},
        {"content": "", "done": True, "source": None, "request_id": frames[2]["request_id"]},
    ]

    call = patch_runtime.stream_calls[0]
    assert call["session_id"] == "sess-1"
    assert call["user_id"] == "7"
    assert call["username"] == "ann"


def test_chat_stream_error_yields_done_error_frame(client: TestClient, patch_runtime: FakeRuntime) -> None:
    """A mid-stream failure terminates with a single done=True error frame."""
    patch_runtime.stream_error = RuntimeError("stream boom")

    response = client.post("/chat/stream", json=_chat_body())
    assert response.status_code == 200

    frames = _sse_frames(response.text)
    assert frames[-1]["content"] == "stream boom"
    assert frames[-1]["done"] is True


# ---------------------------------------------------------------------------
# GET /messages & DELETE /messages
# ---------------------------------------------------------------------------


def test_get_messages_delegates_to_runtime_history(client: TestClient, patch_runtime: FakeRuntime) -> None:
    """GET /messages projects the runtime history into the ChatResponse."""
    patch_runtime.history = [
        Message(role="user", content="hi"),
        Message(role="assistant", content="hello"),
    ]

    response = client.get("/messages")
    assert response.status_code == 200
    assert [message["content"] for message in response.json()["messages"]] == ["hi", "hello"]
    assert patch_runtime.history_calls == ["sess-1"]


def test_clear_messages_delegates_to_runtime_clear(client: TestClient, patch_runtime: FakeRuntime) -> None:
    """DELETE /messages clears the thread via the runtime."""
    response = client.delete("/messages")
    assert response.status_code == 200
    assert response.json() == {"message": "Chat history cleared successfully"}
    assert patch_runtime.clear_calls == ["sess-1"]


# ---------------------------------------------------------------------------
# ChatRequest stays the endpoint input contract
# ---------------------------------------------------------------------------


def test_chat_request_rejects_empty_messages() -> None:
    """The endpoint input schema keeps its min_length=1 guard."""
    with pytest.raises(ValueError):
        ChatRequest(messages=[])


# ---------------------------------------------------------------------------
# _resolve_stream_model — real model name semantics
# ---------------------------------------------------------------------------


def test_resolve_stream_model_prefers_resolved_model_name() -> None:
    """A runtime exposing resolved_model_name labels metrics with the real model."""

    class ResolvedRuntime:
        resolved_model_name = "MiniMax-M3"

    assert chatbot_module._resolve_stream_model(ResolvedRuntime()) == "MiniMax-M3"  # noqa: SLF001


def test_resolve_stream_model_falls_back_to_default_model() -> None:
    """Missing/None resolved_model_name degrades to settings.DEFAULT_LLM_MODEL."""

    class UnresolvedRuntime:
        resolved_model_name = None

    assert chatbot_module._resolve_stream_model(UnresolvedRuntime()) == settings.DEFAULT_LLM_MODEL  # noqa: SLF001
    assert chatbot_module._resolve_stream_model(object()) == settings.DEFAULT_LLM_MODEL  # noqa: SLF001
