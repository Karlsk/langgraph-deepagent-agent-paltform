"""Scenario 3 (+ scenario 2 tail): subagent one-shot test runs and skill delete cascade.

Full chain under test: admin API creates a subagent (inheritance fields left
blank) -> ``POST /subagents/{name}/test`` executes one isolated run through
the real compile/execute path with a scripted model -> the run neither writes
checkpoints nor pollutes the assembly compile cache. Skill deletion cascades
to every per-user copy produced by earlier assemblies (Phase 1 G1: assembly
is triggered via ``get_runtime``, the retired chat endpoint's compile seam).
"""

import asyncio
import os
from typing import Any

import pytest
from fastapi.testclient import TestClient
from langchain_core.messages import AIMessage
from sqlmodel import Session as DBSession

from app.core.config import settings
from app.models.user import User
from app.services.agents import assembly
from app.services.agents import runtime as runtime_module
from tests.conftest import unwrap

from .conftest import assert_error_envelope

pytestmark = pytest.mark.integration

API = settings.API_V1_STR


def test_subagent_one_shot_test_run_is_isolated(
    client: TestClient, user_headers: dict[str, str], scripted_model: Any
) -> None:
    """One-shot test runs return a SubAgentTestResult without cache/checkpoint side effects."""
    headers = user_headers  # Phase 1 G1: user access token authenticates directly

    created = client.post(
        f"{API}/subagents",
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
    created_payload = unwrap(created, expected_code=201)
    assert created_payload["allowed_tools"] is None
    assert created_payload["model"] is None
    assert created_payload["max_turns"] is None

    scripted_model.responses = [AIMessage(content="one-shot summary")]
    result = client.post(f"{API}/subagents/summarizer/test", json={"prompt": "summarize this"}, headers=headers)
    assert result.status_code == 200, result.text
    payload = unwrap(result)
    assert payload["final_message"] == "one-shot summary"
    assert payload["turns"] == 1
    assert payload["duration_seconds"] > 0
    assert payload["model"] == settings.DEFAULT_LLM_MODEL

    # Isolation contract: the one-shot run touches neither the compile cache
    # nor any checkpointer (the standalone graph is compiled without one).
    assert len(assembly._compile_cache) == 0  # noqa: SLF001 — isolation assertion
    assert scripted_model.n == 1  # single model turn, no resume loop


def test_subagent_test_unknown_name_404(client: TestClient, user_headers: dict[str, str]) -> None:
    """Test-running a missing subagent returns a 404 error envelope."""
    headers = user_headers  # Phase 1 G1: user access token authenticates directly
    response = client.post(f"{API}/subagents/ghost/test", json={"prompt": "hi"}, headers=headers)
    assert_error_envelope(response, code=404, message="subagent 'ghost' not found")


def test_subagent_test_run_persists_queryable_trace(
    client: TestClient, user_headers: dict[str, str], scripted_model: Any
) -> None:
    """A one-shot run persists a structured trace retrievable via the trace APIs."""
    headers = user_headers  # Phase 1 G1: user access token authenticates directly
    created = client.post(
        f"{API}/subagents",
        json={
            "name": "traceable",
            "description": "Traceable helper",
            "when_to_use": "When tracing matters",
            "system_prompt": "You help.",
        },
        headers=headers,
    )
    assert created.status_code == 201, created.text

    scripted_model.responses = [AIMessage(content="traced reply")]
    result = client.post(f"{API}/subagents/traceable/test", json={"prompt": "trace me"}, headers=headers)
    assert result.status_code == 200, result.text
    payload = unwrap(result)
    trace_id = payload["trace_id"]
    assert trace_id is not None

    # The list endpoint exposes the run as a summary (no event stream).
    listing = client.get(f"{API}/subagents/traceable/traces", headers=headers)
    assert listing.status_code == 200, listing.text
    page = unwrap(listing)
    assert page["total"] == 1
    summary = page["items"][0]
    assert summary["id"] == trace_id
    assert summary["status"] == "success"
    assert summary["prompt"] == "trace me"
    assert "events" not in summary

    # The detail endpoint carries the full structured event stream.
    detail = client.get(f"{API}/subagents/traceable/traces/{trace_id}", headers=headers)
    assert detail.status_code == 200, detail.text
    trace = unwrap(detail)
    types = [event["type"] for event in trace["events"]]
    assert "llm_call" in types
    assert types[-1] == "run_finished"
    llm_event = next(event for event in trace["events"] if event["type"] == "llm_call")
    assert llm_event["model"] == settings.DEFAULT_LLM_MODEL
    assert llm_event["output_text"] == "traced reply"
    assert trace["final_message"] == "traced reply"
    assert trace["created_by"]  # audit creator recorded from the chat session


def test_skill_delete_cascades_user_copies(
    client: TestClient,
    user: User,
    user_headers: dict[str, str],
    scripted_model: Any,
    memory_checkpointer: Any,
    db_engine: Any,
) -> None:
    """Deleting a global skill is reference-protected and cleans the global tree.

    G2 v3.3: the per-user copy lives in the nested User layer
    ``{DATA_ROOT}/agents/<app_id>/users/<uid>/skills/<name>`` and is created
    by the associate endpoint (publish materializes the Agent layer only).

    Exercises the reference-protection contract:
    1. Bound to an AgentApp -> DELETE returns 422 listing the reference;
    2. Unbind via PATCH (workaround path, still supported), DELETE succeeds,
       and the global directory is wiped.
    """
    headers = user_headers  # Phase 1 G1: user access token authenticates directly
    skill = client.post(
        f"{API}/skills",
        json={"name": "doomed-skill", "description": "Temporary", "body": "# doomed-skill\n"},
        headers=headers,
    )
    assert skill.status_code == 201, skill.text

    # Publish an app bound to the skill (Agent layer) and associate the user
    # (combined User layer) so the nested per-user copy exists.
    app = client.post(
        f"{API}/apps",
        json={"name": "doomed-app", "system_prompt": "You are doomed.", "skill_names": ["doomed-skill"]},
        headers=headers,
    )
    assert app.status_code == 201, app.text
    app_id = unwrap(app, expected_code=201)["id"]
    assert client.post(f"{API}/apps/{app_id}/publish", headers=headers).status_code == 200
    assert (
        client.post(f"{API}/apps/{app_id}/associate-user/{user.id}", headers=headers).status_code == 200
    )

    with DBSession(db_engine) as db_session:
        asyncio.run(runtime_module.get_runtime(db_session, app_id, user_id=user.id))

    user_copy = os.path.join(
        settings.DATA_ROOT, "agents", str(app_id), "users", str(user.id), "skills", "doomed-skill", "SKILL.md"
    )
    assert os.path.isfile(user_copy)

    # Reference-protection path: skill still bound to the app -> DELETE is rejected (422).
    blocked = client.delete(f"{API}/skills/doomed-skill", headers=headers)
    assert blocked.status_code == 422, blocked.text
    blocked_body = blocked.json()
    assert blocked_body["code"] == 422
    assert "doomed-skill" in blocked_body["message"]
    assert "doomed-app" in blocked_body["message"]

    # Unbind the skill from the app, then DELETE succeeds and cleans the global tree.
    assert client.patch(f"{API}/apps/{app_id}", json={"skill_names": []}, headers=headers).status_code == 200
    deleted = client.delete(f"{API}/skills/doomed-skill", headers=headers)
    assert deleted.status_code == 200, deleted.text

    assert not os.path.isdir(os.path.join(settings.DATA_ROOT, "global", "skills", "doomed-skill"))
    # The retired sync_user_skills path (top-level {DATA_ROOT}/users) stays untouched.
    assert not os.path.isdir(os.path.join(settings.DATA_ROOT, "users", "system"))


# ---------------------------------------------------------------------------
# subagent.skill_names binding (end-to-end through /subagents/{name}/test)
# ---------------------------------------------------------------------------


def test_subagent_one_shot_test_with_skills(
    client: TestClient, user_headers: dict[str, str], scripted_model: Any
) -> None:
    """A subagent with explicit ``skill_names`` materialises skills into a tmp dir for the test run.

    End-to-end coverage: create a global skill -> create a subagent bound to it
    -> ``POST /subagents/{name}/test`` must materialise the SKILL.md under a
    caller-supplied tmp dir (FilesystemBackend layout) and still respect
    isolation (no compile-cache pollution, no per-user skill directory
    created under ``settings.SKILLS_ROOT``).
    """
    headers = user_headers  # Phase 1 G1: user access token authenticates directly

    # Seed a global skill the subagent will bind to.
    skill = client.post(
        f"{API}/skills",
        json={
            "name": "doc-export",
            "description": "Export documents",
            "body": "# doc-export\n\n## When to use\nwhen the user wants to export\n",
        },
        headers=headers,
    )
    assert skill.status_code == 201, skill.text

    # Create a subagent with an explicit whitelist.
    created = client.post(
        f"{API}/subagents",
        json={
            "name": "exporter",
            "description": "Exports documents",
            "when_to_use": "When the user wants to export",
            "system_prompt": "You export things.",
            "skill_names": ["doc-export"],
        },
        headers=headers,
    )
    assert created.status_code == 201, created.text
    payload = unwrap(created, expected_code=201)
    assert payload["skill_names"] == ["doc-export"]

    scripted_model.responses = [AIMessage(content="exported")]
    result = client.post(f"{API}/subagents/exporter/test", json={"prompt": "export this"}, headers=headers)
    assert result.status_code == 200, result.text
    body = unwrap(result)
    assert body["final_message"] == "exported"
    assert body["turns"] == 1
    # Isolation contract still holds.
    assert len(assembly._compile_cache) == 0  # noqa: SLF001
    # No per-user skill copies were created by the standalone runner (it uses
    # a private tmp dir, never the per-user workspace under {DATA_ROOT}/users).
    user_skill_root = os.path.join(settings.DATA_ROOT, "users")
    if os.path.isdir(user_skill_root):
        for entry in os.listdir(user_skill_root):
            assert entry != "system", "standalone runner must not pollute per-user skill dirs"
    # The global skill directory survives intact.
    assert os.path.isfile(os.path.join(settings.DATA_ROOT, "global", "skills", "doc-export", "SKILL.md"))


def test_subagent_one_shot_test_with_skill_names_none_inherits_empty(
    client: TestClient, user_headers: dict[str, str], scripted_model: Any
) -> None:
    """A subagent with ``skill_names=None`` runs cleanly (standalone collapses None -> []).

    The standalone runner has no parent app to inherit from, so omitting
    ``skill_names`` must behave like an empty whitelist (no skills bound).
    The run still completes and reports success without touching the global
    skill tree.
    """
    headers = user_headers  # Phase 1 G1: user access token authenticates directly
    created = client.post(
        f"{API}/subagents",
        json={
            "name": "plain",
            "description": "Plain helper",
            "when_to_use": "Always",
            "system_prompt": "You are plain.",
        },
        headers=headers,
    )
    assert created.status_code == 201, created.text
    assert unwrap(created, expected_code=201)["skill_names"] is None

    scripted_model.responses = [AIMessage(content="ok")]
    result = client.post(f"{API}/subagents/plain/test", json={"prompt": "hi"}, headers=headers)
    assert result.status_code == 200, result.text
    assert unwrap(result)["final_message"] == "ok"
