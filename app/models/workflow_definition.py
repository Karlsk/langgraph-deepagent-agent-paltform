"""Persistence models for workflow definitions (3-table normalized schema).

Stores workflow metadata, nodes and edges as separate tables with FK CASCADE.
The ``body`` column on the parent table holds the full rendered YAML for
bidirectional disk sync (mirrors the skill dual-store pattern).
"""

from datetime import UTC, datetime
from typing import Any, Optional

from sqlalchemy import JSON, Column, DateTime, ForeignKey, String, Text
from sqlmodel import Field, Relationship, SQLModel

from app.models.base import BaseModel


class WorkflowDefinitionAsset(BaseModel, table=True):
    """Workflow definition metadata + sync fields.

    Attributes:
        workflow_id: Unique workflow identifier (PK), matches YAML filename stem.
        description: Human-readable description.
        entry_point: Name of the first node in the graph.
        allow_private_networks: Whether HTTP nodes can access private IPs.
        body: Full rendered YAML content (source of truth for disk sync).
        content_hash: sha256 hex of body; drives disk-vs-DB comparison.
        version: Monotonic version counter, bumped on each save.
        scope: Visibility scope ('user' by default).
        updated_at: Auto-updated timestamp on each modification.
    """

    __tablename__ = "workflow_definition"  # pyright: ignore[reportAssignmentType]

    workflow_id: str = Field(primary_key=True, max_length=64)
    description: str = Field(default="")
    entry_point: str
    allow_private_networks: bool = Field(default=True)
    body: Optional[str] = Field(default=None, sa_column=Column(Text))
    content_hash: str = Field(default="", max_length=64)
    version: int = Field(default=1)
    scope: str = Field(default="user", max_length=16, index=True)
    updated_at: datetime = Field(
        default_factory=lambda: datetime.now(UTC),
        sa_column=Column(DateTime(timezone=True), nullable=False, onupdate=lambda: datetime.now(UTC)),
    )

    nodes: list["WorkflowNodeAsset"] = Relationship(
        sa_relationship_kwargs={"cascade": "all, delete-orphan", "lazy": "selectin"},
    )
    edges: list["WorkflowEdgeAsset"] = Relationship(
        sa_relationship_kwargs={"cascade": "all, delete-orphan", "lazy": "selectin"},
    )


class WorkflowNodeAsset(SQLModel, table=True):
    """One row per node in a workflow definition.

    Attributes:
        id: Autoincrement primary key.
        workflow_id: FK to workflow_definition (CASCADE on delete).
        name: Node name (unique within a workflow).
        type: Node type string (llm, http, python, subworkflow, react, etc.).
        config: Node configuration dict.
        state_keys: Optional state_keys list for the node.
    """

    __tablename__ = "workflow_node"  # pyright: ignore[reportAssignmentType]

    id: Optional[int] = Field(default=None, primary_key=True)
    workflow_id: str = Field(
        sa_column=Column(
            String(64),
            ForeignKey("workflow_definition.workflow_id", ondelete="CASCADE"),
            nullable=False,
            index=True,
        )
    )
    name: str = Field(max_length=128)
    type: str = Field(max_length=64)
    config: dict[str, Any] = Field(default_factory=dict, sa_column=Column(JSON, nullable=False, server_default="{}"))
    state_keys: Optional[list[str]] = Field(default=None, sa_column=Column(JSON, nullable=True))


class WorkflowEdgeAsset(SQLModel, table=True):
    """One row per edge in a workflow definition.

    Attributes:
        id: Autoincrement primary key.
        workflow_id: FK to workflow_definition (CASCADE on delete).
        source: Source node name.
        target: Target node name or "END".
        condition: Optional condition expression for conditional edges.
    """

    __tablename__ = "workflow_edge"  # pyright: ignore[reportAssignmentType]

    id: Optional[int] = Field(default=None, primary_key=True)
    workflow_id: str = Field(
        sa_column=Column(
            String(64),
            ForeignKey("workflow_definition.workflow_id", ondelete="CASCADE"),
            nullable=False,
            index=True,
        )
    )
    source: str = Field(max_length=128)
    target: str = Field(max_length=128)
    condition: Optional[str] = Field(default=None, max_length=512)
