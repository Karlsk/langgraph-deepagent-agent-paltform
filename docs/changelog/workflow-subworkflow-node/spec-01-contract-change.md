# spec-01 · 契约变更：子工作流（嵌套）节点 + S23 嵌套守护 + S24 内层日志合并

| 里程碑 | 端 | 人日 | 依赖 | 契约编号 |
| --- | --- | --- | --- | --- |
| M0 | 后端（文档） | 0.5 | 第 1、2 期已合并 | **S23**（新增）、**S24**（新增）、**S18**（修订）、§2.1（白名单 +1 模块）、§2.2（解禁令注解）、§3（红线 4 补充）、§4.5/§4.9/§4.10（可选参链）、**§4.15**（新增）、§5（新异常）、§8 R1（carve-out 扩展） |

> 本文档为**纯契约变更**，不含代码实现。按 CONTRACT §11「禁止先改代码后补契约」要求先行落地。

## 1. 动机

工作流目前无法组合：要把「告警分诊」复用到「值班巡检」里，只能把 YAML 复制一遍。复制的代价是
两份定义各自漂移——改一处忘另一处，正是 H 系列隐患最常见的来源。

嵌套能力在初版被**明确 defer**，理由记录在 `00-架构总览.md:117`：

- **H5（构建期快照）**：`GraphBuilder` 若在构建期持有 registry 引用，编译出的图会钉死当时的注册表快照，
  之后注册/删除的工作流对已编译图不可见，且形成 `nodes/* → registry` 的反向依赖（§3 红线 2 明令禁止）。
- **H3（日志收集）**：内层运行若与外层共用一个 collector，两层日志会交错且无法区分来源；若各自收集而不合并，
  外层轨迹就会缺一段，运维看不出内层发生了什么。

这两个隐患的根因都是「节点直接认识注册表」。解法不是放弃嵌套，而是**把能力以不透明 callable 的形式
沿构造链注入**——与 S20 的 `ChatModelFactory` 完全同款：节点只持有一个 `Callable`，不知道它背后是
registry、是测试替身、还是远端服务。H5 因此不成立（无快照、无反向依赖）。

H3 则由 S24 正面解决：内层持独立 collector，完成后按 `"{caller}/"` 前缀逐条并入外层。

`03-隐患修复方案.md:184` 已为可重入预留了 registry 的 per-id `RLock`（同线程可重入），但**没有环检测、
没有深度上限**。缺这两道守护时，一个自引用的 YAML 会让 `execute_workflow` 无限递归直到 `RecursionError`
——而 `RecursionError` 不是 `WorkflowEngineError` 家族成员，会穿透 CLI 的 `except WorkflowEngineError`
分支落到 catch-all，运维只看到「unexpected error」。S23 补上这两道守护，并把失败收进异常族。

目标：让工作流可以引用工作流，同时保证**引用图必须是有限无环的**，且**外层轨迹完整可读**。

## 2. 影响面

### 2.1 §2.1 文件白名单（**新增 1 个模块**）

| 位置 | 变更 |
| --- | --- |
| `app/workflow/nodes/subworkflow_node.py` | **新增**：`SubWorkflowNodeConfig` + `SubWorkflowNode`，K5 插件路径（模块底部 `register_node_type("subworkflow", ...)`，factory **无**内置分支），< 250 行（R8）。R3 出口以 **`dual_write=False`** 调用 `map_output_to_state`（理由见 §2.4） |

§2.1 模块计数 22 → **23**。`nodes/__init__.py` 导出行同步加 `SubWorkflowNode`。

### 2.2 §2.2 禁止项解禁令（**注解，非删除**）

§2.2 第 2 条原文含「子图嵌套」。本次**不删除该条目**，而是加限定注解：

> 子图嵌套（**指 langgraph 原生 subgraph / `langgraph.types.Send` 并行扇出**）仍禁止。
> 本期的 `subworkflow` 节点是 **registry 注入式**嵌套：节点经不透明 `WorkflowRunner` callable
> 回调 `WorkflowRegistry.execute_workflow`，编译产物仍是扁平 `StateGraph`，不使用 langgraph 的
> subgraph 编译能力。二者机制不同，禁止前者不等于禁止后者。

**为什么必须写清**：若不解注，实现者要么以为自己在违规（不敢写），要么顺手用了 langgraph subgraph
（真的违规，且会把 H5 的快照问题带回来）。`Send` 并行扇出本期**依然不做**——并行嵌套的日志合并
与深度计数语义都要另立契约。

### 2.3 §3 红线 4 补充（不透明 callable 的第二例）

红线 4 现文已写明「宿主经构造参数注入的**不透明 callable**（如 `ChatModelFactory`）不构成依赖」。
本次补充 `WorkflowRunner` 为第二例，并明确一个**关键差异**：

| | `ChatModelFactory` | `WorkflowRunner` |
| --- | --- | --- |
| 实现方 | **宿主**（`app/main.py` 组合根装配，闭包 provider 服务） | **引擎自身**（`WorkflowRegistry._run_nested` 的 bound method） |
| 组合根改动 | spec-20 时需接线 | **零改动**——registry 自注入，`main.py:137-140` 与 `cli.py:53-73` 不动 |
| 为何仍需 ports 别名 | 让 `nodes/*` 能标注类型而不 import 宿主 | 让 `nodes/*` 能标注类型而不 import `registry`（§3 红线 2） |

`WorkflowRunner` 放 `ports.py` 的理由与 `ChatModelFactory` 相同：**红线 2 禁止 `nodes/*` import
`registry`/`graph_builder`**，节点要回调注册表就只能持有一个类型别名标注的 callable。

### 2.4 §4.15 新增接口冻结：`app/workflow/nodes/subworkflow_node.py`

```python
# app/workflow/ports.py 新增（引擎内部端口，零 app.* 依赖）
WorkflowRunner = Callable[[str, dict[str, Any], str], dict[str, Any]]
"""(workflow_id, input_data, caller_label) -> 内层运行结果信封（冻结，恰三键）。

    {"output": dict[str, Any], "run_id": str, "inner_log_count": int}

第三参 caller_label 是发起调用的节点名，供被调方做日志前缀（S24）。
`output` 是内层 RunResult.output；另两键供调用方构造 S24 摘要——它们只存在于内层
RunResult 上，节点自己无从得知，故必须由 runner 一并回传（见下方「信封歧义澄清」）。
引擎不感知其实现——生产为 WorkflowRegistry._run_nested 的 bound method，测试为 FakeRunner。
"""

class SubWorkflowNodeConfig(BaseModel, extra="forbid"):
    """引用另一个已注册工作流（S14 forbid extras）。"""

    workflow_id: str            # 非空；被引用工作流的存在性是**运行期**检查（注册期只校验结构）
    input_map: dict[str, str] = {}   # {内层 state 键: 外层 state 路径}，S7 点路径语法
    inherit_input: bool = False      # 为真时先把外层 state 全量传给内层，再叠加 input_map 结果

class SubWorkflowNode(BaseNode):
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

**执行语义（R3 管线不变）**：`convert_state_to_dict` 进 → 组装内层输入（`inherit_input` 全量 +
`input_map` 覆盖，后者优先）→ `workflow_runner(workflow_id, inner_input, self.name)` 得三键信封 →
**只取 `envelope["output"]`** 交 `map_output_to_state(..., dual_write=False)` 出 → 节点自身记 S24 摘要。

**`dual_write=False` 是本节点对 R3 出口的唯一偏离，且是必要的**（写测试时发现，已回填契约）：
runner 回传的是内层的**整个最终 state**（`input`、各 `{node}_result`、`history`…），不是「一个节点的输出」。
按默认平铺会让内层 channel **静默覆写**外层同名 channel——最典型的是 `input`：**每个**工作流都声明 `input`，
内层的 `input`（可能只是声明默认值）会盖掉外层真正的 `input`，外层后续节点全部读到错值且**无任何报错**。
`history` 同理。关掉平铺后数据不丢失：外层经 `sub_1_result.<path>`（S7 点路径）读取，条件边与 `{...}`
模板均可引用。

`workflow_runner` 为 `None` → **`ConfigError`**，**在 `__init__` 抛出**（时机已冻结，见下方澄清）。
处置口径同 S20 分支②：**不静默降级**、不返回空 dict——静默降级会让「忘了注入 runner」表现为
「子工作流什么都没做」，比直接失败难查得多。

**信封歧义澄清（编码前发现并回填契约）**：初稿把 `WorkflowRunner` 返回值写作「内层运行的输出摘要」，
而 S24 冻结的节点摘要含 `run_id` 与 `inner_log_count`——若 runner 只回传裸 output dict，这两个字段
节点**无从获得**（它们只存在于内层 `RunResult` 上）。两处口径互相矛盾，故按 §11 先行澄清而非在实现里
自行取舍：返回值定为**三键结果信封**，节点只把 `output` 交给 `map_output_to_state`（meta 键不得污染
外层 state），其余两键用于摘要。类型别名 `Callable[..., dict[str, Any]]` 不变，故**未触碰已冻结签名**，
只是把语义写死。

**ConfigError 时机澄清**：初稿只写「→ `ConfigError`」未定时机。定为 **`__init__` 抛出**，依据 S6
「构建期校验优先」——构造期抛出使装配错误落在 `register_workflow` → `api.py` 的
`except (ValueError, WorkflowEngineError)` → **422 build-time error**；若推迟到执行期，同样的装配错误
只能表现为运行期 **500**，排查成本高一个量级。

### 2.5 §4.5 / §4.9 / §4.10 可选参链式透传（S20 同款形态）

| 位置 | 变更 |
| --- | --- |
| `create_node(definition, operator_log=None, chat_model_factory=None, **workflow_runner=None**)` | 新增第 4 个可选参。插件分支用 **`inspect.signature` 探测**目标类 `__init__` 是否接受 `workflow_runner` kwarg，接受才传（见 §3 拍板结论）。**内置分支恒 2 个不变**（R4），llm/http 不受影响 |
| `GraphBuilder.__init__(*, no_match_policy="raise", chat_model_factory=None, **workflow_runner=None**)` | 新增可选参，`_add_nodes` 透传给 `create_node`。**构造器仍无 registry 参数**（H5 签名形态防线不破） |
| `WorkflowRegistry.__init__(*, no_match_policy="raise", chat_model_factory=None, **max_nesting_depth: int = 3**)` | 新增深度上限。registry 以 **bound method `self._run_nested`** 作为 `workflow_runner` 构造 `GraphBuilder` → **自注入，组合根零改动** |
| `WorkflowRegistry._run_nested(workflow_id, input_data, caller_label) -> dict[str, Any]`（**新增私有方法**） | 栈检查（S23）→ 递归 `self.execute_workflow`（自动获得 per-id RLock、独立 collector、S21 输入合成）→ 前缀化内层日志并入外层 collector（S24）→ 返回输出摘要 |
| `registry.py` 模块级 `_RUN_STACK: ContextVar[tuple[str, ...]]`（**新增**，default `()`） | 运行栈。`execute_workflow` 进入时 `set(get() + (workflow_id,))`，`finally` 中 `reset(token)`——严格遵循 S11 的配对纪律，ContextVar 永不泄漏 |

`§4.10` 的 `execute_workflow` **签名不变**（守护冻结面）；变化全在其内部与新增私有方法。

### 2.6 §5 新增异常

```python
class NestedWorkflowError(WorkflowEngineError):
    """嵌套执行守护触发：引用环或超过 max_nesting_depth（S23）。消息含完整运行栈。"""
```

**场景映射新增行**：

| 场景 | 异常类型 | 落实 spec |
| --- | --- | --- |
| `subworkflow` 节点引用环（含自引用 A→A） | `NestedWorkflowError`（消息含完整栈，如 `a -> b -> a`） | S23 |
| 嵌套深度超过 `max_nesting_depth` | `NestedWorkflowError`（消息含栈与上限值） | S23 |
| `subworkflow` 节点未注入 `workflow_runner` | `ConfigError` | §4.15 |
| 被引用 `workflow_id` 未注册 | `WorkflowNotFoundError`（**沿用既有异常**，不新增） | §4.10 |

`test_models.py::test_exception_hierarchy` 正向列表须**纳入** `NestedWorkflowError`（与 2026-09-14
纳入 `PythonNodeError`/`WorkflowValidationError` 同款守护）。

### 2.7 §6 行为语义

**S18（第二次修订）— 节点类型 API 白名单**

| 项 | 现文（2026-09-14 修订后） | 本次修订后 |
| --- | --- | --- |
| 白名单集合 | `{llm, http, python}` | `{llm, http, python, subworkflow}` |
| `subworkflow` 处置 | — | **接受，仅结构校验**：`config["workflow_id"]` 须为非空 `str`，否则 422 |
| `python` 三条注册期条件 | 不变 | 不变 |

**为什么被引用工作流的存在性不做注册期校验**：`PUT wf_outer` 可能先于 `PUT wf_inner` 到达
（前端逐个保存、脚本批量导入、或 inner 被删除后 outer 仍在盘上）。做注册期存在性校验会强制
拓扑序保存，把「顺序」变成隐性契约——比运行期报 `WorkflowNotFoundError` 更难排查。
运行期检查的代价是「保存能过、执行才失败」，由前端下拉只列已注册工作流来缓解（体验层，非边界）。

**S23（新增）— 嵌套执行守护**

| 维度 | 冻结约定 |
| --- | --- |
| 运行栈 | `registry.py` 模块级 `_RUN_STACK: ContextVar[tuple[str, ...]]`，default `()`。`execute_workflow` 进入即 `set(_RUN_STACK.get() + (workflow_id,))`，`finally` 中 `reset(token)`（S11 配对纪律，与 `_RUN_COLLECTOR` 同款） |
| 环检测 | `_run_nested` 中 `workflow_id in _RUN_STACK.get()` → `NestedWorkflowError`，消息含**完整栈**（`" -> ".join(stack + (workflow_id,))`）。同 id 嵌套（A→A 自引用）**先被环检测拒**，不会走到 RLock 重入 |
| 深度上限 | `len(_RUN_STACK.get()) >= max_nesting_depth` → `NestedWorkflowError`，消息含栈与上限值。**语义冻结：`max_nesting_depth` = 运行栈中允许同时存在的工作流数量上限（含最外层）**。默认 3 ⇒ `A→B→C` 可运行（栈深 3），`C` 再引用 `D` 被拒。深度是 **registry 级**配置，**不做 per-node 覆盖**（per-node 会让「全局最深」不可推断，守护形同虚设） |
| 死锁 | 不同 id 各持自己的 per-id `RLock`，且嵌套调用是**同线程递归**（`RLock` 可重入），故无死锁。并发场景：两个线程各跑一条不同 id 的嵌套链，锁获取顺序由各自的 YAML 引用顺序决定 → **理论上存在跨线程 AB-BA 死锁**，见 §5 残留风险 2 |
| 检查时机 | 环与深度检查都在 `_run_nested` **递归之前**，即「调用前拒绝」而非「进入后炸栈」。绝不依赖 Python 的 `RecursionError` 兜底（它不属 `WorkflowEngineError` 家族，会穿透 CLI 分类） |

**S24（新增）— 内层日志合并**

| 维度 | 冻结约定 |
| --- | --- |
| 内层收集 | 内层 `execute_workflow` 自带独立 `RunLogCollector` 并绑定 `_RUN_COLLECTOR`（S11 既有行为，**零改动**）。因是同一线程的 ContextVar 嵌套 set/reset，内层 collector 在外层看来是**临时遮蔽**，内层 `finally` reset 后外层 collector 自动恢复 |
| 前缀合并 | 内层返回后，`_run_nested` 取 `result.execution_logs`，逐条 `model_copy(update={"node_name": f"{caller_label}/{原 node_name}"})` 后 `add` 进**外层** collector（经 `get_run_collector()` 取得）。外层轨迹因此形如 `sub_1/classify`、`sub_1/fetch` |
| 多层复合 | 前缀**自然复合**：C 的日志并入 B 时成 `sub_c/xxx`，B 的日志（已含该条）并入 A 时成 `sub_b/sub_c/xxx`。无需特判层数 |
| 子节点自身 output | `SubWorkflowNode` 的 `ExecutionLog.output_data` **只记摘要** `{output_keys: [...], run_id, duration_ms, inner_log_count}`，**不内嵌内层完整输出与日志**——否则同一份数据在轨迹里出现两次（体积翻倍且前后端都要去重）。内层业务数据经 R3 出口 `map_output_to_state` 正常写入 state，不丢失 |
| 前端影响 | `WorkflowTraceDrawer` **零改动**：前缀名是普通字符串，直接展示即可读出层级 |
| H6 | 合并的是已脱敏的 `ExecutionLog`（`redact` 在 `api.py` 投影时统一做），前缀化不引入新泄漏面；`caller_label` 是节点名（YAML 自有），非用户数据 |

**S23/S24 与既有语义的关系**：内层运行**完整继承** S11（run-scoped 日志）、S12（definition
execution_history 单槽）、S21（输入合成）——因为 `_run_nested` 走的就是 `execute_workflow` 本身，
不是另写一条执行路径。这是本设计最重要的性质：**嵌套不新增执行语义，只新增守护与日志编排**。

### 2.8 §8 R1 carve-out 扩展

R1 的 2026-09-14 carve-out 已为 `python` 开口。本次**同款扩展**至 `subworkflow`：

> `subworkflow` 亦是 K5 **插件类型**（`register_node_type` 注册、factory 无内置分支、不入 `NodeType` 枚举 C8），
> 不构成 R1 意义上的新增内置节点类型；其 API 可达性由 S18 结构校验约束，其执行安全由 S23 守护约束。

**守护测试全部不动**：

- `test_models.py` 的 `NodeType` 成员数 == 2 **不变**（C8：枚举刻意只含 `LLM`/`HTTP`）；
- R4 的 `create_node` 内置分支数 == 2 **不变**（`subworkflow` 走插件路径，factory 不加 `elif`/`if` 类型分支）。

### 2.9 实现期回填的两处精确化

1. **§2.1 `registry.py` 行数注记 `< 350` → `< 400`（= R8 红线上限）**：350 是该模块更小时的估值，
   S21 已加入 `synthesize_run_input`，S23/S24 再加入 `_RUN_STACK` 与 `_run_nested`，实测 **397 行**。
   拆分（把 `RunResult`/`RunLogCollector`/`synthesize_run_input` 移出）会同时改动 §4.10 的冻结归属与全仓
   import，代价远高于放宽注记，且模块职责仍单一（注册表运行时），故不拆。
   **顺带发现**：`api.py` 现为 **497 行**，已超 R8 的 400 行红线——**本期之前即如此**（Phase 2 开始时为 462 行），
   非本次引入，列为待办拆分项，不在本期夹带。
2. **S18「`subworkflow` 注册期结构校验」的落点是节点 config 模型，不是 `api.py`**：
   `SubWorkflowNodeConfig` 的 `workflow_id` 字段校验（必填 + strip 后非空）在 `register_workflow` →
   `build_graph` → `create_node` 期间执行，而该调用就在 `PUT` handler 内、且被
   `except (ValueError, WorkflowEngineError)` 捕获（pydantic `ValidationError` 是 `ValueError` 子类）→ **422**。
   故 S18 要求的可观测结果完全成立，而校验只有**一个**出处。`api.py` 只把 `subworkflow` 加进白名单，
   **不**再手写一遍同款检查——那会造出第二个真相来源，两处口径迟早漂移。
   这与 `python` 的处理并不矛盾：`python` 的 `code` 非空检查必须写在 `api.py`，因为 `PythonNodeConfig`
   本身接受任意字符串；而 `sandboxed` 强制覆写更是只能在服务端做。

### 2.10 不变更项（显式声明）

- **组合根 `main.py` / `cli.py` 零改动**：runner 由 registry 自注入（§2.3）。这是选择「registry bound
  method」而非「宿主注入」的直接收益。
- `state.py` / `utils.py` / `store.py` / `security.py` / `sandbox.py` / `sandbox_worker.py` /
  `logging_conf.py` / `auth.py` **零改动**。
- `nodes/base.py` **零改动**：`_RUN_COLLECTOR` / `set_run_collector` / `get_run_collector` 已够用（S24 复用）。
- `nodes/python_node.py` / `llm_node.py` / `http_node.py` **零改动**：签名探测保证不接受
  `workflow_runner` 的插件类完全不受影响（§3 拍板结论）。
- `api.py` 除白名单 +1 与 `subworkflow` 结构校验外**无新端点、无新限流键**（复用 `workflows_save`）。
- 第 1、2 期成果（S21 简单模式执行入参、S22 沙箱、`PythonNodeForm`）**零改动**。
- `WorkflowTraceDrawer.vue` **零改动**（S24 前缀名直接展示）。

## 3. 备选方案对比（§11.4 + R-EXP 第 5 条：给 2-3 个附影响面）

本次唯一需要拍板的契约形态创新点是：**`workflow_runner` 如何从 factory 传到插件节点**。
`create_node` 的插件分支现在是一句固定 4-kwarg 调用（`name` / `node_type` / `config` / `operator_log`），
而 `SubWorkflowNode` 需要第 5 个。

| 方案 | 描述 | 影响面 | 结论 |
| --- | --- | --- | --- |
| **A. `inspect.signature` 探测（采纳）** | 插件分支探测 `node_class.__init__` 是否声明 `workflow_runner` 形参，声明才传 | `factory.py` +约 6 行；**不触碰 §4.4 `BaseNode` 冻结签名**；R4 内置分支恒 2 不破；`PythonNode`/`LLMNode`/`HTTPNode` 及所有第三方插件**零改动**（不接受该 kwarg 就收不到）。代价：反射是**隐式契约**——插件作者不读文档不会知道自己的 `__init__` 可以收这个参数；`**kwargs` 吞参的插件类会被判定为「接受」从而收到 runner（无害但意外） | **采纳**（用户 2026-09-14 拍板） |
| B. 给 `BaseNode.__init__` 加可选参 | 把 `workflow_runner` 写进 §4.4 冻结签名，所有节点统一接收，factory 无脑透传 | 显式、无反射、pyright 可查。但**触碰 §4.4 冻结接口**（本期唯一一处「改既有冻结签名」而非「加新可选参」），`LLMNode`/`HTTPNode`/`PythonNode` 三个既有节点构造签名都要改，其单测的构造调用全部受影响；对 99% 不需要嵌套的节点是纯噪音参数 | 否决 |
| C. 节点自行从 ContextVar 取 runner | registry 把 runner 放进 ContextVar，节点执行期自取，`create_node` 签名完全不动 | 改动最小（factory 零改动）。但**依赖关系被藏进全局态**：节点的能力来源不再出现在构造签名里，测试必须操纵 ContextVar 才能覆盖，且与 H5「节点不认识注册表」的**精神**相悖（虽然不违反字面红线 2）。S11 已用 ContextVar 承载 run-scoped 日志，那是**运行期数据**；runner 是**装配期依赖**，两者性质不同 | 否决 |

**A 的定位**：它是「加新可选参」而非「改既有签名」，因此与 S20 的 `chat_model_factory` 链式透传
形态一致（该形态已上线并稳定）。反射的隐式性用两条措施对冲：① `create_node` docstring 明写探测规则；
② `docs/workflow-node-development.md` 的插件开发章节加「可选注入参数」小节，说明声明即获得。

## 4. 验收

**守护测试修订（允许改动的既有断言）**

- `tests/unit/workflow/test_models.py::test_exception_hierarchy`：正向列表**扩展**至含 `NestedWorkflowError`（§2.6）。
- `tests/unit/workflow/test_models.py` 的 `NodeType` 成员数 == 2、R4 内置分支 == 2：**不动**（§2.8）。
- `tests/unit/workflow/test_api.py`：`test_save_workflow_unknown_node_type_rejected` 现用 `type="shell"`
  作未知类型（2026-09-14 已从 `python` 改为 `shell`），**无需再改**。
- 前端 `tests/composables/use-workflow-graph.spec.ts` 的未知类型卡现用 `'shell'`，**无需再改**。

**新增测试**

- `tests/unit/workflow/nodes/test_subworkflow_node.py`（5 卡）：config 必填/`extra="forbid"`；
  `workflow_runner=None` → `ConfigError`（不静默降级）；`input_map` + `inherit_input` 组合与优先级；
  输出为摘要形态且经 R3 双写；插件注册可被 `create_node` 解析。用 `FakeRunner` + conftest
  `restore_node_registry`（既有 autouse 夹具）。**须加 `pytestmark = pytest.mark.unit`**
  （`tests/unit/workflow/nodes/` 历史缺该标记，见 2026-09-14 记录）。
- `tests/unit/workflow/test_registry_nesting.py`（**新增独立文件**，13 卡；`test_registry.py` 已 427 行，
  再塞进去会牺牲可读性，故 S23/S24 语义单独成文）：前缀日志合并 + 三层复合（`sub_b/sub_c/c1`）+ 子节点摘要；
  `A→B→A` 环与 `A→A` 自环 → `NestedWorkflowError` 且消息含完整栈；深度语义三卡（默认 3 ⇒ `A→B→C` **成功**、
  第 4 层被拒、`max_nesting_depth=1` 禁一切嵌套）；`_RUN_STACK` 在成功/失败路径都 reset 且拒绝后下一次运行正常；
  悬空引用 → `WorkflowNotFoundError`。
- `tests/integration/workflow/test_nested_concurrency.py`（**新增**，2 卡）：异 id 并发嵌套（各 5 轮）无死锁、
  轨迹前缀正确；同一 outer id 双线程并发被 RLock 串行化且日志条数一致（无交错）。
  **刻意不测** AB-BA 互引拓扑——那会真的挂住（残留风险 2），挂住的测试不是测试。
- `tests/unit/workflow/nodes/test_factory.py`（5 卡）：签名探测——声明 `workflow_runner` 的插件收到它、
  未声明的（`PythonNode` 情形）**收不到**且不报错、`**kwargs` 插件按规则收到、未传 runner 时得到显式 `None`、
  两个内置分支不受新参影响（R4 恒 2）。
- `tests/unit/workflow/test_api.py`（3 卡）：`PUT` 接受 `subworkflow` 且 config 原样透传；**悬空引用也被接受**
  （存在性是运行期检查，钉住这一刻意决定）；`workflow_id` 缺失/空/纯空白/非 str → 422（参数化 4 例）。
- **守护测试扩展**：`test_llm_node.py::test_engine_modules_stay_free_of_host_imports` 原为**手写文件清单**，
  已静默漏掉 `python_node.py`/`sandbox.py`/`sandbox_worker.py`——即新增模块默认逃过红线 4 守护。
  改为遍历 `app/workflow/**/*.py` 并只排除 §3 豁免的三个入口层模块，另断言若干已知文件确被扫到，
  防止路径计算失效导致空转通过。
- 前端：新 `tests/components/subworkflow-node-form.spec.ts`；扩 `node-palette.spec.ts`（4 项）、
  `node-config-panel.spec.ts`（分派 + patch）、`workflow-canvas.spec.ts`（token class）、
  `use-workflow-graph.spec.ts`（接受 `subworkflow` / 缺 `workflow_id` 报错）。零真实网络（`vi.mock('@/api/workflow')`）。

**门禁**：`uv run pytest -m unit`、`uv run pytest tests/integration/workflow`、`make lint`、`make typecheck` 全绿；
前端 `npm run type-check`、`npm test`、`npm run build` 全绿；覆盖率 ≥ 80%（R7）。
已知无关失败：`tests/integration/sdn` 2 卡（依赖真实 DNS）、前端 `agent-list.spec.ts` 2 卡（`interrupt_on`）。

**E2E**（优先用 `docker exec` 在容器内验证引擎层；HTTP/浏览器腿若被权限分类器拦截则如实记录未执行）：
`PUT wf_inner`（llm + http mock）与 `wf_outer`（`subworkflow` 引用 inner）→ 执行 outer →
外层轨迹含 `sub_1/<inner 节点名>` 前缀条目、子节点 output 仅摘要；
反例：自引用执行 → `NestedWorkflowError` 含环栈；**四层**链 `A→B→C→D` 在默认 `depth=3` 下
第四层 → `NestedWorkflowError`。

> **与计划的措辞校正**：原计划 E2E 写「三层链在默认 depth=3 下第三层 → NestedWorkflowError」。
> 按 §2.7 冻结的语义（`max_nesting_depth` = 栈内工作流数量上限，含最外层），`A→B→C` 恰好用满
> 3 层应当**成功**，被拒的是**第四层**。本契约以冻结语义为准，E2E 按四层链验收，避免实现与测试
> 各按一种口径 off-by-one。

**实测记录（2026-09-14，实现完成后）**

| 门禁 | 结果 |
| --- | --- |
| `make lint` | All checks passed |
| `make typecheck` | pyright 0 errors / 0 warnings |
| `uv run pytest -m unit` | **1333 passed**, 126 deselected, 0 failed |
| `uv run pytest -m integration` | **124 passed**, 2 failed（`tests/integration/sdn`，依赖真实 DNS，**本期前即失败**） |
| 覆盖率（`--cov=app.workflow`） | `subworkflow_node.py` **100%**、`registry.py` **100%**、`factory.py` **100%**、`graph_builder.py` **100%**、`ports.py` **100%**；`app/workflow` 整包 **96%**（R7 下限 80%） |
| 前端 `npm run type-check` | 通过 |
| 前端 `npm test` | **673 passed / 2 failed**（`agent-list.spec.ts` 的 `interrupt_on`，**本期前即失败**）；本期 6 个 spec 文件 **105 卡全绿** |
| 前端 `npm run build` | 通过（`vue-tsc -b && vite build`） |

**容器 E2E（19/19 通过）**：在 `deploy-app-1`（**本次重建的镜像**，`platform = Linux 6.12.68-linuxkit`）内
以 in-process ASGI 驱动真实 FastAPI 路由 + 真实 YAML 落盘 + 真实 registry + 真实沙箱子进程。实测轨迹形态：

| 场景 | 实测结果 |
| --- | --- |
| 外层 `e2e_outer`（1 个 `subworkflow` 节点 `sub_1`）→ 内层 3 节点（python×2 + http mock） | `node_names == ['sub_1/transform', 'sub_1/stamp', 'sub_1/fetch', 'sub_1']`——S24 前缀合并与「内层日志在前、调用节点摘要在后」的顺序均如契约 |
| 三层链 `e2e_l2 → e2e_l3 → e2e_l4`（默认 depth=3） | **成功**，`node_names == ['sub_a/sub_b/n', 'sub_a/sub_b', 'sub_a']`——前缀自然复合，无需特判层数 |
| 四层链 `e2e_l1 → … → e2e_l4` | `NestedWorkflowError: nested workflow depth limit 3 exceeded: e2e_l1 -> e2e_l2 -> e2e_l3 -> e2e_l4` |
| 自引用 / 间接环 | `nested workflow cycle detected: e2e_self -> e2e_self` / `e2e_cycle_a -> e2e_cycle_b -> e2e_cycle_a` |
| 守护拒绝后紧接一次正常嵌套运行 | 成功（`_RUN_STACK` 已 reset，无 ContextVar 泄漏） |
| `dual_write=False` 防覆写 | 外层 `output["input"]` 保持 `"abc"`，未被内层 state 的 `input` 覆盖 |
| 悬空引用 | 保存 **200**（S18 存在性是运行期检查），执行 → `WorkflowNotFoundError: workflow not found: e2e_never_saved` |
| 空白 `workflow_id` | **422**，消息为 `build-time error: 1 validation error for SubWorkflowNodeConfig`（S6 构建期校验优先，非 500） |
| **经嵌套到达的 python 节点是否仍进受限子进程** | `PythonNodeError: sandboxed code failed: NameError: name 'hash' is not defined`。`hash` 能通过全部 AST 规则却不在 `SAFE_BUILTINS` 白名单内，故该报错证明嵌套路径没有绕开沙箱走 in-process `exec` |

**未执行的两条腿**（与第 1、2 期同因，如实记录）：① 真实 TCP + JWT 的 HTTP E2E——`PUT` 受 S19 管理员门禁保护，
带凭据的 `curl` 与浏览器自动化均被权限分类器拦截；② 浏览器端编排/执行演示。二者需人工补做。
in-process ASGI 覆盖了除 socket 与鉴权之外的全部环节（路由、限流处理器、异常映射、落盘、注册、编译、执行）。

**写 E2E 载荷时发现的既有陷阱（非本期引入）**：`HTTPNodeConfig.mock_responses` 的类型是
`dict[str, str]`，值必须是 **JSON 字符串**（`_resolve_mock` 对值做 `json.loads`）。E2E 首版按直觉写成
`{"POST <url>": {"echoed": "ok"}}`（嵌套对象）→ 构建期 422。而前端 `HttpNodeForm` 的 Mock Responses
是**单个 JSON 文本域**，用户几乎必然写成嵌套对象形态，即前端可以构造出后端一定拒绝的载荷。
列为待办（§5 残留风险 6），不在本期夹带。

## 5. 残留风险与 open questions

1. **内层 `definition.execution_history`（S12 单槽）被嵌套运行覆盖**：内层工作流的定义对象上只保留
   最近一次运行的日志，嵌套运行会覆盖它此前的独立运行记录。**接受并文档化**——外层 `RunResult`
   才是本次运行的权威轨迹，内层定义上的单槽历史只是调试便利。若要保留需改成多槽有界队列，
   属 S12 契约变更，本期不做。
2. **跨线程 AB-BA 死锁在理论上可达**：两个线程分别执行 `A→B` 与 `B→A`（两个不同的外层工作流，
   互相引用对方作为子工作流——注意这**不是**环，因为各自的运行栈内没有重复 id，S23 环检测不会拦），
   则线程 1 持 `A` 锁等 `B` 锁、线程 2 持 `B` 锁等 `A` 锁。per-id `RLock` 只在**同线程**可重入，
   跨线程无解。**缓解**：`execute_workflow` 全程无超时（既有状况），故死锁会挂住而非报错。
   **本期不做全局锁序**（需给 workflow_id 定全序并按序获取，会改变并发语义）；记录在案，
   生产若出现互相引用的工作流对，应在评审期禁止该拓扑。后续可选：给 `_run_nested` 加获取超时。
3. **被引用工作流后注册 → 运行期 `WorkflowNotFoundError`**：见 §2.7「为什么不做注册期存在性校验」。
   前端下拉只列已注册项属体验层缓解，**不是边界**：直接调 API 仍可保存悬空引用。
4. **`input_map` 的值是外层 state 的点路径（S7 语法），路径不存在时的处置**：本期定为
   **跳过该键**（不写入内层输入，让内层按 S14 走声明默认值），而非报错——与 `{input}` 占位符
   渲染的既有容错口径一致。需在实现时加测试固定，否则易被写成 `KeyError`。
5. **签名探测对 `**kwargs` 插件类的行为**：`inspect.signature` 对含 `**kwargs` 的 `__init__`
   会判定为「可接受任意 kwarg」，于是这类插件会收到 `workflow_runner`。无害（它本来就接受），
   但与「声明即获得」的文档表述有细微出入。实现时以 `VAR_KEYWORD` 存在也算接受，并在
   `docs/workflow-node-development.md` 注明。
6. **`mock_responses` 的值类型与前端表单形态不一致（既有缺陷，本期写 E2E 载荷时发现）**：
   后端 `HTTPNodeConfig.mock_responses: dict[str, str]`，值必须是 **JSON 字符串**
   （`_resolve_mock` 对值做 `json.loads`）；而前端 `HttpNodeForm` 的 Mock Responses 是**单个 JSON
   文本域**，用户按直觉几乎必然写成 `{"POST <url>": {"echoed": "ok"}}` 这样的嵌套对象形态，
   即**前端可以构造出后端一定拒绝的载荷**，且报错发生在保存期、消息是 pydantic 的
   validation error，不点名「值应是 JSON 字符串」。**不在本期修**：改动会触及 S18 之外的
   http 节点契约（要么放宽后端接受 `dict | str`，要么前端做值序列化），属独立契约变更。
   后续可选：前端提交前把嵌套值 `JSON.stringify` 一次，并在帮助文本写明形态。
7. **compose 健康检查恒为 unhealthy（既有缺陷，容器 E2E 期间发现）**：`deploy/docker-compose.yml:94`
   的 healthcheck 用 `curl`，而 app 镜像内**未安装 curl**，故 `deploy-app-1` 永久报告
   `unhealthy`（观测到 FailingStreak 425），尽管应用正常提供服务。影响是「监控信号失真」而非
   「功能不可用」，但会掩盖真实故障。**不在本期修**（属部署配置，与工作流引擎无关）。
   后续可选：改用 `python -c "import urllib.request; urllib.request.urlopen('http://localhost:8000/health')"`。
8. **`node-config-panel.spec.ts` 仍有 2 条真实请求噪音（既有状况，本期已挡住自己引入的部分）**：
   `useProviderModels` 是**模块级 ref 单例**（「避免每次挂载都重发 N+1 请求」），因此每个挂载
   `LlmNodeForm` 且未 mock `@/api/provider` 的 spec 文件都会发起 **2 次**真实 HTTP 尝试，
   在 happy-dom 下表现为 stderr 的 `Fetch.onError` / `ECONNRESET`。本期新增的
   `SubWorkflowNodeForm` 挂载即拉取工作流目录，已按 R7「零真实网络」加
   `vi.mock('@/api/workflow')` 挡住（用 `-t 'subworkflow'` 过滤后 `Fetch.onError` 计数为 0，
   `-t 'H6'` 时仍为 2，证实剩余噪音来自既有的 LLM 表单）。**不在本期修**：属既有测试卫生问题。
