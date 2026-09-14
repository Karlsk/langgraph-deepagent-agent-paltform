"""Node factory and plugin registry (spec-03, CONTRACT §4.5; spec-04/05 scheme A routing).

Resolution order (user-finalized scheme A, spec-04 supplement): built-in
branches first (specialized constructors, exactly two, R4 no elif), then the
plugin registry (generic BaseNode interface), then ValueError for unknown
types. Built-in classes are top-level imported (AD-04); the llm/http imports
sit at the bottom of this module to break the self-registration import cycle.

Optional injected parameters reach plugins by **signature probing** (CONTRACT
§4.5, node-development §7.1): the plugin branch passes ``workflow_runner`` only
to classes whose ``__init__`` declares it or accepts ``**kwargs``. That keeps
``BaseNode``'s frozen signature (§4.4) free of a parameter most nodes would
never use, and leaves every existing plugin untouched.

Dependency red-line 2: never import registry / graph_builder here.
"""

from __future__ import annotations

import inspect

from app.workflow.models import NodeDefinition, OperatorLog
from app.workflow.nodes.base import BaseNode
from app.workflow.ports import ChatModelFactory, WorkflowRunner

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


def _accepts_kwarg(node_class: type[BaseNode], kwarg: str) -> bool:
    """Whether the plugin's ``__init__`` can take ``kwarg`` (§7.1 probing rule).

    A class swallowing ``**kwargs`` counts as accepting: it can receive the value
    whether or not it names it, and withholding it would silently drop an
    injection such a plugin asked for by accepting arbitrary keywords.
    """
    parameters = inspect.signature(node_class.__init__).parameters
    if any(p.kind is inspect.Parameter.VAR_KEYWORD for p in parameters.values()):
        return True
    return kwarg in parameters


def create_node(
    definition: NodeDefinition,
    operator_log: OperatorLog | None = None,
    chat_model_factory: ChatModelFactory | None = None,
    workflow_runner: WorkflowRunner | None = None,
) -> BaseNode:
    """内置优先（恰好 2 分支）→ 插件注册表兜底 → 未知 ValueError（R4，方案 A）.

    ``chat_model_factory`` 仅透传给 llm 分支（S20）；``workflow_runner`` 按签名探测
    透传给声明了它的插件（S23/S24）。两者都是不透明 callable 而非注册表，
    故不违反 H5「factory 无 workflow_registry 参数」。
    """
    op_log = operator_log or OperatorLog(node_name=definition.name, input_schema={}, output_schema={})
    # 1. 内置兜底恰好 2 个分支（R4：禁 elif），专用构造器签名
    if definition.type in ("llm", "LLM"):
        return _llm_node.LLMNode(
            name=definition.name,
            llm_config=definition.config,
            operator_log=op_log,
            chat_model_factory=chat_model_factory,
        )
    if definition.type in ("http", "HTTP"):
        return _http_node.HTTPNode(name=definition.name, config=definition.config, operator_log=op_log)
    # 2. 插件注册表（generic BaseNode interface）+ 可选注入参数按签名探测
    if definition.type in _NODE_REGISTRY:
        node_class = _NODE_REGISTRY[definition.type]
        injected: dict[str, WorkflowRunner | None] = (
            {"workflow_runner": workflow_runner} if _accepts_kwarg(node_class, "workflow_runner") else {}
        )
        return node_class(
            name=definition.name,
            node_type=definition.type,
            config=definition.config,
            operator_log=op_log,
            **injected,
        )
    # 3. 未知类型
    registered = list_node_types()
    msg = (
        f"Unknown node type '{definition.type}'. "
        f"Registered types: {registered}. "
        "Use register_node_type() to add custom types."
    )
    raise ValueError(msg)


# 顶层导入置于文件底部（AD-04）：节点模块自注册需先拿到 register_node_type，
# 打破 factory <-> 节点模块的循环导入；模块形态导入对任意导入顺序均安全
# （属性访问推迟到 create_node 调用期，spec-04/05 接线点）。
import app.workflow.nodes.http_node as _http_node  # noqa: E402
import app.workflow.nodes.llm_node as _llm_node  # noqa: E402
import app.workflow.nodes.python_node  # noqa: E402, F401 — 触发 "python" 插件类型自注册（K5）
import app.workflow.nodes.subworkflow_node  # noqa: E402, F401 — 触发 "subworkflow" 插件类型自注册（K5，S23/S24）
