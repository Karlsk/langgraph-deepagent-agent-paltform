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
from fastapi import APIRouter, Body, Depends, Query, Request
from fastapi.concurrency import run_in_threadpool
from fastapi.responses import JSONResponse
from pydantic import ValidationError
from sqlalchemy import func
from sqlmodel import Session as DBSession
from sqlmodel import col, select
from yaml import safe_dump as yaml_safe_dump

from app.api.v1.agent_assets_common import get_db_session
from app.api.v1.auth import get_current_user
from app.core.config import settings
from app.core.limiter import limiter
from app.models.user import User
from app.models.workflow_run import WorkflowRun
from app.schemas.base import ApiResponse as HostApiResponse
from app.schemas.base import PageResult
from app.services.llm.provider_service import validate_reference
from app.workflow.auth import require_workflow_admin
from app.workflow.cli import ApiResponse
from app.workflow.logging_conf import redact, redact_processor
from app.workflow.models import (
    ExecutionLog,
    NodeDefinition,
    WorkflowDefinition,
    WorkflowEngineError,
    WorkflowNotFoundError,
    WorkflowValidationError,
)
from app.workflow.registry import WorkflowRegistry
from app.workflow.sandbox import validate_code_ast
from app.workflow.security import validate_http_url
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
    error_data = {"metadata": response.metadata} if response.metadata else None
    return {"code": status_code, "message": response.error or "error", "data": error_data}


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


_MAX_FIELD_LEN = 10_000


def _truncate_for_storage(data: Any) -> Any:
    """Cap string values at _MAX_FIELD_LEN for safe DB storage."""
    if isinstance(data, str):
        return data[:_MAX_FIELD_LEN] if len(data) > _MAX_FIELD_LEN else data
    if isinstance(data, dict):
        return {k: _truncate_for_storage(v) for k, v in data.items()}
    if isinstance(data, list):
        return [_truncate_for_storage(item) for item in data]
    return data


def _persist_workflow_run(
    db: DBSession,
    *,
    workflow_id: str,
    run_id: str,
    status: str,
    input_data: dict[str, Any],
    output_data: dict[str, Any],
    error_message: str | None,
    execution_logs: list[dict[str, Any]],
    duration_ms: float,
    node_count: int,
    created_by: str | None,
) -> None:
    """Persist a WorkflowRun row; never raises (logs and swallows DB errors)."""
    try:
        row = WorkflowRun(
            workflow_id=workflow_id,
            run_id=run_id,
            status=status,
            input_data=_truncate_for_storage(input_data),
            output_data=_truncate_for_storage(output_data),
            error_message=error_message,
            execution_logs=_truncate_for_storage(execution_logs),
            duration_ms=duration_ms,
            node_count=node_count,
            created_by=created_by,
        )
        db.add(row)
        db.commit()
    except Exception:  # noqa: BLE001 — persist is best-effort
        logger.exception("workflow_run_persist_failed", workflow_id=workflow_id, run_id=run_id)
        db.rollback()


ALLOWED_NODE_TYPES: frozenset[str] = frozenset({"llm", "http", "python", "subworkflow", "react"})
# `subworkflow` needs no check of its own here (S18 structural validation):
# SubWorkflowNodeConfig already rejects a missing/blank/non-string workflow_id, and
# that runs during register_workflow below — inside this same handler, so the 422
# S18 requires still comes from one source of truth instead of two.
# `python` does need _enforce_python_node_policy because PythonNodeConfig accepts
# any code string, and because sandboxed must be forced regardless of the body.


def _enforce_python_node_policy(node: NodeDefinition) -> None:
    """S18: admit a python node over HTTP only as sandboxed, statically-checked code.

    Mutates ``node.config`` to force ``sandboxed=true`` so both the registry
    entry and the persisted YAML carry it — the flag is a security property,
    not a user preference, so the request body never decides it.

    Raises:
        ValueError: If the node uses ``entry`` mode or has no usable ``code``.
        WorkflowValidationError: If the code fails the sandbox AST pre-check.
    """
    config = node.config
    if "entry" in config:
        msg = f"node '{node.name}': python 'entry' mode cannot be registered over HTTP; use 'code'"
        raise ValueError(msg)
    code = config.get("code")
    if not isinstance(code, str) or not code.strip():
        msg = f"node '{node.name}': python node requires a non-empty 'code' string"
        raise ValueError(msg)
    validate_code_ast(code)
    config["sandboxed"] = True


def _validate_http_node_url(config: dict[str, Any], *, allow_private_networks: bool = True) -> None:
    """Run the spec-20 SSRF check, skipping mock runs and unresolved templates.

    ``"{" in url`` marks a template: its host does not exist yet, so registration
    has nothing to resolve. Skipping here opens no hole — the execution-time check
    on the *rendered* URL (``http_node.py``, before the request is sent) applies
    to literal and template URLs alike.
    """
    if config.get("mock_enabled", False):
        return
    url = config.get("url")
    if not url or "{" in url:
        return
    validate_http_url(url, allow_private_networks=allow_private_networks)


def _validate_definition_payload(payload: dict[str, Any], workflow_id: str) -> WorkflowDefinition:
    """Parse and validate a PUT body: pydantic → id match → node-type whitelist → per-type guards.

    Per-type guards are the SSRF check for http nodes (spec-20, skipped for
    template URLs) and the sandbox policy for python nodes (S18).

    Raises:
        ValidationError: If pydantic model validation fails.
        ValueError: If workflow_id mismatches, a node type is not whitelisted,
            or a python node violates the S18 code-only rule.
        WorkflowValidationError: If an HTTP node URL fails SSRF validation (spec-20)
            or python node code fails the AST pre-check (S18/S22).
    """
    definition = WorkflowDefinition.model_validate(payload)
    if definition.workflow_id != workflow_id:
        msg = f"body workflow_id '{definition.workflow_id}' does not match path id '{workflow_id}'"
        raise ValueError(msg)
    for node in definition.nodes:
        if node.type not in ALLOWED_NODE_TYPES:
            msg = f"node type '{node.type}' is not allowed; allowed types: {sorted(ALLOWED_NODE_TYPES)}"
            raise ValueError(msg)
        if node.type == "http":
            _validate_http_node_url(node.config, allow_private_networks=definition.allow_private_networks)
        elif node.type == "python":
            _enforce_python_node_policy(node)
    return definition


def _validate_provider_refs(definition: WorkflowDefinition) -> None:
    """Reject llm nodes whose provider_ref cannot resolve (S6 build-time first, S20).

    Kept separate from ``_validate_definition_payload`` because it performs sync
    DB I/O through the provider service facade, whereas that helper stays pure.

    Raises:
        ValueError: Naming the offending node and reference.
    """
    for node in definition.nodes:
        if node.type not in ("llm", "LLM"):
            continue
        ref = node.config.get("provider_ref")
        if not ref:
            continue
        try:
            validate_reference(str(ref))
        except ValueError as exc:
            msg = f"node '{node.name}': provider_ref '{ref}' is not usable: {exc}"
            raise ValueError(msg) from exc


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
    db: DBSession = Depends(get_db_session),
    user: User = Depends(get_current_user),
) -> JSONResponse:
    """Execute one registered workflow and return the unified envelope (AD-10).

    Args:
        request: FastAPI request object required by the slowapi limiter and registry lookup.
        workflow_id: Registered workflow to execute.
        payload: Optional JSON object passed as workflow input.
        db: Request-scoped DB session for persisting the run.
        user: Current authenticated user (auth gate + audit trail).

    Returns:
        JSONResponse carrying the host unified envelope ``{code, message, data}`` (200/404/500).
    """
    logger.info("api_workflow_execution_requested", workflow_id=workflow_id)
    # Frontend sends {"input": {...state fields...}}; extract the inner dict.
    # Fall back to the whole payload for direct/legacy callers that send flat state.
    raw = payload or {}
    inner = raw.get("input")
    input_data: dict[str, Any] = inner if isinstance(inner, dict) else raw
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
    node_count = len(definition.nodes) if definition else 0
    serialized_logs = _serialize_execution_logs(result.execution_logs)
    created_by = user.username or str(user.id)

    await run_in_threadpool(
        _persist_workflow_run,
        db,
        workflow_id=workflow_id,
        run_id=result.run_id,
        status=result.status,
        input_data=input_data,
        output_data=result.output if result.status == "success" else {},
        error_message=result.error_message,
        execution_logs=serialized_logs,
        duration_ms=result.duration_ms,
        node_count=node_count,
        created_by=created_by,
    )

    if result.status == "failed":
        logger.warning("api_workflow_execution_failed", workflow_id=workflow_id, error=result.error_message)
        return _project_to_host_envelope(
            ApiResponse(
                success=False,
                error=_redacted_summary(result.error_message or "workflow execution failed"),
                metadata={
                    "workflow_id": workflow_id,
                    "run_id": result.run_id,
                    "duration_ms": result.duration_ms,
                    "node_count": node_count,
                    "execution_logs": serialized_logs,
                },
            ),
            500,
        )

    response = ApiResponse(
        success=True,
        data=result.output,
        metadata={
            "workflow_id": workflow_id,
            "run_id": result.run_id,
            "duration_ms": result.duration_ms,
            "node_count": node_count,
            "execution_logs": serialized_logs,
        },
    )
    return _project_to_host_envelope(response, 200)


def _run_to_summary(row: WorkflowRun) -> dict[str, Any]:
    """Project a WorkflowRun row into a list-view summary dict."""
    return {
        "id": row.id,
        "workflow_id": row.workflow_id,
        "run_id": row.run_id,
        "status": row.status,
        "duration_ms": row.duration_ms,
        "node_count": row.node_count,
        "error_message": row.error_message,
        "created_by": row.created_by,
        "created_at": row.created_at.isoformat() if row.created_at else None,
    }


def _run_to_detail(row: WorkflowRun) -> dict[str, Any]:
    """Project a WorkflowRun row into a detail dict with full execution logs."""
    return {
        **_run_to_summary(row),
        "input_data": row.input_data,
        "output_data": row.output_data,
        "execution_logs": row.execution_logs,
    }


@router.get(
    "/workflows/{workflow_id}/runs",
    response_model=HostApiResponse[PageResult[dict[str, Any]]],
)
@limiter.limit(settings.RATE_LIMIT_ENDPOINTS["workflows_detail"][0])
async def list_workflow_runs(
    request: Request,
    workflow_id: str,
    page: int = Query(1, ge=1),
    page_size: int = Query(20, ge=1, le=100, alias="pageSize"),
    db: DBSession = Depends(get_db_session),
    _user: User = Depends(get_current_user),
) -> JSONResponse:
    """List persisted execution runs for a workflow, newest first.

    Args:
        request: FastAPI request (limiter).
        workflow_id: Workflow whose runs to list.
        page: 1-based page number.
        page_size: Rows per page.
        db: Request-scoped DB session.
        _user: Current authenticated user (auth gate).

    Returns:
        JSONResponse carrying a PageResult of run summaries.
    """
    stmt = select(WorkflowRun).where(col(WorkflowRun.workflow_id) == workflow_id)
    count_stmt = select(func.count()).select_from(WorkflowRun).where(col(WorkflowRun.workflow_id) == workflow_id)
    total = db.exec(count_stmt).one()
    rows = db.exec(
        stmt.order_by(col(WorkflowRun.created_at).desc()).offset((page - 1) * page_size).limit(page_size)
    ).all()
    items = [_run_to_summary(row) for row in rows]
    result = PageResult(items=items, total=total, page=page, page_size=page_size)
    return _project_to_host_envelope(ApiResponse(success=True, data=result.model_dump(by_alias=True)), 200)


@router.get(
    "/workflows/{workflow_id}/runs/{run_id}",
    response_model=HostApiResponse[dict[str, Any]],
    responses={
        404: {
            "model": HostApiResponse[None],
            "description": "Unknown run_id: envelope with code=404, data=null",
        },
    },
)
@limiter.limit(settings.RATE_LIMIT_ENDPOINTS["workflows_detail"][0])
async def get_workflow_run(
    request: Request,
    workflow_id: str,
    run_id: str,
    db: DBSession = Depends(get_db_session),
    _user: User = Depends(get_current_user),
) -> JSONResponse:
    """Fetch full detail of a single workflow run (with execution logs).

    Args:
        request: FastAPI request (limiter).
        workflow_id: Workflow the run belongs to.
        run_id: Unique run identifier.
        db: Request-scoped DB session.
        _user: Current authenticated user (auth gate).

    Returns:
        JSONResponse carrying the full run detail or 404.
    """
    stmt = select(WorkflowRun).where(
        col(WorkflowRun.workflow_id) == workflow_id,
        col(WorkflowRun.run_id) == run_id,
    )
    row = db.exec(stmt).first()
    if row is None:
        return _project_to_host_envelope(
            ApiResponse(success=False, error=f"run not found: {run_id}"), 404
        )
    return _project_to_host_envelope(ApiResponse(success=True, data=_run_to_detail(row)), 200)


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
    db: DBSession = Depends(get_db_session),
    _admin: User = Depends(require_workflow_admin),
) -> JSONResponse:
    """Full-replace register a workflow definition (spec-16, S13 atomic replacement).

    Processing order: parse → id match → whitelist → per-type guards (http SSRF,
    python sandbox policy) → provider_ref → register → persist → return. On
    persist failure the registry entry is rolled back to keep registry = disk.
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
        return _project_to_host_envelope(ApiResponse(success=False, error=_redacted_summary(str(exc))), 422)
    except WorkflowValidationError as exc:
        logger.warning("api_save_workflow_definition_rejected", workflow_id=workflow_id)
        return _project_to_host_envelope(
            ApiResponse(success=False, error=_redacted_summary(f"definition rejected: {exc}")), 422
        )

    try:
        await run_in_threadpool(_validate_provider_refs, definition)
    except ValueError as exc:
        logger.warning("api_save_workflow_provider_ref_invalid", workflow_id=workflow_id)
        return _project_to_host_envelope(
            ApiResponse(success=False, error=_redacted_summary(f"provider_ref invalid: {exc}")), 422
        )

    try:
        await run_in_threadpool(registry.register_workflow, definition)
    except (ValueError, WorkflowEngineError) as exc:
        logger.warning("api_save_workflow_build_failed", workflow_id=workflow_id)
        return _project_to_host_envelope(
            ApiResponse(success=False, error=_redacted_summary(f"build-time error: {exc}")), 422
        )

    try:
        await run_in_threadpool(save_definition_yaml, definition, session=db)
    except Exception as exc:  # noqa: BLE001 — rollback on any persist failure
        logger.exception("api_save_workflow_persist_failed", workflow_id=workflow_id)
        registry.delete_workflow(workflow_id)
        summary = f"persistence failed for '{workflow_id}': {type(exc).__name__}: {exc}"
        return _project_to_host_envelope(ApiResponse(success=False, error=_redacted_summary(summary)), 500)

    return _project_to_host_envelope(ApiResponse(success=True, data=_definition_view(definition)), 200)


def _remove_workflow(registry: WorkflowRegistry, workflow_id: str, *, session: DBSession | None = None) -> bool:
    """Orchestrate memory + disk + DB deletion; return True if removed from registry (C6/H7)."""
    removed = registry.delete_workflow(workflow_id)
    if removed:
        delete_definition_yaml(workflow_id, session=session)
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
    db: DBSession = Depends(get_db_session),
    _admin: User = Depends(require_workflow_admin),
) -> JSONResponse:
    """Delete a workflow from registry, disk and DB; 404 if unknown (spec-18)."""
    logger.info("api_workflow_delete_requested", workflow_id=workflow_id)
    removed = await run_in_threadpool(_remove_workflow, registry, workflow_id, session=db)
    if not removed:
        logger.warning("api_workflow_not_found_for_delete", workflow_id=workflow_id)
        return _project_to_host_envelope(
            ApiResponse(success=False, error=_redacted_summary(f"workflow not found: {workflow_id}")),
            404,
        )
    return _project_to_host_envelope(ApiResponse(success=True, data=None), 200)
