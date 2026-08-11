"""Command-line entrypoint for the workflow engine (spec-08 TC2, AD-10).

Loads workflow definitions from a directory, registers them, executes one
workflow, and prints a unified JSON response envelope to stdout. Structured
logs go to stderr (see ``logging_conf.setup_logging``), keeping stdout
machine-readable.

This module is the sanctioned T201/print exception point (G8) and the sole
spec-08 entry module allowed to stay free of ``app.core.*`` imports.
"""

from __future__ import annotations

import argparse
import json
from dataclasses import dataclass, field
from pathlib import Path
from typing import Any

import structlog
from dotenv import load_dotenv

from app.workflow.logging_conf import redact_processor, setup_logging
from app.workflow.models import WorkflowEngineError
from app.workflow.registry import WorkflowRegistry, load_definitions_from_dir

logger = structlog.get_logger(__name__)

DEFAULT_CONFIG_DIR = Path("app/workflow/config/examples")


@dataclass
class ApiResponse:
    """Unified response envelope shared by the CLI and the optional API (AD-10)."""

    success: bool
    data: Any = None
    error: str | None = None
    metadata: dict[str, Any] = field(default_factory=dict)

    def to_json(self) -> str:
        """Serialize the envelope with ensure_ascii=False (CONTRACT §4.12)."""
        payload = {
            "success": self.success,
            "data": self.data,
            "error": self.error,
            "metadata": self.metadata,
        }
        return json.dumps(payload, ensure_ascii=False, default=str)


def build_registry(directory: str | Path) -> WorkflowRegistry:
    """Load every definition in ``directory`` and register it (shared with api.py)."""
    registry = WorkflowRegistry()
    for definition in load_definitions_from_dir(directory):
        registry.register_workflow(definition)
    return registry


def build_parser() -> argparse.ArgumentParser:
    """Build the CLI parser: single ``run`` subcommand (AD-10)."""
    parser = argparse.ArgumentParser(prog="app.workflow", description="Declarative workflow engine CLI")
    subparsers = parser.add_subparsers(dest="command", required=True)
    run_parser = subparsers.add_parser("run", help="Load definitions from a directory and run one workflow")
    run_parser.add_argument("--dir", default=str(DEFAULT_CONFIG_DIR), help="Directory with YAML workflow definitions")
    run_parser.add_argument("--workflow", required=True, help="workflow_id to execute")
    run_parser.add_argument("--input", default="{}", help="JSON object passed as workflow input")
    run_parser.add_argument("--log-level", default="INFO", help="Root log level name")
    run_parser.add_argument("--json-log", action="store_true", help="Render structured logs as JSON")
    return parser


def _redacted_summary(message: str) -> str:
    """Redact secret-looking fragments from an error summary (H6)."""
    return str(redact_processor(None, "error", {"event": message})["event"])


def _print_failure(error: str) -> None:
    print(ApiResponse(success=False, error=_redacted_summary(error)).to_json())  # noqa: T201 — envelope point


def main(argv: list[str] | None = None) -> int:
    """Run one workflow end to end; return 0 on success, 1 on failure (AD-10)."""
    args = build_parser().parse_args(argv)
    setup_logging(level=args.log_level, json_output=args.json_log)
    load_dotenv()
    logger.info("cli_run_requested", workflow_id=args.workflow, directory=args.dir)

    try:
        input_data = json.loads(args.input)
    except json.JSONDecodeError as exc:
        logger.exception("cli_input_parse_failed", workflow_id=args.workflow)
        _print_failure(f"invalid --input JSON: {exc}")
        return 1
    if not isinstance(input_data, dict):
        logger.warning("cli_input_not_object", workflow_id=args.workflow)
        _print_failure("invalid --input: expected a JSON object")
        return 1

    try:
        registry = build_registry(args.dir)
        result = registry.execute_workflow(args.workflow, input_data)
    except WorkflowEngineError as exc:
        logger.exception("cli_workflow_engine_error", workflow_id=args.workflow)
        _print_failure(f"workflow engine error for '{args.workflow}': {exc}")
        return 1
    except Exception as exc:  # noqa: BLE001 — explicit catch-all layer per R6
        logger.exception("cli_unexpected_error", workflow_id=args.workflow)
        _print_failure(f"unexpected error while running '{args.workflow}': {type(exc).__name__}: {exc}")
        return 1

    definition = registry.get_workflow_definition(args.workflow)
    response = ApiResponse(
        success=True,
        data=result.output,
        metadata={
            "workflow_id": args.workflow,
            "run_id": result.run_id,
            "duration_ms": result.duration_ms,
            "node_count": len(definition.nodes) if definition else 0,
        },
    )
    print(response.to_json())  # noqa: T201 — envelope output is the G8 exception point
    return 0
