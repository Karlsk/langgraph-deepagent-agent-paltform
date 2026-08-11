"""FastAPI router exposing workflow execution (spec-08 TC3, AD-10).

Entry/integration point of the workflow engine; this module is the sole
spec-08 component allowed to import ``app.core.*`` (AD-02 composition root
exception). It reuses the CLI's ``ApiResponse`` envelope and ``build_registry``
assembly; the synchronous ``execute_workflow`` runs inside a threadpool worker.
"""

from __future__ import annotations

import json
from pathlib import Path
from typing import Any

import structlog
from fastapi import APIRouter, Depends, Request
from fastapi.concurrency import run_in_threadpool
from fastapi.responses import JSONResponse

from app.core.config import settings
from app.core.limiter import limiter
from app.workflow.cli import DEFAULT_CONFIG_DIR, ApiResponse, build_registry
from app.workflow.logging_conf import redact_processor
from app.workflow.models import WorkflowNotFoundError
from app.workflow.registry import WorkflowRegistry

logger = structlog.get_logger(__name__)

router = APIRouter()

_registry_directory: Path = DEFAULT_CONFIG_DIR
_registry_cache: dict[str, WorkflowRegistry] = {}


def get_registry() -> WorkflowRegistry:
    """Provide a process-level registry built from the configured directory (DI)."""
    key = str(_registry_directory)
    if key not in _registry_cache:
        logger.info("workflow_registry_built", directory=key)
        _registry_cache[key] = build_registry(_registry_directory)
    return _registry_cache[key]


def _redacted_summary(message: str) -> str:
    """Redact secret-looking fragments from an error summary (H6)."""
    return str(redact_processor(None, "error", {"event": message})["event"])


def _envelope_response(response: ApiResponse, status_code: int) -> JSONResponse:
    return JSONResponse(status_code=status_code, content=json.loads(response.to_json()))


@router.post("/workflows/{workflow_id}/execute")
@limiter.limit(settings.RATE_LIMIT_ENDPOINTS["workflows_execute"][0])
async def execute_workflow(
    request: Request,
    workflow_id: str,
    payload: dict[str, Any] | None = None,
    registry: WorkflowRegistry = Depends(get_registry),
) -> JSONResponse:
    """Execute one registered workflow and return the unified envelope (AD-10).

    Args:
        request: FastAPI request object required by the slowapi limiter.
        workflow_id: Registered workflow to execute.
        payload: Optional JSON object passed as workflow input.
        registry: Process-level registry provided via dependency injection.

    Returns:
        JSONResponse carrying the ApiResponse envelope (200/404/500).
    """
    logger.info("api_workflow_execution_requested", workflow_id=workflow_id)
    input_data = payload or {}
    try:
        result = await run_in_threadpool(registry.execute_workflow, workflow_id, input_data)
    except WorkflowNotFoundError as exc:
        logger.warning("api_workflow_not_found", workflow_id=workflow_id)
        return _envelope_response(
            ApiResponse(success=False, error=_redacted_summary(f"workflow not found: {exc}")), 404
        )
    except Exception as exc:  # noqa: BLE001 — explicit catch-all layer per R6
        logger.exception("api_workflow_execution_failed", workflow_id=workflow_id)
        summary = f"workflow execution failed for '{workflow_id}': {type(exc).__name__}: {exc}"
        return _envelope_response(ApiResponse(success=False, error=_redacted_summary(summary)), 500)

    definition = registry.get_workflow_definition(workflow_id)
    response = ApiResponse(
        success=True,
        data=result.output,
        metadata={
            "workflow_id": workflow_id,
            "run_id": result.run_id,
            "duration_ms": result.duration_ms,
            "node_count": len(definition.nodes) if definition else 0,
        },
    )
    return _envelope_response(response, 200)
