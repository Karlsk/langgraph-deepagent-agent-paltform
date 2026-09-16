"""Persistence model for workflow execution history.

Every workflow run (success or failure) is stored as a ``WorkflowRun`` row
carrying the input, output, execution logs and timing data. This enables
the Dify-like trace viewing feature where users can inspect past runs.
"""

from typing import Any, Optional

from sqlalchemy import JSON, Column, Index, Text
from sqlmodel import Field

from app.models.base import BaseModel


class WorkflowRun(BaseModel, table=True):
    """One recorded execution of a workflow definition.

    Attributes:
        id: Autoincrement primary key.
        workflow_id: The workflow definition id (indexed for per-workflow listing).
        run_id: Unique run identifier from the engine (unique index for detail lookups).
        status: Run outcome (``success`` | ``failed``).
        input_data: The input payload the workflow was invoked with.
        output_data: The workflow's final output state (empty on failure).
        error_message: Failure reason (None on success).
        execution_logs: Per-node execution log entries (structured dicts).
        duration_ms: Wall-clock duration in milliseconds.
        node_count: Number of nodes in the workflow definition at run time.
        created_by: Audit-only identifier of the user who triggered the run.
    """

    __tablename__ = "workflow_run"  # pyright: ignore[reportAssignmentType]
    __table_args__ = (
        Index("ix_workflow_run_created_at", "created_at"),
    )

    id: int = Field(default=None, primary_key=True)
    workflow_id: str = Field(index=True)
    run_id: str = Field(unique=True)
    status: str
    input_data: dict[str, Any] = Field(default_factory=dict, sa_column=Column(JSON, nullable=False))
    output_data: dict[str, Any] = Field(default_factory=dict, sa_column=Column(JSON, nullable=False))
    error_message: Optional[str] = Field(default=None, sa_column=Column(Text))
    execution_logs: list[dict[str, Any]] = Field(default_factory=list, sa_column=Column(JSON, nullable=False))
    duration_ms: float = 0.0
    node_count: int = 0
    created_by: Optional[str] = Field(default=None)
