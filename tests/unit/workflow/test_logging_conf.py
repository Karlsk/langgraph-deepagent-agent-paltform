"""Unit tests for app.workflow.logging_conf (spec-08 TC1: redaction, AD-02 v2)."""

from __future__ import annotations

import logging
from collections.abc import Generator
from typing import Any

import pytest
import structlog

from app.workflow import logging_conf
from app.workflow.logging_conf import (
    SECRET_KEY_PATTERNS,
    redact,
    redact_processor,
    setup_logging,
)

pytestmark = pytest.mark.unit


class _Opaque:
    """Non-JSON-serializable object; redact() must fall back to default=str."""

    def __str__(self) -> str:
        return "opaque-repr"


@pytest.fixture(autouse=True)
def _reset_logging(monkeypatch: pytest.MonkeyPatch) -> Generator[None, None, None]:
    """Isolate module-level structlog/stdlib state between tests."""
    monkeypatch.setattr(logging_conf, "_configured", False)
    structlog.reset_defaults()
    yield
    structlog.reset_defaults()


def test_secret_key_patterns_frozen() -> None:
    """SECRET_KEY_PATTERNS matches CONTRACT §4.11 verbatim."""
    assert SECRET_KEY_PATTERNS == (
        "api_key",
        "apikey",
        "token",
        "secret",
        "password",
        "authorization",
        "cookie",
    )


def test_redact_secret_keys() -> None:
    """Nested secret keys are redacted, plain keys kept, opaque objects str-ed."""
    data: dict[str, Any] = {
        "api_key": "sk-live-abc123",
        "nested": {"Authorization": "Bearer xyz", "plain": "keep"},
        "items": [{"db_password": "hunter2"}],  # noqa: S105 — test-only dummy key
        "opaque": _Opaque(),
    }
    result = redact(data)
    assert result["api_key"] == "***REDACTED***"
    assert result["nested"]["Authorization"] == "***REDACTED***"
    assert result["nested"]["plain"] == "keep"
    assert result["items"][0]["db_password"] == "***REDACTED***"  # noqa: S105
    assert result["opaque"] == "opaque-repr"


def test_redact_truncates_long() -> None:
    """Strings longer than max_len are truncated with the marker."""
    long_value = "x" * 1200
    result = redact({"payload": long_value})
    assert result["payload"].endswith("...(truncated)")
    assert len(result["payload"]) < 1200
    assert redact("y" * 50, max_len=10) == "y" * 10 + "...(truncated)"
    assert redact("short") == "short"
    assert redact(("a", {"api_key": "sk-x"})) == ["a", {"api_key": "***REDACTED***"}]


def test_redact_filter_on_log_record(capsys: pytest.CaptureFixture[str]) -> None:
    """AD-02: lock the processor path — rendered output must never contain raw secrets."""
    setup_logging(level="INFO")
    logger = structlog.get_logger("test_redact")
    logger.info("login_attempt_received", api_key="sk-dummy-secret-123")
    logger.info("request failed with api_key=sk-dummy-secret-456")
    captured = capsys.readouterr().err  # standalone convention: logs on stderr
    assert "sk-dummy-secret-123" not in captured
    assert "sk-dummy-secret-456" not in captured
    assert "***" in captured


def test_redact_processor_masks_event_dict() -> None:
    """Processor masks secret kwargs, nested values, and text fragments in event."""
    event_dict: dict[str, Any] = {
        "event": 'call upstream with "authorization": "Bearer top-secret"',
        "token": "tok-live-999",  # noqa: S105 — test-only dummy key
        "payload": {"session_cookie": "abc", "count": 3},
    }
    result = redact_processor(None, "info", event_dict)
    assert "Bearer top-secret" not in str(result["event"])
    assert result["token"] == "***REDACTED***"  # noqa: S105
    assert result["payload"]["session_cookie"] == "***REDACTED***"
    assert result["payload"]["count"] == 3


def test_setup_logging_still_idempotent() -> None:
    """Repeated setup_logging never stacks handlers/processors; redact in chain."""
    setup_logging(level="INFO")
    root = logging.getLogger()
    handler_count = len(root.handlers)
    processors_first = list(structlog.get_config()["processors"])
    setup_logging(level="INFO")
    setup_logging(level="INFO")
    assert len(root.handlers) == handler_count
    assert structlog.get_config()["processors"] == processors_first
    # redact_processor is part of the CLI standalone chain
    assert redact_processor in processors_first


def test_setup_logging_skips_when_host_configured() -> None:
    """AD-02: already configured by the host (FastAPI) -> idempotent skip."""
    structlog.configure(processors=[structlog.processors.add_log_level])
    setup_logging(level="INFO")
    assert logging_conf._configured is True  # noqa: SLF001 — flag introspection
    # Host chain untouched: no redact_processor appended by setup_logging
    assert redact_processor not in structlog.get_config()["processors"]
