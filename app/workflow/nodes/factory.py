"""Node factory and plugin registry (spec-03, CONTRACT §4.5).

Resolution order: plugin registry first, then exactly two built-in fallback
branches (R4, no elif), then ValueError for unknown types. Built-in classes
use scheme D placeholders until spec-04/05 wire the real imports (AD-04).

Dependency red-line 2: never import registry / graph_builder here.
"""

from __future__ import annotations

from typing import TYPE_CHECKING

from app.workflow.models import NodeDefinition, OperatorLog
from app.workflow.nodes.base import BaseNode

if TYPE_CHECKING:
    from app.workflow.nodes.http_node import HTTPNode as _HTTPNode  # noqa: F401 # pyright: ignore[reportMissingImports] — delivered by spec-05
    from app.workflow.nodes.llm_node import LLMNode as _LLMNode  # noqa: F401 # pyright: ignore[reportMissingImports] — delivered by spec-04

# 方案 D 占位：spec-04/05 交付时替换为真实顶层 import（AD-04 接线点）
LLMNode: type[BaseNode] | None = None
HTTPNode: type[BaseNode] | None = None

_NODE_REGISTRY: dict[str, type[BaseNode]] = {}


def register_node_type(type_name: str, node_class: type[BaseNode]) -> None:
    """注册前校验 BaseNode 子类，否则 TypeError（CONTRACT §4.5）."""
    if not (isinstance(node_class, type) and issubclass(node_class, BaseNode)):
        msg = f"node_class must be a BaseNode subclass, got {node_class!r}"
        raise TypeError(msg)
    _NODE_REGISTRY[type_name] = node_class


def list_node_types() -> list[str]:
    """返回已注册的节点类型名列表."""
    return list(_NODE_REGISTRY)


def create_node(definition: NodeDefinition, operator_log: OperatorLog | None = None) -> BaseNode:
    """插件注册表优先 → 内置兜底 2 分支 → 未知 ValueError（CONTRACT §4.5, R4）."""
    op_log = operator_log or OperatorLog(node_name=definition.name, input_schema={}, output_schema={})
    # 1. 插件注册表优先
    if definition.type in _NODE_REGISTRY:
        node_class = _NODE_REGISTRY[definition.type]
        return node_class(
            name=definition.name,
            node_type=definition.type,
            config=definition.config,
            operator_log=op_log,
        )
    # 2. 内置兜底恰好 2 个分支（R4：禁 elif）
    if definition.type in ("llm", "LLM"):
        if LLMNode is None:
            msg = "LLM node type requires spec-04 implementation (LLMNode not yet available)"
            raise ValueError(msg)
        # spec-04 接线后: return LLMNode(...)
        return LLMNode(name=definition.name, node_type=definition.type, config=definition.config, operator_log=op_log)  # type: ignore[unreachable]
    if definition.type in ("http", "HTTP"):
        if HTTPNode is None:
            msg = "HTTP node type requires spec-05 implementation (HTTPNode not yet available)"
            raise ValueError(msg)
        return HTTPNode(name=definition.name, node_type=definition.type, config=definition.config, operator_log=op_log)  # type: ignore[unreachable]
    # 3. 未知类型
    registered = list_node_types()
    msg = (
        f"Unknown node type '{definition.type}'. "
        f"Registered types: {registered}. "
        "Use register_node_type() to add custom types."
    )
    raise ValueError(msg)
