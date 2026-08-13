"""Scenario 3 (+ scenario 2 tail): subagent one-shot test runs and skill delete cascade.

Full chain under test: admin API creates a subagent (inheritance fields left
blank) -> ``POST /subagents/{name}/test`` executes one isolated run through
the real compile/execute path with a scripted model -> the run neither writes
checkpoints nor pollutes the assembly compile cache. Skill deletion cascades
to every per-user copy produced by earlier assemblies.
"""

import os
from typing import Any

import pytest
from fastapi.testclient import TestClient
from langchain_core.messages import AIMessage

from app.core.config import settings
from app.services.agents import assembly

pytestmark = pytest.mark.integration

API = settings.API_V1_STR


def _auth(client: TestClient, user_headers: dict[str, str]) -> dict[str, str]:
    """Exchange a user token for a chat-session token (management APIs need it)."""
    response = client.post(f"{API}/auth/session", json={}, headers=user_headers)
    assert response.status_code == 200, response.text
    token = response.json()["token"]["access_token"]
    return {"Authorization": f"Bearer {token}"}


def test_subagent_one_shot_test_run_is_isolated(
    client: TestClient, user_headers: dict[str, str], scripted_model: Any
) -> None:
    """One-shot test runs return a SubAgentTestResult without cache/checkpoint side effects."""
    headers = _auth(client, user_headers)

    created = client.post(
        f"{API}/agent-apps/subagents",
        json={
            "name": "summarizer",
            "description": "Summarizes text",
            "when_to_use": "When summarization is needed",
            "system_prompt": "You summarize.",
        },
        headers=headers,
    )
    assert created.status_code == 201, created.text
    # Inheritance fields left blank default to None (inherit at assembly time).
    assert created.json()["allowed_tools"] is None
    assert created.json()["model"] is None
    assert created.json()["max_turns"] is None

    scripted_model.responses = [AIMessage(content="one-shot summary")]
    result = client.post(f"{API}/agent-apps/subagents/summarizer/test", json={"prompt": "summarize this"}, headers=headers)
    assert result.status_code == 200, result.text
    payload = result.json()
    assert payload["final_message"] == "one-shot summary"
    assert payload["turns"] == 1
    assert payload["duration_seconds"] > 0
    assert payload["model"] == settings.DEFAULT_LLM_MODEL

    # Isolation contract: the one-shot run touches neither the compile cache
    # nor any checkpointer (the standalone graph is compiled without one).
    assert len(assembly._compile_cache) == 0  # noqa: SLF001 — isolation assertion
    assert scripted_model.n == 1  # single model turn, no resume loop


def test_subagent_test_unknown_name_404(client: TestClient, user_headers: dict[str, str]) -> None:
    """Test-running a missing subagent returns 404."""
    headers = _auth(client, user_headers)
    response = client.post(f"{API}/agent-apps/subagents/ghost/test", json={"prompt": "hi"}, headers=headers)
    assert response.status_code == 404


def test_skill_delete_cascades_user_copies(
    client: TestClient, user_headers: dict[str, str], scripted_model: Any, memory_checkpointer: Any
) -> None:
    """Deleting a global skill removes the per-user copies created by assembly."""
    headers = _auth(client, user_headers)
    skill = client.post(
        f"{API}/agent-apps/skills",
        json={"name": "doomed-skill", "description": "Temporary", "body": "# doomed-skill\n"},
        headers=headers,
    )
    assert skill.status_code == 201, skill.text

    # Publish an app bound to the skill and chat once so assembly copies it.
    app = client.post(
        f"{API}/agent-apps/apps",
        json={"name": "doomed-app", "system_prompt": "You are doomed.", "skill_names": ["doomed-skill"]},
        headers=headers,
    )
    assert app.status_code == 201, app.text
    app_id = app.json()["id"]
    assert client.post(f"{API}/agent-apps/apps/{app_id}/publish", headers=headers).status_code == 200

    session = client.post(f"{API}/auth/session", json={"agent_app_id": app_id}, headers=user_headers)
    assert session.status_code == 200, session.text
    session_token = {"Authorization": f"Bearer {session.json()['token']['access_token']}"}
    scripted_model.responses = [AIMessage(content="doomed reply")]
    chat = client.post(f"{API}/chatbot/chat", json={"messages": [{"role": "user", "content": "hi"}]}, headers=session_token)
    assert chat.status_code == 200, chat.text

    user_copy_dir = os.path.join(settings.SKILLS_ROOT, "users", "system", "doomed-skill")
    assert os.path.isfile(os.path.join(user_copy_dir, "SKILL.md"))

    # Unbind the skill from the app (delete rejects dangling references), then delete.
    assert client.patch(f"{API}/agent-apps/apps/{app_id}", json={"skill_names": []}, headers=headers).status_code == 200
    deleted = client.delete(f"{API}/agent-apps/skills/doomed-skill", headers=headers)
    assert deleted.status_code == 200, deleted.text

    assert not os.path.exists(user_copy_dir)
    assert not os.path.isdir(os.path.join(settings.SKILLS_ROOT, "global", "doomed-skill"))
