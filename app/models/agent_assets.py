"""Agent asset models: sub-agents, skills, agent apps and MCP servers.

Phase-1 scope: no per-user isolation, all assets are globally shared;
``created_by`` is kept for auditing only.
"""

from typing import Optional

from sqlalchemy import JSON, Column
from sqlmodel import Field

from app.models.base import BaseModel

DEFAULT_AGENT_APP_ID: str = "system-default"


class SubAgentConfig(BaseModel, table=True):
    """Configuration of a reusable sub-agent.

    Attributes:
        name: Globally unique sub-agent name, primary key (immutability enforced at API layer)
        description: Human-readable description shown to the orchestrating agent
        when_to_use: Guidance describing when this sub-agent should be invoked
        system_prompt: System prompt used when the sub-agent runs
        allowed_tools: Optional tool whitelist (None = inherit from parent agent)
        model: Optional LLM model override
        max_turns: Optional turn budget limit
        skill_names: Optional whitelist of SkillAsset names bound to this sub-agent.
            Semantics mirror ``AgentApp.skill_names`` but at the sub-agent scope:

            * ``None`` (default): inherit the parent AgentApp's published skill set.
            * ``[]``: explicitly bind no skills (overrides parent inheritance).
            * ``["pdf-export", ...]``: explicit whitelist scoped to this sub-agent.

            Standalone single-shot tests (no parent app) treat ``None`` as ``[]``
            because there is no parent to inherit from.
        content_hash: Hash of the effective content (used for publish/versioning)
        version: Monotonic configuration version counter
        created_by: Audit-only creator identifier
    """

    __tablename__ = "subagent_config"  # pyright: ignore[reportAssignmentType]

    name: str = Field(primary_key=True)
    description: str
    when_to_use: str
    system_prompt: str
    allowed_tools: Optional[list[str]] = Field(default=None, sa_column=Column(JSON))
    model: Optional[str] = Field(default=None)
    max_turns: Optional[int] = Field(default=None)
    skill_names: Optional[list[str]] = Field(default=None, sa_column=Column(JSON))
    content_hash: str
    version: int = Field(default=1)
    created_by: Optional[str] = Field(default=None)


class SkillAsset(BaseModel, table=True):
    """Reusable skill asset (markdown skill content metadata).

    Attributes:
        name: Globally unique skill name, primary key
        description: Human-readable description of the skill
        content_hash: Hash of the skill content (used for publish/versioning)
        created_by: Audit-only creator identifier
        version: Monotonic configuration version counter
    """

    __tablename__ = "skill_asset"  # pyright: ignore[reportAssignmentType]

    name: str = Field(primary_key=True)
    description: str
    content_hash: str
    created_by: Optional[str] = Field(default=None)
    version: int = Field(default=1)


class AgentApp(BaseModel, table=True):
    """Declarative agent application assembled from skills and sub-agents.

    Attributes:
        id: Primary key (referenced by session.agent_app_id without FK constraint)
        name: Globally unique application name
        system_prompt: System prompt of the assembled agent
        allowed_tools: Optional tool whitelist (None = engine default)
        model: Optional LLM model override
        skill_names: Names of SkillAsset entries bound to this app
        subagent_names: Names of SubAgentConfig entries bound to this app
        interrupt_on: Interrupt configuration passed to the engine
        engine: Execution engine backend
        status: Lifecycle status (draft|published)
        published_hash: Hash snapshot of the last published revision
        version: Monotonic configuration version counter
        created_by: Audit-only creator identifier
    """

    __tablename__ = "agent_app"  # pyright: ignore[reportAssignmentType]

    id: int = Field(default=None, primary_key=True)
    name: str = Field(index=True, unique=True)
    system_prompt: str
    allowed_tools: Optional[list[str]] = Field(default=None, sa_column=Column(JSON))
    model: Optional[str] = Field(default=None)
    skill_names: list[str] = Field(default_factory=list, sa_column=Column(JSON))
    subagent_names: list[str] = Field(default_factory=list, sa_column=Column(JSON))
    interrupt_on: dict = Field(default_factory=dict, sa_column=Column(JSON))
    engine: str = Field(default="deepagents")
    status: str = Field(default="draft")
    published_hash: Optional[str] = Field(default=None)
    version: int = Field(default=1)
    created_by: Optional[str] = Field(default=None)


class McpServerConfig(BaseModel, table=True):
    """Connection configuration of an MCP server.

    Attributes:
        name: Globally unique MCP server name, primary key
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

    __tablename__ = "mcp_server_config"  # pyright: ignore[reportAssignmentType]

    name: str = Field(primary_key=True)
    transport: str
    command: Optional[str] = Field(default=None)
    args: list[str] = Field(default_factory=list, sa_column=Column(JSON))
    env: dict = Field(default_factory=dict, sa_column=Column(JSON))
    url: Optional[str] = Field(default=None)
    headers: dict = Field(default_factory=dict, sa_column=Column(JSON))
    enabled: bool = Field(default=True)
    description: str = Field(default="")
    content_hash: str
    created_by: Optional[str] = Field(default=None)
