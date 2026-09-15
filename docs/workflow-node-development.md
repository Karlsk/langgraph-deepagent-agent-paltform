# 工作流引擎 Node 开发规范

| 项 | 值 |
| --- | --- |
| 文档角色 | 节点（Node）开发者规范：既有节点契约 + 新节点开发流程 + 红线约束 |
| 依据文件 | `spec/CONTRACT.md` §3/§4.4/§4.5/§4.7/§4.8、`02-开发规范.md` §0（R1-R10）、`app/workflow/nodes/` 实现 |
| 涉及编号 | K4/K5/K9/K10、C2/C5/C7、H2/H4/H6、R1-R10、S3/S4/S5/S7/S8/S9/S11/S14/S15、AD-02/AD-03/AD-04、EXP-L1/L3 |
| 适用版本 | `app.workflow.__version__ = 0.1.0` |

> 本规范面向**扩展节点的开发者**。契约以 [CONTRACT.md](workflow-reimpl-plan/spec/CONTRACT.md) 为最终基准；
> 本文件与 CONTRACT 冲突时以 CONTRACT 为准，且不得擅自折中（CONTRACT §1 冲突处理规则）。

---

## 1. 节点体系总览

### 1.1 分层依赖（CONTRACT §3）

引擎为单向分层，只允许向下依赖：

```
L4  registry.py
      │
L3  graph_builder.py ────────► state.py
      │                          │
L2  nodes/factory.py          models.py ◄──────────┐
      │                                            │
L1  nodes/llm_node.py  nodes/http_node.py  nodes/python_node.py
      │                  │                         │
L0  nodes/base.py ──► utils.py ────────────────────┘
        │
    models.py

横切：logging_conf.py 不依赖任何业务模块，任何模块都可 import 它
入口：cli.py / api.py / __main__.py 位于 L4 之上，只允许向下调用
```

### 1.2 四条依赖红线（违反即架构腐化）

1. `models.py` 不得 import 任何引擎模块（只依赖 pydantic / 标准库 / `yaml`）。
2. **`nodes/*` 不得 import `registry` / `graph_builder`**——节点不知道图与注册表的存在（同时根除 H5）。
3. `utils.py` 不得 import LLM / HTTP 客户端库（C7）。
4. **引擎自包含**：`app/workflow/` 任何模块不得 import `app.core.*` / `app.api.*` / `app.services.*`（AD-02）；
   反向集成由外部装配，唯一例外是入口层 `api.py` 允许 import `app.core.limiter` / `app.core.config`（spec-08 composition-root 例外）。

对节点开发者的直接约束：一个新节点模块**只能** import 标准库、第三方库（langchain/httpx/tenacity/pydantic 等）、
以及 `app.workflow.models` / `app.workflow.nodes.base` / `app.workflow.nodes.factory`（仅 `register_node_type`）/ `app.workflow.utils`。

### 1.3 当前节点清单

| 节点类型 | 类 | 注册方式 | 说明 |
| --- | --- | --- | --- |
| `llm` / `LLM` | `LLMNode` | factory 内置分支 + 模块底部 `register_node_type("llm", ...)` | 多供应商对话调用，env 密钥，tenacity 退避重试 |
| `http` / `HTTP` | `HTTPNode` | factory 内置分支 + 模块底部 `register_node_type("http", ...)` | 模板渲染请求，`response_path` 提取，显式 retry/mock |
| `python` | `PythonNode` | 纯插件路径（模块底部 `register_node_type("python", ...)`，factory **无**内置分支） | 代码执行：`code` 模式按 `sandboxed` 二选一（子进程沙箱 / 进程内受信 `exec`），`entry` 模式加载仓库模块函数且不可沙箱化。经 `PUT` 注册者由服务端强制 `sandboxed=true`（S18），详见 §5.5 |
| `subworkflow` | `SubWorkflowNode` | 纯插件路径（模块底部 `register_node_type("subworkflow", ...)`，factory **无**内置分支） | 引用另一个已注册工作流；runner 由 registry 自注入，环与深度守护见 S23、内层日志前缀合并见 S24，详见 §6 |

> `NodeType` 枚举刻意只含 `LLM`/`HTTP` 两个成员（C8/R1）；`python` 与 `subworkflow` 都是**插件类型**，以任意字符串注册，
> 不受枚举约束。`NodeDefinition.type` 保持 `str`（非枚举）正是为了让插件类型透传（R4）。

---

## 2. BaseNode 契约（CONTRACT §4.4）

`BaseNode` 是所有节点的抽象基类，定义了节点的身份、执行单元、日志累积与配置校验契约。

### 2.1 冻结签名

```python
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

### 2.2 必须实现的两个抽象方法

| 方法 | 契约 | 要点 |
| --- | --- | --- |
| `build_runnable()` | 返回**唯一执行单元**（K4） | 内部构造 `func(state) -> dict`，经 `self.wrap_runnable(func)` 包装为 `RunnableLambda(func).with_config(tags=[self.name])`；`tags` 用于 langgraph/langsmith 轨迹中标识节点（`test_runnable_tags`，K4） |
| `validate_config()` | 配置非法时**抛 `ValueError`**，合法返回 `True` | 校验前置到构造期或此方法；不要在 `build_runnable` 的 `func` 里才做配置合法性判断 |

### 2.3 日志累积逻辑（H3/S11/S15）

`log_execution` 是节点写执行日志的**唯一入口**，它做两件事：

```python
def log_execution(self, execution_log: ExecutionLog) -> None:
    self._execution_history.append(execution_log)      # 1. 写节点实例历史
    collector = get_run_collector()                     # 2. 镜像写入运行级收集器（若存在）
    if collector is not None:
        collector.add(execution_log)
```

- **运行级收集器**（`RunLogCollector`）经 `_RUN_COLLECTOR` ContextVar 传播，由 `registry.execute_workflow`
  在每次运行时 `set_run_collector` 绑定、`finally` 复位（S11）。节点无需感知收集器的存在，只管调 `log_execution`。
- **禁止反模式（H1）**：运行时流程**不得**依赖 `clear_execution_history()` 来收集日志；`clear_execution_history()`
  仅供调试。日志隔离由 ContextVar 保证，不靠清空共享节点实例（`grep -n "clear_execution_history" app/workflow/registry.py` 期望零命中）。
- **日志内容纪律（S15/H6）**：`ExecutionLog.input_data` 只记**摘要**（如消息条数、method/url、配置摘要、代码长度），
  **绝不含密钥与完整 state**。参见 §3.4/§4.5/§5.3 各节点的 `_log` 实现。

### 2.4 状态不可变（R3/S5）

节点 `func` 必须遵循标准进出管线：

```
入口：state_dict = convert_state_to_dict(state)     # pydantic→model_dump / dict 直通 / 其它→{}，不 mutate 输入
处理：output = <节点业务逻辑>(state_dict)            # 产出 dict
出口：return map_output_to_state(self.name, output, state_dict)   # 双写 + history 增量
```

- **禁止 mutate 输入 state**（R3）：`convert_state_to_dict` 返回的 dict 只读使用，节点不得原地修改它或原始 state 对象。
- **双写语义（S4/K9）**：`map_output_to_state` 默认写 `{node_name}_result`（整包）+ 平铺 `output` 各字段。
  `{node_name}_result` 槽位由 `StateModelFactory` 在**构建期**预声明（EXP-G8），否则被 langgraph 静默丢弃；
  平铺字段需在 YAML `state_schema` 声明才能写回运行期 state。
- **history 增量（S3/C4）**：`map_output_to_state` 在 add channel 下**只返回增量** `[entry]`，禁返回
  `history + [entry]` 全量（防历史翻倍）；entry 形如 `f"{node_name}: {str(node_output)[:100]}..."`。

---

## 3. LLMNode 详解（CONTRACT §4.7）

多供应商对话节点，密钥 env-only，tenacity 指数退避重试。

### 3.1 LLMConfig 字段表（`extra="forbid"`，S14）

| 字段 | 类型 | 默认 | 说明 |
| --- | --- | --- | --- |
| `llm_type` | `Literal["openai","anthropic"]` | `"openai"` | 供应商选择 |
| `model_name` | `str` | `"gpt-4o-mini"` | 模型名；`validate_config` 要求非空 |
| `base_url` | `str \| None` | `None` | 显式 base_url，优先级最高 |
| `api_key_env` | `str \| None` | `None` | 显式密钥 env 名；未设置按 `llm_type` 取默认（AD-12） |
| `base_url_env` | `str \| None` | `None` | 显式 base_url env 名 |
| `temperature` | `float` | `0.7` | 约束 `ge=0.0, le=2.0` |
| `max_tokens` | `int \| None` | `None` | anthropic 未设时回退保守值 4096（EXP-L1） |
| `top_p` | `float \| None` | `None` | |
| `system_prompt` | `str` | `""` | 非空则前置 `SystemMessage` |
| `extra_params` | `dict[str, Any]` | `{}` | 透传为 `model_kwargs` |
| `max_retries` | `int` | `3` | 约束 `ge=0`；重试次数（tenacity `stop_after_attempt(max_retries+1)`） |
| `retry_base_delay` | `float` | `1.0` | 约束 `gt=0`；退避乘数 |
| `provider_ref` | `str \| None` | `None` | `"<provider_name>/<model_name>"`；非空时凭据与 model_id 全部由宿主注入的 `ChatModelFactory` 从 provider 表解析（S20），`llm_type`/`model_name`/`base_url`/`api_key_env` 降级为展示与 env 回退用途 |

> **不存在明文 `api_key` 字段**（H6/ADR-008/R5）。`provider_ref` 为空时密钥只经 `_resolve_api_key()` 从环境变量解析；
> `provider_ref` 非空时密钥由注入工厂在调用期从 provider 表取得，**config 与 YAML 永不落密钥**。

### 3.2 密钥解析（env-only，R5/H6）

```python
_DEFAULT_API_KEY_ENV = {"openai": "OPENAI_API_KEY", "anthropic": "ANTHROPIC_API_KEY"}
_DEFAULT_BASE_URL_ENV = {"openai": "OPENAI_BASE_URL"}
```

- `_resolve_api_key()`：取 `api_key_env` 或 `llm_type` 默认 env → `os.environ.get(env_name)`；缺失抛
  `ConfigError`，消息**含 env 名、不含密钥值**（H6）。
- `_resolve_base_url()`：显式 `base_url` 优先 → `base_url_env` → `llm_type` 默认 env（AD-12）。

**上述 env 路径仅在 `provider_ref` 为空时生效**（S20）。`_get_llm_instance()` 的完整三分支：

| 条件 | 客户端来源 | 凭据来源 |
| --- | --- | --- |
| `provider_ref` 非空 + 工厂已注入 | `chat_model_factory(provider_ref, overrides)` | provider 表（`Provider.auth_config` / `base_url` / `ModelConfig.model_id`） |
| `provider_ref` 非空 + 工厂为 `None` | — | 抛 `ConfigError`（**不静默回退 env**：打错端点/用错账号计费比直接失败更危险） |
| `provider_ref` 为空 | 按 `llm_type` 分支构造 `ChatOpenAI`/`ChatAnthropic` | env（上表两个 `_resolve_*`） |

`overrides` 携节点级 `temperature`（及 `max_tokens`，若设置），**节点配置优先于** `ModelConfig.extra_params`。
工厂由组合根（`app/main.py`）注入，经 `WorkflowRegistry` → `GraphBuilder` → `create_node` → `LLMNode` 透传；
引擎侧只见 `app/workflow/ports.py` 的 `ChatModelFactory` 类型别名，零 `app.*` 依赖（§3 红线 4）。

> **K10 memoize 副作用**：客户端按节点实例缓存，provider 轮换密钥后需**重新保存/注册该工作流**才会生效。

### 3.3 重试与懒加载（AD-03/S8/K10）

- `_get_llm_instance()`：**懒加载并 memoize** provider 客户端（K10）；构造时统一传 `max_retries=0`
  禁用 SDK 内置重试，tenacity 是唯一重试所有者（EXP-L3/AD-03）。
- `_invoke_with_retry()`：tenacity `Retrying`，`stop_after_attempt(max_retries+1)` + `wait_exponential(multiplier=retry_base_delay)`
  + `retry_if_exception(_is_retryable_llm_error)`；退避序列 `1,2,4`（`retry_base_delay=1.0` 时）。
- **可重试谓词**（EXP-L3 定稿）：`_is_retryable_llm_error` 仅当 `status_code == 429` 或 `>= 500` 才重试；
  其它（401/400 等）不重试。耗尽后包装 `LLMNodeError`（消息含尝试次数）。

### 3.4 输出与日志（S5/S15/H6）

- 成功输出：`{"response": <content>, "model": model_name}`；经 `map_output_to_state` 双写。
- `messages` 取值：`state_dict.get("messages")` 优先，其次实例级 `self.messages`；皆空抛 `ValueError`（S5）。
- `_log()`：`input_data` 只记 `{"message_count": N, "model": model_name}`，**不记消息正文**（H6/S15）。

---

## 4. HTTPNode 详解（CONTRACT §4.8）

模板渲染请求节点，`response_path` 点路径提取，显式 retry/mock 开关。

### 4.1 HTTPNodeConfig 字段表（`extra="forbid"`，S14）

| 字段 | 类型 | 默认 | 说明 |
| --- | --- | --- | --- |
| `url` | `str` | 必填 | `validate_config` 要求非空；支持 `{key}` 模板 |
| `method` | `Literal["GET","POST","PUT","DELETE"]` | `"POST"` | |
| `headers` | `dict[str,str] \| None` | `None` | 各值支持模板渲染 |
| `body_template` | `str \| None` | `None` | 渲染后必须为合法 JSON，否则抛 `ValueError`（带节点名） |
| `response_path` | `str \| None` | `None` | 点路径提取，如 `"data.result"`；`None` 取整个响应 |
| `timeout` | `float` | `30.0` | 约束 `gt=0` |
| `max_retries` | `int` | `0` | **默认不重试**，显式开启（H2/S8） |
| `retry_base_delay` | `float` | `1.0` | 约束 `gt=0` |
| `retry_on_status` | `list[int]` | `[429,500,502,503,504]` | 命中才重试 |
| `mock_enabled` | `bool` | `False` | **默认关闭**，显式启用才生效（H2/H6） |
| `mock_responses` | `dict[str,str] \| None` | `None` | `mock_enabled=True` 时须非空（`validate_config`） |

### 4.2 模板渲染（`render_template`）

- 占位符正则 `\{([A-Za-z_][A-Za-z0-9_]*(?:\[[A-Za-z0-9_]+\])?)\}`：`{key}` 或 `{parent[child]}`。
- 上下文由 `_flatten_context` 扁平化：顶层键 + 一层嵌套 dict 展开为 `parent[child]`。
- 未解析的占位符**原样保留**（不报错），并记 `http_node_placeholder_unresolved` DEBUG 日志。
- 含 JSON 语法的花括号（引号/冒号/空白）不被当作占位符，渲染时原样存活。

### 4.3 响应提取（`_extract` / `response_path`）

点路径逐层 `dict.get(part)`；中途遇非 dict → 返回 `None`；`path=None` → 返回整个 data。

### 4.4 重试与 mock（S8/S9/H2）

- **真实分支**：`_send_with_retry` → tenacity（同 LLMNode 结构）；`_is_retryable` 仅当 `httpx.HTTPStatusError`
  且 `status_code in retry_on_status` 才重试；`_send_once` 用 `httpx.request(..., timeout=...)`（同步，K10）。
  失败包装 `HTTPNodeError`（含 method/url/status/尝试次数）。
- **mock 分支**（仅 `mock_enabled=True`）：`_resolve_mock` 查 `"{METHOD} {url}"`，回退 `"{url}"`；
  **未命中抛 `HTTPNodeError`，绝不静默回退真实调用**（S9/H2/H6）。命中时 `status_code` 固定为 `200`
  （模拟成功响应，与真实分支结构一致）。

### 4.5 输出与日志（S15/H6）

- 成功输出：`{"status_code": <code>, "url": <rendered_url>, "response": <extracted>}`；经 `map_output_to_state` 双写。
- `_log()`：`input_data` 只记 `{"method": <method>, "url": <rendered_url>}`，**不记 body/headers/密钥**（H6/S15）。

---

## 5. PythonNode 插件范例（K5）

`PythonNode` 是引擎附带的**唯一非内置节点类型**，完整演示了 K5 插件路径：factory **无**它的内置分支，
它经模块底部 `register_node_type("python", PythonNode)` 自注册。新节点开发者应以此为蓝本。

### 5.1 配置（`code` / `entry` 互斥，S14；`sandboxed` 只对 `code` 有意义）

```python
class PythonNodeConfig(BaseModel, extra="forbid"):
    code: str | None = None         # 内联 YAML 代码，注入 state（dict 快照），须 return dict
    entry: str | None = None        # "module:function"，以 state dict 调用，须 return dict
    sandboxed: bool = False         # True → 子进程沙箱执行（S22）；仅 code 模式可用

    @model_validator(mode="after")
    def _check_exclusivity(self) -> PythonNodeConfig:
        if (self.code is None) == (self.entry is None):
            raise ValueError("PythonNodeConfig requires exactly one of 'code' or 'entry'")
        if self.entry is not None and self.sandboxed:
            raise ValueError("sandboxed=True requires 'code' mode; 'entry' cannot be sandboxed")
        return self
```

`sandboxed` 对 `entry` 无意义（`entry` 靠 `importlib` 加载仓库模块，无法沙箱化），因此**报错而非静默忽略**——
静默忽略会让读者以为代码跑在沙箱里。经 `PUT` 进入的定义由服务端强制 `sandboxed=true`（S18 ③），客户端传值被覆写。

### 5.2 执行语义

`code` 模式有**两条执行路径**，由 `sandboxed` 决定（S22）：

- **`sandboxed=false`（默认，受信仓库路径）**：进程内 `exec`，只允许运行受信的、仓库自有的代码
  （langchain-sandbox 已评估并否决：失维护、Deno 运行时、每次调用启动延迟）。包装为
  `def __python_node_fn(state):` 后 `exec`，调用并取返回值；须 `return` dict。`exec` 处标注
  `# noqa: S102 — trusted repository-owned code by design`。**该路径不可经 HTTP 注册**（见 §5.5）。
- **`sandboxed=true`（S22 沙箱路径）**：转交 `app/workflow/sandbox.py` 的 `run_sandboxed(code, state_dict)`，
  在子进程中执行，**禁止进程内 `exec`**。代码同样须 `return` dict；超时 / 非零退出 / worker 报 `ok=false` /
  输出非 dict → `PythonNodeError`。
- `entry` 模式（**无沙箱形态**）：`importlib.import_module` 加载 `module:function`，以 state dict 调用；须返回 dict。
- 输出非 dict / 缺 return / entry 格式错 / import 失败 → 抛 `PythonNodeError`（`_ensure_dict` 统一校验）。

### 5.3 日志（H6/S15）

`_log()` 的 `input_data` 只记摘要：`{"mode":"code","code_chars":N,"sandboxed":bool}` 或
`{"mode":"entry","entry":"..."}`——**绝不记代码正文**（H6/S15）。异常分支记录后重抛（H2/R6，禁死 except）。

### 5.4 自注册

```python
# 模块底部（factory 底部 import 本模块触发注册，R4 不加内置分支）
register_node_type("python", PythonNode)
```

### 5.5 API 可达性与沙箱边界（S18 / S22）

`python` 是 K5 插件类型，不入 `NodeType` 枚举（C8 恒 2 成员），也不增加 `create_node` 的内置分支（R4 恒 2）——
CONTRACT §8 R1 已就此追加 carve-out。它能否经 `PUT /api/v1/workflows/{id}` 写入，由 S18 的三条注册期条件决定：

1. **只允许 `code` 模式**：`config` 含 `entry` → 拒绝（`entry` 能加载任意仓库模块，无法沙箱化）；
2. **AST 预检通过**：`validate_code_ast(config["code"])` 拒绝**白名单外** import 与**相对导入** /
   dunder 标识符 / 危险调用（`exec`/`eval`/`open`/`getattr`/`type`/…）/ 超长（64 KiB）；
   import 判定**只看根模块**（`import a.b` / `from a.b import c` 均取 `a`），规则名仍为 `no-import`，
   错误消息携**行号 + 规则名 + 根模块名**，**绝不携代码正文**（H6）→ HTTP 422；
3. **服务端强制 `sandboxed=true`**：忽略并覆写客户端传值。`sandboxed` 是**安全属性**而非用户偏好，
   交给客户端等于把 RCE 开关暴露给请求方。

沙箱本身的冻结约定（子进程 `sys.executable -I`、单 JSON 文档协议（含 `limits` 入参与 `applied`/`refused` 回报）、
退出码 0/2/3、`timeout` kill、**worker 自我施加**的 POSIX rlimit（逐项容错，父进程禁用 `preexec_fn`）、
`env={"PATH":"/usr/bin:/bin"}` 不继承宿主环境、`SAFE_BUILTINS` 内建白名单（含 **受限 `__import__`**）、
**模块白名单**（18 项 stdlib，`sandbox.py` 与 `sandbox_worker.py` 各存一份、守护测试钉住相等）、输出上限、摘要日志）
见 CONTRACT §6「S22 细则」表。三点必须记住：

- **沙箱不是绝对安全**，而是把「经 HTTP 注册的代码」从*等价 RCE* 降到*受限于纯计算 + 有限资源*；
  生产部署建议叠加容器级隔离（gVisor / seccomp / 网络策略）。
- **`sandboxed=false` 的 YAML 仍是进程内 `exec`**，其安全性依赖「能写这些文件的人已经能改代码」这一既有信任边界。
  S18 只保证经 HTTP 进入的定义必为 `sandboxed=true`。
- **（2026-09-14）「无网络」由机制保证降级为评审保证**：原论证是「AST 全禁 import + builtins 无 `__import__` +
  空 env」三条一起成立；白名单放开后，保证的**来源**变成「白名单里没有任何网络模块」。规则 3 只封 dunder，
  **单下划线属性可达**（实测 `re._compiler`），故**每新增一个白名单模块都必须逐个评审其公开与单下划线属性**
  是否携带能力——「加了再说」在这里不成立。

---

## 6. SubWorkflowNode 插件（嵌套，S23 / S24）

`subworkflow` 让一个工作流引用另一个已注册工作流。它与 `python` 同为 **K5 插件类型**
（模块底部 `register_node_type("subworkflow", SubWorkflowNode)`，factory **无**内置分支，不入 `NodeType` 枚举 C8）。

嵌套能力初版被 defer，理由是 **H5**（构建期快照：节点若持有 registry 引用，编译出的图会钉死当时的注册表）
与 **H3**（日志收集：内外层日志交错无法区分来源）。二者根因相同——「节点直接认识注册表」。
本实现把能力以**不透明 callable** 沿构造链注入（与 S20 的 `ChatModelFactory` 同款），节点只持有一个
`WorkflowRunner`，不知道它背后是 registry、测试替身还是远端服务，H5 因此不成立；H3 由 S24 正面解决。

冻结签名见 CONTRACT §4.15。

### 6.1 配置字段（`SubWorkflowNodeConfig`，`extra="forbid"`，S14）

| 字段 | 类型 | 默认 | 说明 |
| --- | --- | --- | --- |
| `workflow_id` | `str` | **必填** | 被引用工作流 id。**存在性是运行期检查**（未注册 → `WorkflowNotFoundError`），注册期只校验「非空 str」（S18） |
| `input_map` | `dict[str, str]` | `{}` | `{内层 state 键: 外层 state 点路径}`，路径语法同 S7。**外层路径不存在则跳过该键**（让内层按 S14 走声明默认值），不抛 `KeyError`——与 `{input}` 占位符渲染的既有容错口径一致 |
| `inherit_input` | `bool` | `False` | 为真则先把外层 state **全量**传给内层，再叠加 `input_map` 解析结果；**`input_map` 优先** |

**为什么不做注册期存在性校验**：`PUT wf_outer` 可能先于 `PUT wf_inner` 到达（前端逐个保存、脚本批量导入、
或 inner 被删而 outer 仍在盘上）。注册期校验会强制拓扑序保存，把「顺序」变成隐性契约，比运行期报错更难排查。

### 6.2 执行语义（R3 管线不变）

```
convert_state_to_dict 进
  → 组装内层输入（inherit_input 全量拷贝 → input_map 覆盖，后者优先）
  → workflow_runner(workflow_id, inner_input, self.name) 得到三键结果信封
  → 只取 envelope["output"] 交 map_output_to_state(..., dual_write=False) 出
  → 节点自身 ExecutionLog 记摘要（§6.4）
```

**`dual_write=False` 是本节点对 R3 出口的唯一偏离，必须如此**：runner 回传的是内层的**整个最终 state**
（`input`、各 `{node}_result`、`history`…），不是「一个节点的输出」。按默认平铺会让内层 channel
**静默覆写**外层同名 channel——最典型的是 `input`：**每个**工作流都声明 `input`，内层的 `input`
（可能只是声明默认值）会盖掉外层真正的 `input`，外层后续节点全部读到错值且**无任何报错**；`history` 同理。
关掉平铺后数据**不丢失**：内层结果落在 `{node_name}_result`，外层经 `sub_1_result.<path>`（S7 点路径）读取，
条件边与 `{...}` 模板均可引用。`history_increment` 保持默认 `True`（只追加一条增量）。

**结果信封（CONTRACT §4.15 冻结，恰三键）**：`{"output": dict, "run_id": str, "inner_log_count": int}`。
`output` 是内层 `RunResult.output`；`run_id` 与 `inner_log_count` 只存在于内层 `RunResult` 上，
节点无从自知，故必须由 runner 一并回传，供 §6.4 的摘要使用。

`workflow_runner` 为 `None` → **`ConfigError`（CONTRACT §5），在 `__init__` 抛出**。时机依据 S6
「构建期校验优先」：构造期抛出使装配错误落在 `register_workflow` → `api.py` 的
`except (ValueError, WorkflowEngineError)` → **422 build-time error**；推迟到执行期则同样的错误
只能表现为运行期 **500**。处置口径与 S20 的「`provider_ref` 非空但工厂为 `None`」一致：
**不静默降级、不返回空 dict**——静默降级会让「忘了注入 runner」表现为「子工作流什么都没做」，
比直接失败难查得多。

生产装配路径（**组合根零改动**）：`WorkflowRegistry.__init__` 以 bound method `self._run_nested`
作为 runner 构造 `GraphBuilder` → `_add_nodes` 透传给 `create_node` → 插件分支按签名探测传给本节点。
详见 §7.1。

### 6.3 嵌套守护（S23）

`registry.py` 模块级 `_RUN_STACK: ContextVar[tuple[str, ...]]`（default `()`）记录当前运行栈。
`execute_workflow` 进入即 `set(_RUN_STACK.get() + (workflow_id,))`，`finally` 中 `reset(token)`——
与 `_RUN_COLLECTOR` 同款的 S11 配对纪律，故 ContextVar 永不泄漏、线程/协程间互不串栈。

`_run_nested` 在**递归之前**过两道守护（「调用前拒绝」而非「进入后炸栈」）：

| 守护 | 条件 | 异常 |
| --- | --- | --- |
| 环检测 | `workflow_id in _RUN_STACK.get()` | `NestedWorkflowError`，消息含**完整栈**（`a -> b -> a`）。同 id 自引用（A→A）**先被这里拒**，不会走到 RLock 重入 |
| 深度上限 | `len(_RUN_STACK.get()) >= max_nesting_depth` | `NestedWorkflowError`，消息含栈与上限值 |

**`max_nesting_depth` 语义（冻结）**：运行栈中允许**同时存在**的工作流数量上限，**含最外层**。
默认 `3` ⇒ `A→B→C` 可运行（栈深恰为 3），`C` 再引用 `D` 被拒。它是 **registry 级**配置
（`WorkflowRegistry(max_nesting_depth=...)`），**不做 per-node 覆盖**——per-node 会让「全局最深」不可推断，
守护形同虚设。

**绝不依赖 `RecursionError` 兜底**：它不属 `WorkflowEngineError` 家族，会穿透 CLI 的
`except WorkflowEngineError` 落到 catch-all，运维只看到「unexpected error」。

**死锁**：单线程嵌套是**同线程递归**，各持自己的 per-id `RLock`（可重入），无死锁。
**跨线程 AB-BA 在理论上可达**——两个线程分别执行 `A→B` 与 `B→A`（这是两个不同的外层工作流互相引用，
各自栈内无重复 id，故环检测**不拦**）：线程 1 持 A 锁等 B 锁、线程 2 持 B 锁等 A 锁。
`execute_workflow` 全程无超时（既有状况），故死锁表现为挂住而非报错。本期不做全局锁序；
生产若出现互相引用的工作流对，应在评审期禁止该拓扑。

### 6.4 内层日志合并（S24）与摘要输出

内层 `execute_workflow` 自带独立 `RunLogCollector`（S11 既有行为，**零改动**）。因是同线程 ContextVar
嵌套 set/reset，内层 collector 在外层看来是**临时遮蔽**，内层 `finally` reset 后外层自动恢复。

内层返回后，`_run_nested` 取 `result.execution_logs`，逐条

```python
log.model_copy(update={"node_name": f"{caller_label}/{log.node_name}"})
```

后 `add` 进**外层** collector（经 `nodes/base.py` 既有的 `get_run_collector()` 取得，无需新增访问器）。
外层轨迹因此形如 `sub_1/classify`、`sub_1/fetch`。

**前缀自然复合**，无需特判层数：C 的日志并入 B 时成 `sub_c/xxx`，B 的日志（已含该条）并入 A 时成
`sub_b/sub_c/xxx`。

**子节点自身的 `ExecutionLog.output_data` 只记摘要**：

```python
{"output_keys": [...], "run_id": ..., "duration_ms": ..., "inner_log_count": ...}
```

字段来源：`output_keys` 取自信封 `output` 的键，`run_id`/`inner_log_count` 取自 runner 回传的信封，
`duration_ms` 由节点自行计时。摘要**不含**内层输出本体，也不含内层日志（后者已按前缀单独并入，见上）。

**不内嵌内层完整输出与日志**——否则同一份数据在轨迹里出现两次（体积翻倍，且前后端都要去重）。
内层业务数据经 R3 出口 `map_output_to_state` 正常写入外层 state，**不丢失**。

前端 `WorkflowTraceDrawer` **零改动**：前缀名是普通字符串，直接展示即可读出层级。

**已知取舍**：内层工作流定义对象上的 `execution_history`（S12 单槽）会被嵌套运行覆盖其此前的独立运行记录。
外层 `RunResult` 才是本次运行的权威轨迹，内层单槽历史只是调试便利；要保留需改成多槽有界队列，属 S12 契约变更。

---

## 7. 插件式注册流程（R4）

### 7.1 factory 解析顺序（方案 A）

`nodes/factory.py` 的 `create_node` 按固定顺序解析节点类型：

```python
def create_node(definition, operator_log=None) -> BaseNode:
    op_log = operator_log or OperatorLog(node_name=definition.name, input_schema={}, output_schema={})
    # 1. 内置兜底恰好 2 个分支（R4：禁 elif），专用构造器签名
    if definition.type in ("llm", "LLM"):
        return _llm_node.LLMNode(name=..., llm_config=definition.config, operator_log=op_log)
    if definition.type in ("http", "HTTP"):
        return _http_node.HTTPNode(name=..., config=definition.config, operator_log=op_log)
    # 2. 插件注册表（generic BaseNode interface）
    if definition.type in _NODE_REGISTRY:
        node_class = _NODE_REGISTRY[definition.type]
        # 可选注入参数按签名探测传递（见下方「插件可选注入参数」）
        return node_class(name=..., node_type=definition.type, config=definition.config, operator_log=op_log,
                          **({"workflow_runner": workflow_runner} if _accepts(node_class, "workflow_runner") else {}))
    # 3. 未知类型
    raise ValueError(f"Unknown node type '{definition.type}'. Registered types: {list_node_types()}. "
                     "Use register_node_type() to add custom types.")
```

**插件可选注入参数（2026-09-14，S23/S24；用户拍板方案 A）**：`SubWorkflowNode` 需要第 5 个构造参数
`workflow_runner`，但插件分支原本是固定 4-kwarg 调用。**不改 `BaseNode` 冻结签名（CONTRACT §4.4）**，
而是在插件分支用 `inspect.signature(node_class.__init__)` 探测：

- 声明了 `workflow_runner` 形参，**或**声明了 `VAR_KEYWORD`（`**kwargs`）→ 传该 kwarg；
- 否则按原 4-kwarg 形态调用。

收益：`PythonNode` 等既有插件与任何第三方插件**零改动**（不接受就收不到），R4「内置分支恰 2 个」守护不破。
代价：这是**隐式契约**——插件作者不读文档不会知道自己的 `__init__` 可以声明这个参数，故本段即为该契约的
正式出处。被否决的两案（给 `BaseNode.__init__` 加可选参 / 节点从 ContextVar 自取）及影响面对比见
`docs/changelog/workflow-subworkflow-node/spec-01-contract-change.md` §3。

### 7.2 register_node_type 契约（CONTRACT §4.5）

```python
_NODE_REGISTRY: dict[str, type[BaseNode]] = {}

def register_node_type(type_name: str, node_class: type[BaseNode]) -> None:
    """注册前校验 BaseNode 子类，否则 TypeError。"""

def list_node_types() -> list[str]:
    """返回已注册的节点类型名列表。"""
```

- 注册前校验 `node_class` 是 `BaseNode` 子类，否则抛 `TypeError`。
- 插件经 **generic BaseNode 接口**构造：`node_class(name=..., node_type=..., config=..., operator_log=...)`——
  因此插件节点的 `__init__` 签名必须兼容这四个关键字参数（与内置节点的专用构造器不同）。
  这四个是**必须兼容**的下限；额外的**可选注入参数**（如 `workflow_runner`）由 factory 按签名探测追加，
  声明即获得、不声明则不收（见 §7.1「插件可选注入参数」）。

### 7.3 R4 红线：严禁在 create_node 堆叠 if/elif

- 内置分支**恰好 2 个**（`("llm","LLM")` / `("http","HTTP")`），新增节点类型**一律走 `register_node_type`**，
  **禁止**在 `create_node` 里加 `elif definition.type == "my_node":` 之类的领域分支。
- 守护测试 `test_builtin_branch_count_is_two` 断言内置分支数 == 2；`grep -n "elif" app/workflow/nodes/factory.py` 人工核对。
- 打破循环导入（AD-04）：factory 与节点模块一律**顶层导入**；`llm_node`/`http_node`/`python_node` 的 import
  置于 `factory.py` **文件底部**（模块形态导入），使节点模块自注册时能先拿到 `register_node_type`，
  同时属性访问推迟到 `create_node` 调用期，对任意导入顺序均安全。

### 7.4 注册时机

`register_node_type` 必须在 **registry 构建之前**执行。模块底部自注册 + factory 底部 import 的组合保证了：
只要 `app.workflow.nodes.factory` 被导入，三个自带节点类型即已注册。宿主自定义节点应在装配前显式 import 其模块
（触发自注册）或直接调用 `register_node_type`。

---

## 8. 新节点开发代码模板

以 `python_node.py` 为蓝本的最小可用骨架。假设要新增一个 `my_node` 类型：

```python
"""My custom node: <一句话职责> (K5 plugin)."""

from __future__ import annotations

import time
from typing import Any, override

import structlog
from langchain_core.runnables import Runnable
from pydantic import BaseModel, ConfigDict

from app.workflow.models import ExecutionLog, OperatorLog, WorkflowEngineError
from app.workflow.nodes.base import BaseNode
from app.workflow.nodes.factory import register_node_type
from app.workflow.utils import convert_state_to_dict, map_output_to_state

logger = structlog.get_logger(__name__)


class MyNodeError(WorkflowEngineError):
    """<节点专属异常，继承引擎统一基类；如需新增须在 models.py 单点定义>。"""


class MyNodeConfig(BaseModel):
    """节点配置：extra='forbid' 拒绝未知字段（S14）。"""

    model_config = ConfigDict(extra="forbid")

    some_param: str
    max_retries: int = 0


class MyNode(BaseNode):
    """<节点职责一句话>。"""

    def __init__(
        self,
        name: str,
        config: dict[str, Any] | MyNodeConfig,
        node_type: str = "my_node",
        operator_log: OperatorLog | None = None,
    ) -> None:
        # 构造期前置校验配置（S14）；执行推迟到 build_runnable
        node_config = config if isinstance(config, MyNodeConfig) else MyNodeConfig(**config)
        super().__init__(name, node_type, node_config.model_dump(), operator_log)
        self._node_config = node_config

    @override
    def validate_config(self) -> bool:
        """配置非法抛 ValueError（构造期已校验，此处为契约保留，K4）。"""
        if not self._node_config.some_param:
            raise ValueError(f"MyNode '{self.name}': some_param must be non-empty")
        return True

    @override
    def build_runnable(self) -> Runnable:
        """唯一执行单元（K4）：R3 标准进出管线。"""

        def func(state: dict[str, Any]) -> dict[str, Any]:
            started = time.perf_counter()
            # 1. R3 入口：state → dict（不 mutate 输入）
            state_dict = convert_state_to_dict(state)
            output: dict[str, Any] = {}
            try:
                # 2. 节点业务逻辑（此处不得 import registry/graph_builder，不得硬编码业务字段名 R2）
                output = {"result": do_something(state_dict, self._node_config)}
                # 3. log_execution：input_data 仅摘要（H6/S15），绝不含密钥与完整 state
                self._log(output, (time.perf_counter() - started) * 1000, error=None)
            except Exception as exc:
                # 4. 异常分支：记录后重抛（H2/R6，禁止死 except）
                self._log(output, (time.perf_counter() - started) * 1000, error=str(exc))
                logger.exception("my_node_execution_failed", node=self.name, error=str(exc))
                raise
            # 5. R3 出口：双写 + history 增量
            return map_output_to_state(self.name, output, state_dict)

        return self.wrap_runnable(func)

    def _log(self, output: dict[str, Any], execution_time_ms: float, error: str | None) -> None:
        """ExecutionLog 的 input_data 只记摘要（S15/H6）。"""
        self.log_execution(
            ExecutionLog(
                node_name=self.name,
                node_type=str(self.node_type),
                input_data={"some_param": self._node_config.some_param},  # 摘要，非完整输入
                output_data=output,
                execution_time_ms=execution_time_ms,
                error=error,
            )
        )


def do_something(state_dict: dict[str, Any], cfg: MyNodeConfig) -> Any:
    """<纯业务逻辑，无副作用，便于单测>。"""
    return ...


# 模块底部自注册（K5 插件路径；factory 底部 import 本模块触发注册，R4 不加内置分支）
register_node_type("my_node", MyNode)
```

### 8.1 模板要点对照

| 步骤 | 契约 | 编号 |
| --- | --- | --- |
| Config `extra="forbid"` | 节点配置拒绝未知字段 | S14 |
| `__init__` 前置校验 | 构造期即校验配置，执行推迟 | K10 |
| `build_runnable` 内 R3 三段管线 | `convert_state_to_dict` 进 / `map_output_to_state` 出 | R3/S5/S4/S3 |
| try/except 记录后重抛 | 禁止死 except | R6/H2 |
| `_log` 只记摘要 | 不含密钥与完整 state | H6/S15 |
| `wrap_runnable` | 自动打 `tags=[name]` | K4 |
| 底部 `register_node_type` | 插件路径，不改 factory 内置分支 | R4/K5 |

### 8.2 TDD 要求（R7）

- **严格 RED → GREEN → REFACTOR**：测试先于实现提交。
- 测试落位 `tests/unit/workflow/nodes/`（单元）与 `tests/integration/workflow/`（跨模块）（AD-08）。
- **零真实网络 / 零真实 LLM 调用**：用 `tests/conftest.py` 的 FakeLLM / EchoNode / 假 env 夹具；
  tenacity 退避用 `monkeypatch.setattr(tenacity.nap, "sleep", ...)` 断言退避序列 `1,2,4`，不真睡（AD-03）。
- `restore_node_registry` autouse fixture 保证注册表在测试间隔离（D7）；测试内注册的类型不泄漏到其它测试。
- 覆盖率 ≥ 80%（`uv run pytest --cov=app.workflow --cov-report=term-missing --cov-fail-under=80`）。

### 8.3 命名与结构（R8）

- 文件小（< 400 行）、函数小（< 50 行）、按领域组织、单一职责。
- 日志事件名 `lowercase_with_underscores`（如 `my_node_execution_failed`），kwargs 传参**禁 f-string**（S15/AD-02）。
- 异常保留 traceback 用 `logger.exception()` 而非 `logger.error()`。

---

## 9. 红线约束对照表（R1-R10）

引自 [02-开发规范.md §0](workflow-reimpl-plan/02-开发规范.md)（一票否决项），附对**节点开发者**的具体含义与机器检查。

| 编号 | 一句话 | 对节点开发者的含义 | 机器检查（CONTRACT §10） |
| --- | --- | --- | --- |
| **R1** | 只实现 BaseNode/LLMNode/HTTPNode，禁止新增**内置**节点类型或领域逻辑（carve-out：`python` 与 `subworkflow` 均为 K5 插件类型，不入 `NodeType`、不加 factory 内置分支，见 §5.5 / §6） | 本期节点范围冻结；确需新内置类型走 CONTRACT §11 变更流程，不得夹带领域逻辑 | `ls app/workflow/nodes/` 对白名单；`NodeType` 成员数守护测试（**恰 2，不因 python/subworkflow 改变**） |
| **R2** | reducer 只来自 YAML 显式声明，禁止硬编码业务字段名 | 节点 `func` 内**不得**出现 `circle_`/`planner_`/`worker_`/`reflector_`/`current_node` 等领域字面量 | `grep -rnE "circle_\|planner_\|worker_\|reflector_\|current_node" app/workflow/` 零命中；`test_no_hardcoded_field_names` |
| **R3** | 节点 `convert_state_to_dict` 进 / `map_output_to_state` 出，禁止 mutate 输入 state | 见 §2.4；`func` 只读使用 state_dict，输出经 `map_output_to_state` | 节点 func 代码审查；R3 进出管线契约测试 |
| **R4** | 新节点必须经 `register_node_type` 注册，`create_node` 内置分支恰好 2 个 | 见 §7.3；禁止在 factory 堆 if/elif | `test_builtin_branch_count_is_two`；`grep -n "elif" app/workflow/nodes/factory.py` |
| **R5** | 密钥 env-only，禁止硬编码，禁止完整 state/密钥写日志 | Config 无明文密钥字段；`_log` 只记摘要 | `grep -rniE "(api[_-]?key\|token\|secret\|password)\s*[:=]" app/workflow/ tests/` 人工确认；`test_execution_log_no_secret_leak`（LLM/HTTP 各一） |
| **R6** | 禁止死 try/except，错误必须显式处理 | `func` 的 except 分支必须记录后重抛或降级（且有测试） | ruff `BLE`；`grep -rn "except.*:\s*pass" app/workflow/` 零命中 |
| **R7** | 严格 TDD，覆盖率 ≥ 80% | 见 §8.2；测试先行，零真实网络/LLM | `uv run pytest --cov=app.workflow --cov-report=term-missing --cov-fail-under=80` |
| **R8** | 小文件（< 400 行）、小函数（< 50 行）、单一职责 | 业务逻辑抽为纯函数（如 `do_something`）便于单测 | `wc -l app/workflow/**/*.py`；review 抽查 |
| **R9** | pydantic v2 全量类型标注，边界处校验 fail fast | Config 用 pydantic 模型；`__init__` 边界校验 | `make typecheck`（pyright standard）零错误；ruff `D` docstring 规则 |
| **R10** | 任何缓存必须有上限 + 失效 + 开关（本期默认无缓存） | 节点**禁止**模块级 dict 缓存 / `lru_cache`；跨运行复用由宿主组装点显式持有并构造传入 | `grep -rn "cache\|lru_cache" app/workflow/` 零命中 |
| **R-EXP** | 1.x API 未探索闭环禁止编码 | 涉及 langgraph/langchain 1.x 行为的节点逻辑，对应 EXP 项须先在 `api-exploration-1x.md` 闭环 | `api-exploration-1x.md` 全部 EXP 项"实测结果"非空 |

### 9.1 节点相关守护测试（CONTRACT §10）

| 守护测试 | 守护对象 | 落点 |
| --- | --- | --- |
| `test_builtin_branch_count_is_two` | R4（factory 内置分支数==2） | spec-03 |
| `test_runnable_tags` | K4（`with_config(tags=[name])`） | spec-04 |
| `test_execution_log_no_secret_leak`（LLM/HTTP 各一） | H6/R5 | spec-04/05 |
| `test_registry_restore_fixture` | D7（注册表测试隔离） | spec-03 |
| `NodeType` 成员数断言（恰 2 个） | R1/C8 | spec-01 |

---

## 10. 相关文档

- 编码契约（接口冻结 §4.4/§4.5/§4.7/§4.8）：[spec/CONTRACT.md](workflow-reimpl-plan/spec/CONTRACT.md)
- 开发规范（R1-R10 正反例全文）：[02-开发规范.md](workflow-reimpl-plan/02-开发规范.md)
- 隐患修复方案（H1-H7）：[03-隐患修复方案.md](workflow-reimpl-plan/03-隐患修复方案.md)
- 架构总览（K1-K10）：[00-架构总览.md](workflow-reimpl-plan/00-架构总览.md)
- 手动测试指南：[workflow-manual-testing.md](workflow-manual-testing.md)
- API 与 Trace 支持方案：[workflow-api-and-trace.md](workflow-api-and-trace.md)
- README 扩展指南（自定义节点最小示例）：[README.md#扩展指南自定义节点类型](../README.md#扩展指南自定义节点类型)
