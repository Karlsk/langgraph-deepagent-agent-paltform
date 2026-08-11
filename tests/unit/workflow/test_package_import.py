"""Smoke tests for the app.workflow package skeleton (spec-00 TC3)."""

import logging
import runpy
import sys

import pytest
import structlog

import app.workflow
from app.workflow import cli, logging_conf
from app.workflow.logging_conf import setup_logging


@pytest.mark.unit
def test_import_package() -> None:
    """Package imports cheaply and exposes a string __version__."""
    assert isinstance(app.workflow.__version__, str)
    assert app.workflow.__version__ == "0.1.0"


@pytest.mark.unit
def test_setup_logging_idempotent() -> None:
    """Calling setup_logging twice must not stack root handlers."""
    setup_logging()
    handlers_after_first = len(logging.root.handlers)
    setup_logging()
    assert len(logging.root.handlers) == handlers_after_first


@pytest.mark.unit
def test_setup_logging_level() -> None:
    """setup_logging(level="DEBUG") sets the root logger effective level."""
    # Reset both idempotency guards (module flag + structlog global config)
    # so the level argument is actually applied.
    structlog.reset_defaults()
    logging_conf._configured = False
    logging_conf.setup_logging(level="DEBUG")
    assert logging.getLogger().getEffectiveLevel() == logging.DEBUG


@pytest.mark.unit
def test_module_entrypoint_exits_with_cli_code(monkeypatch: pytest.MonkeyPatch) -> None:
    """Module entrypoint (python -m app.workflow) delegates to cli.main and propagates its exit code (spec-09 TC2)."""
    monkeypatch.setattr(sys, "argv", ["app.workflow", "run", "--workflow", "demo_minimal"])
    monkeypatch.setattr(cli, "main", lambda argv=None: 0)
    with pytest.raises(SystemExit) as exc_info:
        runpy.run_module("app.workflow", run_name="__main__")
    assert exc_info.value.code == 0
