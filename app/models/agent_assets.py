"""Agent asset models: sub-agents, skills, agent apps and MCP servers.

Phase-1 scope: no per-user isolation, all assets are globally shared;
``created_by`` is kept for auditing only.
"""

from datetime import UTC, datetime
from typing import Optional

from sqlalchemy import JSON, Column, ForeignKey, Text, UniqueConstraint
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
    """Reusable skill asset (dual-store: DB body + disk SKILL.md copy).

    Attributes:
        name: Globally unique skill name, primary key
        description: Human-readable description of the skill
        body: Full SKILL.md content stored in the DB (source of truth).
            ``None`` only on legacy rows created before dual-store; those
            rows are backfilled from disk by ``refresh_disk_from_db``.
        content_hash: Hash of the body; on refresh it is the trigger that
            decides whether the disk copy needs rewriting
        created_by: Audit-only creator identifier
        version: Monotonic configuration version counter
        scope: Visibility scope of the skill (G2): 'global' by default,
            Phase 5+ may extend to 'agent' (per-app private copies)
    """

    __tablename__ = "skill_asset"  # pyright: ignore[reportAssignmentType]

    name: str = Field(primary_key=True)
    description: str
    body: Optional[str] = Field(default=None, sa_column=Column(Text))
    content_hash: str
    created_by: Optional[str] = Field(default=None)
    version: int = Field(default=1)
    scope: str = Field(default="global", max_length=16, index=True)


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
        context_size: Optional absolute token threshold for conversation
            compaction (G3 §11.4.2); NULL falls back to
            ``settings.DEFAULT_AGENT_CONTEXT_SIZE`` at compile time
        engine: Execution engine backend
        status: Lifecycle status (draft|published)
        published_hash: Hash snapshot of the last published revision
        version: Monotonic configuration version counter
        created_by: Audit-only creator identifier
        agent_dir: Physical workspace base path template (G2), e.g.
            ``{DATA_ROOT}/agents/{app_id}``; None until bootstrap/publish sets it
        workspace_hash: Agent-layer content fingerprint (sha256 hex) computed
            at publish time; invalidated (NULL) when a draft is patched
        agent_workspace_status: Workspace materialization state (G2 simplified):
            'pending' (needs bootstrap/materialization) or 'active'
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
    context_size: Optional[int] = Field(default=None)
    engine: str = Field(default="deepagents")
    status: str = Field(default="draft")
    published_hash: Optional[str] = Field(default=None)
    version: int = Field(default=1)
    created_by: Optional[str] = Field(default=None)
    agent_dir: Optional[str] = Field(default=None, max_length=255)
    workspace_hash: Optional[str] = Field(default=None, max_length=64)
    agent_workspace_status: str = Field(default="pending", max_length=16)


class UserAgentAppAssociation(BaseModel, table=True):
    """Association row linking a user to an agent app (G2 user-layer tracking).

    One row per (user, agent_app) pair. ``last_synced_workspace_hash`` records
    the agent-layer ``workspace_hash`` observed at the last user-layer
    materialization; the lazy workspace check compares against it to decide
    whether the user layer needs a refresh (spec-g2-workspace v3.3 §3.4).

    Attributes:
        id: Primary key
        user_id: FK to ``user.id`` (ON DELETE CASCADE — dropping a user
            cleans up its associations automatically)
        agent_app_id: FK to ``agent_app.id`` (ON DELETE CASCADE)
        last_synced_workspace_hash: Agent workspace_hash at the last
            user-layer sync (incremental-sync optimization); NULL until the
            first materialization
        associated_at: When the user was associated with the app
    """

    __tablename__ = "user_agent_app_association"  # pyright: ignore[reportAssignmentType]
    __table_args__ = (
        UniqueConstraint("user_id", "agent_app_id", name="uq_user_agent_app"),
    )

    id: Optional[int] = Field(default=None, primary_key=True)
    user_id: int = Field(
        sa_column=Column(
            "user_id",
            ForeignKey("user.id", ondelete="CASCADE"),
            nullable=False,
            index=True,
        )
    )
    agent_app_id: int = Field(
        sa_column=Column(
            "agent_app_id",
            ForeignKey("agent_app.id", ondelete="CASCADE"),
            nullable=False,
            index=True,
        )
    )
    last_synced_workspace_hash: Optional[str] = Field(default=None, max_length=64)
    associated_at: datetime = Field(default_factory=lambda: datetime.now(UTC))


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
