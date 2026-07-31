"""Workflow DSL models and exception hierarchy.

Pure data-contract layer: Pydantic v2 models for parsing YAML workflow
definitions. No business logic. Single point of definition for the engine
exception family.

Dependency red-line: this module imports ONLY pydantic, stdlib, and yaml.
"""

from __future__ import annotations

from datetime import datetime
from enum import Enum
from pathlib import Path
from typing import Any, Literal

import yaml
from pydantic import BaseModel, ConfigDict, Field, model_validator


class WorkflowEngineError(Exception):
    """Common base class for every engine exception."""


class ConfigError(WorkflowEngineError):
    """Configuration error, e.g. a required env secret is missing."""


class WorkflowNotFoundError(WorkflowEngineError):
    """Unknown workflow_id."""


class ConditionNotMatchedError(WorkflowEngineError):
    """No conditional edge matched during routing."""


class LLMNodeError(WorkflowEngineError):
    """LLM invocation failure, including retry exhaustion."""


class HTTPNodeError(WorkflowEngineError):
    """HTTP invocation failure, including retry exhaustion and explicit mock miss."""


class NodeType(str, Enum):
    """Built-in node types for this phase (trimmed set, C8).

    Plugin types are arbitrary strings and are not constrained by this enum.
    """

    LLM = "llm"
    HTTP = "http"


class StateFieldSchema(BaseModel):
    """Schema of a single state field declared in YAML ``state_schema``."""

    type: str
    default: Any = None
    description: str = ""
    reducer: Literal["add", "last"] | None = None


class NodeDefinition(BaseModel):
    """Definition of a single workflow node.

    ``type`` deliberately stays a plain str (not NodeType) so registry plugin types pass through (R4).
    """

    name: str = Field(..., min_length=1)
    type: str
    config: dict[str, Any] = Field(default_factory=dict)


class EdgeDefinition(BaseModel):
    """Directed edge between nodes; ``target`` is a node name or the literal "END"."""

    source: str
    target: str
    condition: str | None = None


class ExecutionLog(BaseModel):
    """Log entry describing one node execution."""

    node_name: str
    node_type: str
    timestamp: datetime = Field(default_factory=datetime.now)
    input_data: dict
    output_data: dict
    execution_time_ms: float
    error: str | None = None


class OperatorLog(BaseModel):
    """Declared input/output schemas of a node, for observability."""

    node_name: str
    input_schema: dict[str, StateFieldSchema]
    output_schema: dict[str, StateFieldSchema]


class WorkflowDefinition(BaseModel):
    """Top-level workflow DSL model parsed from YAML.

    ``extra="ignore"`` tolerates annotation-style extra keys such as description (K1).
    Model-level validation only covers non-empty nodes and unique node names;
    graph-level validation belongs to GraphBuilder (C5).
    """

    model_config = ConfigDict(extra="ignore")

    workflow_id: str
    entry_point: str
    nodes: list[NodeDefinition]
    edges: list[EdgeDefinition] = Field(default_factory=list)
    state_schema: dict[str, StateFieldSchema]
    operator_logs: dict[str, OperatorLog] = Field(default_factory=dict)
    execution_history: list[ExecutionLog] = Field(default_factory=list)

    @model_validator(mode="after")
    def _validate_nodes(self) -> WorkflowDefinition:
        """Require at least one node and unique node names."""
        if not self.nodes:
            raise ValueError("nodes must contain at least one node")
        names = [node.name for node in self.nodes]
        if len(set(names)) != len(names):
            raise ValueError(f"duplicate node names found: {names}")
        return self


def parse_definition(data: dict[str, Any]) -> WorkflowDefinition:
    """Parse a workflow definition from a dict (yaml.safe_load result).

    Args:
        data: Mapping returned by yaml.safe_load.

    Returns:
        The validated WorkflowDefinition instance.

    Raises:
        ValidationError: If the data violates model constraints.
    """
    return WorkflowDefinition.model_validate(data)


def load_definition_from_yaml(path: str | Path) -> WorkflowDefinition:
    """Load a workflow definition from a YAML file.

    Only yaml.safe_load is used (D6/S16). A missing file or a YAML parse
    failure raises ValueError carrying the path context; model validation
    failures raise pydantic ValidationError.

    Args:
        path: Path to the YAML file.

    Returns:
        The validated WorkflowDefinition instance.

    Raises:
        ValueError: If the file does not exist or the YAML syntax is invalid.
        ValidationError: If the data violates model constraints.
    """
    file_path = Path(path)
    if not file_path.exists():
        msg = f"Workflow definition file not found: {file_path}"
        raise ValueError(msg)
    try:
        with file_path.open(encoding="utf-8") as f:
            data = yaml.safe_load(f)
    except yaml.YAMLError as exc:
        msg = f"Failed to parse YAML file {file_path}: {exc}"
        raise ValueError(msg) from exc
    if not isinstance(data, dict):
        msg = f"Expected mapping in YAML file {file_path}, got {type(data).__name__}"
        raise ValueError(msg)
    return parse_definition(data)
