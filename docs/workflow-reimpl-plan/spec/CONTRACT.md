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

**`app/workflow/`（23 个模块，多一个即违规）：**

```
app/workflow/
  __init__.py            # __version__，廉价导入，不导入重依赖
  models.py              # DSL 模型 + 精简 NodeType + 异常族（单点）
  state.py               # StateModelFactory（< 150 行）
  graph_builder.py       # GraphBuilder + BuildResult（< 300 行，只含 GraphBuilder）
  registry.py            # WorkflowRegistry + RunResult + RunLogCollector + load_definitions_from_dir + synthesize_run_input + _run_nested/_RUN_STACK（< 400 行，即 R8 上限）
  utils.py               # 仅 convert_state_to_dict / map_output_to_state（< 150 行）
  logging_conf.py        # setup_logging + redact + 脱敏 processor
  cli.py                 # ApiResponse + build_registry + build_parser + main（< 200 行）
  __main__.py            # python -m app.workflow 入口
  api.py                 # 【可选】FastAPI router（AD-10）
  auth.py                # 写端点管理员角色校验（S19）。入口层：可 import app.api/app.core（AD-02 组装点例外）
  ports.py               # ChatModelFactory 等宿主注入类型别名（S20），仅类型，不含任何 app.* 依赖
  security.py            # SSRF 校验 validate_http_url（spec-20）。入口层：读 app.core.config（AD-02 组装点例外）
  store.py               # YAML 落盘读写 + 原子替换（S17），只依赖 stdlib + yaml
  sandbox.py             # validate_code_ast + SandboxLimits + run_sandboxed（S22，< 400 行）
  sandbox_worker.py      # 子进程入口；**非 importable API**，只被 sandbox.py 以 sys.executable -I 拉起（S22，< 400 行）
  nodes/
    __init__.py          # 导出 BaseNode/register_node_type/create_node/LLMNode/HTTPNode（**不含插件节点类**）
    base.py              # BaseNode + RunLogCollectorLike + _RUN_COLLECTOR（< 200 行）
    factory.py           # _NODE_REGISTRY + register/list/create（< 120 行）
    llm_node.py          # LLMNode + LLMConfig（< 250 行）
    http_node.py         # HTTPNode + HTTPNodeConfig（< 280 行）
    python_node.py       # PythonNode 插件（K5 范例，R4 守护不变：factory 无内置分支）
    subworkflow_node.py  # SubWorkflowNode 插件（S23/S24，K5 路径，< 250 行）。
                         # 插件节点类**不由 nodes/__init__.py 导出**（PythonNode 同此）：
                         # 调用方经 register_node_type/create_node 取得，导出会诱导绕过注册表直接 import。
                         # 自注册由 factory.py 底部 import 触发（AD-04 同款）
  config/examples/
    minimal.yaml         # Phase 1
    http_demo.yaml       # Phase 9
    condition_branch.yaml # Phase 6
```

> **计数说明（2026-09-14）**：原表记 15 个模块，但 `auth.py` / `ports.py` / `security.py` / `store.py` / `nodes/python_node.py`
> 早已随 spec-17..20 与 K5 插件范例落地却未回写白名单（文档漂移）。本次按第 11 章流程一并补录，
> 并新增 S22 所需的 `sandbox.py` / `sandbox_worker.py`。补录不改变任何代码，只使白名单与实际仓库一致。
> 同日第二次变更（S23/S24）新增 `nodes/subworkflow_node.py`，计数 22 → 23；并把 `registry.py` 的行数注记
> 由 `< 350` 放宽到 `< 400`（= R8 红线上限）。**理由**：350 是该模块更小时的估值，S21 已加入
> `synthesize_run_input`，S23/S24 再加入 `_RUN_STACK` 与 `_run_nested`（含守护与日志合并），实测 397 行。
> 拆分（把 `RunResult`/`RunLogCollector`/`synthesize_run_input` 移出）会同时改动 §4.10 的冻结归属与
> 全仓 import，代价远高于放宽注记，且模块职责仍单一（注册表运行时），故不拆。**注：`api.py` 现为 497 行，
> 已超 R8 的 400 行红线（本期之前即如此，非本次引入），列为待办拆分项。**

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
  > **解禁令注解（2026-09-14，S23/S24）**：本条的「子图嵌套」指 **langgraph 原生 subgraph 编译**，
  > 它**仍然禁止**；`langgraph.types.Send` 并行扇出**依然不做**（并行嵌套的日志合并与深度计数需另立契约）。
  > 本期新增的 `subworkflow` 节点是 **registry 注入式**嵌套：节点经不透明 `WorkflowRunner` callable
  > 回调 `WorkflowRegistry.execute_workflow`，编译产物仍是**扁平 `StateGraph`**，不使用 langgraph 的
  > subgraph 能力。二者机制不同——禁止前者不等于禁止后者。
  > **为什么必须写清**：不解注则实现者要么以为自己在违规（不敢写），要么顺手用 langgraph subgraph
  > （真的违规，且把 H5 构建期快照问题带回来）。
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
4. 引擎自包含：`app/workflow/` 任何模块**不得 import `app.core.*` / `app.api.*` / `app.services.*`**（AD-02；反向集成时由外部装配）。**唯一例外是入口层 `api.py`**（及为其服务的 `auth.py` / `security.py`），它可 import `app.core.limiter` / `app.core.config` / `app.api.v1.auth` / `app.services.llm.provider_service`（S20 注册期校验 `provider_ref`）；引擎内核模块（`models` / `nodes/*` / `graph_builder` / `registry` / `store` / `ports` / `utils` / `cli`）**一律不得**跨线。宿主经构造参数注入的**不透明 callable**（如 `ChatModelFactory`）不构成依赖——引擎只持有类型别名 `app/workflow/ports.py`，不感知其实现，装配责任在组合根（`app/main.py`）。**第二类不透明 callable（2026-09-14，S23/S24）**：`WorkflowRunner`，同样定义于 `ports.py`。它与 `ChatModelFactory` 的关键差异是**实现方**——`ChatModelFactory` 由宿主装配（闭包 provider 服务），`WorkflowRunner` 由**引擎自身**提供（`WorkflowRegistry._run_nested` 的 bound method 自注入），故**组合根 `main.py` / `cli.py` 零改动**。放 `ports.py` 的理由与前者相同：红线 2 禁止 `nodes/*` import `registry`/`graph_builder`，节点要回调注册表就只能持有一个类型别名标注的 callable。

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
    chat_model_factory: ChatModelFactory | None = None,
    workflow_runner: WorkflowRunner | None = None,
) -> BaseNode:
    """插件注册表优先；内置兜底恰好 2 个分支：("llm","LLM")→LLMNode、("http","HTTP")→HTTPNode；
    未知类型 ValueError 列出 list_node_types() 并提示 register_node_type()；
    无 workflow_registry 参数（H5）。chat_model_factory 仅透传给 llm 分支（S20），
    是不透明 callable 而非注册表，故不违反 H5。

    workflow_runner（S23/S24，2026-09-14 新增）同为不透明 callable，故亦不违反 H5。
    **签名探测规则（冻结）**：插件分支以 inspect.signature(node_class.__init__) 探测形参，
    仅当声明了 workflow_runner 形参、或声明了 VAR_KEYWORD（**kwargs）时才传该 kwarg；
    否则按原 4-kwarg 形态调用。目的：不给 BaseNode 冻结签名（§4.4）加参，
    且既有插件（PythonNode 等）与第三方插件零改动。内置分支恒 2 个（R4 守护不破）。"""
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
    provider_ref: str | None = None    # "<provider_name>/<model_name>"；非空时走注入工厂解析（S20）
    # 不设明文 api_key 字段（H6/ADR-008）。provider_ref 只存**引用**，
    # 凭据由宿主注入的 ChatModelFactory 在调用期从 provider 表解析，config/YAML 永不落密钥。

class LLMNode(BaseNode):
    def __init__(
        self,
        name: str,
        llm_config: LLMConfig | dict[str, Any],
        messages: list[BaseMessage] | None = None,
        operator_log: OperatorLog | None = None,
        chat_model_factory: ChatModelFactory | None = None,
    ) -> None: ...
    def validate_config(self) -> bool: ...
    def _resolve_api_key(self) -> str: ...        # 缺失 → ConfigError，消息含 env 名不含密钥值
    def _get_llm_instance(self) -> Any: ...       # 懒加载（K10）；三分支见 S20
    def _invoke_with_retry(self, llm: Any, messages: list[Any]) -> Any: ...
    def build_runnable(self) -> Runnable: ...
# 模块底部自注册：register_node_type("llm", LLMNode)
```

`ChatModelFactory` 定义于 `app/workflow/ports.py`（引擎内部端口，零 `app.*` 依赖）：

```python
ChatModelFactory = Callable[[str, dict[str, Any]], Any]  # (provider_ref, overrides) -> chat client
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
        chat_model_factory: ChatModelFactory | None = None,
        workflow_runner: WorkflowRunner | None = None,
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

七步顺序（K6，代码注释逐步标注 1..7）：`_validate_definition` → `create_state_model` → `StateGraph(state)` → `_add_nodes` → `_add_edges` → `set_entry_point` → `compile()`。构造器**无 registry 参数**（H5 签名形态防线）。`_add_nodes` 把两个注入的不透明 callable（`chat_model_factory` S20、`workflow_runner` S23/S24）一并透传给 `create_node`——正因它们是 callable 而非注册表，H5 的签名形态防线才得以保住。

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
        chat_model_factory: ChatModelFactory | None = None,
        max_nesting_depth: int = 3,
    ) -> None:
        """max_nesting_depth（S23，2026-09-14 新增）= 运行栈中允许同时存在的工作流数量上限（含最外层）。
        默认 3 ⇒ A→B→C 可运行（栈深 3），C 再引用 D 被拒。**registry 级配置，不做 per-node 覆盖**
        （per-node 会让「全局最深」不可推断，守护形同虚设）。
        registry 以 **bound method self._run_nested** 作为 workflow_runner 构造 GraphBuilder
        → **自注入，组合根 main.py / cli.py 零改动**（§3 红线 4 第二类不透明 callable）。"""
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
    # 嵌套执行（S23/S24，2026-09-14 新增）。**私有方法**，作为 WorkflowRunner 注入给 subworkflow 节点；
    # execute_workflow 自身签名不变（冻结面），变化只在其内部（运行栈 set/reset）与本方法。
    def _run_nested(self, workflow_id: str, input_data: dict[str, Any], caller_label: str) -> dict[str, Any]:
        """栈检查（S23）→ 递归 self.execute_workflow（自动获得 per-id RLock、独立 collector、S21 输入合成）
        → 内层日志按 "{caller_label}/" 前缀并入外层 collector（S24）→ 返回 §4.15 冻结的三键结果信封
        {"output": RunResult.output, "run_id": ..., "inner_log_count": ...}。
        环或深度超限 → NestedWorkflowError（消息含完整栈）；被引用 id 未注册 → WorkflowNotFoundError。
        检查在递归**之前**（调用前拒绝），绝不依赖 RecursionError 兜底——它不属 WorkflowEngineError 家族，
        会穿透 CLI 的异常分类。"""
    # 查询接口
    def get_workflow_definition(self, workflow_id: str) -> WorkflowDefinition | None: ...
    def get_operator_logs(self, workflow_id: str) -> dict[str, OperatorLog]: ...
    def get_operator_log_by_node(self, workflow_id: str, node_name: str) -> OperatorLog | None: ...
    def get_execution_history(self, workflow_id: str) -> list[ExecutionLog]: ...
    def get_node_execution_history(self, workflow_id: str, node_name: str) -> list[ExecutionLog]: ...
    def get_node_by_name(self, workflow_id: str, node_name: str) -> BaseNode | None: ...
    def get_registry_stats(self) -> dict[str, Any]: ...

# 运行栈（S23，2026-09-14 新增）。模块级 ContextVar，与 nodes/base.py 的 _RUN_COLLECTOR 同款纪律：
# execute_workflow 进入时 set(_RUN_STACK.get() + (workflow_id,))，finally 中 reset(token)（S11 配对），
# 故 ContextVar 永不泄漏、协程/线程间互不串栈。
_RUN_STACK: ContextVar[tuple[str, ...]]

def load_definitions_from_dir(directory: str | Path) -> list[WorkflowDefinition]: ...

# S21（2026-09-11 新增）：运行输入合成。纯函数，返回新 dict，不 mutate 入参。
def synthesize_run_input(definition: WorkflowDefinition, input_data: dict[str, Any]) -> dict[str, Any]: ...
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

**修订说明【画布编排契约扩展，2026-09-07】**（`ApiResponse` 签名不变，`metadata` 语义扩展）：

- `metadata` 基线四键不变：`{workflow_id, run_id, duration_ms, node_count}`（与 `api.py` 现状一致）。
- 新增**可选第五键** `execution_logs: list[dict]`：仅 `POST /api/v1/workflows/{workflow_id}/execute` **成功响应**携带，序列化自 `RunResult.execution_logs`（每条 `ExecutionLog.model_dump(mode="json")`），egress 前经 `logging_conf.redact` 二次脱敏（H6/S15）。
- 404 / 500 失败信封 `data=null`，`metadata` 不出现（向后兼容：老客户端忽略新键即可）。
- 投影位置：改动集中在 `api.py` egress（`execute_workflow` 成功分支 `metadata` 构造追加 `execution_logs`）；引擎内核零改动。
- 详细目标形态与约束见 `docs/workflow-api-and-trace.md` §7.1（方案 A）。

### 4.13 `app/workflow/api.py` — 画布编排管理端点（AD-10，2026-09-07 新增）

> 以下签名属 **api.py 层**（入口集成，AD-10 composition-root exception），非引擎内核。
> 全部端点沿用宿主统一信封 `{code, message, data}`、`get_registry(request)` DI、structlog、slowapi 限流；
> 写端点须鉴权（S19）。详细请求/响应形态见 `docs/workflow-frontend-spec.md` §4.2。

```python
# --- 投影辅助（api.py 内部，与既有 _project_to_host_envelope 风格一致） ---

def _workflow_summary(registry: WorkflowRegistry, wf_id: str) -> dict[str, Any]:
    """单条列表投影：{workflow_id, node_count, entry_point, description?}。"""

def _definition_to_json(definition: WorkflowDefinition) -> dict[str, Any]:
    """查定义投影：WorkflowDefinition → JSON dict（剔除 execution_history 运行期字段，保留 ui_layout 注解）。"""

def _definition_to_yaml_text(definition: WorkflowDefinition) -> str:
    """查定义投影：WorkflowDefinition → yaml_text 字符串（yaml.safe_dump，供只读预览）。"""

# --- 端点签名（FastAPI router 层） ---

@router.get("/workflows")
async def list_workflows(request: Request) -> JSONResponse:
    """列表：读 registry.list_workflows() + get_registry_stats()，不引入新存储（H4）。
    成功 data: list[dict]，每条 = _workflow_summary(registry, wf_id)。"""

@router.get("/workflows/{workflow_id}")
async def get_workflow(request: Request, workflow_id: str, format: Literal["json", "yaml"] = "json") -> JSONResponse:
    """查定义：format=json → _definition_to_json(definition)；format=yaml → {"yaml_text": ...}。
    不存在 → 404（WorkflowNotFoundError → 宿主信封）。"""

@router.put("/workflows/{workflow_id}")
async def save_workflow(request: Request, workflow_id: str, body: dict[str, Any]) -> JSONResponse:
    """全量保存注册（S13 原子替换）：
    body → parse_definition(body)（WorkflowDefinition.model_validate）
         → 节点类型白名单校验（S18：type ∈ {llm, http, python}；python 须 code-only + AST 通过，
           服务端强制 sandboxed=true；违规 → 422）
         → register_workflow(definition, default_edges=body.get("default_edges"))
         → yaml.safe_dump 落盘 user 目录（S17：文件名白名单 ^[A-Za-z0-9_-]{1,64}$）
         → 成功 data: {"yaml_text": ..., "workflow_id": ...}。
    构建期校验失败 → 422（message 携脱敏原因，H6）；未授权 → 403（S19）。"""

@router.delete("/workflows/{workflow_id}")
async def delete_workflow(request: Request, workflow_id: str) -> JSONResponse:
    """删除：delete_workflow(workflow_id)（唯一删除入口，C6/H7：四表同步）
         → 同步删 YAML 文件（S17）。
    不存在 → 404；未授权 → 403（S19）。
    成功 data: null。"""
```

**约束**：

- **引擎内核零改动**：`registry.py` / `nodes/*` / `graph_builder.py` / `models.py` 不修改（AD-02 引擎自包含红线不变）。
  *范围限定*：本条只约束画布编排这一特性自身；后续 S22（2026-09-14）按其契约行另行修改 `models.py`（异常族规范化）
  与 `nodes/python_node.py`（沙箱分支），不视为违反本条。
- **限流 / DI / 信封**：沿用现状 execute 端点范式（slowapi、`get_registry`、宿主统一信封，AD-10）。
- **写端点鉴权**（S19）：`PUT` / `DELETE` 须 `Depends(get_current_user)` + 管理员角色校验；现状 execute 端点无鉴权，需后端补齐策略。
- **节点类型白名单**（S18，2026-09-14 修订）：`PUT` 服务端二次校验 `node.type ∈ {llm, http, python}`；`python` 须满足
  code-only + `validate_code_ast` 通过 + 服务端强制 `sandboxed=true`（客户端值被忽略并覆写），其余一律拒绝（详见 S18/S22）。
- **YAML 落盘**（S17）：用户定义目录 `app/workflow/config/user/`（与只读 `examples/` 分离）；`PUT` 全量覆盖写 + 原子替换；文件名白名单校验 `^[A-Za-z0-9_-]{1,64}$`（防路径穿越）；`build_registry` 启动扫描 `examples/` + `user/`（S16 fail-fast）。
- **前端契约期望**：`docs/workflow-frontend-spec.md` §4.2 声明的端点路径、请求/响应形态、错误码须与本契约一致。

### 4.14 `app/workflow/sandbox.py` — Python 节点沙箱（S22，2026-09-14 新增）

> 引擎内核模块（只依赖 stdlib + `app.workflow.models`），供 `PythonNode` 与 `api.py` 注册期校验共用。
> `sandbox_worker.py` 不在此冻结签名——它是**子进程入口**而非 importable API，其线上协议冻结在 S22。

```python
def validate_code_ast(code: str) -> None:
    """静态拒绝不可沙箱化的代码。通过返回 None，否则抛 WorkflowValidationError。

    规则（命中即拒；消息含**行号 + 规则名**，绝不含代码正文，H6）：
      1. import / from ... import（ast.Import / ast.ImportFrom）——全禁，无白名单模块
      2. 危险调用名（ast.Call.func 为 ast.Name 且 id ∈ _FORBIDDEN_CALLS）：
         exec / eval / compile / open / __import__ / globals / locals / vars / dir /
         getattr / setattr / delattr / type / input / breakpoint / exit / quit / help
      3. dunder 标识符：任何 ast.Name.id 或 ast.Attribute.attr 含 "__"
         （封死 ().__class__.__bases__.__subclasses__() 这类内省逃逸链）
      4. 体积 > _MAX_CODE_BYTES（64 KiB）
    """

@dataclass(frozen=True)
class SandboxLimits:
    """沙箱资源上限（由 worker 逐项自我施加，失败项经 applied/refused 回报，见 S22 细则）。"""

    timeout_s: float = 10.0
    max_memory_mb: int = 256
    max_output_bytes: int = 1_000_000

def run_sandboxed(code: str, state: dict[str, Any], limits: SandboxLimits = SandboxLimits()) -> dict[str, Any]:
    """在子进程沙箱中执行 code，返回其 dict 结果。

    执行前**复跑** validate_code_ast（纵深防御：调用方可能绕过注册期校验）。
    超时 / 非零退出 / worker 报 ok=false / 输出非 dict → 一律抛 PythonNodeError。
    """
```

**约束**：

- **默认参数在定义处求值一次**（`SandboxLimits()` 不可变 dataclass），不得写成 `limits=None` 再在函数内构造，以保证签名与行为一致。
- **`sandbox.py` 不得 import `app.core.*` / `app.api.*` / `app.services.*`**（引擎自包含红线，AD-02）；异常只用 §5 冻结族。
- **`sandbox_worker.py` 不进入任何模块的 import 图**：只被 `run_sandboxed` 以 `[sys.executable, "-I", <绝对路径>]` 拉起；
  协议为 stdin/stdout 各恰好一个 JSON 文档（详见 S22）。
- **R8**：两模块各 < 400 行，函数 < 50 行；AST 规则表与 `SAFE_BUILTINS` 为模块级常量，便于守护测试直接断言。

### 4.15 `app/workflow/nodes/subworkflow_node.py` — 子工作流节点（S23/S24，2026-09-14 新增）

```python
# app/workflow/ports.py 新增（引擎内部端口，零 app.* 依赖）
WorkflowRunner = Callable[[str, dict[str, Any], str], dict[str, Any]]
"""(workflow_id, input_data, caller_label) -> 内层运行结果信封。

第三参 caller_label 是发起调用的节点名，供被调方做日志前缀（S24）。

**返回信封（冻结，恰三键）**：
    {"output": dict[str, Any], "run_id": str, "inner_log_count": int}
`output` 是内层 `RunResult.output`（内层业务数据）；另两键供调用方构造 S24 摘要——
它们只存在于内层 `RunResult` 上，节点自己无从得知，故必须由 runner 一并回传。
节点**只把 `output` 交给 `map_output_to_state`**，信封的 meta 键不得写入外层 state。

引擎不感知其实现：生产为 WorkflowRegistry._run_nested 的 bound method，测试为 FakeRunner。
与 ChatModelFactory 的差异见 §3 红线 4——本 callable 由**引擎自身**提供，组合根零改动。
"""

class SubWorkflowNodeConfig(BaseModel, extra="forbid"):
    """引用另一个已注册工作流（S14 forbid extras）。"""

    workflow_id: str                   # 非空；被引用工作流的存在性是**运行期**检查（注册期只校验结构，S18）
    input_map: dict[str, str] = {}     # {内层 state 键: 外层 state 点路径}，路径语法同 S7
    inherit_input: bool = False        # 为真则先把外层 state 全量传给内层，再叠加 input_map 结果

class SubWorkflowNode(BaseNode):
    """K5 插件类型（模块底部 register_node_type("subworkflow", SubWorkflowNode)，factory 无内置分支）。"""

    def __init__(
        self,
        name: str,
        config: dict[str, Any] | SubWorkflowNodeConfig,
        node_type: str = "subworkflow",
        operator_log: OperatorLog | None = None,
        workflow_runner: WorkflowRunner | None = None,
    ) -> None: ...
    def validate_config(self) -> bool: ...
    def build_runnable(self) -> Runnable: ...
```

**执行语义（R3 管线不变）**：`convert_state_to_dict` 进 → 组装内层输入（`inherit_input` 为真则先全量拷贝外层
state，再叠加 `input_map` 解析结果，**后者优先**）→ `workflow_runner(workflow_id, inner_input, self.name)`
得到结果信封 → **只取 `envelope["output"]`** 交 `map_output_to_state`（信封 meta 键不入外层 state）；
节点自身 `ExecutionLog.output_data` 记 S24 摘要（`output_keys` 取自 `output`，`run_id`/`inner_log_count`
取自信封，`duration_ms` 由节点自行计时）。

**`dual_write=False`（冻结，本节点是 R3 出口的唯一例外）**：调用
`map_output_to_state(self.name, envelope["output"], state_dict, dual_write=False)`，即内层数据**只落在
`{node_name}_result`**，**不**平铺进外层同名 channel。

理由：runner 回传的是内层的**整个最终 state**（含 `input`、各 `{node}_result`、`history` 等），
不是「一个节点的输出」。按默认 `dual_write=True` 平铺会让内层 channel **静默覆写**外层同名 channel——
最典型的是 `input`：**每个**工作流都声明 `input`，内层的 `input`（可能是声明默认值）会盖掉外层真正的
`input`，且外层后续节点全部读到错值而无任何报错。`history` 同理会被内层历史覆盖。
`dual_write=False` 下数据**不丢失**：外层经 `sub_1_result.<path>`（S7 点路径）读取，条件边与
`{...}` 模板均可引用；这与其它节点的 `{node}_result` 约定一致，只是少了平铺那一半。
`history_increment` 保持默认 `True`（只追加一条 `"{node}: ..."` 增量，不覆盖）。

**约束**：

- `workflow_runner` 为 `None` → **`ConfigError`**，**在 `__init__` 抛出**（时机冻结）。理由：S6「构建期校验优先」——
  构造期抛出使它落在 `register_workflow` → `api.py` 的 `except (ValueError, WorkflowEngineError)` → **422
  build-time error**；若推迟到执行期则同样装配错误只能表现为运行期 **500**，排查成本高一个量级。
  处置口径与 S20 分支②一致：**不静默降级、不返回空 dict**——静默降级会让「忘了注入 runner」表现为
  「子工作流什么都没做」，比直接失败难查得多。
- `input_map` 的外层路径**不存在时跳过该键**（不写入内层输入，让内层按 S14 走声明默认值），
  而非抛 `KeyError`——与 `{input}` 占位符渲染的既有容错口径一致。须有测试固定，否则易被写成报错。
- **不得 import `registry` / `graph_builder`**（§3 红线 2）；只 import `ports` 的类型别名。
- 节点自身 `ExecutionLog.output_data` **只记摘要**（S24），不内嵌内层完整输出与日志。
- **R8**：< 250 行，函数 < 50 行。
- 守护不变：`NodeType` 成员数恒 2（C8），`create_node` 内置分支恒 2（R4）。

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
class PythonNodeError(WorkflowEngineError):
    """python 节点执行失败（inline code 编译/返回契约、entry 解析，以及 S22 沙箱超时/非零退出/输出非 dict）。"""
class WorkflowValidationError(WorkflowEngineError):
    """注册期定义校验失败 → HTTP 422（S6 构建期优先）。承载 SSRF（spec-20）与沙箱 AST 预检（S22）两类原因。"""
class NestedWorkflowError(WorkflowEngineError):
    """嵌套执行守护触发（S23）：引用环（含自引用）或超过 max_nesting_depth。消息含完整运行栈。"""
```

**规范化说明（2026-09-14）**：`WorkflowValidationError` 原定义于 `app/workflow/security.py`（spec-20 引入），
违反本章「单点定义于 `models.py`」规则。本次**迁入 `models.py`** 并改挂 `WorkflowEngineError` 基类，
`security.py` / `api.py` / 相关测试改为从 `models.py` 导入（4 处 import 调整）。

迁移动机：S22 的 `sandbox.py` 需要抛同一类「注册期校验失败 → 422」异常，若沿用现状就会出现第二个模块
从 SSRF 专用模块 import 通用校验异常的怪依赖，把既有漂移扩大成结构性问题。

**可观测影响（已逐处核对）**：HTTP 状态码全部不变——`PUT` 的注册期校验仍由 `api.py` 的
`except WorkflowValidationError` 命中 → 422；`execute` 端点的 catch-all 仍 → 500。唯一差异是 CLI：执行期
SSRF 拦截（`http_node.py` 二次校验）原落入 `except Exception` 分支打印 `unexpected error while running ...`，
改挂基类后落入 `except WorkflowEngineError` 分支打印 `workflow engine error for ...`，**退出码同为 1**，
无测试断言该字符串。

**场景映射（pytest.raises 按此断言）：**

| 场景 | 异常类型 | 落实 spec |
| --- | --- | --- |
| 缺必需 env 密钥 | `ConfigError` | spec-04 |
| LLM 调用失败 / 重试耗尽 | `LLMNodeError` | spec-04 |
| HTTP 调用失败 / 重试耗尽或不可重试 / mock 启用但未命中 | `HTTPNodeError` | spec-05 |
| 条件路由全部未命中（`no_match_policy="raise"`） | `ConditionNotMatchedError` | spec-06 |
| 未知 `workflow_id` | `WorkflowNotFoundError` | spec-07 |
| 定义解析 / 字段校验失败 | `ValueError` / pydantic `ValidationError` | spec-01 |
| python 节点：输出非 dict / 缺 return / entry 格式错 / import 失败 | `PythonNodeError` | `docs/workflow-node-development.md` §5 |
| python 节点（S22 沙箱）：超时 / 非零退出 / worker 报 `ok=false` / AST 执行期复检失败 | `PythonNodeError` | spec-22 |
| HTTP 节点 URL 落入私网/环回/元数据段（SSRF） | `WorkflowValidationError` → 422 | spec-20 |
| python 节点代码 AST 预检拒绝（import / dunder / 危险调用 / 超长） | `WorkflowValidationError` → 422 | spec-22 |
| `subworkflow` 节点引用环（含自引用 A→A） | `NestedWorkflowError`（消息含完整栈，如 `a -> b -> a`） | S23 |
| 嵌套深度超过 `max_nesting_depth` | `NestedWorkflowError`（消息含栈与上限值） | S23 |
| `subworkflow` 节点未注入 `workflow_runner` | `ConfigError`（**不静默降级**，同 S20 分支②） | §4.15 |
| 被引用的 `workflow_id` 未注册 | `WorkflowNotFoundError`（**沿用既有异常，不新增**） | §4.10 |

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
| S17 | YAML 落盘持久化（2026-09-07 新增） | 用户定义目录 `app/workflow/config/user/`（与只读 `examples/` 分离）；`PUT` 全量覆盖写 + 原子替换（对齐 S13 原子替换语义）；文件名 `workflow_id` 白名单校验 `^[A-Za-z0-9_-]{1,64}$`（防路径穿越）；`build_registry` 启动扫描 `examples/` + `user/`（S16 fail-fast 不变）；`DELETE` 同步删除对应 YAML 文件。落盘失败 → 500（message 携脱敏原因，H6） |
| S18 | 节点类型 API 白名单（2026-09-07 新增，**2026-09-14 两次修订**） | `PUT /api/v1/workflows/{id}` 服务端二次校验每个 `node.type ∈ {llm, http, python, subworkflow}`（`llm`/`http` 为 `NodeType` 枚举内置集 C8；`python`/`subworkflow` 为 K5 插件类型，见 §8 R1 carve-out）；未知类型 → HTTP 422。**第一次修订要点**：`python` 由「一律拒绝（S15 非沙箱 RCE）」改为**有条件接受**，三条注册期条件全部满足方可通过：① **只允许 `code` 模式**——`config` 含 `entry` 即拒（`entry` 可 `importlib` 加载任意仓库模块，无法沙箱化）；② **AST 预检通过**——`validate_code_ast(config["code"])`（§4.14），失败 → 422（S6 构建期优先），`message` 携**行号 + 规则名**、不携代码正文（H6）；③ **服务端强制 `sandboxed=true`**——忽略并覆写客户端传入值，落盘 YAML 与注册进 registry 的定义中该字段恒为 `true`（`sandboxed` 是**安全属性**而非用户偏好，交给客户端等于把 RCE 开关暴露给请求方）。另：`code` 缺失/为空/纯空白 → 422（设计器默认 config 即空 code，前端预校验**不是**边界）。**第二次修订要点**：白名单加 `subworkflow`，注册期**仅结构校验**——`config["workflow_id"]` 须为非空 `str`，否则 422；**被引用工作流的存在性为运行期检查**（未注册 → `WorkflowNotFoundError`）。理由：`PUT wf_outer` 可能先于 `PUT wf_inner` 到达（前端逐个保存、脚本批量导入、或 inner 被删而 outer 仍在盘上），注册期存在性校验会强制拓扑序保存，把「顺序」变成隐性契约，比运行期报错更难排查。`ALLOWED_NODE_TYPES` 仍为 `frozenset`；无新端点，限流键复用 `workflows_save`。前端画布 palette 增列 `python`/`subworkflow`（体验层），但**安全边界始终在后端** |
| S19 | 写端点鉴权（2026-09-07 新增） | `PUT` / `DELETE` 写端点须 `Depends(get_current_user)` + **管理员角色**校验；未授权 → HTTP 403。现状 `execute` 端点无鉴权（仅 slowapi 限流），本期不改动；写端点鉴权策略由后端联动任务补齐（前端按角色禁用写按钮，`docs/workflow-frontend-spec.md` §5.4） |
| S20 | LLM 凭据解析（2026-09-11 新增） | `LLMConfig.provider_ref` 非空时，客户端由宿主注入的 `ChatModelFactory` 构建：`factory(provider_ref, overrides)`，`overrides` 携节点级 `temperature`（及 `max_tokens`，若设置），**节点配置优先于** `ModelConfig.extra_params`。凭据（api_key/base_url/model_id）全部来自 provider 表，**config 与 YAML 永不落密钥**（H6）。`_get_llm_instance()` 三分支：① `provider_ref` + 工厂 → 工厂路径；② `provider_ref` 但工厂为 `None` → **`ConfigError`**（消息含节点名与 ref，**不静默回退 env**——用错端点/密钥比直接失败更危险）；③ `provider_ref` 为空 → 现有 env 路径（`llm_type` 分支）逐字不变（向后兼容既有 YAML）。K10 memoize 不变：客户端按节点实例缓存，provider 换密钥需重新注册工作流方生效。`provider_ref` 存在性/enabled 校验在**注册期**完成（S6 构建期优先）：`PUT` 对每个携 `provider_ref` 的 llm 节点校验，失败 → HTTP 422 |
| S21 | 运行输入合成（2026-09-11 新增） | `execute_workflow` 在 `graph.invoke` 前调用纯函数 `synthesize_run_input(definition, input_data)`（§4.10），规则按序：① `input_data["messages"]` 真值 → 原样透传（显式消息优先，向后兼容）；② 否则 `input_data["input"]` 为非空 `str` → **附加式**注入 `messages=[{"role":"user","content":<input>}]`（不删改任何用户键，`input` 键仍写入 state 供 `{input}` 占位符使用）；③ `input` 缺失/非 `str`/空白 → 不合成（缺失 channel 按 S14 走声明默认值，LLMNode 空 messages 仍按现状 `ValueError`）；④ 对**所有**工作流生效，不按节点类型特判（R2）；无 llm 节点时多余 `messages` 键被 langgraph 静默丢弃（S14）。wire 双形态兼容：`{"input":"hi"}` 与 `{"input":{"input":"hi",...}}` 均命中合成（`api.py` 解包逻辑不变）。本语义约束**运行入口的输入预处理**，不改变 `state.py`「状态模型构建不做字段名特判」原则 |
| S22 | 沙箱执行（2026-09-14 新增） | `sandboxed=true` 的 python 节点，其代码**必须**在子进程中执行（`sandbox.run_sandboxed`，§4.14），**禁止任何形式的进程内 `exec`**；`sandboxed=false` 的既有受信仓库路径逐字不变。执行前复跑 `validate_code_ast`（纵深防御：调用方可能绕过 S18 注册期校验）。失败一律包 `PythonNodeError`（§5）。进程/协议/资源/日志细则见下方「S22 细则」表，均为**冻结约定**，实现不得自行放宽 |
| S23 | 嵌套执行守护（2026-09-14 新增） | `subworkflow` 节点经注入的 `WorkflowRunner`（§4.15）回调 `WorkflowRegistry._run_nested`，后者递归调用 `execute_workflow`。递归**之前**必须过两道守护：① **环检测**——目标 id 已在运行栈中 → `NestedWorkflowError`（含完整栈）；② **深度上限**——`len(栈) >= max_nesting_depth` → `NestedWorkflowError`。绝不依赖 `RecursionError` 兜底（不属 `WorkflowEngineError` 家族，会穿透 CLI 异常分类）。细则见下方「S23 细则」表，均为**冻结约定** |
| S24 | 内层日志合并（2026-09-14 新增） | 内层运行持**独立** `RunLogCollector`（S11 既有行为，零改动）；内层返回后，其 `execution_logs` 逐条以 `"{caller_label}/"` 前缀改写 `node_name` 后并入**外层** collector，使外层轨迹完整可读（形如 `sub_1/classify`）。`subworkflow` 节点自身的 `output_data` **只记摘要**，不内嵌内层完整输出与日志（否则同一份数据在轨迹里出现两次）。细则见下方「S24 细则」表，均为**冻结约定** |

**S22 细则（冻结）：**

| 维度 | 约定 |
| --- | --- |
| 进程隔离 | `subprocess.run([sys.executable, "-I", <sandbox_worker.py 绝对路径>], ...)`。`-I`（isolated）忽略 `PYTHON*` 环境变量与 user site-packages，防止宿主路径污染沙箱 |
| 通信协议 | stdin/stdout 各**恰好一个** JSON 文档。入：`{"code": str, "state": dict, "limits": {"timeout_s": float, "max_memory_mb": int, "max_output_bytes": int}}`；出：`{"ok": true, "output": dict, "limits": {"applied": [str], "refused": [str]}}` 或 `{"ok": false, "error": str, "limits": {"applied": [...], "refused": [...]}}`。`limits` 入参由 worker 用于**自我施加** rlimit（见「资源上限」行）；`limits` 出参是施加结果报告，`applied`/`refused` 元素形如 `"RLIMIT_CPU"` / `"RLIMIT_AS:ValueError"`。worker 内**替换 `sys.stdout`** 为丢弃器，用户代码的 `print` 不得污染协议流；结果经 `os.write(1, ...)` 单次写出 |
| 退出码 | `0` = 正常（含 `ok=false` 的业务失败）；`2` = SyntaxError；`3` = 运行期异常。非零退出 → 父进程包 `PythonNodeError` |
| 超时 | `subprocess.run(timeout=limits.timeout_s)`，到期由 stdlib kill 子进程 → `TimeoutExpired` → `PythonNodeError`。**禁止无限等待** |
| 资源上限 | rlimit 由 **worker 自我施加**：`sandbox_worker.py` 在解释器启动完成、`exec` 用户代码**之前**，按 stdin `limits` 依次 `resource.setrlimit`：`RLIMIT_AS`（= `max_memory_mb`）、`RLIMIT_CPU`（= `ceil(timeout_s)`）、`RLIMIT_FSIZE = 0`（禁写任何文件）、`RLIMIT_NPROC = 0`（禁 fork 子进程）。**逐项独立容错**：单项 `setrlimit` 抛异常 → 记入 `refused`（含异常类型名），继续施加其余项，**不阻断执行**。父进程**不得使用 `preexec_fn`**；`refused` 非空 → `logger.warning("sandbox_rlimit_partial", refused=..., platform=sys.platform)`。生产为 Linux 容器，四项均可施加。<br>*实测记录（2026-09-14，darwin 25.3.0 / arm64 / CPython 3.13）*：`RLIMIT_AS` 当前 `(INT64_MAX, INT64_MAX)`，`setrlimit` **抛 `ValueError: current limit exceeds maximum limit`**（并非「被忽略」）；`RLIMIT_CPU` / `RLIMIT_FSIZE` 可施加；`RLIMIT_NPROC` 当前 `(2666, 4000)`，降至 `(0,0)` 可施加。原设计把施加放进父进程 `preexec_fn`，而 `preexec_fn` 内抛异常会使 `subprocess.run` 整体失败（`SubprocessError: Exception occurred in preexec_fn`）——后果是 macOS 上**每一次**沙箱调用都失败，而不只是内存上限失效；另 `preexec_fn` 在多线程宿主中官方标注为不安全（FastAPI 即多线程）。故按 §7.5 改为 worker 自我施加，并顺带获得逐项可观测性（`applied`/`refused` 替代父进程盲猜的 warning） |
| 环境变量 | 子进程 `env={"PATH": "/usr/bin:/bin"}`。**不继承宿主环境**——API 密钥、DB DSN 一律不进沙箱（H6/R5） |
| 内建白名单 | worker 以 `SAFE_BUILTINS` 作为 `__builtins__`：仅纯计算类（`len/range/sorted/min/max/sum/abs/round/divmod/pow`、`str/int/float/bool/list/dict/set/tuple/frozenset/bytes`、`enumerate/zip/map/filter/reversed/isinstance/issubclass/print`、`chr/ord/hex/oct/bin/format/repr`、`True/False/None`）+ 常用异常类（`Exception/ValueError/TypeError/KeyError/IndexError/AttributeError/ZeroDivisionError/ArithmeticError/LookupError/RuntimeError/StopIteration/NotImplementedError`）。**不含** `open/exec/eval/compile/__import__/globals/locals/vars/dir/getattr/setattr/delattr/type/input/breakpoint/exit/quit/help` |
| 无网络 | stdlib 无法做 seccomp 级 syscall 过滤，本期防线是**能力剥夺**：AST 全禁 import + builtins 无 `__import__`/`open`/`getattr` + 空 env，使沙箱内代码在语言层面拿不到 `socket`/`urllib`/`http.client` 任何入口。生产建议叠加容器网络策略 |
| 输出上限 | 父进程校验 `len(stdout) <= limits.max_output_bytes`，超限 → `PythonNodeError` |
| 日志 | 只记摘要（`sandboxed` 布尔、代码长度、退出码、耗时），**绝不记代码正文与完整 state**（H6/S15）；事件名 lowercase_with_underscores，kwargs 传参禁 f-string（S15/AD-02） |
| R3 不变 | 沙箱是 `PythonNode` 的内部执行策略：入口 `convert_state_to_dict` / 出口 `map_output_to_state` 管线、`_ensure_dict` 返回契约、`graph_builder.py` / `registry.py` / `state.py` / `factory.py` **全部零改动**。**本行只约束 S22 沙箱变更**；S23/S24 因嵌套需要改动 `registry.py`（运行栈 + `_run_nested`）与 `factory.py`（`workflow_runner` 透传），见该两节，不构成本行的反例 |

**S23 细则（冻结）：**

| 维度 | 约定 |
| --- | --- |
| 运行栈 | `registry.py` 模块级 `_RUN_STACK: ContextVar[tuple[str, ...]]`，default `()`。`execute_workflow` 进入即 `set(_RUN_STACK.get() + (workflow_id,))`，`finally` 中 `reset(token)`——严格遵循 S11 配对纪律（与 `_RUN_COLLECTOR` 同款），故 ContextVar 永不泄漏、线程/协程间互不串栈 |
| 环检测 | `_run_nested` 中 `workflow_id in _RUN_STACK.get()` → `NestedWorkflowError`，消息含**完整栈**（`" -> ".join(stack + (workflow_id,))`）。同 id 嵌套（A→A 自引用）**先被环检测拒**，不会走到 RLock 重入 |
| 深度上限 | `len(_RUN_STACK.get()) >= max_nesting_depth` → `NestedWorkflowError`，消息含栈与上限值。**语义冻结：`max_nesting_depth` = 运行栈中允许同时存在的工作流数量上限（含最外层）**。默认 3 ⇒ `A→B→C` 可运行（栈深 3），`C` 再引用 `D` 被拒。**registry 级配置，不做 per-node 覆盖**（per-node 会让「全局最深」不可推断，守护形同虚设） |
| 检查时机 | 环与深度检查都在 `_run_nested` **递归之前**，即「调用前拒绝」而非「进入后炸栈」。绝不依赖 Python `RecursionError` 兜底——它不属 `WorkflowEngineError` 家族，会穿透 CLI 的 `except WorkflowEngineError` 落到 catch-all，运维只见「unexpected error」 |
| 死锁 | 同 id 嵌套被环检测先拒；不同 id 的嵌套是**同线程递归**，各持自己的 per-id `RLock`（可重入），故单线程无死锁。**跨线程 AB-BA 在理论上可达**（两个线程分别执行 `A→B` 与 `B→A`，各自栈内无重复 id，环检测不拦）：见 changelog §5 残留风险 2，本期不做全局锁序 |
| 继承既有语义 | `_run_nested` 走的就是 `execute_workflow` 本身，不是另写一条执行路径，故内层**完整继承** S11（run-scoped 日志）、S12（definition execution_history 单槽）、S21（输入合成）。这是本设计最重要的性质：**嵌套不新增执行语义，只新增守护与日志编排** |

**S24 细则（冻结）：**

| 维度 | 约定 |
| --- | --- |
| 内层收集 | 内层 `execute_workflow` 自带独立 `RunLogCollector` 并绑定 `_RUN_COLLECTOR`（S11 既有行为，**零改动**）。同线程 ContextVar 嵌套 set/reset ⇒ 内层 collector 在外层看来是**临时遮蔽**，内层 `finally` reset 后外层 collector 自动恢复 |
| 前缀合并 | 内层返回后，`_run_nested` 取 `result.execution_logs`，逐条 `model_copy(update={"node_name": f"{caller_label}/{原 node_name}"})` 后 `add` 进**外层** collector（经 `nodes/base.py` 既有的 `get_run_collector()` 取得，无需新增访问器）。外层轨迹因此形如 `sub_1/classify`、`sub_1/fetch` |
| 多层复合 | 前缀**自然复合**，无需特判层数：C 的日志并入 B 时成 `sub_c/xxx`，B 的日志（已含该条）并入 A 时成 `sub_b/sub_c/xxx` |
| 子节点自身 output | `SubWorkflowNode` 的 `ExecutionLog.output_data` **只记摘要** `{output_keys: [...], run_id, duration_ms, inner_log_count}`，**不内嵌内层完整输出与日志**——否则同一份数据在轨迹里出现两次（体积翻倍且前后端都要去重）。字段来源：`output_keys` 取自信封 `output` 的键、`run_id`/`inner_log_count` 取自 runner 回传的信封（§4.15，二者只存在于内层 `RunResult` 上，节点无从自知）、`duration_ms` 由节点自行计时。内层业务数据（信封 `output`）经 R3 出口写入外层 state 时 **`dual_write=False`**（见 §4.15：runner 回传的是内层**整个 state**，平铺会静默覆写外层同名 channel，尤以每个工作流都有的 `input` 为甚），即只落 `{node_name}_result`，数据不丢失、经 S7 点路径读取；信封的 meta 键**不得**写入外层 state |
| 前端影响 | `WorkflowTraceDrawer` **零改动**：前缀名是普通字符串，直接展示即可读出层级 |
| H6 | 合并的是既有 `ExecutionLog`（脱敏在 `api.py` 投影时由 `redact` 统一做），前缀化不引入新泄漏面；`caller_label` 是节点名（YAML 自有），非用户数据 |

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
| R1 | 只实现 BaseNode/LLMNode/HTTPNode，禁止新增**内置**节点类型或领域逻辑。**carve-out（2026-09-14）**：`python` 与 `subworkflow` 均是 K5 **插件类型**（`register_node_type` 任意字符串注册、factory 无内置分支、不入 `NodeType` 枚举 C8），不构成 R1 意义上的新增内置节点类型；`python` 的 API 可达性由 S18/S22 约束，`subworkflow` 的由 S18 结构校验与 S23 执行守护约束 | `ls app/workflow/nodes/` 对白名单；`grep -rn "plan\|worker\|dispatcher" app/workflow/` 仅出现于"未来扩展"注释；`NodeType` 成员数守护测试（**恒为 2，不因 python/subworkflow 改变**） |
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

### 变更记录

| 日期 | 变更内容 | 影响章节 | 生效 spec | 备注 |
| --- | --- | --- | --- | --- |
| 2026-09-07 | 画布编排契约扩展：§4.12 `metadata` 新增可选第五键 `execution_logs`（execute 成功响应内嵌轨迹，脱敏后）；新增 §4.13 画布管理端点签名（GET 列表 / GET 查定义 / PUT 全量保存 / DELETE 删除）；§6 新增 S17（YAML 落盘持久化）、S18（节点类型 API 白名单）、S19（写端点鉴权） | §4.12 / §4.13 / §6（S17-S19） | `docs/changelog/workflow-canvas-orchestration/spec-01-contract-change.md` 及下游 spec-02..04、16..20 | 纯契约变更，提交 `docs:`；引擎内核零改动；详细设计见 `docs/workflow-frontend-spec.md` §4-5 与 `docs/workflow-api-and-trace.md` §7.1 |
| 2026-09-11 | LLM 节点接入 provider 体系：§4.7 `LLMConfig` 新增可选 `provider_ref`、`LLMNode.__init__` 新增可选 `chat_model_factory`；§4.5 `create_node`、`GraphBuilder.__init__`、`WorkflowRegistry.__init__` 各新增可选 `chat_model_factory` 透传参数；新增 `app/workflow/ports.py` 承载 `ChatModelFactory` 类型别名；§3 红线 4 补充「注入的不透明 callable 不构成 `app.*` 依赖」；§6 新增 S20（凭据解析三分支 + 注册期校验） | §3 / §4.5 / §4.7 / §6（S20） | `docs/changelog/workflow-llm-provider-integration/spec-01-contract-change.md`；下游 `spec-04-llmnode.md`、`docs/workflow-node-development.md` §3.1-3.2、`docs/changelog/workflow-canvas-orchestration/spec-12-node-config-forms.md` | 签名变更（全部为新增带默认值的可选参数，向后兼容），代码提交用 `refactor!`。**动机**：引擎原先只从 env 取凭据，与 provider 表的 `auth_config.api_key`/`base_url` 完全割裂，设计器手填 `model_name` 既不校验存在性也用不上真实端点 |
| 2026-09-11 | 运行输入合成：§4.10 新增模块级纯函数 `synthesize_run_input(definition, input_data)`；§6 新增 S21（`execute_workflow` 在 `graph.invoke` 前把非空 `input` str 附加式合成为 `messages=[{"role":"user","content":...}]`；`messages` 已存在则透传；对所有工作流生效、不按节点类型特判） | §4.10 / §6（S21） | `docs/changelog/workflow-input-synthesis/spec-01-contract-change.md` | 纯契约变更，提交 `docs:`；引擎仅 registry 一行接线，`api.py`/`cli.py`/`state.py`/`llm_node.py` 零改动。**动机**：执行含 LLM 节点的工作流须手写完整 langchain 消息结构，`messages` 实为引擎内置通道而非业务字段 |
| 2026-09-14 | Python 节点真沙箱：§2.1 新增 `app/workflow/sandbox.py`、`app/workflow/sandbox_worker.py`（并补录 5 个早已落地却漏记的模块 `auth.py`/`ports.py`/`security.py`/`store.py`/`nodes/python_node.py`，计数 15→22）；新增 §4.14 冻结 `validate_code_ast` / `SandboxLimits` / `run_sandboxed`；§5 异常族规范化（`PythonNodeError` 补入冻结块；`WorkflowValidationError` 从 `security.py` **迁入 `models.py`** 并改挂 `WorkflowEngineError`）；**S18 修订**（白名单 `{llm,http}` → `{llm,http,python}`，`python` 须 code-only + AST 通过 + 服务端强制 `sandboxed=true`）；§6 新增 S22（子进程沙箱执行）及其「S22 细则」冻结表（进程隔离 `-I` / 单 JSON 文档协议 / 退出码 0-2-3 / 超时 kill / POSIX rlimit / 空 env / `SAFE_BUILTINS` / 输出上限 / 摘要日志）；§8 R1 追加 K5 插件 carve-out（`NodeType` 恒 2 成员、R4 内置分支恒 2，守护测试不动） | §2.1 / §4.13 / §4.14 / §5 / §6（S18 修订、S22 新增）/ §8（R1） | `docs/changelog/workflow-python-sandbox/spec-01-contract-change.md`；同步 `docs/workflow-node-development.md` §5.1-5.5 与红线表 R1；同步 `docs/workflow-frontend-spec.md` §2.1（Out 行）/ §5.1（节点类型白名单）/ §7（NodeDTO type）/ §8（`validateGraph` 预校验）/ §9（组件树 + `PythonNodeForm.vue`）/ §13（测试覆盖点）/ §14（验收） | 纯契约变更，提交 `docs:`；下游代码提交用 `refactor!`（`PythonNodeConfig` 新增 `sandboxed` + 互斥校验扩展）。**动机**：`PythonNode` 现为进程内非沙箱 `exec`，S18 因此把 `python` 挡在 HTTP 之外——代价是设计器画不出 python 节点，用户做一点数据加工只能塞进 LLM 提示词或外部 HTTP 服务。S18 挡住的是**实现方式**，不是节点类型的价值 |
| 2026-09-14 | **S22 rlimit 机制修订（§7.5 实测触发）**：资源上限由「父进程 `preexec_fn` 施加」改为「**worker 启动后自我施加**，逐项容错 + `applied`/`refused` 报告」；通信协议 stdin 文档新增 `limits` 入参、stdout 文档新增 `limits` 报告；父进程明确**禁用 `preexec_fn`**，`sandbox_rlimit_partial` warning 改由 worker 的 `refused` 派生 | §6（S22 细则：通信协议、资源上限） | `docs/changelog/workflow-python-sandbox/spec-01-contract-change.md` §2.2/§5；`docs/workflow-node-development.md` §5.2/§5.5 | **实测**：darwin 25.3.0 / CPython 3.13 上 `setrlimit(RLIMIT_AS, ...)` 抛 `ValueError: current limit exceeds maximum limit`，而 `preexec_fn` 内抛异常使 `subprocess.run` 整体失败 → macOS 上**每一次**沙箱调用都会失败（原备注假设「macOS 常忽略 `RLIMIT_AS`，不阻断执行」，与实测不符）。**备选方案对比**：① 保留 `preexec_fn` 但整体 try/except 吞掉 —— 内存上限静默失效且无可观测性，且 `preexec_fn` 在多线程宿主（FastAPI）中官方标注不安全，未解决根因；② **worker 自我施加（采纳）** —— 修复 macOS 全量失败，去掉 `preexec_fn` 线程安全隐患，逐项容错换来 `applied`/`refused` 一等可观测性；代价是协议多一个 `limits` 键（双向），属冻结行变更；③ 完全放弃 rlimit，只留 timeout + 容器策略 —— Linux 生产同样失去内存/fork 进程级防线，削弱纵深防御，不采纳。**动机**：契约冻结的机制在目标平台上不可运行，编码前必须先改契约（§11.3「禁止先改代码后补契约」） |
| 2026-09-14 | 子工作流（嵌套）节点：§2.1 新增 `nodes/subworkflow_node.py`（计数 22→23）；§2.2「子图嵌套」禁止条目加**解禁令注解**（langgraph 原生 subgraph 与 `Send` 仍禁，registry 注入式嵌套解禁）；§3 红线 4 补 `WorkflowRunner` 为第二类不透明 callable（**引擎自注入**，组合根零改动）；新增 §4.15 冻结 `WorkflowRunner` / `SubWorkflowNodeConfig` / `SubWorkflowNode`；§4.5 `create_node`、§4.9 `GraphBuilder.__init__` 各新增可选 `workflow_runner`，§4.10 `WorkflowRegistry.__init__` 新增 `max_nesting_depth=3` 并冻结私有 `_run_nested` 与模块级 `_RUN_STACK`（`execute_workflow` 签名不变）；§5 新增 `NestedWorkflowError` + 4 行场景映射；**S18 第二次修订**（白名单加 `subworkflow`，注册期仅结构校验，被引用存在性为运行期检查）；§6 新增 **S23 嵌套执行守护** 与 **S24 内层日志合并** 及两张冻结细则表；§8 R1 carve-out 扩展至 `subworkflow` | §2.1 / §2.2 / §3 / §4.5 / §4.9 / §4.10 / §4.15（新增）/ §5 / §6（S18 修订、S23-S24 新增）/ §8（R1） | `docs/changelog/workflow-subworkflow-node/spec-01-contract-change.md`；同步 `docs/workflow-node-development.md`（§1.3 节点清单加行、**新增 §6 SubWorkflowNode 章节**、§7.1「插件可选注入参数」+ §7.2 构造契约、§9 红线表 R1 行；原 §6-§9 顺延为 §7-§10，两处自引用 §6.3/§7.2 同步改为 §7.3/§8.2）与 `docs/workflow-frontend-spec.md` §2.1（Out 行）/ §5.1（白名单 + subworkflow 前端约束）/ §7（NodeDTO type）/ §8（映射表 + `validateGraph`）/ §9（组件树 + `SubWorkflowNodeForm.vue`）/ §12（测试覆盖点）/ §14（验收） | 纯契约变更，提交 `docs:`；下游代码提交用 `refactor!`（三处构造签名新增带默认值的可选参，向后兼容）。**动机**：工作流无法组合，复用只能复制 YAML，两份定义各自漂移正是 H 系列隐患的常见来源。嵌套初版被 defer 的理由是 H5（构建期快照）与 H3（日志收集），二者根因都是「节点直接认识注册表」——改用不透明 callable 注入后 H5 不成立，H3 由 S24 正面解决。**拍板记录**：`workflow_runner` 从 factory 传到插件节点的方式，用户 2026-09-14 选定**方案 A `inspect.signature` 探测**（否决 B「给 `BaseNode.__init__` 加可选参」——触碰 §4.4 冻结签名且三个既有节点构造全受影响；否决 C「节点从 ContextVar 自取」——把装配期依赖藏进全局态，与 H5 精神相悖）。三案影响面对比见 changelog §3 |

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
