"""Unit tests for app.workflow.nodes.llm_node (spec-04, CONTRACT §4.7, AD-03/04/12, H2/H6, R3/R5/R6).

All tests run with zero real network and zero real LLM calls: provider
clients are either injected via ``_llm_instance`` or patched at the
``app.workflow.nodes.llm_node`` import location.
"""

from typing import Any

import pytest
from langchain_core.messages import AIMessage, HumanMessage, SystemMessage
from pydantic import ValidationError

from app.workflow.models import ConfigError, ExecutionLog, LLMNodeError, NodeType
from app.workflow.nodes.llm_node import LLMConfig, LLMNode

DUMMY_KEY = "sk-dummy-test-key-spec04"


class FakeLLM:
    """Fake chat model recording invoke calls; supports side_effect sequences."""

    def __init__(self, side_effect: list[Any] | None = None) -> None:
        self.calls: list[list[Any]] = []
        self.side_effect = list(side_effect) if side_effect is not None else []

    def invoke(self, messages: list[Any]) -> Any:
        self.calls.append(list(messages))
        if self.side_effect:
            item = self.side_effect.pop(0)
            if isinstance(item, BaseException):
                raise item
            return item
        return AIMessage(content="hi")


def make_node(config: dict[str, Any] | LLMConfig | None = None, **kwargs: Any) -> LLMNode:
    """Build an LLMNode with a safe default config."""
    return LLMNode(name=kwargs.pop("name", "llm1"), llm_config=config or {}, **kwargs)


# ---------------------------------------------------------------------------
# TC1: LLMConfig validation, construction, env key resolution
# ---------------------------------------------------------------------------


@pytest.mark.unit
def test_config_temperature_out_of_range() -> None:
    """temperature outside [0, 2] is rejected by pydantic (CONTRACT §4.7)."""
    with pytest.raises(ValidationError):
        LLMConfig(temperature=3)


@pytest.mark.unit
def test_config_extra_forbid_rejects_unknown() -> None:
    """Unknown config fields are rejected (S14, extra='forbid')."""
    with pytest.raises(ValidationError):
        LLMConfig(unknown_field="x")  # type: ignore[call-arg]


@pytest.mark.unit
def test_config_has_no_plaintext_api_key_field() -> None:
    """LLMConfig carries no plaintext api_key field (H6/ADR-008)."""
    assert "api_key" not in LLMConfig.model_fields


@pytest.mark.unit
def test_missing_api_key_raises_clear_error(monkeypatch: pytest.MonkeyPatch) -> None:
    """Missing default env key raises ConfigError naming the env var, never a secret value (AD-12)."""
    dummy = "sk-previously-set-value-xyz"
    monkeypatch.setenv("OPENAI_API_KEY", dummy)
    monkeypatch.delenv("OPENAI_API_KEY")
    node = make_node()
    with pytest.raises(ConfigError) as exc_info:
        node._resolve_api_key()  # noqa: SLF001 — exercising frozen contract method
    message = str(exc_info.value)
    assert "OPENAI_API_KEY" in message
    assert dummy not in message


@pytest.mark.unit
def test_api_key_env_override(monkeypatch: pytest.MonkeyPatch) -> None:
    """api_key_env overrides the default env name; missing override names it in the error."""
    monkeypatch.setenv("CUSTOM_KEY", DUMMY_KEY)
    node = make_node({"api_key_env": "CUSTOM_KEY"})
    assert node._resolve_api_key() == DUMMY_KEY  # noqa: SLF001

    monkeypatch.delenv("CUSTOM_KEY")
    with pytest.raises(ConfigError, match="CUSTOM_KEY"):
        node._resolve_api_key()  # noqa: SLF001


@pytest.mark.unit
def test_anthropic_default_env_name(monkeypatch: pytest.MonkeyPatch) -> None:
    """anthropic resolves ANTHROPIC_API_KEY by default (AD-12)."""
    monkeypatch.setenv("ANTHROPIC_API_KEY", DUMMY_KEY)
    node = make_node({"llm_type": "anthropic"})
    assert node._resolve_api_key() == DUMMY_KEY  # noqa: SLF001

    monkeypatch.delenv("ANTHROPIC_API_KEY")
    with pytest.raises(ConfigError, match="ANTHROPIC_API_KEY"):
        node._resolve_api_key()  # noqa: SLF001


@pytest.mark.unit
def test_validate_config_empty_model_name() -> None:
    """Empty model_name is invalid and raises ValueError (TC1)."""
    node = make_node({"model_name": ""})
    with pytest.raises(ValueError, match="model_name"):
        node.validate_config()


@pytest.mark.unit
def test_validate_config_valid_returns_true() -> None:
    """A valid config passes validation."""
    assert make_node().validate_config() is True


@pytest.mark.unit
def test_init_dict_config_converted() -> None:
    """dict input is auto-converted to LLMConfig; BaseNode fields are set per contract."""
    node = make_node({"model_name": "gpt-4o", "temperature": 0.3})
    assert isinstance(node._llm_config, LLMConfig)  # noqa: SLF001
    assert node._llm_config.model_name == "gpt-4o"  # noqa: SLF001
    assert node.node_type == NodeType.LLM
    assert node.config == node._llm_config.model_dump()  # noqa: SLF001
    assert node._llm_instance is None  # noqa: SLF001 — lazy-load slot (K10)


@pytest.mark.unit
def test_init_accepts_llm_config_instance() -> None:
    """An LLMConfig instance is accepted as-is."""
    cfg = LLMConfig(model_name="claude-sonnet-4-5", llm_type="anthropic")
    node = make_node(cfg)
    assert node._llm_config is cfg  # noqa: SLF001
