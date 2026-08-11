"""Graph builder: WorkflowDefinition -> compiled langgraph StateGraph (spec-06).

CONTRACT §4.9: GraphBuilder follows the K6 seven-step build order and owns no
registry reference (H5). Router debug output is structlog-only, recording the
matched condition and target, never the full state (C3/H6).

Dependency red-line: langgraph public API + app.workflow internals only;
never import app.core.* or registry internals.
"""

from __future__ import annotations

from collections.abc import Callable
from typing import Any, Literal, NamedTuple

import structlog
from langgraph.graph import END, StateGraph

from app.workflow.models import EdgeDefinition, WorkflowDefinition
from app.workflow.nodes.base import BaseNode
from app.workflow.nodes.factory import create_node
from app.workflow.state import StateModelFactory

logger = structlog.get_logger(__name__)


class BuildResult(NamedTuple):
    """Build output: compiled graph plus node-name -> instance map (CONTRACT §4.9)."""

    compiled_graph: Any  # langgraph CompiledStateGraph
    nodes_map: dict[str, BaseNode]  # collected by the registry for logs


class GraphBuilder:
    """Compiles a WorkflowDefinition into an executable langgraph graph (K6)."""

    def __init__(self, *, no_match_policy: Literal["raise", "default"] = "raise") -> None:
        """Store the no-match routing policy; deliberately no registry parameter (H5)."""
        self.no_match_policy = no_match_policy

    def build_graph(
        self,
        definition: WorkflowDefinition,
        *,
        default_edges: dict[str, str] | None = None,
    ) -> BuildResult:
        """Seven-step build (K6): validate -> state model -> StateGraph -> nodes -> edges -> entry -> compile."""
        # 1. 干净校验（C5）
        self._validate_definition(definition)
        # 2. 动态 state 模型（预声明 {name}_result 槽位，EXP-G8）
        node_names = [node.name for node in definition.nodes]
        state_model = StateModelFactory.create_state_model(definition.state_schema, node_names)
        # 3. StateGraph
        graph = StateGraph(state_model)
        # 4. 节点
        nodes_map = self._add_nodes(graph, definition)
        # 5. 边
        self._add_edges(graph, definition, nodes_map, default_edges)
        # 6. 入口（EXP-G5：set_entry_point ≡ add_edge(START, name)）
        graph.set_entry_point(definition.entry_point)
        # 7. 编译
        return BuildResult(compiled_graph=graph.compile(), nodes_map=nodes_map)

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

    def _add_nodes(self, graph: StateGraph, definition: WorkflowDefinition) -> dict[str, BaseNode]:
        """Create each node via the factory, attach its runnable, collect instances."""
        nodes_map: dict[str, BaseNode] = {}
        for node_def in definition.nodes:
            try:
                node = create_node(node_def, operator_log=definition.operator_logs.get(node_def.name))
                graph.add_node(node_def.name, node.build_runnable())
            except Exception:
                logger.error("node_build_failed", workflow_id=definition.workflow_id, node_name=node_def.name)
                raise
            nodes_map[node_def.name] = node
        return nodes_map

    def _add_edges(
        self,
        graph: StateGraph,
        definition: WorkflowDefinition,
        nodes_map: dict[str, BaseNode],
        default_edges: dict[str, str] | None,
    ) -> None:
        """Group edges by source; mixing normal and conditional edges per source is rejected (C3)."""
        normal: dict[str, list[EdgeDefinition]] = {}
        conditional: dict[str, list[EdgeDefinition]] = {}
        for edge in definition.edges:
            if edge.source not in nodes_map:
                msg = f"workflow '{definition.workflow_id}': edge source '{edge.source}' missing from built nodes"
                raise ValueError(msg)
            bucket = conditional if edge.condition else normal
            bucket.setdefault(edge.source, []).append(edge)
        for source in normal.keys() & conditional.keys():
            msg = f"workflow '{definition.workflow_id}': source '{source}' mixes normal and conditional edges"
            raise ValueError(msg)
        for source, edges in normal.items():
            for edge in edges:
                graph.add_edge(source, END if edge.target == "END" else edge.target)
        for source, edges in conditional.items():
            default_target = self._resolve_default_target(definition, source, nodes_map, default_edges)
            router = self._build_condition_router(source, list(edges), default_target)
            # EXP-G4/G5：path_map 把字面量 "END" 映射为 END 对象
            path_map: dict[Any, str] = {edge.target: (END if edge.target == "END" else edge.target) for edge in edges}
            graph.add_conditional_edges(source, router, path_map)

    def _resolve_default_target(
        self,
        definition: WorkflowDefinition,
        source: str,
        nodes_map: dict[str, BaseNode],
        default_edges: dict[str, str] | None,
    ) -> str | None:
        """S6: policy='default' requires a valid default_edges[source] at build time."""
        if self.no_match_policy != "default":
            return None
        target = (default_edges or {}).get(source)
        if target is None:
            msg = (
                f"workflow '{definition.workflow_id}': no_match_policy='default' "
                f"but default_edges has no entry for source '{source}'"
            )
            raise ValueError(msg)
        if target not in nodes_map and target != "END":
            msg = f"workflow '{definition.workflow_id}': default target '{target}' for source '{source}' is invalid"
            raise ValueError(msg)
        return target

    def _build_condition_router(
        self,
        source: str,
        conditional_edges: list[EdgeDefinition],
        default_target: str | None,
    ) -> Callable[[Any], str]:
        """C3/H6 router closure (implemented in spec-06 TC3)."""
        raise NotImplementedError("condition router lands in spec-06 TC3")
