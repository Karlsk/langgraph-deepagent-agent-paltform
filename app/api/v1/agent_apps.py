"""Admin API for agent assets: sub-agents, skills, agent apps, MCP servers and LLM configs.

Phase-1 scope: all assets are globally shared (no per-user ownership checks);
every endpoint only authenticates via ``get_current_session`` and records the
creator in the audit-only ``created_by`` field.

Error semantics: missing resources return 404; name collisions, validation
failures and dangling skill/subagent/tool references return 422; unexpected
failures return 500 after ``logger.exception``. MCP server secrets may only
be expressed as ``${ENV_VAR}`` placeholders — plaintext secret values are
rejected at the interface layer.
"""

import asyncio
import hashlib
import json
import os
import re
from collections.abc import Generator, Mapping
from typing import Any, TypeVar

from fastapi import APIRouter, Depends, HTTPException, Request
from langchain_mcp_adapters.client import MultiServerMCPClient
from pydantic import BaseModel as PydanticBaseModel
from pydantic import ValidationError
from sqlalchemy.exc import IntegrityError
from sqlmodel import Session as DBSession
from sqlmodel import col, select

from app.api.v1.auth import get_current_session
from app.core.config import settings
from app.core.limiter import limiter
from app.core.logging import logger
from app.models.agent_assets import (
    DEFAULT_LLM_CONFIG_NAME,
    AgentApp,
    LlmConfig,
    McpServerConfig,
    SkillAsset,
    SubAgentConfig,
)
from app.models.session import Session as ChatSession
from app.schemas.agent_apps import (
    AgentAppCreate,
    AgentAppRead,
    AgentAppUpdate,
    LlmConfigCreate,
    LlmConfigRead,
    LlmConfigUpdate,
    McpServerCreate,
    McpServerRead,
    McpServerUpdate,
    SkillContentRead,
    SkillCreate,
    SkillGenerateRequest,
    SkillGenerateResponse,
    SkillRead,
    SkillUpdate,
    SubAgentCreate,
    SubAgentRead,
    SubAgentTestRequest,
    SubAgentTestResult,
    SubAgentUpdate,
    ToolCatalogEntry,
)
from app.services.agents import assembly, skills_store
from app.services.agents.mcp_manager import (
    build_connection_config,
    build_tool_catalog,
    check_server_tool_collision,
    load_mcp_servers,
    shutdown_mcp_clients,
    validate_tool_names,
)
from app.services.agents.test_runner import run_subagent_once
from app.services.database import database_service
from app.services.llm.llm_store import compute_llm_config_hash

router = APIRouter()

_ModelT = TypeVar("_ModelT", bound=PydanticBaseModel)

# Secrets may only be expressed as a single ${ENV_VAR} placeholder.
_PLACEHOLDER_ONLY_PATTERN = re.compile(r"\$\{[A-Za-z_][A-Za-z0-9_]*\}")

# Probe of a candidate MCP server must not block the management API.
_MCP_PROBE_TIMEOUT_SECONDS = 30.0

# Shell interpreters are always forbidden as stdio commands (unconditional
# blacklist; the configurable allowlist lives in settings).
_SHELL_INTERPRETERS = frozenset(
    {"sh", "bash", "zsh", "dash", "fish", "ksh", "csh", "tcsh", "cmd", "powershell", "pwsh"}
)
# Inline execution modes that turn a trusted interpreter into arbitrary code execution.
_PYTHON_INLINE_FLAGS = frozenset({"-c", "-m"})
_NODE_INLINE_FLAGS = frozenset({"-e", "--eval", "-p", "--print"})

# System default AgentApp name (bootstrap-seeded; delete-protected like the
# default LlmConfig).
_DEFAULT_AGENT_APP_NAME = "default"

# LlmConfig PATCH fields backed by NOT NULL columns: explicit JSON null is
# rejected (omit the field to keep it unchanged); base_url/temperature/
# max_tokens keep their explicit-null clear semantics.
_LLM_CONFIG_NOT_NULL_PATCH_FIELDS = frozenset({"model_name", "api_key", "enabled", "description"})


# ---------------------------------------------------------------------------
# DB session dependency
# ---------------------------------------------------------------------------


def get_db_session() -> Generator[DBSession, Any, None]:
    """Yield a request-scoped SQLModel session bound to the shared engine.

    Yields:
        DBSession: A SQLModel session closed automatically on teardown.
    """
    with DBSession(database_service.engine) as session:
        yield session


# ---------------------------------------------------------------------------
# Shared helpers
# ---------------------------------------------------------------------------


def _creator(current_session: ChatSession) -> str:
    """Derive the audit-only creator identifier from the chat session."""
    return current_session.username or str(current_session.user_id)


def _canonical_sha256(payload: dict[str, Any]) -> str:
    """Hash a canonical (sorted-keys, compact) JSON projection of the payload."""
    canonical = json.dumps(payload, sort_keys=True, separators=(",", ":"), ensure_ascii=False, default=str)
    return hashlib.sha256(canonical.encode("utf-8")).hexdigest()


def _subagent_content_hash(cfg: SubAgentConfig) -> str:
    """Compute the content hash of the effective sub-agent configuration."""
    return _canonical_sha256(
        {
            "description": cfg.description,
            "when_to_use": cfg.when_to_use,
            "system_prompt": cfg.system_prompt,
            "allowed_tools": cfg.allowed_tools,
            "model": cfg.model,
            "max_turns": cfg.max_turns,
        }
    )


def _mcp_content_hash(cfg: McpServerConfig) -> str:
    """Compute the content hash of the effective MCP server configuration."""
    return _canonical_sha256(
        {
            "transport": cfg.transport,
            "command": cfg.command,
            "args": cfg.args,
            "env": cfg.env,
            "url": cfg.url,
            "headers": cfg.headers,
            "enabled": cfg.enabled,
            "description": cfg.description,
        }
    )


def _mcp_fingerprint(db: DBSession) -> str:
    """Fingerprint the enabled MCP configuration set (publish hash input)."""
    servers = load_mcp_servers(db)
    return "|".join(sorted(f"{server.name}:{server.content_hash}" for server in servers))


def _mask_api_key(api_key: str) -> str:
    """Project an API key into its masked form.

    Short keys (length <= 8) mask completely — exposing their tail would
    leak half or more of the secret. Longer keys keep the last four chars.
    """
    if len(api_key) <= 8:
        return "****"
    return "****" + api_key[-4:]


def _llm_config_read(cfg: LlmConfig) -> dict[str, Any]:
    """Project an LlmConfig row into its API response form.

    The raw ``api_key`` is physically excluded; only the masked projection
    ``api_key_masked`` is ever returned.
    """
    return {
        "name": cfg.name,
        "model_name": cfg.model_name,
        "api_key_masked": _mask_api_key(cfg.api_key),
        "base_url": cfg.base_url,
        "temperature": cfg.temperature,
        "max_tokens": cfg.max_tokens,
        "enabled": cfg.enabled,
        "description": cfg.description,
        "content_hash": cfg.content_hash,
        "created_by": cfg.created_by,
    }


def _llm_fingerprint(llm_configs: Mapping[str, LlmConfig]) -> str:
    """Fingerprint a set of LlmConfig rows (publish hash input)."""
    return "|".join(sorted(f"{cfg.name}:{cfg.content_hash}" for cfg in llm_configs.values()))


async def _read_patch_body(request: Request) -> dict[str, Any]:
    """Parse a PATCH JSON body, defending the immutable ``name`` field.

    Args:
        request: The incoming FastAPI request.

    Returns:
        The parsed JSON object body.

    Raises:
        HTTPException: 422 when the body is not a JSON object or tries to
            modify the immutable ``name`` field.
    """
    try:
        body = await request.json()
    except json.JSONDecodeError as exc:
        raise HTTPException(status_code=422, detail="request body must be valid JSON") from exc
    if not isinstance(body, dict):
        raise HTTPException(status_code=422, detail="request body must be a JSON object")
    if "name" in body:
        raise HTTPException(status_code=422, detail="name is immutable and cannot be changed")
    return body


def _validate_payload(model_type: type[_ModelT], body: dict[str, Any]) -> _ModelT:
    """Validate a manually parsed body against a schema, mapping errors to 422.

    Args:
        model_type: The Pydantic schema to validate against.
        body: The parsed JSON object body.

    Returns:
        The validated schema instance.

    Raises:
        HTTPException: 422 when schema validation fails.
    """
    try:
        return model_type.model_validate(body)
    except ValidationError as exc:
        logger.warning("agent_apps_payload_invalid", model=model_type.__name__, error=str(exc))
        raise HTTPException(status_code=422, detail=str(exc)) from exc


def _ensure_placeholder_secrets(values: Mapping[str, str], *, section: str, server_name: str) -> None:
    """Reject plaintext secrets: values must be pure ``${ENV_VAR}`` placeholders.

    Args:
        values: The env (stdio) or headers (http) mapping to validate.
        section: Config section name used in the error detail.
        server_name: Owning MCP server name (logging context).

    Raises:
        HTTPException: 422 when any value is not a single placeholder.
    """
    for key, value in values.items():
        if not _PLACEHOLDER_ONLY_PATTERN.fullmatch(str(value)):
            logger.warning("mcp_secret_placeholder_violation", server=server_name, section=section, key=key)
            raise HTTPException(
                status_code=422,
                detail=f"{section}.{key} must be a ${{ENV_VAR}} placeholder; plaintext secrets are forbidden",
            )


def _validate_transport_pairing(transport: str, command: str | None, url: str | None) -> None:
    """Validate the merged transport/command/url combination of an MCP server.

    Args:
        transport: Effective transport backend (stdio|http).
        command: Effective stdio command.
        url: Effective http endpoint URL.

    Raises:
        HTTPException: 422 when required fields are missing or forbidden.
    """
    if transport == "stdio":
        if not command:
            raise HTTPException(status_code=422, detail="command is required for stdio transport")
        if url is not None:
            raise HTTPException(status_code=422, detail="url must not be set for stdio transport")
        return
    if not url:
        raise HTTPException(status_code=422, detail="url is required for http transport")
    if command is not None:
        raise HTTPException(status_code=422, detail="command must not be set for http transport")


def _validate_stdio_command(command: str, args: list[str], server_name: str) -> None:
    """Constrain the stdio command surface (phase-1 shared deployment, no isolation).

    Rejects shell interpreters outright, requires the executable basename to be
    in ``settings.MCP_STDIO_ALLOWED_COMMANDS``, and forbids inline execution
    modes (``python -c/-m``, ``node -e/--eval``) that amount to arbitrary code.

    Args:
        command: The stdio executable.
        args: The argument list passed to the executable.
        server_name: Owning MCP server name (logging context).

    Raises:
        HTTPException: 422 when the command or its inline mode is forbidden.
    """
    base = os.path.basename(command.strip()).lower()
    if base.endswith(".exe"):
        base = base.removesuffix(".exe")
    if base in _SHELL_INTERPRETERS:
        logger.warning("mcp_stdio_command_rejected", server=server_name, reason="shell_interpreter", command=base)
        raise HTTPException(status_code=422, detail=f"stdio command '{base}' is a forbidden shell interpreter")
    allowlist = {name.lower() for name in settings.MCP_STDIO_ALLOWED_COMMANDS}
    if base not in allowlist:
        logger.warning("mcp_stdio_command_rejected", server=server_name, reason="not_allowlisted", command=base)
        raise HTTPException(
            status_code=422,
            detail=f"stdio command '{base}' is not in MCP_STDIO_ALLOWED_COMMANDS ({', '.join(sorted(allowlist))})",
        )
    if base.startswith("python") and any(arg in _PYTHON_INLINE_FLAGS for arg in args):
        logger.warning("mcp_stdio_command_rejected", server=server_name, reason="inline_execution", command=base)
        raise HTTPException(status_code=422, detail="stdio args must not use inline execution modes (-c/-m)")
    if base == "node" and any(arg in _NODE_INLINE_FLAGS for arg in args):
        logger.warning("mcp_stdio_command_rejected", server=server_name, reason="inline_execution", command=base)
        raise HTTPException(status_code=422, detail="stdio args must not use inline execution modes (-e/--eval)")


async def _probe_server_tool_names(server: McpServerConfig) -> list[str] | None:
    """Load the tool names of a candidate server via an ephemeral client.

    Used for fail-fast collision validation before the configuration is
    persisted. Probe failures and timeouts (``_MCP_PROBE_TIMEOUT_SECONDS``)
    degrade to ``None`` (skip the collision check), mirroring mcp_manager's
    per-server degradation policy.

    Args:
        server: Candidate (possibly unpersisted) MCP server configuration.

    Returns:
        The tool names exposed by the server, or None when they could not be
        loaded (excluded config, connection failure or timeout).
    """
    connection = build_connection_config(server)
    if connection is None:
        return None

    async def _load() -> list[str]:
        client = MultiServerMCPClient({server.name: connection})
        tools = await client.get_tools()
        return [tool.name for tool in tools]

    try:
        return await asyncio.wait_for(_load(), timeout=_MCP_PROBE_TIMEOUT_SECONDS)
    except TimeoutError:
        logger.warning(
            "mcp_server_tool_probe_timeout", server=server.name, timeout_seconds=_MCP_PROBE_TIMEOUT_SECONDS
        )
        return None
    except Exception:  # noqa: BLE001 — probe degradation must not block CRUD
        logger.exception("mcp_server_tool_probe_failed", server=server.name)
        return None


# ---------------------------------------------------------------------------
# Sub-agents
# ---------------------------------------------------------------------------


@router.get("/subagents", response_model=list[SubAgentRead])
@limiter.limit(settings.RATE_LIMIT_ENDPOINTS["subagent"][0])
async def list_subagents(
    request: Request,
    db: DBSession = Depends(get_db_session),
    current_session: ChatSession = Depends(get_current_session),
) -> list[SubAgentConfig]:
    """List every stored sub-agent configuration.

    Args:
        request: The FastAPI request object for rate limiting.
        db: Request-scoped DB session.
        current_session: Authenticated chat session.

    Returns:
        All sub-agent rows ordered by name.
    """
    try:
        return list(db.exec(select(SubAgentConfig).order_by(col(SubAgentConfig.name))).all())
    except Exception as exc:
        logger.exception("subagent_list_failed")
        raise HTTPException(status_code=500, detail=str(exc)) from exc


@router.post("/subagents", response_model=SubAgentRead, status_code=201)
@limiter.limit(settings.RATE_LIMIT_ENDPOINTS["subagent"][0])
async def create_subagent(
    request: Request,
    payload: SubAgentCreate,
    db: DBSession = Depends(get_db_session),
    current_session: ChatSession = Depends(get_current_session),
) -> SubAgentConfig:
    """Create a reusable sub-agent configuration.

    Args:
        request: The FastAPI request object for rate limiting.
        payload: The sub-agent definition.
        db: Request-scoped DB session.
        current_session: Authenticated chat session (audit only).

    Returns:
        The persisted sub-agent row.

    Raises:
        HTTPException: 422 when the name is already taken.
    """
    try:
        if db.get(SubAgentConfig, payload.name) is not None:
            logger.warning("subagent_create_conflict", name=payload.name)
            raise HTTPException(status_code=422, detail=f"subagent '{payload.name}' already exists")

        subagent = SubAgentConfig(
            name=payload.name,
            description=payload.description,
            when_to_use=payload.when_to_use,
            system_prompt=payload.system_prompt,
            allowed_tools=payload.allowed_tools,
            model=payload.model,
            max_turns=payload.max_turns,
            content_hash="",
            created_by=_creator(current_session),
        )
        subagent.content_hash = _subagent_content_hash(subagent)
        db.add(subagent)
        db.commit()
        db.refresh(subagent)
        logger.info("subagent_created", name=payload.name, created_by=subagent.created_by)
        return subagent
    except HTTPException:
        raise
    except Exception as exc:
        logger.exception("subagent_create_failed", name=payload.name)
        raise HTTPException(status_code=500, detail=str(exc)) from exc


@router.get("/subagents/{name}", response_model=SubAgentRead)
@limiter.limit(settings.RATE_LIMIT_ENDPOINTS["subagent"][0])
async def get_subagent(
    request: Request,
    name: str,
    db: DBSession = Depends(get_db_session),
    current_session: ChatSession = Depends(get_current_session),
) -> SubAgentConfig:
    """Fetch one sub-agent configuration by name.

    Args:
        request: The FastAPI request object for rate limiting.
        name: Sub-agent primary key.
        db: Request-scoped DB session.
        current_session: Authenticated chat session.

    Returns:
        The matching sub-agent row.

    Raises:
        HTTPException: 404 when the sub-agent does not exist.
    """
    try:
        subagent = db.get(SubAgentConfig, name)
        if subagent is None:
            raise HTTPException(status_code=404, detail=f"subagent '{name}' not found")
        return subagent
    except HTTPException:
        raise
    except Exception as exc:
        logger.exception("subagent_read_failed", name=name)
        raise HTTPException(status_code=500, detail=str(exc)) from exc


@router.patch("/subagents/{name}", response_model=SubAgentRead)
@limiter.limit(settings.RATE_LIMIT_ENDPOINTS["subagent"][0])
async def update_subagent(
    request: Request,
    name: str,
    db: DBSession = Depends(get_db_session),
    current_session: ChatSession = Depends(get_current_session),
) -> SubAgentConfig:
    """Partially update a sub-agent (name is immutable).

    Args:
        request: The FastAPI request object for rate limiting.
        name: Sub-agent primary key.
        db: Request-scoped DB session.
        current_session: Authenticated chat session.

    Returns:
        The updated sub-agent row with refreshed hash and bumped version.

    Raises:
        HTTPException: 404 when missing, 422 on name change or empty payload.
    """
    body = await _read_patch_body(request)
    payload = _validate_payload(SubAgentUpdate, body)
    try:
        subagent = db.get(SubAgentConfig, name)
        if subagent is None:
            raise HTTPException(status_code=404, detail=f"subagent '{name}' not found")

        updates = payload.model_dump(exclude_unset=True)
        if not updates:
            raise HTTPException(status_code=422, detail="nothing to update")

        for field, value in updates.items():
            setattr(subagent, field, value)
        subagent.content_hash = _subagent_content_hash(subagent)
        subagent.version += 1
        db.add(subagent)
        db.commit()
        db.refresh(subagent)
        logger.info("subagent_updated", name=name, version=subagent.version)
        return subagent
    except HTTPException:
        raise
    except Exception as exc:
        logger.exception("subagent_update_failed", name=name)
        raise HTTPException(status_code=500, detail=str(exc)) from exc


@router.delete("/subagents/{name}")
@limiter.limit(settings.RATE_LIMIT_ENDPOINTS["subagent"][0])
async def delete_subagent(
    request: Request,
    name: str,
    db: DBSession = Depends(get_db_session),
    current_session: ChatSession = Depends(get_current_session),
) -> None:
    """Delete a sub-agent configuration.

    Args:
        request: The FastAPI request object for rate limiting.
        name: Sub-agent primary key.
        db: Request-scoped DB session.
        current_session: Authenticated chat session.

    Raises:
        HTTPException: 404 when the sub-agent does not exist.
    """
    try:
        subagent = db.get(SubAgentConfig, name)
        if subagent is None:
            raise HTTPException(status_code=404, detail=f"subagent '{name}' not found")
        db.delete(subagent)
        db.commit()
        logger.info("subagent_deleted", name=name)
    except HTTPException:
        raise
    except Exception as exc:
        logger.exception("subagent_delete_failed", name=name)
        raise HTTPException(status_code=500, detail=str(exc)) from exc


@router.post("/subagents/{name}/test", response_model=SubAgentTestResult)
@limiter.limit(settings.RATE_LIMIT_ENDPOINTS["subagent_test"][0])
async def test_subagent(
    request: Request,
    name: str,
    payload: SubAgentTestRequest,
    db: DBSession = Depends(get_db_session),
    current_session: ChatSession = Depends(get_current_session),
) -> SubAgentTestResult:
    """Run one isolated one-shot test of a sub-agent configuration.

    Args:
        request: The FastAPI request object for rate limiting.
        name: Sub-agent primary key.
        payload: The test prompt.
        db: Request-scoped DB session.
        current_session: Authenticated chat session.

    Returns:
        The test run result (final message, turns, duration, model).

    Raises:
        HTTPException: 404 when the sub-agent does not exist, 500 on run failure.
    """
    try:
        if db.get(SubAgentConfig, name) is None:
            raise HTTPException(status_code=404, detail=f"subagent '{name}' not found")
        logger.info("subagent_test_requested", name=name, user_id=current_session.user_id)
        return await run_subagent_once(session=db, name=name, prompt=payload.prompt)
    except HTTPException:
        raise
    except ValueError as exc:
        raise HTTPException(status_code=404, detail=str(exc)) from exc
    except Exception as exc:
        logger.exception("subagent_test_failed", name=name)
        raise HTTPException(status_code=500, detail=str(exc)) from exc


# ---------------------------------------------------------------------------
# Skills
# ---------------------------------------------------------------------------


@router.get("/skills", response_model=list[SkillRead])
@limiter.limit(settings.RATE_LIMIT_ENDPOINTS["skill"][0])
async def list_skills(
    request: Request,
    db: DBSession = Depends(get_db_session),
    current_session: ChatSession = Depends(get_current_session),
) -> list[dict[str, Any]]:
    """List metadata of every global skill.

    Args:
        request: The FastAPI request object for rate limiting.
        db: Request-scoped DB session.
        current_session: Authenticated chat session.

    Returns:
        Skill metadata rows (name/description/content_hash/version/created_by).
    """
    try:
        return await skills_store.list_global(db)
    except Exception as exc:
        logger.exception("skill_list_failed")
        raise HTTPException(status_code=500, detail=str(exc)) from exc


@router.post("/skills", response_model=SkillRead, status_code=201)
@limiter.limit(settings.RATE_LIMIT_ENDPOINTS["skill"][0])
async def create_skill(
    request: Request,
    payload: SkillCreate,
    db: DBSession = Depends(get_db_session),
    current_session: ChatSession = Depends(get_current_session),
) -> SkillAsset:
    """Create a global skill from direct input (atomic file write + DB row).

    Args:
        request: The FastAPI request object for rate limiting.
        payload: The skill definition (name, description, SKILL.md body).
        db: Request-scoped DB session.
        current_session: Authenticated chat session (audit only).

    Returns:
        The persisted skill asset row.

    Raises:
        HTTPException: 422 when the name is invalid or already taken.
    """
    try:
        return await skills_store.create_global(
            db,
            name=payload.name,
            description=payload.description,
            body=payload.body,
            created_by=_creator(current_session),
        )
    except ValueError as exc:
        logger.warning("skill_create_rejected", name=payload.name, error=str(exc))
        raise HTTPException(status_code=422, detail=str(exc)) from exc
    except Exception as exc:
        logger.exception("skill_create_failed", name=payload.name)
        raise HTTPException(status_code=500, detail=str(exc)) from exc


@router.post("/skills/generate", response_model=SkillGenerateResponse)
@limiter.limit(settings.RATE_LIMIT_ENDPOINTS["skill_generate"][0])
async def generate_skill(
    request: Request,
    payload: SkillGenerateRequest,
    current_session: ChatSession = Depends(get_current_session),
) -> SkillGenerateResponse:
    """Generate a SKILL.md draft via the LLM (draft only, nothing persisted).

    Args:
        request: The FastAPI request object for rate limiting.
        payload: Draft generation guidance.
        current_session: Authenticated chat session.

    Returns:
        The generated draft content.

    Raises:
        HTTPException: 500 when the LLM fails after retries.
    """
    try:
        logger.info("skill_generate_requested", user_id=current_session.user_id)
        draft = await skills_store.generate_skill_draft(description=payload.description, hint=payload.hint)
        return SkillGenerateResponse(draft=draft)
    except Exception as exc:
        logger.exception("skill_generate_failed")
        raise HTTPException(status_code=500, detail=str(exc)) from exc


@router.get("/skills/{name}", response_model=SkillRead)
@limiter.limit(settings.RATE_LIMIT_ENDPOINTS["skill"][0])
async def get_skill(
    request: Request,
    name: str,
    db: DBSession = Depends(get_db_session),
    current_session: ChatSession = Depends(get_current_session),
) -> SkillAsset:
    """Fetch one skill asset's metadata by name.

    Args:
        request: The FastAPI request object for rate limiting.
        name: Skill primary key.
        db: Request-scoped DB session.
        current_session: Authenticated chat session.

    Returns:
        The matching skill asset row.

    Raises:
        HTTPException: 404 when the skill does not exist.
    """
    try:
        asset = db.get(SkillAsset, name)
        if asset is None:
            raise HTTPException(status_code=404, detail=f"skill '{name}' not found")
        return asset
    except HTTPException:
        raise
    except Exception as exc:
        logger.exception("skill_read_failed", name=name)
        raise HTTPException(status_code=500, detail=str(exc)) from exc


@router.get("/skills/{name}/content", response_model=SkillContentRead)
@limiter.limit(settings.RATE_LIMIT_ENDPOINTS["skill"][0])
async def get_skill_content(
    request: Request,
    name: str,
    current_session: ChatSession = Depends(get_current_session),
) -> dict[str, str]:
    """Fetch the raw SKILL.md body of a global skill by name.

    Args:
        request: The FastAPI request object for rate limiting.
        name: Skill primary key.
        current_session: Authenticated chat session.

    Returns:
        The skill name and its full SKILL.md content.

    Raises:
        HTTPException: 404 when the skill does not exist.
    """
    try:
        content = await skills_store.read_global(name)
    except ValueError as exc:
        raise HTTPException(status_code=404, detail=f"skill '{name}' not found") from exc
    except Exception as exc:
        logger.exception("skill_content_read_failed", name=name)
        raise HTTPException(status_code=500, detail=str(exc)) from exc
    return {"name": name, "content": content}


@router.patch("/skills/{name}", response_model=SkillRead)
@limiter.limit(settings.RATE_LIMIT_ENDPOINTS["skill"][0])
async def update_skill(
    request: Request,
    name: str,
    db: DBSession = Depends(get_db_session),
    current_session: ChatSession = Depends(get_current_session),
) -> SkillAsset:
    """Partially update a skill (name is immutable).

    Args:
        request: The FastAPI request object for rate limiting.
        name: Skill primary key.
        db: Request-scoped DB session.
        current_session: Authenticated chat session.

    Returns:
        The updated skill asset row with refreshed hash and bumped version.

    Raises:
        HTTPException: 404 when missing, 422 on name change or empty payload.
    """
    body = await _read_patch_body(request)
    payload = _validate_payload(SkillUpdate, body)
    try:
        if db.get(SkillAsset, name) is None:
            raise HTTPException(status_code=404, detail=f"skill '{name}' not found")
        if payload.description is None and payload.body is None:
            raise HTTPException(status_code=422, detail="nothing to update: provide description and/or body")

        return await skills_store.update_global(db, name=name, description=payload.description, body=payload.body)
    except HTTPException:
        raise
    except ValueError as exc:
        logger.warning("skill_update_rejected", name=name, error=str(exc))
        raise HTTPException(status_code=422, detail=str(exc)) from exc
    except Exception as exc:
        logger.exception("skill_update_failed", name=name)
        raise HTTPException(status_code=500, detail=str(exc)) from exc


@router.delete("/skills/{name}")
@limiter.limit(settings.RATE_LIMIT_ENDPOINTS["skill"][0])
async def delete_skill(
    request: Request,
    name: str,
    db: DBSession = Depends(get_db_session),
    current_session: ChatSession = Depends(get_current_session),
) -> None:
    """Delete a global skill (cascades to per-user copies).

    Args:
        request: The FastAPI request object for rate limiting.
        name: Skill primary key.
        db: Request-scoped DB session.
        current_session: Authenticated chat session.

    Raises:
        HTTPException: 404 when the skill does not exist.
    """
    try:
        if db.get(SkillAsset, name) is None:
            raise HTTPException(status_code=404, detail=f"skill '{name}' not found")
        await skills_store.delete_global(db, name=name)
        logger.info("skill_deleted", name=name)
    except HTTPException:
        raise
    except ValueError as exc:
        raise HTTPException(status_code=422, detail=str(exc)) from exc
    except Exception as exc:
        logger.exception("skill_delete_failed", name=name)
        raise HTTPException(status_code=500, detail=str(exc)) from exc


# ---------------------------------------------------------------------------
# Agent apps
# ---------------------------------------------------------------------------


@router.get("/apps", response_model=list[AgentAppRead])
@limiter.limit(settings.RATE_LIMIT_ENDPOINTS["agent_app"][0])
async def list_agent_apps(
    request: Request,
    db: DBSession = Depends(get_db_session),
    current_session: ChatSession = Depends(get_current_session),
) -> list[AgentApp]:
    """List every stored agent application.

    Args:
        request: The FastAPI request object for rate limiting.
        db: Request-scoped DB session.
        current_session: Authenticated chat session.

    Returns:
        All agent app rows ordered by id.
    """
    try:
        return list(db.exec(select(AgentApp).order_by(col(AgentApp.id))).all())
    except Exception as exc:
        logger.exception("agent_app_list_failed")
        raise HTTPException(status_code=500, detail=str(exc)) from exc


@router.post("/apps", response_model=AgentAppRead, status_code=201)
@limiter.limit(settings.RATE_LIMIT_ENDPOINTS["agent_app"][0])
async def create_agent_app(
    request: Request,
    payload: AgentAppCreate,
    db: DBSession = Depends(get_db_session),
    current_session: ChatSession = Depends(get_current_session),
) -> AgentApp:
    """Create a draft agent application.

    Args:
        request: The FastAPI request object for rate limiting.
        payload: The agent app definition.
        db: Request-scoped DB session.
        current_session: Authenticated chat session (audit only).

    Returns:
        The persisted agent app row (status=draft, engine=deepagents).

    Raises:
        HTTPException: 422 when the name is already taken.
    """
    try:
        existing = db.exec(select(AgentApp).where(col(AgentApp.name) == payload.name)).first()
        if existing is not None:
            logger.warning("agent_app_create_conflict", name=payload.name)
            raise HTTPException(status_code=422, detail=f"agent app '{payload.name}' already exists")

        app_cfg = AgentApp(
            name=payload.name,
            system_prompt=payload.system_prompt,
            allowed_tools=payload.allowed_tools,
            model=payload.model,
            skill_names=payload.skill_names,
            subagent_names=payload.subagent_names,
            interrupt_on=payload.interrupt_on,
            created_by=_creator(current_session),
        )
        db.add(app_cfg)
        db.commit()
        db.refresh(app_cfg)
        logger.info("agent_app_created", app_id=app_cfg.id, name=payload.name)
        return app_cfg
    except HTTPException:
        raise
    except Exception as exc:
        logger.exception("agent_app_create_failed", name=payload.name)
        raise HTTPException(status_code=500, detail=str(exc)) from exc


@router.get("/apps/published", response_model=list[AgentAppRead])
@limiter.limit(settings.RATE_LIMIT_ENDPOINTS["agent_app"][0])
async def list_published_agent_apps(
    request: Request,
    db: DBSession = Depends(get_db_session),
    current_session: ChatSession = Depends(get_current_session),
) -> list[AgentApp]:
    """List published agent applications (assistant picker for chat).

    Registered before ``/apps/{app_id}`` so the literal path wins routing.

    Args:
        request: The FastAPI request object for rate limiting.
        db: Request-scoped DB session.
        current_session: Authenticated chat session.

    Returns:
        All agent app rows with status=published ordered by id.
    """
    try:
        statement = select(AgentApp).where(col(AgentApp.status) == "published").order_by(col(AgentApp.id))
        return list(db.exec(statement).all())
    except Exception as exc:
        logger.exception("agent_app_published_list_failed")
        raise HTTPException(status_code=500, detail=str(exc)) from exc


@router.get("/apps/{app_id}", response_model=AgentAppRead)
@limiter.limit(settings.RATE_LIMIT_ENDPOINTS["agent_app"][0])
async def get_agent_app(
    request: Request,
    app_id: int,
    db: DBSession = Depends(get_db_session),
    current_session: ChatSession = Depends(get_current_session),
) -> AgentApp:
    """Fetch one agent application by id.

    Args:
        request: The FastAPI request object for rate limiting.
        app_id: Agent app primary key.
        db: Request-scoped DB session.
        current_session: Authenticated chat session.

    Returns:
        The matching agent app row.

    Raises:
        HTTPException: 404 when the agent app does not exist.
    """
    try:
        app_cfg = db.get(AgentApp, app_id)
        if app_cfg is None:
            raise HTTPException(status_code=404, detail=f"agent app '{app_id}' not found")
        return app_cfg
    except HTTPException:
        raise
    except Exception as exc:
        logger.exception("agent_app_read_failed", app_id=app_id)
        raise HTTPException(status_code=500, detail=str(exc)) from exc


@router.patch("/apps/{app_id}", response_model=AgentAppRead)
@limiter.limit(settings.RATE_LIMIT_ENDPOINTS["agent_app"][0])
async def update_agent_app(
    request: Request,
    app_id: int,
    db: DBSession = Depends(get_db_session),
    current_session: ChatSession = Depends(get_current_session),
) -> AgentApp:
    """Partially update an agent app (name is immutable; lists replace wholesale).

    Args:
        request: The FastAPI request object for rate limiting.
        app_id: Agent app primary key.
        db: Request-scoped DB session.
        current_session: Authenticated chat session.

    Returns:
        The updated agent app row with bumped version.

    Raises:
        HTTPException: 404 when missing, 422 on name change or empty payload.
    """
    body = await _read_patch_body(request)
    payload = _validate_payload(AgentAppUpdate, body)
    try:
        app_cfg = db.get(AgentApp, app_id)
        if app_cfg is None:
            raise HTTPException(status_code=404, detail=f"agent app '{app_id}' not found")

        updates = payload.model_dump(exclude_unset=True)
        if not updates:
            raise HTTPException(status_code=422, detail="nothing to update")

        for field in ("skill_names", "subagent_names", "interrupt_on"):
            if field in updates and updates[field] is None:
                raise HTTPException(
                    status_code=422,
                    detail=f"{field} must not be null; pass an empty {'list' if field != 'interrupt_on' else 'dict'} to clear it",
                )

        for field, value in updates.items():
            setattr(app_cfg, field, value)
        if app_cfg.status == "published":
            # Content edits invalidate the published fingerprint: demote back
            # to draft so a broken config cannot keep serving live sessions.
            app_cfg.status = "draft"
            logger.info("agent_app_unpublished_on_edit", app_id=app_id)
        app_cfg.version += 1
        db.add(app_cfg)
        db.commit()
        db.refresh(app_cfg)
        logger.info("agent_app_updated", app_id=app_id, version=app_cfg.version)
        return app_cfg
    except HTTPException:
        raise
    except Exception as exc:
        logger.exception("agent_app_update_failed", app_id=app_id)
        raise HTTPException(status_code=500, detail=str(exc)) from exc


@router.delete("/apps/{app_id}")
@limiter.limit(settings.RATE_LIMIT_ENDPOINTS["agent_app"][0])
async def delete_agent_app(
    request: Request,
    app_id: int,
    db: DBSession = Depends(get_db_session),
    current_session: ChatSession = Depends(get_current_session),
) -> None:
    """Delete an agent application.

    Args:
        request: The FastAPI request object for rate limiting.
        app_id: Agent app primary key.
        db: Request-scoped DB session.
        current_session: Authenticated chat session.

    Raises:
        HTTPException: 404 when the agent app does not exist.
    """
    try:
        app_cfg = db.get(AgentApp, app_id)
        if app_cfg is None:
            raise HTTPException(status_code=404, detail=f"agent app '{app_id}' not found")
        if app_cfg.name == _DEFAULT_AGENT_APP_NAME:
            logger.warning("agent_app_delete_rejected", app_id=app_id, reason="default_protected")
            raise HTTPException(
                status_code=422,
                detail="the system default agent app is protected and cannot be deleted",
            )
        db.delete(app_cfg)
        db.commit()
        logger.info("agent_app_deleted", app_id=app_id)
    except HTTPException:
        raise
    except Exception as exc:
        logger.exception("agent_app_delete_failed", app_id=app_id)
        raise HTTPException(status_code=500, detail=str(exc)) from exc


@router.post("/apps/{app_id}/publish", response_model=AgentAppRead)
@limiter.limit(settings.RATE_LIMIT_ENDPOINTS["agent_app"][0])
async def publish_agent_app(
    request: Request,
    app_id: int,
    db: DBSession = Depends(get_db_session),
    current_session: ChatSession = Depends(get_current_session),
) -> AgentApp:
    """Publish an agent app after referential + tool-whitelist validation.

    Validation order: skill/subagent reference existence (422) ->
    ``assembly.validate_publish`` against the live tool catalog (422) ->
    stamp status=published, published_hash (config fingerprint) and bump
    the version.

    Args:
        request: The FastAPI request object for rate limiting.
        app_id: Agent app primary key.
        db: Request-scoped DB session.
        current_session: Authenticated chat session.

    Returns:
        The published agent app row.

    Raises:
        HTTPException: 404 when missing, 422 on dangling references or
            allowed_tools outside the tool catalog.
    """
    try:
        app_cfg = db.get(AgentApp, app_id)
        if app_cfg is None:
            raise HTTPException(status_code=404, detail=f"agent app '{app_id}' not found")

        subagent_cfgs: list[SubAgentConfig] = []
        for subagent_name in app_cfg.subagent_names:
            cfg = db.get(SubAgentConfig, subagent_name)
            if cfg is None:
                raise HTTPException(status_code=422, detail=f"referenced subagent '{subagent_name}' does not exist")
            subagent_cfgs.append(cfg)

        skill_hashes: dict[str, str] = {}
        for skill_name in app_cfg.skill_names:
            asset = db.get(SkillAsset, skill_name)
            if asset is None:
                raise HTTPException(status_code=422, detail=f"referenced skill '{skill_name}' does not exist")
            skill_hashes[skill_name] = asset.content_hash

        catalog = await build_tool_catalog(db)
        llm_configs: dict[str, LlmConfig] = {
            row.name: row for row in db.exec(select(LlmConfig).order_by(col(LlmConfig.name))).all()
        }
        try:
            assembly.validate_publish(app_cfg, subagent_cfgs, catalog, llm_configs)
        except ValueError as exc:
            logger.warning("agent_app_publish_validation_failed", app_id=app_id, error=str(exc))
            raise HTTPException(status_code=422, detail=str(exc)) from exc

        reference_names = {app_cfg.model or DEFAULT_LLM_CONFIG_NAME}
        reference_names.update(cfg.model or DEFAULT_LLM_CONFIG_NAME for cfg in subagent_cfgs)
        referenced = {name: llm_configs[name] for name in reference_names}

        app_cfg.status = "published"
        app_cfg.published_hash = assembly.compute_fingerprint(
            app_cfg, subagent_cfgs, skill_hashes, _mcp_fingerprint(db), _llm_fingerprint(referenced)
        )
        app_cfg.version += 1
        db.add(app_cfg)
        db.commit()
        db.refresh(app_cfg)
        logger.info("agent_app_published", app_id=app_id, version=app_cfg.version)
        return app_cfg
    except HTTPException:
        raise
    except Exception as exc:
        logger.exception("agent_app_publish_failed", app_id=app_id)
        raise HTTPException(status_code=500, detail=str(exc)) from exc


# ---------------------------------------------------------------------------
# MCP servers
# ---------------------------------------------------------------------------


@router.get("/mcp-servers", response_model=list[McpServerRead])
@limiter.limit(settings.RATE_LIMIT_ENDPOINTS["mcp_server"][0])
async def list_mcp_servers(
    request: Request,
    db: DBSession = Depends(get_db_session),
    current_session: ChatSession = Depends(get_current_session),
) -> list[McpServerConfig]:
    """List every stored MCP server configuration.

    Args:
        request: The FastAPI request object for rate limiting.
        db: Request-scoped DB session.
        current_session: Authenticated chat session.

    Returns:
        All MCP server rows ordered by name.
    """
    try:
        return list(db.exec(select(McpServerConfig).order_by(col(McpServerConfig.name))).all())
    except Exception as exc:
        logger.exception("mcp_server_list_failed")
        raise HTTPException(status_code=500, detail=str(exc)) from exc


@router.post("/mcp-servers", response_model=McpServerRead, status_code=201)
@limiter.limit(settings.RATE_LIMIT_ENDPOINTS["mcp_server"][0])
async def create_mcp_server(
    request: Request,
    payload: McpServerCreate,
    db: DBSession = Depends(get_db_session),
    current_session: ChatSession = Depends(get_current_session),
) -> McpServerConfig:
    """Register an MCP server with fail-fast tool-name collision validation.

    Args:
        request: The FastAPI request object for rate limiting.
        payload: The MCP server connection definition.
        db: Request-scoped DB session.
        current_session: Authenticated chat session (audit only).

    Returns:
        The persisted MCP server row.

    Raises:
        HTTPException: 422 on name conflict, plaintext secrets or tool
            name collisions with the existing catalog.
    """
    try:
        _ensure_placeholder_secrets(payload.env, section="env", server_name=payload.name)
        _ensure_placeholder_secrets(payload.headers, section="headers", server_name=payload.name)
        if payload.transport == "stdio":
            _validate_stdio_command(payload.command or "", payload.args, payload.name)
        if db.get(McpServerConfig, payload.name) is not None:
            logger.warning("mcp_server_create_conflict", name=payload.name)
            raise HTTPException(status_code=422, detail=f"mcp server '{payload.name}' already exists")

        server = McpServerConfig(
            name=payload.name,
            transport=payload.transport,
            command=payload.command,
            args=payload.args,
            env=payload.env,
            url=payload.url,
            headers=payload.headers,
            enabled=payload.enabled,
            description=payload.description,
            content_hash="",
            created_by=_creator(current_session),
        )
        server.content_hash = _mcp_content_hash(server)

        tool_names = await _probe_server_tool_names(server)
        if tool_names:
            try:
                await check_server_tool_collision(db, tool_names)
            except ValueError as exc:
                logger.warning("mcp_server_collision_rejected", name=payload.name, error=str(exc))
                raise HTTPException(status_code=422, detail=str(exc)) from exc

        db.add(server)
        db.commit()
        db.refresh(server)
        await shutdown_mcp_clients()
        logger.info("mcp_server_created", name=payload.name)
        return server
    except HTTPException:
        raise
    except Exception as exc:
        logger.exception("mcp_server_create_failed", name=payload.name)
        raise HTTPException(status_code=500, detail=str(exc)) from exc


@router.get("/mcp-servers/{name}", response_model=McpServerRead)
@limiter.limit(settings.RATE_LIMIT_ENDPOINTS["mcp_server"][0])
async def get_mcp_server(
    request: Request,
    name: str,
    db: DBSession = Depends(get_db_session),
    current_session: ChatSession = Depends(get_current_session),
) -> McpServerConfig:
    """Fetch one MCP server configuration by name.

    Args:
        request: The FastAPI request object for rate limiting.
        name: MCP server primary key.
        db: Request-scoped DB session.
        current_session: Authenticated chat session.

    Returns:
        The matching MCP server row.

    Raises:
        HTTPException: 404 when the MCP server does not exist.
    """
    try:
        server = db.get(McpServerConfig, name)
        if server is None:
            raise HTTPException(status_code=404, detail=f"mcp server '{name}' not found")
        return server
    except HTTPException:
        raise
    except Exception as exc:
        logger.exception("mcp_server_read_failed", name=name)
        raise HTTPException(status_code=500, detail=str(exc)) from exc


@router.patch("/mcp-servers/{name}", response_model=McpServerRead)
@limiter.limit(settings.RATE_LIMIT_ENDPOINTS["mcp_server"][0])
async def update_mcp_server(
    request: Request,
    name: str,
    db: DBSession = Depends(get_db_session),
    current_session: ChatSession = Depends(get_current_session),
) -> McpServerConfig:
    """Partially update an MCP server (name is immutable).

    An explicit transport switch rebuilds command/url from the payload alone;
    otherwise fields merge over the stored row. The merged configuration is
    validated (transport pairing, placeholder-only secrets, tool collisions)
    before it is persisted, and the MCP client cache is invalidated after.

    Args:
        request: The FastAPI request object for rate limiting.
        name: MCP server primary key.
        db: Request-scoped DB session.
        current_session: Authenticated chat session.

    Returns:
        The updated MCP server row with refreshed hash.

    Raises:
        HTTPException: 404 when missing, 422 on name change, invalid merged
            transport fields, plaintext secrets or tool collisions.
    """
    body = await _read_patch_body(request)
    payload = _validate_payload(McpServerUpdate, body)
    try:
        server = db.get(McpServerConfig, name)
        if server is None:
            raise HTTPException(status_code=404, detail=f"mcp server '{name}' not found")
        if payload.model_dump(exclude_unset=True) == {}:
            raise HTTPException(status_code=422, detail="nothing to update")

        switching = payload.transport is not None and payload.transport != server.transport
        merged_transport = payload.transport if payload.transport is not None else server.transport
        if switching:
            # Explicit transport switch: forbidden fields are dropped unless
            # the payload itself re-supplies them (schema already guards).
            merged_command = payload.command
            merged_url = payload.url
        else:
            merged_command = payload.command if payload.command is not None else server.command
            merged_url = payload.url if payload.url is not None else server.url
        merged_args = payload.args if payload.args is not None else list(server.args)
        merged_env = payload.env if payload.env is not None else dict(server.env)
        merged_headers = payload.headers if payload.headers is not None else dict(server.headers)
        merged_enabled = payload.enabled if payload.enabled is not None else server.enabled
        merged_description = payload.description if payload.description is not None else server.description

        _validate_transport_pairing(merged_transport, merged_command, merged_url)
        if merged_transport == "stdio" and (
            payload.command is not None or payload.args is not None or switching
        ):
            _validate_stdio_command(merged_command or "", merged_args, name)
        _ensure_placeholder_secrets(merged_env, section="env", server_name=name)
        _ensure_placeholder_secrets(merged_headers, section="headers", server_name=name)

        candidate = McpServerConfig(
            name=name,
            transport=merged_transport,
            command=merged_command,
            args=merged_args,
            env=merged_env,
            url=merged_url,
            headers=merged_headers,
            enabled=merged_enabled,
            description=merged_description,
            content_hash="",
        )
        candidate.content_hash = _mcp_content_hash(candidate)

        tool_names = await _probe_server_tool_names(candidate)
        if tool_names:
            catalog = await build_tool_catalog(db)
            other_names = [entry["name"] for entry in catalog if entry.get("server") != name]
            try:
                validate_tool_names(other_names, tool_names)
            except ValueError as exc:
                logger.warning("mcp_server_collision_rejected", name=name, error=str(exc))
                raise HTTPException(status_code=422, detail=str(exc)) from exc

        server.transport = merged_transport
        server.command = merged_command
        server.url = merged_url
        server.args = merged_args
        server.env = merged_env
        server.headers = merged_headers
        server.enabled = merged_enabled
        server.description = merged_description
        server.content_hash = candidate.content_hash
        db.add(server)
        db.commit()
        db.refresh(server)
        await shutdown_mcp_clients()
        logger.info("mcp_server_updated", name=name, transport=merged_transport)
        return server
    except HTTPException:
        raise
    except Exception as exc:
        logger.exception("mcp_server_update_failed", name=name)
        raise HTTPException(status_code=500, detail=str(exc)) from exc


@router.delete("/mcp-servers/{name}")
@limiter.limit(settings.RATE_LIMIT_ENDPOINTS["mcp_server"][0])
async def delete_mcp_server(
    request: Request,
    name: str,
    db: DBSession = Depends(get_db_session),
    current_session: ChatSession = Depends(get_current_session),
) -> None:
    """Delete an MCP server and invalidate the MCP client cache.

    Args:
        request: The FastAPI request object for rate limiting.
        name: MCP server primary key.
        db: Request-scoped DB session.
        current_session: Authenticated chat session.

    Raises:
        HTTPException: 404 when the MCP server does not exist.
    """
    try:
        server = db.get(McpServerConfig, name)
        if server is None:
            raise HTTPException(status_code=404, detail=f"mcp server '{name}' not found")
        db.delete(server)
        db.commit()
        await shutdown_mcp_clients()
        logger.info("mcp_server_deleted", name=name)
    except HTTPException:
        raise
    except Exception as exc:
        logger.exception("mcp_server_delete_failed", name=name)
        raise HTTPException(status_code=500, detail=str(exc)) from exc


# ---------------------------------------------------------------------------
# LLM configs
# ---------------------------------------------------------------------------


@router.get("/llm-configs", response_model=list[LlmConfigRead])
@limiter.limit(settings.RATE_LIMIT_ENDPOINTS["llm_config"][0])
async def list_llm_configs(
    request: Request,
    db: DBSession = Depends(get_db_session),
    current_session: ChatSession = Depends(get_current_session),
) -> list[dict[str, Any]]:
    """List every stored LLM configuration (api_key always masked).

    Args:
        request: The FastAPI request object for rate limiting.
        db: Request-scoped DB session.
        current_session: Authenticated chat session.

    Returns:
        All LLM config rows (masked projections) ordered by name.
    """
    try:
        rows = db.exec(select(LlmConfig).order_by(col(LlmConfig.name))).all()
        return [_llm_config_read(row) for row in rows]
    except Exception as exc:
        logger.exception("llm_config_list_failed")
        raise HTTPException(status_code=500, detail=str(exc)) from exc


@router.post("/llm-configs", response_model=LlmConfigRead, status_code=201)
@limiter.limit(settings.RATE_LIMIT_ENDPOINTS["llm_config"][0])
async def create_llm_config(
    request: Request,
    payload: LlmConfigCreate,
    db: DBSession = Depends(get_db_session),
    current_session: ChatSession = Depends(get_current_session),
) -> dict[str, Any]:
    """Create an LLM configuration referenced by agent asset ``model`` fields.

    Args:
        request: The FastAPI request object for rate limiting.
        payload: The LLM connection definition.
        db: Request-scoped DB session.
        current_session: Authenticated chat session (audit only).

    Returns:
        The persisted LLM config row (masked projection).

    Raises:
        HTTPException: 422 when the name is already taken (pre-check or a
            lost unique-name race at commit time).
    """
    try:
        if db.get(LlmConfig, payload.name) is not None:
            logger.warning("llm_config_create_conflict", name=payload.name)
            raise HTTPException(status_code=422, detail=f"llm config '{payload.name}' already exists")

        config = LlmConfig(
            name=payload.name,
            model_name=payload.model_name,
            api_key=payload.api_key,
            base_url=payload.base_url,
            temperature=payload.temperature,
            max_tokens=payload.max_tokens,
            enabled=payload.enabled,
            description=payload.description,
            content_hash="",
            created_by=_creator(current_session),
        )
        config.content_hash = compute_llm_config_hash(config)
        db.add(config)
        try:
            db.commit()
        except IntegrityError as exc:
            # Concurrent create won the unique-name race between the pre-check
            # and the insert: degrade to the same 422 the pre-check returns.
            db.rollback()
            logger.warning("llm_config_create_conflict", name=payload.name)
            raise HTTPException(status_code=422, detail=f"llm config '{payload.name}' already exists") from exc
        db.refresh(config)
        logger.info("llm_config_created", name=payload.name, created_by=config.created_by)
        return _llm_config_read(config)
    except HTTPException:
        raise
    except Exception as exc:
        logger.exception("llm_config_create_failed", name=payload.name)
        raise HTTPException(status_code=500, detail=str(exc)) from exc


@router.get("/llm-configs/{name}", response_model=LlmConfigRead)
@limiter.limit(settings.RATE_LIMIT_ENDPOINTS["llm_config"][0])
async def get_llm_config(
    request: Request,
    name: str,
    db: DBSession = Depends(get_db_session),
    current_session: ChatSession = Depends(get_current_session),
) -> dict[str, Any]:
    """Fetch one LLM configuration by name (api_key always masked).

    Args:
        request: The FastAPI request object for rate limiting.
        name: LLM config primary key.
        db: Request-scoped DB session.
        current_session: Authenticated chat session.

    Returns:
        The matching LLM config row (masked projection).

    Raises:
        HTTPException: 404 when the LLM config does not exist.
    """
    try:
        config = db.get(LlmConfig, name)
        if config is None:
            raise HTTPException(status_code=404, detail=f"llm config '{name}' not found")
        return _llm_config_read(config)
    except HTTPException:
        raise
    except Exception as exc:
        logger.exception("llm_config_read_failed", name=name)
        raise HTTPException(status_code=500, detail=str(exc)) from exc


@router.patch("/llm-configs/{name}", response_model=LlmConfigRead)
@limiter.limit(settings.RATE_LIMIT_ENDPOINTS["llm_config"][0])
async def update_llm_config(
    request: Request,
    name: str,
    db: DBSession = Depends(get_db_session),
    current_session: ChatSession = Depends(get_current_session),
) -> dict[str, Any]:
    """Partially update an LLM config (name is immutable).

    Omitting ``api_key`` keeps the stored key unchanged. Content edits only
    drift the compile fingerprint: published apps are never demoted.

    Args:
        request: The FastAPI request object for rate limiting.
        name: LLM config primary key.
        db: Request-scoped DB session.
        current_session: Authenticated chat session.

    Returns:
        The updated LLM config row (masked projection) with refreshed hash.

    Raises:
        HTTPException: 404 when missing, 422 on name change, empty payload
            or explicit null on a NOT NULL field.
    """
    body = await _read_patch_body(request)
    payload = _validate_payload(LlmConfigUpdate, body)
    try:
        config = db.get(LlmConfig, name)
        if config is None:
            raise HTTPException(status_code=404, detail=f"llm config '{name}' not found")

        updates = payload.model_dump(exclude_unset=True)
        if not updates:
            raise HTTPException(status_code=422, detail="nothing to update")

        null_fields = sorted(
            field for field, value in updates.items() if value is None and field in _LLM_CONFIG_NOT_NULL_PATCH_FIELDS
        )
        if null_fields:
            logger.warning("llm_config_update_rejected_null", name=name, fields=null_fields)
            raise HTTPException(
                status_code=422,
                detail=f"{', '.join(null_fields)}: null is not allowed; omit the field to keep it unchanged",
            )

        for field, value in updates.items():
            setattr(config, field, value)
        config.content_hash = compute_llm_config_hash(config)
        db.add(config)
        db.commit()
        db.refresh(config)
        logger.info("llm_config_updated", name=name)
        return _llm_config_read(config)
    except HTTPException:
        raise
    except Exception as exc:
        logger.exception("llm_config_update_failed", name=name)
        raise HTTPException(status_code=500, detail=str(exc)) from exc


@router.delete("/llm-configs/{name}")
@limiter.limit(settings.RATE_LIMIT_ENDPOINTS["llm_config"][0])
async def delete_llm_config(
    request: Request,
    name: str,
    db: DBSession = Depends(get_db_session),
    current_session: ChatSession = Depends(get_current_session),
) -> None:
    """Delete an LLM configuration.

    Guards: the bootstrap-seeded ``default`` config is undeletable, and any
    config still referenced by an AgentApp or SubAgentConfig ``model`` field
    (explicitly or via the NULL->default resolution) is rejected with 422.

    Args:
        request: The FastAPI request object for rate limiting.
        name: LLM config primary key.
        db: Request-scoped DB session.
        current_session: Authenticated chat session.

    Raises:
        HTTPException: 404 when missing, 422 when protected or referenced.
    """
    try:
        config = db.get(LlmConfig, name)
        if config is None:
            raise HTTPException(status_code=404, detail=f"llm config '{name}' not found")
        if name == DEFAULT_LLM_CONFIG_NAME:
            logger.warning("llm_config_delete_rejected", name=name, reason="default_protected")
            raise HTTPException(status_code=422, detail=f"llm config '{DEFAULT_LLM_CONFIG_NAME}' cannot be deleted")

        referencing_apps = db.exec(select(AgentApp).where(col(AgentApp.model) == name)).all()
        referencing_subagents = db.exec(select(SubAgentConfig).where(col(SubAgentConfig.model) == name)).all()
        if referencing_apps or referencing_subagents:
            owners = sorted(
                [f"agent_app:{row.name}" for row in referencing_apps]
                + [f"subagent:{row.name}" for row in referencing_subagents]
            )
            logger.warning("llm_config_delete_rejected", name=name, reason="referenced")
            raise HTTPException(
                status_code=422,
                detail=f"llm config '{name}' is referenced by: {', '.join(owners)}",
            )

        db.delete(config)
        db.commit()
        logger.info("llm_config_deleted", name=name)
    except HTTPException:
        raise
    except Exception as exc:
        logger.exception("llm_config_delete_failed", name=name)
        raise HTTPException(status_code=500, detail=str(exc)) from exc


# ---------------------------------------------------------------------------
# Tool catalog
# ---------------------------------------------------------------------------


@router.get("/tools/catalog", response_model=list[ToolCatalogEntry])
@limiter.limit(settings.RATE_LIMIT_ENDPOINTS["tools_catalog"][0])
async def get_tool_catalog(
    request: Request,
    db: DBSession = Depends(get_db_session),
    current_session: ChatSession = Depends(get_current_session),
) -> list[dict[str, Any]]:
    """Expose the merged tool catalog (builtin + MCP with server attribution).

    Args:
        request: The FastAPI request object for rate limiting.
        db: Request-scoped DB session.
        current_session: Authenticated chat session.

    Returns:
        Catalog entries with source labels for form checkbox rendering.
    """
    try:
        entries = await build_tool_catalog(db)
        return [dict(entry) for entry in entries]
    except Exception as exc:
        logger.exception("tool_catalog_read_failed")
        raise HTTPException(status_code=500, detail=str(exc)) from exc
