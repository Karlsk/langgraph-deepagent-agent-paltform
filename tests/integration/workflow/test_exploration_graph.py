"""EXP-G1..G8 characterization tests: langgraph 1.0.2 graph-building API (offline).

Freezes the measured behaviors recorded in api-exploration-1x.md group G as a
long-term regression suite (spec-00 TC4, R-EXP). Fully offline: no network calls;
only the locked .venv versions' graph build/execution semantics are exercised.

Note: test_g8_* freezes the ACTUAL 1.x behavior (extra keys silently dropped),
which deviates from the planning assumption (K2 Dify-style extra-key retention).
Resolved per CONTRACT §11 on 2026-07-30: option 1 adopted — `{node_name}_result`
keys are pre-declared at build time; other extra keys are unsupported.
"""

import operator
import warnings
from typing import Annotated, Any

import pytest
from langchain_core.runnables import RunnableLambda
from langgraph.graph import END, START, StateGraph
from langgraph.pregel import Pregel
from pydantic import BaseModel, ConfigDict, create_model

pytestmark = pytest.mark.integration


class _ConditionNotMatchedError(Exception):
    """Stand-in for the engine's custom exception (real one lands in spec-01 models.py)."""


def _last(a: Any, b: Any) -> Any:
    """Reducer for 'last' semantics: the later write wins."""
    return b


class _DynamicStateBase(BaseModel):
    """Base class for the dynamic state model (extra='allow', per spec-02 planning)."""

    model_config = ConfigDict(extra="allow")


def _make_state_model() -> type[BaseModel]:
    """Build a dynamic model shaped like the planned StateModelFactory output."""
    return create_model(
        "DynamicWorkflowState",
        input=(str, ""),
        history=(Annotated[list, operator.add], []),
        mode=(Annotated[str, _last], ""),
        plain=(str, ""),
        __base__=_DynamicStateBase,
    )


def test_g1_dynamic_pydantic_model_as_state_schema() -> None:
    """G1: create_model dynamic model works as state schema; channels inferred and compiled."""
    model = _make_state_model()
    graph = StateGraph(model)
    channel_types = {k: type(v).__name__ for k, v in graph.channels.items()}
    assert channel_types == {
        "input": "LastValue",
        "history": "BinaryOperatorAggregate",
        "mode": "BinaryOperatorAggregate",
        "plain": "LastValue",
    }
    graph.add_node("noop", RunnableLambda(lambda s: {}))
    graph.add_edge(START, "noop")
    graph.add_edge("noop", END)
    compiled = graph.compile()
    assert type(compiled).__name__ == "CompiledStateGraph"
    assert isinstance(compiled, Pregel)


def test_g2_g3_reducer_merge_partial_update_and_input_form() -> None:
    """G2+G3: add-reducer merges lists; _last/plain overwrite; partial update keeps values; node arg is a model."""
    model = _make_state_model()
    seen_types: list[type] = []

    def node_a(state: Any) -> dict[str, Any]:
        seen_types.append(type(state))
        return {"history": ["a"], "mode": "A", "plain": "pa"}

    def node_b(state: Any) -> dict[str, Any]:
        seen_types.append(type(state))
        return {"history": ["b"], "mode": "B", "plain": "pb"}  # partial update: no "input" key

    graph = StateGraph(model)
    graph.add_node("a", RunnableLambda(node_a))
    graph.add_node("b", RunnableLambda(node_b))
    graph.add_edge(START, "a")
    graph.add_edge("a", "b")
    graph.add_edge("b", END)
    out = graph.compile().invoke({"input": "hello"})

    # G3: nodes receive pydantic model instances (handled by convert_state_to_dict's model_dump branch)
    assert all(issubclass(t, BaseModel) for t in seen_types)
    # G2: add channel merges; _last and un-annotated fields are last-write-wins
    assert out["history"] == ["a", "b"]
    assert out["mode"] == "B"
    assert out["plain"] == "pb"
    # G3: returned dict is a partial update; keys not returned keep their values
    assert out["input"] == "hello"


def test_g4_conditional_edges_path_map_to_end() -> None:
    """G4: three-arg add_conditional_edges works; path_map can map a label to the END constant."""
    model = _make_state_model()
    graph = StateGraph(model)
    graph.add_node("router_src", RunnableLambda(lambda s: {"mode": "go_end"}))
    graph.add_node("other", RunnableLambda(lambda s: {"plain": "other"}))
    graph.add_edge(START, "router_src")

    def path_func(state: Any) -> str:
        return "finish" if state.mode == "go_end" else "cont"

    graph.add_conditional_edges("router_src", path_func, {"finish": END, "cont": "other"})
    out = graph.compile().invoke({"input": "x"})
    # "finish" -> END taken; the "other" node never runs
    assert out.get("plain", "") != "other"
    assert out["mode"] == "go_end"


def test_g5_start_end_constants_and_entry_point() -> None:
    """G5: START/END are importable sentinel strings; set_entry_point == add_edge(START, x)."""
    assert START == "__start__"
    assert END == "__end__"
    model = _make_state_model()
    graph = StateGraph(model)
    graph.add_node("n", RunnableLambda(lambda s: {}))
    graph.set_entry_point("n")
    assert (START, "n") in graph.edges


def test_g6_invoke_output_dict_and_config_propagation() -> None:
    """G6: invoke returns a plain dict (declared fields only); config tags/metadata reach 2-arg node funcs."""
    model = _make_state_model()
    captured: dict[str, Any] = {}

    def node_cfg(state: Any, config: dict[str, Any]) -> dict[str, Any]:
        captured.update(config)
        return {"plain": "cfg"}

    graph = StateGraph(model)
    graph.add_node("n", RunnableLambda(node_cfg))
    graph.add_edge(START, "n")
    graph.add_edge("n", END)
    out = graph.compile().invoke({"input": "x"}, config={"tags": ["mytag"], "metadata": {"k": "v"}})
    assert isinstance(out, dict) and not isinstance(out, BaseModel)
    assert set(out) == {"input", "history", "mode", "plain"}
    assert "mytag" in captured.get("tags", [])
    assert captured.get("metadata", {}).get("k") == "v"


def test_g6_invoke_input_not_pydantic_validated() -> None:
    """G6 extra: input dict is not pydantic-validated; bad types blow up at the reducer (TypeError)."""
    model = _make_state_model()
    graph = StateGraph(model)
    graph.add_node("n", RunnableLambda(lambda s: {"history": ["x"]}))
    graph.add_edge(START, "n")
    graph.add_edge("n", END)
    with pytest.raises(TypeError, match="concatenate"):
        graph.compile().invoke({"history": "not-a-list"})


def test_g7_node_exception_propagates_unwrapped() -> None:
    """G7: custom exception from a node func propagates unwrapped through invoke."""
    model = _make_state_model()

    def bad_node(state: Any) -> dict[str, Any]:
        raise _ConditionNotMatchedError("no branch matched for source=x")

    graph = StateGraph(model)
    graph.add_node("bad", RunnableLambda(bad_node))
    graph.add_edge(START, "bad")
    graph.add_edge("bad", END)
    with pytest.raises(_ConditionNotMatchedError, match="no branch matched for source=x"):
        graph.compile().invoke({"input": "x"})


def test_g7_path_func_exception_propagates_unwrapped() -> None:
    """G7 extra: an exception raised inside a conditional path_func also propagates unwrapped."""
    model = _make_state_model()

    def raising_path(state: Any) -> str:
        raise _ConditionNotMatchedError("router no match")

    graph = StateGraph(model)
    graph.add_node("s", RunnableLambda(lambda s: {}))
    graph.add_edge(START, "s")
    graph.add_conditional_edges("s", raising_path, {"x": END})
    with pytest.raises(_ConditionNotMatchedError, match="router no match"):
        graph.compile().invoke({"input": "x"})


def test_g8_extra_keys_silently_dropped() -> None:
    """G8 (deviation freeze): with extra='allow', undeclared node output keys are silently dropped.

    The channel set is frozen from type annotations at StateGraph construction time
    (state.py `_get_channels`); node updates are filtered by `k in output_keys` in
    `_get_updates`, so undeclared keys have no channel to write to. This contradicts
    the planning assumption (K2 extra-key retention). Resolved per CONTRACT §11
    (2026-07-30, option 1: pre-declared dual-write keys). If a future version
    changes this behavior, this test will flag it.
    """
    model = _make_state_model()

    def write_extra(state: Any) -> dict[str, Any]:
        return {"foo": "bar", "plain": "written"}

    graph = StateGraph(model)
    graph.add_node("w", RunnableLambda(write_extra))
    graph.add_edge(START, "w")
    graph.add_edge("w", END)
    with warnings.catch_warnings(record=True) as caught:
        warnings.simplefilter("always")
        out = graph.compile().invoke({"input": "x"})
    assert "foo" not in out  # silently dropped
    assert out["plain"] == "written"  # declared keys still written
    assert caught == []  # no warnings emitted


def test_g8_input_extra_keys_also_dropped() -> None:
    """G8 extra: undeclared keys in the invoke input dict are silently dropped as well."""
    model = _make_state_model()
    graph = StateGraph(model)
    graph.add_node("n", RunnableLambda(lambda s: {}))
    graph.add_edge(START, "n")
    graph.add_edge("n", END)
    out = graph.compile().invoke({"input": "x", "unknown_in": 1})
    assert "unknown_in" not in out
