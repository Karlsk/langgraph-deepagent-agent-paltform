"""Unit tests for the env-driven LLM model registry.

Zero real network / zero real LLM: ``ChatOpenAI`` construction is offline,
and configuration is driven by monkeypatched environment variables.
"""

import pytest

from app.core.config import Settings, settings
from app.services.llm import registry

pytestmark = pytest.mark.unit


# ---------------------------------------------------------------------------
# _build_llms — entry construction
# ---------------------------------------------------------------------------


def test_build_llms_maps_names_in_order() -> None:
    """Every name becomes one entry whose ChatOpenAI model matches exactly."""
    entries = registry._build_llms(["model-a", "model-b"])  # noqa: SLF001

    assert [e["name"] for e in entries] == ["model-a", "model-b"]
    for entry in entries:
        assert entry["llm"].model_name == entry["name"]


def test_build_llms_empty_list_yields_empty_registry() -> None:
    """An empty model list produces no entries (config prevents this case)."""
    assert registry._build_llms([]) == []  # noqa: SLF001


# ---------------------------------------------------------------------------
# settings.LLM_MODELS — env parsing and fallback
# ---------------------------------------------------------------------------


def test_llm_models_parsed_from_env(monkeypatch: pytest.MonkeyPatch) -> None:
    """LLM_MODELS is a comma-separated, order-preserving fallback chain."""
    monkeypatch.setenv("LLM_MODELS", "model-a, model-b ,model-c")

    fresh = Settings()

    assert fresh.LLM_MODELS == ["model-a", "model-b", "model-c"]


def test_llm_models_falls_back_to_default_model(monkeypatch: pytest.MonkeyPatch) -> None:
    """Without LLM_MODELS the chain degrades to [DEFAULT_LLM_MODEL]."""
    monkeypatch.delenv("LLM_MODELS", raising=False)
    monkeypatch.setenv("DEFAULT_LLM_MODEL", "MiniMax-M3")

    fresh = Settings()

    assert fresh.LLM_MODELS == ["MiniMax-M3"]


# ---------------------------------------------------------------------------
# LLMRegistry — lookup behaviour against the live (env-driven) list
# ---------------------------------------------------------------------------


def test_registry_names_match_llm_models_setting() -> None:
    """The pre-built registry mirrors settings.LLM_MODELS one-to-one."""
    assert [e["name"] for e in registry.LLMRegistry.LLMS] == settings.LLM_MODELS


def test_registry_get_unknown_model_raises() -> None:
    """Requesting an unregistered model fails fast with the available list."""
    with pytest.raises(ValueError, match="not found in registry"):
        registry.LLMRegistry.get("definitely-not-registered")
