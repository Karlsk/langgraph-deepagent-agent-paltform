"""SDN alert inspection entrypoint: ``python -m app.sdn`` (delegates to the engine CLI).

Two process-local adjustments, engine stays untouched:

- recursion limit: each alert costs 5 loop steps, so the langgraph default of
  25 is too small for realistic alert counts; raised via the official
  ``LANGGRAPH_DEFAULT_RECURSION_LIMIT`` env switch BEFORE langgraph imports.
- SSL: the SDN controller uses a self-signed certificate and the engine's
  HTTPNode exposes no verify switch, so ``httpx.request`` is wrapped here to
  relax verify for this entry process ONLY (do not reuse elsewhere).

Run (credentials live in ``state_schema`` defaults of the config YAML)::

    uv run python -m app.sdn run --workflow sdn_alert_inspection --input '{}'
"""

from __future__ import annotations

import os
import sys
from pathlib import Path
from typing import Any

# Must precede any langgraph import: read once at langgraph._internal._config import time.
os.environ.setdefault("LANGGRAPH_DEFAULT_RECURSION_LIMIT", "200")

import httpx  # noqa: E402

from app.workflow.cli import main as workflow_main  # noqa: E402

CONFIG_DIR = Path(__file__).resolve().parent / "config"

_real_request = httpx.request


def _request_no_verify(method: str, url: str, **kwargs: Any) -> httpx.Response:
    """Forward to httpx.request with verify relaxed for self-signed controllers."""
    kwargs.setdefault("verify", False)
    return _real_request(method, url, **kwargs)


# Process-local SSL relaxation (see module docstring); affects this entry only.
httpx.request = _request_no_verify  # pyright: ignore[reportAttributeAccessIssue] — deliberate process-local patch


def main(argv: list[str] | None = None) -> int:
    """Delegate to the workflow CLI with --dir fixed to app/sdn/config."""
    args = list(sys.argv[1:] if argv is None else argv)
    if "--dir" not in args and args and args[0] == "run":
        args[1:1] = ["--dir", str(CONFIG_DIR)]
    return workflow_main(args)


raise SystemExit(main())
