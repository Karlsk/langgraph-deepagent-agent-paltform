"""Characterization tests for the LLM client contract (EXP-L1..L5).

These lock in the observed behaviour behind EXP-L1..L5 of
``api-exploration-1x.md`` (spec-00 TC6). They are offline only — **no real
API calls**: HTTP traffic is intercepted with ``httpx.MockTransport`` and all
API keys are dummies. They serve as regression guards for spec-04's reliance
on:

* ``ChatOpenAI`` / ``ChatAnthropic`` constructor parameter aliases
  (``model`` / ``api_key`` / ``base_url`` / ``temperature`` / ``max_tokens`` /
  ``timeout`` / ``max_retries`` / ``model_kwargs``);
* ``invoke`` returning a ``langchain_core.messages.AIMessage`` with string
  ``content``, ``usage_metadata`` and ``response_metadata``;
* provider SDK exceptions (429/5xx) propagating unwrapped, enabling a
  status-code-based tenacity retry predicate;
* pydantic ``SecretStr`` masking of API keys in ``repr``/``str``/dumps;
* the langchain-anthropic 1.0.x ↔ langchain-core 1.0.4 pairing (AD-05).

Evidence sources (``.venv`` at langchain-openai 1.0.2 / langchain-anthropic
1.0.4 / openai 2.7.1 / anthropic 0.120.2):

* ``langchain_openai/chat_models/base.py:470-641`` field/alias definitions.
* ``langchain_anthropic/chat_models.py:1430-1497`` field/alias definitions,
  ``:1587-1591`` ``set_default_max_tokens``, ``:76-88``
  ``_default_max_tokens_for``.
* ``openai/_exceptions.py`` / ``anthropic/_exceptions.py`` status-error trees.
"""

from importlib.metadata import (
    requires,
    version,
)

import anthropic
import httpx
import openai
import pytest
from langchain_anthropic import ChatAnthropic
from langchain_core.messages import AIMessage
from langchain_openai import ChatOpenAI
from pydantic import SecretStr
from tenacity import (
    Retrying,
    retry_if_exception,
    stop_after_attempt,
)

DUMMY_KEY = "dummy-exploration-key-123"

_OPENAI_OK_BODY = {
    "id": "chatcmpl-123",
    "object": "chat.completion",
    "created": 1700000000,
    "model": "gpt-4o-mini-2024-07-18",
    "choices": [
        {
            "index": 0,
            "message": {"role": "assistant", "content": "hello from mock"},
            "finish_reason": "stop",
        }
    ],
    "usage": {"prompt_tokens": 10, "completion_tokens": 5, "total_tokens": 15},
}

_ANTHROPIC_OK_BODY = {
    "id": "msg_123",
    "type": "message",
    "role": "assistant",
    "content": [{"type": "text", "text": "hello from mock"}],
    "model": "claude-sonnet-4-5",
    "stop_reason": "end_turn",
    "stop_sequence": None,
    "usage": {"input_tokens": 10, "output_tokens": 5},
}


def _mock_chat_openai(status: int, body: dict) -> ChatOpenAI:
    """Build a ChatOpenAI whose HTTP layer always answers ``status``/``body``."""

    def handler(request: httpx.Request) -> httpx.Response:
        return httpx.Response(status, json=body)

    return ChatOpenAI(
        model="gpt-4o-mini",
        api_key=DUMMY_KEY,
        max_retries=0,
        http_client=httpx.Client(transport=httpx.MockTransport(handler)),
    )


def _mock_chat_anthropic(status: int, body: dict) -> ChatAnthropic:
    """Build a ChatAnthropic whose HTTP layer always answers ``status``/``body``.

    ``ChatAnthropic`` has no ``http_client`` constructor field; its SDK client
    is a ``cached_property`` (``chat_models.py:1617``), so we pre-seed the
    instance ``__dict__`` with a mock-transport ``anthropic.Client``.
    ``max_tokens`` must stay small: with the model-family default (64000 for
    claude-sonnet-4-5) a non-streaming create raises the anthropic SDK's
    "Streaming is required..." ValueError (``_base_client.py:775``).
    """

    def handler(request: httpx.Request) -> httpx.Response:
        return httpx.Response(status, json=body)

    llm = ChatAnthropic(
        model="claude-sonnet-4-5",
        api_key=DUMMY_KEY,
        max_tokens=256,
        max_retries=0,
    )
    llm.__dict__["_client"] = anthropic.Client(
        api_key=DUMMY_KEY,
        max_retries=0,
        http_client=httpx.Client(transport=httpx.MockTransport(handler)),
    )
    return llm


@pytest.mark.integration
def test_l1_chat_openai_constructor_params_land() -> None:
    """EXP-L1: LLMConfig-style kwargs land on the aliased ChatOpenAI fields."""
    llm = ChatOpenAI(
        model="gpt-4o-mini",
        api_key=DUMMY_KEY,
        base_url="http://localhost:9999/v1",
        temperature=0.3,
        max_tokens=256,
        timeout=30,
        max_retries=0,
        model_kwargs={"foo": "bar"},
    )

    assert llm.model_name == "gpt-4o-mini"  # field model_name, alias "model"
    assert isinstance(llm.openai_api_key, SecretStr)  # alias "api_key"
    assert llm.openai_api_base == "http://localhost:9999/v1"  # alias "base_url"
    assert llm.temperature == 0.3
    assert llm.max_tokens == 256  # alias "max_completion_tokens"; default None
    assert llm.request_timeout == 30  # alias "timeout"
    assert llm.max_retries == 0  # default None (SDK default applies)
    assert llm.model_kwargs == {"foo": "bar"}  # extra_params passthrough


@pytest.mark.integration
def test_l1_chat_anthropic_constructor_params_land() -> None:
    """EXP-L1: the same kwarg surface works on ChatAnthropic via aliases."""
    llm = ChatAnthropic(
        model="claude-sonnet-4-5",
        api_key=DUMMY_KEY,
        base_url="http://localhost:9999",
        temperature=0.3,
        max_tokens=256,
        timeout=30,
        max_retries=0,
        model_kwargs={"foo": "bar"},
    )

    assert llm.model == "claude-sonnet-4-5"  # field model (required), alias "model_name"
    assert isinstance(llm.anthropic_api_key, SecretStr)  # alias "api_key"
    assert llm.anthropic_api_url == "http://localhost:9999"  # alias "base_url"
    assert llm.temperature == 0.3
    assert llm.max_tokens == 256  # alias "max_tokens_to_sample"
    assert llm.default_request_timeout == 30  # alias "timeout"
    assert llm.max_retries == 0  # default 2 (differs from ChatOpenAI's None)
    assert llm.model_kwargs == {"foo": "bar"}


@pytest.mark.integration
def test_l1_anthropic_max_tokens_defaults_by_model_family() -> None:
    """EXP-L1: ChatAnthropic auto-fills max_tokens per model family.

    ``set_default_max_tokens`` (chat_models.py:1587) resolves ``None`` via
    ``_default_max_tokens_for``; unknown families fall back to 4096. ChatOpenAI
    keeps ``max_tokens=None`` (server-side default) — an asymmetry spec-04 must
    normalise via explicit ``LLMConfig.max_tokens``.
    """
    assert ChatAnthropic(model="claude-sonnet-4-5", api_key=DUMMY_KEY).max_tokens == 64000
    assert ChatAnthropic(model="claude-unknown-model", api_key=DUMMY_KEY).max_tokens == 4096
    assert ChatOpenAI(model="gpt-4o-mini", api_key=DUMMY_KEY).max_tokens is None


@pytest.mark.integration
def test_l2_openai_invoke_returns_aimessage_shape() -> None:
    """EXP-L2: ChatOpenAI.invoke returns AIMessage with str content + usage."""
    msg = _mock_chat_openai(200, _OPENAI_OK_BODY).invoke("hi")

    assert type(msg) is AIMessage
    assert msg.content == "hello from mock"  # plain str for text-only replies
    assert msg.usage_metadata is not None
    assert msg.usage_metadata["input_tokens"] == 10
    assert msg.usage_metadata["output_tokens"] == 5
    assert msg.usage_metadata["total_tokens"] == 15
    assert msg.response_metadata["model_name"] == "gpt-4o-mini-2024-07-18"
    assert msg.response_metadata["model_provider"] == "openai"
    assert msg.response_metadata["finish_reason"] == "stop"
    # Normalised block view is available regardless of provider.
    assert msg.content_blocks == [{"type": "text", "text": "hello from mock"}]


@pytest.mark.integration
def test_l2_anthropic_invoke_returns_aimessage_shape() -> None:
    """EXP-L2: ChatAnthropic.invoke returns the same AIMessage contract."""
    msg = _mock_chat_anthropic(200, _ANTHROPIC_OK_BODY).invoke("hi")

    assert type(msg) is AIMessage
    # Single text block collapses to a plain str, same as OpenAI.
    assert msg.content == "hello from mock"
    assert msg.usage_metadata is not None
    assert msg.usage_metadata["input_tokens"] == 10
    assert msg.usage_metadata["output_tokens"] == 5
    assert msg.usage_metadata["total_tokens"] == 15
    assert msg.response_metadata["model_name"] == "claude-sonnet-4-5"
    assert msg.response_metadata["model_provider"] == "anthropic"
    assert msg.response_metadata["stop_reason"] == "end_turn"
    assert msg.content_blocks == [{"type": "text", "text": "hello from mock"}]


@pytest.mark.integration
def test_l3_openai_429_and_5xx_raise_sdk_errors_unwrapped() -> None:
    """EXP-L3: openai SDK status errors surface unwrapped from invoke."""
    body_429 = {"error": {"message": "Rate limit reached", "type": "rate_limit_error"}}
    with pytest.raises(openai.RateLimitError) as exc_info:
        _mock_chat_openai(429, body_429).invoke("hi")
    assert exc_info.value.status_code == 429
    assert "429" in str(exc_info.value)
    assert "rate" in str(exc_info.value).lower()
    assert isinstance(exc_info.value, openai.APIStatusError)

    body_500 = {"error": {"message": "The server had an error", "type": "server_error"}}
    with pytest.raises(openai.InternalServerError) as exc_info:
        _mock_chat_openai(500, body_500).invoke("hi")
    assert exc_info.value.status_code == 500

    # openai maps every >=500 status to InternalServerError (503 included).
    with pytest.raises(openai.InternalServerError) as exc_info:
        _mock_chat_openai(503, body_500).invoke("hi")
    assert exc_info.value.status_code == 503


@pytest.mark.integration
def test_l3_anthropic_429_and_5xx_raise_sdk_errors_unwrapped() -> None:
    """EXP-L3: anthropic SDK status errors surface unwrapped from invoke."""
    body_429 = {"type": "error", "error": {"type": "rate_limit_error", "message": "Rate limit exceeded"}}
    with pytest.raises(anthropic.RateLimitError) as exc_info:
        _mock_chat_anthropic(429, body_429).invoke("hi")
    assert exc_info.value.status_code == 429
    assert "429" in str(exc_info.value)
    assert isinstance(exc_info.value, anthropic.APIStatusError)

    body_500 = {"type": "error", "error": {"type": "api_error", "message": "Internal server error"}}
    with pytest.raises(anthropic.InternalServerError) as exc_info:
        _mock_chat_anthropic(500, body_500).invoke("hi")
    assert exc_info.value.status_code == 500

    # anthropic has a dedicated 529 subclass (still an APIStatusError).
    body_529 = {"type": "error", "error": {"type": "overloaded_error", "message": "Overloaded"}}
    with pytest.raises(anthropic.OverloadedError) as exc_info:
        _mock_chat_anthropic(529, body_529).invoke("hi")
    assert exc_info.value.status_code == 529


def _is_retryable_llm_error(exc: BaseException) -> bool:
    """EXP-L3 recommended tenacity predicate: retry on 429 or any 5xx.

    Both SDKs expose ``status_code: int`` on ``APIStatusError``; matching on
    it avoids importing provider exception types and is robust against 5xx
    messages that contain neither "429" nor "rate".
    """
    status = getattr(exc, "status_code", None)
    return isinstance(status, int) and (status == 429 or status >= 500)


@pytest.mark.integration
def test_l3_tenacity_predicate_classifies_and_drives_retry() -> None:
    """EXP-L3: the status-code predicate works standalone and inside tenacity."""
    retryable = [
        openai.RateLimitError,
        anthropic.RateLimitError,
        openai.InternalServerError,
        anthropic.OverloadedError,
    ]
    raised: list[BaseException] = []
    for status, cls, llm in (
        (429, openai.RateLimitError, _mock_chat_openai(429, {"error": {"message": "rl"}})),
        (
            500,
            anthropic.InternalServerError,
            _mock_chat_anthropic(500, {"type": "error", "error": {"type": "api_error", "message": "ise"}}),
        ),
    ):
        with pytest.raises(cls) as exc_info:
            llm.invoke("hi")
        assert exc_info.value.status_code == status
        raised.append(exc_info.value)
    assert all(_is_retryable_llm_error(exc) for exc in raised)
    assert all(issubclass(cls, Exception) for cls in retryable)

    # Non-retryable: 400 maps to BadRequestError, predicate must say no.
    with pytest.raises(openai.BadRequestError) as exc_info:
        _mock_chat_openai(400, {"error": {"message": "bad request"}}).invoke("hi")
    assert not _is_retryable_llm_error(exc_info.value)

    # Wired into tenacity: two attempts on a permanent 429, then reraise.
    llm_429 = _mock_chat_openai(429, {"error": {"message": "rl"}})
    attempts = 0

    def call() -> None:
        nonlocal attempts
        attempts += 1
        llm_429.invoke("hi")

    with pytest.raises(openai.RateLimitError):
        for attempt in Retrying(
            retry=retry_if_exception(_is_retryable_llm_error),
            stop=stop_after_attempt(2),
            reraise=True,
        ):
            with attempt:
                call()
    assert attempts == 2


@pytest.mark.integration
def test_l4_api_key_never_leaks_from_repr_str_or_dump() -> None:
    """EXP-L4: SecretStr keeps the api_key out of repr/str/model_dump (H6)."""
    checks = (
        (ChatOpenAI(model="gpt-4o-mini", api_key=DUMMY_KEY), "openai_api_key"),
        (ChatAnthropic(model="claude-sonnet-4-5", api_key=DUMMY_KEY), "anthropic_api_key"),
    )
    for llm, field in checks:
        secret = getattr(llm, field)
        assert isinstance(secret, SecretStr)
        assert DUMMY_KEY not in repr(llm)
        assert DUMMY_KEY not in str(llm)
        assert repr(secret) == "SecretStr('**********')"
        assert str(secret) == "**********"
        assert DUMMY_KEY not in str(llm.model_dump())
        assert DUMMY_KEY not in llm.model_dump_json()
        # The raw value stays reachable for the SDK via get_secret_value().
        assert secret.get_secret_value() == DUMMY_KEY


@pytest.mark.integration
def test_l5_langchain_anthropic_pairs_with_frozen_langchain_core() -> None:
    """EXP-L5: installed 1.5.x pairing is metadata-consistent with core 1.5.4."""
    assert version("langchain-core") == "1.5.4"
    lc_anthropic = version("langchain-anthropic")
    assert lc_anthropic.startswith("1.5."), lc_anthropic  # >=1.5.4 constraint

    reqs = requires("langchain-anthropic") or []
    core_req = next(r for r in reqs if r.startswith("langchain-core"))
    # dist-info METADATA: langchain-core<2.0.0,>=1.5.4 — 1.5.4 satisfies it.
    assert ">=1.5.4" in core_req and "<2.0.0" in core_req
    anthropic_req = next(r for r in reqs if r.startswith("anthropic"))
    assert anthropic_req == "anthropic<1.0.0,>=0.120.0"
    assert version("anthropic").startswith("0.")
