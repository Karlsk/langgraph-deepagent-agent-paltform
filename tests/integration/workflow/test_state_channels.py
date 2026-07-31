"""Integration tests: generated state models drive real langgraph channels (spec-02 TC3).

Feeds StateModelFactory output into langgraph 1.0.2 StateGraph and verifies
add-channel merging plus the EXP-G8 revised semantics: pre-declared
``{node_name}_result`` keys are retained in the final state while undeclared
keys are silently dropped (mutually corroborating test_exploration_graph.py
test_g2_*/test_g8_*). Fully offline; public API only.
"""

from typing import Any

import pytest
from langchain_core.runnables import RunnableLambda
from langgraph.graph import (
    END,
    START,
    StateGraph,
)

from app.workflow.models import StateFieldSchema
from app.workflow.state import StateModelFactory

pytestmark = pytest.mark.integration


def test_add_channel_merges() -> None:
    """Two nodes each returning {"history": [x]} merge into ["a", "b"] (EXP-G2)."""
    model = StateModelFactory.create_state_model(
        {"input": StateFieldSchema(type="str", default="")},
    )

    def node_a(state: Any) -> dict[str, Any]:
        # EXP-G3: node funcs receive pydantic model instances, not dicts
        assert state.input == "hello"
        return {"history": ["a"]}

    def node_b(state: Any) -> dict[str, Any]:
        return {"history": ["b"]}

    graph = StateGraph(model)
    graph.add_node("a", RunnableLambda(node_a))
    graph.add_node("b", RunnableLambda(node_b))
    graph.add_edge(START, "a")
    graph.add_edge("a", "b")
    graph.add_edge("b", END)
    out = graph.compile().invoke({"input": "hello"})

    assert out["history"] == ["a", "b"]


def test_predeclared_node_result_retained() -> None:
    """Pre-declared {node_name}_result survives invoke; undeclared keys are dropped (EXP-G8)."""
    model = StateModelFactory.create_state_model(
        {"input": StateFieldSchema(type="str", default="")},
        node_names=["fetch"],
    )

    def fetch(state: Any) -> dict[str, Any]:
        return {"fetch_result": {"status": "ok"}, "foo": "bar"}

    graph = StateGraph(model)
    graph.add_node("fetch", RunnableLambda(fetch))
    graph.add_edge(START, "fetch")
    graph.add_edge("fetch", END)
    out = graph.compile().invoke({"input": "x"})

    # Pre-declared LastValue channel retains the node's write
    assert out["fetch_result"] == {"status": "ok"}
    # Undeclared key is silently dropped (mirrors test_g8_extra_keys_silently_dropped)
    assert "foo" not in out
