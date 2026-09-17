"""Chatflow wrapper graph: a thin single-node LangGraph around a workflow (D1).

The declarative workflow engine stays untouched (``app/workflow/`` is a black
box reached only through ``registry.execute_workflow``). This module supplies
the missing conversational layer: a ``messages``-channel state graph with one
node that hands the whole accumulated history to the bound workflow and
projects the workflow reply back as a single ``AIMessage``. Compiled with the
runtime's checkpointer it gives ``WorkflowAppRuntime`` the same stateless
replay (D2) and cross-cutting semantics every other engine enjoys, without the
chat API/service layer knowing which engine runs underneath.
"""

import json
from typing import Annotated, Any, TypedDict

from fastapi.concurrency import run_in_threadpool
from langchain_core.messages import AIMessage, BaseMessage
from langgraph.checkpoint.base import BaseCheckpointSaver
from langgraph.graph import END, START, StateGraph, add_messages
from langgraph.graph.state import CompiledStateGraph

from app.core.logging import logger
from app.services.agents.workflow_bridge import get_workflow_registry
from app.utils import extract_text_content


class ChatflowState(TypedDict):
    """Wrapper-graph state: a single additively reduced ``messages`` channel."""

    messages: Annotated[list[BaseMessage], add_messages]


def extract_reply(output: dict[str, Any]) -> str:
    """Project a workflow ``RunResult.output`` to the assistant reply text (D5).

    The trailing ``AIMessage`` of ``output["messages"]`` carries the reply when
    the workflow exposes a messages channel; anything else (no messages, a
    non-AI tail, an empty extraction) falls back to a deterministic JSON dump
    of the whole output so a structured-only workflow still answers with
    something readable. ``default=str`` keeps non-serialisable values (e.g.
    ``BaseMessage`` objects) from raising.

    Args:
        output: The workflow run's final state dict.

    Returns:
        The reply text, never empty for a non-empty output.
    """
    messages = output.get("messages")
    if isinstance(messages, list) and messages:
        last = messages[-1]
        if isinstance(last, AIMessage):
            text = extract_text_content(last.content)
            if text:
                return text
    return json.dumps(output, ensure_ascii=False, default=str)


def build_chatflow_graph(workflow_id: str, checkpointer: BaseCheckpointSaver | None) -> CompiledStateGraph:
    """Compile the single-node wrapper graph binding ``workflow_id`` (D1/D4).

    The node runs the synchronous ``execute_workflow`` in a threadpool so the
    event loop is never blocked (AD-10), re-feeds the full ``messages`` history
    each turn (stateless replay, D2 — ``synthesize_run_input`` passes a truthy
    ``messages`` straight through), and yields exactly one ``AIMessage`` per
    turn (MVP single chunk, D4: the inner workflow runs in its own callback
    context, so its tokens cannot stream through this wrapper). A failed
    ``RunResult`` raises so the runtime base class records the turn as failed.

    Args:
        workflow_id: Registered workflow the wrapper invokes as a black box.
        checkpointer: Thread checkpointer shared with the runtime (may be None).

    Returns:
        The compiled wrapper graph.
    """

    async def chatflow(state: ChatflowState) -> dict[str, Any]:
        registry = get_workflow_registry()
        result = await run_in_threadpool(registry.execute_workflow, workflow_id, {"messages": state["messages"]})
        if result.status == "failed":
            logger.warning(
                "chatflow_workflow_failed",
                workflow_id=workflow_id,
                run_id=result.run_id,
                error_message=result.error_message,
            )
            raise RuntimeError(f"workflow '{workflow_id}' execution failed: {result.error_message}")
        reply = extract_reply(result.output)
        logger.debug("chatflow_turn_completed", workflow_id=workflow_id, run_id=result.run_id)
        return {"messages": [AIMessage(content=reply)]}

    builder = StateGraph(ChatflowState)
    builder.add_node("chatflow", chatflow)
    builder.add_edge(START, "chatflow")
    builder.add_edge("chatflow", END)
    return builder.compile(checkpointer=checkpointer)
