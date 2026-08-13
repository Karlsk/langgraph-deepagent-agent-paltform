"""Unit tests for LLMService retry semantics and model fallback.

Zero real network / zero real LLM: every model is a fake whose ``ainvoke``
returns canned data or raises fabricated openai errors; tenacity backoff is
disabled so tests finish instantly.
"""

import asyncio
from typing import Any, Optional

import httpx
import pytest
from langchain_core.messages import AIMessage, HumanMessage
from openai import (
    APIConnectionError,
    APIStatusError,
    APITimeoutError,
    AuthenticationError,
    BadRequestError,
    InternalServerError,
    NotFoundError,
    RateLimitError,
)
from tenacity import wait_none

from app.core.config import settings
from app.services.llm.registry import LLMRegistry
from app.services.llm.service import LLMService, _is_retryable_error

pytestmark = pytest.mark.unit

_MESSAGES = [HumanMessage(content="hello")]


def _http_error(exc_cls: type[APIStatusError], status_code: int) -> APIStatusError:
    """Fabricate an openai status error without any network traffic."""
    response = httpx.Response(status_code, request=httpx.Request("POST", "https://provider.test/v1/chat/completions"))
    return exc_cls(message="boom", response=response, body=None)


class FakeLLM:
    """Counts invocations and returns canned data or raises a canned error."""

    def __init__(self, result: Any = None, error: Optional[Exception] = None) -> None:
        """Store the canned outcome; ``error`` wins over ``result``."""
        self.calls = 0
        self._result = result
        self._error = error

    async def ainvoke(self, messages: Any) -> Any:  # noqa: ARG002
        """Record the attempt and replay the canned outcome."""
        self.calls += 1
        if self._error is not None:
            raise self._error
        return self._result


@pytest.fixture(autouse=True)
def no_retry_wait(monkeypatch: pytest.MonkeyPatch) -> None:
    """Disable tenacity exponential backoff so retry tests finish instantly."""
    monkeypatch.setattr(LLMService._invoke_with_retry.retry, "wait", wait_none())  # noqa: SLF001


@pytest.fixture
def two_model_service(monkeypatch: pytest.MonkeyPatch):
    """Build a service over two fake models: m1 failing, m2 succeeding."""
    failing = FakeLLM(error=_http_error(BadRequestError, 400))
    ok = FakeLLM(result=AIMessage(content="ok"))
    monkeypatch.setattr(settings, "DEFAULT_LLM_MODEL", "m1")
    monkeypatch.setattr(LLMRegistry, "LLMS", [{"name": "m1", "llm": failing}, {"name": "m2", "llm": ok}])
    return LLMService(), failing, ok


# ---------------------------------------------------------------------------
# _is_retryable_error — classification predicate
# ---------------------------------------------------------------------------


@pytest.mark.parametrize(
    "exc",
    [
        RateLimitError(message="slow down", response=_http_error(RateLimitError, 429).response, body=None),
        APITimeoutError(request=httpx.Request("POST", "https://provider.test")),
        APIConnectionError(request=httpx.Request("POST", "https://provider.test")),
        _http_error(InternalServerError, 500),
    ],
)
def test_transient_errors_are_retryable(exc: Exception) -> None:
    """Rate limits, timeouts, connection errors and 5xx are worth retrying."""
    assert _is_retryable_error(exc) is True


@pytest.mark.parametrize(
    "exc",
    [
        _http_error(BadRequestError, 400),
        _http_error(AuthenticationError, 401),
        _http_error(NotFoundError, 404),
        ValueError("not an openai error"),
    ],
)
def test_deterministic_errors_are_not_retryable(exc: Exception) -> None:
    """4xx client errors and non-openai errors must never be retried."""
    assert _is_retryable_error(exc) is False


# ---------------------------------------------------------------------------
# _invoke_with_retry — per-model attempt budget
# ---------------------------------------------------------------------------


def test_bad_request_not_retried() -> None:
    """A 400 raises immediately after exactly one attempt (no retry storm)."""
    service = LLMService()
    failing = FakeLLM(error=_http_error(BadRequestError, 400))

    with pytest.raises(BadRequestError):
        asyncio.run(service._invoke_with_retry(failing, _MESSAGES))  # noqa: SLF001

    assert failing.calls == 1


def test_rate_limit_retried_up_to_budget() -> None:
    """A transient 429 consumes the full MAX_LLM_CALL_RETRIES budget."""
    service = LLMService()
    failing = FakeLLM(
        error=RateLimitError(message="slow down", response=_http_error(RateLimitError, 429).response, body=None)
    )

    with pytest.raises(RateLimitError):
        asyncio.run(service._invoke_with_retry(failing, _MESSAGES))  # noqa: SLF001

    assert failing.calls == settings.MAX_LLM_CALL_RETRIES


# ---------------------------------------------------------------------------
# call — circular model fallback
# ---------------------------------------------------------------------------


def test_fallback_advances_after_non_retryable_error(two_model_service: tuple) -> None:
    """A 400 on the first model falls back to the second, trying each once."""
    service, failing, ok = two_model_service

    result = asyncio.run(service.call(_MESSAGES))

    assert result.content == "ok"
    assert failing.calls == 1
    assert ok.calls == 1


def test_fallback_exhaustion_raises_runtime_error(monkeypatch: pytest.MonkeyPatch) -> None:
    """When every model 400s, each is tried exactly once before RuntimeError."""
    first = FakeLLM(error=_http_error(BadRequestError, 400))
    second = FakeLLM(error=_http_error(BadRequestError, 400))
    monkeypatch.setattr(settings, "DEFAULT_LLM_MODEL", "m1")
    monkeypatch.setattr(LLMRegistry, "LLMS", [{"name": "m1", "llm": first}, {"name": "m2", "llm": second}])
    service = LLMService()

    with pytest.raises(RuntimeError, match="trying 2 models"):
        asyncio.run(service.call(_MESSAGES))

    assert first.calls == 1
    assert second.calls == 1


def test_fallback_retries_transient_error_before_advancing(two_model_service: tuple) -> None:
    """A persistent 5xx burns the retry budget on m1 before moving to m2."""
    service, _, ok = two_model_service
    failing = FakeLLM(error=_http_error(InternalServerError, 500))
    LLMRegistry.LLMS[0]["llm"] = failing
    service._llm = failing  # noqa: SLF001

    result = asyncio.run(service.call(_MESSAGES))

    assert result.content == "ok"
    assert failing.calls == settings.MAX_LLM_CALL_RETRIES
    assert ok.calls == 1
