"""Unit tests for the DB-backed LLM config resolution layer (``llm_store``).

Zero real network / zero real LLM: ``ChatOpenAI`` construction is captured by
a recording factory, and configs live in an in-memory SQLite database.
"""

from collections.abc import Generator
from typing import Any

import pytest
from pydantic import SecretStr
from sqlmodel import Session, SQLModel, create_engine

from app.core.config import settings
from app.models.agent_assets import DEFAULT_LLM_CONFIG_NAME, LlmConfig
from app.services.llm import llm_store

pytestmark = pytest.mark.unit


@pytest.fixture
def db_session() -> Generator[Session, None, None]:
    """Provide an isolated in-memory SQLite session with all tables created."""
    engine = create_engine("sqlite://")
    SQLModel.metadata.create_all(engine)
    with Session(engine) as session:
        yield session


def _make_config(**overrides: Any) -> LlmConfig:
    """Build an LlmConfig row with sensible defaults for store tests."""
    defaults: dict[str, Any] = {
        "name": DEFAULT_LLM_CONFIG_NAME,
        "model_name": "MiniMax-M3",
        "api_key": "sk-secret-1234",
        "base_url": None,
        "temperature": None,
        "max_tokens": None,
        "enabled": True,
        "description": "",
        "content_hash": "h-default",
    }
    defaults.update(overrides)
    return LlmConfig(**defaults)


def _seed(session: Session, cfg: LlmConfig) -> LlmConfig:
    session.add(cfg)
    session.commit()
    session.refresh(cfg)
    return cfg


class _CapturingFactory:
    """Records the kwargs of every ChatOpenAI construction (zero network)."""

    def __init__(self) -> None:
        """Prepare the capture slot."""
        self.kwargs: dict[str, Any] | None = None

    def __call__(self, **kwargs: Any) -> object:
        """Capture kwargs and return a sentinel (never a real client)."""
        self.kwargs = kwargs
        return object()


# ---------------------------------------------------------------------------
# build_chat_model — parameter mapping
# ---------------------------------------------------------------------------


def test_build_chat_model_maps_model_and_api_key(monkeypatch: pytest.MonkeyPatch) -> None:
    """model_name maps to ``model``; api_key is wrapped in SecretStr."""
    factory = _CapturingFactory()
    monkeypatch.setattr(llm_store, "ChatOpenAI", factory)

    llm_store.build_chat_model(_make_config())

    assert factory.kwargs is not None
    assert factory.kwargs["model"] == "MiniMax-M3"
    assert isinstance(factory.kwargs["api_key"], SecretStr)
    assert factory.kwargs["api_key"].get_secret_value() == "sk-secret-1234"


def test_build_chat_model_omits_none_optionals(monkeypatch: pytest.MonkeyPatch) -> None:
    """None base_url/temperature/max_tokens are never passed (SDK env fallback)."""
    factory = _CapturingFactory()
    monkeypatch.setattr(llm_store, "ChatOpenAI", factory)

    llm_store.build_chat_model(_make_config())

    assert factory.kwargs is not None
    assert "base_url" not in factory.kwargs
    assert "temperature" not in factory.kwargs
    assert "max_tokens" not in factory.kwargs
    assert "max_completion_tokens" not in factory.kwargs


def test_build_chat_model_passes_explicit_overrides(monkeypatch: pytest.MonkeyPatch) -> None:
    """base_url/temperature forward verbatim; max_tokens maps to max_completion_tokens."""
    factory = _CapturingFactory()
    monkeypatch.setattr(llm_store, "ChatOpenAI", factory)

    cfg = _make_config(base_url="https://proxy.example.com/v1", temperature=0.5, max_tokens=1024)
    llm_store.build_chat_model(cfg)

    assert factory.kwargs is not None
    assert factory.kwargs["base_url"] == "https://proxy.example.com/v1"
    assert factory.kwargs["temperature"] == 0.5
    # OpenAI's unified parameter: reasoning models reject the legacy max_tokens.
    assert factory.kwargs["max_completion_tokens"] == 1024
    assert "max_tokens" not in factory.kwargs


def test_build_chat_model_passes_max_retries_from_settings(monkeypatch: pytest.MonkeyPatch) -> None:
    """Every built client restores transient-error retries from settings."""
    factory = _CapturingFactory()
    monkeypatch.setattr(llm_store, "ChatOpenAI", factory)
    monkeypatch.setattr(settings, "MAX_LLM_CALL_RETRIES", 4)

    llm_store.build_chat_model(_make_config())

    assert factory.kwargs is not None
    assert factory.kwargs["max_retries"] == 4


def test_build_chat_model_returns_fresh_instances(monkeypatch: pytest.MonkeyPatch) -> None:
    """Every call builds a new instance (no instance-level caching)."""
    created: list[object] = []

    def factory(**kwargs: Any) -> object:
        instance = object()
        created.append(instance)
        return instance

    monkeypatch.setattr(llm_store, "ChatOpenAI", factory)

    cfg = _make_config()
    first = llm_store.build_chat_model(cfg)
    second = llm_store.build_chat_model(cfg)

    assert first is not second
    assert len(created) == 2


# ---------------------------------------------------------------------------
# compute_llm_config_hash — content coverage
# ---------------------------------------------------------------------------


def test_hash_is_stable_and_covers_api_key() -> None:
    """Identical configs hash equally; rotating the api_key changes the hash."""
    baseline = llm_store.compute_llm_config_hash(_make_config())
    assert baseline == llm_store.compute_llm_config_hash(_make_config())
    assert len(baseline) == 64

    rotated = llm_store.compute_llm_config_hash(_make_config(api_key="sk-other-9876"))
    assert rotated != baseline


@pytest.mark.parametrize(
    "mutation",
    [
        {"model_name": "gpt-5"},
        {"base_url": "https://proxy.example.com/v1"},
        {"temperature": 0.9},
        {"max_tokens": 4096},
        {"enabled": False},
        {"description": "rotated"},
    ],
)
def test_hash_changes_with_every_effective_field(mutation: dict[str, Any]) -> None:
    """Every effective field mutation changes the content hash."""
    baseline = llm_store.compute_llm_config_hash(_make_config())
    mutated = llm_store.compute_llm_config_hash(_make_config(**mutation))
    assert mutated != baseline


# ---------------------------------------------------------------------------
# load_llm_config — resolution semantics
# ---------------------------------------------------------------------------


def test_load_llm_config_none_resolves_to_default(db_session: Session) -> None:
    """A None reference resolves to the default config row."""
    seeded = _seed(db_session, _make_config())

    resolved = llm_store.load_llm_config(db_session, None)

    assert resolved.name == DEFAULT_LLM_CONFIG_NAME
    assert resolved.model_name == seeded.model_name


def test_load_llm_config_resolves_explicit_name(db_session: Session) -> None:
    """An explicit reference resolves the matching row."""
    _seed(db_session, _make_config())
    _seed(db_session, _make_config(name="minimax", model_name="MiniMax-M3", content_hash="h-minimax"))

    resolved = llm_store.load_llm_config(db_session, "minimax")

    assert resolved.name == "minimax"


def test_load_llm_config_missing_raises_listing_available(db_session: Session) -> None:
    """A missing reference raises ValueError listing the available config names."""
    _seed(db_session, _make_config())
    _seed(db_session, _make_config(name="minimax", content_hash="h-minimax"))

    with pytest.raises(ValueError, match="ghost") as exc_info:
        llm_store.load_llm_config(db_session, "ghost")

    message = str(exc_info.value)
    assert DEFAULT_LLM_CONFIG_NAME in message
    assert "minimax" in message


def test_load_llm_config_disabled_raises_and_lists_enabled_only(db_session: Session) -> None:
    """A disabled config is unresolvable; the listing only shows enabled names."""
    _seed(db_session, _make_config())
    _seed(db_session, _make_config(name="frozen", enabled=False, content_hash="h-frozen"))

    with pytest.raises(ValueError, match="frozen") as exc_info:
        llm_store.load_llm_config(db_session, "frozen")

    assert DEFAULT_LLM_CONFIG_NAME in str(exc_info.value)
