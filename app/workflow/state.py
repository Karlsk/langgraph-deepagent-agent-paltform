"""Dynamic state model factory for the workflow engine (spec-02, K2/K3).

Implements the frozen contract in CONTRACT §4.3 (TYPE_MAP / _last /
StateModelFactory.create_state_model) and the behavior semantics S1/S2/S14.
Contract cleanup C2: reducers are driven ONLY by explicit YAML declarations;
there is no field-name special-casing of any kind. Per the EXP-G8 revision
note (2026-07-30, option 1), ``node_names`` pre-declares ``{name}_result``
LastValue channels at build time; any other undeclared key is not writable
into the runtime state, and ``extra="allow"`` remains model-level lenient
validation only.

Dependency red-line: this module imports ONLY pydantic, stdlib, and
app.workflow.models.
"""

from __future__ import annotations

import operator
from typing import (
    Annotated,
    Any,
)

from pydantic import (
    BaseModel,
    ConfigDict,
    Field,
    create_model,
)

from app.workflow.models import StateFieldSchema

TYPE_MAP: dict[str, Any] = {
    "str": str,
    "int": int,
    "float": float,
    "bool": bool,
    "list": list,
    "dict": Any,
    "object": Any,
    "any": Any,
    "List[str]": list,
    "Dict[str, Any]": Any,
}


def _last(a: Any, b: Any) -> Any:
    """reducer='last' merge function: the later write wins."""
    return b


class _DynamicStateBase(BaseModel):
    """Base for generated state models: lenient model validation only, not a runtime write surface (S14/EXP-G8)."""

    model_config = ConfigDict(extra="allow")


# AD-11: pydantic's Field() overloads advertise the default's type instead of
# FieldInfo, so the (annotation, FieldInfo) pairs are typed as tuple[Any, Any].
_FieldDef = tuple[Any, Any]


def _build_field(name: str, field: StateFieldSchema) -> _FieldDef:
    """Build one (annotation, FieldInfo) pair per the frozen reducer rules (C2).

    Raises:
        ValueError: If ``field.type`` is not in TYPE_MAP (fail fast, R9).
    """
    if field.type not in TYPE_MAP:
        supported = ", ".join(sorted(TYPE_MAP))
        msg = f"Unknown type {field.type!r} for state field {name!r}; supported types: {supported}"
        raise ValueError(msg)
    py_type = TYPE_MAP[field.type]
    if field.reducer == "add":
        return (Annotated[list, operator.add], Field(default_factory=list, description=field.description))
    if field.reducer == "last":
        return (Annotated[py_type, _last], Field(default=field.default, description=field.description))
    return (py_type, Field(default=field.default, description=field.description))


class StateModelFactory:
    """Factory that synthesizes the dynamic pydantic state model via create_model (K2/K3)."""

    @staticmethod
    def create_state_model(
        state_schema: dict[str, StateFieldSchema],
        node_names: list[str] | None = None,
    ) -> type[BaseModel]:
        """Synthesize the dynamic state model from the YAML state_schema (CONTRACT §4.3).

        reducer='add' → Annotated[list, operator.add]；'last' → Annotated[T, _last]；
        未声明 → 普通字段（LastValue 后写覆盖）；未声明 history 自动注入（add channel）；
        基类 ConfigDict(extra='allow')；未知 type → ValueError 并列出支持类型。

        Per the EXP-G8 revision note, ``node_names`` pre-declares one
        ``{name}_result: (Any, None)`` LastValue channel per node at build
        time; explicitly declared keys always take precedence.

        Args:
            state_schema: Field name → StateFieldSchema mapping from YAML.
            node_names: Optional node names whose ``{name}_result`` slots are
                pre-declared (EXP-G8 option 1).

        Returns:
            A DynamicWorkflowState subclass of _DynamicStateBase.

        Raises:
            ValueError: If any field declares an unsupported type.
        """
        field_definitions: dict[str, _FieldDef] = {
            name: _build_field(name, field) for name, field in state_schema.items()
        }
        if "history" not in state_schema:
            field_definitions["history"] = (
                Annotated[list, operator.add],
                Field(default_factory=list, description="Auto-injected execution history (reducer=add)"),
            )
        for node_name in node_names or []:
            result_key = f"{node_name}_result"
            if result_key not in field_definitions:
                field_definitions[result_key] = (
                    Any,
                    Field(default=None, description=f"Pre-declared result slot for node {node_name!r} (EXP-G8)"),
                )
        # AD-11: create_model's typed overloads cannot express dynamic **kwargs;
        # minimal, localized pyright relaxation on this single call.
        return create_model(  # pyright: ignore[reportCallIssue]
            "DynamicWorkflowState",
            **field_definitions,  # pyright: ignore[reportArgumentType]
            __base__=_DynamicStateBase,
        )
