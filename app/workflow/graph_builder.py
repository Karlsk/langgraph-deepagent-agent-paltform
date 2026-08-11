"""Graph builder: WorkflowDefinition -> compiled langgraph StateGraph (spec-06).

CONTRACT §4.9: GraphBuilder follows the K6 seven-step build order and owns no
registry reference (H5). Router debug output is structlog-only, recording the
matched condition and target, never the full state (C3/H6).

Dependency red-line: langgraph public API + app.workflow internals only;
never import app.core.* or registry internals.
"""

from __future__ import annotations

from typing import Literal

import structlog

from app.workflow.models import WorkflowDefinition

logger = structlog.get_logger(__name__)


class GraphBuilder:
    """Compiles a WorkflowDefinition into an executable langgraph graph (K6)."""

    def __init__(self, *, no_match_policy: Literal["raise", "default"] = "raise") -> None:
        """Store the no-match routing policy; deliberately no registry parameter (H5)."""
        self.no_match_policy = no_match_policy

    def _validate_definition(self, definition: WorkflowDefinition) -> None:
        """Graph-level validation (C5): every error names workflow_id and the offender.

        No literal exemptions for legacy node names (C5 guard): all nodes are
        validated uniformly.
        """
        workflow_id = definition.workflow_id
        if not workflow_id:
            msg = "workflow_id must be non-empty"
            raise ValueError(msg)
        if not definition.nodes:
            msg = f"workflow '{workflow_id}': nodes must be non-empty"
            raise ValueError(msg)
        node_names = {node.name for node in definition.nodes}
        if definition.entry_point not in node_names:
            msg = f"workflow '{workflow_id}': entry_point '{definition.entry_point}' not in nodes {sorted(node_names)}"
            raise ValueError(msg)
        for edge in definition.edges:
            if edge.source not in node_names:
                msg = f"workflow '{workflow_id}': edge source '{edge.source}' not in nodes {sorted(node_names)}"
                raise ValueError(msg)
            if edge.target not in node_names and edge.target != "END":
                msg = (
                    f"workflow '{workflow_id}': edge target '{edge.target}' "
                    f"not in nodes {sorted(node_names)} or literal 'END'"
                )
                raise ValueError(msg)
