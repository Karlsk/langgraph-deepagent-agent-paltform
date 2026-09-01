"""Unit tests for long-term memory (mem0) configuration decoupling (SPEC-LTM-01).

Covers: ``LONG_TERM_MEMORY_MODEL`` defaulting to ``DEFAULT_LLM_MODEL``, and the
independent embedder endpoint settings (base_url / api_key / embedding_dims)
being forwarded into the mem0 config dict, with empty values normalized to
``None`` so mem0 falls back to ``OPENAI_BASE_URL`` / ``OPENAI_API_KEY``.

No network / LLM / DB calls: ``AsyncMemory.from_config`` is monkeypatched.
"""

import asyncio
import functools
from typing import Any
from unittest.mock import AsyncMock

import pytest

from app.core.config import Settings
from app.services import memory as memory_module
from app.services.memory import memory_service

pytestmark = pytest.mark.unit


def _sync(test):
    """Wrap an async scenario into a sync pytest test.

    Project convention: no async pytest plugin, every async case runs
    through ``asyncio.run``.
    """

    @functools.wraps(test)
    def wrapper(*args: Any, **kwargs: Any) -> None:
        asyncio.run(test(*args, **kwargs))

    return wrapper


@pytest.fixture(autouse=True)
def _reset_memory_singleton():
    """Ensure each test builds a fresh mem0 instance."""
    memory_service._memory = None  # noqa: SLF001 — reset cached instance for test isolation
    yield
    memory_service._memory = None  # noqa: SLF001 — reset cached instance for test isolation


def _fresh_settings(monkeypatch: pytest.MonkeyPatch) -> Settings:
    """Instantiate Settings against a controlled environment."""
    for var in (
        "LONG_TERM_MEMORY_MODEL",
        "LONG_TERM_MEMORY_EMBEDDER_MODEL",
        "LONG_TERM_MEMORY_EMBEDDER_BASE_URL",
        "LONG_TERM_MEMORY_EMBEDDER_API_KEY",
        "LONG_TERM_MEMORY_EMBEDDER_DIMS",
        "LONG_TERM_MEMORY_COLLECTION_NAME",
    ):
        monkeypatch.delenv(var, raising=False)
    monkeypatch.setenv("DEFAULT_LLM_MODEL", "gateway-chat-model")
    return Settings()


def test_memory_model_defaults_to_default_llm_model(monkeypatch: pytest.MonkeyPatch) -> None:
    """Without an explicit override the memory LLM follows DEFAULT_LLM_MODEL."""
    settings = _fresh_settings(monkeypatch)
    assert settings.LONG_TERM_MEMORY_MODEL == "gateway-chat-model"
    assert settings.LONG_TERM_MEMORY_MODEL == settings.DEFAULT_LLM_MODEL


def test_memory_model_explicit_env_overrides_default(monkeypatch: pytest.MonkeyPatch) -> None:
    """An explicit LONG_TERM_MEMORY_MODEL wins over the DEFAULT_LLM_MODEL fallback."""
    _fresh_settings(monkeypatch)
    monkeypatch.setenv("LONG_TERM_MEMORY_MODEL", "dedicated-extractor")
    assert Settings().LONG_TERM_MEMORY_MODEL == "dedicated-extractor"


def test_embedder_defaults_when_endpoint_settings_blank(monkeypatch: pytest.MonkeyPatch) -> None:
    """Blank embedder endpoint settings keep the historical defaults."""
    settings = _fresh_settings(monkeypatch)
    assert settings.LONG_TERM_MEMORY_EMBEDDER_MODEL == "text-embedding-3-small"
    assert settings.LONG_TERM_MEMORY_EMBEDDER_BASE_URL == ""
    assert settings.LONG_TERM_MEMORY_EMBEDDER_API_KEY == ""
    assert settings.LONG_TERM_MEMORY_EMBEDDER_DIMS == 1536


@_sync
async def test_memory_config_dict_normalizes_blank_endpoint_to_none(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    """Empty base_url/api_key are passed as None so mem0 falls back to OPENAI_*."""
    # Bind a freshly built Settings into the memory module (it imports the
    # global singleton; env changes only apply to new instances).
    monkeypatch.setattr(memory_module, "settings", _fresh_settings(monkeypatch))
    captured: dict[str, Any] = {}

    async def fake_from_config(config_dict: dict[str, Any]) -> Any:
        captured.update(config_dict)
        return object()

    monkeypatch.setattr(memory_module.AsyncMemory, "from_config", fake_from_config)

    await memory_service._get_memory()  # noqa: SLF001 — config-dict construction is the unit under test

    embedder_cfg = captured["embedder"]["config"]
    assert embedder_cfg["openai_base_url"] is None
    assert embedder_cfg["api_key"] is None
    assert embedder_cfg["embedding_dims"] == 1536
    assert captured["llm"]["config"]["model"] == "gateway-chat-model"


@_sync
async def test_memory_config_dict_forwards_explicit_endpoint(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    """Explicit embedder endpoint settings are forwarded verbatim to mem0."""
    _fresh_settings(monkeypatch)
    monkeypatch.setenv("LONG_TERM_MEMORY_EMBEDDER_MODEL", "bge-m3")
    monkeypatch.setenv("LONG_TERM_MEMORY_EMBEDDER_BASE_URL", "https://embed.example.com/v1")
    monkeypatch.setenv("LONG_TERM_MEMORY_EMBEDDER_API_KEY", "sk-embed")
    monkeypatch.setenv("LONG_TERM_MEMORY_EMBEDDER_DIMS", "1024")
    # Rebuild after env setup so the new instance picks up the overrides.
    monkeypatch.setattr(memory_module, "settings", Settings())
    captured: dict[str, Any] = {}

    async def fake_from_config(config_dict: dict[str, Any]) -> Any:
        captured.update(config_dict)
        return AsyncMock()

    monkeypatch.setattr(memory_module.AsyncMemory, "from_config", fake_from_config)

    await memory_service._get_memory()  # noqa: SLF001 — config-dict construction is the unit under test

    embedder_cfg = captured["embedder"]["config"]
    assert embedder_cfg["model"] == "bge-m3"
    assert embedder_cfg["openai_base_url"] == "https://embed.example.com/v1"
    assert embedder_cfg["api_key"] == "sk-embed"
    assert embedder_cfg["embedding_dims"] == 1024
