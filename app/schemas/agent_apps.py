"""Pydantic schemas for agent asset endpoints: sub-agents, skills, agent apps, MCP servers and LLM configs.

Phase-1 scope: no per-user isolation, all assets are globally shared;
``created_by`` is kept for auditing only. Name fields follow the
``^[a-z0-9][a-z0-9_-]*$`` identifier pattern shared with the ORM layer.
"""

from typing import Literal, Optional, Self

from pydantic import BaseModel, Field, field_validator, model_validator
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

    Attributes:
        name: Globally unique sub-agent name (immutable after creation)
        description: Human-readable description shown to the orchestrating agent
        when_to_use: Guidance describing when this sub-agent should be invoked
        system_prompt: System prompt used when the sub-agent runs
        allowed_tools: Optional tool whitelist (None = inherit from parent agent)
        model: Optional LLM model override
        max_turns: Optional turn budget limit
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


class SubAgentUpdate(BaseModel):
    """Partial update model for a sub-agent (PATCH semantics; name is immutable).

    Attributes:
        description: Updated description
        when_to_use: Updated invocation guidance
        system_prompt: Updated system prompt
        allowed_tools: Updated tool whitelist (None = inherit from parent agent)
        model: Updated LLM model override
        max_turns: Updated turn budget limit
    """

    description: Optional[str] = Field(default=None, description="Updated description")
    when_to_use: Optional[str] = Field(default=None, description="Updated invocation guidance")
    system_prompt: Optional[str] = Field(default=None, description="Updated system prompt")
    allowed_tools: Optional[list[str]] = Field(
        default=None, description="Updated tool whitelist (None = inherit from parent agent)"
    )
    model: Optional[str] = Field(default=None, description="Updated LLM model override")
    max_turns: Optional[int] = Field(default=None, ge=1, description="Updated turn budget limit")


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
    """

    final_message: str = Field(..., description="Final assistant message produced by the run")
    turns: int = Field(..., description="Number of agent turns consumed")
    duration_seconds: float = Field(..., description="Wall-clock duration of the run")
    model: str = Field(..., description="LLM model that executed the run")


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
    subagent_names: list[str] = Field(
        default_factory=list, description="Names of sub-agent configs bound to this app"
    )
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
    subagent_names: Optional[list[str]] = Field(
        default=None, description="Replacement list of bound sub-agent names"
    )
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
    version: int = Field(..., description="Monotonic configuration version counter")
    created_by: Optional[str] = Field(default=None, description="Audit-only creator identifier")


# ---------------------------------------------------------------------------
# MCP servers
# ---------------------------------------------------------------------------


class McpServerCreate(BaseModel):
    """Request model for registering an MCP server.

    Transport constraints: stdio requires ``command`` (and forbids ``url``);
    http requires ``url`` (and forbids ``command``).

    Attributes:
        name: Globally unique MCP server name (immutable after creation)
        transport: Transport backend (stdio|http)
        command: Executable command for stdio transport
        args: Argument list for the stdio command
        env: Extra environment variables for the stdio process
        url: Endpoint URL for http transport
        headers: Extra HTTP headers for http transport
        enabled: Whether this server is active at runtime
        description: Human-readable description of the server
    """

    name: str = _name_field("Globally unique MCP server name")  # pyright: ignore[reportAssignmentType]
    transport: Literal["stdio", "http"] = Field(..., description="Transport backend")
    command: Optional[str] = Field(default=None, description="Executable command for stdio transport")
    args: list[str] = Field(default_factory=list, description="Argument list for the stdio command")
    env: dict[str, str] = Field(default_factory=dict, description="Extra environment variables for stdio")
    url: Optional[str] = Field(default=None, description="Endpoint URL for http transport")
    headers: dict[str, str] = Field(default_factory=dict, description="Extra HTTP headers for http transport")
    enabled: bool = Field(default=True, description="Whether this server is active at runtime")
    description: str = Field(default="", description="Human-readable description of the server")

    @model_validator(mode="after")
    def validate_transport_fields(self) -> Self:
        """Enforce command/url mutual exclusion and presence per transport.

        Returns:
            Self: The validated model

        Raises:
            ValueError: If required fields are missing or forbidden for the transport
        """
        if self.transport == "stdio":
            if not self.command:
                raise ValueError("command is required for stdio transport")
            if self.url is not None:
                raise ValueError("url must not be set for stdio transport")
        else:
            if not self.url:
                raise ValueError("url is required for http transport")
            if self.command is not None:
                raise ValueError("command must not be set for http transport")
        return self


class McpServerUpdate(BaseModel):
    """Partial update model for an MCP server (PATCH semantics; name is immutable).

    Attributes:
        transport: Updated transport backend (stdio|http)
        command: Updated executable command for stdio transport
        args: Replacement argument list for the stdio command
        env: Replacement environment variables for the stdio process
        url: Updated endpoint URL for http transport
        headers: Replacement HTTP headers for http transport
        enabled: Updated active flag
        description: Updated description
    """

    transport: Optional[Literal["stdio", "http"]] = Field(default=None, description="Updated transport backend")
    command: Optional[str] = Field(default=None, description="Updated executable command for stdio transport")
    args: Optional[list[str]] = Field(default=None, description="Replacement argument list for the stdio command")
    env: Optional[dict[str, str]] = Field(default=None, description="Replacement environment variables for stdio")
    url: Optional[str] = Field(default=None, description="Updated endpoint URL for http transport")
    headers: Optional[dict[str, str]] = Field(default=None, description="Replacement HTTP headers for http transport")
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
                raise ValueError("url is required for http transport")
            if self.command is not None:
                raise ValueError("command must not be set for http transport")
        return self


class McpServerRead(BaseModel):
    """Response model for an MCP server.

    Attributes:
        name: Globally unique MCP server name
        transport: Transport backend (stdio|http)
        command: Executable command for stdio transport
        args: Argument list for the stdio command
        env: Extra environment variables for the stdio process
        url: Endpoint URL for http transport
        headers: Extra HTTP headers for http transport
        enabled: Whether this server is active at runtime
        description: Human-readable description of the server
        content_hash: Hash of the effective content (used for publish/versioning)
        created_by: Audit-only creator identifier
    """

    name: str = Field(..., description="Globally unique MCP server name")
    transport: Literal["stdio", "http"] = Field(..., description="Transport backend")
    command: Optional[str] = Field(default=None, description="Executable command for stdio transport")
    args: list[str] = Field(default_factory=list, description="Argument list for the stdio command")
    env: dict[str, str] = Field(default_factory=dict, description="Extra environment variables for stdio")
    url: Optional[str] = Field(default=None, description="Endpoint URL for http transport")
    headers: dict[str, str] = Field(default_factory=dict, description="Extra HTTP headers for http transport")
    enabled: bool = Field(..., description="Whether this server is active at runtime")
    description: str = Field(default="", description="Human-readable description of the server")
    content_hash: str = Field(..., description="Hash of the effective content")
    created_by: Optional[str] = Field(default=None, description="Audit-only creator identifier")


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


# ---------------------------------------------------------------------------
# LLM configs
# ---------------------------------------------------------------------------


class LlmConfigCreate(BaseModel):
    """Request model for creating an LLM configuration.

    Attributes:
        name: Globally unique LLM config name (immutable after creation)
        model_name: Upstream model identifier sent to the provider
        api_key: Provider API key (stored as configured; never echoed back)
        base_url: Optional OpenAI-compatible endpoint (None = SDK env fallback)
        temperature: Optional sampling temperature override
        max_tokens: Optional completion token budget override
        enabled: Whether this config may be resolved at runtime
        description: Human-readable description of the config
    """

    name: str = _name_field("Globally unique LLM config name")  # pyright: ignore[reportAssignmentType]
    model_name: str = Field(..., min_length=1, description="Upstream model identifier sent to the provider")
    api_key: str = Field(..., min_length=1, description="Provider API key (never echoed back by the API)")
    base_url: Optional[str] = Field(
        default=None, description="Optional OpenAI-compatible endpoint (None = SDK env fallback)"
    )
    temperature: Optional[float] = Field(
        default=None, ge=0.0, le=2.0, description="Optional sampling temperature override"
    )
    max_tokens: Optional[int] = Field(default=None, ge=1, description="Optional completion token budget override")
    enabled: bool = Field(default=True, description="Whether this config may be resolved at runtime")
    description: str = Field(default="", description="Human-readable description of the config")

    @field_validator("base_url", mode="after")
    @classmethod
    def normalize_empty_base_url(cls, value: Optional[str]) -> Optional[str]:
        """Normalize an empty-string endpoint to None (SDK env fallback chain)."""
        return None if value == "" else value


class LlmConfigUpdate(BaseModel):
    """Partial update model for an LLM config (PATCH semantics; name is immutable).

    ``api_key`` omitted = the stored key is kept unchanged.

    Attributes:
        model_name: Updated upstream model identifier
        api_key: Replacement provider API key (omit to keep the stored key)
        base_url: Updated endpoint URL
        temperature: Updated sampling temperature override
        max_tokens: Updated completion token budget override
        enabled: Updated active flag
        description: Updated description
    """

    model_name: Optional[str] = Field(default=None, min_length=1, description="Updated upstream model identifier")
    api_key: Optional[str] = Field(
        default=None, min_length=1, description="Replacement provider API key (omit to keep the stored key)"
    )
    base_url: Optional[str] = Field(default=None, description="Updated endpoint URL")
    temperature: Optional[float] = Field(
        default=None, ge=0.0, le=2.0, description="Updated sampling temperature override"
    )
    max_tokens: Optional[int] = Field(default=None, ge=1, description="Updated completion token budget override")
    enabled: Optional[bool] = Field(default=None, description="Updated active flag")
    description: Optional[str] = Field(default=None, description="Updated description")

    @field_validator("base_url", mode="after")
    @classmethod
    def normalize_empty_base_url(cls, value: Optional[str]) -> Optional[str]:
        """Normalize an empty-string endpoint to None (SDK env fallback chain)."""
        return None if value == "" else value


class LlmConfigRead(BaseModel):
    """Response model for an LLM configuration.

    Physically excludes ``api_key``: only the masked projection
    ``api_key_masked`` (``****`` + last four characters) is ever returned.

    Attributes:
        name: Globally unique LLM config name
        model_name: Upstream model identifier sent to the provider
        api_key_masked: Masked form of the stored API key
        base_url: OpenAI-compatible endpoint (None = SDK env fallback)
        temperature: Sampling temperature override
        max_tokens: Completion token budget override
        enabled: Whether this config may be resolved at runtime
        description: Human-readable description of the config
        content_hash: Hash of the effective content (used for publish/versioning)
        created_by: Audit-only creator identifier
    """

    name: str = Field(..., description="Globally unique LLM config name")
    model_name: str = Field(..., description="Upstream model identifier sent to the provider")
    api_key_masked: str = Field(..., description="Masked form of the stored API key")
    base_url: Optional[str] = Field(default=None, description="OpenAI-compatible endpoint")
    temperature: Optional[float] = Field(default=None, description="Sampling temperature override")
    max_tokens: Optional[int] = Field(default=None, description="Completion token budget override")
    enabled: bool = Field(..., description="Whether this config may be resolved at runtime")
    description: str = Field(default="", description="Human-readable description of the config")
    content_hash: str = Field(..., description="Hash of the effective content")
    created_by: Optional[str] = Field(default=None, description="Audit-only creator identifier")
