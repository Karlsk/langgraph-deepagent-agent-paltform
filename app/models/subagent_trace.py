"""Persistence model for sub-agent execution traces.

Every recorded sub-agent run (one-shot test executions today; chat-embedded
subagent calls once the chat pipeline lands — G3 §2.1) is stored as a
``SubAgentTrace`` row carrying the structured event stream (LLM calls,
tool calls, run outcome) captured by ``RunTracer``. The table is the single
source of truth for offline behaviour verification and scripted comparison;
visual tracing stays with Langfuse.
"""

from typing import Any, Optional

from sqlalchemy import JSON, Column, Index, Text
from sqlmodel import Field

from app.models.base import BaseModel


class SubAgentTrace(BaseModel, table=True):
    """One recorded execution trace of a sub-agent (test run or chat-embedded).

    Attributes:
        id: Autoincrement primary key (surfaced as ``trace_id`` in the API)
        name: SubAgentConfig name (test rows) or AgentApp name (chat rows,
            G4 §7.1) the run exercised (indexed for per-agent listing)
        status: Run outcome (success|error)
        prompt: User prompt the sub-agent was invoked with
        model: LLM model id that executed the run
        turns: Number of model turns consumed (AIMessage count; on error the
            number of LLM calls observed before failure)
        duration_seconds: Wall-clock duration of the run
        final_message: Final assistant message (empty on failed runs)
        events: Structured trace event stream (llm_call / tool_call /
            run_finished entries, see ``app.services.agents.run_tracer``);
            chat rows carry an ``agent`` field per event (coordinator |
            subagent name, G4 §7.1)
        error: Stringified failure reason (None on success)
        created_by: Audit-only identifier of the user who triggered the run
        source: Row origin — ``test`` (one-shot test runs, default for legacy
            rows) or ``chat`` (G4 chat rounds)
        session_id: Session id of the chat round (chat rows only, G4 §7.1)
    """

    __tablename__ = "subagent_trace"  # pyright: ignore[reportAssignmentType]
    __table_args__ = (Index("ix_subagent_trace_created_at", "created_at"),)

    id: int = Field(default=None, primary_key=True)
    name: str = Field(index=True)
    status: str
    prompt: str = Field(sa_column=Column(Text, nullable=False))
    model: str
    turns: int
    duration_seconds: float
    final_message: str = Field(default="", sa_column=Column(Text, nullable=False))
    events: list[dict[str, Any]] = Field(default_factory=list, sa_column=Column(JSON, nullable=False))
    error: Optional[str] = Field(default=None, sa_column=Column(Text))
    created_by: Optional[str] = Field(default=None)
    source: str = Field(default="test", description="Row origin: test | chat")
    session_id: Optional[str] = Field(default=None, index=True, description="Chat session id (chat rows)")
