# spec-01 · 契约变更：Python 节点真沙箱执行（S22）+ S18 白名单开放 `python`

| 里程碑 | 端 | 人日 | 依赖 | 契约编号 |
| --- | --- | --- | --- | --- |
| M0 | 后端（文档） | 0.5 | 无 | **S22**（新增）、**S18**（修订）、§2.1（白名单 +2 模块）、§4.14（新增）、§5（规范化）、§8 R1（carve-out 措辞） |

> 本文档为**纯契约变更**，不含代码实现。按 CONTRACT §11「禁止先改代码后补契约」要求先行落地。

## 1. 动机

引擎已附带 `PythonNode`（`app/workflow/nodes/python_node.py`，K5 插件路径、模块底部自注册、有单测），但它的执行语义是**进程内、非沙箱**：`code` 模式把 YAML 里的字符串包成 `def __python_node_fn(state):` 后直接 `exec`（`python_node.py:98-108`，带 `# noqa: S102 — trusted repository-owned code by design`），`entry` 模式用 `importlib.import_module` 加载仓库函数。这套设计的前提是「代码只能来自受信的仓库自有 YAML」。

而 spec-16 之后，工作流定义可以经 `PUT /api/v1/workflows/{id}` 由前端设计器写入并落盘（S17）。于是 S18 用**节点类型白名单**把 `python` 挡在 HTTP 之外：

> `ALLOWED_NODE_TYPES = frozenset({"llm", "http"})`（`api.py:117`）；S18 原文「拒绝 `python`（S15 非沙箱 RCE，经 HTTP 注册等价远程代码执行）」。

这个判断在当时是对的——非沙箱 `exec` 经 HTTP 注册就是 RCE。但代价是：**设计器里画不出 python 节点**，用户要做一点数据加工（字符串处理、列表聚合、字段改名）就只能塞进 LLM 提示词或外部 HTTP 服务。S18 挡住的是实现方式（非沙箱），不是节点类型本身的价值。

目标：给 `python` 节点一条**真沙箱**执行路径，使 S18 可以安全地把它放进白名单——既保住「经 HTTP 注册不可等于 RCE」这条安全底线，又放开数据加工能力。

## 2. 影响面

### 2.1 §2.1 文件白名单（**新增 2 个模块**）

| 位置 | 变更 |
| --- | --- |
| `app/workflow/sandbox.py` | **新增**：AST 预检 + 子进程沙箱执行器（`validate_code_ast` / `SandboxLimits` / `run_sandboxed`），< 400 行（R8） |
| `app/workflow/sandbox_worker.py` | **新增**：子进程入口，受限命名空间执行用户代码，单 JSON 文档协议，< 400 行（R8）。**非 importable API**：只被 `sandbox.py` 以 `sys.executable -I <path>` 拉起，不进入任何模块的 import 图 |

§2.1 模块计数相应从 15 调整为 22。除 `python_node.py` 外，`auth.py` / `ports.py` / `security.py` / `store.py`
也早已随 spec-17..20 落地却漏记于白名单（文档漂移），本次一并补录并加「计数说明」脚注。补录不改变任何代码，
只使白名单与实际仓库一致。

### 2.2 §4.14 新增接口冻结：`app/workflow/sandbox.py`

```python
def validate_code_ast(code: str) -> None:
    """静态拒绝不可沙箱化的代码；通过则返回 None，否则抛 WorkflowValidationError。

    规则（命中即拒，消息含**行号 + 规则名**，绝不含代码正文，H6）：
      1. `import` / `from ... import`（ast.Import / ast.ImportFrom）——全禁，无白名单模块
      2. 危险调用名（ast.Call.func 为 ast.Name 且 id ∈ _FORBIDDEN_CALLS）：
         exec / eval / compile / open / __import__ / globals / locals / vars / dir /
         getattr / setattr / delattr / type / input / breakpoint / exit / quit / help
      3. dunder 标识符：任何 ast.Name.id 或 ast.Attribute.attr 含 `__`
         （封死 `().__class__.__bases__.__subclasses__()` 这类内省逃逸链）
      4. 体积 > _MAX_CODE_BYTES（64 KiB）
    """

@dataclass(frozen=True)
class SandboxLimits:
    """沙箱资源上限（由 worker 逐项自我施加，失败项经 applied/refused 回报，见 §5 残留风险 1）。"""

    timeout_s: float = 10.0
    max_memory_mb: int = 256
    max_output_bytes: int = 1_000_000

def run_sandboxed(code: str, state: dict[str, Any], limits: SandboxLimits = SandboxLimits()) -> dict[str, Any]:
    """在子进程沙箱中执行 ``code``，返回其 dict 结果。

    执行前**复跑** validate_code_ast（纵深防御：调用方可能绕过注册期校验）。
    失败一律抛 PythonNodeError（超时 / 非零退出 / worker 报 ok=false / 输出非 dict）。
    """
```

`sandbox_worker.py` 的**线上协议**冻结在 S22（属行为语义，非可 import 签名）。

### 2.3 §4.7 类比：`PythonNodeConfig` 新增字段

| 位置 | 变更 |
| --- | --- |
| `app/workflow/nodes/python_node.py` · `PythonNodeConfig` | 新增 `sandboxed: bool = False`。**互斥校验扩展**：`sandboxed=True` 时只允许 `code` 模式，`entry` + `sandboxed=True` → pydantic `ValidationError`（`sandboxed` 对 `entry` 无意义，静默忽略比报错更危险） |
| `PythonNode._run_inline_code` | `cfg.sandboxed` 为真 → 走 `run_sandboxed(code, state_dict)`；为假 → 现有进程内 `exec` 路径**逐字不变**（受信仓库 YAML 向后兼容，`extra="forbid"` 不变） |
| `PythonNode._log` | `input_data` 摘要新增 `"sandboxed": bool`。**仍绝不记代码正文**（H6/S15），`code` 模式摘要保持 `{"mode","code_chars"}` 形态 |

`entry` 模式执行路径、`_ensure_dict`、自注册语句、R3 进出管线均不变。

### 2.4 §6 行为语义

**S18（修订）— 节点类型 API 白名单**

| 项 | 原文 | 修订后 |
| --- | --- | --- |
| 白名单集合 | `node.type ∈ {llm, http}` | `node.type ∈ {llm, http, python}` |
| `python` 处置 | 一律拒绝（S15 非沙箱 RCE） | **有条件接受**，三条全部满足方可通过注册期校验 |
| 未知类型 | 拒绝 → 422 | 不变 |

`python` 节点的注册期条件（`PUT` 服务端强制，前端 palette 只是体验层，**安全边界始终在后端**）：

1. **只允许 `code` 模式**：`config` 含 `entry` → 拒绝（`entry` 可 `importlib` 加载任意仓库模块，无法沙箱化）；
2. **AST 预检通过**：`validate_code_ast(config["code"])`，失败 → HTTP 422（S6 构建期校验优先），`message` 携**行号 + 规则名**、不携代码正文（H6）；
3. **服务端强制 `sandboxed=true`**：忽略客户端传入值并覆写为 `true`，落盘 YAML 与注册进 registry 的定义中该字段恒为 `true`。理由：`sandboxed` 是**安全属性**而非用户偏好，交给客户端等于把 RCE 开关暴露给请求方。

`ALLOWED_NODE_TYPES` 仍为 `frozenset`，限流键复用 `workflows_save`（无新端点）。

**S22（新增）— 沙箱执行**

`sandboxed=true` 的 python 节点，其代码**必须**在子进程中执行，禁止任何形式的进程内 `exec`：

| 维度 | 冻结约定 |
| --- | --- |
| 进程隔离 | `subprocess.run([sys.executable, "-I", <sandbox_worker.py 绝对路径>], ...)`。`-I`（isolated）忽略 `PYTHON*` 环境变量与 user site-packages，防止宿主路径污染沙箱 |
| 通信协议 | stdin/stdout 各**恰好一个** JSON 文档。入：`{"code": str, "state": dict, "limits": {"timeout_s": float, "max_memory_mb": int, "max_output_bytes": int}}`；出：`{"ok": true, "output": dict, "limits": {"applied": [str], "refused": [str]}}` 或 `{"ok": false, "error": str, "limits": {...}}`。worker 内**替换 `sys.stdout`** 为丢弃器，用户代码的 `print` 不得污染协议流；结果经 `os.write(1, ...)` 单次写出 |
| 退出码 | `0` = 正常（含 `ok=false` 的业务失败）；`2` = SyntaxError；`3` = 运行期异常。非零退出 → 父进程包 `PythonNodeError` |
| 超时 | `subprocess.run(timeout=limits.timeout_s)`，到期由 stdlib kill 子进程 → `TimeoutExpired` → `PythonNodeError`。**禁止无限等待** |
| 资源上限 | rlimit 由 **worker 自我施加**（解释器启动完成、`exec` 用户代码之前），按 stdin `limits` 依次设 `RLIMIT_AS`（= `max_memory_mb`）、`RLIMIT_CPU`（= `ceil(timeout_s)`）、`RLIMIT_FSIZE = 0`（禁写任何文件）、`RLIMIT_NPROC = 0`（禁 fork 子进程）。**逐项独立容错**：单项失败记入 `refused` 并继续，不阻断执行；父进程**禁用 `preexec_fn`**，`refused` 非空 → `logger.warning("sandbox_rlimit_partial", refused=..., platform=...)`。见 §5 实测修订记录 |
| 环境变量 | 子进程 `env={"PATH": "/usr/bin:/bin"}`。**不继承宿主环境**——API 密钥、DB DSN 一律不进沙箱（H6） |
| 内建白名单 | worker 以 `SAFE_BUILTINS` 作为 `__builtins__`：仅纯计算类（`len/range/sorted/min/max/sum/abs/round/divmod/pow`、`str/int/float/bool/list/dict/set/tuple/frozenset/bytes`、`enumerate/zip/map/filter/reversed/isinstance/issubclass/print`、`chr/ord/hex/oct/bin/format/repr`、`True/False/None`）+ 常用异常类（`Exception/ValueError/TypeError/KeyError/IndexError/AttributeError/ZeroDivisionError/ArithmeticError/LookupError/RuntimeError/StopIteration/NotImplementedError`）。**不含** `open/exec/eval/compile/__import__/globals/locals/vars/dir/getattr/setattr/delattr/type/input/breakpoint/exit/quit/help` |
| 输出上限 | 父进程校验 `len(stdout) <= limits.max_output_bytes`，超限 → `PythonNodeError`（见 §5 残留风险 3） |
| 日志 | 只记摘要（`sandboxed` 布尔、代码长度、退出码、耗时），**绝不记代码正文与完整 state**（H6/S15）；事件名 lowercase_with_underscores，kwargs 传参禁 f-string |

S22 与 S21 一样对**所有**工作流生效，不按节点类型特判输入（R2）；`sandboxed=false` 的 YAML（既有受信仓库路径）行为逐字不变。

### 2.5 §5 异常族规范化（消除既有漂移）

§5 明文「单点定义于 `app/workflow/models.py`；其它模块与文档只引用，**不得各自另行定义**」，但现状有两处偏离：

1. `PythonNodeError` 实际已在 `models.py:45`，却未列入 §5 冻结块；
2. `WorkflowValidationError` 定义在 `app/workflow/security.py:15`（spec-20 SSRF 引入），违反单点规则。

本次将 `WorkflowValidationError` **迁入 `models.py`** 并改挂 `WorkflowEngineError` 基类，`security.py` / `api.py` /
`tests/unit/workflow/test_http_ssrf.py` 改为从 `models.py` 导入（共 4 处 import 调整），并把 `PythonNodeError`、
`WorkflowValidationError` 一并补入 §5 冻结块与场景映射表。

**可观测影响（已逐处核对）**：HTTP 状态码全部不变——`PUT` 注册期校验仍由 `api.py:390` 的
`except WorkflowValidationError` 命中 → 422（`api.py:406` 的 `except (ValueError, WorkflowEngineError)` 只包
`register_workflow`，而 SSRF/AST 校验都在前一个 try 内，不会被误分类）；`execute` 端点的 catch-all 仍 → 500。
唯一差异在 CLI：执行期 SSRF 拦截（`http_node.py:211`）原落入 `except Exception` 打印
`unexpected error while running ...`，改挂基类后落入 `except WorkflowEngineError` 打印 `workflow engine error for ...`，
**退出码同为 1**，且无测试断言该字符串。

**守护测试**：`tests/unit/workflow/test_models.py::test_exception_hierarchy` 现为正向列表（非穷举断言），
补入两个新类不会打破它；本期须**扩展**该列表以覆盖 `PythonNodeError` 与 `WorkflowValidationError`，
使「异常族单点」守护真正闭合。

**为什么本次必须做**：`sandbox.py` 的 AST 拒绝需要抛「注册期定义校验失败 → 422」这一类异常，与 SSRF 校验同族。若不先规范化，就会出现第二个模块从 `security.py`（一个 SSRF 专用模块）import 通用校验异常的怪依赖，把既有漂移扩大成结构性问题。

### 2.6 §8 R1 carve-out

R1 原文「只实现 BaseNode/LLMNode/HTTPNode，禁止新增节点类型」。`python` 是 **K5 插件类型**（任意字符串注册，factory 无内置分支），既不在 `NodeType` 枚举内（C8 刻意只含 `LLM`/`HTTP`），也不构成 R1 意义上的「新增内置节点类型」。R1 行追加 carve-out 措辞明确这一点，**守护测试不动**：

- `test_models.py` 的 `NodeType` 成员数 == 2 **不变**；
- R4 的 `create_node` 内置分支数 == 2 **不变**（`python` 走插件路径，factory 不加 `elif`）。

### 2.7 不变更项（显式声明）

- `store.py` / `main.py` / `cli.py` **零改动**：YAML 缺省 `sandboxed=false`，受信仓库路径与 CLI 行为逐字不变；组合根无需感知沙箱。
- `api.py` 无新端点、无新限流键；`python` 复用 `workflows_save`。
- `graph_builder.py` / `registry.py` / `state.py` / `factory.py` **零改动**：沙箱是 `PythonNode` 的内部执行策略，对图构建与运行时透明。
- 前端 `WorkflowExecuteDialog` / `ExecuteInputFields` / `useExecuteForm`（第 1 期成果）**零改动**：执行入参形状与 S21 合成逻辑不受影响。
- 前端**设计器侧需改**（另立任务）：`nodeCatalog.ts` 增 `python` 条目与 `DEFAULT_CONFIGS`、`NodePalette.vue` 增可拖拽项、
  `useWorkflowGraph.ts` 的 `VALID_NODE_TYPES` 扩为 `{llm,http,python}` 并补 python 的 code/entry 预校验、
  新增 `panel/PythonNodeForm.vue`。`docs/workflow-frontend-spec.md` §2.1/§5.1/§7/§8/§9/§13/§14 已在本次 `docs:` 提交中同步
  （CONTRACT §11.3：禁止只改 CONTRACT 不同步 spec）。

## 3. 备选方案对比（R-EXP 第 5 条：给 2-3 个附影响面）

| 方案 | 描述 | 影响面 | 结论 |
| --- | --- | --- | --- |
| A. 子进程 + AST 预检（本方案） | stdlib `subprocess` + `ast` + rlimit，无新依赖 | 引擎 +2 模块（各 <400 行）；`python_node` 一个分支；`api.py` 白名单 +3 行校验。可测（AST 纯函数；超时/内存走 integration）。每次执行 ~30-60ms 进程启动开销 | **采纳** |
| B. 进程内受限 `exec`（仅 AST + 受限 builtins，不起子进程） | 沿用 `_run_inline_code`，只换命名空间 | 零启动开销，改动最小。但**无超时**（死循环挂住 worker 线程，需另起线程 + 无法安全 kill）、**无内存上限**（`[0]*10**10` 直接 OOM 拖垮 API 进程）、共享地址空间（宿主对象一旦泄漏即失守）。安全等级不足以支撑「经 HTTP 注册」 | 否决 |
| C. 第三方沙箱（langchain-sandbox / Deno / WASM 运行时 / gVisor） | 引入外部运行时 | `docs/workflow-node-development.md:279` 已记录 langchain-sandbox 被否决（失维护、Deno 运行时、每次调用启动延迟）。WASM 需新依赖链与 Python 解释器编译产物；gVisor 属部署层（可与 A 叠加，非互斥）。均超出本期范围 | 否决（本期） |

**A 的定位**：stdlib-only、可被单测与 integration 覆盖、失败模式明确（超时 kill / rlimit / AST 拒绝）。它不是「绝对安全」，而是「把经 HTTP 注册的代码从 *等价 RCE* 降到 *受限于纯计算 + 有限资源*」，与 S18 的三条注册期条件叠加后达到可开放的程度。生产部署仍建议叠加容器级隔离（gVisor/seccomp），见 §5 残留风险。

## 4. 验收

**守护测试修订（唯一允许改动的既有断言）**

- `tests/unit/workflow/test_api.py:628`（原「`type=python` → 422」单卡）**改写为三卡**：① `python` + `code` + 合法代码 → 接受，落盘 `sandboxed=true`；② `python` + `entry` → 422；③ 客户端传 `sandboxed=false` → 服务端覆写为 `true`。另加一卡：AST 非法（如 `import os`）→ 422 且 `message` 含规则名、**不含代码正文**。
- `tests/unit/workflow/test_models.py::test_exception_hierarchy`：正向列表**扩展**至含 `PythonNodeError` 与
  `WorkflowValidationError`（§2.5）。
- `tests/unit/workflow/test_models.py` 的 `NodeType` 成员数 == 2、R4 内置分支 == 2：**不动**（§2.6）。

**`api.py` 错误分支泛化（随 S18 修订必做）**

`api.py:390` 现为 `except WorkflowValidationError` → 日志事件 `api_save_workflow_ssrf_blocked`、message 前缀
`"SSRF guard: {exc}"`。该异常自本期起承载**两类**原因（SSRF + 沙箱 AST），若不改，AST 拒绝会被误标为 SSRF：
日志事件须改为原因中立名（如 `api_save_workflow_definition_rejected`），message 前缀改为
`"definition rejected: {exc}"`（异常自身消息已含 scheme/规则名，足以区分）。**仍不得回显代码正文**（H6）。

**新增测试**

- `tests/unit/workflow/test_sandbox.py`：AST 拒 `import` / dunder / `open`+`exec` / 超长；接受纯计算代码；`run_sandboxed` 返回 output；运行期异常包 `PythonNodeError`；输出非 dict 报错；无网络双断言（沙箱内既无 `socket` 也无 import 能力）。**rlimit 修订项**：断言父进程调用 kwargs **不含 `preexec_fn`**；stdin 文档含 `limits`（`timeout_s`/`max_memory_mb`/`max_output_bytes`）；worker 回报 `refused` 非空 → `sandbox_rlimit_partial` warning，`refused` 为空 → 不告警。超时 kill 与内存超限标 `integration`（依赖真实子进程与 rlimit）。
- `tests/unit/workflow/nodes/test_python_node.py` 追加：`sandboxed=true` 路由到 `run_sandboxed`（monkeypatch 计数）；`sandboxed=false` 仍走进程内 `exec`（回归保护）；`entry` + `sandboxed=true` → `ValidationError`；日志摘要含 `sandboxed` 且不含代码正文。

**门禁**：`uv run pytest -m unit`、`uv run pytest -m integration`、`make lint`、`make typecheck` 全绿；前端 `npm run type-check`、`npm test`、`npm run build` 全绿。

**E2E**：docker-up 全栈 → 管理员登录 → 画布拖 python 节点，代码 `return {"upper": state["input"].upper()}` → PUT 200 → 简单模式执行 → 轨迹抽屉摘要含 `sandboxed: true` 且输出正确。反例：`import os` → PUT 422，`message` 含规则名不含代码体；非管理员 → 403。

## 5. 残留风险与 open questions

1. **~~`RLIMIT_AS` 在 macOS 常被忽略~~ → 实测推翻，已按 §7.5 修订契约**（2026-09-14，darwin 25.3.0 / arm64 / CPython 3.13）：`resource.setrlimit(RLIMIT_AS, (64MB, 64MB))` **抛 `ValueError: current limit exceeds maximum limit`**（当前软硬限均为 `INT64_MAX`），不是「被忽略」。原设计把施加放进父进程 `preexec_fn`，而 `preexec_fn` 内抛异常会让 `subprocess.run` 整体失败（`SubprocessError: Exception occurred in preexec_fn`）——后果是 macOS 上**每一次**沙箱调用都失败，而不只是内存上限失效。`RLIMIT_CPU` / `RLIMIT_FSIZE` 可施加；`RLIMIT_NPROC` 当前 `(2666, 4000)`，降至 `(0,0)` 可施加。
   **修订后契约**：rlimit 改由 **worker 自我施加**（解释器启动完成、`exec` 用户代码之前），逐项独立容错，施加结果以 `applied`/`refused` 回报在 stdout 文档中；父进程**禁用 `preexec_fn`**，`refused` 非空时 `logger.warning("sandbox_rlimit_partial", refused=..., platform=...)`。实测 worker 输出：`{"applied": ["RLIMIT_CPU","RLIMIT_FSIZE","RLIMIT_NPROC"], "refused": ["RLIMIT_AS:ValueError"], "ok": true, "output": {...}}`。
   **残余**：macOS 开发机上仍无内存硬上限（平台限制，不可绕开）；Linux 生产容器四项均可施加。超时 kill 是跨平台兜底。
2. **~~`preexec_fn` 在多线程进程中非线程安全~~ → 风险已消除**：修订后父进程不再传 `preexec_fn`，多线程宿主（FastAPI）下的 fork-exec 安全隐患随之消失。原「窗口极窄、已知且被接受」的论证不再需要。备选方案对比见 CONTRACT §11 变更记录 2026-09-14 第二行。
3. **`max_output_bytes` 为捕获后校验，非流式限流**：`subprocess.run(capture_output=True)` 会先把 stdout 全量读进父进程内存再判长度。子进程的输出量受 `RLIMIT_AS` 与 `timeout_s` 双重约束，但 `RLIMIT_AS` 在 macOS 上施加失败（见风险 1），故该双重约束**仅在 Linux 生产成立**；且父进程侧的峰值内存始终未被硬性封顶。彻底解决需流式读 + 超限即 kill，列为后续迭代项。
4. **「无网络」不是硬保证**：stdlib 无法做 seccomp/namespace 级 syscall 过滤。本期防线是**能力剥夺**——AST 全禁 import + builtins 无 `__import__`/`open`/`getattr` + 空 env，使沙箱内代码在语言层面**拿不到** `socket`/`urllib`/`http.client` 任何入口。残余风险（例如未来若放开某个携 I/O 能力的 builtin）记录在案；生产建议叠加容器网络策略。
5. **dunder 规则只作用于标识符，不作用于字符串常量**：`getattr(obj, "__class__")` 里的 `"__class__"` 是字符串字面量，规则 3 拦不住。因此 `getattr`/`setattr`/`delattr`/`vars`/`dir`/`type` 被**同时**从 AST 允许名单（规则 2）与 `SAFE_BUILTINS` 中剔除——两道防线互为冗余，缺一不可。
6. **`sandboxed=false` 的 YAML 仍是进程内 `exec`**：这是受信仓库路径的向后兼容前提（`examples/` 与既有用户 YAML）。它的安全性依赖「能写这些文件的人已经能改代码」这一既有信任边界，**不因本期变更而改变**；S18 只保证经 HTTP 进入的定义必为 `sandboxed=true`。
7. **dunder 规则（规则 3）只有一道防线，且实测确认**：直接拉起 worker（绕过 AST 预检）执行
   `return {"x": str(().__class__.__bases__)}` → `{"ok": true, "output": {"x": "(<class 'object'>,)"}}`。
   即**属性式** dunder 内省在沙箱内可正常解析——`SAFE_BUILTINS` 白名单只能剥夺*名字*，剥夺不了属性访问语法。
   因此规则 1（import）与规则 2（危险调用）各有两道防线（AST + 无 `__import__`/`getattr`），
   而规则 3 **只有 AST 预检一道**；一旦被绕过，`().__class__.__bases__[0].__subclasses__()` 这类经典逃逸链在语言层面是可达的。
   **实际暴露面**：`validate_code_ast` 在注册期（S18）与每次 `run_sandboxed` 生成子进程前**各跑一次**，
   要绕过必须能直接以 `sys.executable -I sandbox_worker.py` 拉起子进程——而此时攻击者已能在宿主上执行任意命令，沙箱已非边界。
   **补第二道防线的代价**（本期不做）：AST 层改写属性访问（插入运行时守卫）会破坏「代码逐字执行」的可预期性；
   或改用 `__class_getitem__`/audit hook（`sys.addaudithook` 可拦 `object.__getattribute__`，但开销与误伤面均需评估）。
