"""Scenario 6 + 7: HIL interrupt/resume round trip and fingerprint reassembly.

The chat HTTP endpoints (``/chatbot/*``) were retired in Phase 1 G1, but the
runtime layer they exercised is retained business logic. Both scenarios are
therefore driven directly through ``runtime_module.get_runtime`` /
``runtime.ainvoke`` (the same seam the retired endpoints used):

- an AgentApp with ``interrupt_on`` pauses on the MCP tool call (the
  interrupt value is surfaced to the caller), a structured
  ``{"decisions": [...]}`` reply resumes the thread to completion;
- fingerprint semantics: identical ``get_runtime`` calls reuse the cached
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
from app.models.user import User
from app.schemas.chat import Message
from app.services.agents import runtime as runtime_module
from tests.conftest import unwrap

from .conftest import make_mcp_tool

pytestmark = pytest.mark.integration

API = settings.API_V1_STR

# Namespaced catalog name of the fake it_search tool (server "it-server").
IT_SEARCH = "it-server__it_search"


def _setup_mcp_server(client: TestClient, headers: dict[str, str], fake_tools: dict[str, list[Any]]) -> None:
    """Register an http MCP server exposing the fake ``it_search`` tool."""
    fake_tools["it-server"] = [make_mcp_tool("it_search", reply="tool-result")]
    body = {"name": "it-server", "transport": "http", "url": "https://mcp.example.com/sse"}
    response = client.post(f"{API}/mcp-servers", json=body, headers=headers)
    assert response.status_code == 201, response.text


def test_hil_interrupt_then_resume_round_trip(
    client: TestClient,
    user: User,
    user_headers: dict[str, str],
    fake_mcp: dict[str, list[Any]],
    scripted_model: Any,
    memory_checkpointer: Any,
    db_engine: Any,
) -> None:
    """interrupt_on pauses before the MCP tool; a decisions reply resumes to completion."""
    _setup_mcp_server(client, user_headers, fake_mcp)

    created = client.post(
        f"{API}/apps",
        json={
            "name": "hil-app",
            "system_prompt": "You need approval.",
            "allowed_tools": [IT_SEARCH],
            "interrupt_on": {IT_SEARCH: True},
        },
        headers=user_headers,
    )
    assert created.status_code == 201, created.text
    app_id = unwrap(created, expected_code=201)["id"]
    assert client.post(f"{API}/apps/{app_id}/publish", headers=user_headers).status_code == 200

    with DBSession(db_engine) as db_session:
        runtime = asyncio.run(runtime_module.get_runtime(db_session, app_id, user_id=user.id))

    # Turn 1: the scripted model calls it_search; the HIL gate interrupts.
    scripted_model.responses = [
        AIMessage(
            content="",
            tool_calls=[{"name": IT_SEARCH, "args": {}, "id": "tc-fixed", "type": "tool_call"}],
        ),
        AIMessage(content="approved-final"),
    ]
    first = asyncio.run(
        runtime.ainvoke(
            [Message(role="user", content="search it")],
            session_id="hil-thread-1",
        )
    )
    assert len(first) == 1
    interrupt_content = first[0].content
    assert first[0].role == "assistant"
    assert IT_SEARCH in interrupt_content  # interrupt value names the pending action
    assert scripted_model.n == 1  # paused before the tool ran

    # Turn 2: approve the pending action; the run resumes to completion.
    second = asyncio.run(
        runtime.ainvoke(
            [Message(role="user", content='{"decisions": [{"type": "approve"}]}')],
            session_id="hil-thread-1",
        )
    )
    assert second[-1].content == "approved-final"
    assert scripted_model.n == 2  # exactly one extra model call for the resume


def test_runtime_cache_hit_and_fingerprint_reassembly(
    client: TestClient,
    user: User,
    user_headers: dict[str, str],
    fake_mcp: dict[str, list[Any]],
    scripted_model: Any,
    memory_checkpointer: Any,
    db_engine: Any,
) -> None:
    """Same fingerprint reuses the cached runtime; MCP changes rebuild it."""
    _setup_mcp_server(client, user_headers, fake_mcp)

    created = client.post(
        f"{API}/apps",
        json={"name": "cached-app", "system_prompt": "You are cached.", "allowed_tools": [IT_SEARCH]},
        headers=user_headers,
    )
    assert created.status_code == 201, created.text
    app_id = unwrap(created, expected_code=201)["id"]
    assert client.post(f"{API}/apps/{app_id}/publish", headers=user_headers).status_code == 200

    with DBSession(db_engine) as db_session:
        runtime_1 = asyncio.run(runtime_module.get_runtime(db_session, app_id, user_id=user.id))
        runtime_2 = asyncio.run(runtime_module.get_runtime(db_session, app_id, user_id=user.id))
    assert runtime_1 is runtime_2  # identical fingerprint -> cache hit

    # Changing the MCP server configuration changes the fingerprint.
    patched = client.patch(
        f"{API}/mcp-servers/it-server", json={"url": "https://mcp.example.com/v2"}, headers=user_headers
    )
    assert patched.status_code == 200, patched.text

    with DBSession(db_engine) as db_session:
        runtime_3 = asyncio.run(runtime_module.get_runtime(db_session, app_id, user_id=user.id))
    assert runtime_3 is not runtime_1  # fingerprint changed -> fresh runtime
