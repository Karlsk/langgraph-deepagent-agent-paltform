"""Scenario 6 + 7: HIL interrupt/resume round trip and fingerprint reassembly.

Full chain under test: an AgentApp with ``interrupt_on`` pauses on the MCP
tool call (the interrupt value is surfaced through the chat endpoint), a
structured ``{"decisions": [...]}`` reply resumes the thread to completion.
Fingerprint semantics: identical ``get_runtime`` calls reuse the cached
runtime instance; an MCP configuration change invalidates the fingerprint
and yields a fresh runtime.
"""

import asyncio
from typing import Any

import pytest
from fastapi.testclient import TestClient
from langchain_core.messages import AIMessage
from sqlmodel import Session as DBSession

from app.core.config import settings
from app.services.agents import runtime as runtime_module
from tests.conftest import unwrap

from .conftest import make_mcp_tool

pytestmark = pytest.mark.integration

API = settings.API_V1_STR


def _auth(client: TestClient, user_headers: dict[str, str]) -> dict[str, str]:
    """Exchange a user token for a chat-session token (management APIs need it)."""
    response = client.post(f"{API}/auth/session", json={}, headers=user_headers)
    assert response.status_code == 200, response.text
    return {"Authorization": f"Bearer {unwrap(response)['token']['access_token']}"}


def _setup_mcp_server(client: TestClient, headers: dict[str, str], fake_tools: dict[str, list[Any]]) -> None:
    """Register an http MCP server exposing the fake ``it_search`` tool."""
    fake_tools["it-server"] = [make_mcp_tool("it_search", reply="tool-result")]
    body = {"name": "it-server", "transport": "http", "url": "https://mcp.example.com/sse"}
    response = client.post(f"{API}/mcp-servers", json=body, headers=headers)
    assert response.status_code == 201, response.text


# Namespaced catalog name of the fake it_search tool (server "it-server").
IT_SEARCH = "it-server__it_search"


def test_hil_interrupt_then_resume_round_trip(
    client: TestClient,
    user_headers: dict[str, str],
    fake_mcp: dict[str, list[Any]],
    scripted_model: Any,
    memory_checkpointer: Any,
) -> None:
    """interrupt_on pauses before the MCP tool; a decisions reply resumes to completion."""
    headers = _auth(client, user_headers)
    _setup_mcp_server(client, headers, fake_mcp)

    created = client.post(
        f"{API}/apps",
        json={
            "name": "hil-app",
            "system_prompt": "You need approval.",
            "allowed_tools": [IT_SEARCH],
            "interrupt_on": {IT_SEARCH: True},
        },
        headers=headers,
    )
    assert created.status_code == 201, created.text
    app_id = unwrap(created, expected_code=201)["id"]
    assert client.post(f"{API}/apps/{app_id}/publish", headers=headers).status_code == 200

    session = client.post(f"{API}/auth/session", json={"agent_app_id": app_id}, headers=user_headers)
    assert session.status_code == 200, session.text
    chat_headers = {"Authorization": f"Bearer {unwrap(session)['token']['access_token']}"}

    # Turn 1: the scripted model calls it_search; the HIL gate interrupts.
    scripted_model.responses = [
        AIMessage(
            content="",
            tool_calls=[{"name": IT_SEARCH, "args": {}, "id": "tc-fixed", "type": "tool_call"}],
        ),
        AIMessage(content="approved-final"),
    ]
    first = client.post(
        f"{API}/chatbot/chat", json={"messages": [{"role": "user", "content": "search it"}]}, headers=chat_headers
    )
    assert first.status_code == 200, first.text
    interrupt_messages = unwrap(first)["messages"]
    assert len(interrupt_messages) == 1
    interrupt_content = interrupt_messages[0]["content"]
    assert interrupt_messages[0]["role"] == "assistant"
    assert IT_SEARCH in interrupt_content  # interrupt value names the pending action
    assert scripted_model.n == 1  # paused before the tool ran

    # Turn 2: approve the pending action; the run resumes to completion.
    second = client.post(
        f"{API}/chatbot/chat",
        json={"messages": [{"role": "user", "content": '{"decisions": [{"type": "approve"}]}'}]},
        headers=chat_headers,
    )
    assert second.status_code == 200, second.text
    final_messages = unwrap(second)["messages"]
    assert final_messages[-1]["content"] == "approved-final"
    assert scripted_model.n == 2  # exactly one extra model call for the resume


def test_runtime_cache_hit_and_fingerprint_reassembly(
    client: TestClient,
    user_headers: dict[str, str],
    fake_mcp: dict[str, list[Any]],
    scripted_model: Any,
    memory_checkpointer: Any,
    db_engine: Any,
) -> None:
    """Same fingerprint reuses the cached runtime; MCP changes rebuild it."""
    headers = _auth(client, user_headers)
    _setup_mcp_server(client, headers, fake_mcp)

    created = client.post(
        f"{API}/apps",
        json={"name": "cached-app", "system_prompt": "You are cached.", "allowed_tools": [IT_SEARCH]},
        headers=headers,
    )
    assert created.status_code == 201, created.text
    app_id = unwrap(created, expected_code=201)["id"]
    assert client.post(f"{API}/apps/{app_id}/publish", headers=headers).status_code == 200

    with DBSession(db_engine) as db_session:
        runtime_1 = asyncio.run(runtime_module.get_runtime(db_session, str(app_id)))
        runtime_2 = asyncio.run(runtime_module.get_runtime(db_session, str(app_id)))
    assert runtime_1 is runtime_2  # identical fingerprint -> cache hit

    # Changing the MCP server configuration changes the fingerprint.
    patched = client.patch(f"{API}/mcp-servers/it-server", json={"url": "https://mcp.example.com/v2"}, headers=headers)
    assert patched.status_code == 200, patched.text

    with DBSession(db_engine) as db_session:
        runtime_3 = asyncio.run(runtime_module.get_runtime(db_session, str(app_id)))
    assert runtime_3 is not runtime_1  # fingerprint changed -> fresh runtime
