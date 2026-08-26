"""Pydantic schemas for agent asset endpoints: sub-agents, skills, agent apps and MCP servers.

Phase-1 scope: no per-user isolation, all assets are globally shared;
``created_by`` is kept for auditing only. Name fields follow the
``^[a-z0-9][a-z0-9_-]*$`` identifier pattern shared with the ORM layer.

Asset ``model`` fields reference a model config as ``"<provider>/<model>"``
(NULL resolves to ``default/default``); provider/model schemas live in
``app.schemas.providers``.
"""

from datetime import datetime
from typing import Any, Literal, Optional, Self

from pydantic import BaseModel, Field, model_validator
from pydantic.fields import FieldInfo

NAME_PATTERN = r"^[a-z0-9][a-z0-9_-]*$"
NAME_MAX_LENGTH = 64


def _name_field(description: str) -> FieldInfo:
    """Build the shared identifier field used by all Create schemas."""
    return Field(
        ...,
        description=description,
        pattern=NAME_PATTERN,
        max_length=NAME_MAX_LENGTH,
    )


# ---------------------------------------------------------------------------
# Sub-agents
# ---------------------------------------------------------------------------


class SubAgentCreate(BaseModel):
    """Request model for creating a reusable sub-agent.

    Inheritance semantics for ``skill_names`` mirror those of the parent agent
    app:

    * ``None`` (default): inherit the parent AgentApp's published skill set.
    * ``[]``: explicitly bind no skills (overrides parent inheritance).
    * ``["pdf-export", ...]``: explicit whitelist scoped to this sub-agent.

    Standalone single-shot tests (no parent app) treat ``None`` as ``[]``
    because there is no parent to inherit from.

    Attributes:
        name: Globally unique sub-agent name (immutable after creation)
        description: Human-readable description shown to the orchestrating agent
        when_to_use: Guidance describing when this sub-agent should be invoked
        system_prompt: System prompt used when the sub-agent runs
        allowed_tools: Optional tool whitelist (None = inherit from parent agent)
        model: Optional LLM model override
        max_turns: Optional turn budget limit
        skill_names: Optional skill whitelist (None = inherit from parent agent)
    """

    name: str = _name_field("Globally unique sub-agent name")  # pyright: ignore[reportAssignmentType]
    description: str = Field(..., description="Human-readable description shown to the orchestrating agent")
    when_to_use: str = Field(..., description="Guidance describing when this sub-agent should be invoked")
    system_prompt: str = Field(..., description="System prompt used when the sub-agent runs")
    allowed_tools: Optional[list[str]] = Field(
        default=None, description="Optional tool whitelist (None = inherit from parent agent)"
    )
    model: Optional[str] = Field(default=None, description="Optional LLM model override")
    max_turns: Optional[int] = Field(default=None, ge=1, description="Optional turn budget limit")
    skill_names: Optional[list[str]] = Field(
        default=None,
        description=(
            "Optional skill whitelist (None = inherit parent agent app's skill set, "
            "[] = explicitly bind no skills, ['pdf-export', ...] = explicit whitelist)"
        ),
    )


class SubAgentUpdate(BaseModel):
    """Partial update model for a sub-agent (PATCH semantics; name is immutable).

    List/dict fields use whole-replacement semantics: passing a value replaces
    the stored collection entirely; omitting it leaves it untouched. Use
    ``skill_names`` to replace the bound skill whitelist; ``None`` means the
    field was not provided in the request body.

    Attributes:
        description: Updated description
        when_to_use: Updated invocation guidance
        system_prompt: Updated system prompt
        allowed_tools: Updated tool whitelist (None = inherit from parent agent)
        model: Updated LLM model override
        max_turns: Updated turn budget limit
        skill_names: Replacement skill whitelist (None = not provided in PATCH body)
    """

    description: Optional[str] = Field(default=None, description="Updated description")
    when_to_use: Optional[str] = Field(default=None, description="Updated invocation guidance")
    system_prompt: Optional[str] = Field(default=None, description="Updated system prompt")
    allowed_tools: Optional[list[str]] = Field(
        default=None, description="Updated tool whitelist (None = inherit from parent agent)"
    )
    model: Optional[str] = Field(default=None, description="Updated LLM model override")
    max_turns: Optional[int] = Field(default=None, ge=1, description="Updated turn budget limit")
    skill_names: Optional[list[str]] = Field(
        default=None,
        description=(
            "Replacement skill whitelist (None = not provided; [] = explicitly no skills; "
            "['pdf-export', ...] = explicit whitelist)"
        ),
    )


class SubAgentRead(BaseModel):
    """Response model for a sub-agent.

    Attributes:
        name: Globally unique sub-agent name
        description: Human-readable description shown to the orchestrating agent
        when_to_use: Guidance describing when this sub-agent should be invoked
        system_prompt: System prompt used when the sub-agent runs
        allowed_tools: Optional tool whitelist (None = inherit from parent agent)
        model: Optional LLM model override
        max_turns: Optional turn budget limit
        skill_names: Optional skill whitelist (None = inherit parent agent app's skill set)
        content_hash: Hash of the effective content (used for publish/versioning)
        version: Monotonic configuration version counter
        created_by: Audit-only creator identifier
    """

    name: str = Field(..., description="Globally unique sub-agent name")
    description: str = Field(..., description="Human-readable description shown to the orchestrating agent")
    when_to_use: str = Field(..., description="Guidance describing when this sub-agent should be invoked")
    system_prompt: str = Field(..., description="System prompt used when the sub-agent runs")
    allowed_tools: Optional[list[str]] = Field(default=None, description="Optional tool whitelist")
    model: Optional[str] = Field(default=None, description="Optional LLM model override")
    max_turns: Optional[int] = Field(default=None, description="Optional turn budget limit")
    skill_names: Optional[list[str]] = Field(
        default=None,
        description=(
            "Optional skill whitelist (None = inherit parent agent app's skill set, "
            "[] = explicitly bind no skills, [...] = explicit whitelist)"
        ),
    )
    content_hash: str = Field(..., description="Hash of the effective content")
    version: int = Field(..., description="Monotonic configuration version counter")
    created_by: Optional[str] = Field(default=None, description="Audit-only creator identifier")


class SubAgentTestRequest(BaseModel):
    """Request model for test-running a sub-agent.

    Attributes:
        prompt: User prompt used to exercise the sub-agent
    """

    prompt: str = Field(..., min_length=1, description="User prompt used to exercise the sub-agent")


class SubAgentTestResult(BaseModel):
    """Response model for a sub-agent test run.

    Attributes:
        final_message: Final assistant message produced by the run
        turns: Number of agent turns consumed
        duration_seconds: Wall-clock duration of the run
        model: LLM model that executed the run
        trace_id: Id of the persisted execution trace (queryable via
            ``GET /subagents/{name}/test-traces/{trace_id}``); None only
            when trace persistence failed
    """

    final_message: str = Field(..., description="Final assistant message produced by the run")
    turns: int = Field(..., description="Number of agent turns consumed")
    duration_seconds: float = Field(..., description="Wall-clock duration of the run")
    model: str = Field(..., description="LLM model that executed the run")
    trace_id: Optional[int] = Field(default=None, description="Id of the persisted execution trace")


class SubAgentTraceSummary(BaseModel):
    """Summary row of a persisted sub-agent test run trace (no event stream).

    Attributes:
        id: Trace primary key
        status: Run outcome (success|error)
        prompt: User prompt the sub-agent was invoked with
        model: LLM model id that executed the run
        turns: Number of model turns consumed
        duration_seconds: Wall-clock duration of the run
        final_message: Final assistant message (empty on failed runs)
        error: Stringified failure reason (None on success)
        created_by: Audit-only identifier of the user who triggered the run
        created_at: Timestamp at which the trace was recorded
    """

    id: int = Field(..., description="Trace primary key")
    status: str = Field(..., description="Run outcome (success|error)")
    prompt: str = Field(..., description="User prompt the sub-agent was invoked with")
    model: str = Field(..., description="LLM model id that executed the run")
    turns: int = Field(..., description="Number of model turns consumed")
    duration_seconds: float = Field(..., description="Wall-clock duration of the run")
    final_message: str = Field(default="", description="Final assistant message (empty on failed runs)")
    error: Optional[str] = Field(default=None, description="Stringified failure reason (None on success)")
    created_by: Optional[str] = Field(default=None, description="Audit-only identifier of the triggering user")
    created_at: datetime = Field(..., description="Timestamp at which the trace was recorded")


class SubAgentTraceDetail(SubAgentTraceSummary):
    """Full trace of one sub-agent test run, including the event stream.

    Attributes:
        events: Structured trace event stream (llm_call / tool_call /
            run_finished entries; see app.services.agents.run_tracer)
    """

    events: list[dict[str, Any]] = Field(default_factory=list, description="Structured trace event stream")


# ---------------------------------------------------------------------------
# Skills
# ---------------------------------------------------------------------------


class SkillCreate(BaseModel):
    """Request model for creating a skill asset.

    Attributes:
        name: Globally unique skill name (immutable after creation)
        description: Human-readable description of the skill
        body: Full SKILL.md content
    """

    name: str = _name_field("Globally unique skill name")  # pyright: ignore[reportAssignmentType]
    description: str = Field(default="", description="Human-readable description of the skill")
    body: str = Field(..., min_length=1, description="Full SKILL.md content")


class SkillUpdate(BaseModel):
    """Partial update model for a skill (PATCH semantics; name is immutable).

    Attributes:
        description: Updated description
        body: Updated full SKILL.md content
    """

    description: Optional[str] = Field(default=None, description="Updated description")
    body: Optional[str] = Field(default=None, min_length=1, description="Updated full SKILL.md content")


class SkillRead(BaseModel):
    """Response model for a skill asset (metadata only; body served separately).

    Attributes:
        name: Globally unique skill name
        description: Human-readable description of the skill
        content_hash: Hash of the skill content (used for publish/versioning)
        version: Monotonic configuration version counter
        created_by: Audit-only creator identifier
    """

    name: str = Field(..., description="Globally unique skill name")
    description: str = Field(..., description="Human-readable description of the skill")
    content_hash: str = Field(..., description="Hash of the skill content")
    version: int = Field(..., description="Monotonic configuration version counter")
    created_by: Optional[str] = Field(default=None, description="Audit-only creator identifier")


class SkillContentRead(BaseModel):
    """Response model for the raw body of a skill asset.

    Attributes:
        name: Globally unique skill name
        content: Full SKILL.md content
    """

    name: str = Field(..., description="Globally unique skill name")
    content: str = Field(..., description="Full SKILL.md content")


class SkillRefreshEntry(BaseModel):
    """One skill's outcome of a disk-refresh-from-DB operation.

    Attributes:
        name: Globally unique skill name
        action: What the refresh did:
            ``rewritten`` — disk file was missing or drifted from the DB
            hash and has been rewritten from the DB body;
            ``unchanged`` — disk hash matched, file left untouched;
            ``backfilled`` — legacy NULL-body row populated from its disk
            file (the only surviving copy), hash resynced;
            ``missing`` — both copies lost, skill is unrecoverable.
    """

    name: str = Field(..., description="Globally unique skill name")
    action: Literal["rewritten", "unchanged", "backfilled", "missing"] = Field(
        ..., description="Refresh outcome for this skill"
    )


class SkillRefreshReport(BaseModel):
    """Aggregated report of a disk-refresh-from-DB operation.

    Attributes:
        items: Per-skill outcome entries.
        total: Number of skills inspected.
        rewritten: Files rewritten from the DB body.
        unchanged: Files already matching the DB hash.
        backfilled: Legacy NULL-body rows populated from disk.
        missing: Skills lost from both stores.
    """

    items: list[SkillRefreshEntry] = Field(default_factory=list, description="Per-skill outcomes")
    total: int = Field(..., description="Number of skills inspected")
    rewritten: int = Field(..., description="Files rewritten from the DB body")
    unchanged: int = Field(..., description="Files already matching the DB hash")
    backfilled: int = Field(..., description="Legacy rows populated from disk")
    missing: int = Field(..., description="Skills lost from both stores")


class SkillSyncEntry(BaseModel):
    """One outcome entry of a workspace-sync (directory reconciliation).

    Attributes:
        name: Skill name; invalid entries carry the relative file path
            (``<dir>/SKILL.md``) instead.
        action: What the sync did / would do:
            ``unchanged`` — DB row and disk file match;
            ``rewritten`` — disk file drifted or was missing and is
            rewritten from the DB row (DB is the source of truth);
            ``imported`` — disk-only file imported as a new DB row;
            ``invalid`` — file rejected per-file (see ``reason``).
        reason: Rejection detail for ``invalid`` entries (else None).
    """

    name: str = Field(..., description="Skill name (invalid entries carry the file path)")
    action: Literal["unchanged", "rewritten", "imported", "invalid"] = Field(
        ..., description="Sync outcome for this entry"
    )
    reason: Optional[str] = Field(default=None, description="Rejection reason (invalid only)")


class SkillSyncReport(BaseModel):
    """Aggregated report of a workspace-sync (dry-run or applied).

    Attributes:
        items: Per-skill / per-file outcome entries.
        scanned: Number of SKILL.md files found in the directory.
        unchanged: DB rows whose rendered file matches disk.
        rewritten: Files (re)written from the DB row.
        imported: Disk-only files imported as new DB rows.
        invalid: Files degraded per-file with a reason.
    """

    items: list[SkillSyncEntry] = Field(default_factory=list, description="Per-entry outcomes")
    scanned: int = Field(..., description="SKILL.md files found in the directory")
    unchanged: int = Field(..., description="Rows matching their disk file")
    rewritten: int = Field(..., description="Files rewritten from the DB row")
    imported: int = Field(..., description="Disk-only files imported into the DB")
    invalid: int = Field(..., description="Files degraded per-file")


class SkillGenerateRequest(BaseModel):
    """Request model for LLM-assisted skill draft generation.

    Attributes:
        description: What the skill should do
        hint: Optional extra guidance for generation
    """

    description: str = Field(..., description="What the skill should do")
    hint: str = Field(default="", description="Optional extra guidance for generation")


class SkillGenerateResponse(BaseModel):
    """Response model for skill draft generation.

    Attributes:
        draft: Generated SKILL.md draft content
    """

    draft: str = Field(..., description="Generated SKILL.md draft content")


# ---------------------------------------------------------------------------
# Agent apps
# ---------------------------------------------------------------------------


class AgentAppCreate(BaseModel):
    """Request model for creating an agent application.

    Attributes:
        name: Globally unique application name (immutable after creation)
        system_prompt: System prompt of the assembled agent
        allowed_tools: Optional tool whitelist (None = engine default)
        model: Optional LLM model override
        skill_names: Names of skill assets bound to this app
        subagent_names: Names of sub-agent configs bound to this app
        interrupt_on: Interrupt configuration passed to the engine
    """

    name: str = _name_field("Globally unique application name")  # pyright: ignore[reportAssignmentType]
    system_prompt: str = Field(..., description="System prompt of the assembled agent")
    allowed_tools: Optional[list[str]] = Field(
        default=None, description="Optional tool whitelist (None = engine default)"
    )
    model: Optional[str] = Field(default=None, description="Optional LLM model override")
    skill_names: list[str] = Field(default_factory=list, description="Names of skill assets bound to this app")
    subagent_names: list[str] = Field(default_factory=list, description="Names of sub-agent configs bound to this app")
    interrupt_on: dict[str, bool] = Field(default_factory=dict, description="Interrupt configuration for the engine")


class AgentAppUpdate(BaseModel):
    """Partial update model for an agent app (PATCH semantics; name is immutable).

    List/dict fields use whole-replacement semantics: passing a value replaces
    the stored collection entirely; omitting it leaves it untouched.

    Attributes:
        system_prompt: Updated system prompt
        allowed_tools: Updated tool whitelist
        model: Updated LLM model override
        skill_names: Replacement list of bound skill names
        subagent_names: Replacement list of bound sub-agent names
        interrupt_on: Replacement interrupt configuration
    """

    system_prompt: Optional[str] = Field(default=None, description="Updated system prompt")
    allowed_tools: Optional[list[str]] = Field(default=None, description="Updated tool whitelist")
    model: Optional[str] = Field(default=None, description="Updated LLM model override")
    skill_names: Optional[list[str]] = Field(default=None, description="Replacement list of bound skill names")
    subagent_names: Optional[list[str]] = Field(default=None, description="Replacement list of bound sub-agent names")
    interrupt_on: Optional[dict[str, bool]] = Field(default=None, description="Replacement interrupt configuration")


class AgentAppRead(BaseModel):
    """Response model for an agent application.

    Attributes:
        id: Primary key (referenced by session.agent_app_id without FK constraint)
        name: Globally unique application name
        system_prompt: System prompt of the assembled agent
        allowed_tools: Optional tool whitelist (None = engine default)
        model: Optional LLM model override
        skill_names: Names of skill assets bound to this app
        subagent_names: Names of sub-agent configs bound to this app
        interrupt_on: Interrupt configuration passed to the engine
        engine: Execution engine backend
        status: Lifecycle status (draft|published)
        published_hash: Hash snapshot of the last published revision
        agent_dir: Agent workspace directory stamped at publish time
        workspace_hash: Content hash over the agent workspace skill files
        agent_workspace_status: Agent workspace materialization status
        version: Monotonic configuration version counter
        created_by: Audit-only creator identifier
    """

    id: int = Field(..., description="Primary key of the agent app")
    name: str = Field(..., description="Globally unique application name")
    system_prompt: str = Field(..., description="System prompt of the assembled agent")
    allowed_tools: Optional[list[str]] = Field(default=None, description="Optional tool whitelist")
    model: Optional[str] = Field(default=None, description="Optional LLM model override")
    skill_names: list[str] = Field(default_factory=list, description="Names of bound skill assets")
    subagent_names: list[str] = Field(default_factory=list, description="Names of bound sub-agent configs")
    interrupt_on: dict[str, bool] = Field(default_factory=dict, description="Interrupt configuration")
    engine: str = Field(..., description="Execution engine backend")
    status: str = Field(..., description="Lifecycle status (draft|published)")
    published_hash: Optional[str] = Field(default=None, description="Hash snapshot of the last published revision")
    agent_dir: Optional[str] = Field(default=None, description="Agent workspace directory stamped at publish time")
    workspace_hash: Optional[str] = Field(default=None, description="Content hash over the agent workspace skill files")
    agent_workspace_status: str = Field(default="pending", description="Agent workspace materialization status (pending|ready|stale)")
    version: int = Field(..., description="Monotonic configuration version counter")
    created_by: Optional[str] = Field(default=None, description="Audit-only creator identifier")


# ---------------------------------------------------------------------------
# MCP servers
# ---------------------------------------------------------------------------


class McpServerCreate(BaseModel):
    """Request model for registering an MCP server.

    Transport constraints: stdio requires ``command`` (and forbids ``url``);
    sse and http require ``url`` (and forbid ``command``).

    Attributes:
        name: Globally unique MCP server name (immutable after creation; must
            not contain ``__`` — the reserved ``{server}__{tool}`` namespace
            separator)
        transport: Transport backend (stdio|sse|http)
        command: Executable command for stdio transport
        args: Argument list for the stdio command
        env: Extra environment variables for the stdio process
        url: Endpoint URL for sse/http transports
        headers: Extra HTTP headers for sse/http transports
        enabled: Whether this server is active at runtime
        description: Human-readable description of the server
    """

    name: str = _name_field("Globally unique MCP server name")  # pyright: ignore[reportAssignmentType]
    transport: Literal["stdio", "sse", "http"] = Field(..., description="Transport backend")
    command: Optional[str] = Field(default=None, description="Executable command for stdio transport")
    args: list[str] = Field(default_factory=list, description="Argument list for the stdio command")
    env: dict[str, str] = Field(default_factory=dict, description="Extra environment variables for stdio")
    url: Optional[str] = Field(default=None, description="Endpoint URL for sse/http transports")
    headers: dict[str, str] = Field(default_factory=dict, description="Extra HTTP headers for sse/http transports")
    enabled: bool = Field(default=True, description="Whether this server is active at runtime")
    description: str = Field(default="", description="Human-readable description of the server")

    @model_validator(mode="after")
    def validate_transport_fields(self) -> Self:
        """Enforce command/url mutual exclusion, presence and the name policy.

        Returns:
            Self: The validated model

        Raises:
            ValueError: If required fields are missing or forbidden for the
                transport, or the name contains the ``__`` namespace separator.
        """
        if "__" in self.name:
            raise ValueError("name must not contain '__' (reserved as the server__tool namespace separator)")
        if self.transport == "stdio":
            if not self.command:
                raise ValueError("command is required for stdio transport")
            if self.url is not None:
                raise ValueError("url must not be set for stdio transport")
        else:
            if not self.url:
                raise ValueError(f"url is required for {self.transport} transport")
            if self.command is not None:
                raise ValueError(f"command must not be set for {self.transport} transport")
        return self


class McpServerUpdate(BaseModel):
    """Partial update model for an MCP server (PATCH semantics; name is immutable).

    Attributes:
        transport: Updated transport backend (stdio|sse|http)
        command: Updated executable command for stdio transport
        args: Replacement argument list for the stdio command
        env: Replacement environment variables for the stdio process
        url: Updated endpoint URL for sse/http transports
        headers: Replacement HTTP headers for sse/http transports
        enabled: Updated active flag
        description: Updated description
    """

    transport: Optional[Literal["stdio", "sse", "http"]] = Field(default=None, description="Updated transport backend")
    command: Optional[str] = Field(default=None, description="Updated executable command for stdio transport")
    args: Optional[list[str]] = Field(default=None, description="Replacement argument list for the stdio command")
    env: Optional[dict[str, str]] = Field(default=None, description="Replacement environment variables for stdio")
    url: Optional[str] = Field(default=None, description="Updated endpoint URL for sse/http transports")
    headers: Optional[dict[str, str]] = Field(
        default=None, description="Replacement HTTP headers for sse/http transports"
    )
    enabled: Optional[bool] = Field(default=None, description="Updated active flag")
    description: Optional[str] = Field(default=None, description="Updated description")

    @model_validator(mode="after")
    def validate_transport_fields(self) -> Self:
        """Enforce command/url requirements when a transport is supplied.

        Returns:
            Self: The validated model

        Raises:
            ValueError: If required fields are missing or forbidden for the transport
        """
        if self.transport is None:
            return self
        if self.transport == "stdio":
            if not self.command:
                raise ValueError("command is required for stdio transport")
            if self.url is not None:
                raise ValueError("url must not be set for stdio transport")
        else:
            if not self.url:
                raise ValueError(f"url is required for {self.transport} transport")
            if self.command is not None:
                raise ValueError(f"command must not be set for {self.transport} transport")
        return self


class McpServerRead(BaseModel):
    """Response model for an MCP server.

    Attributes:
        name: Globally unique MCP server name
        transport: Transport backend (stdio|sse|http)
        command: Executable command for stdio transport
        args: Argument list for the stdio command
        env: Extra environment variables for the stdio process
        url: Endpoint URL for sse/http transports
        headers: Extra HTTP headers for sse/http transports
        enabled: Whether this server is active at runtime
        description: Human-readable description of the server
        content_hash: Hash of the effective content (used for publish/versioning)
        created_by: Audit-only creator identifier
    """

    name: str = Field(..., description="Globally unique MCP server name")
    transport: Literal["stdio", "sse", "http"] = Field(..., description="Transport backend")
    command: Optional[str] = Field(default=None, description="Executable command for stdio transport")
    args: list[str] = Field(default_factory=list, description="Argument list for the stdio command")
    env: dict[str, str] = Field(default_factory=dict, description="Extra environment variables for stdio")
    url: Optional[str] = Field(default=None, description="Endpoint URL for sse/http transports")
    headers: dict[str, str] = Field(default_factory=dict, description="Extra HTTP headers for sse/http transports")
    enabled: bool = Field(..., description="Whether this server is active at runtime")
    description: str = Field(default="", description="Human-readable description of the server")
    content_hash: str = Field(..., description="Hash of the effective content")
    created_by: Optional[str] = Field(default=None, description="Audit-only creator identifier")


class McpToolInfo(BaseModel):
    """One tool of an MCP server as returned by the debug listing endpoint.

    Attributes:
        name: Raw tool name as exposed by the server (un-namespaced)
        description: Tool description
        args_schema: JSON schema of the tool arguments
    """

    name: str = Field(..., description="Raw tool name as exposed by the server")
    description: str = Field(default="", description="Tool description")
    args_schema: dict[str, Any] = Field(default_factory=dict, description="JSON schema of the tool arguments")


class McpToolCallRequest(BaseModel):
    """Request model for the MCP tool debug-call endpoint.

    Attributes:
        tool_name: Raw (un-namespaced) tool name on the target server
        arguments: Tool arguments validated against the tool schema
    """

    tool_name: str = Field(..., min_length=1, description="Raw tool name on the target server")
    arguments: dict[str, Any] = Field(default_factory=dict, description="Tool arguments")


class McpToolCallResult(BaseModel):
    """Response model for the MCP tool debug-call endpoint.

    Attributes:
        server: Owning MCP server name
        tool_name: Raw tool name that was invoked
        result: Tool output content (string or content-block list)
    """

    server: str = Field(..., description="Owning MCP server name")
    tool_name: str = Field(..., description="Raw tool name that was invoked")
    result: Any = Field(default=None, description="Tool output content")


# ---------------------------------------------------------------------------
# Tool catalog
# ---------------------------------------------------------------------------


class ToolCatalogEntry(BaseModel):
    """A single tool entry exposed in the runtime tool catalog.

    Attributes:
        name: Tool name as registered with the engine
        source: Origin of the tool (builtin|mcp)
        server: Owning MCP server name (mcp-sourced tools only)
    """

    name: str = Field(..., description="Tool name as registered with the engine")
    source: Literal["builtin", "mcp"] = Field(..., description="Origin of the tool")
    server: Optional[str] = Field(default=None, description="Owning MCP server name (mcp-sourced tools only)")
