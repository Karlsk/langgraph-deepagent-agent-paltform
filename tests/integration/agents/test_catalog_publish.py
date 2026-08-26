"""Scenario 1 + 4: MCP registration flows into the tool catalog and AgentApp publish.

Full chain under test: admin API creates an http MCP server with ``${ENV_VAR}``
placeholder headers -> the merged tool catalog exposes builtin + mcp entries
with source labels -> an AgentApp bound to skill/subagent/MCP tool publishes
after whitelist validation and appears in ``GET /apps/published``.

Phase 1 G1: management APIs authenticate with the user access token directly
(the ``POST /auth/session`` exchange is retired); skill re-materialisation is
driven through ``runtime_module.get_runtime`` instead of the retired chat
endpoint.
"""

import asyncio
import os
from typing import Any

import pytest
from fastapi.testclient import TestClient
from sqlmodel import Session as DBSession

from app.core.config import settings
from app.models.user import User
from app.services.agents import assembly
from app.services.agents import runtime as runtime_module
from tests.conftest import unwrap

from .conftest import assert_error_envelope, make_mcp_tool

pytestmark = pytest.mark.integration

API = settings.API_V1_STR


def _create_mcp_server(
    client: TestClient, headers: dict[str, str], fake_tools: dict[str, list[Any]], monkeypatch: pytest.MonkeyPatch
) -> dict[str, Any]:
    """Register an http MCP server whose fake tools are pre-registered."""
    monkeypatch.setenv("IT_MCP_AUTH", "secret-token")
    fake_tools["it-server"] = [make_mcp_tool("it_search")]
    body = {
        "name": "it-server",
        "transport": "http",
        "url": "https://mcp.example.com/sse",
        "headers": {"Authorization": "${IT_MCP_AUTH}"},
    }
    response = client.post(f"{API}/mcp-servers", json=body, headers=headers)
    assert response.status_code == 201, response.text
    return unwrap(response, expected_code=201)


def test_mcp_server_registration_feeds_tool_catalog(
    client: TestClient, user_headers: dict[str, str], fake_mcp: dict[str, list[Any]], monkeypatch: pytest.MonkeyPatch
) -> None:
    """Creating an http MCP server makes its tools appear in the merged catalog."""
    headers = user_headers

    # Baseline: only builtin entries, all labelled source=builtin.
    baseline = client.get(f"{API}/tools/catalog", headers=headers)
    assert baseline.status_code == 200
    baseline_entries = unwrap(baseline)
    baseline_names = {entry["name"] for entry in baseline_entries}
    assert {"duckduckgo_results_json", "ask_human"} <= baseline_names
    assert all(entry["source"] == "builtin" for entry in baseline_entries)

    created = _create_mcp_server(client, headers, fake_mcp, monkeypatch)
    assert created["headers"] == {"Authorization": "${IT_MCP_AUTH}"}
    assert created["content_hash"]

    catalog = client.get(f"{API}/tools/catalog", headers=headers)
    assert catalog.status_code == 200
    entries = unwrap(catalog)
    by_name = {entry["name"]: entry for entry in entries}

    assert by_name["duckduckgo_results_json"]["source"] == "builtin"
    assert by_name["ask_human"]["source"] == "builtin"
    assert by_name["it-server__it_search"]["source"] == "mcp"
    assert by_name["it-server__it_search"]["server"] == "it-server"


def test_agent_app_publish_chain_with_skill_subagent_and_mcp_tool(
    client: TestClient,
    user_headers: dict[str, str],
    fake_mcp: dict[str, list[Any]],
    scripted_model: Any,
    memory_checkpointer: Any,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    """Create -> out-of-whitelist publish 422 -> fix -> publish -> visible in /apps/published."""
    headers = user_headers
    _create_mcp_server(client, headers, fake_mcp, monkeypatch)

    # Global skill (direct input) + subagent with inherited fields left blank.
    skill = client.post(
        f"{API}/skills",
        json={
            "name": "report-style",
            "description": "Report style guide",
            "body": "# report-style\n\n## Steps\n1. draft\n",
        },
        headers=headers,
    )
    assert skill.status_code == 201, skill.text
    subagent = client.post(
        f"{API}/subagents",
        json={
            "name": "researcher",
            "description": "Research helper",
            "when_to_use": "When research is needed",
            "system_prompt": "You are a researcher.",
        },
        headers=headers,
    )
    assert subagent.status_code == 201, subagent.text
    subagent_payload = unwrap(subagent, expected_code=201)
    assert subagent_payload["allowed_tools"] is None
    assert subagent_payload["model"] is None

    # AgentApp referencing both assets plus a whitelist containing an unknown tool.
    created = client.post(
        f"{API}/apps",
        json={
            "name": "support-app",
            "system_prompt": "You are support.",
            "skill_names": ["report-style"],
            "subagent_names": ["researcher"],
            "allowed_tools": ["it-server__it_search", "ghost_tool"],
        },
        headers=headers,
    )
    assert created.status_code == 201, created.text
    created_payload = unwrap(created, expected_code=201)
    app_id = created_payload["id"]
    assert created_payload["status"] == "draft"

    # Out-of-whitelist publish is rejected with a 422 envelope naming the offender.
    denied = client.post(f"{API}/apps/{app_id}/publish", headers=headers)
    assert_error_envelope(denied, code=422)
    assert "ghost_tool" in denied.json()["message"]

    # Fix the whitelist, publish succeeds, /apps/published lists the app.
    fixed = client.patch(f"{API}/apps/{app_id}", json={"allowed_tools": ["it-server__it_search"]}, headers=headers)
    assert fixed.status_code == 200, fixed.text
    published = client.post(f"{API}/apps/{app_id}/publish", headers=headers)
    assert published.status_code == 200, published.text
    published_payload = unwrap(published)
    assert published_payload["status"] == "published"
    assert published_payload["published_hash"]

    listing = client.get(f"{API}/apps/published", headers=headers)
    assert listing.status_code == 200
    names = [row["name"] for row in unwrap(listing)]
    assert "support-app" in names


def test_skill_content_refreshed_on_reassembly(
    client: TestClient,
    user: User,
    user_headers: dict[str, str],
    scripted_model: Any,
    memory_checkpointer: Any,
    db_engine: Any,
) -> None:
    """Republishing a skill edit refreshes the user copy on the next runtime call.

    G2 v3.3 chain: publish snapshots the skill into the Agent layer; the
    associate endpoint builds the combined User layer; a later skill edit +
    republish changes the Agent layer, and the next ``get_runtime`` lazy
    validation resyncs the User layer to the new content.
    """
    headers = user_headers
    skill = client.post(
        f"{API}/skills",
        json={"name": "style-guide", "description": "Style", "body": "# style-guide\n\nversion-1\n"},
        headers=headers,
    )
    assert skill.status_code == 201, skill.text

    app = client.post(
        f"{API}/apps",
        json={"name": "styled-app", "system_prompt": "You are styled.", "skill_names": ["style-guide"]},
        headers=headers,
    )
    assert app.status_code == 201, app.text
    app_id = unwrap(app, expected_code=201)["id"]
    assert client.post(f"{API}/apps/{app_id}/publish", headers=headers).status_code == 200
    assert client.post(f"{API}/apps/{app_id}/associate-user/{user.id}", headers=headers).status_code == 200

    user_copy = os.path.join(
        settings.DATA_ROOT, "agents", str(app_id), "users", str(user.id), "skills", "style-guide", "SKILL.md"
    )
    with open(user_copy, encoding="utf-8") as handle:
        assert "version-1" in handle.read()

    # Edit the skill, then republish so the Agent layer snapshot is refreshed.
    updated = client.patch(f"{API}/skills/style-guide", json={"body": "# style-guide\n\nversion-2\n"}, headers=headers)
    assert updated.status_code == 200, updated.text
    assert client.post(f"{API}/apps/{app_id}/publish", headers=headers).status_code == 200

    # Clear caches to model a restart; the next get_runtime lazily resyncs the
    # user layer because the expected fingerprint no longer matches.
    assembly.clear_compile_cache()
    runtime_module.clear_runtime_cache()

    with DBSession(db_engine) as db_session:
        asyncio.run(runtime_module.get_runtime(db_session, app_id, user_id=user.id))
    with open(user_copy, encoding="utf-8") as handle:
        assert "version-2" in handle.read()
