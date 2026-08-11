"""Minimal idempotent structlog bootstrap for the workflow engine.

Per AD-02 v2, this module only serves the standalone CLI entrypoint and
bare test environments. In the FastAPI deployment the host owns logging
configuration (``app.core.logging``); ``setup_logging`` detects an already
configured structlog and skips, so it is effectively a no-op there.

In the standalone scenario it also installs ``redact_processor`` into the
processor chain (AD-02 v2); in the FastAPI scenario the host composition
root registers it instead.

This module must never import anything from ``app.core`` / ``app.api`` /
``app.services`` (engine self-containment red line).
"""

from __future__ import annotations

import json
import logging
import re
import sys
from typing import Any, cast

import structlog
from structlog.typing import Processor

_configured: bool = False

SECRET_KEY_PATTERNS: tuple[str, ...] = (
    "api_key",
    "apikey",
    "token",
    "secret",
    "password",
    "authorization",
    "cookie",
)

_REDACTED = "***REDACTED***"
_TRUNCATION_MARK = "...(truncated)"

_ALTERNATION = "|".join(re.escape(p) for p in SECRET_KEY_PATTERNS)
_SECRET_KEY_RE = re.compile(_ALTERNATION, re.IGNORECASE)
# `key=value` and `"key": "..."` fragments embedded in free text (H6).
_KV_PLAIN_RE = re.compile(rf"((?:{_ALTERNATION})\s*=\s*)\S+", re.IGNORECASE)
_KV_QUOTED_RE = re.compile(rf'("(?:{_ALTERNATION})"\s*:\s*)"[^"]*"', re.IGNORECASE)


def _is_secret_key(key: str) -> bool:
    """Case-insensitive substring match against SECRET_KEY_PATTERNS."""
    return _SECRET_KEY_RE.search(key) is not None


def _redact_text(text: str) -> str:
    """Mask secret-looking `key=value` / `"key": "..."` fragments in free text."""
    text = _KV_QUOTED_RE.sub(r'\1"***"', text)
    return _KV_PLAIN_RE.sub(r"\1***", text)


def redact(data: Any, *, max_len: int = 500) -> Any:
    """Recursively redact secrets from a JSON-like structure.

    Values whose key matches SECRET_KEY_PATTERNS (case-insensitive) become
    ``***REDACTED***``; strings longer than ``max_len`` are truncated with a
    ``...(truncated)`` marker; objects that are not JSON-serializable fall
    back to ``default=str`` normalization.
    """
    if isinstance(data, dict):
        return {
            key: _REDACTED if isinstance(key, str) and _is_secret_key(key) else redact(value, max_len=max_len)
            for key, value in data.items()
        }
    if isinstance(data, list):
        return [redact(item, max_len=max_len) for item in data]
    if isinstance(data, tuple):
        return [redact(item, max_len=max_len) for item in data]
    if isinstance(data, str):
        if len(data) > max_len:
            return data[:max_len] + _TRUNCATION_MARK
        return data
    if data is None or isinstance(data, (bool, int, float)):
        return data
    # Not JSON-serializable: normalize via default=str, then recurse.
    return redact(json.loads(json.dumps(data, default=str)), max_len=max_len)


def redact_processor(logger: Any, method_name: str, event_dict: dict[str, Any]) -> dict[str, Any]:
    """Structlog processor applying redaction to the event and all kwargs [AD-02].

    Replaces the original stdlib RedactFilter from the pre-reimpl design;
    registered by ``setup_logging`` in the standalone CLI scenario, and by
    the host composition root in the FastAPI scenario (AD-02 v2).
    """
    redacted: dict[str, Any] = {}
    for key, value in event_dict.items():
        if isinstance(key, str) and _is_secret_key(key):
            redacted[key] = _REDACTED
        elif isinstance(value, str):
            masked = _redact_text(value)
            redacted[key] = masked if key == "event" else redact(masked)
        else:
            redacted[key] = redact(value)
    return redacted


def setup_logging(level: str = "INFO", *, json_output: bool = False) -> None:
    """Configure structlog and stdlib logging for standalone usage.

    Idempotent with a double guard: a module-level flag plus
    ``structlog.is_configured()``. If structlog was already configured
    (e.g. by ``app.core.logging`` in the FastAPI integration scenario),
    the flag is set and the call returns without touching anything.

    Args:
        level: Root log level name, e.g. ``"INFO"`` or ``"DEBUG"``.
        json_output: Render log entries as JSON instead of console format.
    """
    global _configured
    if _configured:
        return
    if structlog.is_configured():
        _configured = True
        return

    # Standalone CLI convention: logs go to stderr, stdout stays machine-readable.
    renderer = structlog.processors.JSONRenderer() if json_output else structlog.dev.ConsoleRenderer()
    # cast keeps the frozen redact_processor signature (§4.11) while satisfying Processor.
    processors = cast(
        "list[Processor]",
        [
            structlog.processors.add_log_level,
            structlog.processors.TimeStamper(fmt="iso"),
            structlog.processors.format_exc_info,
            redact_processor,
            renderer,
        ],
    )
    structlog.configure(
        processors=processors,
        logger_factory=structlog.PrintLoggerFactory(file=sys.stderr),
        # No first-use caching: keeps short-lived CLI processes honest and
        # avoids loggers pinned to a stale stream (bare-test friendliness).
        cache_logger_on_first_use=False,
    )
    logging.basicConfig(level=level)
    # basicConfig is a no-op when root already has handlers; bind level explicitly.
    logging.getLogger().setLevel(level)
    _configured = True
