"""Scenario 1 + 4: MCP registration flows into the tool catalog and AgentApp publish.

Full chain under test: admin API creates an http MCP server with ``${ENV_VAR}``
placeholder headers -> the merged tool catalog exposes builtin + mcp entries
with source labels -> an AgentApp bound to skill/subagent/MCP tool publishes
after whitelist validation and appears in ``GET /apps/published``.
"""

from typing import Any

import pytest
from fastapi.testclient import TestClient
from langchain_core.messages import AIMessage

from app.core.config import settings
from app.services.agents import assembly

from .conftest import make_mcp_tool

pytestmark = pytest.mark.integration

API = settings.API_V1_STR


def _auth(client: TestClient, user_headers: dict[str, str]) -> dict[str, str]:
    """Exchange a user token for a chat-session token (management APIs need it)."""
    response = client.post(f"{API}/auth/session", json={}, headers=user_headers)
    assert response.status_code == 200, response.text
    token = response.json()["token"]["access_token"]
    return {"Authorization": f"Bearer {token}"}


def _create_mcp_server(client: TestClient, headers: dict[str, str], fake_tools: dict[str, list[Any]], monkeypatch: pytest.MonkeyPatch) -> dict[str, Any]:
    """Register an http MCP server whose fake tools are pre-registered."""
    monkeypatch.setenv("IT_MCP_AUTH", "secret-token")
    fake_tools["it-server"] = [make_mcp_tool("it_search")]
    body = {
        "name": "it-server",
        "transport": "http",
        "url": "https://mcp.example.com/sse",
        "headers": {"Authorization": "${IT_MCP_AUTH}"},
    }
    response = client.post(f"{API}/agent-apps/mcp-servers", json=body, headers=headers)
    assert response.status_code == 201, response.text
    return response.json()


def test_mcp_server_registration_feeds_tool_catalog(
    client: TestClient, user_headers: dict[str, str], fake_mcp: dict[str, list[Any]], monkeypatch: pytest.MonkeyPatch
) -> None:
    """Creating an http MCP server makes its tools appear in the merged catalog."""
    headers = _auth(client, user_headers)

    # Baseline: only builtin entries, all labelled source=builtin.
    baseline = client.get(f"{API}/agent-apps/tools/catalog", headers=headers)
    assert baseline.status_code == 200
    baseline_names = {entry["name"] for entry in baseline.json()}
    assert {"duckduckgo_results_json", "ask_human"} <= baseline_names
    assert all(entry["source"] == "builtin" for entry in baseline.json())

    created = _create_mcp_server(client, headers, fake_mcp, monkeypatch)
    assert created["headers"] == {"Authorization": "${IT_MCP_AUTH}"}
    assert created["content_hash"]

    catalog = client.get(f"{API}/agent-apps/tools/catalog", headers=headers)
    assert catalog.status_code == 200
    entries = catalog.json()
    by_name = {entry["name"]: entry for entry in entries}

    assert by_name["duckduckgo_results_json"]["source"] == "builtin"
    assert by_name["ask_human"]["source"] == "builtin"
    assert by_name["it_search"]["source"] == "mcp"
    assert by_name["it_search"]["server"] == "it-server"


def test_agent_app_publish_chain_with_skill_subagent_and_mcp_tool(
    client: TestClient,
    user_headers: dict[str, str],
    fake_mcp: dict[str, list[Any]],
    scripted_model: Any,
    memory_checkpointer: Any,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    """Create -> out-of-whitelist publish 422 -> fix -> publish -> visible in /apps/published."""
    headers = _auth(client, user_headers)
    _create_mcp_server(client, headers, fake_mcp, monkeypatch)

    # Global skill (direct input) + subagent with inherited fields left blank.
    skill = client.post(
        f"{API}/agent-apps/skills",
        json={"name": "report-style", "description": "Report style guide", "body": "# report-style\n\n## Steps\n1. draft\n"},
        headers=headers,
    )
    assert skill.status_code == 201, skill.text
    subagent = client.post(
        f"{API}/agent-apps/subagents",
        json={
            "name": "researcher",
            "description": "Research helper",
            "when_to_use": "When research is needed",
            "system_prompt": "You are a researcher.",
        },
        headers=headers,
    )
    assert subagent.status_code == 201, subagent.text
    assert subagent.json()["allowed_tools"] is None
    assert subagent.json()["model"] is None

    # AgentApp referencing both assets plus a whitelist containing an unknown tool.
    created = client.post(
        f"{API}/agent-apps/apps",
        json={
            "name": "support-app",
            "system_prompt": "You are support.",
            "skill_names": ["report-style"],
            "subagent_names": ["researcher"],
            "allowed_tools": ["it_search", "ghost_tool"],
        },
        headers=headers,
    )
    assert created.status_code == 201, created.text
    app_id = created.json()["id"]
    assert created.json()["status"] == "draft"

    # Out-of-whitelist publish is rejected with 422 naming the offender.
    denied = client.post(f"{API}/agent-apps/apps/{app_id}/publish", headers=headers)
    assert denied.status_code == 422
    assert "ghost_tool" in denied.text

    # Fix the whitelist, publish succeeds, /apps/published lists the app.
    fixed = client.patch(f"{API}/agent-apps/apps/{app_id}", json={"allowed_tools": ["it_search"]}, headers=headers)
    assert fixed.status_code == 200, fixed.text
    published = client.post(f"{API}/agent-apps/apps/{app_id}/publish", headers=headers)
    assert published.status_code == 200, published.text
    assert published.json()["status"] == "published"
    assert published.json()["published_hash"]

    listing = client.get(f"{API}/agent-apps/apps/published", headers=headers)
    assert listing.status_code == 200
    names = [row["name"] for row in listing.json()]
    assert "support-app" in names


def test_skill_content_refreshed_on_reassembly(
    client: TestClient,
    user_headers: dict[str, str],
    scripted_model: Any,
    memory_checkpointer: Any,
) -> None:
    """Updating a global skill changes the user copy after the next assembly."""
    headers = _auth(client, user_headers)
    skill = client.post(
        f"{API}/agent-apps/skills",
        json={"name": "style-guide", "description": "Style", "body": "# style-guide\n\nversion-1\n"},
        headers=headers,
    )
    assert skill.status_code == 201, skill.text

    app = client.post(
        f"{API}/agent-apps/apps",
        json={"name": "styled-app", "system_prompt": "You are styled.", "skill_names": ["style-guide"]},
        headers=headers,
    )
    assert app.status_code == 201, app.text
    app_id = app.json()["id"]
    assert client.post(f"{API}/agent-apps/apps/{app_id}/publish", headers=headers).status_code == 200

    session = client.post(f"{API}/auth/session", json={"agent_app_id": app_id}, headers=user_headers)
    assert session.status_code == 200, session.text
    session_token = {"Authorization": f"Bearer {session.json()['token']['access_token']}"}

    # First chat compiles the app: the user copy carries version-1.
    scripted_model.responses = [AIMessage(content="styled answer")]
    chat = client.post(f"{API}/chatbot/chat", json={"messages": [{"role": "user", "content": "hi"}]}, headers=session_token)
    assert chat.status_code == 200, chat.text

    skills_root = settings.SKILLS_ROOT
    user_copy = f"{skills_root}/users/system/style-guide/SKILL.md"
    with open(user_copy, encoding="utf-8") as handle:
        assert "version-1" in handle.read()

    # Update the global skill; the next compile re-materializes the copy.
    updated = client.patch(f"{API}/agent-apps/skills/style-guide", json={"body": "# style-guide\n\nversion-2\n"}, headers=headers)
    assert updated.status_code == 200, updated.text

    # Clear caches to model a restart, then chat again (reassembly path).
    assembly.clear_compile_cache()
    from app.services.agents import runtime as runtime_module

    runtime_module.clear_runtime_cache()

    scripted_model.responses = [AIMessage(content="styled answer v2")]
    chat2 = client.post(f"{API}/chatbot/chat", json={"messages": [{"role": "user", "content": "hi again"}]}, headers=session_token)
    assert chat2.status_code == 200, chat2.text
    with open(user_copy, encoding="utf-8") as handle:
        assert "version-2" in handle.read()
