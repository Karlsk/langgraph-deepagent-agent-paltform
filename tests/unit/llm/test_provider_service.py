"""Unit tests for app.services.llm.provider_service (workflow-llm-provider-integration Stage C).

The facade is the only sanctioned entry point for cross-module callers that have
no request scope. Zero real DB access: the session helper and the two store
functions are patched at the provider_service import location.
"""

from __future__ import annotations

import inspect
from contextlib import contextmanager
from typing import Any

import pytest

from app.services.llm import provider_service

pytestmark = pytest.mark.unit


class _FakeProvider:
    """Stand-in for the Provider ORM row."""

    def __init__(self, name: str = "acme", base_url: str = "https://api.acme.test/v1") -> None:
        """Store the two fields build_chat_model reads."""
        self.name = name
        self.base_url = base_url
        self.auth_config = {"api_key": "sk-facade-test-key"}  # noqa: S105 — dummy sentinel


class _FakeModel:
    """Stand-in for the ModelConfig ORM row, with a pydantic-like model_copy."""

    def __init__(self, name: str = "gpt-4o", model_id: str = "gpt-4o-2024", extra_params: dict[str, Any] | None = None) -> None:
        """Store the fields build_chat_model reads."""
        self.name = name
        self.model_id = model_id
        self.extra_params = extra_params if extra_params is not None else {}

    def model_copy(self, update: dict[str, Any]) -> "_FakeModel":
        """Return a detached copy with the given fields replaced."""
        clone = _FakeModel(self.name, self.model_id, dict(self.extra_params))
        for key, value in update.items():
            setattr(clone, key, value)
        return clone


@contextmanager
def _fake_session():
    """Yield a sentinel instead of a real SQLModel session."""
    yield object()


@pytest.fixture
def patched(monkeypatch: pytest.MonkeyPatch) -> dict[str, Any]:
    """Patch the session helper and both store functions, recording their calls."""
    recorded: dict[str, Any] = {}

    def fake_load(session: Any, reference: str | None) -> tuple[_FakeProvider, _FakeModel]:
        recorded["load_session"] = session
        recorded["load_ref"] = reference
        provider = _FakeProvider()
        model = _FakeModel(extra_params={"temperature": 0.9, "max_tokens": 1000})
        recorded["provider"] = provider
        recorded["model"] = model
        return provider, model

    def fake_build(provider: Any, model: Any) -> str:
        recorded["build_provider"] = provider
        recorded["build_model"] = model
        return "fake-client"

    monkeypatch.setattr(provider_service, "_session", _fake_session)
    monkeypatch.setattr(provider_service, "load_model_config", fake_load)
    monkeypatch.setattr(provider_service, "build_chat_model", fake_build)
    return recorded


def test_resolve_chat_model_delegates_to_store(patched: dict[str, Any]) -> None:
    """The facade owns resolution: it calls load_model_config then build_chat_model."""
    client = provider_service.resolve_chat_model("acme/gpt-4o")

    assert client == "fake-client"
    assert patched["load_ref"] == "acme/gpt-4o"
    assert patched["build_provider"] is patched["provider"]


def test_node_overrides_win_over_provider_extra_params(patched: dict[str, Any]) -> None:
    """S20: node-level config takes precedence over ModelConfig.extra_params."""
    provider_service.resolve_chat_model("acme/gpt-4o", {"temperature": 0.2})

    effective = patched["build_model"]
    assert effective.extra_params["temperature"] == 0.2
    # Untouched provider defaults survive
    assert effective.extra_params["max_tokens"] == 1000


def test_overrides_none_keeps_provider_params_untouched(patched: dict[str, Any]) -> None:
    """No overrides -> the resolved model row is passed through as-is (no needless copy)."""
    provider_service.resolve_chat_model("acme/gpt-4o")

    assert patched["build_model"] is patched["model"]
    assert patched["build_model"].extra_params == {"temperature": 0.9, "max_tokens": 1000}


def test_resolve_does_not_mutate_the_stored_row(patched: dict[str, Any]) -> None:
    """Merging overrides must not write back into the ORM row's extra_params."""
    provider_service.resolve_chat_model("acme/gpt-4o", {"temperature": 0.1})

    stored = patched["model"]
    effective = patched["build_model"]
    assert effective is not stored
    assert stored.extra_params["temperature"] == 0.9
    assert effective.extra_params["temperature"] == 0.1


def test_signature_matches_chat_model_factory() -> None:
    """The facade must be injectable as ChatModelFactory without an adapter (CONTRACT §4.7)."""
    signature = inspect.signature(provider_service.resolve_chat_model, eval_str=True)

    assert list(signature.parameters) == ["reference", "overrides"]
    assert signature.parameters["reference"].annotation is str
    # overrides optional, so the engine may call factory(ref, {}) or factory(ref)
    assert signature.parameters["overrides"].default is None


def test_validate_reference_passes_through_for_valid_ref(patched: dict[str, Any]) -> None:
    """A resolvable reference validates silently (S6 registration-time check)."""
    assert provider_service.validate_reference("acme/gpt-4o") is None
    assert patched["load_ref"] == "acme/gpt-4o"


def test_validate_reference_propagates_value_error(monkeypatch: pytest.MonkeyPatch) -> None:
    """Missing/disabled ref -> ValueError from the store propagates unchanged (S6 -> HTTP 422)."""
    monkeypatch.setattr(provider_service, "_session", _fake_session)

    def fake_load(session: Any, reference: str | None) -> tuple[Any, Any]:
        msg = f"model config '{reference}' not found or disabled. available models: acme/gpt-4o"
        raise ValueError(msg)

    monkeypatch.setattr(provider_service, "load_model_config", fake_load)

    with pytest.raises(ValueError, match="not found or disabled"):
        provider_service.validate_reference("acme/does-not-exist")


def test_facade_never_logs_or_raises_the_api_key(monkeypatch: pytest.MonkeyPatch) -> None:
    """H6: the secret stays inside build_chat_model; the facade surfaces only names."""
    secret = "sk-facade-test-key"  # noqa: S105 — dummy sentinel

    def fake_load(session: Any, reference: str | None) -> tuple[_FakeProvider, _FakeModel]:
        return _FakeProvider(), _FakeModel()

    def fake_build(provider: Any, model: Any) -> str:
        raise RuntimeError("boom during client build")

    monkeypatch.setattr(provider_service, "_session", _fake_session)
    monkeypatch.setattr(provider_service, "load_model_config", fake_load)
    monkeypatch.setattr(provider_service, "build_chat_model", fake_build)

    with pytest.raises(RuntimeError) as excinfo:
        provider_service.resolve_chat_model("acme/gpt-4o")

    assert secret not in str(excinfo.value)


def test_facade_uses_its_own_session_bound_to_shared_engine() -> None:
    """The detached form owns its session lifecycle (no request scope available)."""
    source = inspect.getsource(provider_service._session)
    assert "database_service.engine" in source
    assert "Session(" in source
