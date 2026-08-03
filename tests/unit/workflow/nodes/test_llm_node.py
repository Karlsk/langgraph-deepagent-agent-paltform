"""Unit tests for app.workflow.nodes.llm_node (spec-04, CONTRACT §4.7, AD-03/04/12, H2/H6, R3/R5/R6).

All tests run with zero real network and zero real LLM calls: provider
clients are either injected via ``_llm_instance`` or patched at the
``app.workflow.nodes.llm_node`` import location.
"""

from typing import Any

import pytest
import tenacity
from langchain_core.messages import AIMessage, HumanMessage, SystemMessage
from pydantic import BaseModel, ValidationError

from app.workflow.models import ConfigError, LLMNodeError, NodeType
from app.workflow.nodes.llm_node import LLMConfig, LLMNode

DUMMY_KEY = "sk-dummy-test-key-spec04"


class FakeLLM:
    """Fake chat model recording invoke calls; supports side_effect sequences."""

    def __init__(self, side_effect: list[Any] | None = None) -> None:
        """Record calls; optionally replay a side_effect sequence of results/exceptions."""
        self.calls: list[list[Any]] = []
        self.side_effect = list(side_effect) if side_effect is not None else []

    def invoke(self, messages: list[Any]) -> Any:
        """Mimic a chat model invoke, raising queued exceptions or returning queued messages."""
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


class FakeStatusError(Exception):
    """Stand-in for SDK APIStatusError carrying a status_code attribute (EXP-L3)."""

    def __init__(self, message: str, status_code: int) -> None:
        """Store the status code the retry predicate inspects."""
        super().__init__(message)
        self.status_code = status_code


def patch_sleep(monkeypatch: pytest.MonkeyPatch) -> list[float]:
    """Monkeypatch tenacity.nap.sleep, returning the recorded delay sequence (AD-03)."""
    delays: list[float] = []
    monkeypatch.setattr(tenacity.nap, "sleep", lambda seconds: delays.append(seconds))
    return delays


# ---------------------------------------------------------------------------
# TC1: LLMConfig validation, construction, env key resolution
# ---------------------------------------------------------------------------


@pytest.mark.unit
def test_config_temperature_out_of_range() -> None:
    """Temperature outside [0, 2] is rejected by pydantic (CONTRACT §4.7)."""
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
    """Anthropic resolves ANTHROPIC_API_KEY by default (AD-12)."""
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
    """Dict input is auto-converted to LLMConfig; BaseNode fields are set per contract."""
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


# ---------------------------------------------------------------------------
# TC2: lazy loading + build_runnable 7-step pipeline
# ---------------------------------------------------------------------------


@pytest.mark.unit
def test_invoke_success_maps_state() -> None:
    """Successful invoke dual-writes response/model: {name}_result + flattened keys (S4/S5)."""
    node = make_node()
    node._llm_instance = FakeLLM()  # noqa: SLF001 — injected fake, zero network
    state = {"messages": [HumanMessage(content="hello")]}
    result = node.build_runnable().invoke(state)
    assert result["response"] == "hi"
    assert result["model"] == "gpt-4o-mini"
    assert result["llm1_result"] == {"response": "hi", "model": "gpt-4o-mini"}
    # R3/S5: the input state must never be mutated
    assert state == {"messages": [HumanMessage(content="hello")]}
    assert "llm1_result" not in state


@pytest.mark.unit
def test_invoke_pydantic_state_entry() -> None:
    """convert_state_to_dict handles pydantic state objects at the R3 entry."""

    class PydanticState(BaseModel):
        messages: list[Any]

    node = make_node()
    node._llm_instance = FakeLLM()  # noqa: SLF001
    state = PydanticState(messages=[HumanMessage(content="hello")])
    result = node.build_runnable().invoke(state)
    assert result["response"] == "hi"


@pytest.mark.unit
def test_system_prompt_prepended() -> None:
    """Non-empty system_prompt is prepended as the first SystemMessage."""
    fake = FakeLLM()
    node = make_node({"system_prompt": "be brief"})
    node._llm_instance = fake  # noqa: SLF001
    node.build_runnable().invoke({"messages": [HumanMessage(content="hello")]})
    received = fake.calls[0]
    assert isinstance(received[0], SystemMessage)
    assert received[0].content == "be brief"
    assert received[1].content == "hello"


@pytest.mark.unit
def test_messages_from_state_win() -> None:
    """State messages take priority over instance messages."""
    fake = FakeLLM()
    node = make_node(messages=[HumanMessage(content="instance-msg")])
    node._llm_instance = fake  # noqa: SLF001
    node.build_runnable().invoke({"messages": [HumanMessage(content="state-msg")]})
    assert fake.calls[0][0].content == "state-msg"


@pytest.mark.unit
def test_instance_messages_fallback() -> None:
    """Without state messages, instance messages are used."""
    fake = FakeLLM()
    node = make_node(messages=[HumanMessage(content="instance-msg")])
    node._llm_instance = fake  # noqa: SLF001
    node.build_runnable().invoke({})
    assert fake.calls[0][0].content == "instance-msg"


@pytest.mark.unit
def test_no_messages_raises() -> None:
    """Both message sources empty raises ValueError and records ExecutionLog.error (H2)."""
    node = make_node()
    node._llm_instance = FakeLLM()  # noqa: SLF001
    with pytest.raises(ValueError, match="messages"):
        node.build_runnable().invoke({})
    history = node.get_execution_history()
    assert len(history) == 1
    assert history[0].error is not None


@pytest.mark.unit
def test_success_execution_log_summary_only() -> None:
    """Successful run logs exactly once with summary input_data and timing (S15)."""
    node = make_node()
    node._llm_instance = FakeLLM()  # noqa: SLF001
    node.build_runnable().invoke({"messages": [HumanMessage(content="hello")]})
    history = node.get_execution_history()
    assert len(history) == 1
    log = history[0]
    assert log.node_name == "llm1"
    assert log.input_data == {"message_count": 1, "model": "gpt-4o-mini"}
    assert log.output_data == {"response": "hi", "model": "gpt-4o-mini"}
    assert log.execution_time_ms >= 0
    assert log.error is None


@pytest.mark.unit
def test_runnable_tags() -> None:
    """build_runnable() output carries tags=[name] (K4, EXP-C2 introspection)."""
    node = make_node()
    runnable = node.build_runnable()
    assert node.name in runnable.config["tags"]  # type: ignore[attr-defined]


@pytest.mark.unit
def test_lazy_instance_reuse(monkeypatch: pytest.MonkeyPatch) -> None:
    """The provider client is built once and memoized (K10)."""
    monkeypatch.setenv("OPENAI_API_KEY", DUMMY_KEY)
    calls = {"count": 0}

    def fake_ctor(**kwargs: Any) -> FakeLLM:
        calls["count"] += 1
        return FakeLLM()

    monkeypatch.setattr("app.workflow.nodes.llm_node.ChatOpenAI", fake_ctor)
    node = make_node()
    first = node._get_llm_instance()  # noqa: SLF001
    second = node._get_llm_instance()  # noqa: SLF001
    assert first is second
    assert calls["count"] == 1


@pytest.mark.unit
def test_openai_client_construction_params(monkeypatch: pytest.MonkeyPatch) -> None:
    """ChatOpenAI receives resolved env key, max_retries=0 and model_kwargs (AD-03/04/12)."""
    monkeypatch.setenv("OPENAI_API_KEY", DUMMY_KEY)
    monkeypatch.setenv("OPENAI_BASE_URL", "https://proxy.example.com/v1")
    captured: dict[str, Any] = {}

    def fake_ctor(**kwargs: Any) -> FakeLLM:
        captured.update(kwargs)
        return FakeLLM()

    monkeypatch.setattr("app.workflow.nodes.llm_node.ChatOpenAI", fake_ctor)
    node = make_node({"extra_params": {"seed": 7}})
    node._get_llm_instance()  # noqa: SLF001
    assert captured["api_key"] == DUMMY_KEY
    assert captured["base_url"] == "https://proxy.example.com/v1"
    assert captured["max_retries"] == 0
    assert captured["model"] == "gpt-4o-mini"
    assert captured["model_kwargs"] == {"seed": 7}


@pytest.mark.unit
def test_anthropic_branch(monkeypatch: pytest.MonkeyPatch) -> None:
    """llm_type='anthropic' instantiates ChatAnthropic with explicit max_tokens (EXP-L1)."""
    monkeypatch.setenv("ANTHROPIC_API_KEY", DUMMY_KEY)
    captured: dict[str, Any] = {}

    def fake_ctor(**kwargs: Any) -> FakeLLM:
        captured.update(kwargs)
        return FakeLLM()

    monkeypatch.setattr("app.workflow.nodes.llm_node.ChatAnthropic", fake_ctor)
    node = make_node({"llm_type": "anthropic", "model_name": "claude-sonnet-4-5"})
    node._get_llm_instance()  # noqa: SLF001
    assert captured["model"] == "claude-sonnet-4-5"
    assert captured["api_key"] == DUMMY_KEY
    assert captured["max_tokens"] == 4096  # None config falls back to the safe default
    assert captured["max_retries"] == 0


@pytest.mark.unit
def test_invoke_success_via_retry_pipeline() -> None:
    """A first-try success passes the AIMessage content through unchanged."""
    fake = FakeLLM()
    node = make_node()
    response = node._invoke_with_retry(fake, [HumanMessage(content="hello")])  # noqa: SLF001
    assert response.content == "hi"
    assert len(fake.calls) == 1


# ---------------------------------------------------------------------------
# TC3: tenacity retry semantics, failure paths, H6 leak protection
# ---------------------------------------------------------------------------


@pytest.mark.unit
def test_retry_on_429_then_success(monkeypatch: pytest.MonkeyPatch) -> None:
    """Two 429s then success: 3 invokes, tenacity.nap.sleep twice with backoff [1, 2] (AD-03)."""
    delays = patch_sleep(monkeypatch)
    fake = FakeLLM(
        side_effect=[
            FakeStatusError("Error code: 429 - rate limit", 429),
            FakeStatusError("Error code: 429 - rate limit", 429),
            AIMessage(content="ok"),
        ]
    )
    node = make_node()
    response = node._invoke_with_retry(fake, [HumanMessage(content="hello")])  # noqa: SLF001
    assert response.content == "ok"
    assert len(fake.calls) == 3
    assert delays == [1.0, 2.0]


@pytest.mark.unit
def test_retry_on_5xx(monkeypatch: pytest.MonkeyPatch) -> None:
    """5xx status codes hit the EXP-L3 predicate and are retried."""
    delays = patch_sleep(monkeypatch)
    fake = FakeLLM(side_effect=[FakeStatusError("Error code: 500", 500), AIMessage(content="ok")])
    node = make_node()
    response = node._invoke_with_retry(fake, [HumanMessage(content="hello")])  # noqa: SLF001
    assert response.content == "ok"
    assert len(fake.calls) == 2
    assert delays == [1.0]


@pytest.mark.unit
def test_non_rate_limit_error_no_retry(monkeypatch: pytest.MonkeyPatch) -> None:
    """A non-retryable error is invoked once, never slept, wrapped into LLMNodeError."""
    delays = patch_sleep(monkeypatch)
    fake = FakeLLM(side_effect=[FakeStatusError("Error code: 400 - bad request", 400)])
    node = make_node()
    with pytest.raises(LLMNodeError, match="bad request"):
        node._invoke_with_retry(fake, [HumanMessage(content="hello")])  # noqa: SLF001
    assert len(fake.calls) == 1
    assert delays == []


@pytest.mark.unit
def test_retry_exhausted_raises(monkeypatch: pytest.MonkeyPatch) -> None:
    """Always-429 with max_retries=2: 3 attempts, 2 waits [1, 2], LLMNodeError names attempts."""
    delays = patch_sleep(monkeypatch)
    fake = FakeLLM(side_effect=[FakeStatusError("Error code: 429", 429)] * 3)
    node = make_node({"max_retries": 2})
    with pytest.raises(LLMNodeError, match="3 attempts"):
        node._invoke_with_retry(fake, [HumanMessage(content="hello")])  # noqa: SLF001
    assert len(fake.calls) == 3
    assert delays == [1.0, 2.0]


@pytest.mark.unit
def test_retry_respects_custom_base_delay(monkeypatch: pytest.MonkeyPatch) -> None:
    """retry_base_delay scales the exponential backoff sequence (S8)."""
    delays = patch_sleep(monkeypatch)
    fake = FakeLLM(side_effect=[FakeStatusError("429", 429)] * 3)
    node = make_node({"max_retries": 2, "retry_base_delay": 0.5})
    with pytest.raises(LLMNodeError):
        node._invoke_with_retry(fake, [HumanMessage(content="hello")])  # noqa: SLF001
    assert delays == [0.5, 1.0]


@pytest.mark.unit
def test_invoke_failure_records_error_log() -> None:
    """Invoke failure inside the pipeline records ExecutionLog.error then re-raises (H2/R6)."""
    fake = FakeLLM(side_effect=[FakeStatusError("Error code: 400", 400)])
    node = make_node()
    node._llm_instance = fake  # noqa: SLF001
    with pytest.raises(LLMNodeError):
        node.build_runnable().invoke({"messages": [HumanMessage(content="hello")]})
    history = node.get_execution_history()
    assert len(history) == 1
    assert history[0].error is not None
    assert "400" in history[0].error


@pytest.mark.unit
def test_execution_log_no_secret_leak(monkeypatch: pytest.MonkeyPatch) -> None:
    """H6 guard: serialized ExecutionLog carries neither the env key value nor the full state."""
    secret_value = "sk-super-secret-value-leak-check"  # noqa: S105 — dummy sentinel, not a real credential
    monkeypatch.setenv("OPENAI_API_KEY", secret_value)
    state_payload = "confidential-state-payload-marker"
    node = make_node()
    node._llm_instance = FakeLLM()  # noqa: SLF001
    node.build_runnable().invoke({"messages": [HumanMessage(content=state_payload)], "extra": state_payload})
    history = node.get_execution_history()
    assert history
    for log in history:
        serialized = log.model_dump_json()
        assert secret_value not in serialized
        assert state_payload not in serialized
