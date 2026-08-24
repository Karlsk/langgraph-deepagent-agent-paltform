"""Unit tests for the agent asset management API (subagents/skills/apps/MCP/providers).

Zero real network / zero real LLM / zero real MCP: the DB layer runs on an
in-memory SQLite session injected via dependency override, the auth
dependency is overridden with a fake chat Session row, and every service
touching the outside world (test runner, skill draft LLM, MCP probes,
catalog builder, client shutdown) is monkeypatched.
"""

from collections.abc import Generator
from datetime import UTC, datetime
from pathlib import Path
from typing import Any
from unittest.mock import AsyncMock

import pytest
from fastapi import FastAPI, HTTPException
from fastapi.exceptions import RequestValidationError
from fastapi.testclient import TestClient
from slowapi.errors import RateLimitExceeded
from sqlalchemy.pool import StaticPool
from sqlmodel import SQLModel, create_engine
from sqlmodel import Session as DBSession
from starlette.exceptions import HTTPException as StarletteHTTPException

from app.api.error_handlers import (
    http_exception_handler,
    rate_limit_exceeded_handler,
    validation_exception_handler,
)
from app.api.v1 import agent_assets_common as common_module
from app.api.v1 import apps as apps_module
from app.api.v1 import mcp_servers as mcp_servers_module
from app.api.v1 import skills as skills_module
from app.api.v1 import subagents as subagents_module
from app.api.v1 import auth as auth_module
from app.core import mcp_client
from app.core.config import settings
from app.core.limiter import limiter
from app.core.mcp_client import MCPUpstreamError, ToolSummary
from app.models.provider import DEFAULT_MODEL_REF, ModelConfig, Provider
from app.models.session import Session as ChatSession
from app.models.subagent_trace import SubAgentTestTrace
from app.schemas.agent_apps import SubAgentTestResult
from app.services.agents import skills_store
from pydantic import ValidationError
from tests.conftest import unwrap

pytestmark = pytest.mark.unit

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
    """Stub build_tool_catalog on the API modules with a mutable builtin list."""
    entries: list[dict[str, Any]] = [dict(entry) for entry in BUILTIN_CATALOG]

    async def fake_build_tool_catalog(session: Any) -> list[dict[str, Any]]:
        return entries

    monkeypatch.setattr(apps_module, "build_tool_catalog", fake_build_tool_catalog)
    monkeypatch.setattr(mcp_servers_module, "build_tool_catalog", fake_build_tool_catalog)
    return entries


@pytest.fixture(autouse=True)
def quiet_shutdown(monkeypatch: pytest.MonkeyPatch) -> AsyncMock:
    """Record MCP client cache invalidation calls without touching real clients."""
    shutdown = AsyncMock()
    monkeypatch.setattr(mcp_servers_module, "shutdown_mcp_clients", shutdown)
    return shutdown


@pytest.fixture(autouse=True)
def probe_tools(monkeypatch: pytest.MonkeyPatch) -> AsyncMock:
    """Stub the core-layer MCP tool probe (zero real connections)."""
    probe = AsyncMock(return_value=[])
    monkeypatch.setattr(mcp_client, "probe_tools", probe)
    return probe


@pytest.fixture
def client(db_session: DBSession, fake_chat_session: ChatSession) -> Generator[TestClient, None, None]:
    """Minimal app wiring the agent-asset routers with limiter + dependency overrides.

    Registers the exact envelope handlers from ``app.api.error_handlers``
    so error-path assertions validate the production {code, message, data}
    shape (the catch-all ``Exception`` handler stays unregistered so
    unexpected errors still surface instead of being swallowed by
    TestClient).
    """
    app = FastAPI()
    app.state.limiter = limiter
    app.add_exception_handler(RequestValidationError, validation_exception_handler)
    app.add_exception_handler(HTTPException, http_exception_handler)  # pyright: ignore[reportArgumentType]
    app.add_exception_handler(StarletteHTTPException, http_exception_handler)  # pyright: ignore[reportArgumentType]
    app.add_exception_handler(RateLimitExceeded, rate_limit_exceeded_handler)  # pyright: ignore[reportArgumentType]
    app.include_router(subagents_module.router)
    app.include_router(skills_module.router)
    app.include_router(apps_module.router)
    app.include_router(mcp_servers_module.router)
    app.dependency_overrides[auth_module.get_current_session] = lambda: fake_chat_session
    app.dependency_overrides[common_module.get_db_session] = lambda: db_session
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


def _seed_default_pair(db_session: DBSession) -> None:
    """Seed the default provider/model pair that NULL model references resolve to."""
    provider = Provider(
        name="default",
        type="OPENAI_COMPATIBLE",
        auth_config={"api_key": "sk-test-default"},
    )
    db_session.add(provider)
    db_session.commit()
    db_session.refresh(provider)
    db_session.add(ModelConfig(provider_id=provider.id, name="default", model_id="MiniMax-M3"))
    db_session.commit()


# ---------------------------------------------------------------------------
# SubAgent CRUD
# ---------------------------------------------------------------------------


def test_create_subagent_returns_201_with_audit_fields(client: TestClient) -> None:
    """POST /subagents persists the row with hash, version and created_by."""
    response = client.post("/subagents", json=_subagent_body())
    assert response.status_code == 201
    payload = unwrap(response, expected_code=201)
    assert payload["name"] == "researcher"
    assert payload["version"] == 1
    assert payload["created_by"] == "ann"
    assert payload["content_hash"]


def test_create_subagent_duplicate_name_rejected(client: TestClient) -> None:
    """A second create with the same name is rejected with 422."""
    assert client.post("/subagents", json=_subagent_body()).status_code == 201
    response = client.post("/subagents", json=_subagent_body())
    assert response.status_code == 422


def test_list_subagents_returns_created_rows(client: TestClient) -> None:
    """GET /subagents lists every stored sub-agent."""
    client.post("/subagents", json=_subagent_body())
    client.post("/subagents", json=_subagent_body(name="writer"))
    response = client.get("/subagents")
    assert response.status_code == 200
    assert [row["name"] for row in unwrap(response)] == ["researcher", "writer"]


def test_get_subagent_returns_row_or_404(client: TestClient) -> None:
    """GET /subagents/{name} resolves existing rows and 404s unknown ones."""
    client.post("/subagents", json=_subagent_body())
    assert client.get("/subagents/researcher").status_code == 200
    assert client.get("/subagents/ghost").status_code == 404


def test_patch_subagent_updates_fields_and_bumps_version(client: TestClient) -> None:
    """PATCH applies partial fields, refreshes the hash and bumps version."""
    created = unwrap(client.post("/subagents", json=_subagent_body()), expected_code=201)
    response = client.patch("/subagents/researcher", json={"description": "New desc"})
    assert response.status_code == 200
    payload = unwrap(response)
    assert payload["description"] == "New desc"
    assert payload["version"] == 2
    assert payload["content_hash"] != created["content_hash"]


def test_patch_subagent_rejects_name_change(client: TestClient) -> None:
    """PATCH containing the immutable name field is rejected with 422."""
    client.post("/subagents", json=_subagent_body())
    response = client.patch("/subagents/researcher", json={"name": "other"})
    assert response.status_code == 422


def test_patch_subagent_empty_payload_rejected(client: TestClient) -> None:
    """PATCH without any updatable field is rejected with 422."""
    client.post("/subagents", json=_subagent_body())
    response = client.patch("/subagents/researcher", json={})
    assert response.status_code == 422


def test_patch_subagent_unknown_name_404(client: TestClient) -> None:
    """PATCH on a missing sub-agent returns 404."""
    response = client.patch("/subagents/ghost", json={"description": "x"})
    assert response.status_code == 404


def test_delete_subagent_removes_row(client: TestClient) -> None:
    """DELETE removes the row; subsequent reads 404, unknown deletes 404."""
    client.post("/subagents", json=_subagent_body())
    assert client.delete("/subagents/researcher").status_code == 200
    assert client.get("/subagents/researcher").status_code == 404
    assert client.delete("/subagents/researcher").status_code == 404


def test_create_subagent_with_skill_names_returns_field(client: TestClient) -> None:
    """POST /subagents persists skill_names and surfaces it on the read payload."""
    body = _subagent_body(skill_names=["pdf-export", "csv-clean"])
    response = client.post("/subagents", json=body)
    assert response.status_code == 201
    payload = unwrap(response, expected_code=201)
    assert payload["skill_names"] == ["pdf-export", "csv-clean"]


def test_create_subagent_default_skill_names_is_null(client: TestClient) -> None:
    """Omitting skill_names yields a NULL row (inherit parent app semantics)."""
    response = client.post("/subagents", json=_subagent_body())
    assert response.status_code == 201
    assert unwrap(response, expected_code=201)["skill_names"] is None


def test_patch_subagent_skill_names_bumps_hash_and_version(client: TestClient) -> None:
    """skill_names is part of the content_hash; changing it invalidates the hash."""
    created = unwrap(client.post("/subagents", json=_subagent_body()), expected_code=201)
    response = client.patch("/subagents/researcher", json={"skill_names": ["pdf-export"]})
    assert response.status_code == 200
    payload = unwrap(response)
    assert payload["skill_names"] == ["pdf-export"]
    assert payload["version"] == 2
    assert payload["content_hash"] != created["content_hash"]


def test_delete_subagent_referenced_by_agent_app_rejected(client: TestClient, db_session: DBSession) -> None:
    """A SubAgent bound by an AgentApp's subagent_names is delete-protected (422)."""
    client.post("/subagents", json=_subagent_body())
    _seed_default_pair(db_session)
    unwrap(client.post("/apps", json=_app_body(subagent_names=["researcher"])), expected_code=201)
    response = client.delete("/subagents/researcher")
    assert response.status_code == 422
    body = response.json()
    assert body["code"] == 422
    assert "agent_app:support-app" in body["message"]
    # Row is preserved; reads still succeed.
    assert client.get("/subagents/researcher").status_code == 200


# ---------------------------------------------------------------------------
# SubAgent test-run endpoint
# ---------------------------------------------------------------------------


def test_subagent_test_invokes_run_subagent_once(
    client: TestClient, db_session: DBSession, monkeypatch: pytest.MonkeyPatch
) -> None:
    """POST /subagents/{name}/test delegates to run_subagent_once."""
    client.post("/subagents", json=_subagent_body())
    run_once = AsyncMock(
        return_value=SubAgentTestResult(
            final_message="done", turns=2, duration_seconds=1.5, model="gpt-5-mini", trace_id=42
        )
    )
    monkeypatch.setattr(subagents_module, "run_subagent_once", run_once)

    response = client.post("/subagents/researcher/test", json={"prompt": "hello"})
    assert response.status_code == 200
    assert unwrap(response) == {
        "final_message": "done",
        "turns": 2,
        "duration_seconds": 1.5,
        "model": "gpt-5-mini",
        "trace_id": 42,
    }
    run_once.assert_awaited_once()
    kwargs = run_once.await_args.kwargs
    assert kwargs["name"] == "researcher"
    assert kwargs["prompt"] == "hello"
    assert kwargs["session"] is db_session
    # The audit creator is forwarded so the persisted trace records the user.
    assert kwargs["created_by"] == "ann"


def test_subagent_test_unknown_name_404(client: TestClient) -> None:
    """Test-running a missing sub-agent returns 404 without invoking the runner."""
    response = client.post("/subagents/ghost/test", json={"prompt": "hello"})
    assert response.status_code == 404


def test_subagent_test_runner_failure_500(client: TestClient, monkeypatch: pytest.MonkeyPatch) -> None:
    """Runner errors surface as 500."""
    client.post("/subagents", json=_subagent_body())
    monkeypatch.setattr(subagents_module, "run_subagent_once", AsyncMock(side_effect=RuntimeError("boom")))
    response = client.post("/subagents/researcher/test", json={"prompt": "hello"})
    assert response.status_code == 500


# ---------------------------------------------------------------------------
# SubAgent test-run traces (list + detail)
# ---------------------------------------------------------------------------


def _seed_trace(db_session: DBSession, **overrides: Any) -> SubAgentTestTrace:
    """Persist a SubAgentTestTrace row with sensible defaults for API tests."""
    defaults: dict[str, Any] = {
        "name": "researcher",
        "status": "success",
        "prompt": "hello",
        "model": "gpt-5-mini",
        "turns": 1,
        "duration_seconds": 1.25,
        "final_message": "done",
        "events": [{"seq": 1, "type": "run_finished", "status": "success"}],
        "error": None,
        "created_by": "ann",
    }
    defaults.update(overrides)
    trace = SubAgentTestTrace(**defaults)
    db_session.add(trace)
    db_session.commit()
    db_session.refresh(trace)
    return trace


def test_list_test_traces_returns_summaries_newest_first(client: TestClient, db_session: DBSession) -> None:
    """GET /subagents/{name}/test-traces paginates summaries without events."""
    client.post("/subagents", json=_subagent_body())
    base = datetime(2026, 8, 24, 8, 0, 0, tzinfo=UTC)
    older = _seed_trace(db_session, prompt="first", created_at=base)
    newer = _seed_trace(
        db_session, prompt="second", status="error", error="RuntimeError: boom", created_at=base.replace(hour=9)
    )

    response = client.get("/subagents/researcher/test-traces")
    assert response.status_code == 200
    payload = unwrap(response)
    assert payload["total"] == 2
    assert payload["page"] == 1
    assert payload["pageSize"] == 10
    # Newest first; summaries carry no event stream.
    assert [row["id"] for row in payload["items"]] == [newer.id, older.id]
    assert "events" not in payload["items"][0]
    assert payload["items"][0]["status"] == "error"
    assert payload["items"][0]["error"] == "RuntimeError: boom"
    assert payload["items"][0]["created_by"] == "ann"


def test_list_test_traces_filters_by_subagent_name(client: TestClient, db_session: DBSession) -> None:
    """Only traces of the addressed sub-agent are returned."""
    client.post("/subagents", json=_subagent_body())
    client.post("/subagents", json=_subagent_body(name="writer"))
    _seed_trace(db_session)  # researcher
    _seed_trace(db_session, name="writer")

    response = client.get("/subagents/researcher/test-traces")
    payload = unwrap(response)
    assert payload["total"] == 1
    assert payload["items"][0]["prompt"] == "hello"


def test_list_test_traces_unknown_subagent_404(client: TestClient) -> None:
    """Listing traces of a missing sub-agent returns 404."""
    assert client.get("/subagents/ghost/test-traces").status_code == 404


def test_get_test_trace_detail_includes_events(client: TestClient, db_session: DBSession) -> None:
    """GET /subagents/{name}/test-traces/{trace_id} returns the full event stream."""
    client.post("/subagents", json=_subagent_body())
    trace = _seed_trace(
        db_session,
        events=[
            {"seq": 1, "type": "llm_call", "status": "success", "output_text": "done"},
            {"seq": 2, "type": "run_finished", "status": "success"},
        ],
    )

    response = client.get(f"/subagents/researcher/test-traces/{trace.id}")
    assert response.status_code == 200
    payload = unwrap(response)
    assert payload["id"] == trace.id
    assert payload["final_message"] == "done"
    assert [event["type"] for event in payload["events"]] == ["llm_call", "run_finished"]


def test_get_test_trace_unknown_id_or_owner_404(client: TestClient, db_session: DBSession) -> None:
    """A missing trace id or an owner mismatch returns 404."""
    client.post("/subagents", json=_subagent_body())
    client.post("/subagents", json=_subagent_body(name="writer"))
    trace = _seed_trace(db_session, name="writer")

    assert client.get("/subagents/researcher/test-traces/9999").status_code == 404
    # The trace exists but belongs to "writer", not "researcher".
    assert client.get(f"/subagents/researcher/test-traces/{trace.id}").status_code == 404
    assert client.get("/subagents/ghost/test-traces/1").status_code == 404


# ---------------------------------------------------------------------------
# Skill CRUD (real skills_store on a tmp SKILLS_ROOT)
# ---------------------------------------------------------------------------


def test_create_skill_returns_201_and_writes_file(client: TestClient, isolated_skills_root: str) -> None:
    """POST /skills persists metadata and writes the global SKILL.md."""
    response = client.post("/skills", json=_skill_body())
    assert response.status_code == 201
    payload = unwrap(response, expected_code=201)
    assert payload["name"] == "pdf-export"
    assert payload["version"] == 1
    assert payload["created_by"] == "ann"
    assert (Path(isolated_skills_root) / "global" / "pdf-export" / "SKILL.md").exists()


def test_create_skill_duplicate_name_rejected(client: TestClient) -> None:
    """A second create with the same name is rejected with 422."""
    assert client.post("/skills", json=_skill_body()).status_code == 201
    assert client.post("/skills", json=_skill_body()).status_code == 422


def test_list_skills_returns_metadata(client: TestClient) -> None:
    """GET /skills lists every stored skill."""
    client.post("/skills", json=_skill_body())
    client.post("/skills", json=_skill_body(name="csv-clean", description="Clean CSV"))
    response = client.get("/skills")
    assert response.status_code == 200
    assert {row["name"] for row in unwrap(response)} == {"pdf-export", "csv-clean"}


def test_get_skill_returns_row_or_404(client: TestClient) -> None:
    """GET /skills/{name} resolves existing rows and 404s unknown ones."""
    client.post("/skills", json=_skill_body())
    assert client.get("/skills/pdf-export").status_code == 200
    assert client.get("/skills/ghost").status_code == 404


def test_patch_skill_updates_body_and_bumps_version(client: TestClient) -> None:
    """PATCH rewrites the skill content, refreshes the hash and bumps version."""
    created = unwrap(client.post("/skills", json=_skill_body()), expected_code=201)
    response = client.patch("/skills/pdf-export", json={"body": "# v2\n"})
    assert response.status_code == 200
    payload = unwrap(response)
    assert payload["version"] == 2
    assert payload["content_hash"] != created["content_hash"]


def test_patch_skill_rejects_name_change(client: TestClient) -> None:
    """PATCH containing the immutable name field is rejected with 422."""
    client.post("/skills", json=_skill_body())
    response = client.patch("/skills/pdf-export", json={"name": "other"})
    assert response.status_code == 422


def test_patch_skill_empty_payload_rejected(client: TestClient) -> None:
    """PATCH without description/body is rejected with 422."""
    client.post("/skills", json=_skill_body())
    response = client.patch("/skills/pdf-export", json={})
    assert response.status_code == 422


def test_patch_skill_unknown_name_404(client: TestClient) -> None:
    """PATCH on a missing skill returns 404."""
    response = client.patch("/skills/ghost", json={"description": "x"})
    assert response.status_code == 404


def test_get_skill_content_returns_body(client: TestClient) -> None:
    """GET /skills/{name}/content returns the raw SKILL.md body."""
    client.post("/skills", json=_skill_body())
    response = client.get("/skills/pdf-export/content")
    assert response.status_code == 200
    payload = unwrap(response)
    assert payload["name"] == "pdf-export"
    assert payload["content"] == "# pdf-export\n\n## Steps\n1. render\n"


def test_get_skill_content_unknown_name_404(client: TestClient) -> None:
    """Reading the content of a missing skill returns 404."""
    assert client.get("/skills/ghost/content").status_code == 404


def test_get_skill_content_selfheals_lost_disk_file(client: TestClient, isolated_skills_root: str) -> None:
    """GET /skills/{name}/content rebuilds a lost disk file from the DB body."""
    client.post("/skills", json=_skill_body())
    (Path(isolated_skills_root) / "global" / "pdf-export" / "SKILL.md").unlink()

    response = client.get("/skills/pdf-export/content")

    assert response.status_code == 200
    payload = unwrap(response)
    assert payload["content"] == "# pdf-export\n\n## Steps\n1. render\n"
    assert (Path(isolated_skills_root) / "global" / "pdf-export" / "SKILL.md").exists()


def test_refresh_all_skills_rewrites_lost_and_drifted_files(client: TestClient, isolated_skills_root: str) -> None:
    """POST /skills/refresh rebuilds lost/drifted disk files from the DB bodies."""
    client.post("/skills", json=_skill_body())
    client.post("/skills", json=_skill_body(name="csv-clean", description="Clean CSV"))
    # pdf-export: disk file lost entirely; csv-clean: drifted content
    (Path(isolated_skills_root) / "global" / "pdf-export" / "SKILL.md").unlink()
    (Path(isolated_skills_root) / "global" / "csv-clean" / "SKILL.md").write_text("drifted", encoding="utf-8")

    response = client.post("/skills/refresh")

    assert response.status_code == 200
    report = unwrap(response)
    by_name = {entry["name"]: entry["action"] for entry in report["items"]}
    assert by_name == {"pdf-export": "rewritten", "csv-clean": "rewritten"}
    assert report["rewritten"] == 2
    body = _skill_body()["body"]
    assert (Path(isolated_skills_root) / "global" / "pdf-export" / "SKILL.md").read_text(encoding="utf-8") == body
    assert (Path(isolated_skills_root) / "global" / "csv-clean" / "SKILL.md").read_text(encoding="utf-8") == body


def test_refresh_all_reports_unchanged_for_healthy_files(client: TestClient) -> None:
    """POST /skills/refresh marks matching disk files as unchanged."""
    client.post("/skills", json=_skill_body())

    response = client.post("/skills/refresh")

    assert response.status_code == 200
    report = unwrap(response)
    assert report["items"] == [{"name": "pdf-export", "action": "unchanged"}]
    assert report["unchanged"] == 1


def test_refresh_single_skill_rewrites_lost_file(client: TestClient, isolated_skills_root: str) -> None:
    """POST /skills/{name}/refresh rebuilds one skill's disk file from the DB."""
    client.post("/skills", json=_skill_body())
    skill_file = Path(isolated_skills_root) / "global" / "pdf-export" / "SKILL.md"
    skill_file.unlink()

    response = client.post("/skills/pdf-export/refresh")

    assert response.status_code == 200
    report = unwrap(response)
    assert report["items"] == [{"name": "pdf-export", "action": "rewritten"}]
    assert skill_file.read_text(encoding="utf-8") == _skill_body()["body"]


def test_refresh_single_unknown_skill_404(client: TestClient) -> None:
    """POST /skills/{name}/refresh on a missing DB row returns 404."""
    response = client.post("/skills/ghost/refresh")

    assert response.status_code == 404


def test_delete_skill_removes_row(client: TestClient) -> None:
    """DELETE removes the skill; subsequent reads 404."""
    client.post("/skills", json=_skill_body())
    assert client.delete("/skills/pdf-export").status_code == 200
    assert client.get("/skills/pdf-export").status_code == 404
    assert client.delete("/skills/pdf-export").status_code == 404


def test_delete_skill_referenced_by_app_rejected(client: TestClient) -> None:
    """A skill in any AgentApp.skill_names is delete-protected (422)."""
    client.post("/skills", json=_skill_body())
    unwrap(client.post("/apps", json=_app_body(skill_names=["pdf-export"])), expected_code=201)
    response = client.delete("/skills/pdf-export")
    assert response.status_code == 422
    body = response.json()
    assert body["code"] == 422
    assert "agent_app:support-app" in body["message"]
    assert client.get("/skills/pdf-export").status_code == 200


def test_delete_skill_referenced_by_subagent_rejected(client: TestClient) -> None:
    """A skill in any SubAgentConfig.skill_names is delete-protected (422)."""
    client.post("/skills", json=_skill_body())
    client.post("/subagents", json=_subagent_body(skill_names=["pdf-export"]))
    response = client.delete("/skills/pdf-export")
    assert response.status_code == 422
    body = response.json()
    assert body["code"] == 422
    assert "subagent:researcher" in body["message"]
    assert client.get("/skills/pdf-export").status_code == 200


def test_delete_skill_inherit_subagent_not_blocked(client: TestClient) -> None:
    """A SubAgent with skill_names=None does NOT block skill deletion (inherit mode)."""
    client.post("/skills", json=_skill_body())
    client.post("/subagents", json=_subagent_body())  # skill_names omitted -> None
    response = client.delete("/skills/pdf-export")
    assert response.status_code == 200


# ---------------------------------------------------------------------------
# Skill draft generation
# ---------------------------------------------------------------------------


def test_skill_generate_returns_draft(client: TestClient, monkeypatch: pytest.MonkeyPatch) -> None:
    """POST /skills/generate delegates to generate_skill_draft (draft only)."""
    generate = AsyncMock(return_value="# draft-skill\n")
    monkeypatch.setattr(skills_store, "generate_skill_draft", generate)

    response = client.post("/skills/generate", json={"description": "do things", "hint": "be brief"})
    assert response.status_code == 200
    assert unwrap(response) == {"draft": "# draft-skill\n"}
    generate.assert_awaited_once_with(description="do things", hint="be brief")
    assert unwrap(client.get("/skills")) == []


def test_skill_generate_llm_failure_500(client: TestClient, monkeypatch: pytest.MonkeyPatch) -> None:
    """LLM draft failures surface as 500."""
    monkeypatch.setattr(skills_store, "generate_skill_draft", AsyncMock(side_effect=RuntimeError("llm down")))
    response = client.post("/skills/generate", json={"description": "do things"})
    assert response.status_code == 500


# ---------------------------------------------------------------------------
# AgentApp CRUD
# ---------------------------------------------------------------------------


def test_create_agent_app_returns_201_with_defaults(client: TestClient) -> None:
    """POST /apps creates a draft app on the deepagents engine."""
    response = client.post("/apps", json=_app_body())
    assert response.status_code == 201
    payload = unwrap(response, expected_code=201)
    assert payload["name"] == "support-app"
    assert payload["status"] == "draft"
    assert payload["engine"] == "deepagents"
    assert payload["version"] == 1
    assert payload["created_by"] == "ann"
    assert isinstance(payload["id"], int)


def test_create_agent_app_duplicate_name_rejected(client: TestClient) -> None:
    """A second create with the same name is rejected with 422."""
    assert client.post("/apps", json=_app_body()).status_code == 201
    assert client.post("/apps", json=_app_body()).status_code == 422


def test_list_agent_apps(client: TestClient) -> None:
    """GET /apps lists every stored app."""
    client.post("/apps", json=_app_body())
    client.post("/apps", json=_app_body(name="sales-app"))
    response = client.get("/apps")
    assert response.status_code == 200
    assert {row["name"] for row in unwrap(response)} == {"support-app", "sales-app"}


def test_get_agent_app_returns_row_or_404(client: TestClient) -> None:
    """GET /apps/{id} resolves existing rows and 404s unknown ids."""
    app_id = unwrap(client.post("/apps", json=_app_body()), expected_code=201)["id"]
    assert client.get(f"/apps/{app_id}").status_code == 200
    assert client.get("/apps/9999").status_code == 404


def test_list_published_apps_route_precedes_id_route(client: TestClient) -> None:
    """GET /apps/published is a distinct route (not swallowed by /apps/{id})."""
    response = client.get("/apps/published")
    assert response.status_code == 200
    assert unwrap(response) == []


def test_patch_agent_app_replaces_collections_and_bumps_version(client: TestClient) -> None:
    """PATCH replaces list fields wholesale and bumps the version."""
    client.post("/skills", json=_skill_body())
    app_id = unwrap(client.post("/apps", json=_app_body()), expected_code=201)["id"]
    response = client.patch(f"/apps/{app_id}", json={"skill_names": ["pdf-export"]})
    assert response.status_code == 200
    payload = unwrap(response)
    assert payload["skill_names"] == ["pdf-export"]
    assert payload["version"] == 2


def test_patch_agent_app_rejects_name_change(client: TestClient) -> None:
    """PATCH containing the immutable name field is rejected with 422."""
    app_id = unwrap(client.post("/apps", json=_app_body()), expected_code=201)["id"]
    response = client.patch(f"/apps/{app_id}", json={"name": "other"})
    assert response.status_code == 422


def test_patch_agent_app_unknown_id_404(client: TestClient) -> None:
    """PATCH on a missing app returns 404."""
    response = client.patch("/apps/9999", json={"system_prompt": "x"})
    assert response.status_code == 404


@pytest.mark.parametrize("field", ["skill_names", "subagent_names", "interrupt_on"])
def test_patch_agent_app_explicit_null_collection_rejected(client: TestClient, field: str) -> None:
    """Explicit JSON null on non-null JSON columns is rejected with 422 (not 500)."""
    app_id = unwrap(client.post("/apps", json=_app_body()), expected_code=201)["id"]
    response = client.patch(f"/apps/{app_id}", json={field: None})
    assert response.status_code == 422
    assert unwrap(client.get(f"/apps/{app_id}"))["version"] == 1


def test_patch_published_app_content_edit_reverts_status_to_draft(client: TestClient, db_session: DBSession) -> None:
    """Editing content fields of a published app demotes it back to draft."""
    app_id = _seed_publishable_app(client, db_session)
    assert unwrap(client.post(f"/apps/{app_id}/publish"))["status"] == "published"

    response = client.patch(f"/apps/{app_id}", json={"system_prompt": "You are edited."})
    assert response.status_code == 200
    assert unwrap(response)["status"] == "draft"
    assert unwrap(client.get("/apps/published")) == []


def test_delete_agent_app_removes_row(client: TestClient) -> None:
    """DELETE removes the app; subsequent reads 404."""
    app_id = unwrap(client.post("/apps", json=_app_body()), expected_code=201)["id"]
    assert client.delete(f"/apps/{app_id}").status_code == 200
    assert client.get(f"/apps/{app_id}").status_code == 404
    assert client.delete(f"/apps/{app_id}").status_code == 404


# ---------------------------------------------------------------------------
# AgentApp publish
# ---------------------------------------------------------------------------


def _seed_publishable_app(client: TestClient, db_session: DBSession, **app_overrides: Any) -> int:
    """Create one skill + one subagent + default model pair + one app; return app id."""
    client.post("/skills", json=_skill_body())
    client.post("/subagents", json=_subagent_body())
    _seed_default_pair(db_session)
    body = _app_body(skill_names=["pdf-export"], subagent_names=["researcher"])
    body["allowed_tools"] = ["duckduckgo_results_json"]
    body.update(app_overrides)
    return int(unwrap(client.post("/apps", json=body), expected_code=201)["id"])


def test_publish_success_sets_status_hash_and_version(client: TestClient, db_session: DBSession) -> None:
    """Publish validates references + whitelist, then stamps hash/status/version."""
    app_id = _seed_publishable_app(client, db_session)
    response = client.post(f"/apps/{app_id}/publish")
    assert response.status_code == 200
    payload = unwrap(response)
    assert payload["status"] == "published"
    assert payload["version"] == 2
    assert payload["published_hash"]

    listed = unwrap(client.get("/apps/published"))
    assert [row["id"] for row in listed] == [app_id]


def test_publish_unknown_tool_whitelist_rejected(client: TestClient, db_session: DBSession) -> None:
    """allowed_tools outside the catalog are rejected with 422."""
    app_id = _seed_publishable_app(client, db_session, allowed_tools=["ghost-tool"])
    response = client.post(f"/apps/{app_id}/publish")
    assert response.status_code == 422
    assert unwrap(client.get(f"/apps/{app_id}"))["status"] == "draft"


def test_publish_subagent_unknown_tool_rejected(client: TestClient) -> None:
    """A bound subagent with an unknown tool whitelist blocks publish (422)."""
    client.post("/subagents", json=_subagent_body(allowed_tools=["ghost-tool"]))
    app_id = unwrap(
        client.post("/apps", json=_app_body(subagent_names=["researcher"], allowed_tools=None)),
        expected_code=201,
    )["id"]
    response = client.post(f"/apps/{app_id}/publish")
    assert response.status_code == 422


def test_publish_missing_skill_reference_rejected(client: TestClient) -> None:
    """Referencing a nonexistent skill is rejected with 422."""
    app_id = unwrap(client.post("/apps", json=_app_body(skill_names=["ghost-skill"])), expected_code=201)["id"]
    response = client.post(f"/apps/{app_id}/publish")
    assert response.status_code == 422


def test_publish_missing_subagent_reference_rejected(client: TestClient) -> None:
    """Referencing a nonexistent subagent is rejected with 422."""
    app_id = unwrap(client.post("/apps", json=_app_body(subagent_names=["ghost-sub"])), expected_code=201)["id"]
    response = client.post(f"/apps/{app_id}/publish")
    assert response.status_code == 422


def test_publish_subagent_missing_skill_reference_rejected(client: TestClient, db_session: DBSession) -> None:
    """A subagent's explicit skill_names must resolve to a real SkillAsset."""
    client.post("/subagents", json=_subagent_body(skill_names=["ghost-skill"]))
    _seed_default_pair(db_session)
    app_id = unwrap(client.post("/apps", json=_app_body(subagent_names=["researcher"])), expected_code=201)["id"]
    response = client.post(f"/apps/{app_id}/publish")
    assert response.status_code == 422
    body = response.json()
    assert body["code"] == 422
    assert "ghost-skill" in body["message"]
    assert "researcher" in body["message"]


def test_publish_subagent_skills_union_with_app_skills_changes_hash(client: TestClient, db_session: DBSession) -> None:
    """A subagent-only skill must be folded into the publish hash (recompile trigger).

    App has only skill A; sub-agent adds skill B (not in app). Publishing the
    same app with a different sub-agent (no skill B) must produce a different
    ``published_hash`` because the sub-agent contributes to the fingerprint.
    """
    client.post("/skills", json=_skill_body(name="pdf-export"))
    client.post("/skills", json=_skill_body(name="csv-clean", body="v2\n"))
    _seed_default_pair(db_session)

    # App + sub-agent bound to the extra skill only
    client.post("/subagents", json=_subagent_body(skill_names=["csv-clean"]))
    app_a = unwrap(
        client.post(
            "/apps",
            json=_app_body(name="with-sub-skill", skill_names=["pdf-export"], subagent_names=["researcher"]),
        ),
        expected_code=201,
    )["id"]
    response_a = unwrap(client.post(f"/apps/{app_a}/publish"))

    # App + a different sub-agent that contributes no extra skills
    client.post("/subagents", json=_subagent_body(name="plain", skill_names=None))
    app_b = unwrap(
        client.post(
            "/apps",
            json=_app_body(name="plain-sub", skill_names=["pdf-export"], subagent_names=["plain"]),
        ),
        expected_code=201,
    )["id"]
    response_b = unwrap(client.post(f"/apps/{app_b}/publish"))

    assert response_a["published_hash"] != response_b["published_hash"]


def test_publish_unknown_app_404(client: TestClient) -> None:
    """Publishing a missing app returns 404."""
    response = client.post("/apps/9999/publish")
    assert response.status_code == 404


def test_publish_missing_model_reference_rejected(client: TestClient) -> None:
    """An app referencing a nonexistent provider/model pair is rejected with 422."""
    app_id = unwrap(client.post("/apps", json=_app_body(model="ghost/none")), expected_code=201)["id"]
    response = client.post(f"/apps/{app_id}/publish")
    assert response.status_code == 422
    body = response.json()
    assert body["code"] == 422
    assert "ghost/none" in body["message"]
    assert body["data"] is None


def test_publish_missing_default_model_rejected(client: TestClient) -> None:
    """A NULL model reference needs the default pair; its absence blocks publish."""
    app_id = unwrap(client.post("/apps", json=_app_body()), expected_code=201)["id"]
    response = client.post(f"/apps/{app_id}/publish")
    assert response.status_code == 422
    body = response.json()
    assert body["code"] == 422
    assert DEFAULT_MODEL_REF in body["message"]
    assert body["data"] is None


def test_publish_disabled_model_reference_rejected(client: TestClient, db_session: DBSession) -> None:
    """A disabled referenced model (or its provider) blocks publish with 422."""
    provider = Provider(name="frozen", type="OLLAMA", auth_config={}, enabled=False)
    db_session.add(provider)
    db_session.commit()
    db_session.refresh(provider)
    db_session.add(ModelConfig(provider_id=provider.id, name="locked", model_id="frozen-model"))
    db_session.commit()
    app_id = unwrap(client.post("/apps", json=_app_body(model="frozen/locked")), expected_code=201)["id"]
    response = client.post(f"/apps/{app_id}/publish")
    assert response.status_code == 422
    body = response.json()
    assert body["code"] == 422
    assert "disabled" in body["message"]
    assert body["data"] is None


def test_delete_default_agent_app_forbidden(client: TestClient) -> None:
    """The system default agent app is delete-protected (422), symmetric to providers."""
    app_id = unwrap(client.post("/apps", json=_app_body(name="default")), expected_code=201)["id"]
    response = client.delete(f"/apps/{app_id}")
    assert response.status_code == 422
    assert client.get(f"/apps/{app_id}").status_code == 200


def test_patch_default_agent_app_still_demotes_to_draft(client: TestClient, db_session: DBSession) -> None:
    """PATCH on a published app keeps its draft-demotion semantics (unchanged)."""
    _seed_default_pair(db_session)
    app_id = unwrap(client.post("/apps", json=_app_body(name="default")), expected_code=201)["id"]
    assert unwrap(client.post(f"/apps/{app_id}/publish"))["status"] == "published"
    response = client.patch(f"/apps/{app_id}", json={"system_prompt": "edited"})
    assert response.status_code == 200
    assert unwrap(response)["status"] == "draft"


# ---------------------------------------------------------------------------
# MCP server CRUD
# ---------------------------------------------------------------------------


def test_create_mcp_server_stdio_returns_201(client: TestClient) -> None:
    """POST /mcp-servers persists the stdio config with hash + audit fields."""
    response = client.post("/mcp-servers", json=_mcp_body())
    assert response.status_code == 201
    payload = unwrap(response, expected_code=201)
    assert payload["name"] == "fs-server"
    assert payload["transport"] == "stdio"
    assert payload["enabled"] is True
    assert payload["created_by"] == "ann"
    assert payload["content_hash"]


def test_create_mcp_server_duplicate_name_rejected(client: TestClient) -> None:
    """A second create with the same name is rejected with 422."""
    assert client.post("/mcp-servers", json=_mcp_body()).status_code == 201
    assert client.post("/mcp-servers", json=_mcp_body()).status_code == 422


def test_create_mcp_server_stdio_missing_command_rejected(client: TestClient) -> None:
    """The schema transport guard rejects stdio without command (422)."""
    response = client.post("/mcp-servers", json=_mcp_body(command=None))
    assert response.status_code == 422


@pytest.mark.parametrize("command", ["sh", "bash", "zsh", "dash", "fish", "cmd", "powershell", "curl"])
def test_create_mcp_server_stdio_command_outside_allowlist_rejected(client: TestClient, command: str) -> None:
    """Shell interpreters and non-allowlisted executables are rejected (422)."""
    response = client.post("/mcp-servers", json=_mcp_body(command=command))
    assert response.status_code == 422


@pytest.mark.parametrize("args", [["-c", "print(1)"], ["-m", "evil"]])
def test_create_mcp_server_python_inline_execution_rejected(client: TestClient, args: list[str]) -> None:
    """Python -c / -m inline execution modes are rejected (422)."""
    response = client.post("/mcp-servers", json=_mcp_body(command="python", args=args))
    assert response.status_code == 422


def test_create_mcp_server_node_inline_execution_rejected(client: TestClient) -> None:
    """Node -e / --eval inline execution modes are rejected (422)."""
    response = client.post("/mcp-servers", json=_mcp_body(command="node", args=["-e", "process.exit()"]))
    assert response.status_code == 422


def test_create_mcp_server_python_script_allowed(client: TestClient) -> None:
    """Allowlisted executables running plain script files are accepted."""
    response = client.post("/mcp-servers", json=_mcp_body(command="python", args=["server.py"]))
    assert response.status_code == 201


def test_create_mcp_server_stdio_allowlist_configurable(client: TestClient, monkeypatch: pytest.MonkeyPatch) -> None:
    """MCP_STDIO_ALLOWED_COMMANDS extends the accepted executable set."""
    monkeypatch.setattr(settings, "MCP_STDIO_ALLOWED_COMMANDS", ["python", "node", "uvx", "npx", "myserver"])
    assert client.post("/mcp-servers", json=_mcp_body(command="myserver")).status_code == 201


def test_patch_mcp_server_stdio_command_revalidated(client: TestClient) -> None:
    """PATCH changing command/args re-validates against the allowlist."""
    client.post("/mcp-servers", json=_mcp_body())
    bad_command = client.patch("/mcp-servers/fs-server", json={"command": "bash"})
    assert bad_command.status_code == 422
    bad_args = client.patch("/mcp-servers/fs-server", json={"command": "python", "args": ["-c", "x"]})
    assert bad_args.status_code == 422
    assert unwrap(client.get("/mcp-servers/fs-server"))["command"] == "uvx"


def test_create_mcp_server_plaintext_env_secret_rejected(client: TestClient) -> None:
    """Plaintext secret values in env are rejected; placeholders are accepted."""
    response = client.post("/mcp-servers", json=_mcp_body(env={"API_TOKEN": "hunter2"}))
    assert response.status_code == 422

    ok = client.post("/mcp-servers", json=_mcp_body(env={"API_TOKEN": "${API_TOKEN}"}))
    assert ok.status_code == 201
    assert unwrap(ok, expected_code=201)["env"] == {"API_TOKEN": "${API_TOKEN}"}


def test_create_mcp_server_placeholder_trailing_newline_rejected(client: TestClient) -> None:
    """A trailing newline after the placeholder must not bypass validation."""
    response = client.post("/mcp-servers", json=_mcp_body(env={"API_TOKEN": "${API_TOKEN}\n"}))
    assert response.status_code == 422


def test_create_mcp_server_plaintext_headers_rejected(client: TestClient) -> None:
    """Plaintext header values are rejected; ``${ENV_VAR}`` placeholders pass."""
    response = client.post(
        "/mcp-servers",
        json=_mcp_body(
            transport="http", command=None, url="https://mcp.example.com", headers={"Authorization": "Bearer sk-1"}
        ),
    )
    assert response.status_code == 422

    ok = client.post(
        "/mcp-servers",
        json=_mcp_body(
            transport="http", command=None, url="https://mcp.example.com", headers={"Authorization": "${MCP_AUTH}"}
        ),
    )
    assert ok.status_code == 201
    assert unwrap(ok, expected_code=201)["headers"] == {"Authorization": "${MCP_AUTH}"}


def test_create_mcp_server_tool_collision_rejected(
    client: TestClient, probe_tools: AsyncMock, monkeypatch: pytest.MonkeyPatch
) -> None:
    """A candidate tool colliding with the catalog is rejected with 422."""
    probe_tools.return_value = ["duckduckgo_results_json"]
    collision = AsyncMock(side_effect=ValueError("tool_name_collision: duckduckgo_results_json"))
    monkeypatch.setattr(mcp_servers_module, "check_server_tool_collision", collision)

    response = client.post("/mcp-servers", json=_mcp_body())
    assert response.status_code == 422
    collision.assert_awaited_once()


def test_create_mcp_server_probe_failure_degrades(
    client: TestClient, probe_tools: AsyncMock, monkeypatch: pytest.MonkeyPatch
) -> None:
    """When the probe cannot load tools the collision check is skipped (degrade)."""
    probe_tools.return_value = None
    collision = AsyncMock()
    monkeypatch.setattr(mcp_servers_module, "check_server_tool_collision", collision)

    response = client.post("/mcp-servers", json=_mcp_body())
    assert response.status_code == 201
    collision.assert_not_awaited()


def test_get_and_list_mcp_servers(client: TestClient) -> None:
    """GET endpoints resolve stored servers and 404 unknown names."""
    client.post("/mcp-servers", json=_mcp_body())
    assert client.get("/mcp-servers/fs-server").status_code == 200
    assert client.get("/mcp-servers/ghost").status_code == 404
    names = [row["name"] for row in unwrap(client.get("/mcp-servers"))]
    assert names == ["fs-server"]


def test_patch_mcp_server_updates_and_invalidates_cache(client: TestClient, quiet_shutdown: AsyncMock) -> None:
    """PATCH applies fields, refreshes the hash and invalidates the MCP cache."""
    created = unwrap(client.post("/mcp-servers", json=_mcp_body()), expected_code=201)
    quiet_shutdown.reset_mock()

    response = client.patch("/mcp-servers/fs-server", json={"enabled": False, "description": "files"})
    assert response.status_code == 200
    payload = unwrap(response)
    assert payload["enabled"] is False
    assert payload["description"] == "files"
    assert payload["content_hash"] != created["content_hash"]
    quiet_shutdown.assert_awaited_once()


def test_patch_mcp_server_headers_merge_and_reject_plaintext(client: TestClient) -> None:
    """PATCH merges headers, persists placeholders and rejects plaintext values."""
    assert client.post("/mcp-servers", json=_mcp_body()).status_code == 201

    bad = client.patch("/mcp-servers/fs-server", json={"headers": {"X-Token": "raw-secret"}})
    assert bad.status_code == 422
    assert unwrap(client.get("/mcp-servers/fs-server"))["headers"] == {}

    ok = client.patch("/mcp-servers/fs-server", json={"headers": {"X-Token": "${X_TOKEN}"}})
    assert ok.status_code == 200
    assert unwrap(ok)["headers"] == {"X-Token": "${X_TOKEN}"}
    assert unwrap(client.get("/mcp-servers/fs-server"))["headers"] == {"X-Token": "${X_TOKEN}"}


def test_patch_mcp_server_rejects_name_change(client: TestClient) -> None:
    """PATCH containing the immutable name field is rejected with 422."""
    client.post("/mcp-servers", json=_mcp_body())
    response = client.patch("/mcp-servers/fs-server", json={"name": "other"})
    assert response.status_code == 422


def test_patch_mcp_server_unknown_name_404(client: TestClient) -> None:
    """PATCH on a missing server returns 404."""
    response = client.patch("/mcp-servers/ghost", json={"enabled": False})
    assert response.status_code == 404


def test_patch_mcp_server_plaintext_env_rejected(client: TestClient) -> None:
    """PATCH env values must stay placeholder-only."""
    client.post("/mcp-servers", json=_mcp_body())
    response = client.patch("/mcp-servers/fs-server", json={"env": {"TOKEN": "plain-secret"}})
    assert response.status_code == 422


def test_patch_mcp_server_transport_switch_requires_matching_fields(client: TestClient) -> None:
    """Switching transports validates the merged config (url for http)."""
    client.post("/mcp-servers", json=_mcp_body())

    missing_url = client.patch("/mcp-servers/fs-server", json={"transport": "http"})
    assert missing_url.status_code == 422

    switched = client.patch("/mcp-servers/fs-server", json={"transport": "http", "url": "https://mcp.example/sse"})
    assert switched.status_code == 200
    assert unwrap(switched)["transport"] == "http"


def test_delete_mcp_server_removes_row_and_invalidates_cache(client: TestClient, quiet_shutdown: AsyncMock) -> None:
    """DELETE removes the server and invalidates the MCP client cache."""
    client.post("/mcp-servers", json=_mcp_body())
    quiet_shutdown.reset_mock()

    assert client.delete("/mcp-servers/fs-server").status_code == 200
    assert client.get("/mcp-servers/fs-server").status_code == 404
    assert client.delete("/mcp-servers/fs-server").status_code == 404
    quiet_shutdown.assert_awaited_once()


# ---------------------------------------------------------------------------
# sse transport + namespace-reserved name policy
# ---------------------------------------------------------------------------


def test_create_mcp_server_sse_returns_201(client: TestClient) -> None:
    """An sse server registers with url + placeholder-only headers."""
    response = client.post(
        "/mcp-servers",
        json=_mcp_body(
            name="events",
            transport="sse",
            command=None,
            url="https://events.example.com/sse",
            headers={"Authorization": "${MCP_AUTH}"},
        ),
    )
    assert response.status_code == 201
    payload = unwrap(response, expected_code=201)
    assert payload["transport"] == "sse"
    assert payload["url"] == "https://events.example.com/sse"
    assert payload["headers"] == {"Authorization": "${MCP_AUTH}"}


@pytest.mark.parametrize(
    "overrides",
    [
        {"transport": "sse", "command": None},  # missing url
        {"transport": "sse", "command": "uvx", "url": "https://events.example.com/sse"},  # command forbidden
    ],
)
def test_create_mcp_server_sse_pairing_violations_rejected(client: TestClient, overrides: dict[str, Any]) -> None:
    """Sse without url or with command is rejected with 422."""
    response = client.post("/mcp-servers", json=_mcp_body(name="events", **overrides))
    assert response.status_code == 422


def test_create_mcp_server_name_with_namespace_separator_rejected(client: TestClient) -> None:
    """Names containing '__' are rejected so {server}__{tool} stays parseable."""
    response = client.post("/mcp-servers", json=_mcp_body(name="bad__name"))
    assert response.status_code == 422


# ---------------------------------------------------------------------------
# Router order: literal paths must precede /mcp-servers/{name}
# ---------------------------------------------------------------------------


def test_mcp_router_registers_literal_paths_before_parametrized_name() -> None:
    """stdio-manifests / stdio-sync must never be captured as a server name."""
    paths = [route.path for route in mcp_servers_module.router.routes]
    literal = {"/mcp-servers/page", "/mcp-servers/stdio-manifests", "/mcp-servers/stdio-sync"}
    parametrized_index = paths.index("/mcp-servers/{name}")
    for path in literal:
        assert paths.index(path) < parametrized_index


# ---------------------------------------------------------------------------
# stdio manifest discovery endpoints
# ---------------------------------------------------------------------------


def test_stdio_manifests_preview_returns_dry_run_report(client: TestClient, monkeypatch: pytest.MonkeyPatch) -> None:
    """GET /mcp-servers/stdio-manifests returns the plan without writing."""
    report = {
        "scanned": 2,
        "created": ["weather"],
        "updated": [],
        "unchanged": ["stub"],
        "skipped": [],
        "invalid": [{"file": "broken.json", "reason": "bad json"}],
    }

    def fake_plan(db: Any) -> dict[str, Any]:
        del db
        return report

    monkeypatch.setattr(mcp_servers_module, "plan_stdio_sync", fake_plan)

    response = client.get("/mcp-servers/stdio-manifests")

    assert response.status_code == 200
    assert unwrap(response) == report


def test_stdio_sync_applies_report_and_invalidates_cache_on_change(
    client: TestClient, monkeypatch: pytest.MonkeyPatch, quiet_shutdown: AsyncMock
) -> None:
    """POST /mcp-servers/stdio-sync commits the report and refreshes caches."""
    report = {"scanned": 1, "created": ["weather"], "updated": [], "unchanged": [], "skipped": [], "invalid": []}

    async def fake_sync(db: Any) -> dict[str, Any]:
        del db
        return report

    monkeypatch.setattr(mcp_servers_module, "sync_stdio_manifests", fake_sync)

    response = client.post("/mcp-servers/stdio-sync")

    assert response.status_code == 200
    assert unwrap(response) == report
    quiet_shutdown.assert_awaited_once()


def test_stdio_sync_without_changes_keeps_caches(
    client: TestClient, monkeypatch: pytest.MonkeyPatch, quiet_shutdown: AsyncMock
) -> None:
    """A no-op sync does not invalidate the pooled sessions."""
    report = {"scanned": 1, "created": [], "updated": [], "unchanged": ["weather"], "skipped": [], "invalid": []}

    async def fake_sync(db: Any) -> dict[str, Any]:
        del db
        return report

    monkeypatch.setattr(mcp_servers_module, "sync_stdio_manifests", fake_sync)

    assert client.post("/mcp-servers/stdio-sync").status_code == 200
    quiet_shutdown.assert_not_awaited()


# ---------------------------------------------------------------------------
# Tool debug endpoints (ephemeral sessions; explicit error semantics)
# ---------------------------------------------------------------------------


def _seed_sse_server(client: TestClient) -> None:
    """Register one sse server row for the debug endpoint tests."""
    client.post(
        "/mcp-servers",
        json=_mcp_body(name="events", transport="sse", command=None, url="https://events.example.com/sse"),
    )


def test_list_mcp_server_tools_returns_live_summaries(client: TestClient, monkeypatch: pytest.MonkeyPatch) -> None:
    """GET /mcp-servers/{name}/tools returns raw tool names, descriptions, schemas."""
    _seed_sse_server(client)

    async def fake_list(spec: Any, timeout: float) -> list[ToolSummary]:
        del spec, timeout
        return [ToolSummary(name="subscribe", description="Subscribe to a feed", args_schema={"type": "object"})]

    monkeypatch.setattr(mcp_client, "list_tools", fake_list)

    response = client.get("/mcp-servers/events/tools")

    assert response.status_code == 200
    assert unwrap(response) == [
        {"name": "subscribe", "description": "Subscribe to a feed", "args_schema": {"type": "object"}}
    ]


def test_list_mcp_server_tools_unknown_server_404(client: TestClient) -> None:
    """Listing tools of a missing server returns 404."""
    assert client.get("/mcp-servers/ghost/tools").status_code == 404


@pytest.mark.parametrize(
    "error, expected",
    [
        (ValueError("unresolved ${ENV_VAR}"), 422),
        (MCPUpstreamError("boom"), 502),
        (TimeoutError(), 504),
    ],
)
def test_list_mcp_server_tools_error_mapping(
    client: TestClient,
    monkeypatch: pytest.MonkeyPatch,
    error: Exception,
    expected: int,
) -> None:
    """Excluded config / upstream failure / timeout map to 422 / 502 / 504."""
    _seed_sse_server(client)

    async def fake_list(spec: Any, timeout: float) -> list[ToolSummary]:
        del spec, timeout
        raise error

    monkeypatch.setattr(mcp_client, "list_tools", fake_list)

    assert client.get("/mcp-servers/events/tools").status_code == expected


def test_call_mcp_server_tool_returns_result(client: TestClient, monkeypatch: pytest.MonkeyPatch) -> None:
    """POST /mcp-servers/{name}/call-tool returns the tool output content."""
    _seed_sse_server(client)

    async def fake_call(spec: Any, tool_name: str, arguments: dict[str, Any], timeout: float) -> str:
        del spec, timeout
        return f"{tool_name}:{arguments.get('topic')}"

    monkeypatch.setattr(mcp_client, "call_tool", fake_call)

    response = client.post(
        "/mcp-servers/events/call-tool",
        json={"tool_name": "subscribe", "arguments": {"topic": "news"}},
    )

    assert response.status_code == 200
    assert unwrap(response) == {"server": "events", "tool_name": "subscribe", "result": "subscribe:news"}


def test_call_mcp_server_tool_unknown_server_404(client: TestClient) -> None:
    """Calling a tool of a missing server returns 404."""
    assert client.post("/mcp-servers/ghost/call-tool", json={"tool_name": "x", "arguments": {}}).status_code == 404


@pytest.mark.parametrize(
    "error, expected",
    [
        (ValueError("unknown tool 'nope'"), 422),
        (ValidationError.from_exception_data("title", []), 422),
        (MCPUpstreamError("boom"), 502),
        (TimeoutError(), 504),
    ],
)
def test_call_mcp_server_tool_error_mapping(
    client: TestClient,
    monkeypatch: pytest.MonkeyPatch,
    error: Exception,
    expected: int,
) -> None:
    """Unknown tool / invalid args / upstream failure / timeout map to 422/422/502/504."""
    _seed_sse_server(client)

    async def fake_call(spec: Any, tool_name: str, arguments: dict[str, Any], timeout: float) -> str:
        del spec, tool_name, arguments, timeout
        raise error

    monkeypatch.setattr(mcp_client, "call_tool", fake_call)

    response = client.post("/mcp-servers/events/call-tool", json={"tool_name": "x", "arguments": {}})

    assert response.status_code == expected


# ---------------------------------------------------------------------------
# Tool catalog
# ---------------------------------------------------------------------------


def test_tools_catalog_returns_builtin_and_mcp_entries(client: TestClient, catalog: list[dict[str, Any]]) -> None:
    """GET /tools/catalog exposes source labels and MCP server attribution."""
    catalog.append({"name": "echo", "source": "mcp", "server": "fs-server"})

    response = client.get("/tools/catalog")
    assert response.status_code == 200
    entries = unwrap(response)
    assert {"name": "duckduckgo_results_json", "source": "builtin", "server": None} in entries
    assert {"name": "echo", "source": "mcp", "server": "fs-server"} in entries


# ---------------------------------------------------------------------------
# Pagination (listPage endpoints)
# ---------------------------------------------------------------------------


def _seed_two_subagents(client: TestClient) -> None:
    """Seed two sub-agents ordered by name: researcher, writer."""
    client.post("/subagents", json=_subagent_body())
    client.post("/subagents", json=_subagent_body(name="writer"))


def test_list_subagents_page_defaults_echo_pagination(client: TestClient) -> None:
    """GET /subagents/page returns a PageResult with default page/pageSize."""
    _seed_two_subagents(client)

    payload = unwrap(client.get("/subagents/page"))

    assert [row["name"] for row in payload["items"]] == ["researcher", "writer"]
    assert payload["total"] == 2
    assert payload["page"] == 1
    assert payload["pageSize"] == 10


def test_list_subagents_page_keyword_filters_case_insensitively(client: TestClient) -> None:
    """Keyword matches name via case-insensitive substring."""
    _seed_two_subagents(client)

    payload = unwrap(client.get("/subagents/page", params={"keyword": "RES"}))

    assert [row["name"] for row in payload["items"]] == ["researcher"]
    assert payload["total"] == 1


def test_list_subagents_page_beyond_last_returns_empty_items(client: TestClient) -> None:
    """Pages past the end return empty items but keep the filtered total."""
    _seed_two_subagents(client)

    payload = unwrap(client.get("/subagents/page", params={"page": 3}))

    assert payload["items"] == []
    assert payload["total"] == 2
    assert payload["page"] == 3


def test_list_subagents_page_respects_page_size_window(client: TestClient) -> None:
    """The pageSize alias mirrors the query param and slices the ordered rows."""
    _seed_two_subagents(client)

    payload = unwrap(client.get("/subagents/page", params={"page": 2, "pageSize": 1}))

    assert [row["name"] for row in payload["items"]] == ["writer"]
    assert payload["pageSize"] == 1
    assert payload["total"] == 2


def test_list_subagents_page_rejects_out_of_bounds_params(client: TestClient) -> None:
    """Values below page 1 or above pageSize 100 are rejected with 422."""
    assert client.get("/subagents/page", params={"page": 0}).status_code == 422
    assert client.get("/subagents/page", params={"pageSize": 101}).status_code == 422


def test_list_skills_page_returns_page_result(client: TestClient) -> None:
    """GET /skills/page paginates skill metadata with keyword filtering."""
    client.post("/skills", json=_skill_body())
    client.post("/skills", json=_skill_body(name="csv-clean", description="Clean CSV", body="# csv\n"))

    payload = unwrap(client.get("/skills/page", params={"keyword": "PDF"}))

    assert [row["name"] for row in payload["items"]] == ["pdf-export"]
    assert payload["total"] == 1
    assert payload["pageSize"] == 10


def test_list_agent_apps_page_returns_page_result(client: TestClient) -> None:
    """GET /apps/page paginates agent apps ordered by id."""
    client.post("/apps", json=_app_body())
    client.post("/apps", json=_app_body(name="sales-app"))

    payload = unwrap(client.get("/apps/page"))

    assert {row["name"] for row in payload["items"]} == {"support-app", "sales-app"}
    assert payload["total"] == 2
    assert payload["pageSize"] == 10


def test_list_mcp_servers_page_returns_page_result(client: TestClient) -> None:
    """GET /mcp-servers/page paginates MCP servers with keyword filtering."""
    client.post("/mcp-servers", json=_mcp_body())

    payload = unwrap(client.get("/mcp-servers/page", params={"keyword": "FS"}))

    assert [row["name"] for row in payload["items"]] == ["fs-server"]
    assert payload["total"] == 1
