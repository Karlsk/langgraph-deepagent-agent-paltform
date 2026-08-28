"""Unit tests for the bundle export/import API and service.

Zero real network / zero real LLM: the DB layer runs on an in-memory SQLite
session injected via dependency override, and the auth dependency is
overridden with a fake User row.
"""

from collections.abc import Generator
from typing import Any

import pytest
from fastapi import FastAPI, HTTPException
from fastapi.exceptions import RequestValidationError
from fastapi.testclient import TestClient
from slowapi.errors import RateLimitExceeded
from sqlalchemy.pool import StaticPool
from sqlmodel import Session as DBSession
from sqlmodel import SQLModel, create_engine
from starlette.exceptions import HTTPException as StarletteHTTPException

from app.api.error_handlers import (
    http_exception_handler,
    rate_limit_exceeded_handler,
    validation_exception_handler,
)
from app.api.v1 import agent_assets_common as common_module
from app.api.v1 import auth as auth_module
from app.api.v1.bundle import router as bundle_router
from app.core.limiter import limiter
from app.models.agent_assets import AgentApp, McpServerConfig, SkillAsset, SubAgentConfig
from app.models.provider import ModelConfig, Provider
from app.models.user import User
from tests.conftest import unwrap

import io
import json

pytestmark = pytest.mark.unit


# ---------------------------------------------------------------------------
# Fixtures
# ---------------------------------------------------------------------------


@pytest.fixture(autouse=True)
def reset_rate_limiter() -> Generator[None, None, None]:
    """Isolate slowapi counters between tests."""
    limiter.reset()
    yield


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
def fake_user() -> User:
    """A detached User row standing in for get_current_user."""
    return User(id=1, email="test@example.com", username="tester", hashed_password="x")  # noqa: S106


@pytest.fixture
def client(db_session: DBSession, fake_user: User) -> Generator[TestClient, None, None]:
    """Minimal app wiring the bundle router with limiter + dependency overrides."""
    app = FastAPI()
    app.state.limiter = limiter
    app.add_exception_handler(RequestValidationError, validation_exception_handler)
    app.add_exception_handler(HTTPException, http_exception_handler)  # pyright: ignore[reportArgumentType]
    app.add_exception_handler(StarletteHTTPException, http_exception_handler)  # pyright: ignore[reportArgumentType]
    app.add_exception_handler(RateLimitExceeded, rate_limit_exceeded_handler)  # pyright: ignore[reportArgumentType]
    app.include_router(bundle_router)
    app.dependency_overrides[auth_module.get_current_user] = lambda: fake_user
    app.dependency_overrides[common_module.get_db_session] = lambda: db_session
    with TestClient(app) as test_client:
        yield test_client
    app.dependency_overrides.clear()


# ---------------------------------------------------------------------------
# Seed helpers
# ---------------------------------------------------------------------------


def _seed_provider(db: DBSession, name: str = "openai", **kwargs: Any) -> Provider:
    """Insert a provider row."""
    p = Provider(name=name, type="OPENAI_COMPATIBLE", base_url="https://api.openai.com/v1", **kwargs)
    db.add(p)
    db.commit()
    db.refresh(p)
    return p


def _seed_model_config(db: DBSession, provider_id: int, name: str = "gpt-4o") -> ModelConfig:
    """Insert a model config row."""
    mc = ModelConfig(provider_id=provider_id, name=name, model_id=name, context_size=128000)
    db.add(mc)
    db.commit()
    db.refresh(mc)
    return mc


def _seed_skill(db: DBSession, name: str = "pdf-export", **kwargs: Any) -> SkillAsset:
    """Insert a skill asset row."""
    s = SkillAsset(name=name, description="Export PDF", content_hash="abc123", **kwargs)
    db.add(s)
    db.commit()
    db.refresh(s)
    return s


def _seed_subagent(db: DBSession, name: str = "researcher", **kwargs: Any) -> SubAgentConfig:
    """Insert a subagent config row."""
    sa = SubAgentConfig(
        name=name,
        description="Research assistant",
        when_to_use="When research needed",
        system_prompt="You are a researcher.",
        content_hash="def456",
        **kwargs,
    )
    db.add(sa)
    db.commit()
    db.refresh(sa)
    return sa


def _seed_app(db: DBSession, name: str = "my-agent", **kwargs: Any) -> AgentApp:
    """Insert an agent app row."""
    app = AgentApp(name=name, system_prompt="You are helpful.", **kwargs)
    db.add(app)
    db.commit()
    db.refresh(app)
    return app


def _seed_mcp(db: DBSession, name: str = "echo-server", **kwargs: Any) -> McpServerConfig:
    """Insert an MCP server config row."""
    mcp = McpServerConfig(
        name=name,
        transport="stdio",
        command="python",
        args=["-m", "echo"],
        env={"SECRET": "value"},
        content_hash="ghi789",
        **kwargs,
    )
    db.add(mcp)
    db.commit()
    db.refresh(mcp)
    return mcp


def _make_bundle(
    *,
    providers: list[dict[str, Any]] | None = None,
    skills: list[dict[str, Any]] | None = None,
    subagents: list[dict[str, Any]] | None = None,
    apps: list[dict[str, Any]] | None = None,
    mcps: list[dict[str, Any]] | None = None,
) -> dict[str, Any]:
    """Build a bundle JSON dict."""
    return {
        "version": "1.0",
        "exported_at": "2026-08-28T12:00:00Z",
        "entities": {
            "providers": providers or [],
            "skills": skills or [],
            "subagents": subagents or [],
            "apps": apps or [],
            "mcps": mcps or [],
        },
    }


# ---------------------------------------------------------------------------
# Catalog tests
# ---------------------------------------------------------------------------


class TestCatalog:
    """Tests for GET /bundle/catalog."""

    def test_catalog_empty(self, client: TestClient) -> None:
        """Empty DB returns empty lists for all entity types."""
        resp = client.get("/bundle/catalog")
        data = unwrap(resp, expected_code=200)
        assert data["providers"] == []
        assert data["skills"] == []
        assert data["subagents"] == []
        assert data["apps"] == []
        assert data["mcps"] == []

    def test_catalog_returns_all_entity_types(self, client: TestClient, db_session: DBSession) -> None:
        """Catalog returns items for each entity type that has data."""
        _seed_provider(db_session, "openai")
        _seed_skill(db_session, "pdf-export")
        _seed_subagent(db_session, "researcher")
        _seed_app(db_session, "my-agent")
        _seed_mcp(db_session, "echo-server")

        resp = client.get("/bundle/catalog")
        data = unwrap(resp, expected_code=200)
        assert len(data["providers"]) == 1
        assert data["providers"][0]["name"] == "openai"
        assert len(data["skills"]) == 1
        assert data["skills"][0]["name"] == "pdf-export"
        assert len(data["subagents"]) == 1
        assert data["subagents"][0]["name"] == "researcher"
        assert len(data["apps"]) == 1
        assert data["apps"][0]["name"] == "my-agent"
        assert len(data["mcps"]) == 1
        assert data["mcps"][0]["name"] == "echo-server"


# ---------------------------------------------------------------------------
# Export tests
# ---------------------------------------------------------------------------


class TestExport:
    """Tests for POST /bundle/export."""

    def test_export_full_selection(self, client: TestClient, db_session: DBSession) -> None:
        """Selecting '*' exports all entities of that type."""
        _seed_skill(db_session, "skill-a")
        _seed_skill(db_session, "skill-b")

        resp = client.post("/bundle/export", json={"skills": "*"})
        assert resp.status_code == 200
        bundle = resp.json()
        assert bundle["version"] == "1.0"
        assert len(bundle["entities"]["skills"]) == 2
        names = {s["name"] for s in bundle["entities"]["skills"]}
        assert names == {"skill-a", "skill-b"}

    def test_export_selective(self, client: TestClient, db_session: DBSession) -> None:
        """Selecting specific names exports only those entities."""
        _seed_skill(db_session, "skill-a")
        _seed_skill(db_session, "skill-b")

        resp = client.post("/bundle/export", json={"skills": ["skill-a"]})
        assert resp.status_code == 200
        bundle = resp.json()
        assert len(bundle["entities"]["skills"]) == 1
        assert bundle["entities"]["skills"][0]["name"] == "skill-a"

    def test_export_omitted_type_not_included(self, client: TestClient, db_session: DBSession) -> None:
        """Entity types not mentioned in the request are excluded from output."""
        _seed_skill(db_session, "skill-a")
        _seed_provider(db_session, "openai")

        resp = client.post("/bundle/export", json={"skills": "*"})
        assert resp.status_code == 200
        bundle = resp.json()
        assert "skills" in bundle["entities"]
        assert "providers" not in bundle["entities"]

    def test_export_excludes_sensitive_fields(self, client: TestClient, db_session: DBSession) -> None:
        """Provider.auth_config and McpServerConfig.env are excluded from export."""
        _seed_provider(db_session, "openai")
        _seed_mcp(db_session, "echo-server")

        resp = client.post("/bundle/export", json={"providers": "*", "mcps": "*"})
        assert resp.status_code == 200
        bundle = resp.json()

        # Provider should NOT have auth_config
        provider = bundle["entities"]["providers"][0]
        assert "auth_config" not in provider

        # MCP should NOT have env
        mcp = bundle["entities"]["mcps"][0]
        assert "env" not in mcp

    def test_export_provider_includes_models(self, client: TestClient, db_session: DBSession) -> None:
        """Provider export includes its associated ModelConfig rows."""
        p = _seed_provider(db_session, "openai")
        _seed_model_config(db_session, p.id, "gpt-4o")

        resp = client.post("/bundle/export", json={"providers": "*"})
        assert resp.status_code == 200
        bundle = resp.json()
        provider = bundle["entities"]["providers"][0]
        assert len(provider["models"]) == 1
        assert provider["models"][0]["name"] == "gpt-4o"


# ---------------------------------------------------------------------------
# Preview tests
# ---------------------------------------------------------------------------


class TestPreview:
    """Tests for POST /bundle/import/preview."""

    def test_preview_marks_existing_as_skip(self, client: TestClient, db_session: DBSession) -> None:
        """Existing entities are marked as 'skip'."""
        _seed_skill(db_session, "existing-skill")

        bundle = _make_bundle(skills=[{"name": "existing-skill", "description": "test"}])
        file_content = json.dumps(bundle).encode()
        resp = client.post(
            "/bundle/import/preview",
            files={"file": ("bundle.json", io.BytesIO(file_content), "application/json")},
        )
        data = unwrap(resp, expected_code=200)
        assert data["skills"][0]["action"] == "skip"
        assert data["skills"][0]["name"] == "existing-skill"

    def test_preview_marks_new_as_create(self, client: TestClient, db_session: DBSession) -> None:
        """Non-existing entities are marked as 'create'."""
        bundle = _make_bundle(skills=[{"name": "new-skill", "description": "test"}])
        file_content = json.dumps(bundle).encode()
        resp = client.post(
            "/bundle/import/preview",
            files={"file": ("bundle.json", io.BytesIO(file_content), "application/json")},
        )
        data = unwrap(resp, expected_code=200)
        assert data["skills"][0]["action"] == "create"
        assert data["skills"][0]["name"] == "new-skill"


# ---------------------------------------------------------------------------
# Import tests
# ---------------------------------------------------------------------------


class TestImport:
    """Tests for POST /bundle/import."""

    def test_import_creates_new(self, client: TestClient, db_session: DBSession) -> None:
        """New entities are created with status 'created'."""
        bundle = _make_bundle(
            skills=[{"name": "new-skill", "description": "A new skill", "body": "# Skill", "scope": "global"}]
        )
        resp = client.post("/bundle/import", json={"bundle": bundle, "skills": "*"})
        data = unwrap(resp, expected_code=200)
        assert data["skills"][0]["status"] == "created"
        assert data["skills"][0]["name"] == "new-skill"

    def test_import_skips_existing(self, client: TestClient, db_session: DBSession) -> None:
        """Existing entities are skipped."""
        _seed_skill(db_session, "existing-skill")

        bundle = _make_bundle(
            skills=[{"name": "existing-skill", "description": "test"}]
        )
        resp = client.post("/bundle/import", json={"bundle": bundle, "skills": "*"})
        data = unwrap(resp, expected_code=200)
        assert data["skills"][0]["status"] == "skipped"

    def test_import_selective(self, client: TestClient, db_session: DBSession) -> None:
        """Only selected types are imported."""
        bundle = _make_bundle(
            skills=[{"name": "skill-a", "description": "A", "body": "# A", "scope": "global"}],
            subagents=[{"name": "sub-a", "description": "B", "when_to_use": "x", "system_prompt": "y"}],
        )
        resp = client.post("/bundle/import", json={"bundle": bundle, "skills": "*"})
        data = unwrap(resp, expected_code=200)
        assert len(data["skills"]) == 1
        assert data["skills"][0]["status"] == "created"
        # subagents not selected, should be empty
        assert data["subagents"] == []

    def test_import_creates_in_dependency_order(self, client: TestClient, db_session: DBSession) -> None:
        """Import respects dependency order: providers -> mcps -> skills -> subagents -> apps."""
        bundle = _make_bundle(
            providers=[{"name": "prov-a", "type": "OPENAI", "base_url": "https://example.com"}],
            skills=[{"name": "skill-a", "description": "A", "body": "# A", "scope": "global"}],
            subagents=[{"name": "sub-a", "description": "B", "when_to_use": "x", "system_prompt": "y"}],
            apps=[{"name": "app-a", "system_prompt": "z", "skill_names": ["skill-a"]}],
            mcps=[{"name": "mcp-a", "transport": "stdio", "command": "echo"}],
        )
        resp = client.post(
            "/bundle/import",
            json={"bundle": bundle, "providers": "*", "skills": "*", "subagents": "*", "apps": "*", "mcps": "*"},
        )
        data = unwrap(resp, expected_code=200)
        assert data["providers"][0]["status"] == "created"
        assert data["mcps"][0]["status"] == "created"
        assert data["skills"][0]["status"] == "created"
        assert data["subagents"][0]["status"] == "created"
        assert data["apps"][0]["status"] == "created"

    def test_import_provider_zeros_auth_config(self, client: TestClient, db_session: DBSession) -> None:
        """Imported providers have auth_config zeroed (not carried from export)."""
        bundle = _make_bundle(
            providers=[{"name": "prov-a", "type": "OPENAI", "base_url": "https://example.com"}]
        )
        resp = client.post("/bundle/import", json={"bundle": bundle, "providers": "*"})
        data = unwrap(resp, expected_code=200)
        assert data["providers"][0]["status"] == "created"

        # Verify in DB that auth_config is empty
        from sqlmodel import select
        prov = db_session.exec(select(Provider).where(Provider.name == "prov-a")).first()
        assert prov is not None
        assert prov.auth_config == {}

    def test_import_mcp_zeros_env(self, client: TestClient, db_session: DBSession) -> None:
        """Imported MCPs have env zeroed."""
        bundle = _make_bundle(
            mcps=[{"name": "mcp-a", "transport": "stdio", "command": "echo", "args": []}]
        )
        resp = client.post("/bundle/import", json={"bundle": bundle, "mcps": "*"})
        data = unwrap(resp, expected_code=200)
        assert data["mcps"][0]["status"] == "created"

        from sqlmodel import select
        mcp = db_session.exec(select(McpServerConfig).where(McpServerConfig.name == "mcp-a")).first()
        assert mcp is not None
        assert mcp.env == {}

    def test_import_empty_bundle(self, client: TestClient) -> None:
        """Importing an empty bundle succeeds with no results."""
        bundle = _make_bundle()
        resp = client.post("/bundle/import", json={"bundle": bundle, "skills": "*"})
        data = unwrap(resp, expected_code=200)
        assert data["skills"] == []
