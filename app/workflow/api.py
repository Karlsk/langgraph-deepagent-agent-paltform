"""FastAPI router exposing workflow execution (spec-08 TC3, AD-10).

Entry/integration point of the workflow engine; this module is the sole
spec-08 component allowed to import ``app.core.*`` (AD-02 composition root
exception). It reuses the CLI's frozen ``ApiResponse`` envelope internally
(CONTRACT §4.12) and projects it at the HTTP egress into the host unified
envelope ``{code, message, data}`` (see spec-08 §6 "HTTP wire 形态"); the
synchronous ``execute_workflow`` runs inside a threadpool worker.

The registry is injected by the host composition root on
``app.state.workflow_registry`` (spec-09 TC1, H4/G7): the engine module keeps
no module-level cache or mutable globals.
"""

from __future__ import annotations

from typing import Any

import structlog
from fastapi import APIRouter, Request
from fastapi.concurrency import run_in_threadpool
from fastapi.responses import JSONResponse

from app.core.config import settings
from app.core.limiter import limiter
from app.schemas.base import ApiResponse as HostApiResponse
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


def _host_envelope_content(response: ApiResponse, status_code: int) -> dict[str, Any]:
    """Build the host unified envelope content ``{code, message, data}`` (spec-08 §6).

    Success: ``code`` mirrors the HTTP status and ``message`` is ``"success"``; when the
    workflow output is a dict the run metadata is folded into it, otherwise it travels
    beside the raw output under ``{"result": ..., "metadata": ...}``.
    Errors: ``message`` carries the redacted error summary, ``data`` is null.
    """
    if response.success:
        data: Any
        if isinstance(response.data, dict):
            data = {**response.data, "metadata": response.metadata}
        else:
            data = {"result": response.data, "metadata": response.metadata}
        return {"code": status_code, "message": "success", "data": data}
    return {"code": status_code, "message": response.error or "error", "data": None}


def _project_to_host_envelope(response: ApiResponse, status_code: int) -> JSONResponse:
    """Egress mapping: internal ApiResponse (CONTRACT §4.12) -> host unified envelope.

    The CLI stdout keeps the untouched §4.12 envelope (regression line, test_cli.py).
    """
    return JSONResponse(status_code=status_code, content=_host_envelope_content(response, status_code))


@router.post(
    "/workflows/{workflow_id}/execute",
    # Documentation-only contract (spec-08 §6): the route returns JSONResponse
    # instances, so FastAPI skips serialization and the wire shape is produced
    # exclusively by ``_project_to_host_envelope``; ``response_model``/``responses``
    # never alter runtime behavior, they only express the envelope in OpenAPI.
    response_model=HostApiResponse[dict[str, Any]],
    responses={
        404: {
            "model": HostApiResponse[None],
            "description": "Unknown workflow_id: envelope with code=404, message=redacted summary, data=null",
        },
        500: {
            "model": HostApiResponse[None],
            "description": "Execution failure: envelope with code=500, message=redacted summary, data=null",
        },
    },
)
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
        JSONResponse carrying the host unified envelope ``{code, message, data}`` (200/404/500).
    """
    logger.info("api_workflow_execution_requested", workflow_id=workflow_id)
    input_data = payload or {}
    try:
        # Resolved inside the try block so a missing injection lands in the envelope (R6).
        registry = get_registry(request)
        result = await run_in_threadpool(registry.execute_workflow, workflow_id, input_data)
    except WorkflowNotFoundError as exc:
        logger.warning("api_workflow_not_found", workflow_id=workflow_id)
        return _project_to_host_envelope(
            ApiResponse(success=False, error=_redacted_summary(f"workflow not found: {exc}")), 404
        )
    except Exception as exc:  # noqa: BLE001 — explicit catch-all layer per R6
        logger.exception("api_workflow_execution_failed", workflow_id=workflow_id)
        summary = f"workflow execution failed for '{workflow_id}': {type(exc).__name__}: {exc}"
        return _project_to_host_envelope(ApiResponse(success=False, error=_redacted_summary(summary)), 500)

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
    return _project_to_host_envelope(response, 200)
