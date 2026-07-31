# 工作流引擎重实现 · 编码契约（CONTRACT）

| 项 | 值 |
| --- | --- |
| 文档版本 | v1.0 |
| 编写日期 | 2026-07-30 |
| 文档状态 | 生效中 |
| 角色 | 实现期（Phase 0-9）**唯一契约基准**，即《01-分阶段开发计划》头部引用的《绑定契约 CONTRACT》 |
| 适用范围 | `app/workflow/` 全部编码、测试、评审活动 |
| 编号体系 | K1-K10 / C1-C9 / H1-H7 / R1-R10 / Phase 0-9 / AD-01..12 / EXP-G/C/L/X / TC |

> 四份规划文档（00-03）是设计论证；本文件是**冻结清单**。实现者与评审者以本文件为最终核对依据。
> 本文件不重复规划文档的论证过程，只冻结"做什么、不做什么、做成什么样、怎么检查"。

---

## 1. 契约效力与优先级

**效力层级（高 > 低）：**

```
CONTRACT.md（本文件） > spec-00..09 > 规划文档（00-03） > 原 workflow_v2 代码惯例
```

- 功能契约（接口签名、异常族、行为语义、DoD、TDD 要点）严格继承《01-分阶段开发计划》，本文件第 4/5/6 章是其冻结形态。
- 工程口径（日志/重试/导入/lint/目录）按本文件第 9 章 AD 条目适配当前仓库；AD 条目与规划文档冲突时**以 AD 为准**，且 AD 必须注明被覆盖的原文档口径。
- **冲突处理规则**：发现任何两层级之间的矛盾 → **停下，提问**，给出 2-3 个可选方案（各带影响面），由人类决策者拍板。**禁止"善意推断"式自行折中**（AI 协作守则）。
- 本文件的修改只能走第 11 章变更流程；任何人不得"先改代码、后补契约"。

## 2. 范围契约（R1 落地）

### 2.1 本期允许创建/修改的文件全集（白名单）

**`app/workflow/`（15 个模块，多一个即违规）：**

```
app/workflow/
  __init__.py            # __version__，廉价导入，不导入重依赖
  models.py              # DSL 模型 + 精简 NodeType + 异常族（单点）
  state.py               # StateModelFactory（< 150 行）
  graph_builder.py       # GraphBuilder + BuildResult（< 300 行，只含 GraphBuilder）
  registry.py            # WorkflowRegistry + RunResult + RunLogCollector + load_definitions_from_dir（< 350 行）
  utils.py               # 仅 convert_state_to_dict / map_output_to_state（< 150 行）
  logging_conf.py        # setup_logging + redact + 脱敏 processor
  cli.py                 # ApiResponse + build_registry + build_parser + main（< 200 行）
  __main__.py            # python -m app.workflow 入口
  api.py                 # 【可选】FastAPI router（AD-10）
  nodes/
    __init__.py          # 导出 BaseNode/register_node_type/create_node/LLMNode/HTTPNode
    base.py              # BaseNode + RunLogCollectorLike + _RUN_COLLECTOR（< 200 行）
    factory.py           # _NODE_REGISTRY + register/list/create（< 120 行）
    llm_node.py          # LLMNode + LLMConfig（< 250 行）
    http_node.py         # HTTPNode + HTTPNodeConfig（< 280 行）
  config/examples/
    minimal.yaml         # Phase 1
    http_demo.yaml       # Phase 9
    condition_branch.yaml # Phase 6
```

**测试（镜像结构，`tests/` 目录本仓库新建）：**

```
tests/__init__.py
tests/conftest.py                     # load_dotenv + restore_node_registry(autouse) + FakeLLM/EchoNode 等公共夹具
tests/unit/__init__.py
tests/unit/workflow/                  # 镜像 app/workflow：test_package_import / test_models / test_state /
                                      # test_utils / test_graph_builder / test_registry / test_logging_conf / test_cli
tests/unit/workflow/nodes/            # test_base / test_factory / test_llm_node / test_http_node
tests/integration/__init__.py
tests/integration/workflow/           # test_state_channels / test_graph_e2e / test_concurrency / test_log_collection
```

**既有文件改动白名单（只允许这些，且改动范围限对应 spec 描述）：**

| 文件 | 改动内容 | 落点 |
| --- | --- | --- |
| `pyproject.toml` | 依赖（PyYAML/langchain-anthropic/httpx 提主依赖/pytest-cov）、pytest markers、ruff select+per-file-ignores | spec-00 |
| `Makefile` | 新增 `test` / `test-unit` / `test-integration` / `test-cov` 目标 | spec-00 |
| `.github/workflows/ci.yaml` | 增加 `pytest -m unit` 步骤；Phase 9 加覆盖率门禁 | spec-00 / spec-09 |
| `.env.example` | 增补 `ANTHROPIC_API_KEY=` 空值占位 | spec-00 |
| `app/api/v1/api.py` | 【可选】挂载 workflow router | spec-08 |
| `README.md`（根） | 追加"工作流引擎"快速开始章节 | spec-09 |
| `docs/workflow-reimpl-plan/delivery/` | 新增《安全加固清单》《契约符合性矩阵》两个 md | spec-09 |

**白名单外新增任何文件 = 违规**；确需新增 → 走第 11 章变更流程。

### 2.2 本期禁止项（丢弃清单，一行代码都不写）

- 17 种领域节点（plan/worker/reflection/llm_reflection/agent/tool/merge/extract/dispatcher/collector/subgraph/triage/device_work/controller_work/aggregate/score/output）及 `NodeType` 领域枚举。
- 子图嵌套、`langgraph.types.Send` 并行扇出、Neo4j 计划生成、MCP client、`game_agent/`、`node4j/`。
- `LLMHelper` 单例、`invoke_with_tools`、`auto_generate_operator_logs` 领域 schema 分支、`prompt_template.py`、`extract_json_block`。
- 任何领域字段名特判：`circle_conclusions` / `planner_result` / `worker_result` / `reflector_result` / `circle_meta` / `circle_index` / `step` / `current_node` / `device` / `cmd` / `short_memory` 等字面量**不得出现**在 `app/workflow/` 代码中（"未来扩展"说明性注释除外）。
- 任何缓存（H4：本期默认无缓存）。
- `unregister_workflow`（H7：唯一删除入口是 `delete_workflow`）。

## 3. 包结构与依赖方向契约

**分层单向依赖（只允许向下）：**

```
L4  registry.py
      │
L3  graph_builder.py ────────► state.py
      │                          │
L2  nodes/factory.py          models.py ◄──────────┐
      │                                            │
L1  nodes/llm_node.py  nodes/http_node.py          │
      │                  │                         │
L0  nodes/base.py ──► utils.py ────────────────────┘
        │
    models.py

横切：logging_conf.py 不依赖任何业务模块，任何模块都可 import 它
入口：cli.py / api.py / __main__.py 位于 L4 之上，只允许向下调用
```

**四条依赖红线（违反即架构腐化）：**

1. `models.py` 不得 import 任何引擎模块（只依赖 pydantic / 标准库 / `yaml`）。
2. `nodes/*` 不得 import `registry` / `graph_builder`（节点不知道图与注册表的存在，同时根除 H5）。
3. `utils.py` 不得 import LLM/HTTP 客户端库（C7）。
4. 引擎自包含：`app/workflow/` 任何模块**不得 import `app.core.*` / `app.api.*` / `app.services.*`**（AD-02；反向集成时由外部装配，如可选 `api.py` 允许 import `app.core.limiter`，它是入口层例外，见 spec-08）。

## 4. 接口冻结清单

> 以下签名为**跨阶段合同**：实现逐字一致（导入路径 `app.workflow.*`），偏差打回。
> 继承自《01-分阶段开发计划》各 Phase"接口契约"小节；AD 适配点以【AD-xx】行内标注。

### 4.1 `app/workflow/__init__.py`

```python
__version__: str = "0.1.0"   # 不导入任何重依赖，保持导入廉价
```

### 4.2 `app/workflow/models.py`

```python
class NodeType(str, Enum):
    """本期节点类型枚举（精简版，落实 C8）。插件类型是任意字符串，不受此枚举约束。"""
    LLM = "llm"
    HTTP = "http"

class StateFieldSchema(BaseModel):
    type: str
    default: Any = None
    description: str = ""
    reducer: Literal["add", "last"] | None = None

class NodeDefinition(BaseModel):
    name: str                              # 非空校验
    type: str                              # 刻意保持 str：支持注册表插件类型（R4）
    config: dict[str, Any] = Field(default_factory=dict)

class EdgeDefinition(BaseModel):
    source: str
    target: str                            # 节点名或字面量 "END"
    condition: str | None = None           # 点路径条件，如 "check_result.status == 'ok'"

class ExecutionLog(BaseModel):
    node_name: str
    node_type: str
    timestamp: datetime                    # 默认 datetime.now
    input_data: dict
    output_data: dict
    execution_time_ms: float
    error: str | None = None

class OperatorLog(BaseModel):
    node_name: str
    input_schema: dict[str, StateFieldSchema]
    output_schema: dict[str, StateFieldSchema]

class WorkflowDefinition(BaseModel):
    # model_config 保持默认 extra="ignore"（YAML 注释性键容忍，K1）
    workflow_id: str
    entry_point: str
    nodes: list[NodeDefinition]
    edges: list[EdgeDefinition] = Field(default_factory=list)
    state_schema: dict[str, StateFieldSchema]
    operator_logs: dict[str, OperatorLog] = Field(default_factory=dict)
    execution_history: list[ExecutionLog] = Field(default_factory=list)
    # 模型级校验：节点名唯一、nodes 非空；图级校验归 GraphBuilder._validate_definition（C5）

def parse_definition(data: dict[str, Any]) -> WorkflowDefinition: ...
def load_definition_from_yaml(path: str | Path) -> WorkflowDefinition: ...
    # 只准 yaml.safe_load（D6）；文件不存在/解析失败 → 带路径上下文的 ValueError
```

### 4.3 `app/workflow/state.py`

```python
TYPE_MAP: dict[str, Any] = {
    "str": str, "int": int, "float": float, "bool": bool,
    "list": list, "dict": Any, "object": Any, "any": Any,
    "List[str]": list, "Dict[str, Any]": Any,
}

def _last(a: Any, b: Any) -> Any:
    """reducer='last' 的合并函数：后写覆盖。"""

class StateModelFactory:
    @staticmethod
    def create_state_model(
        state_schema: dict[str, StateFieldSchema],
    ) -> type[BaseModel]:
        """reducer='add' → Annotated[list, operator.add]；'last' → Annotated[T, _last]；
        未声明 → 普通字段（LastValue 后写覆盖）；未声明 history 自动注入（add channel）；
        基类 ConfigDict(extra='allow')；未知 type → ValueError 并列出支持类型。"""
```

**修订备注【EXP-G8 决策，2026-07-30】**（签名不变，语义补充）：依据 EXP-G8 实测（langgraph 1.0.2 的 channel 集合在构造期冻结，未声明键被静默丢弃，`extra="allow"` 对 channel 写入面无效；证据见 `api-exploration-1x.md` G8 行）+ 2026-07-30 决策（方案 1）：`create_state_model` 需支持构建期按 `definition.nodes` 预声明 `{node_name}_result: (Any, None)` 字段（LastValue channel）以承载 S4 双写；除此之外的任意 extra 键**不支持写入运行期 state**，YAML `state_schema` 必须显式声明；`extra="allow"` 仅保留为模型自身宽容校验。入参形态（节点名清单入参或由 GraphBuilder 组装）的具体设计留待 spec-02 实施时落地。

### 4.4 `app/workflow/nodes/base.py`

```python
@runtime_checkable
class RunLogCollectorLike(Protocol):
    """运行级日志收集器接口（Phase 7 提供实现，H3）。"""
    def add(self, log: ExecutionLog) -> None: ...

_RUN_COLLECTOR: ContextVar[RunLogCollectorLike | None]  # ContextVar("workflow_run_collector", default=None)

def set_run_collector(collector: RunLogCollectorLike | None) -> Token: ...
def get_run_collector() -> RunLogCollectorLike | None: ...

class BaseNode(ABC):
    name: str
    node_type: NodeType | str
    config: dict[str, Any]
    operator_log: OperatorLog | None

    def __init__(
        self,
        name: str,
        node_type: NodeType | str,
        config: dict[str, Any],
        operator_log: OperatorLog | None = None,
    ) -> None: ...

    @abstractmethod
    def build_runnable(self) -> Runnable:
        """唯一执行单元：RunnableLambda(func).with_config(tags=[name])，func(state)->dict（K4）。"""

    @abstractmethod
    def validate_config(self) -> bool:
        """配置非法时抛 ValueError。"""

    def log_execution(self, execution_log: ExecutionLog) -> None:
        """写实例历史 + 当前运行级收集器（若存在）。"""

    def get_execution_history(self) -> list[ExecutionLog]: ...   # 返回副本
    def clear_execution_history(self) -> None: ...               # 仅调试；运行时流程不得依赖
    def wrap_runnable(self, func: Callable[[dict[str, Any]], dict[str, Any]]) -> RunnableLambda: ...
```

### 4.5 `app/workflow/nodes/factory.py`

```python
_NODE_REGISTRY: dict[str, type[BaseNode]]

def register_node_type(type_name: str, node_class: type[BaseNode]) -> None:
    """注册前校验 BaseNode 子类，否则 TypeError。"""
def list_node_types() -> list[str]: ...
def create_node(
    definition: NodeDefinition,
    operator_log: OperatorLog | None = None,
) -> BaseNode:
    """插件注册表优先；内置兜底恰好 2 个分支：("llm","LLM")→LLMNode、("http","HTTP")→HTTPNode；
    未知类型 ValueError 列出 list_node_types() 并提示 register_node_type()；
    无 workflow_registry 参数（H5）。"""
```

【AD-04】factory 及节点模块一律**顶层导入**（覆盖原文档"函数内延迟导入"口径）；langchain-anthropic 为正式依赖。

### 4.6 `app/workflow/utils.py`

```python
def convert_state_to_dict(state: Any) -> dict[str, Any]:
    """pydantic → model_dump()；dict 直通；其它 → {}。"""
def map_output_to_state(
    node_name: str,
    node_output: dict[str, Any],
    state: dict[str, Any] | None = None,
    *,
    dual_write: bool = True,
    history_increment: bool = True,
) -> dict[str, Any]:
    """双写：state_update[f"{node_name}_result"] = node_output 且 update(node_output)；
    dual_write=False 只写整包。history 仅当 state 含 list 型 history、node_output 未写 history
    且 history_increment=True 时追加**增量** [entry]（add channel 下禁返回全量，C4 修正）。"""
```

### 4.7 `app/workflow/nodes/llm_node.py`

```python
class LLMConfig(BaseModel):
    # model_config = ConfigDict(extra="forbid")（02 §3.3：节点配置拒绝未知字段）
    llm_type: Literal["openai", "anthropic"] = "openai"
    model_name: str = "gpt-4o-mini"
    base_url: str | None = None
    api_key_env: str | None = None     # 显式 env 名；未设置按 llm_type 取默认
    base_url_env: str | None = None
    temperature: float = Field(default=0.7, ge=0.0, le=2.0)
    max_tokens: int | None = None
    top_p: float | None = None
    system_prompt: str = ""
    extra_params: dict[str, Any] = Field(default_factory=dict)
    max_retries: int = Field(default=3, ge=0)
    retry_base_delay: float = Field(default=1.0, gt=0)
    # 不设明文 api_key 字段（H6/ADR-008）

class LLMNode(BaseNode):
    def __init__(
        self,
        name: str,
        llm_config: LLMConfig | dict[str, Any],
        messages: list[BaseMessage] | None = None,
        operator_log: OperatorLog | None = None,
    ) -> None: ...
    def validate_config(self) -> bool: ...
    def _resolve_api_key(self) -> str: ...        # 缺失 → ConfigError，消息含 env 名不含密钥值
    def _get_llm_instance(self) -> Any: ...       # 懒加载（K10）
    def _invoke_with_retry(self, llm: Any, messages: list[Any]) -> Any: ...
    def build_runnable(self) -> Runnable: ...
# 模块底部自注册：register_node_type("llm", LLMNode)
```

【AD-03】`_invoke_with_retry` 用 **tenacity** 实现（覆盖原文档手写循环口径），语义合同不变：仅 429/rate 命中重试；退避 `retry_base_delay * 2**attempt`；耗尽抛 `LLMNodeError`（含尝试次数）；测试 monkeypatch `tenacity.nap.sleep` 断言退避序列 `1,2,4`。

### 4.8 `app/workflow/nodes/http_node.py`

```python
class HTTPNodeConfig(BaseModel):
    # model_config = ConfigDict(extra="forbid")
    url: str
    method: Literal["GET", "POST", "PUT", "DELETE"] = "POST"
    headers: dict[str, str] | None = None
    body_template: str | None = None
    response_path: str | None = None          # 点路径，如 "data.result"
    timeout: float = Field(default=30.0, gt=0)
    max_retries: int = Field(default=0, ge=0) # 默认不重试；显式开启（H2）
    retry_base_delay: float = Field(default=1.0, gt=0)
    retry_on_status: list[int] = Field(default_factory=lambda: [429, 500, 502, 503, 504])
    mock_enabled: bool = False                # 默认关闭；显式启用才生效（H2/H6）
    mock_responses: dict[str, str] | None = None

class HTTPNode(BaseNode):
    def __init__(
        self,
        name: str,
        config: dict[str, Any] | HTTPNodeConfig,
        operator_log: OperatorLog | None = None,
    ) -> None: ...
    def validate_config(self) -> bool: ...
    def render_template(self, template: str, context: dict[str, Any]) -> str: ...
    def _extract(self, data: Any, path: str | None) -> Any: ...
    def build_runnable(self) -> Runnable: ...
# 模块底部自注册：register_node_type("http", HTTPNode)
```

【AD-03】重试同样 tenacity 化；`_send_once` 为内部辅助（`httpx.request(..., timeout=...)`，同步 K10）。

### 4.9 `app/workflow/graph_builder.py`

```python
class BuildResult(NamedTuple):
    compiled_graph: Any                 # langgraph CompiledStateGraph
    nodes_map: dict[str, BaseNode]      # 节点名 -> 节点实例（供 registry 收集日志）

class GraphBuilder:
    def __init__(
        self,
        *,
        no_match_policy: Literal["raise", "default"] = "raise",
    ) -> None: ...
    def build_graph(
        self,
        definition: WorkflowDefinition,
        *,
        default_edges: dict[str, str] | None = None,
    ) -> BuildResult: ...
    def _validate_definition(self, definition: WorkflowDefinition) -> None: ...
    def _add_nodes(self, graph: StateGraph, definition: WorkflowDefinition) -> dict[str, BaseNode]: ...
    def _add_edges(
        self,
        graph: StateGraph,
        definition: WorkflowDefinition,
        nodes_map: dict[str, BaseNode],
        default_edges: dict[str, str] | None,
    ) -> None: ...
    def _build_condition_router(
        self,
        source: str,
        conditional_edges: list[EdgeDefinition],
        default_target: str | None,
    ) -> Callable[[Any], str]: ...
    @staticmethod
    def _parse_condition(condition: str) -> tuple[str, str | None]: ...
    @staticmethod
    def _resolve_path(state_dict: dict[str, Any], path: str) -> Any: ...
```

七步顺序（K6，代码注释逐步标注 1..7）：`_validate_definition` → `create_state_model` → `StateGraph(state)` → `_add_nodes` → `_add_edges` → `set_entry_point` → `compile()`。构造器**无 registry 参数**（H5 签名形态防线）。

### 4.10 `app/workflow/registry.py`

```python
@dataclass(frozen=True)
class RunResult:
    workflow_id: str
    run_id: str
    output: dict[str, Any]
    execution_logs: list[ExecutionLog]
    started_at: datetime
    finished_at: datetime
    @property
    def duration_ms(self) -> float: ...

class RunLogCollector:
    """运行级日志收集器（run-scoped，H1/H3）。"""
    def __init__(self, run_id: str) -> None: ...
    def add(self, log: ExecutionLog) -> None: ...      # 内部加锁
    def collect(self) -> list[ExecutionLog]: ...       # 按 timestamp 排序的副本

class WorkflowRegistry:
    def __init__(
        self,
        *,
        no_match_policy: Literal["raise", "default"] = "raise",
    ) -> None: ...
    def register_workflow(
        self,
        definition: WorkflowDefinition,
        *,
        default_edges: dict[str, str] | None = None,
    ) -> str: ...
    def delete_workflow(self, workflow_id: str) -> bool: ...   # 唯一删除入口（C6/H7）
    def get_workflow(self, workflow_id: str) -> Any: ...       # 不存在 → WorkflowNotFoundError
    def has_workflow(self, workflow_id: str) -> bool: ...
    def list_workflows(self) -> list[str]: ...
    def execute_workflow(self, workflow_id: str, input_data: dict[str, Any]) -> RunResult: ...
    # 查询接口
    def get_workflow_definition(self, workflow_id: str) -> WorkflowDefinition | None: ...
    def get_operator_logs(self, workflow_id: str) -> dict[str, OperatorLog]: ...
    def get_operator_log_by_node(self, workflow_id: str, node_name: str) -> OperatorLog | None: ...
    def get_execution_history(self, workflow_id: str) -> list[ExecutionLog]: ...
    def get_node_execution_history(self, workflow_id: str, node_name: str) -> list[ExecutionLog]: ...
    def get_node_by_name(self, workflow_id: str, node_name: str) -> BaseNode | None: ...
    def get_registry_stats(self) -> dict[str, Any]: ...

def load_definitions_from_dir(directory: str | Path) -> list[WorkflowDefinition]: ...
```

### 4.11 `app/workflow/logging_conf.py`

```python
def setup_logging(level: str = "INFO", *, json_output: bool = False) -> None:
    """structlog 初始化。幂等：重复调用不叠加 handler；
    已被外部（app.core.logging）配置过时幂等跳过【AD-02】。"""

SECRET_KEY_PATTERNS: tuple[str, ...] = ("api_key", "apikey", "token", "secret", "password", "authorization", "cookie")

def redact(data: Any, *, max_len: int = 500) -> Any:
    """递归脱敏：密钥键值替换为 ***REDACTED***，超长截断并标注 ...(truncated)，不可 JSON 化走 default=str。"""

def redact_processor(logger: Any, method_name: str, event_dict: dict[str, Any]) -> dict[str, Any]:
    """structlog processor 形态的脱敏器【AD-02：替代原文档 stdlib RedactFilter】，
    对 event_dict 递归应用 redact()；由 setup_logging 挂入处理器链。"""
```

**修订说明【AD-02 v2，2026-07-30】**（上方签名全部保持不变，仅语义与职责边界收窄）：

- `setup_logging` 语义收窄为**最小幂等 bootstrap**，仅用于 **CLI 独立入口**（spec-08）与**裸测试环境**；FastAPI 集成场景下日志配置由 `app.core.logging` 全权负责，该函数因幂等检测而永不生效（等效于不存在）。幂等验收方式不变（重复调用不叠加 handler，handler 计数断言）。
- **引擎模块只 `get_logger` 不配置日志**：`app/workflow/` 内除 `logging_conf.py` 自身外，任何模块不得调用/引入日志配置，只用 `structlog.get_logger(__name__)`；"不得 import `app.core.*`"红线不变。引擎日志在部署时自动继承宿主的 processor 链、格式与输出。
- **`redact_processor` 由宿主组装点注册**：实现保留在本模块（安全能力随引擎携带），但挂入全局 processor 链的动作在 FastAPI 场景下属于宿主组装点（composition root：`app/main.py` 启动处或 `app.core.logging` 暴露的注入钩子）；仅 CLI 独立模式下由 `setup_logging` 自行挂入。

### 4.12 `app/workflow/cli.py`

```python
@dataclass
class ApiResponse:
    success: bool
    data: Any = None
    error: str | None = None
    metadata: dict[str, Any] = field(default_factory=dict)
    def to_json(self) -> str: ...        # ensure_ascii=False

def build_registry(directory: str | Path) -> WorkflowRegistry: ...
def build_parser() -> argparse.ArgumentParser: ...   # 子命令 run：--dir/--workflow/--input/--log-level/--json-log
def main(argv: list[str] | None = None) -> int: ...  # 成功 0 / 失败 1；信封 print 输出（T201 豁免点）
```

## 5. 异常族契约

**单点定义于 `app/workflow/models.py`**；其它模块与文档只引用，**不得各自另行定义**：

```python
class WorkflowEngineError(Exception):
    """引擎全部异常的统一基类。"""
class ConfigError(WorkflowEngineError):
    """配置错误（如缺失必需 env 密钥）。"""
class WorkflowNotFoundError(WorkflowEngineError):
    """未知 workflow_id。"""
class ConditionNotMatchedError(WorkflowEngineError):
    """条件路由无任何命中边。"""
class LLMNodeError(WorkflowEngineError):
    """LLM 调用失败（含重试耗尽）。"""
class HTTPNodeError(WorkflowEngineError):
    """HTTP 调用失败（含重试耗尽、mock 未命中显式报错）。"""
```

**场景映射（pytest.raises 按此断言）：**

| 场景 | 异常类型 | 落实 spec |
| --- | --- | --- |
| 缺必需 env 密钥 | `ConfigError` | spec-04 |
| LLM 调用失败 / 重试耗尽 | `LLMNodeError` | spec-04 |
| HTTP 调用失败 / 重试耗尽或不可重试 / mock 启用但未命中 | `HTTPNodeError` | spec-05 |
| 条件路由全部未命中（`no_match_policy="raise"`） | `ConditionNotMatchedError` | spec-06 |
| 未知 `workflow_id` | `WorkflowNotFoundError` | spec-07 |
| 定义解析 / 字段校验失败 | `ValueError` / pydantic `ValidationError` | spec-01 |

## 6. 行为语义契约

| # | 语义 | 冻结约定 |
| --- | --- | --- |
| S1 | reducer 三态 | `add` → `Annotated[list, operator.add]` 列表合并；`last` → `Annotated[T, _last]` 后写覆盖；未声明 → 普通字段（LangGraph LastValue 后写覆盖）。reducer **只**由 YAML 显式声明驱动（C2） |
| S2 | history 自动注入 | `state_schema` 未显式声明 `history` 时注入 `(Annotated[list, operator.add], default_factory=list)`；显式声明优先 |
| S3 | history 增量追加 | `map_output_to_state` 在 add channel 下只返回**增量** `[entry]`，禁返回 `history + [entry]` 全量（C4 修正：防历史翻倍）；entry 形如 `f"{node_name}: {str(node_output)[:100]}..."` |
| S4 | 双写 | 默认 `{node_name}_result` 整包 + 逐字段平铺（K9 Dify 风格）；`dual_write=False` 只写整包。【EXP-G8 决策，2026-07-30】`{node_name}_result` 键须在构建期由 StateModelFactory 预声明（见 §4.3 修订备注），否则被 langgraph 静默丢弃 |
| S5 | 节点进出 | 入口 `convert_state_to_dict(state)`，出口 `map_output_to_state(name, output, state_dict)`（R3）；禁止 mutate 输入 state |
| S6 | 条件路由 no-match | `raise`（默认）→ `ConditionNotMatchedError`（含 source 与全部条件）；`default` → 走 `default_edges[source]`，构建期未提供即 `ValueError`。**禁止静默落到最后一条边**（C3） |
| S7 | 条件表达式 | `_parse_condition`：含 `==` → 等值比较（两侧 strip、expected 去引号）；否则纯路径真值判断；`_resolve_path` 点路径逐层解析，非 dict 中途 → None。**禁 eval** |
| S8 | retry 显式开关 | LLM `max_retries` 默认 3；HTTP `max_retries` 默认 0（不重试）；退避 `retry_base_delay * 2**attempt`；仅命中重试谓词（LLM：429/rate；HTTP：`retry_on_status`）才重试 |
| S9 | mock 显式开关 | `mock_enabled=False` 默认；启用时未命中 key（`"{METHOD} {url}"` 回退 `"{url}"`）→ `HTTPNodeError`，**禁止静默回退真实调用**（H2/H6） |
| S10 | execute_workflow 并发 | per-workflow `RLock` 串行化同一 workflow；不同 workflow 并行；`_meta_lock` 守护锁表创建（H1，ADR-004） |
| S11 | 运行级日志收集 | 每次运行创建 `RunLogCollector(run_id)`，经 `_RUN_COLLECTOR` ContextVar 传播；`try/finally` 用 token 复位，**ContextVar 不得泄漏**；运行时**禁止**调用 `node.clear_execution_history()` 收集日志（H1/H3） |
| S12 | execution_history 单槽位 | `definition.execution_history` 只保留**最近一次运行**的日志（防无界增长，文档化决策） |
| S13 | delete 不变量 | `delete_workflow` 同时删 `_registry` / `_definitions` / `_nodes_map` / `_run_locks` 四处条目；重复注册 = `_meta_lock` 下原子替换（H7） |
| S14 | extra 策略三分 | WorkflowDefinition=`ignore`；节点配置（LLMConfig/HTTPNodeConfig）=`forbid`；动态状态模型=`allow`（02 §3.3）。【EXP-G8 决策，2026-07-30】动态模型 `allow` 仅为模型自身宽容校验；运行期 state 仅支持声明键（含预声明的 `{node_name}_result`），未声明键被 langgraph 静默丢弃（见 `api-exploration-1x.md` G8 行） |
| S15 | 日志形态 | structlog；事件名 lowercase_with_underscores；kwargs 传参禁 f-string；`logger.exception()` 留 traceback；ExecutionLog/日志只记摘要（消息条数、method/url、配置摘要），**不含密钥与完整 state**（H6）【AD-02】 |
| S16 | YAML 安全 | 全引擎只允许 `yaml.safe_load`（D6） |

## 7. 探索先行规则（R-EXP）

> 新增规则，优先级等同红线。执行载体：`api-exploration-1x.md`。

1. **凡涉及 langgraph / langchain 1.x API 行为的编码，对应 EXP 项必须先在 `api-exploration-1x.md` 闭环**（填写实测行为 + 证据）。未闭环即编码 = 违规，评审打回。
2. **门禁映射**：EXP-G → spec-02/06/07；EXP-C → spec-03/07；EXP-L → spec-04；EXP-X → spec-00/01。全部 EXP 闭环是 **M1 开工前置条件**。
3. **探索手段仅限**：`.venv` 已装包源码核查、离线实例化、mock 传输层、characterization test。**禁止真实 API 调用**（不发 OpenAI/Anthropic 请求）。
4. **版本以 uv.lock 为准**：langgraph 1.0.2 / langchain 1.0.5 / langchain-core 1.0.4 / langchain-openai 1.0.2 / langchain-anthropic（spec-00 新增，区间由 EXP-L5 确定）/ pydantic 2.11 / Python 3.13。
5. **实测与规划假设不符** → 停下，走第 11 章变更流程（可能涉及回退方案，如 TypedDict state）；**禁止擅自改设计绕开**。
6. 探索产出同时回写 `api-exploration-1x.md` 的"0.x 假设 vs 1.x 实测对照总表"（EXP-X2），作为本文件附录。

## 8. 红线契约 R1-R10（仓库适配版）

> 一票否决项。每条 = 一句话 + 机器检查方法。正/反例全文见《02-开发规范》第 0 章。

| 编号 | 一句话 | 机器检查方法 |
| --- | --- | --- |
| R1 | 只实现 BaseNode/LLMNode/HTTPNode，禁止新增节点类型或领域逻辑 | `ls app/workflow/nodes/` 对白名单；`grep -rn "plan\|worker\|dispatcher" app/workflow/` 仅出现于"未来扩展"注释；`NodeType` 成员数守护测试 |
| R2 | reducer 只来自 YAML 显式声明，禁止硬编码业务字段名 | `grep -nE "circle_\|planner_\|worker_\|reflector_\|current_node" app/workflow/state.py` 零命中；守护测试 `test_no_hardcoded_field_names` |
| R3 | 节点 convert_state_to_dict 进 / map_output_to_state 出，禁止 mutate 输入 state | 节点 func 代码审查；R3 进出管线测试（spec-04/05 契约测试） |
| R4 | 新节点必须经 register_node_type 注册，create_node 内置分支恰好 2 个 | 守护测试断言内置分支数 == 2 且为 `("llm","LLM")` / `("http","HTTP")` 大小写集合；`grep -n "elif" app/workflow/nodes/factory.py` 人工核对 |
| R5 | 密钥 env-only，禁止硬编码，禁止完整 state/密钥写日志 | `grep -rniE "(api[_-]?key\|token\|secret\|password)\s*[:=]" app/workflow/ tests/` 人工逐条确认；H6 守护测试（test_execution_log_no_secret_leak 等） |
| R6 | 禁止死 try/except，错误必须显式处理（记录 + 重抛/降级且有测试） | ruff `BLE` 规则；`grep -rn "except.*:\s*pass" app/workflow/` 零命中；失败路径测试覆盖（H2） |
| R7 | 严格 TDD（RED-GREEN-REFACTOR），覆盖率 ≥ 80% | 测试先于实现提交；`uv run pytest --cov=app.workflow --cov-report=term-missing --cov-fail-under=80` |
| R8 | 小文件（< 400 行）、小函数（< 50 行）、单一职责 | `wc -l app/workflow/**/*.py`；review 抽查 |
| R9 | pydantic v2 全量类型标注，边界处校验 fail fast | `make typecheck`（pyright standard）零错误；ruff `D` 系列 docstring 规则 |
| R10 | 任何缓存必须有上限 + 失效 + 开关（本期默认无缓存） | `grep -n "cache\|lru_cache" app/workflow/` 零命中 |
| R-EXP | 1.x API 未探索闭环禁止编码（见第 7 章） | `api-exploration-1x.md` 全部 EXP 项"实测结果"非空 |

## 9. 适配决策 AD-01..12（单点定义）

> 工程口径适配，不改变功能契约。各 spec 与 README 只引用编号。

- **AD-01 包映射**：规划文档占位包名 `workflow_engine/` → 落地 `app/workflow/`；导入路径 `app.workflow.*`；测试落位 `tests/unit|integration/workflow/`（镜像）。模块逐一对位见第 2 章白名单。
- **AD-02 日志 structlog 化**：引擎内一律 `structlog.get_logger(__name__)`；事件名 lowercase_with_underscores、kwargs 传参禁 f-string、`logger.exception()` 留 traceback。`setup_logging`/`redact` 契约保留；脱敏实现为 structlog processor（`redact_processor`），替代原文档 stdlib `RedactFilter`。引擎**不 import app.core.\***；CLI 场景自举配置；FastAPI 场景已被 `app.core.logging` 配置时幂等跳过。（细化与 v2 修订见 §4.11 修订说明【AD-02 v2，2026-07-30】：引擎模块只 `get_logger` 不配置日志；`setup_logging` 收窄为最小幂等 bootstrap（仅 CLI 独立入口与裸测试）；`redact_processor` 由宿主组装点注册，CLI 独立场景例外。）
- **AD-03 重试 tenacity 化**：LLM 429/rate 与 HTTP `retry_on_status` 重试统一用 tenacity（`stop_after_attempt(max_retries+1)` + `wait_exponential(multiplier=retry_base_delay)` + retry 谓词），耗尽包装 `LLMNodeError`/`HTTPNodeError`。测试 monkeypatch `tenacity.nap.sleep`，断言重试次数与退避序列 `1,2,4`，不真睡。行为与原文档手写循环等价（S8）。
- **AD-04 全部顶层导入**：覆盖原文档"函数内延迟导入 langchain"口径；langchain-anthropic 为正式依赖，`ImportError` 分支及其测试删除。
- **AD-05 依赖并入现有 pyproject.toml**：新增直接依赖 `PyYAML>=6.0`（lock 已 6.0.2）、`langchain-anthropic`（配套区间由 EXP-L5 定）；`httpx` 从 test group 提升为主依赖；dev group 新增 `pytest-cov`；`[tool.pytest.ini_options]` 追加 `unit`/`integration` marker（保留既有 `slow`）。
- **AD-06 版本现实**：langgraph 1.0.2 / langchain 1.0.5 / langchain-core 1.0.4 / langchain-openai 1.0.2 / pydantic 2.11 / Python 3.13，**以 uv.lock 为准**（原文档 langgraph 0.2-0.7 约束作废）。配套 R-EXP 规则（第 7 章）前置消化 1.x API 差异。
- **AD-07 ruff 门禁扩展**：ruff `select` 追加 `T20`（禁 print）、`BLE`（禁盲 except）、`S`（bandit）；`per-file-ignores`：`app/workflow/cli.py` 豁免 `T201`、`tests/**` 豁免 `S101`；既有代码若产生新告警以最小 per-file-ignores 收口并记录于 spec-00。格式化以 `ruff format`（line-length 119）为准（取代原文档 black/isort/100 口径）。
- **AD-08 测试落位**：新建 `tests/unit/workflow/` + `tests/integration/workflow/`；`tests/conftest.py` 提供 `load_dotenv`、`restore_node_registry` autouse fixture、FakeLLM/EchoNode/假 env 公共夹具。覆盖率命令：`uv run pytest --cov=app.workflow --cov-report=term-missing --cov-fail-under=80`。
- **AD-09 Makefile/CI**：Makefile 新增 `test` / `test-unit` / `test-integration` / `test-cov`；`ci.yaml` 增加 `uv run pytest -m unit`（Phase 0）与覆盖率门禁（Phase 9）。
- **AD-10 CLI 主交付、FastAPI router 可选**：`uv run python -m app.workflow run --workflow ... --input ...`；`api.py` 为可选任务（0.25 人日，挂 `app/api/v1/api.py`），若实施必须：slowapi rate limit 装饰器、DI、同步 `execute_workflow` 经 `run_in_threadpool` 包装、structlog。
- **AD-11 pyright 门禁**：`make typecheck`（standard，覆盖 app/）纳入每个 spec 的 DoD；动态模型（create_model）处不可避免的类型放宽须最小化并注释。
- **AD-12 .env.example**：增补 `ANTHROPIC_API_KEY=` 空值占位（已有 OPENAI_API_KEY / OPENAI_BASE_URL）；示例 YAML 头部注释写明所需 env。

## 10. 机器门禁清单

**lint / 类型 / 测试门禁（每个 spec 的 DoD 都引用）：**

```bash
make lint                # ruff check . 全仓零告警（含 T20/BLE/S）
make format              # ruff format .（line-length 119）
make typecheck           # pyright standard 零错误
uv run pytest -m unit                    # 单元层
uv run pytest -m integration             # 集成层（含并发压测）
uv run pytest --cov=app.workflow --cov-report=term-missing --cov-fail-under=80
```

**grep 安全闸门（Phase 9 终检必跑，各 Phase 自查）：**

```bash
# 硬编码密钥（人工逐条确认，误报记入审计记录）
grep -rniE "(api[_-]?key|token|secret|password)\s*[:=]" app/workflow/ tests/
# 领域字段名特判（C2/R2 守护，零命中）
grep -rnE "circle_|planner_|worker_|reflector_|current_node" app/workflow/
# 缓存（H4/R10 守护，零命中）
grep -rn "cache\|lru_cache" app/workflow/
# 运行时禁用 clear_execution_history 收集日志（H1 守护，registry.py 零命中）
grep -n "clear_execution_history" app/workflow/registry.py
# print（G8/R5，cli.py 豁免外零命中）
grep -rn "print(" app/workflow/ --exclude=cli.py
# yaml 安全：全引擎只允许 yaml.safe_load（S16）；以下命令用于检测被禁止的 yaml.load 调用，零命中为通过
grep -rn "yaml\.load(" app/workflow/
# dispatcher 豁免残留（C5 守护，零命中）
grep -rni "dispatcher\|triage\|subgraph" app/workflow/
```

**守护测试清单（具名回归，随对应 Phase 落地）：**

| 守护测试 | 守护对象 | 落点 |
| --- | --- | --- |
| `test_no_hardcoded_field_names` | R2/C2 | spec-02 |
| `test_exception_hierarchy` | 异常族单点 | spec-01 |
| `test_registry_restore_fixture` | D7 测试隔离 | spec-03 |
| `test_builtin_branch_count_is_two`（工厂内置分支数==2） | R4 | spec-03 |
| `test_runnable_tags` | K4 | spec-04 |
| `test_execution_log_no_secret_leak`（LLM/HTTP 各一） | H6 | spec-04/05 |
| `test_validate_no_dispatcher_exemption` | C5 | spec-06 |
| `test_router_no_print_no_full_state` | C3/H6 | spec-06 |
| `test_delete_removes_all_three_maps` | H7 | spec-07 |
| `test_no_unregister_api` | H7 | spec-07 |
| `test_concurrent_same_workflow_logs_isolated`（16×64） | H1 | spec-07 |
| `test_collector_reset_after_run` | H1/S11 | spec-07 |
| `test_logs_cover_all_executed_nodes` | H3 | spec-07 |
| `NodeType` 成员数断言（恰 2 个） | R1/C8 | spec-01 |
| 三表 key 集合不变量单测 | H7/S13 | spec-07 |

**pytest marker 纪律**：纯函数/类级、毫秒级 → `@pytest.mark.unit`；跨模块协作（YAML→图→执行）、并发压测 → `@pytest.mark.integration`；`--strict-markers`。

## 11. 变更管理

1. **什么算契约变更**：第 4 章签名、第 5 章异常族、第 6 章行为语义、第 2 章白名单、第 8 章红线、第 9 章 AD 条目的任何增删改。
2. **流程**：提出（写明动机 + 影响面）→ 评审（人类决策者拍板）→ **同步更新** CONTRACT.md + spec/README 引用处 + 受影响代码/测试 → 提交信息用 `docs:`（纯契约）或 `refactor!`（签名变更）。
3. **禁止**：先改代码后补契约；只改 CONTRACT 不同步 spec；口头变更。
4. **EXP 触发的变更**：探索实测与假设不符时，按本流程变更；备选方案须给出 2-3 个并附影响面对比（R-EXP 第 5 条）。
5. 规划文档（00-03）为历史论证，不回改；冲突以本文件 AD 条目为准。

## 12. 每 Phase 交付自检表

> 每个 Phase 交付汇报**必须照抄本表并逐项勾选**（G1-G8 + 红线自检 + R-EXP）。

**通用 DoD（继承 A.5，按仓库门禁适配）：**

| # | 条款 | 验证方式 |
| --- | --- | --- |
| G1 | 任务清单 checkbox 全勾，每项有对应提交 | `git log` 对照 |
| G2 | 全部测试绿（unit + 涉及的 integration） | `uv run pytest -m unit` / `-m integration` |
| G3 | lint / 格式化零问题 | `make lint`；`ruff format --check .` |
| G4 | 全量类型标注（R9），公开 API 有 docstring | `make typecheck` + ruff `D` 规则 |
| G5 | 接口契约与本文件第 4 章一致（签名/异常/返回结构） | 逐行核对 |
| G6 | 红线自检通过：R1/R2/R5/R6（+ 本 Phase 相关红线） | 第 10 章 grep 闸门 + 人工核对 |
| G7 | 提交符合 conventional commits | `git log --oneline` |
| G8 | 无新增 print（cli.py 用户输出例外） | `grep -rn "print(" app/workflow/ --exclude=cli.py` |

**红线自检（R1-R10 + R-EXP）：**

| 红线 | 本 Phase 是否涉及 | 通过？ | 证据 |
| --- | --- | --- | --- |
| R1 范围 | | | |
| R2 通用性 | | | |
| R3 进出契约 | | | |
| R4 注册表优先 | | | |
| R5 密钥/日志 | | | |
| R6 错误显式 | | | |
| R7 TDD | | | |
| R8 结构 | | | |
| R9 类型 | | | |
| R10 缓存 | | | |
| R-EXP 探索先行 | | | |

**交付汇报结尾必须附"偏离与疑问清单"**（无偏离写"无"）。
