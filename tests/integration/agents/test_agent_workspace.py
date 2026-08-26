"""G2 three-layer workspace end-to-end chains (spec v3.3 §8.2.1).

Zero real LLM / zero real MCP by construction (conftest seams): every
scenario drives the real API + service + filesystem chain and asserts the
nested workspace layout of §2.1:

- Global: ``{DATA_ROOT}/global/skills/<name>/SKILL.md``
- Agent:  ``{DATA_ROOT}/agents/<app_id>/skills/<name>/SKILL.md``
- User:   ``{DATA_ROOT}/agents/<app_id>/users/<user_id>/skills/<name>/SKILL.md``
"""

import asyncio
from pathlib import Path
from typing import Any

import pytest
from fastapi.testclient import TestClient
from sqlmodel import Session as DBSession

from app.core.config import settings
from app.models.agent_assets import AgentApp
from app.models.user import User
from app.services.agents import assembly
from app.services.agents import runtime as runtime_module
from tests.conftest import unwrap

pytestmark = pytest.mark.integration

API = settings.API_V1_STR


def _create_skill_and_app(
    client: TestClient, headers: dict[str, str], *, skill_body: str = "# style-guide\n\nversion-1\n"
) -> tuple[str, int]:
    """Create one Global skill plus a draft app bound to it; return (name, app_id)."""
    skill = client.post(
        f"{API}/skills",
        json={"name": "style-guide", "description": "Style", "body": skill_body},
        headers=headers,
    )
    assert skill.status_code == 201, skill.text
    app = client.post(
        f"{API}/apps",
        json={"name": "ws-app", "system_prompt": "You are ws.", "skill_names": ["style-guide"]},
        headers=headers,
    )
    assert app.status_code == 201, app.text
    return "style-guide", unwrap(app, expected_code=201)["id"]


def _seed_second_user(db_engine: Any) -> User:
    """Register a second user directly in the in-memory database."""
    second = User(
        email="bob@example.com",
        hashed_password=User.hash_password("Passw0rd!Strong"),
        username="bob",
    )
    with DBSession(db_engine) as session:
        session.add(second)
        session.commit()
        session.refresh(second)
    return second


def test_full_publish_flow_creates_three_layers(
    client: TestClient,
    user: User,
    user_headers: dict[str, str],
    scripted_model: Any,
    memory_checkpointer: Any,
) -> None:
    """Publish snapshots Global -> Agent; associate materializes the User layer."""
    headers = user_headers
    skill_name, app_id = _create_skill_and_app(client, headers)

    published = client.post(f"{API}/apps/{app_id}/publish", headers=headers)
    assert published.status_code == 200, published.text
    payload = unwrap(published)
    assert payload["status"] == "published"
    assert payload["workspace_hash"]
    assert payload["agent_workspace_status"] == "active"

    assert client.post(f"{API}/apps/{app_id}/associate-user/{user.id}", headers=headers).status_code == 200

    data_root = Path(settings.DATA_ROOT)
    global_skill = data_root / "global" / "skills" / skill_name / "SKILL.md"
    agent_skill = data_root / "agents" / str(app_id) / "skills" / skill_name / "SKILL.md"
    user_skill = data_root / "agents" / str(app_id) / "users" / str(user.id) / "skills" / skill_name / "SKILL.md"
    assert global_skill.is_file()
    assert agent_skill.is_file()
    assert user_skill.is_file()
    assert global_skill.read_text(encoding="utf-8") == agent_skill.read_text(encoding="utf-8")
    assert agent_skill.read_text(encoding="utf-8") == user_skill.read_text(encoding="utf-8")


def test_patch_published_app_reverts_to_draft(
    client: TestClient,
    user_headers: dict[str, str],
    scripted_model: Any,
    memory_checkpointer: Any,
    db_engine: Any,
) -> None:
    """PATCH on a published app demotes it to draft and clears the workspace hash (解读 B)."""
    headers = user_headers
    _create_skill_and_app(client, headers)
    app_id = [row for row in unwrap(client.get(f"{API}/apps", headers=headers)) if row["name"] == "ws-app"][0]["id"]

    assert client.post(f"{API}/apps/{app_id}/publish", headers=headers).status_code == 200

    patched = client.patch(f"{API}/apps/{app_id}", json={"skill_names": []}, headers=headers)
    assert patched.status_code == 200, patched.text

    with DBSession(db_engine) as session:
        row = session.get(AgentApp, app_id)
        assert row is not None
        assert row.status == "draft"  # a patched config cannot keep serving live sessions
        assert row.workspace_hash is None
        assert row.agent_workspace_status == "pending"


def test_lazy_validation_resyncs_user_layer(
    client: TestClient,
    user: User,
    user_headers: dict[str, str],
    scripted_model: Any,
    memory_checkpointer: Any,
    db_engine: Any,
) -> None:
    """A stale User layer is resynced lazily by the next ``get_runtime`` call."""
    headers = user_headers
    _create_skill_and_app(client, headers)
    app_id = [row for row in unwrap(client.get(f"{API}/apps", headers=headers)) if row["name"] == "ws-app"][0]["id"]
    assert client.post(f"{API}/apps/{app_id}/publish", headers=headers).status_code == 200
    assert client.post(f"{API}/apps/{app_id}/associate-user/{user.id}", headers=headers).status_code == 200

    user_copy = (
        Path(settings.DATA_ROOT)
        / "agents" / str(app_id) / "users" / str(user.id) / "skills" / "style-guide" / "SKILL.md"
    )
    assert "version-1" in user_copy.read_text(encoding="utf-8")

    # Skill edit + republish refresh the Agent snapshot; the User layer stays stale.
    assert client.patch(f"{API}/skills/style-guide", json={"body": "# style-guide\n\nversion-2\n"}, headers=headers).status_code == 200
    assert client.post(f"{API}/apps/{app_id}/publish", headers=headers).status_code == 200

    assembly.clear_compile_cache()
    runtime_module.clear_runtime_cache()
    with DBSession(db_engine) as db_session:
        asyncio.run(runtime_module.get_runtime(db_session, app_id, user_id=user.id))

    assert "version-2" in user_copy.read_text(encoding="utf-8")


def test_cross_user_isolation(
    client: TestClient,
    user: User,
    user_headers: dict[str, str],
    scripted_model: Any,
    memory_checkpointer: Any,
    db_engine: Any,
) -> None:
    """Per-(app_id, user_id) User layers: mutating one copy never leaks into another."""
    headers = user_headers
    second_user = _seed_second_user(db_engine)
    _create_skill_and_app(client, headers)
    app_id = [row for row in unwrap(client.get(f"{API}/apps", headers=headers)) if row["name"] == "ws-app"][0]["id"]
    assert client.post(f"{API}/apps/{app_id}/publish", headers=headers).status_code == 200
    assert client.post(f"{API}/apps/{app_id}/associate-user/{user.id}", headers=headers).status_code == 200
    assert client.post(f"{API}/apps/{app_id}/associate-user/{second_user.id}", headers=headers).status_code == 200

    base = Path(settings.DATA_ROOT) / "agents" / str(app_id) / "users"
    alice_copy = base / str(user.id) / "skills" / "style-guide" / "SKILL.md"
    bob_copy = base / str(second_user.id) / "skills" / "style-guide" / "SKILL.md"
    assert alice_copy.is_file() and bob_copy.is_file()

    alice_copy.write_text("# style-guide\n\ntampered-by-alice\n", encoding="utf-8")
    assert "version-1" in bob_copy.read_text(encoding="utf-8")  # bob is untouched

    # Lazy validation repairs alice's drifted copy without disturbing bob's.
    assembly.clear_compile_cache()
    runtime_module.clear_runtime_cache()
    with DBSession(db_engine) as db_session:
        asyncio.run(runtime_module.get_runtime(db_session, app_id, user_id=user.id))
    assert "tampered-by-alice" not in alice_copy.read_text(encoding="utf-8")
    assert "version-1" in bob_copy.read_text(encoding="utf-8")
