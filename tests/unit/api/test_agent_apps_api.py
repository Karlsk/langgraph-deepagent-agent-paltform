"""Unit tests for the agent asset management API (subagents/skills/apps/MCP/LLM configs).

Zero real network / zero real LLM / zero real MCP: the DB layer runs on an
in-memory SQLite session injected via dependency override, the auth
dependency is overridden with a fake chat Session row, and every service
touching the outside world (test runner, skill draft LLM, MCP probes,
catalog builder, client shutdown) is monkeypatched.
"""

from collections.abc import Generator
from pathlib import Path
from typing import Any
from unittest.mock import AsyncMock

import pytest
from fastapi import FastAPI
from fastapi.testclient import TestClient
from slowapi import _rate_limit_exceeded_handler
from slowapi.errors import RateLimitExceeded
from sqlalchemy.exc import IntegrityError
from sqlalchemy.pool import StaticPool
from sqlmodel import SQLModel, create_engine
from sqlmodel import Session as DBSession

from app.api.v1 import agent_apps as agent_apps_module
from app.api.v1 import auth as auth_module
from app.core.config import settings
from app.core.limiter import limiter
from app.models.agent_assets import DEFAULT_LLM_CONFIG_NAME, LlmConfig, McpServerConfig
from app.models.session import Session as ChatSession
from app.schemas.agent_apps import SubAgentTestResult
from app.services.agents import skills_store

pytestmark = pytest.mark.unit

# Real probe implementation captured before autouse fixtures stub it out.
_REAL_PROBE_SERVER_TOOL_NAMES = agent_apps_module._probe_server_tool_names

BUILTIN_CATALOG: list[dict[str, Any]] = [
    {"name": "duckduckgo_results_json", "source": "builtin"},
    {"name": "ask_human", "source": "builtin"},
]


# ---------------------------------------------------------------------------
# Fixtures
# ---------------------------------------------------------------------------


@pytest.fixture(autouse=True)
def reset_rate_limiter() -> Generator[None, None, None]:
    """Isolate slowapi counters between tests (shared in-memory storage)."""
    limiter.reset()
    yield


@pytest.fixture(autouse=True)
def isolated_skills_root(tmp_path: Any, monkeypatch: pytest.MonkeyPatch) -> str:
    """Point the real skills_store at a per-test directory (no shared state)."""
    root = str(tmp_path / "skills")
    monkeypatch.setattr(settings, "SKILLS_ROOT", root)
    return root


@pytest.fixture
def db_session() -> Generator[DBSession, None, None]:
    """Provide an isolated in-memory SQLite session with all tables created."""
    engine = create_engine(
        "sqlite://",
        connect_args={"check_same_thread": False},
        poolclass=StaticPool,
    )
    SQLModel.metadata.create_all(engine)
    session = DBSession(engine)
    yield session
    session.close()


@pytest.fixture
def fake_chat_session() -> ChatSession:
    """A detached chat Session row standing in for get_current_session."""
    return ChatSession(id="sess-1", user_id=7, name="", username="ann", agent_app_id=None)


@pytest.fixture(autouse=True)
def catalog(monkeypatch: pytest.MonkeyPatch) -> list[dict[str, Any]]:
    """Stub build_tool_catalog on the API module with a mutable builtin list."""
    entries: list[dict[str, Any]] = [dict(entry) for entry in BUILTIN_CATALOG]

    async def fake_build_tool_catalog(session: Any) -> list[dict[str, Any]]:
        return entries

    monkeypatch.setattr(agent_apps_module, "build_tool_catalog", fake_build_tool_catalog)
    return entries


@pytest.fixture(autouse=True)
def quiet_shutdown(monkeypatch: pytest.MonkeyPatch) -> AsyncMock:
    """Record MCP client cache invalidation calls without touching real clients."""
    shutdown = AsyncMock()
    monkeypatch.setattr(agent_apps_module, "shutdown_mcp_clients", shutdown)
    return shutdown


@pytest.fixture(autouse=True)
def probe_tools(monkeypatch: pytest.MonkeyPatch) -> AsyncMock:
    """Stub the candidate MCP server tool probe (zero real connections)."""
    probe = AsyncMock(return_value=[])
    monkeypatch.setattr(agent_apps_module, "_probe_server_tool_names", probe)
    return probe


@pytest.fixture
def client(db_session: DBSession, fake_chat_session: ChatSession) -> Generator[TestClient, None, None]:
    """Minimal app wiring the agent-apps router with limiter + dependency overrides."""
    app = FastAPI()
    app.state.limiter = limiter
    app.add_exception_handler(RateLimitExceeded, _rate_limit_exceeded_handler)  # pyright: ignore[reportArgumentType]
    app.include_router(agent_apps_module.router, prefix="/agent-apps")
    app.dependency_overrides[auth_module.get_current_session] = lambda: fake_chat_session
    app.dependency_overrides[agent_apps_module.get_db_session] = lambda: db_session
    with TestClient(app) as test_client:
        yield test_client
    app.dependency_overrides.clear()


# ---------------------------------------------------------------------------
# Body factories
# ---------------------------------------------------------------------------


def _subagent_body(**overrides: Any) -> dict[str, Any]:
    body: dict[str, Any] = {
        "name": "researcher",
        "description": "Research helper",
        "when_to_use": "When web research is needed",
        "system_prompt": "You are a researcher.",
    }
    body.update(overrides)
    return body


def _skill_body(**overrides: Any) -> dict[str, Any]:
    body: dict[str, Any] = {
        "name": "pdf-export",
        "description": "Export documents to PDF",
        "body": "# pdf-export\n\n## Steps\n1. render\n",
    }
    body.update(overrides)
    return body


def _app_body(**overrides: Any) -> dict[str, Any]:
    body: dict[str, Any] = {"name": "support-app", "system_prompt": "You are support."}
    body.update(overrides)
    return body


def _mcp_body(**overrides: Any) -> dict[str, Any]:
    body: dict[str, Any] = {
        "name": "fs-server",
        "transport": "stdio",
        "command": "uvx",
        "args": ["mcp-fs"],
    }
    body.update(overrides)
    return body


def _llm_config_body(**overrides: Any) -> dict[str, Any]:
    body: dict[str, Any] = {
        "name": "proxy",
        "model_name": "MiniMax-M3",
        "api_key": "sk-secret-1234",
        "base_url": "https://proxy.example.com/v1",
    }
    body.update(overrides)
    return body


# ---------------------------------------------------------------------------
# SubAgent CRUD
# ---------------------------------------------------------------------------


def test_create_subagent_returns_201_with_audit_fields(client: TestClient) -> None:
    """POST /subagents persists the row with hash, version and created_by."""
    response = client.post("/agent-apps/subagents", json=_subagent_body())
    assert response.status_code == 201
    payload = response.json()
    assert payload["name"] == "researcher"
    assert payload["version"] == 1
    assert payload["created_by"] == "ann"
    assert payload["content_hash"]


def test_create_subagent_duplicate_name_rejected(client: TestClient) -> None:
    """A second create with the same name is rejected with 422."""
    assert client.post("/agent-apps/subagents", json=_subagent_body()).status_code == 201
    response = client.post("/agent-apps/subagents", json=_subagent_body())
    assert response.status_code == 422


def test_list_subagents_returns_created_rows(client: TestClient) -> None:
    """GET /subagents lists every stored sub-agent."""
    client.post("/agent-apps/subagents", json=_subagent_body())
    client.post("/agent-apps/subagents", json=_subagent_body(name="writer"))
    response = client.get("/agent-apps/subagents")
    assert response.status_code == 200
    assert [row["name"] for row in response.json()] == ["researcher", "writer"]


def test_get_subagent_returns_row_or_404(client: TestClient) -> None:
    """GET /subagents/{name} resolves existing rows and 404s unknown ones."""
    client.post("/agent-apps/subagents", json=_subagent_body())
    assert client.get("/agent-apps/subagents/researcher").status_code == 200
    assert client.get("/agent-apps/subagents/ghost").status_code == 404


def test_patch_subagent_updates_fields_and_bumps_version(client: TestClient) -> None:
    """PATCH applies partial fields, refreshes the hash and bumps version."""
    created = client.post("/agent-apps/subagents", json=_subagent_body()).json()
    response = client.patch("/agent-apps/subagents/researcher", json={"description": "New desc"})
    assert response.status_code == 200
    payload = response.json()
    assert payload["description"] == "New desc"
    assert payload["version"] == 2
    assert payload["content_hash"] != created["content_hash"]


def test_patch_subagent_rejects_name_change(client: TestClient) -> None:
    """PATCH containing the immutable name field is rejected with 422."""
    client.post("/agent-apps/subagents", json=_subagent_body())
    response = client.patch("/agent-apps/subagents/researcher", json={"name": "other"})
    assert response.status_code == 422


def test_patch_subagent_empty_payload_rejected(client: TestClient) -> None:
    """PATCH without any updatable field is rejected with 422."""
    client.post("/agent-apps/subagents", json=_subagent_body())
    response = client.patch("/agent-apps/subagents/researcher", json={})
    assert response.status_code == 422


def test_patch_subagent_unknown_name_404(client: TestClient) -> None:
    """PATCH on a missing sub-agent returns 404."""
    response = client.patch("/agent-apps/subagents/ghost", json={"description": "x"})
    assert response.status_code == 404


def test_delete_subagent_removes_row(client: TestClient) -> None:
    """DELETE removes the row; subsequent reads 404, unknown deletes 404."""
    client.post("/agent-apps/subagents", json=_subagent_body())
    assert client.delete("/agent-apps/subagents/researcher").status_code == 200
    assert client.get("/agent-apps/subagents/researcher").status_code == 404
    assert client.delete("/agent-apps/subagents/researcher").status_code == 404


# ---------------------------------------------------------------------------
# SubAgent test-run endpoint
# ---------------------------------------------------------------------------


def test_subagent_test_invokes_run_subagent_once(
    client: TestClient, db_session: DBSession, monkeypatch: pytest.MonkeyPatch
) -> None:
    """POST /subagents/{name}/test delegates to run_subagent_once."""
    client.post("/agent-apps/subagents", json=_subagent_body())
    run_once = AsyncMock(
        return_value=SubAgentTestResult(final_message="done", turns=2, duration_seconds=1.5, model="gpt-5-mini")
    )
    monkeypatch.setattr(agent_apps_module, "run_subagent_once", run_once)

    response = client.post("/agent-apps/subagents/researcher/test", json={"prompt": "hello"})
    assert response.status_code == 200
    assert response.json() == {"final_message": "done", "turns": 2, "duration_seconds": 1.5, "model": "gpt-5-mini"}
    run_once.assert_awaited_once()
    kwargs = run_once.await_args.kwargs
    assert kwargs["name"] == "researcher"
    assert kwargs["prompt"] == "hello"
    assert kwargs["session"] is db_session


def test_subagent_test_unknown_name_404(client: TestClient) -> None:
    """Test-running a missing sub-agent returns 404 without invoking the runner."""
    response = client.post("/agent-apps/subagents/ghost/test", json={"prompt": "hello"})
    assert response.status_code == 404


def test_subagent_test_runner_failure_500(client: TestClient, monkeypatch: pytest.MonkeyPatch) -> None:
    """Runner errors surface as 500."""
    client.post("/agent-apps/subagents", json=_subagent_body())
    monkeypatch.setattr(agent_apps_module, "run_subagent_once", AsyncMock(side_effect=RuntimeError("boom")))
    response = client.post("/agent-apps/subagents/researcher/test", json={"prompt": "hello"})
    assert response.status_code == 500


# ---------------------------------------------------------------------------
# Skill CRUD (real skills_store on a tmp SKILLS_ROOT)
# ---------------------------------------------------------------------------


def test_create_skill_returns_201_and_writes_file(client: TestClient, isolated_skills_root: str) -> None:
    """POST /skills persists metadata and writes the global SKILL.md."""
    response = client.post("/agent-apps/skills", json=_skill_body())
    assert response.status_code == 201
    payload = response.json()
    assert payload["name"] == "pdf-export"
    assert payload["version"] == 1
    assert payload["created_by"] == "ann"
    assert (Path(isolated_skills_root) / "global" / "pdf-export" / "SKILL.md").exists()


def test_create_skill_duplicate_name_rejected(client: TestClient) -> None:
    """A second create with the same name is rejected with 422."""
    assert client.post("/agent-apps/skills", json=_skill_body()).status_code == 201
    assert client.post("/agent-apps/skills", json=_skill_body()).status_code == 422


def test_list_skills_returns_metadata(client: TestClient) -> None:
    """GET /skills lists every stored skill."""
    client.post("/agent-apps/skills", json=_skill_body())
    client.post("/agent-apps/skills", json=_skill_body(name="csv-clean", description="Clean CSV"))
    response = client.get("/agent-apps/skills")
    assert response.status_code == 200
    assert {row["name"] for row in response.json()} == {"pdf-export", "csv-clean"}


def test_get_skill_returns_row_or_404(client: TestClient) -> None:
    """GET /skills/{name} resolves existing rows and 404s unknown ones."""
    client.post("/agent-apps/skills", json=_skill_body())
    assert client.get("/agent-apps/skills/pdf-export").status_code == 200
    assert client.get("/agent-apps/skills/ghost").status_code == 404


def test_patch_skill_updates_body_and_bumps_version(client: TestClient) -> None:
    """PATCH rewrites the skill content, refreshes the hash and bumps version."""
    created = client.post("/agent-apps/skills", json=_skill_body()).json()
    response = client.patch("/agent-apps/skills/pdf-export", json={"body": "# v2\n"})
    assert response.status_code == 200
    payload = response.json()
    assert payload["version"] == 2
    assert payload["content_hash"] != created["content_hash"]


def test_patch_skill_rejects_name_change(client: TestClient) -> None:
    """PATCH containing the immutable name field is rejected with 422."""
    client.post("/agent-apps/skills", json=_skill_body())
    response = client.patch("/agent-apps/skills/pdf-export", json={"name": "other"})
    assert response.status_code == 422


def test_patch_skill_empty_payload_rejected(client: TestClient) -> None:
    """PATCH without description/body is rejected with 422."""
    client.post("/agent-apps/skills", json=_skill_body())
    response = client.patch("/agent-apps/skills/pdf-export", json={})
    assert response.status_code == 422


def test_patch_skill_unknown_name_404(client: TestClient) -> None:
    """PATCH on a missing skill returns 404."""
    response = client.patch("/agent-apps/skills/ghost", json={"description": "x"})
    assert response.status_code == 404


def test_get_skill_content_returns_body(client: TestClient) -> None:
    """GET /skills/{name}/content returns the raw SKILL.md body."""
    client.post("/agent-apps/skills", json=_skill_body())
    response = client.get("/agent-apps/skills/pdf-export/content")
    assert response.status_code == 200
    payload = response.json()
    assert payload["name"] == "pdf-export"
    assert payload["content"] == "# pdf-export\n\n## Steps\n1. render\n"


def test_get_skill_content_unknown_name_404(client: TestClient) -> None:
    """Reading the content of a missing skill returns 404."""
    assert client.get("/agent-apps/skills/ghost/content").status_code == 404


def test_delete_skill_removes_row(client: TestClient) -> None:
    """DELETE removes the skill; subsequent reads 404."""
    client.post("/agent-apps/skills", json=_skill_body())
    assert client.delete("/agent-apps/skills/pdf-export").status_code == 200
    assert client.get("/agent-apps/skills/pdf-export").status_code == 404
    assert client.delete("/agent-apps/skills/pdf-export").status_code == 404


# ---------------------------------------------------------------------------
# Skill draft generation
# ---------------------------------------------------------------------------


def test_skill_generate_returns_draft(client: TestClient, monkeypatch: pytest.MonkeyPatch) -> None:
    """POST /skills/generate delegates to generate_skill_draft (draft only)."""
    generate = AsyncMock(return_value="# draft-skill\n")
    monkeypatch.setattr(skills_store, "generate_skill_draft", generate)

    response = client.post("/agent-apps/skills/generate", json={"description": "do things", "hint": "be brief"})
    assert response.status_code == 200
    assert response.json() == {"draft": "# draft-skill\n"}
    generate.assert_awaited_once_with(description="do things", hint="be brief")
    assert client.get("/agent-apps/skills").json() == []


def test_skill_generate_llm_failure_500(client: TestClient, monkeypatch: pytest.MonkeyPatch) -> None:
    """LLM draft failures surface as 500."""
    monkeypatch.setattr(skills_store, "generate_skill_draft", AsyncMock(side_effect=RuntimeError("llm down")))
    response = client.post("/agent-apps/skills/generate", json={"description": "do things"})
    assert response.status_code == 500


# ---------------------------------------------------------------------------
# AgentApp CRUD
# ---------------------------------------------------------------------------


def test_create_agent_app_returns_201_with_defaults(client: TestClient) -> None:
    """POST /apps creates a draft app on the deepagents engine."""
    response = client.post("/agent-apps/apps", json=_app_body())
    assert response.status_code == 201
    payload = response.json()
    assert payload["name"] == "support-app"
    assert payload["status"] == "draft"
    assert payload["engine"] == "deepagents"
    assert payload["version"] == 1
    assert payload["created_by"] == "ann"
    assert isinstance(payload["id"], int)


def test_create_agent_app_duplicate_name_rejected(client: TestClient) -> None:
    """A second create with the same name is rejected with 422."""
    assert client.post("/agent-apps/apps", json=_app_body()).status_code == 201
    assert client.post("/agent-apps/apps", json=_app_body()).status_code == 422


def test_list_agent_apps(client: TestClient) -> None:
    """GET /apps lists every stored app."""
    client.post("/agent-apps/apps", json=_app_body())
    client.post("/agent-apps/apps", json=_app_body(name="sales-app"))
    response = client.get("/agent-apps/apps")
    assert response.status_code == 200
    assert {row["name"] for row in response.json()} == {"support-app", "sales-app"}


def test_get_agent_app_returns_row_or_404(client: TestClient) -> None:
    """GET /apps/{id} resolves existing rows and 404s unknown ids."""
    app_id = client.post("/agent-apps/apps", json=_app_body()).json()["id"]
    assert client.get(f"/agent-apps/apps/{app_id}").status_code == 200
    assert client.get("/agent-apps/apps/9999").status_code == 404


def test_list_published_apps_route_precedes_id_route(client: TestClient) -> None:
    """GET /apps/published is a distinct route (not swallowed by /apps/{id})."""
    response = client.get("/agent-apps/apps/published")
    assert response.status_code == 200
    assert response.json() == []


def test_patch_agent_app_replaces_collections_and_bumps_version(client: TestClient) -> None:
    """PATCH replaces list fields wholesale and bumps the version."""
    client.post("/agent-apps/skills", json=_skill_body())
    app_id = client.post("/agent-apps/apps", json=_app_body()).json()["id"]
    response = client.patch(f"/agent-apps/apps/{app_id}", json={"skill_names": ["pdf-export"]})
    assert response.status_code == 200
    payload = response.json()
    assert payload["skill_names"] == ["pdf-export"]
    assert payload["version"] == 2


def test_patch_agent_app_rejects_name_change(client: TestClient) -> None:
    """PATCH containing the immutable name field is rejected with 422."""
    app_id = client.post("/agent-apps/apps", json=_app_body()).json()["id"]
    response = client.patch(f"/agent-apps/apps/{app_id}", json={"name": "other"})
    assert response.status_code == 422


def test_patch_agent_app_unknown_id_404(client: TestClient) -> None:
    """PATCH on a missing app returns 404."""
    response = client.patch("/agent-apps/apps/9999", json={"system_prompt": "x"})
    assert response.status_code == 404


@pytest.mark.parametrize("field", ["skill_names", "subagent_names", "interrupt_on"])
def test_patch_agent_app_explicit_null_collection_rejected(client: TestClient, field: str) -> None:
    """Explicit JSON null on non-null JSON columns is rejected with 422 (not 500)."""
    app_id = client.post("/agent-apps/apps", json=_app_body()).json()["id"]
    response = client.patch(f"/agent-apps/apps/{app_id}", json={field: None})
    assert response.status_code == 422
    assert client.get(f"/agent-apps/apps/{app_id}").json()["version"] == 1


def test_patch_published_app_content_edit_reverts_status_to_draft(client: TestClient) -> None:
    """Editing content fields of a published app demotes it back to draft."""
    app_id = _seed_publishable_app(client)
    assert client.post(f"/agent-apps/apps/{app_id}/publish").json()["status"] == "published"

    response = client.patch(f"/agent-apps/apps/{app_id}", json={"system_prompt": "You are edited."})
    assert response.status_code == 200
    assert response.json()["status"] == "draft"
    assert client.get("/agent-apps/apps/published").json() == []


def test_delete_agent_app_removes_row(client: TestClient) -> None:
    """DELETE removes the app; subsequent reads 404."""
    app_id = client.post("/agent-apps/apps", json=_app_body()).json()["id"]
    assert client.delete(f"/agent-apps/apps/{app_id}").status_code == 200
    assert client.get(f"/agent-apps/apps/{app_id}").status_code == 404
    assert client.delete(f"/agent-apps/apps/{app_id}").status_code == 404


# ---------------------------------------------------------------------------
# AgentApp publish
# ---------------------------------------------------------------------------


def _seed_publishable_app(client: TestClient, **app_overrides: Any) -> int:
    """Create one skill + one subagent + default LLM config + one app; return app id."""
    client.post("/agent-apps/skills", json=_skill_body())
    client.post("/agent-apps/subagents", json=_subagent_body())
    client.post("/agent-apps/llm-configs", json=_llm_config_body(name=DEFAULT_LLM_CONFIG_NAME))
    body = _app_body(skill_names=["pdf-export"], subagent_names=["researcher"])
    body["allowed_tools"] = ["duckduckgo_results_json"]
    body.update(app_overrides)
    return int(client.post("/agent-apps/apps", json=body).json()["id"])


def test_publish_success_sets_status_hash_and_version(client: TestClient) -> None:
    """Publish validates references + whitelist, then stamps hash/status/version."""
    app_id = _seed_publishable_app(client)
    response = client.post(f"/agent-apps/apps/{app_id}/publish")
    assert response.status_code == 200
    payload = response.json()
    assert payload["status"] == "published"
    assert payload["version"] == 2
    assert payload["published_hash"]

    listed = client.get("/agent-apps/apps/published").json()
    assert [row["id"] for row in listed] == [app_id]


def test_publish_unknown_tool_whitelist_rejected(client: TestClient) -> None:
    """allowed_tools outside the catalog are rejected with 422."""
    app_id = _seed_publishable_app(client, allowed_tools=["ghost-tool"])
    response = client.post(f"/agent-apps/apps/{app_id}/publish")
    assert response.status_code == 422
    assert client.get(f"/agent-apps/apps/{app_id}").json()["status"] == "draft"


def test_publish_subagent_unknown_tool_rejected(client: TestClient) -> None:
    """A bound subagent with an unknown tool whitelist blocks publish (422)."""
    client.post("/agent-apps/subagents", json=_subagent_body(allowed_tools=["ghost-tool"]))
    app_id = client.post("/agent-apps/apps", json=_app_body(subagent_names=["researcher"], allowed_tools=None)).json()[
        "id"
    ]
    response = client.post(f"/agent-apps/apps/{app_id}/publish")
    assert response.status_code == 422


def test_publish_missing_skill_reference_rejected(client: TestClient) -> None:
    """Referencing a nonexistent skill is rejected with 422."""
    app_id = client.post("/agent-apps/apps", json=_app_body(skill_names=["ghost-skill"])).json()["id"]
    response = client.post(f"/agent-apps/apps/{app_id}/publish")
    assert response.status_code == 422


def test_publish_missing_subagent_reference_rejected(client: TestClient) -> None:
    """Referencing a nonexistent subagent is rejected with 422."""
    app_id = client.post("/agent-apps/apps", json=_app_body(subagent_names=["ghost-sub"])).json()["id"]
    response = client.post(f"/agent-apps/apps/{app_id}/publish")
    assert response.status_code == 422


def test_publish_unknown_app_404(client: TestClient) -> None:
    """Publishing a missing app returns 404."""
    response = client.post("/agent-apps/apps/9999/publish")
    assert response.status_code == 404


def test_publish_missing_llm_config_reference_rejected(client: TestClient) -> None:
    """An app referencing a nonexistent LLM config is rejected with 422."""
    app_id = client.post("/agent-apps/apps", json=_app_body(model="ghost-config")).json()["id"]
    response = client.post(f"/agent-apps/apps/{app_id}/publish")
    assert response.status_code == 422
    assert "ghost-config" in response.json()["detail"]


def test_publish_missing_default_llm_config_rejected(client: TestClient) -> None:
    """A NULL model reference needs the default config; its absence blocks publish."""
    app_id = client.post("/agent-apps/apps", json=_app_body()).json()["id"]
    response = client.post(f"/agent-apps/apps/{app_id}/publish")
    assert response.status_code == 422
    assert DEFAULT_LLM_CONFIG_NAME in response.json()["detail"]


def test_publish_disabled_llm_config_reference_rejected(client: TestClient) -> None:
    """A disabled referenced LLM config blocks publish with 422."""
    client.post("/agent-apps/llm-configs", json=_llm_config_body(name="frozen", enabled=False))
    app_id = client.post("/agent-apps/apps", json=_app_body(model="frozen")).json()["id"]
    response = client.post(f"/agent-apps/apps/{app_id}/publish")
    assert response.status_code == 422
    assert "disabled" in response.json()["detail"]


# ---------------------------------------------------------------------------
# LLM config CRUD
# ---------------------------------------------------------------------------


def test_create_llm_config_returns_201_masked(client: TestClient) -> None:
    """POST /llm-configs persists the row; api_key is never echoed back."""
    response = client.post("/agent-apps/llm-configs", json=_llm_config_body())
    assert response.status_code == 201
    payload = response.json()
    assert payload["name"] == "proxy"
    assert payload["model_name"] == "MiniMax-M3"
    assert payload["api_key_masked"] == "****1234"
    assert "api_key" not in payload
    assert payload["created_by"] == "ann"
    assert payload["content_hash"]


def test_create_llm_config_duplicate_name_rejected(client: TestClient) -> None:
    """A second create with the same name is rejected with 422."""
    assert client.post("/agent-apps/llm-configs", json=_llm_config_body()).status_code == 201
    assert client.post("/agent-apps/llm-configs", json=_llm_config_body()).status_code == 422


def test_create_llm_config_missing_required_fields_rejected(client: TestClient) -> None:
    """name/model_name/api_key are mandatory (422 when missing)."""
    assert client.post("/agent-apps/llm-configs", json={"name": "x", "model_name": "m"}).status_code == 422
    assert client.post("/agent-apps/llm-configs", json={"name": "x", "api_key": "k"}).status_code == 422


def test_list_llm_configs_masks_every_row(client: TestClient) -> None:
    """GET /llm-configs lists masked projections only."""
    client.post("/agent-apps/llm-configs", json=_llm_config_body())
    client.post("/agent-apps/llm-configs", json=_llm_config_body(name="backup", api_key="sk-abcd"))
    response = client.get("/agent-apps/llm-configs")
    assert response.status_code == 200
    rows = response.json()
    assert [row["name"] for row in rows] == ["backup", "proxy"]
    assert all("api_key" not in row for row in rows)
    # Short keys (<= 8 chars) never leak their tail.
    assert {row["api_key_masked"] for row in rows} == {"****1234", "****"}


def test_get_llm_config_returns_masked_row_or_404(client: TestClient) -> None:
    """GET /llm-configs/{name} resolves masked rows and 404s unknown ones."""
    client.post("/agent-apps/llm-configs", json=_llm_config_body())
    found = client.get("/agent-apps/llm-configs/proxy")
    assert found.status_code == 200
    assert "api_key" not in found.json()
    assert found.json()["api_key_masked"] == "****1234"
    assert client.get("/agent-apps/llm-configs/ghost").status_code == 404


def test_patch_llm_config_updates_fields_and_refreshes_hash(client: TestClient) -> None:
    """PATCH applies partial fields and recomputes the content hash."""
    created = client.post("/agent-apps/llm-configs", json=_llm_config_body()).json()
    response = client.patch("/agent-apps/llm-configs/proxy", json={"temperature": 0.9, "description": "tuned"})
    assert response.status_code == 200
    payload = response.json()
    assert payload["temperature"] == 0.9
    assert payload["description"] == "tuned"
    assert payload["content_hash"] != created["content_hash"]


def test_patch_llm_config_omitted_api_key_keeps_stored_key(
    client: TestClient, db_session: DBSession
) -> None:
    """Omitting api_key on PATCH leaves the stored key untouched."""
    client.post("/agent-apps/llm-configs", json=_llm_config_body())
    response = client.patch("/agent-apps/llm-configs/proxy", json={"description": "no rotation"})
    assert response.status_code == 200
    row = db_session.get(LlmConfig, "proxy")
    assert row is not None and row.api_key == "sk-secret-1234"

    rotated = client.patch("/agent-apps/llm-configs/proxy", json={"api_key": "sk-rotated-9999"})
    assert rotated.status_code == 200
    assert rotated.json()["api_key_masked"] == "****9999"
    row = db_session.get(LlmConfig, "proxy")
    assert row is not None and row.api_key == "sk-rotated-9999"


def test_patch_llm_config_rejects_name_change_and_empty_payload(client: TestClient) -> None:
    """PATCH with the immutable name field or an empty body is rejected (422)."""
    client.post("/agent-apps/llm-configs", json=_llm_config_body())
    assert client.patch("/agent-apps/llm-configs/proxy", json={"name": "other"}).status_code == 422
    assert client.patch("/agent-apps/llm-configs/proxy", json={}).status_code == 422


def test_patch_llm_config_unknown_name_404(client: TestClient) -> None:
    """PATCH on a missing LLM config returns 404."""
    assert client.patch("/agent-apps/llm-configs/ghost", json={"description": "x"}).status_code == 404


def test_delete_llm_config_removes_row(client: TestClient) -> None:
    """DELETE removes an unreferenced row; subsequent reads 404."""
    client.post("/agent-apps/llm-configs", json=_llm_config_body())
    assert client.delete("/agent-apps/llm-configs/proxy").status_code == 200
    assert client.get("/agent-apps/llm-configs/proxy").status_code == 404
    assert client.delete("/agent-apps/llm-configs/proxy").status_code == 404


def test_delete_default_llm_config_forbidden(client: TestClient) -> None:
    """The bootstrap-seeded default config can never be deleted (422)."""
    client.post("/agent-apps/llm-configs", json=_llm_config_body(name=DEFAULT_LLM_CONFIG_NAME))
    response = client.delete(f"/agent-apps/llm-configs/{DEFAULT_LLM_CONFIG_NAME}")
    assert response.status_code == 422
    assert client.get(f"/agent-apps/llm-configs/{DEFAULT_LLM_CONFIG_NAME}").status_code == 200


def test_delete_llm_config_referenced_by_app_rejected(client: TestClient) -> None:
    """A config referenced by an AgentApp.model field is delete-protected (422)."""
    client.post("/agent-apps/llm-configs", json=_llm_config_body())
    client.post("/agent-apps/apps", json=_app_body(model="proxy"))
    response = client.delete("/agent-apps/llm-configs/proxy")
    assert response.status_code == 422
    assert "support-app" in response.json()["detail"]


def test_delete_llm_config_referenced_by_subagent_rejected(client: TestClient) -> None:
    """A config referenced by a SubAgentConfig.model field is delete-protected (422)."""
    client.post("/agent-apps/llm-configs", json=_llm_config_body())
    client.post("/agent-apps/subagents", json=_subagent_body(model="proxy"))
    response = client.delete("/agent-apps/llm-configs/proxy")
    assert response.status_code == 422
    assert "researcher" in response.json()["detail"]


def test_edit_llm_config_does_not_demote_published_app(client: TestClient) -> None:
    """Editing an LLM config only drifts the fingerprint: published apps stay published."""
    app_id = _seed_publishable_app(client)
    assert client.post(f"/agent-apps/apps/{app_id}/publish").json()["status"] == "published"

    edited = client.patch(
        f"/agent-apps/llm-configs/{DEFAULT_LLM_CONFIG_NAME}", json={"api_key": "sk-rotated-0000"}
    )
    assert edited.status_code == 200

    app_payload = client.get(f"/agent-apps/apps/{app_id}").json()
    assert app_payload["status"] == "published"  # no demotion, only fingerprint drift
    assert client.get("/agent-apps/apps/published").json() != []


@pytest.mark.parametrize("api_key,expected", [("a", "****"), ("abcd", "****"), ("12345678", "****"), ("123456789", "****6789")])
def test_mask_api_key_short_key_boundaries(api_key: str, expected: str) -> None:
    """Keys of length <= 8 mask fully; length 9 keeps the last four chars."""
    assert agent_apps_module._mask_api_key(api_key) == expected  # noqa: SLF001 — unit under test


@pytest.mark.parametrize("field", ["model_name", "api_key", "enabled", "description"])
def test_patch_llm_config_explicit_null_on_required_field_rejected(client: TestClient, field: str) -> None:
    """Explicit JSON null on NOT NULL fields is rejected with 422 (omit instead)."""
    client.post("/agent-apps/llm-configs", json=_llm_config_body())
    response = client.patch("/agent-apps/llm-configs/proxy", json={field: None})
    assert response.status_code == 422
    assert "null is not allowed" in response.json()["detail"]


@pytest.mark.parametrize("field", ["base_url", "temperature", "max_tokens"])
def test_patch_llm_config_explicit_null_clears_optional_field(client: TestClient, field: str) -> None:
    """Explicit JSON null keeps its clear-to-None semantics for optional fields."""
    client.post("/agent-apps/llm-configs", json=_llm_config_body(temperature=0.7, max_tokens=512))
    response = client.patch("/agent-apps/llm-configs/proxy", json={field: None})
    assert response.status_code == 200
    assert response.json()[field] is None


def test_create_llm_config_commit_race_returns_422(
    client: TestClient, db_session: DBSession, monkeypatch: pytest.MonkeyPatch
) -> None:
    """A unique-name race lost at commit time degrades to 422 (never 500)."""

    def racing_commit() -> None:
        raise IntegrityError("insert", {}, Exception("duplicate key"))

    monkeypatch.setattr(db_session, "commit", racing_commit)
    response = client.post("/agent-apps/llm-configs", json=_llm_config_body())
    assert response.status_code == 422
    assert "already exists" in response.json()["detail"]


def test_create_llm_config_empty_base_url_normalized_to_none(client: TestClient) -> None:
    """POST with base_url='' stores None (SDK env fallback chain stays intact)."""
    response = client.post("/agent-apps/llm-configs", json=_llm_config_body(base_url=""))
    assert response.status_code == 201
    assert response.json()["base_url"] is None


def test_patch_llm_config_empty_base_url_normalized_to_none(client: TestClient) -> None:
    """PATCH with base_url='' stores None, same as omitting the endpoint."""
    client.post("/agent-apps/llm-configs", json=_llm_config_body())
    response = client.patch("/agent-apps/llm-configs/proxy", json={"base_url": ""})
    assert response.status_code == 200
    assert response.json()["base_url"] is None


def test_delete_default_agent_app_forbidden(client: TestClient) -> None:
    """The system default agent app is delete-protected (422), symmetric to llm-configs."""
    app_id = client.post("/agent-apps/apps", json=_app_body(name="default")).json()["id"]
    response = client.delete(f"/agent-apps/apps/{app_id}")
    assert response.status_code == 422
    assert client.get(f"/agent-apps/apps/{app_id}").status_code == 200


def test_patch_default_agent_app_still_demotes_to_draft(client: TestClient) -> None:
    """PATCH on a published app keeps its draft-demotion semantics (unchanged)."""
    client.post("/agent-apps/llm-configs", json=_llm_config_body(name=DEFAULT_LLM_CONFIG_NAME))
    app_id = client.post("/agent-apps/apps", json=_app_body(name="default")).json()["id"]
    assert client.post(f"/agent-apps/apps/{app_id}/publish").json()["status"] == "published"
    response = client.patch(f"/agent-apps/apps/{app_id}", json={"system_prompt": "edited"})
    assert response.status_code == 200
    assert response.json()["status"] == "draft"


# ---------------------------------------------------------------------------
# MCP server CRUD
# ---------------------------------------------------------------------------


def test_create_mcp_server_stdio_returns_201(client: TestClient) -> None:
    """POST /mcp-servers persists the stdio config with hash + audit fields."""
    response = client.post("/agent-apps/mcp-servers", json=_mcp_body())
    assert response.status_code == 201
    payload = response.json()
    assert payload["name"] == "fs-server"
    assert payload["transport"] == "stdio"
    assert payload["enabled"] is True
    assert payload["created_by"] == "ann"
    assert payload["content_hash"]


def test_create_mcp_server_duplicate_name_rejected(client: TestClient) -> None:
    """A second create with the same name is rejected with 422."""
    assert client.post("/agent-apps/mcp-servers", json=_mcp_body()).status_code == 201
    assert client.post("/agent-apps/mcp-servers", json=_mcp_body()).status_code == 422


def test_create_mcp_server_stdio_missing_command_rejected(client: TestClient) -> None:
    """The schema transport guard rejects stdio without command (422)."""
    response = client.post("/agent-apps/mcp-servers", json=_mcp_body(command=None))
    assert response.status_code == 422


@pytest.mark.parametrize("command", ["sh", "bash", "zsh", "dash", "fish", "cmd", "powershell", "curl"])
def test_create_mcp_server_stdio_command_outside_allowlist_rejected(client: TestClient, command: str) -> None:
    """Shell interpreters and non-allowlisted executables are rejected (422)."""
    response = client.post("/agent-apps/mcp-servers", json=_mcp_body(command=command))
    assert response.status_code == 422


@pytest.mark.parametrize("args", [["-c", "print(1)"], ["-m", "evil"]])
def test_create_mcp_server_python_inline_execution_rejected(client: TestClient, args: list[str]) -> None:
    """Python -c / -m inline execution modes are rejected (422)."""
    response = client.post("/agent-apps/mcp-servers", json=_mcp_body(command="python", args=args))
    assert response.status_code == 422


def test_create_mcp_server_node_inline_execution_rejected(client: TestClient) -> None:
    """Node -e / --eval inline execution modes are rejected (422)."""
    response = client.post("/agent-apps/mcp-servers", json=_mcp_body(command="node", args=["-e", "process.exit()"]))
    assert response.status_code == 422


def test_create_mcp_server_python_script_allowed(client: TestClient) -> None:
    """Allowlisted executables running plain script files are accepted."""
    response = client.post("/agent-apps/mcp-servers", json=_mcp_body(command="python", args=["server.py"]))
    assert response.status_code == 201


def test_create_mcp_server_stdio_allowlist_configurable(client: TestClient, monkeypatch: pytest.MonkeyPatch) -> None:
    """MCP_STDIO_ALLOWED_COMMANDS extends the accepted executable set."""
    monkeypatch.setattr(settings, "MCP_STDIO_ALLOWED_COMMANDS", ["python", "node", "uvx", "npx", "myserver"])
    assert client.post("/agent-apps/mcp-servers", json=_mcp_body(command="myserver")).status_code == 201


def test_patch_mcp_server_stdio_command_revalidated(client: TestClient) -> None:
    """PATCH changing command/args re-validates against the allowlist."""
    client.post("/agent-apps/mcp-servers", json=_mcp_body())
    bad_command = client.patch("/agent-apps/mcp-servers/fs-server", json={"command": "bash"})
    assert bad_command.status_code == 422
    bad_args = client.patch("/agent-apps/mcp-servers/fs-server", json={"command": "python", "args": ["-c", "x"]})
    assert bad_args.status_code == 422
    assert client.get("/agent-apps/mcp-servers/fs-server").json()["command"] == "uvx"


def test_create_mcp_server_plaintext_env_secret_rejected(client: TestClient) -> None:
    """Plaintext secret values in env are rejected; placeholders are accepted."""
    response = client.post("/agent-apps/mcp-servers", json=_mcp_body(env={"API_TOKEN": "hunter2"}))
    assert response.status_code == 422

    ok = client.post("/agent-apps/mcp-servers", json=_mcp_body(env={"API_TOKEN": "${API_TOKEN}"}))
    assert ok.status_code == 201
    assert ok.json()["env"] == {"API_TOKEN": "${API_TOKEN}"}


def test_create_mcp_server_placeholder_trailing_newline_rejected(client: TestClient) -> None:
    """A trailing newline after the placeholder must not bypass validation."""
    response = client.post("/agent-apps/mcp-servers", json=_mcp_body(env={"API_TOKEN": "${API_TOKEN}\n"}))
    assert response.status_code == 422


def test_create_mcp_server_plaintext_headers_rejected(client: TestClient) -> None:
    """Plaintext header values are rejected; ``${ENV_VAR}`` placeholders pass."""
    response = client.post(
        "/agent-apps/mcp-servers",
        json=_mcp_body(transport="http", command=None, url="https://mcp.example.com", headers={"Authorization": "Bearer sk-1"}),
    )
    assert response.status_code == 422

    ok = client.post(
        "/agent-apps/mcp-servers",
        json=_mcp_body(transport="http", command=None, url="https://mcp.example.com", headers={"Authorization": "${MCP_AUTH}"}),
    )
    assert ok.status_code == 201
    assert ok.json()["headers"] == {"Authorization": "${MCP_AUTH}"}


def test_probe_server_tool_names_timeout_degrades(monkeypatch: pytest.MonkeyPatch) -> None:
    """A probe exceeding the timeout degrades to None instead of hanging."""
    import asyncio

    class SlowClient:
        def __init__(self, connections: Any) -> None:
            pass

        async def get_tools(self) -> list[Any]:
            await asyncio.sleep(5)
            return []

    monkeypatch.setattr(agent_apps_module, "MultiServerMCPClient", SlowClient)
    monkeypatch.setattr(agent_apps_module, "_MCP_PROBE_TIMEOUT_SECONDS", 0.01)
    server = McpServerConfig(
        name="slow",
        transport="stdio",
        command="uvx",
        args=[],
        env={},
        content_hash="x",
    )
    assert asyncio.run(_REAL_PROBE_SERVER_TOOL_NAMES(server)) is None


def test_create_mcp_server_tool_collision_rejected(
    client: TestClient, probe_tools: AsyncMock, monkeypatch: pytest.MonkeyPatch
) -> None:
    """A candidate tool colliding with the catalog is rejected with 422."""
    probe_tools.return_value = ["duckduckgo_results_json"]
    collision = AsyncMock(side_effect=ValueError("tool_name_collision: duckduckgo_results_json"))
    monkeypatch.setattr(agent_apps_module, "check_server_tool_collision", collision)

    response = client.post("/agent-apps/mcp-servers", json=_mcp_body())
    assert response.status_code == 422
    collision.assert_awaited_once()


def test_create_mcp_server_probe_failure_degrades(
    client: TestClient, probe_tools: AsyncMock, monkeypatch: pytest.MonkeyPatch
) -> None:
    """When the probe cannot load tools the collision check is skipped (degrade)."""
    probe_tools.return_value = None
    collision = AsyncMock()
    monkeypatch.setattr(agent_apps_module, "check_server_tool_collision", collision)

    response = client.post("/agent-apps/mcp-servers", json=_mcp_body())
    assert response.status_code == 201
    collision.assert_not_awaited()


def test_get_and_list_mcp_servers(client: TestClient) -> None:
    """GET endpoints resolve stored servers and 404 unknown names."""
    client.post("/agent-apps/mcp-servers", json=_mcp_body())
    assert client.get("/agent-apps/mcp-servers/fs-server").status_code == 200
    assert client.get("/agent-apps/mcp-servers/ghost").status_code == 404
    names = [row["name"] for row in client.get("/agent-apps/mcp-servers").json()]
    assert names == ["fs-server"]


def test_patch_mcp_server_updates_and_invalidates_cache(client: TestClient, quiet_shutdown: AsyncMock) -> None:
    """PATCH applies fields, refreshes the hash and invalidates the MCP cache."""
    created = client.post("/agent-apps/mcp-servers", json=_mcp_body()).json()
    quiet_shutdown.reset_mock()

    response = client.patch("/agent-apps/mcp-servers/fs-server", json={"enabled": False, "description": "files"})
    assert response.status_code == 200
    payload = response.json()
    assert payload["enabled"] is False
    assert payload["description"] == "files"
    assert payload["content_hash"] != created["content_hash"]
    quiet_shutdown.assert_awaited_once()


def test_patch_mcp_server_headers_merge_and_reject_plaintext(client: TestClient) -> None:
    """PATCH merges headers, persists placeholders and rejects plaintext values."""
    assert client.post("/agent-apps/mcp-servers", json=_mcp_body()).status_code == 201

    bad = client.patch("/agent-apps/mcp-servers/fs-server", json={"headers": {"X-Token": "raw-secret"}})
    assert bad.status_code == 422
    assert client.get("/agent-apps/mcp-servers/fs-server").json()["headers"] == {}

    ok = client.patch("/agent-apps/mcp-servers/fs-server", json={"headers": {"X-Token": "${X_TOKEN}"}})
    assert ok.status_code == 200
    assert ok.json()["headers"] == {"X-Token": "${X_TOKEN}"}
    assert client.get("/agent-apps/mcp-servers/fs-server").json()["headers"] == {"X-Token": "${X_TOKEN}"}


def test_patch_mcp_server_rejects_name_change(client: TestClient) -> None:
    """PATCH containing the immutable name field is rejected with 422."""
    client.post("/agent-apps/mcp-servers", json=_mcp_body())
    response = client.patch("/agent-apps/mcp-servers/fs-server", json={"name": "other"})
    assert response.status_code == 422


def test_patch_mcp_server_unknown_name_404(client: TestClient) -> None:
    """PATCH on a missing server returns 404."""
    response = client.patch("/agent-apps/mcp-servers/ghost", json={"enabled": False})
    assert response.status_code == 404


def test_patch_mcp_server_plaintext_env_rejected(client: TestClient) -> None:
    """PATCH env values must stay placeholder-only."""
    client.post("/agent-apps/mcp-servers", json=_mcp_body())
    response = client.patch("/agent-apps/mcp-servers/fs-server", json={"env": {"TOKEN": "plain-secret"}})
    assert response.status_code == 422


def test_patch_mcp_server_transport_switch_requires_matching_fields(client: TestClient) -> None:
    """Switching transports validates the merged config (url for http)."""
    client.post("/agent-apps/mcp-servers", json=_mcp_body())

    missing_url = client.patch("/agent-apps/mcp-servers/fs-server", json={"transport": "http"})
    assert missing_url.status_code == 422

    switched = client.patch(
        "/agent-apps/mcp-servers/fs-server", json={"transport": "http", "url": "https://mcp.example/sse"}
    )
    assert switched.status_code == 200
    assert switched.json()["transport"] == "http"


def test_delete_mcp_server_removes_row_and_invalidates_cache(client: TestClient, quiet_shutdown: AsyncMock) -> None:
    """DELETE removes the server and invalidates the MCP client cache."""
    client.post("/agent-apps/mcp-servers", json=_mcp_body())
    quiet_shutdown.reset_mock()

    assert client.delete("/agent-apps/mcp-servers/fs-server").status_code == 200
    assert client.get("/agent-apps/mcp-servers/fs-server").status_code == 404
    assert client.delete("/agent-apps/mcp-servers/fs-server").status_code == 404
    quiet_shutdown.assert_awaited_once()


# ---------------------------------------------------------------------------
# Tool catalog
# ---------------------------------------------------------------------------


def test_tools_catalog_returns_builtin_and_mcp_entries(client: TestClient, catalog: list[dict[str, Any]]) -> None:
    """GET /tools/catalog exposes source labels and MCP server attribution."""
    catalog.append({"name": "echo", "source": "mcp", "server": "fs-server"})

    response = client.get("/agent-apps/tools/catalog")
    assert response.status_code == 200
    entries = response.json()
    assert {"name": "duckduckgo_results_json", "source": "builtin", "server": None} in entries
    assert {"name": "echo", "source": "mcp", "server": "fs-server"} in entries
