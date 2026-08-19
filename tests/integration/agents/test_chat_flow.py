"""Scenario 5 + 8: session-bound chat round trips and default-app fallback.

Full chain under test: ``POST /auth/session`` binds a published AgentApp ->
the four chat endpoints (/chat, /chat/stream, GET /messages, DELETE /messages)
run through the real runtime path (scripted model, in-memory checkpointer):
ainvoke round trip, SSE frame discipline (chunk frames + source + done final
frame), history projection and clear semantics. Sessions without a binding
fall back to the bootstrapped ``name="default"`` app.
"""

import asyncio
from typing import Any

import pytest
from fastapi.testclient import TestClient
from langchain_core.messages import AIMessage
from prometheus_client import REGISTRY
from sqlmodel import Session as DBSession

from app.core.config import settings
from app.services.agents.bootstrap import ensure_default_agent_app
from tests.conftest import unwrap

from .conftest import assert_error_envelope, parse_sse_events

pytestmark = pytest.mark.integration

API = settings.API_V1_STR


def _publish_app(client: TestClient, headers: dict[str, str], name: str = "chat-app") -> int:
    """Create and publish a minimal AgentApp, returning its id."""
    created = client.post(f"{API}/apps", json={"name": name, "system_prompt": "You are chat."}, headers=headers)
    assert created.status_code == 201, created.text
    app_id = unwrap(created, expected_code=201)["id"]
    published = client.post(f"{API}/apps/{app_id}/publish", headers=headers)
    assert published.status_code == 200, published.text
    return app_id


def _session_headers(client: TestClient, user_headers: dict[str, str], agent_app_id: int | None) -> dict[str, str]:
    """Create a chat session and return its bearer headers."""
    payload: dict[str, Any] = {}
    if agent_app_id is not None:
        payload["agent_app_id"] = agent_app_id
    response = client.post(f"{API}/auth/session", json=payload, headers=user_headers)
    assert response.status_code == 200, response.text
    return {"Authorization": f"Bearer {unwrap(response)['token']['access_token']}"}


def _management_headers(client: TestClient, user_headers: dict[str, str]) -> dict[str, str]:
    """Exchange a user token for a chat-session token (management APIs need it)."""
    response = client.post(f"{API}/auth/session", json={}, headers=user_headers)
    assert response.status_code == 200, response.text
    return {"Authorization": f"Bearer {unwrap(response)['token']['access_token']}"}


def test_chat_endpoints_round_trip_via_runtime(
    client: TestClient,
    user_headers: dict[str, str],
    scripted_model: Any,
    memory_checkpointer: Any,
) -> None:
    """A session bound to a published app chats, streams, reads and clears history."""
    management = _management_headers(client, user_headers)
    app_id = _publish_app(client, management)
    headers = _session_headers(client, user_headers, app_id)

    # -- POST /chat: ainvoke round trip -------------------------------------
    scripted_model.responses = [AIMessage(content="hello from runtime")]
    response = client.post(
        f"{API}/chatbot/chat", json={"messages": [{"role": "user", "content": "hi"}]}, headers=headers
    )
    assert response.status_code == 200, response.text
    messages = unwrap(response)["messages"]
    assert messages[-1]["role"] == "assistant"
    assert messages[-1]["content"] == "hello from runtime"

    # -- POST /chat/stream: chunk frames carry source, final frame done -----
    scripted_model.responses = [AIMessage(content="streamed reply")]
    stream_response = client.post(
        f"{API}/chatbot/chat/stream", json={"messages": [{"role": "user", "content": "hi"}]}, headers=headers
    )
    assert stream_response.status_code == 200, stream_response.text
    assert stream_response.headers["content-type"].startswith("text/event-stream")
    events = parse_sse_events(stream_response.text)
    assert events, "expected at least one SSE frame"
    assert all(event["done"] is False for event in events[:-1])
    assert any(event["content"] == "streamed reply" for event in events[:-1])
    assert all(event.get("source") in ("coordinator", "system") for event in events[:-1])
    final = events[-1]
    assert final["done"] is True
    assert final["content"] == ""

    # -- GET /messages: history projection from the checkpoint --------------
    history = client.get(f"{API}/chatbot/messages", headers=headers)
    assert history.status_code == 200, history.text
    history_messages = unwrap(history)["messages"]
    roles = [message["role"] for message in history_messages]
    contents = [message["content"] for message in history_messages]
    assert roles.count("user") >= 2
    assert "hello from runtime" in contents

    # -- DELETE /messages: clears the thread --------------------------------
    cleared = client.delete(f"{API}/chatbot/messages", headers=headers)
    assert cleared.status_code == 200, cleared.text
    empty = client.get(f"{API}/chatbot/messages", headers=headers)
    assert empty.status_code == 200
    assert unwrap(empty)["messages"] == []


def test_session_binding_rejects_unpublished_app(client: TestClient, user_headers: dict[str, str]) -> None:
    """Binding a session to a draft AgentApp fails; missing ids fail with 404."""
    management = _management_headers(client, user_headers)
    created = client.post(f"{API}/apps", json={"name": "draft-app", "system_prompt": "Draft."}, headers=management)
    assert created.status_code == 201, created.text
    draft_id = unwrap(created, expected_code=201)["id"]

    denied = client.post(f"{API}/auth/session", json={"agent_app_id": draft_id}, headers=user_headers)
    assert_error_envelope(denied, code=422, message="Agent app is not published")

    missing = client.post(f"{API}/auth/session", json={"agent_app_id": 999999}, headers=user_headers)
    assert_error_envelope(missing, code=404, message="Agent app not found")


def test_register_login_session_round_trip(client: TestClient) -> None:
    """Full auth round trip: register -> login -> create session with the token."""
    registered = client.post(
        f"{API}/auth/register",
        json={"email": "bob@example.com", "password": "Passw0rd!Strong", "username": "bob"},
    )
    assert registered.status_code == 200, registered.text
    assert unwrap(registered)["email"] == "bob@example.com"

    # Duplicate registration is rejected with an error envelope.
    duplicate = client.post(
        f"{API}/auth/register",
        json={"email": "bob@example.com", "password": "Passw0rd!Strong", "username": "bob"},
    )
    assert_error_envelope(duplicate, code=400, message="Email already registered")

    # Login with the real credentials issues a user token.
    login = client.post(
        f"{API}/auth/login",
        data={"email": "bob@example.com", "password": "Passw0rd!Strong", "grant_type": "password"},
    )
    assert login.status_code == 200, login.text
    user_headers = {"Authorization": f"Bearer {unwrap(login)['access_token']}"}

    bad_login = client.post(
        f"{API}/auth/login",
        data={"email": "bob@example.com", "password": "WrongPassword1!", "grant_type": "password"},
    )
    assert_error_envelope(bad_login, code=401, message="Incorrect email or password")

    # The token authenticates session creation.
    session = client.post(f"{API}/auth/session", json={}, headers=user_headers)
    assert session.status_code == 200, session.text
    assert unwrap(session)["session_id"]


def test_default_agent_app_out_of_the_box(
    client: TestClient,
    user_headers: dict[str, str],
    db_engine: Any,
    scripted_model: Any,
    memory_checkpointer: Any,
) -> None:
    """Sessions without agent_app_id fall back to the bootstrapped default app."""
    with DBSession(db_engine) as db_session:
        default_app = asyncio.run(ensure_default_agent_app(db_session))
    assert default_app.name == "default"
    assert default_app.status == "published"

    headers = _session_headers(client, user_headers, agent_app_id=None)
    scripted_model.responses = [AIMessage(content="default app reply")]
    response = client.post(
        f"{API}/chatbot/chat", json={"messages": [{"role": "user", "content": "hello default"}]}, headers=headers
    )
    assert response.status_code == 200, response.text
    assert unwrap(response)["messages"][-1]["content"] == "default app reply"


def test_chat_stream_observes_subagent_task_duration(
    client: TestClient,
    user_headers: dict[str, str],
    scripted_model: Any,
    memory_checkpointer: Any,
) -> None:
    """Streaming a delegating chat observes subagent_task_duration_seconds{subagent}."""
    management = _management_headers(client, user_headers)
    created_sub = client.post(
        f"{API}/subagents",
        json={
            "name": "researcher",
            "description": "Research helper",
            "when_to_use": "When research is needed",
            "system_prompt": "You research.",
        },
        headers=management,
    )
    assert created_sub.status_code == 201, created_sub.text

    created = client.post(
        f"{API}/apps",
        json={"name": "delegating-app", "system_prompt": "You delegate.", "subagent_names": ["researcher"]},
        headers=management,
    )
    assert created.status_code == 201, created.text
    app_id = unwrap(created, expected_code=201)["id"]
    assert client.post(f"{API}/apps/{app_id}/publish", headers=management).status_code == 200
    headers = _session_headers(client, user_headers, app_id)

    # Coordinator delegates via the task tool; the subagent answers; coordinator wraps.
    scripted_model.responses = [
        AIMessage(
            content="",
            tool_calls=[
                {
                    "name": "task",
                    "args": {"description": "research it", "subagent_type": "researcher", "task": "research it"},
                    "id": "task-1",
                    "type": "tool_call",
                }
            ],
        ),
        AIMessage(content="subagent result"),
        AIMessage(content="wrapped final"),
    ]

    before = REGISTRY.get_sample_value("subagent_task_duration_seconds_count", {"subagent": "researcher"}) or 0.0
    stream_response = client.post(
        f"{API}/chatbot/chat/stream",
        json={"messages": [{"role": "user", "content": "research this"}]},
        headers=headers,
    )
    assert stream_response.status_code == 200, stream_response.text
    events = parse_sse_events(stream_response.text)

    # Subagent chunks carry their own source; the coordinator frames survive too.
    assert any(event.get("source") == "researcher" for event in events[:-1])
    assert any(event.get("content") == "wrapped final" for event in events[:-1])

    after = REGISTRY.get_sample_value("subagent_task_duration_seconds_count", {"subagent": "researcher"}) or 0.0
    assert after == before + 1
