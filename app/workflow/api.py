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

from typing import Any, Literal

import structlog
from fastapi import APIRouter, Body, Depends, Request
from fastapi.concurrency import run_in_threadpool
from fastapi.responses import JSONResponse
from pydantic import ValidationError
from yaml import safe_dump as yaml_safe_dump

from app.api.v1.auth import get_current_user
from app.core.config import settings
from app.core.limiter import limiter
from app.models.user import User
from app.schemas.base import ApiResponse as HostApiResponse
from app.workflow.auth import require_workflow_admin
from app.workflow.cli import ApiResponse
from app.workflow.logging_conf import redact, redact_processor
from app.workflow.models import ExecutionLog, WorkflowDefinition, WorkflowEngineError, WorkflowNotFoundError
from app.workflow.registry import WorkflowRegistry
from app.workflow.store import delete_definition_yaml, save_definition_yaml

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
        elif isinstance(response.data, list):
            data = response.data
        else:
            data = {"result": response.data, "metadata": response.metadata}
        return {"code": status_code, "message": "success", "data": data}
    return {"code": status_code, "message": response.error or "error", "data": None}


def _project_to_host_envelope(response: ApiResponse, status_code: int) -> JSONResponse:
    """Egress mapping: internal ApiResponse (CONTRACT §4.12) -> host unified envelope.

    The CLI stdout keeps the untouched §4.12 envelope (regression line, test_cli.py).
    """
    return JSONResponse(status_code=status_code, content=_host_envelope_content(response, status_code))


def _workflow_summary(registry: WorkflowRegistry, wf_id: str) -> dict[str, Any]:
    """Build a workflow summary dict (CONTRACT §4.13)."""
    definition = registry.get_workflow_definition(wf_id)
    return {
        "workflow_id": wf_id,
        "node_count": len(definition.nodes) if definition else 0,
        "entry_point": definition.entry_point if definition else "",
    }


def _definition_view(definition: WorkflowDefinition) -> dict[str, Any]:
    """Shared projection: exclude execution_history, both formats consume this dict."""
    return definition.model_dump(mode="json", exclude={"execution_history"})


def _definition_to_json(definition: WorkflowDefinition) -> dict[str, Any]:
    """CONTRACT §4.13 frozen: JSON projection (delegates to _definition_view)."""
    return _definition_view(definition)


def _definition_to_yaml_text(definition: WorkflowDefinition) -> str:
    """CONTRACT §4.13 frozen: YAML text via yaml.safe_dump for read-only preview."""
    return yaml_safe_dump(_definition_view(definition), allow_unicode=True, sort_keys=False)


def _serialize_execution_logs(logs: list[ExecutionLog]) -> list[dict[str, Any]]:
    """Serialize execution logs with redaction (H6) and truncation (max_len=500)."""
    return [redact(log.model_dump(mode="json"), max_len=500) for log in logs]


ALLOWED_NODE_TYPES: frozenset[str] = frozenset({"llm", "http"})


def _validate_definition_payload(payload: dict[str, Any], workflow_id: str) -> WorkflowDefinition:
    """Parse and validate a PUT body: pydantic → id match → node-type whitelist (S15).

    Raises:
        ValidationError: If pydantic model validation fails.
        ValueError: If workflow_id mismatches or a node type is not whitelisted.
    """
    definition = WorkflowDefinition.model_validate(payload)
    if definition.workflow_id != workflow_id:
        msg = f"body workflow_id '{definition.workflow_id}' does not match path id '{workflow_id}'"
        raise ValueError(msg)
    for node in definition.nodes:
        if node.type not in ALLOWED_NODE_TYPES:
            msg = f"node type '{node.type}' is not allowed; allowed types: {sorted(ALLOWED_NODE_TYPES)}"
            raise ValueError(msg)
    return definition


@router.get(
    "/workflows",
    response_model=HostApiResponse[list[dict[str, Any]]],
    responses={
        500: {
            "model": HostApiResponse[None],
            "description": "Missing registry injection: envelope with code=500, data=null",
        },
    },
)
@limiter.limit(settings.RATE_LIMIT_ENDPOINTS["workflows_list"][0])
async def list_workflows(
    request: Request,
    _user: User = Depends(get_current_user),
) -> JSONResponse:
    """List all registered workflows as summaries (CONTRACT §4.13).

    Args:
        request: FastAPI request object required by the slowapi limiter and registry lookup.
        _user: Current authenticated user (auth gate).

    Returns:
        JSONResponse carrying the host unified envelope with ``data`` as a list of summaries.
    """
    try:
        registry = get_registry(request)
    except RuntimeError as exc:
        logger.exception("api_workflow_registry_missing")
        return _project_to_host_envelope(ApiResponse(success=False, error=str(exc)), 500)
    summaries = [_workflow_summary(registry, wf_id) for wf_id in registry.list_workflows()]
    return _project_to_host_envelope(ApiResponse(success=True, data=summaries), 200)


@router.get(
    "/workflows/capabilities",
    response_model=HostApiResponse[dict[str, Any]],
)
@limiter.limit(settings.RATE_LIMIT_ENDPOINTS["workflows_list"][0])
async def workflow_capabilities(
    request: Request,
    user: User = Depends(get_current_user),
) -> JSONResponse:
    """Return current user's workflow editing capabilities (spec-19).

    Args:
        request: FastAPI request object (limiter).
        user: Current authenticated user.

    Returns:
        JSONResponse with ``{can_edit: bool}`` based on admin allowlist.
    """
    can_edit = user.username in settings.WORKFLOW_ADMIN_USERNAMES
    return _project_to_host_envelope(ApiResponse(success=True, data={"can_edit": can_edit}), 200)


@router.get(
    "/workflows/{workflow_id}",
    response_model=HostApiResponse[dict[str, Any]],
    responses={
        404: {
            "model": HostApiResponse[None],
            "description": "Unknown workflow_id: envelope with code=404, data=null",
        },
        500: {
            "model": HostApiResponse[None],
            "description": "Missing registry injection: envelope with code=500, data=null",
        },
    },
)
@limiter.limit(settings.RATE_LIMIT_ENDPOINTS["workflows_detail"][0])
async def get_workflow(
    request: Request,
    workflow_id: str,
    format: Literal["json", "yaml"] = "json",
    _user: User = Depends(get_current_user),
) -> JSONResponse:
    """Return a single workflow definition (CONTRACT §4.13).

    Args:
        request: FastAPI request (limiter + registry lookup).
        workflow_id: Registered workflow to inspect.
        format: ``"json"`` (default) or ``"yaml"`` for read-only preview.
        _user: Current authenticated user (auth gate).

    Returns:
        JSONResponse carrying the host unified envelope.
    """
    try:
        registry = get_registry(request)
    except RuntimeError as exc:
        logger.exception("api_workflow_registry_missing")
        return _project_to_host_envelope(ApiResponse(success=False, error=str(exc)), 500)

    definition = registry.get_workflow_definition(workflow_id)
    if definition is None:
        logger.warning("api_workflow_definition_not_found", workflow_id=workflow_id)
        return _project_to_host_envelope(
            ApiResponse(success=False, error=_redacted_summary(f"workflow not found: {workflow_id}")),
            404,
        )

    if format == "yaml":
        data: dict[str, Any] = {"yaml_text": _definition_to_yaml_text(definition)}
    else:
        data = _definition_to_json(definition)

    return _project_to_host_envelope(ApiResponse(success=True, data=data), 200)


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
    _user: User = Depends(get_current_user),
) -> JSONResponse:
    """Execute one registered workflow and return the unified envelope (AD-10).

    Args:
        request: FastAPI request object required by the slowapi limiter and registry lookup.
        workflow_id: Registered workflow to execute.
        payload: Optional JSON object passed as workflow input.
        _user: Current authenticated user (auth gate).

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
            "execution_logs": _serialize_execution_logs(result.execution_logs),
        },
    )
    return _project_to_host_envelope(response, 200)


@router.put(
    "/workflows/{workflow_id}",
    response_model=HostApiResponse[dict[str, Any]],
    responses={
        422: {
            "model": HostApiResponse[None],
            "description": "Validation or build-time error: envelope with code=422, message=redacted summary, data=null",
        },
        500: {
            "model": HostApiResponse[None],
            "description": "Persistence failure: envelope with code=500, message=redacted summary, data=null",
        },
    },
)
@limiter.limit(settings.RATE_LIMIT_ENDPOINTS["workflows_save"][0])
async def save_workflow(
    workflow_id: str,
    request: Request,
    payload: dict[str, Any] = Body(...),
    registry: WorkflowRegistry = Depends(get_registry),
    _admin: User = Depends(require_workflow_admin),
) -> JSONResponse:
    """Full-replace register a workflow definition (spec-16, S13 atomic replacement).

    Processing order: parse → id match → whitelist → register → persist → return.
    On persist failure the registry entry is rolled back to keep registry = disk.
    """
    try:
        definition = _validate_definition_payload(payload, workflow_id)
    except ValidationError as exc:
        logger.warning("api_save_workflow_validation_failed", workflow_id=workflow_id)
        return _project_to_host_envelope(
            ApiResponse(success=False, error=_redacted_summary(f"invalid definition: {exc}")), 422
        )
    except ValueError as exc:
        logger.warning("api_save_workflow_id_mismatch_or_blocked", workflow_id=workflow_id)
        return _project_to_host_envelope(
            ApiResponse(success=False, error=_redacted_summary(str(exc))), 422
        )

    try:
        await run_in_threadpool(registry.register_workflow, definition)
    except (ValueError, WorkflowEngineError) as exc:
        logger.warning("api_save_workflow_build_failed", workflow_id=workflow_id)
        return _project_to_host_envelope(
            ApiResponse(success=False, error=_redacted_summary(f"build-time error: {exc}")), 422
        )

    try:
        await run_in_threadpool(save_definition_yaml, definition)
    except Exception as exc:  # noqa: BLE001 — rollback on any persist failure
        logger.exception("api_save_workflow_persist_failed", workflow_id=workflow_id)
        registry.delete_workflow(workflow_id)
        summary = f"persistence failed for '{workflow_id}': {type(exc).__name__}: {exc}"
        return _project_to_host_envelope(ApiResponse(success=False, error=_redacted_summary(summary)), 500)

    return _project_to_host_envelope(ApiResponse(success=True, data=_definition_view(definition)), 200)


def _remove_workflow(registry: WorkflowRegistry, workflow_id: str) -> bool:
    """Orchestrate memory + disk deletion; return True if removed from registry (C6/H7)."""
    removed = registry.delete_workflow(workflow_id)
    if removed:
        delete_definition_yaml(workflow_id)
    return removed


@router.delete(
    "/workflows/{workflow_id}",
    response_model=HostApiResponse[None],
    responses={
        404: {
            "model": HostApiResponse[None],
            "description": "Unknown workflow_id: envelope with code=404, data=null",
        },
    },
)
@limiter.limit(settings.RATE_LIMIT_ENDPOINTS["workflows_delete"][0])
async def delete_workflow_endpoint(
    workflow_id: str,
    request: Request,
    registry: WorkflowRegistry = Depends(get_registry),
    _admin: User = Depends(require_workflow_admin),
) -> JSONResponse:
    """Delete a workflow from registry and disk; 404 if unknown (spec-18)."""
    logger.info("api_workflow_delete_requested", workflow_id=workflow_id)
    removed = await run_in_threadpool(_remove_workflow, registry, workflow_id)
    if not removed:
        logger.warning("api_workflow_not_found_for_delete", workflow_id=workflow_id)
        return _project_to_host_envelope(
            ApiResponse(success=False, error=_redacted_summary(f"workflow not found: {workflow_id}")),
            404,
        )
    return _project_to_host_envelope(ApiResponse(success=True, data=None), 200)
