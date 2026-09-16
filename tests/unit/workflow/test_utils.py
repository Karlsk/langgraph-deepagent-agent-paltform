"""Unit tests for app.workflow.utils (spec-03, CONTRACT §4.6, S3/S4/S5/C4/C7)."""

from typing import Any

import pytest
from pydantic import BaseModel

from app.workflow.utils import convert_state_to_dict, map_output_to_state, resolve_dot_path


class _State(BaseModel):
    """Tiny pydantic state used for conversion tests."""

    mode: str = "x"
    count: int = 0


@pytest.mark.unit
def test_convert_dict_passthrough() -> None:
    """A dict state passes through unchanged (same object, no copy)."""
    state = {"a": 1}
    assert convert_state_to_dict(state) is state


@pytest.mark.unit
def test_convert_pydantic_model_dump() -> None:
    """A pydantic model converts via model_dump()."""
    assert convert_state_to_dict(_State(mode="y", count=2)) == {"mode": "y", "count": 2}


@pytest.mark.unit
@pytest.mark.parametrize("other", [None, 42, "text", [1, 2], object()])
def test_convert_other_types_empty_dict(other: Any) -> None:
    """Any non-dict, non-pydantic input converts to {} (S5)."""
    assert convert_state_to_dict(other) == {}


@pytest.mark.unit
def test_map_output_dual_write_and_history() -> None:
    """Explicit dual-write: {name}_result package + flattened keys + history increment (S3/S4/C4)."""
    output = {"answer": "ok"}
    state: dict[str, Any] = {"history": ["earlier"]}
    result = map_output_to_state("n1", output, state, dual_write=True)
    assert result["n1_result"] == output
    assert result["answer"] == "ok"
    assert result["history"] == [f"n1: {output!s:.100}..."]


@pytest.mark.unit
def test_map_output_no_dual_write() -> None:
    """dual_write=False only writes the {name}_result package, no flattening (S4)."""
    result = map_output_to_state("n1", {"answer": "ok"}, {}, dual_write=False)
    assert result["n1_result"] == {"answer": "ok"}
    assert "answer" not in result


@pytest.mark.unit
def test_map_output_history_skips() -> None:
    """No history increment when state lacks a list history, when disabled, or when output owns history (C4)."""
    # state without history list
    assert "history" not in map_output_to_state("n1", {"a": 1}, {})
    # history_increment disabled
    assert "history" not in map_output_to_state("n1", {"a": 1}, {"history": []}, history_increment=False)
    # node output already carries history (dual_write needed to surface it at top level)
    result = map_output_to_state("n1", {"history": ["own"]}, {"history": []}, dual_write=True)
    assert result["history"] == ["own"]


@pytest.mark.unit
def test_map_output_state_keys() -> None:
    """state_keys writes listed keys from node_output to top-level even without dual_write (S4)."""
    output = {"flag": True, "data": "payload"}
    result = map_output_to_state("n1", output, {}, state_keys=["flag"])
    assert result["n1_result"] == output
    assert result["flag"] is True
    assert "data" not in result  # not in state_keys, not dual_write


@pytest.mark.unit
def test_map_output_does_not_mutate_inputs() -> None:
    """Neither state nor node_output is mutated; state=None defaults to {} (S5)."""
    output = {"answer": "ok"}
    state: dict[str, Any] = {"history": []}
    map_output_to_state("n1", output, state)
    assert output == {"answer": "ok"}
    assert state == {"history": []}
    result = map_output_to_state("n1", output)
    assert result["n1_result"] == output
    assert "history" not in result


@pytest.mark.unit
def test_resolve_dot_path_single_key() -> None:
    """A single key resolves like dict.get()."""
    assert resolve_dot_path({"a": 1}, "a") == 1


@pytest.mark.unit
def test_resolve_dot_path_nested() -> None:
    """Dot-separated keys walk into nested dicts."""
    data = {"x": {"y": {"z": 42}}}
    assert resolve_dot_path(data, "x.y.z") == 42


@pytest.mark.unit
def test_resolve_dot_path_missing_returns_none() -> None:
    """A missing key returns None rather than raising."""
    assert resolve_dot_path({"a": 1}, "b") is None


@pytest.mark.unit
def test_resolve_dot_path_mid_chain_non_dict_returns_none() -> None:
    """If an intermediate value is not a dict, the walk returns None."""
    assert resolve_dot_path({"a": {"b": 42}}, "a.b.c") is None


@pytest.mark.unit
def test_resolve_dot_path_empty_string_key() -> None:
    """An empty-string path resolves the dict itself (single split)."""
    data = {"": "empty"}
    assert resolve_dot_path(data, "") == "empty"
