"""Unit tests for the provider / model config management API.

Zero real network / zero real LLM: the DB layer runs on an in-memory SQLite
session injected via dependency override, the auth dependency is overridden
with a fake chat Session row, and the connectivity probe's AsyncOpenAI client
is replaced by an in-process fake (UP / DOWN / DEGRADED outcomes scripted).
"""

from collections.abc import Generator
from typing import Any

import pytest
from fastapi import FastAPI, HTTPException
from fastapi.exceptions import RequestValidationError
from fastapi.testclient import TestClient
from slowapi.errors import RateLimitExceeded
from sqlalchemy.exc import IntegrityError
from sqlalchemy.pool import StaticPool
from sqlmodel import SQLModel, create_engine, select
from sqlmodel import Session as DBSession
from starlette.exceptions import HTTPException as StarletteHTTPException

from app.api.error_handlers import (
    http_exception_handler,
    rate_limit_exceeded_handler,
    validation_exception_handler,
)
from app.api.v1 import agent_assets_common as common_module
from app.api.v1 import providers as providers_module
from app.api.v1 import auth as auth_module
from app.core.config import settings
from app.core.limiter import limiter
from app.models.agent_assets import AgentApp, SubAgentConfig
from app.models.provider import DEFAULT_MODEL_REF, ModelConfig, Provider, ProviderHealth
from app.models.session import Session as ChatSession
from app.schemas.providers import RemoteModelInfo
from tests.conftest import unwrap

pytestmark = pytest.mark.unit


# ---------------------------------------------------------------------------
# Fixtures
# ---------------------------------------------------------------------------


@pytest.fixture(autouse=True)
def reset_rate_limiter() -> Generator[None, None, None]:
    """Isolate slowapi counters between tests (shared in-memory storage)."""
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
def fake_chat_session() -> ChatSession:
    """A detached chat Session row standing in for get_current_session."""
    return ChatSession(id="sess-1", user_id=7, name="", username="ann", agent_app_id=None)


@pytest.fixture
def client(db_session: DBSession, fake_chat_session: ChatSession) -> Generator[TestClient, None, None]:
    """Minimal app wiring the providers router with limiter + dependency overrides."""
    app = FastAPI()
    app.state.limiter = limiter
    app.add_exception_handler(RequestValidationError, validation_exception_handler)
    app.add_exception_handler(HTTPException, http_exception_handler)  # pyright: ignore[reportArgumentType]
    app.add_exception_handler(StarletteHTTPException, http_exception_handler)  # pyright: ignore[reportArgumentType]
    app.add_exception_handler(RateLimitExceeded, rate_limit_exceeded_handler)  # pyright: ignore[reportArgumentType]
    app.include_router(providers_module.router)
    app.dependency_overrides[auth_module.get_current_session] = lambda: fake_chat_session
    app.dependency_overrides[common_module.get_db_session] = lambda: db_session
    with TestClient(app) as test_client:
        yield test_client
    app.dependency_overrides.clear()


# ---------------------------------------------------------------------------
# Helpers
# ---------------------------------------------------------------------------


def _provider_body(**overrides: Any) -> dict[str, Any]:
    body: dict[str, Any] = {
        "name": "proxy",
        "type": "OPENAI_COMPATIBLE",
        "base_url": "https://proxy.example.com/v1",
        "auth_config": {"api_key": "sk-secret-1234"},
    }
    body.update(overrides)
    return body


def _model_body(**overrides: Any) -> dict[str, Any]:
    body: dict[str, Any] = {
        "name": "m3",
        "model_id": "MiniMax-M3",
        "context_size": 204800,
        "extra_params": {"temperature": 0.7},
    }
    body.update(overrides)
    return body


def _seed_provider(db_session: DBSession, name: str = "proxy", **overrides: Any) -> Provider:
    """Seed a provider row directly (bypassing the API layer)."""
    fields: dict[str, Any] = {"name": name, "type": "OPENAI_COMPATIBLE", "auth_config": {"api_key": "sk-secret-1234"}}
    fields.update(overrides)
    provider = Provider(**fields)
    db_session.add(provider)
    db_session.commit()
    db_session.refresh(provider)
    return provider


def _seed_model(db_session: DBSession, provider: Provider, name: str = "m3", **overrides: Any) -> ModelConfig:
    """Seed a model config row directly under an existing provider."""
    fields: dict[str, Any] = {"provider_id": provider.id, "name": name, "model_id": f"model-{name}"}
    fields.update(overrides)
    model = ModelConfig(**fields)
    db_session.add(model)
    db_session.commit()
    db_session.refresh(model)
    return model


def _seed_default_pair(db_session: DBSession) -> None:
    """Seed the bootstrap-equivalent default/default pair."""
    provider = _seed_provider(db_session, name="default")
    _seed_model(db_session, provider, name="default", model_id="MiniMax-M3")


class FakeAsyncOpenAI:
    """In-process stand-in for AsyncOpenAI scripted per outcome."""

    outcome: str = "up"  # up | down

    def __init__(self, **kwargs: Any) -> None:
        """Capture construction kwargs for assertions."""
        self.kwargs = kwargs
        self.models = self
        self.closed = False

    async def list(self) -> list[Any]:
        """models.list() probe: raise for the scripted DOWN outcome."""
        if FakeAsyncOpenAI.outcome == "down":
            raise ConnectionError("fake probe failure")
        return []

    async def close(self) -> None:
        """Record the client shutdown call."""
        self.closed = True


# ---------------------------------------------------------------------------
# Provider CRUD
# ---------------------------------------------------------------------------


def test_create_provider_returns_201_masked(client: TestClient) -> None:
    """POST /providers persists the row; the api_key is never echoed back."""
    response = client.post("/providers", json=_provider_body())
    assert response.status_code == 201
    payload = unwrap(response, expected_code=201)
    assert payload["name"] == "proxy"
    assert payload["type"] == "OPENAI_COMPATIBLE"
    assert payload["api_key_masked"] == "****1234"
    assert "auth_config" not in payload
    assert "api_key" not in payload
    assert payload["created_by"] == "ann"


def test_create_provider_duplicate_name_rejected(client: TestClient) -> None:
    """A second create with the same name is rejected with 422."""
    assert client.post("/providers", json=_provider_body()).status_code == 201
    assert client.post("/providers", json=_provider_body()).status_code == 422


def test_create_provider_non_ollama_requires_api_key(client: TestClient) -> None:
    """Non-OLLAMA providers must carry auth_config.api_key (422 otherwise)."""
    response = client.post("/providers", json=_provider_body(auth_config={}))
    assert response.status_code == 422
    body = response.json()
    assert body["code"] == 422
    # The validation envelope carries the field-level detail in data.
    assert "api_key" in str(body["data"])


def test_create_provider_ollama_allows_missing_api_key(client: TestClient) -> None:
    """OLLAMA providers may be created without credentials."""
    response = client.post("/providers", json=_provider_body(name="local", type="OLLAMA", auth_config={}))
    assert response.status_code == 201
    assert unwrap(response)["api_key_masked"] == ""


def test_create_provider_invalid_type_rejected(client: TestClient) -> None:
    """Types outside the enum are rejected with 422."""
    assert client.post("/providers", json=_provider_body(type="GEMINI")).status_code == 422


def test_create_provider_commit_race_returns_422(
    client: TestClient, db_session: DBSession, monkeypatch: pytest.MonkeyPatch
) -> None:
    """A unique-name race lost at commit time degrades to 422 (never 500)."""

    def racing_commit() -> None:
        raise IntegrityError("insert", {}, Exception("duplicate key"))

    monkeypatch.setattr(db_session, "commit", racing_commit)
    response = client.post("/providers", json=_provider_body())
    assert response.status_code == 422
    assert "already exists" in response.json()["message"]


def test_list_providers_masks_every_row(client: TestClient) -> None:
    """GET /providers lists masked projections only, ordered by name."""
    client.post("/providers", json=_provider_body())
    client.post("/providers", json=_provider_body(name="backup", auth_config={"api_key": "sk-abcd"}))
    response = client.get("/providers")
    assert response.status_code == 200
    rows = unwrap(response)
    assert [row["name"] for row in rows] == ["backup", "proxy"]
    assert all("auth_config" not in row for row in rows)
    # Short keys (<= 8 chars) never leak their tail.
    assert {row["api_key_masked"] for row in rows} == {"****1234", "****"}


def test_get_provider_returns_masked_row_or_404(client: TestClient) -> None:
    """GET /providers/{name} resolves masked rows and 404s unknown ones."""
    client.post("/providers", json=_provider_body())
    found = unwrap(client.get("/providers/proxy"))
    assert "auth_config" not in found
    assert found["api_key_masked"] == "****1234"
    assert client.get("/providers/ghost").status_code == 404


def test_patch_provider_updates_fields(client: TestClient) -> None:
    """PATCH applies partial fields to the provider row."""
    unwrap(client.post("/providers", json=_provider_body()), expected_code=201)
    response = client.patch("/providers/proxy", json={"base_url": "https://other.example/v1", "enabled": False})
    assert response.status_code == 200
    payload = unwrap(response)
    assert payload["base_url"] == "https://other.example/v1"
    assert payload["enabled"] is False


def test_patch_provider_omitted_auth_config_keeps_stored_key(client: TestClient, db_session: DBSession) -> None:
    """Omitting auth_config on PATCH leaves the stored credentials untouched."""
    client.post("/providers", json=_provider_body())
    response = client.patch("/providers/proxy", json={"enabled": False})
    assert response.status_code == 200
    provider = db_session.exec(select(Provider).where(Provider.name == "proxy")).first()
    assert provider is not None and provider.auth_config == {"api_key": "sk-secret-1234"}

    rotated = client.patch("/providers/proxy", json={"auth_config": {"api_key": "sk-rotated-9999"}})
    assert rotated.status_code == 200
    assert unwrap(rotated)["api_key_masked"] == "****9999"


def test_patch_provider_rejects_name_change_and_empty_payload(client: TestClient) -> None:
    """PATCH with the immutable name field or an empty body is rejected (422)."""
    client.post("/providers", json=_provider_body())
    assert client.patch("/providers/proxy", json={"name": "other"}).status_code == 422
    assert client.patch("/providers/proxy", json={}).status_code == 422


def test_patch_provider_unknown_name_404(client: TestClient) -> None:
    """PATCH on a missing provider returns 404."""
    assert client.patch("/providers/ghost", json={"enabled": False}).status_code == 404


@pytest.mark.parametrize("field", ["type", "base_url", "enabled", "auth_config"])
def test_patch_provider_explicit_null_on_required_field_rejected(client: TestClient, field: str) -> None:
    """Explicit JSON null on NOT NULL fields is rejected with 422 (omit instead)."""
    client.post("/providers", json=_provider_body())
    response = client.patch("/providers/proxy", json={field: None})
    assert response.status_code == 422
    assert "null is not allowed" in response.json()["message"]


def test_patch_provider_clearing_key_on_openai_rejected(client: TestClient) -> None:
    """Re-validation rejects an auth_config without api_key for non-OLLAMA rows."""
    client.post("/providers", json=_provider_body())
    response = client.patch("/providers/proxy", json={"auth_config": {}})
    assert response.status_code == 422
    assert "api_key" in response.json()["message"]


def test_delete_provider_soft_deletes_and_cascades(client: TestClient, db_session: DBSession) -> None:
    """DELETE soft-deletes the provider + its models and drops the health row."""
    provider = _seed_provider(db_session)
    _seed_model(db_session, provider)
    db_session.add(ProviderHealth(provider_id=provider.id, status="UP"))
    db_session.commit()

    assert client.delete("/providers/proxy").status_code == 200
    assert client.get("/providers/proxy").status_code == 404
    assert client.delete("/providers/proxy").status_code == 404

    db_session.expire_all()
    stored = db_session.exec(select(Provider).where(Provider.name == "proxy")).first()
    assert stored is not None and stored.deleted is True
    models = db_session.exec(select(ModelConfig).where(ModelConfig.provider_id == provider.id)).all()
    assert models and all(model.deleted for model in models)
    assert db_session.exec(select(ProviderHealth).where(ProviderHealth.provider_id == provider.id)).first() is None


def test_delete_default_provider_forbidden(client: TestClient, db_session: DBSession) -> None:
    """The bootstrap-seeded default provider can never be deleted (422)."""
    _seed_default_pair(db_session)
    assert client.delete("/providers/default").status_code == 422
    assert client.get("/providers/default").status_code == 200


def test_delete_provider_referenced_by_app_rejected(client: TestClient, db_session: DBSession) -> None:
    """A provider with a model referenced by an AgentApp is delete-protected."""
    provider = _seed_provider(db_session)
    _seed_model(db_session, provider)
    db_session.add(AgentApp(name="support-app", system_prompt="x", model="proxy/m3"))
    db_session.commit()

    response = client.delete("/providers/proxy")
    assert response.status_code == 422
    body = response.json()
    assert body["code"] == 422
    assert "support-app" in body["message"]
    assert body["data"] is None


def test_delete_provider_referenced_by_subagent_rejected(client: TestClient, db_session: DBSession) -> None:
    """A provider with a model referenced by a SubAgentConfig is delete-protected."""
    provider = _seed_provider(db_session)
    _seed_model(db_session, provider)
    db_session.add(
        SubAgentConfig(
            name="researcher", description="d", when_to_use="w", system_prompt="s", content_hash="h", model="proxy/m3"
        )
    )
    db_session.commit()

    response = client.delete("/providers/proxy")
    assert response.status_code == 422
    assert "researcher" in response.json()["message"]


# ---------------------------------------------------------------------------
# Pagination
# ---------------------------------------------------------------------------


def test_list_providers_page_enriches_model_count_and_health(client: TestClient, db_session: DBSession) -> None:
    """GET /providers/page joins the enabled model count and health snapshot."""
    provider = _seed_provider(db_session)
    _seed_model(db_session, provider, name="m3")
    _seed_model(db_session, provider, name="legacy", enabled=False)
    db_session.add(ProviderHealth(provider_id=provider.id, status="UP", latency_ms=12))
    db_session.commit()

    payload = unwrap(client.get("/providers/page"))

    assert payload["total"] == 1
    assert payload["page"] == 1
    assert payload["pageSize"] == 10
    row = payload["items"][0]
    assert row["provider"]["name"] == "proxy"
    assert row["provider"]["api_key_masked"] == "****1234"
    assert row["model_count"] == 1  # disabled model excluded
    assert row["health"]["status"] == "UP"
    assert row["health"]["latency_ms"] == 12


def test_list_providers_page_defaults_health_to_unknown(client: TestClient) -> None:
    """Never-probed providers surface the UNKNOWN health default."""
    client.post("/providers", json=_provider_body())
    row = unwrap(client.get("/providers/page"))["items"][0]
    assert row["model_count"] == 0
    assert row["health"]["status"] == "UNKNOWN"
    assert row["health"]["last_check_at"] is None


def test_list_providers_page_keyword_filters_case_insensitively(client: TestClient) -> None:
    """Keyword matches name via case-insensitive substring."""
    client.post("/providers", json=_provider_body())
    client.post("/providers", json=_provider_body(name="backup", auth_config={"api_key": "sk-999999999"}))
    payload = unwrap(client.get("/providers/page", params={"keyword": "PROX"}))
    assert [row["provider"]["name"] for row in payload["items"]] == ["proxy"]
    assert payload["total"] == 1


def test_list_providers_page_rejects_out_of_bounds_params(client: TestClient) -> None:
    """Values below page 1 or above pageSize 100 are rejected with 422."""
    assert client.get("/providers/page", params={"page": 0}).status_code == 422
    assert client.get("/providers/page", params={"pageSize": 101}).status_code == 422


# ---------------------------------------------------------------------------
# Masking contract
# ---------------------------------------------------------------------------


@pytest.mark.parametrize(
    "api_key,expected", [("a", "****"), ("abcd", "****"), ("12345678", "****"), ("123456789", "****6789")]
)
def test_mask_api_key_short_key_boundaries(api_key: str, expected: str) -> None:
    """Keys of length <= 8 mask fully; length 9 keeps the last four chars."""
    assert providers_module._mask_api_key(api_key) == expected  # noqa: SLF001 — unit under test


# ---------------------------------------------------------------------------
# On-demand connectivity probe
# ---------------------------------------------------------------------------


def test_provider_test_up_writes_health(
    client: TestClient, db_session: DBSession, monkeypatch: pytest.MonkeyPatch
) -> None:
    """A successful probe records UP, resets fail_count and stamps success_at."""
    FakeAsyncOpenAI.outcome = "up"
    monkeypatch.setattr(providers_module, "AsyncOpenAI", FakeAsyncOpenAI)
    provider = _seed_provider(db_session)
    db_session.add(ProviderHealth(provider_id=provider.id, status="DOWN", fail_count=3))
    db_session.commit()

    payload = unwrap(client.post("/providers/proxy/test"))

    assert payload["status"] == "UP"
    assert payload["error_message"] is None
    assert payload["latency_ms"] >= 0
    db_session.expire_all()
    health = db_session.exec(select(ProviderHealth).where(ProviderHealth.provider_id == provider.id)).first()
    assert health is not None
    assert health.status == "UP"
    assert health.fail_count == 0
    assert health.last_check_at is not None
    assert health.last_success_at is not None


def test_provider_test_down_writes_health(
    client: TestClient, db_session: DBSession, monkeypatch: pytest.MonkeyPatch
) -> None:
    """A failed probe records DOWN with the error message and bumps fail_count."""
    FakeAsyncOpenAI.outcome = "down"
    monkeypatch.setattr(providers_module, "AsyncOpenAI", FakeAsyncOpenAI)
    provider = _seed_provider(db_session)

    payload = unwrap(client.post("/providers/proxy/test"))

    assert payload["status"] == "DOWN"
    assert "fake probe failure" in (payload["error_message"] or "")
    db_session.expire_all()
    health = db_session.exec(select(ProviderHealth).where(ProviderHealth.provider_id == provider.id)).first()
    assert health is not None
    assert health.status == "DOWN"
    assert health.fail_count == 1
    assert health.last_success_at is None

    # Consecutive failures keep incrementing.
    unwrap(client.post("/providers/proxy/test"))
    db_session.expire_all()
    health = db_session.exec(select(ProviderHealth).where(ProviderHealth.provider_id == provider.id)).first()
    assert health is not None and health.fail_count == 2


def test_provider_test_slow_success_degraded(
    client: TestClient, db_session: DBSession, monkeypatch: pytest.MonkeyPatch
) -> None:
    """A success exceeding the latency threshold degrades to DEGRADED."""
    FakeAsyncOpenAI.outcome = "up"
    monkeypatch.setattr(providers_module, "AsyncOpenAI", FakeAsyncOpenAI)
    monkeypatch.setattr(settings, "PROVIDER_HEALTH_DEGRADED_MS", -1)
    _seed_provider(db_session)

    payload = unwrap(client.post("/providers/proxy/test"))

    assert payload["status"] == "DEGRADED"


def test_provider_test_missing_404(client: TestClient) -> None:
    """Probing a nonexistent provider returns 404."""
    assert client.post("/providers/ghost/test").status_code == 404


def test_provider_test_disabled_422(client: TestClient, db_session: DBSession) -> None:
    """Probing a disabled provider is rejected with 422."""
    _seed_provider(db_session, enabled=False)
    assert client.post("/providers/proxy/test").status_code == 422


# ---------------------------------------------------------------------------
# Discover upstream models (POST /providers/{name}/discover-models)
# ---------------------------------------------------------------------------


def test_discover_models_returns_envelope_with_rows(
    client: TestClient, db_session: DBSession, monkeypatch: pytest.MonkeyPatch
) -> None:
    """A successful discovery returns RemoteModelInfo rows inside the envelope."""
    _seed_provider(db_session)
    canned = [
        RemoteModelInfo(id="deepseek-v4-flash", owned_by="deepseek", raw={"id": "deepseek-v4-flash"}),
        RemoteModelInfo(id="deepseek-v4-pro", owned_by="deepseek", raw={"id": "deepseek-v4-pro"}),
    ]

    async def fake_discover(_provider: Any) -> list[RemoteModelInfo]:
        """Stand-in for the real service: return canned rows without any network call."""
        return canned

    monkeypatch.setattr(providers_module, "discover_remote_models", fake_discover)

    response = client.post("/providers/proxy/discover-models")
    assert response.status_code == 200
    body = response.json()
    assert body["code"] == 200
    rows = body["data"]
    assert [r["id"] for r in rows] == ["deepseek-v4-flash", "deepseek-v4-pro"]
    assert all(r["owned_by"] == "deepseek" for r in rows)
    assert all(r["raw"]["id"].startswith("deepseek-v4") for r in rows)


def test_discover_models_unknown_provider_404(client: TestClient) -> None:
    """Discovering models of a missing provider returns 404."""
    assert client.post("/providers/ghost/discover-models").status_code == 404


def test_discover_models_anthropic_returns_422(
    client: TestClient, db_session: DBSession, monkeypatch: pytest.MonkeyPatch
) -> None:
    """An ANTHROPIC provider is rejected with 422 (service raises ValueError)."""

    async def fake_discover(_provider: Any) -> list[RemoteModelInfo]:
        """Simulate the service's ANTHROPIC guard without touching the network."""
        raise ValueError("provider type 'ANTHROPIC' does not support auto-discovery")

    monkeypatch.setattr(providers_module, "discover_remote_models", fake_discover)
    _seed_provider(db_session, type="ANTHROPIC")

    response = client.post("/providers/proxy/discover-models")
    assert response.status_code == 422
    assert "ANTHROPIC" in response.json()["message"]


def test_discover_models_upstream_failure_returns_502(
    client: TestClient, db_session: DBSession, monkeypatch: pytest.MonkeyPatch
) -> None:
    """An upstream ConnectionError surfaces as 502 (bad gateway)."""

    async def fake_discover(_provider: Any) -> list[RemoteModelInfo]:
        """Simulate an upstream failure so the endpoint maps it to 502."""
        raise ConnectionError("fake upstream DNS failure")

    monkeypatch.setattr(providers_module, "discover_remote_models", fake_discover)
    _seed_provider(db_session)

    response = client.post("/providers/proxy/discover-models")
    assert response.status_code == 502
    assert "upstream call failed" in response.json()["message"]
    assert "DNS failure" in response.json()["message"]


def test_discover_models_handles_disabled_provider(
    client: TestClient, db_session: DBSession, monkeypatch: pytest.MonkeyPatch
) -> None:
    """Discovery does not require the provider to be enabled (test endpoint does)."""
    canned = [RemoteModelInfo(id="m-1", owned_by="vendor", raw={"id": "m-1"})]

    async def fake_discover(_provider: Any) -> list[RemoteModelInfo]:
        """Return one row so the test verifies the disabled flag is not enforced."""
        return canned

    monkeypatch.setattr(providers_module, "discover_remote_models", fake_discover)
    _seed_provider(db_session, enabled=False)

    response = client.post("/providers/proxy/discover-models")
    assert response.status_code == 200
    assert [r["id"] for r in response.json()["data"]] == ["m-1"]


# ---------------------------------------------------------------------------
# Model configs (nested under providers)
# ---------------------------------------------------------------------------


def test_list_models_of_provider_ordered_with_ref(client: TestClient) -> None:
    """GET /providers/{name}/models lists rows ordered by name with refs."""
    client.post("/providers", json=_provider_body())
    client.post("/providers/proxy/models", json=_model_body(name="zeta"))
    client.post("/providers/proxy/models", json=_model_body(name="alpha", model_id="other-id"))

    rows = unwrap(client.get("/providers/proxy/models"))

    assert [row["name"] for row in rows] == ["alpha", "zeta"]
    assert [row["ref"] for row in rows] == ["proxy/alpha", "proxy/zeta"]
    assert all(row["provider_name"] == "proxy" for row in rows)


def test_list_models_unknown_provider_404(client: TestClient) -> None:
    """Listing models of a missing provider returns 404."""
    assert client.get("/providers/ghost/models").status_code == 404


def test_create_model_returns_201_with_fields(client: TestClient) -> None:
    """POST /providers/{name}/models persists the row with ref + audit fields."""
    client.post("/providers", json=_provider_body())
    response = client.post("/providers/proxy/models", json=_model_body())
    assert response.status_code == 201
    payload = unwrap(response, expected_code=201)
    assert payload["ref"] == "proxy/m3"
    assert payload["model_id"] == "MiniMax-M3"
    assert payload["context_size"] == 204800
    assert payload["extra_params"] == {"temperature": 0.7}
    assert payload["created_by"] == "ann"


def test_create_model_duplicate_name_rejected(client: TestClient) -> None:
    """A duplicate (provider, name) pair degrades to 422 at commit time."""
    client.post("/providers", json=_provider_body())
    assert client.post("/providers/proxy/models", json=_model_body()).status_code == 201
    duplicate = client.post("/providers/proxy/models", json=_model_body(model_id="other-model-id"))
    assert duplicate.status_code == 422
    assert "already exists" in duplicate.json()["message"]


def test_create_model_duplicate_model_id_rejected(client: TestClient) -> None:
    """A duplicate (provider, model_id) pair is rejected with 422."""
    client.post("/providers", json=_provider_body())
    assert client.post("/providers/proxy/models", json=_model_body()).status_code == 201
    duplicate = client.post("/providers/proxy/models", json=_model_body(name="alias"))
    assert duplicate.status_code == 422


def test_create_model_unknown_provider_404(client: TestClient) -> None:
    """Creating a model under a missing provider returns 404."""
    assert client.post("/providers/ghost/models", json=_model_body()).status_code == 404


def test_create_model_name_with_slash_rejected(client: TestClient) -> None:
    """Model names must never contain '/' (reference separator)."""
    client.post("/providers", json=_provider_body())
    assert client.post("/providers/proxy/models", json=_model_body(name="a/b")).status_code == 422


def test_patch_model_updates_fields(client: TestClient) -> None:
    """PATCH applies partial fields to the model config row."""
    client.post("/providers", json=_provider_body())
    client.post("/providers/proxy/models", json=_model_body())
    response = client.patch("/providers/proxy/models/m3", json={"context_size": 4096, "enabled": False})
    assert response.status_code == 200
    payload = unwrap(response)
    assert payload["context_size"] == 4096
    assert payload["enabled"] is False


def test_patch_model_rejects_name_change_empty_payload_and_null(client: TestClient) -> None:
    """PATCH rejects the immutable name, empty bodies and null on NOT NULL fields."""
    client.post("/providers", json=_provider_body())
    client.post("/providers/proxy/models", json=_model_body())
    assert client.patch("/providers/proxy/models/m3", json={"name": "other"}).status_code == 422
    assert client.patch("/providers/proxy/models/m3", json={}).status_code == 422
    assert client.patch("/providers/proxy/models/m3", json={"model_id": None}).status_code == 422


def test_patch_model_missing_404(client: TestClient) -> None:
    """PATCH on a missing model returns 404."""
    client.post("/providers", json=_provider_body())
    assert client.patch("/providers/proxy/models/ghost", json={"enabled": False}).status_code == 404


def test_delete_model_soft_deletes(client: TestClient, db_session: DBSession) -> None:
    """DELETE soft-deletes the model row; subsequent reads 404."""
    client.post("/providers", json=_provider_body())
    client.post("/providers/proxy/models", json=_model_body())
    assert client.delete("/providers/proxy/models/m3").status_code == 200
    assert client.get("/providers/proxy/models").json()["data"] == []

    db_session.expire_all()
    stored = db_session.exec(select(ModelConfig).where(ModelConfig.name == "m3")).first()
    assert stored is not None and stored.deleted is True


def test_delete_default_model_forbidden(client: TestClient, db_session: DBSession) -> None:
    """The bootstrap-seeded default/default pair can never be deleted (422)."""
    _seed_default_pair(db_session)
    response = client.delete("/providers/default/models/default")
    assert response.status_code == 422
    assert DEFAULT_MODEL_REF in response.json()["message"]


def test_delete_model_referenced_by_app_rejected(client: TestClient, db_session: DBSession) -> None:
    """A model referenced by an AgentApp.model field is delete-protected (422)."""
    provider = _seed_provider(db_session)
    _seed_model(db_session, provider)
    db_session.add(AgentApp(name="support-app", system_prompt="x", model="proxy/m3"))
    db_session.commit()

    response = client.delete("/providers/proxy/models/m3")
    assert response.status_code == 422
    body = response.json()
    assert body["code"] == 422
    assert "support-app" in body["message"]
    assert body["data"] is None


def test_delete_model_referenced_by_subagent_rejected(client: TestClient, db_session: DBSession) -> None:
    """A model referenced by a SubAgentConfig.model field is delete-protected."""
    provider = _seed_provider(db_session)
    _seed_model(db_session, provider)
    db_session.add(
        SubAgentConfig(
            name="researcher", description="d", when_to_use="w", system_prompt="s", content_hash="h", model="proxy/m3"
        )
    )
    db_session.commit()

    response = client.delete("/providers/proxy/models/m3")
    assert response.status_code == 422
    assert "researcher" in response.json()["message"]


# ---------------------------------------------------------------------------
# Hard-delete escape hatch (TC1-3)
# ---------------------------------------------------------------------------

HARD_DELETE_HEADER = {"X-Confirm-Hard-Delete": "true"}


def test_delete_provider_hard_query_param_physically_removes_row(client: TestClient, db_session: DBSession) -> None:
    """DELETE /providers/{name}?hard=true + confirm header physically removes the row."""
    _seed_provider(db_session)
    provider_id = db_session.exec(select(Provider).where(Provider.name == "proxy")).first().id

    response = client.delete("/providers/proxy?hard=true", headers=HARD_DELETE_HEADER)
    assert response.status_code == 200

    db_session.expire_all()
    stored = db_session.exec(select(Provider).where(Provider.name == "proxy")).first()
    assert stored is None
    assert db_session.get(Provider, provider_id) is None


def test_delete_provider_hard_cascade_purges_models_and_health(client: TestClient, db_session: DBSession) -> None:
    """Hard-delete cascades through ModelConfig rows and drops the ProviderHealth row."""
    provider = _seed_provider(db_session)
    _seed_model(db_session, provider)
    db_session.add(ProviderHealth(provider_id=provider.id, status="UP"))
    db_session.commit()

    response = client.delete("/providers/proxy?hard=true", headers=HARD_DELETE_HEADER)
    assert response.status_code == 200

    db_session.expire_all()
    assert db_session.exec(select(Provider).where(Provider.name == "proxy")).first() is None
    assert db_session.exec(select(ModelConfig).where(ModelConfig.provider_id == provider.id)).first() is None
    assert db_session.exec(select(ProviderHealth).where(ProviderHealth.provider_id == provider.id)).first() is None


def test_delete_provider_hard_unblocks_same_name_recreate(client: TestClient, db_session: DBSession) -> None:
    """After hard delete the same name can be recreated (closes the tombstone trap)."""
    _seed_provider(db_session)
    first = client.delete("/providers/proxy?hard=true", headers=HARD_DELETE_HEADER)
    assert first.status_code == 200

    recreate = client.post("/providers", json=_provider_body())
    assert recreate.status_code == 201
    assert unwrap(recreate, expected_code=201)["name"] == "proxy"


def test_delete_provider_hard_requires_confirm_header(client: TestClient, db_session: DBSession) -> None:
    """hard=true without X-Confirm-Hard-Delete header is rejected with 422."""
    _seed_provider(db_session)
    response = client.delete("/providers/proxy?hard=true")
    assert response.status_code == 422
    assert "X-Confirm-Hard-Delete" in response.json()["message"]

    db_session.expire_all()
    assert db_session.exec(select(Provider).where(Provider.name == "proxy")).first() is not None


def test_delete_provider_hard_invalid_header_rejected(client: TestClient, db_session: DBSession) -> None:
    """hard=true with X-Confirm-Hard-Delete != 'true' is rejected with 422."""
    _seed_provider(db_session)
    response = client.delete("/providers/proxy?hard=true", headers={"X-Confirm-Hard-Delete": "false"})
    assert response.status_code == 422
    assert "X-Confirm-Hard-Delete" in response.json()["message"]

    db_session.expire_all()
    assert db_session.exec(select(Provider).where(Provider.name == "proxy")).first() is not None


def test_delete_provider_hard_default_still_forbidden(client: TestClient, db_session: DBSession) -> None:
    """Even the hard path is blocked on the bootstrap-seeded default provider."""
    _seed_default_pair(db_session)
    response = client.delete("/providers/default?hard=true", headers=HARD_DELETE_HEADER)
    assert response.status_code == 422
    assert DEFAULT_MODEL_REF in response.json()["message"] or "default" in response.json()["message"]


def test_delete_provider_hard_referenced_by_app_still_forbidden(client: TestClient, db_session: DBSession) -> None:
    """Reference protection fires before the hard branch (no escape hatch for references)."""
    provider = _seed_provider(db_session)
    _seed_model(db_session, provider)
    db_session.add(AgentApp(name="support-app", system_prompt="x", model="proxy/m3"))
    db_session.commit()

    response = client.delete("/providers/proxy?hard=true", headers=HARD_DELETE_HEADER)
    assert response.status_code == 422
    assert "support-app" in response.json()["message"]

    db_session.expire_all()
    assert db_session.exec(select(Provider).where(Provider.name == "proxy")).first() is not None


def test_delete_provider_soft_with_no_param_unchanged(client: TestClient, db_session: DBSession) -> None:
    """Backward compat: DELETE without ?hard keeps the soft-delete contract."""
    _seed_provider(db_session)
    response = client.delete("/providers/proxy")
    assert response.status_code == 200

    db_session.expire_all()
    stored = db_session.exec(select(Provider).where(Provider.name == "proxy")).first()
    assert stored is not None and stored.deleted is True


def test_delete_provider_hard_emits_audit_log(
    client: TestClient, db_session: DBSession, caplog: pytest.LogCaptureFixture
) -> None:
    """Hard-delete emits the audit-only provider_hard_deleted warning event."""
    _seed_provider(db_session)
    import logging

    with caplog.at_level(logging.INFO, logger="app"):
        response = client.delete("/providers/proxy?hard=true", headers=HARD_DELETE_HEADER)
    assert response.status_code == 200
    event_names = [record.message for record in caplog.records]
    assert any("provider_hard_deleted" in str(name) for name in event_names)


def test_delete_provider_hard_404_when_missing(client: TestClient) -> None:
    """hard=true on a missing provider still returns 404."""
    response = client.delete("/providers/ghost?hard=true", headers=HARD_DELETE_HEADER)
    assert response.status_code == 404


def test_hard_delete_provider_helper_signature_frozen() -> None:
    """Contract: hard_delete_provider accepts (db, provider) and returns a counts dict."""
    import inspect
    import typing

    from app.services.llm.llm_store import hard_delete_provider

    sig = inspect.signature(hard_delete_provider)
    params = list(sig.parameters.keys())
    assert params == ["db", "provider"]
    # Accept either plain ``dict`` or parameterized ``dict[K, V]`` annotations.
    return_annotation = sig.return_annotation
    origin = typing.get_origin(return_annotation)
    assert origin is dict or return_annotation is dict, f"return must be dict-like, got {return_annotation}"


def test_delete_model_hard_query_param_physically_removes_row(client: TestClient, db_session: DBSession) -> None:
    """DELETE /providers/{name}/models/{model}?hard=true + header purges the row."""
    provider = _seed_provider(db_session)
    _seed_model(db_session, provider, name="m3")
    _seed_model(db_session, provider, name="zeta")
    response = client.delete("/providers/proxy/models/m3?hard=true", headers=HARD_DELETE_HEADER)
    assert response.status_code == 200

    db_session.expire_all()
    assert db_session.exec(select(ModelConfig).where(ModelConfig.name == "m3")).first() is None
    # Sibling model is preserved.
    assert db_session.exec(select(ModelConfig).where(ModelConfig.name == "zeta")).first() is not None


def test_delete_model_hard_requires_confirm_header(client: TestClient, db_session: DBSession) -> None:
    """hard=true without header is rejected (model is not purged)."""
    provider = _seed_provider(db_session)
    _seed_model(db_session, provider)
    response = client.delete("/providers/proxy/models/m3?hard=true")
    assert response.status_code == 422
    assert "X-Confirm-Hard-Delete" in response.json()["message"]

    db_session.expire_all()
    assert db_session.exec(select(ModelConfig).where(ModelConfig.name == "m3")).first() is not None


def test_delete_model_hard_default_still_forbidden(client: TestClient, db_session: DBSession) -> None:
    """Even the hard path is blocked on the bootstrap-seeded default model."""
    _seed_default_pair(db_session)
    response = client.delete("/providers/default/models/default?hard=true", headers=HARD_DELETE_HEADER)
    assert response.status_code == 422
    assert DEFAULT_MODEL_REF in response.json()["message"]


def test_delete_model_hard_referenced_still_forbidden(client: TestClient, db_session: DBSession) -> None:
    """Reference protection fires before the hard branch (no escape hatch)."""
    provider = _seed_provider(db_session)
    _seed_model(db_session, provider)
    db_session.add(AgentApp(name="support-app", system_prompt="x", model="proxy/m3"))
    db_session.commit()

    response = client.delete("/providers/proxy/models/m3?hard=true", headers=HARD_DELETE_HEADER)
    assert response.status_code == 422
    assert "support-app" in response.json()["message"]

    db_session.expire_all()
    assert db_session.exec(select(ModelConfig).where(ModelConfig.name == "m3")).first() is not None


def test_delete_model_hard_unblocks_same_name_recreate(client: TestClient, db_session: DBSession) -> None:
    """After hard-delete a model of the same (provider, name) can be recreated."""
    provider = _seed_provider(db_session)
    _seed_model(db_session, provider)
    first = client.delete("/providers/proxy/models/m3?hard=true", headers=HARD_DELETE_HEADER)
    assert first.status_code == 200

    recreate = client.post("/providers/proxy/models", json=_model_body(model_id="new-id"))
    assert recreate.status_code == 201


def test_delete_model_hard_emits_audit_log(
    client: TestClient, db_session: DBSession, caplog: pytest.LogCaptureFixture
) -> None:
    """Hard-delete on a model emits the audit-only model_config_hard_deleted warning event."""
    provider = _seed_provider(db_session)
    _seed_model(db_session, provider)
    import logging

    with caplog.at_level(logging.INFO, logger="app"):
        response = client.delete("/providers/proxy/models/m3?hard=true", headers=HARD_DELETE_HEADER)
    assert response.status_code == 200
    event_names = [record.message for record in caplog.records]
    assert any("model_config_hard_deleted" in str(name) for name in event_names)


# ---------------------------------------------------------------------------
# Trash endpoints (TC7-9) — list soft-deleted providers and their models
# ---------------------------------------------------------------------------


def test_list_deleted_providers_empty(client: TestClient) -> None:
    """No soft-deleted providers → data is [] (never 404 for an empty list)."""
    response = client.get("/providers/deleted")
    assert response.status_code == 200
    assert unwrap(response) == []


def test_list_deleted_providers_returns_only_soft_deleted(client: TestClient, db_session: DBSession) -> None:
    """Trash list filters out live rows and exposes the masked projection of soft-deleted rows."""
    _seed_provider(db_session, name="proxy")
    _seed_provider(db_session, name="backup")
    assert client.delete("/providers/proxy").status_code == 200

    rows = unwrap(client.get("/providers/deleted"))
    assert [row["name"] for row in rows] == ["proxy"]
    assert rows[0]["deleted"] is True


def test_list_deleted_providers_ordered_by_updated_desc(client: TestClient, db_session: DBSession) -> None:
    """Newer soft-delete comes first (operators triage the freshest tombstone first)."""
    _seed_provider(db_session, name="first")
    _seed_provider(db_session, name="second")
    assert client.delete("/providers/first").status_code == 200
    assert client.delete("/providers/second").status_code == 200

    rows = unwrap(client.get("/providers/deleted"))
    assert [row["name"] for row in rows] == ["second", "first"]


def test_list_deleted_providers_masks_auth_config(client: TestClient, db_session: DBSession) -> None:
    """Trash rows never leak raw auth secrets (extra security on soft-deleted rows)."""
    _seed_provider(db_session)
    assert client.delete("/providers/proxy").status_code == 200

    rows = unwrap(client.get("/providers/deleted"))
    assert len(rows) == 1
    assert "auth_config" not in rows[0]
    assert rows[0]["api_key_masked"] == "****1234"


def test_get_deleted_provider_by_name_returns_soft_deleted(client: TestClient, db_session: DBSession) -> None:
    """GET /providers/deleted/{name} returns the soft-deleted row (masked projection)."""
    _seed_provider(db_session)
    assert client.delete("/providers/proxy").status_code == 200

    payload = unwrap(client.get("/providers/deleted/proxy"))
    assert payload["name"] == "proxy"
    assert payload["deleted"] is True
    assert "auth_config" not in payload


def test_get_deleted_provider_by_name_returns_404_when_active(client: TestClient) -> None:
    """Active rows are not visible to the trash view (consistency with trash list)."""
    response = client.get("/providers/deleted/proxy")
    assert response.status_code == 404


def test_get_deleted_provider_by_name_returns_404_when_unknown(client: TestClient) -> None:
    """Unknown names → 404 (no provider row exists at all)."""
    response = client.get("/providers/deleted/ghost")
    assert response.status_code == 404


def test_list_deleted_models_under_soft_deleted_provider(client: TestClient, db_session: DBSession) -> None:
    """Even a soft-deleted provider's models are still readable via the trash endpoint."""
    provider = _seed_provider(db_session)
    _seed_model(db_session, provider, name="m3")
    _seed_model(db_session, provider, name="zeta")
    assert client.delete("/providers/proxy").status_code == 200

    rows = unwrap(client.get("/providers/deleted/proxy/models"))
    names = sorted(row["name"] for row in rows)
    assert names == ["m3", "zeta"]
    assert all(row["deleted"] is True for row in rows)


def test_list_deleted_models_returns_404_when_provider_active(client: TestClient) -> None:
    """Trash models view is only for tombstoned providers — active provider 404s."""
    response = client.get("/providers/deleted/proxy/models")
    assert response.status_code == 404


def test_trash_endpoints_emit_info_log(
    client: TestClient, db_session: DBSession, caplog: pytest.LogCaptureFixture
) -> None:
    """All three trash endpoints emit an info-level event for observability."""
    _seed_provider(db_session)
    assert client.delete("/providers/proxy").status_code == 200
    import logging

    with caplog.at_level(logging.INFO, logger="app"):
        assert client.get("/providers/deleted").status_code == 200
        assert client.get("/providers/deleted/proxy").status_code == 200
        assert client.get("/providers/deleted/proxy/models").status_code == 200

    event_names = [record.message for record in caplog.records]
    assert any("provider_trash_listed" in str(name) for name in event_names)
    assert any("provider_trash_read" in str(name) for name in event_names)
    assert any("model_trash_listed" in str(name) for name in event_names)
