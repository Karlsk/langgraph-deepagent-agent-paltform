"""Unit tests for the LLM provider remote-model discovery service.

Zero real network / zero real LLM: every ``AsyncOpenAI`` construction is
replaced by an in-process fake whose ``models.list()`` returns canned data
or raises fabricated errors; client.close() is asserted to run in a finally
so connection resources never leak on failure paths.
"""

import asyncio
from typing import Any

import pytest

from app.models.provider import Provider
from app.services.llm.discovery import UNSUPPORTED_TYPES, discover_remote_models

pytestmark = pytest.mark.unit


class FakeModel:
    """Stand-in for ``openai.types.Model``: only id / owned_by / model_dump matter."""

    def __init__(self, model_id: str, owned_by: str | None = "openai") -> None:
        """Capture fields used by the discovery projection."""
        self.id = model_id
        self.owned_by = owned_by

    def model_dump(self) -> dict[str, Any]:
        """Serialize to a dict for ``raw`` preservation."""
        return {"id": self.id, "object": "model", "owned_by": self.owned_by}


class FakeModelsPage:
    """Async-iterable stand-in for ``AsyncOpenAI.models.list()`` pagination."""

    def __init__(self, models: list[FakeModel]) -> None:
        """Store the canned list to replay during iteration."""
        self._models = models

    def __aiter__(self) -> "FakeModelsPage":
        """Return self as the async iterator."""
        return self

    async def __anext__(self) -> FakeModel:
        """Pop the next canned model or stop the iteration."""
        if not self._models:
            raise StopAsyncIteration
        return self._models.pop(0)


class FakeAsyncOpenAI:
    """In-process stand-in for AsyncOpenAI scripted per outcome."""

    outcome: str = "up"
    canned_models: list[FakeModel] = []
    last: "FakeAsyncOpenAI | None" = None

    def __init__(self, **kwargs: Any) -> None:
        """Capture construction kwargs for assertions; track the latest instance."""
        self.kwargs = kwargs
        # Instance-level ``self.models`` mirrors the real AsyncOpenAI shape
        # (``client.models.list()``) so production code paths work unchanged.
        self.models = self
        self.closed = False
        FakeAsyncOpenAI.last = self

    def list(self) -> FakeModelsPage:
        """models.list() returns a fake async-iterable page."""
        if FakeAsyncOpenAI.outcome == "up":
            return FakeModelsPage(list(FakeAsyncOpenAI.canned_models))
        raise ConnectionError("fake upstream failure")

    async def close(self) -> None:
        """Record the async client shutdown call (mirrors AsyncOpenAI.close)."""
        self.closed = True


def _make_provider(**overrides: Any) -> Provider:
    """Build a transient Provider row (no DB persistence) for service unit tests."""
    fields: dict[str, Any] = {
        "name": "deepseek",
        "type": "OPENAI_COMPATIBLE",
        "base_url": "https://api.deepseek.com/v1",
        "auth_config": {"api_key": "sk-test-key"},
        "enabled": True,
    }
    fields.update(overrides)
    return Provider(**fields)


# ---------------------------------------------------------------------------
# UNSUPPORTED_TYPES — frozen set guards
# ---------------------------------------------------------------------------


def test_unsupported_types_contains_anthropic() -> None:
    """Anthropic is the one provider family the service refuses by design."""
    assert "ANTHROPIC" in UNSUPPORTED_TYPES


def test_unsupported_types_is_frozen() -> None:
    """The rejection set must be immutable to prevent runtime tampering."""
    with pytest.raises((AttributeError, TypeError)):
        UNSUPPORTED_TYPES.add("OPENAI")  # type: ignore[attr-defined]


# ---------------------------------------------------------------------------
# discover_remote_models — happy path
# ---------------------------------------------------------------------------


def test_discover_returns_empty_when_upstream_empty(monkeypatch: pytest.MonkeyPatch) -> None:
    """An empty upstream list is projected to an empty RemoteModelInfo list."""
    FakeAsyncOpenAI.outcome = "up"
    FakeAsyncOpenAI.canned_models = []
    monkeypatch.setattr(
        "app.services.llm.discovery.AsyncOpenAI", FakeAsyncOpenAI
    )

    result = asyncio.run(discover_remote_models(_make_provider()))

    assert result == []


def test_discover_projects_each_model_id_and_owned_by(monkeypatch: pytest.MonkeyPatch) -> None:
    """Each upstream Model is projected into (id, owned_by, raw)."""
    FakeAsyncOpenAI.outcome = "up"
    FakeAsyncOpenAI.canned_models = [
        FakeModel("deepseek-v4-flash", owned_by="deepseek"),
        FakeModel("deepseek-v4-pro", owned_by="deepseek"),
    ]
    monkeypatch.setattr(
        "app.services.llm.discovery.AsyncOpenAI", FakeAsyncOpenAI
    )

    result = asyncio.run(discover_remote_models(_make_provider(name="deepseek")))

    assert [m.id for m in result] == ["deepseek-v4-flash", "deepseek-v4-pro"]
    assert all(m.owned_by == "deepseek" for m in result)
    assert all(m.raw["object"] == "model" for m in result)


def test_discover_passes_api_key_and_base_url(monkeypatch: pytest.MonkeyPatch) -> None:
    """The constructed client receives the provider's auth + base_url verbatim."""
    FakeAsyncOpenAI.outcome = "up"
    FakeAsyncOpenAI.canned_models = []
    monkeypatch.setattr(
        "app.services.llm.discovery.AsyncOpenAI", FakeAsyncOpenAI
    )

    asyncio.run(
        discover_remote_models(
            _make_provider(base_url="https://api.deepseek.com/v1", auth_config={"api_key": "sk-abcdef-1234"})
        )
    )

    fake = FakeAsyncOpenAI.last
    assert fake is not None
    assert fake.kwargs["api_key"] == "sk-abcdef-1234"
    assert fake.kwargs["base_url"] == "https://api.deepseek.com/v1"


def test_discover_falls_back_to_no_key_when_auth_config_empty(monkeypatch: pytest.MonkeyPatch) -> None:
    """A missing api_key falls back to the 'no-key' placeholder (OLLAMA-style)."""
    FakeAsyncOpenAI.outcome = "up"
    FakeAsyncOpenAI.models = []
    monkeypatch.setattr(
        "app.services.llm.discovery.AsyncOpenAI", FakeAsyncOpenAI
    )

    asyncio.run(
        discover_remote_models(_make_provider(type="OLLAMA", auth_config={}))
    )

    assert FakeAsyncOpenAI.last is not None
    assert FakeAsyncOpenAI.last.kwargs["api_key"] == "no-key"


# ---------------------------------------------------------------------------
# discover_remote_models — rejection paths
# ---------------------------------------------------------------------------


def test_discover_rejects_anthropic_with_value_error() -> None:
    """Anthropic providers are refused synchronously before any network call."""
    with pytest.raises(ValueError, match="does not support auto-discovery"):
        asyncio.run(discover_remote_models(_make_provider(type="ANTHROPIC")))


def test_discover_propagates_upstream_connection_error(monkeypatch: pytest.MonkeyPatch) -> None:
    """Upstream connection errors bubble up so the endpoint can map them to 502."""
    FakeAsyncOpenAI.outcome = "down"
    monkeypatch.setattr(
        "app.services.llm.discovery.AsyncOpenAI", FakeAsyncOpenAI
    )

    with pytest.raises(ConnectionError, match="fake upstream failure"):
        asyncio.run(discover_remote_models(_make_provider()))


# ---------------------------------------------------------------------------
# discover_remote_models — resource cleanup (no leaked clients)
# ---------------------------------------------------------------------------


def test_discover_closes_client_on_success(monkeypatch: pytest.MonkeyPatch) -> None:
    """close() runs even when iteration completes without error."""
    FakeAsyncOpenAI.outcome = "up"
    FakeAsyncOpenAI.canned_models = [FakeModel("gpt-4o", owned_by="openai")]
    monkeypatch.setattr(
        "app.services.llm.discovery.AsyncOpenAI", FakeAsyncOpenAI
    )

    asyncio.run(discover_remote_models(_make_provider()))

    assert FakeAsyncOpenAI.last is not None
    assert FakeAsyncOpenAI.last.closed is True


def test_discover_closes_client_on_failure(monkeypatch: pytest.MonkeyPatch) -> None:
    """close() still runs when iteration raises, so connections never leak."""
    FakeAsyncOpenAI.outcome = "down"
    monkeypatch.setattr(
        "app.services.llm.discovery.AsyncOpenAI", FakeAsyncOpenAI
    )

    with pytest.raises(ConnectionError):
        asyncio.run(discover_remote_models(_make_provider()))

    assert FakeAsyncOpenAI.last is not None
    assert FakeAsyncOpenAI.last.closed is True