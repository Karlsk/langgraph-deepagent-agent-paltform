"""Unit tests for the DB-backed model config resolution layer (``llm_store``).

Zero real network / zero real LLM: ``ChatOpenAI`` construction is captured by
a recording factory, and provider/model rows live in an in-memory SQLite
database.
"""

from collections.abc import Generator
from typing import Any

import pytest
from pydantic import SecretStr
from sqlmodel import Session, SQLModel, create_engine

from app.core.config import settings
from app.models.provider import DEFAULT_MODEL_NAME, DEFAULT_MODEL_REF, DEFAULT_PROVIDER_NAME, ModelConfig, Provider
from app.services.llm import llm_store

pytestmark = pytest.mark.unit


@pytest.fixture
def db_session() -> Generator[Session, None, None]:
    """Provide an isolated in-memory SQLite session with all tables created."""
    engine = create_engine("sqlite://")
    SQLModel.metadata.create_all(engine)
    with Session(engine) as session:
        yield session


def _make_provider(**overrides: Any) -> Provider:
    """Build a Provider row with sensible defaults for store tests."""
    defaults: dict[str, Any] = {
        "name": DEFAULT_PROVIDER_NAME,
        "type": "OPENAI_COMPATIBLE",
        "base_url": "",
        "auth_config": {"api_key": "sk-secret-1234"},
        "enabled": True,
    }
    defaults.update(overrides)
    return Provider(**defaults)


def _make_model(**overrides: Any) -> ModelConfig:
    """Build a ModelConfig row with sensible defaults for store tests."""
    defaults: dict[str, Any] = {
        "provider_id": 1,
        "name": DEFAULT_MODEL_NAME,
        "model_id": "MiniMax-M3",
        "context_size": None,
        "extra_params": {},
        "enabled": True,
    }
    defaults.update(overrides)
    return ModelConfig(**defaults)


def _seed_pair(session: Session, provider: Provider, model: ModelConfig) -> tuple[Provider, ModelConfig]:
    """Persist a provider row and one model config row bound to it."""
    session.add(provider)
    session.commit()
    session.refresh(provider)
    model.provider_id = provider.id
    session.add(model)
    session.commit()
    session.refresh(model)
    return provider, model


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
# parse_model_ref — reference syntax
# ---------------------------------------------------------------------------


def test_parse_model_ref_splits_on_first_slash() -> None:
    """A well-formed reference splits into provider and model segments."""
    assert llm_store.parse_model_ref("openai/gpt-4o") == ("openai", "gpt-4o")


@pytest.mark.parametrize("bad", ["", "no-slash", "/model", "provider/"])
def test_parse_model_ref_rejects_malformed(bad: str) -> None:
    """Malformed references raise ValueError (empty/excess segments)."""
    with pytest.raises(ValueError, match="invalid model reference"):
        llm_store.parse_model_ref(bad)


def test_parse_model_ref_keeps_extra_slashes_in_model_name() -> None:
    """Only the first slash is a separator; the model name keeps the rest."""
    assert llm_store.parse_model_ref("openai/gpt/4o") == ("openai", "gpt/4o")


# ---------------------------------------------------------------------------
# build_chat_model — parameter mapping
# ---------------------------------------------------------------------------


def test_build_chat_model_maps_model_id_and_api_key(monkeypatch: pytest.MonkeyPatch) -> None:
    """model_id maps to ``model``; auth_config api_key is wrapped in SecretStr."""
    factory = _CapturingFactory()
    monkeypatch.setattr(llm_store, "ChatOpenAI", factory)

    llm_store.build_chat_model(_make_provider(), _make_model())

    assert factory.kwargs is not None
    assert factory.kwargs["model"] == "MiniMax-M3"
    assert isinstance(factory.kwargs["api_key"], SecretStr)
    assert factory.kwargs["api_key"].get_secret_value() == "sk-secret-1234"


def test_build_chat_model_omits_unset_optionals(monkeypatch: pytest.MonkeyPatch) -> None:
    """Empty base_url and empty extra_params are never passed (SDK env fallback)."""
    factory = _CapturingFactory()
    monkeypatch.setattr(llm_store, "ChatOpenAI", factory)

    llm_store.build_chat_model(_make_provider(), _make_model())

    assert factory.kwargs is not None
    assert "base_url" not in factory.kwargs
    assert "temperature" not in factory.kwargs
    assert "max_tokens" not in factory.kwargs
    assert "max_completion_tokens" not in factory.kwargs


def test_build_chat_model_passes_explicit_overrides(monkeypatch: pytest.MonkeyPatch) -> None:
    """base_url/temperature forward verbatim; max_tokens maps to max_completion_tokens."""
    factory = _CapturingFactory()
    monkeypatch.setattr(llm_store, "ChatOpenAI", factory)

    provider = _make_provider(base_url="https://proxy.example.com/v1")
    model = _make_model(extra_params={"temperature": 0.5, "max_tokens": 1024})
    llm_store.build_chat_model(provider, model)

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

    llm_store.build_chat_model(_make_provider(), _make_model())

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

    provider, model = _make_provider(), _make_model()
    first = llm_store.build_chat_model(provider, model)
    second = llm_store.build_chat_model(provider, model)

    assert first is not second
    assert len(created) == 2


# ---------------------------------------------------------------------------
# compute_model_config_hash — content coverage
# ---------------------------------------------------------------------------


def test_hash_is_stable_and_covers_api_key() -> None:
    """Identical pairs hash equally; rotating the api_key changes the hash."""
    baseline = llm_store.compute_model_config_hash(_make_provider(), _make_model())
    assert baseline == llm_store.compute_model_config_hash(_make_provider(), _make_model())
    assert len(baseline) == 64

    rotated = llm_store.compute_model_config_hash(
        _make_provider(auth_config={"api_key": "sk-other-9876"}), _make_model()
    )
    assert rotated != baseline


@pytest.mark.parametrize(
    "provider_mutation,model_mutation",
    [
        ({"type": "OPENAI"}, {}),
        ({"base_url": "https://proxy.example.com/v1"}, {}),
        ({"enabled": False}, {}),
        ({}, {"model_id": "gpt-5"}),
        ({}, {"name": "renamed"}),
        ({}, {"context_size": 128000}),
        ({}, {"extra_params": {"temperature": 0.9}}),
        ({}, {"enabled": False}),
    ],
)
def test_hash_changes_with_every_effective_field(
    provider_mutation: dict[str, Any], model_mutation: dict[str, Any]
) -> None:
    """Every effective field mutation on either row changes the content hash."""
    baseline = llm_store.compute_model_config_hash(_make_provider(), _make_model())
    mutated = llm_store.compute_model_config_hash(
        _make_provider(**provider_mutation), _make_model(**model_mutation)
    )
    assert mutated != baseline


# ---------------------------------------------------------------------------
# load_model_config — resolution semantics
# ---------------------------------------------------------------------------


def test_load_model_config_none_resolves_to_default_pair(db_session: Session) -> None:
    """A None reference resolves to the default provider/model pair."""
    _seed_pair(db_session, _make_provider(), _make_model())

    provider, model = llm_store.load_model_config(db_session, None)

    assert provider.name == DEFAULT_PROVIDER_NAME
    assert model.name == DEFAULT_MODEL_NAME
    assert model.model_id == "MiniMax-M3"


def test_load_model_config_resolves_explicit_reference(db_session: Session) -> None:
    """An explicit ``provider/model`` reference resolves the matching pair."""
    _seed_pair(db_session, _make_provider(), _make_model())
    other = Provider(name="minimax", type="OPENAI_COMPATIBLE", auth_config={"api_key": "sk-x"})
    _seed_pair(db_session, other, _make_model(name="m3", model_id="MiniMax-M3"))

    provider, model = llm_store.load_model_config(db_session, "minimax/m3")

    assert provider.name == "minimax"
    assert model.name == "m3"


def test_load_model_config_missing_raises_listing_available(db_session: Session) -> None:
    """A missing reference raises ValueError listing the available refs."""
    _seed_pair(db_session, _make_provider(), _make_model())
    other = Provider(name="minimax", type="OPENAI_COMPATIBLE", auth_config={"api_key": "sk-x"})
    _seed_pair(db_session, other, _make_model(name="m3", model_id="MiniMax-M3"))

    with pytest.raises(ValueError, match="ghost/none") as exc_info:
        llm_store.load_model_config(db_session, "ghost/none")

    message = str(exc_info.value)
    assert DEFAULT_MODEL_REF in message
    assert "minimax/m3" in message


def test_load_model_config_disabled_pair_raises_and_lists_enabled_only(db_session: Session) -> None:
    """A disabled model is unresolvable; the listing only shows enabled refs."""
    _seed_pair(db_session, _make_provider(), _make_model())
    frozen = Provider(name="frozen", type="OPENAI_COMPATIBLE", auth_config={"api_key": "sk-x"})
    _seed_pair(db_session, frozen, _make_model(name="locked", model_id="m", enabled=False))

    with pytest.raises(ValueError, match="frozen/locked") as exc_info:
        llm_store.load_model_config(db_session, "frozen/locked")

    assert DEFAULT_MODEL_REF in str(exc_info.value)
    assert "frozen/locked" not in str(exc_info.value).split("available models: ")[1]


def test_load_model_config_soft_deleted_pair_is_unresolvable(db_session: Session) -> None:
    """A soft-deleted provider/model pair behaves like a missing reference."""
    _seed_pair(db_session, _make_provider(), _make_model())
    gone = Provider(name="gone", type="OPENAI_COMPATIBLE", auth_config={"api_key": "sk-x"})
    _seed_pair(db_session, gone, _make_model(name="ghost", model_id="m"))
    gone.deleted = True
    db_session.add(gone)
    db_session.commit()

    with pytest.raises(ValueError, match="gone/ghost"):
        llm_store.load_model_config(db_session, "gone/ghost")


def test_load_model_config_malformed_reference_raises(db_session: Session) -> None:
    """A reference without the provider/model shape fails fast."""
    _seed_pair(db_session, _make_provider(), _make_model())

    with pytest.raises(ValueError, match="invalid model reference"):
        llm_store.load_model_config(db_session, "no-slash")
