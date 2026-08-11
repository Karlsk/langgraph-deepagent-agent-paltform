"""FastAPI router exposing workflow execution (spec-08 TC3, AD-10).

Entry/integration point of the workflow engine; this module is the sole
spec-08 component allowed to import ``app.core.*`` (AD-02 composition root
exception). It reuses the CLI's ``ApiResponse`` envelope; the synchronous
``execute_workflow`` runs inside a threadpool worker.

The registry is injected by the host composition root on
``app.state.workflow_registry`` (spec-09 TC1, H4/G7): the engine module keeps
no module-level cache or mutable globals.
"""

from __future__ import annotations

import json
from typing import Any

import structlog
from fastapi import APIRouter, Request
from fastapi.concurrency import run_in_threadpool
from fastapi.responses import JSONResponse

from app.core.config import settings
from app.core.limiter import limiter
from app.workflow.cli import ApiResponse
from app.workflow.logging_conf import redact_processor
from app.workflow.models import WorkflowNotFoundError
from app.workflow.registry import WorkflowRegistry

logger = structlog.get_logger(__name__)

router = APIRouter()


def get_registry(request: Request) -> WorkflowRegistry:
    """Provide the host-injected registry from app.state (H4/G7: no module-level cache)."""
    registry = getattr(request.app.state, "workflow_registry", None)
    if registry is None:
        msg = "app.state.workflow_registry is not set; the host must inject a WorkflowRegistry"
        raise RuntimeError(msg)
    return registry


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
) -> JSONResponse:
    """Execute one registered workflow and return the unified envelope (AD-10).

    Args:
        request: FastAPI request object required by the slowapi limiter and registry lookup.
        workflow_id: Registered workflow to execute.
        payload: Optional JSON object passed as workflow input.

    Returns:
        JSONResponse carrying the ApiResponse envelope (200/404/500).
    """
    logger.info("api_workflow_execution_requested", workflow_id=workflow_id)
    input_data = payload or {}
    try:
        # Resolved inside the try block so a missing injection lands in the envelope (R6).
        registry = get_registry(request)
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
