"""Minimal idempotent structlog bootstrap for the workflow engine.

Per AD-02 v2, this module only serves the standalone CLI entrypoint and
bare test environments. In the FastAPI deployment the host owns logging
configuration (``app.core.logging``); ``setup_logging`` detects an already
configured structlog and skips, so it is effectively a no-op there.

This module must never import anything from ``app.core`` / ``app.api`` /
``app.services`` (engine self-containment red line).
"""

import logging

import structlog

_configured: bool = False


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

    renderer = structlog.processors.JSONRenderer() if json_output else structlog.dev.ConsoleRenderer()
    structlog.configure(
        processors=[
            structlog.processors.add_log_level,
            structlog.processors.TimeStamper(fmt="iso"),
            structlog.processors.format_exc_info,
            renderer,
        ],
        cache_logger_on_first_use=True,
    )
    logging.basicConfig(level=level)
    # basicConfig is a no-op when root already has handlers; bind level explicitly.
    logging.getLogger().setLevel(level)
    _configured = True
