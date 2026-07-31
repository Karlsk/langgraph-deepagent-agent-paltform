"""Unit tests for app.workflow.state (spec-02 TC2, CONTRACT §4.3, C2/S1/S2/S14, EXP-G8)."""

import operator
from pathlib import Path
from typing import (
    Annotated,
    Any,
    get_args,
    get_origin,
    get_type_hints,
)

import pytest

from app.workflow import state as state_module
from app.workflow.models import StateFieldSchema
from app.workflow.state import (
    StateModelFactory,
    _last,
)


def _hints(model: type) -> dict:
    """Return the model's type hints including Annotated metadata."""
    return get_type_hints(model, include_extras=True)


def _reducer_metadata(annotation: object) -> tuple:
    """Extract the Annotated metadata tuple (empty for plain annotations)."""
    if get_origin(annotation) is Annotated:
        return get_args(annotation)[1:]
    return ()


@pytest.mark.unit
def test_plain_field_last_value() -> None:
    """A field without reducer has no reducer metadata; assignment is last-write-wins (S1)."""
    model = StateModelFactory.create_state_model(
        {"mode": StateFieldSchema(type="str", default="")},
    )
    assert _reducer_metadata(_hints(model)["mode"]) == ()
    instance = model()
    instance.mode = "first"
    instance.mode = "second"
    assert instance.mode == "second"


@pytest.mark.unit
def test_reducer_add_annotation() -> None:
    """reducer="add" yields Annotated[list, operator.add] introspectable metadata (S1)."""
    model = StateModelFactory.create_state_model(
        {"items": StateFieldSchema(type="list", reducer="add")},
    )
    annotation = _hints(model)["items"]
    assert get_args(annotation)[0] is list
    assert operator.add in _reducer_metadata(annotation)
    assert model().items == []


@pytest.mark.unit
def test_reducer_last_annotation() -> None:
    """reducer="last" yields Annotated[T, _last] and _last returns the later value (S1)."""
    model = StateModelFactory.create_state_model(
        {"mode": StateFieldSchema(type="str", default="", reducer="last")},
    )
    annotation = _hints(model)["mode"]
    assert get_args(annotation)[0] is str
    assert _last in _reducer_metadata(annotation)
    assert _last("a", "b") == "b"


@pytest.mark.unit
def test_unknown_type_raises() -> None:
    """An unmapped type fails fast with the field name and all supported types (R9)."""
    with pytest.raises(ValueError, match="birthday") as exc_info:
        StateModelFactory.create_state_model(
            {"birthday": StateFieldSchema(type="date")},
        )
    message = str(exc_info.value)
    for supported in ("str", "int", "float", "bool", "list", "dict", "object", "any", "List[str]", "Dict[str, Any]"):
        assert supported in message


@pytest.mark.unit
def test_extra_fields_allowed() -> None:
    """Model-level lenient validation only (S14, spec-02 §7 revision note): extra attrs accepted."""
    model = StateModelFactory.create_state_model(
        {"input": StateFieldSchema(type="str", default="")},
    )
    instance = model(input="x", undeclared_key="kept")
    assert instance.undeclared_key == "kept"  # type: ignore[attr-defined]


@pytest.mark.unit
def test_history_auto_injected() -> None:
    """Undeclared history is auto-injected as an add channel with default [] (K2/S2)."""
    model = StateModelFactory.create_state_model(
        {"input": StateFieldSchema(type="str", default="")},
    )
    annotation = _hints(model)["history"]
    assert get_args(annotation)[0] is list
    assert operator.add in _reducer_metadata(annotation)
    assert model().history == []  # type: ignore[attr-defined]
    description = model.model_fields["history"].description
    assert description == "Auto-injected execution history (reducer=add)"


@pytest.mark.unit
def test_explicit_history_overrides() -> None:
    """An explicitly declared history (reducer="last") is NOT overwritten by auto-injection (S2)."""
    model = StateModelFactory.create_state_model(
        {"history": StateFieldSchema(type="list", default=None, reducer="last")},
    )
    metadata = _reducer_metadata(_hints(model)["history"])
    assert _last in metadata
    assert operator.add not in metadata


@pytest.mark.unit
@pytest.mark.parametrize(
    "field_name",
    [
        "circle_conclusions",
        "planner_result",
        "worker_result",
        "reflector_result",
        "current_node",
        "some_plain_name",
    ],
)
def test_no_hardcoded_field_names(field_name: str) -> None:
    """Guard (R2/C2): legacy domain field names behave exactly like any plain field."""
    model = StateModelFactory.create_state_model(
        {field_name: StateFieldSchema(type="str", default="")},
    )
    assert _reducer_metadata(_hints(model)[field_name]) == ()
    assert model.model_fields[field_name].default == ""
    source = Path(state_module.__file__).read_text(encoding="utf-8")
    for residue in ("circle_", "planner_", "worker_", "reflector_", "current_node"):
        assert residue not in source, f"domain residue found in state.py: {residue}"


@pytest.mark.unit
def test_node_names_predeclare_result_fields() -> None:
    """node_names pre-declares {name}_result as (Any, None) LastValue fields (EXP-G8 option 1)."""
    model = StateModelFactory.create_state_model(
        {"input": StateFieldSchema(type="str", default="")},
        node_names=["fetch", "summarize"],
    )
    hints = _hints(model)
    for key in ("fetch_result", "summarize_result"):
        assert key in model.model_fields
        assert _reducer_metadata(hints[key]) == ()
        assert hints[key] is Any
        assert model.model_fields[key].default is None


@pytest.mark.unit
def test_node_names_none_adds_no_result_fields() -> None:
    """With node_names=None no *_result field is synthesized."""
    model = StateModelFactory.create_state_model(
        {"input": StateFieldSchema(type="str", default="")},
    )
    result_fields = [name for name in model.model_fields if name.endswith("_result")]
    assert result_fields == []


@pytest.mark.unit
def test_node_names_collision_keeps_explicit_declaration() -> None:
    """An explicit {name}_result declaration wins over the pre-declared slot (no overwrite)."""
    model = StateModelFactory.create_state_model(
        {"fetch_result": StateFieldSchema(type="list", default=None, reducer="add")},
        node_names=["fetch"],
    )
    annotation = _hints(model)["fetch_result"]
    assert operator.add in _reducer_metadata(annotation)
    factory = model.model_fields["fetch_result"].default_factory
    assert factory is list
