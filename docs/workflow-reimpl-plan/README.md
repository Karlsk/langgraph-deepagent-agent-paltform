# 声明式工作流引擎重实现 · 文档导航（README）

> **一句话用途**：本套文档指导在一个全新项目中，重新实现一个「精简、生产级」的**声明式工作流引擎**——核心思想沿用原 `workflow_v2`（声明式 YAML → LangGraph 状态图编译器 + 进程级注册运行时），本期仅实现 2 个具体节点类型：`LLMNode`、`HTTPNode`（外加抽象 `BaseNode`）。

- 文档全部用**中文**书写；代码标识符、文件路径、配置键保留**英文**。
- 本套文档内部互相引用时，统一使用契约（CONTRACT）中的编号体系：保留项 **K1..K10**、清理项 **C1..C9**、隐患 **H1..H7**、红线 **R1..R10**、阶段 **Phase 0..9**。**禁止自造同义异名**。
- 任何决策以 CONTRACT 为准；若原始代码与契约冲突，以契约为准。

---

## 目录

1. [文档清单](#1-文档清单)
2. [推荐阅读顺序](#2-推荐阅读顺序)
3. [快速开始（AI 协作最小指引）](#3-快速开始ai-协作最小指引)
4. [范围声明（本期仅 BaseNode / LLMNode / HTTPNode）](#4-范围声明本期仅-basenode--llmnode--httpnode)
5. [阶段总览（Phase 0..9）](#5-阶段总览phase-09)
6. [隐患速查表（H1..H7）](#6-隐患速查表h1h7)
7. [目标包结构（统一口径）](#7-目标包结构统一口径)
8. [技术选型速查](#8-技术选型速查)
9. [编号与术语约定](#9-编号与术语约定)
10. [与原始代码的关系](#10-与原始代码的关系)
11. [文档维护约定](#11-文档维护约定)

---

## 1. 文档清单

| 文件 | 内容 | 读者 |
|---|---|---|
| `README.md`（本文件） | 文档导航、阅读顺序、AI 协作快速开始、范围声明、阶段/隐患速查、编号与术语约定 | 全体（人类 + AI 协作代理） |
| `00-架构总览.md`《架构总览》 | 架构设计：整体架构图、模块职责划分（`state.py` / `graph_builder.py` / `registry.py` 单一职责拆分）、数据流、保留设计契约 K1..K10 的详细说明、动态状态模型与 reducer 机制、条件路由器设计、运行级日志收集设计 | 架构师、AI 开发代理（动手前必读） |
| `01-分阶段开发计划.md`《分阶段开发计划》 | 分阶段实施计划：Phase 0..9 每阶段的任务清单、TDD 步骤（RED-GREEN-REFACTOR）、涉及模块、DoD（完成定义）、验收测试要点 | 开发执行者（按阶段逐段执行） |
| `02-开发规范.md`《开发规范》 | 开发规范与防跑偏红线：R1..R10 红线全文、代码风格（black/isort/ruff）、命名与 docstring、日志规范（结构化 + 脱敏）、测试与 mock 规范、conventional commits 提交规范、AI 协作守则 | 全体（每个 Phase 开始前复读） |
| `03-隐患修复方案.md`《隐患修复方案》 | 隐患与清理项随查手册：H1..H7 的「编号 → 根因 → 修复 → 验证 → 所属阶段」完整档案，C1..C9 清理项的对照说明，R 红线与 H/C 的映射 | 开发与代码评审（遇到问题随查） |

> 四份正文文档（00/01/02/03）与本 README 共同构成完整契约。**本 README 只做导航与速查**；细节一律以对应正文文档为准，正文文档一律以 CONTRACT 为准。

---

## 2. 推荐阅读顺序

```text
00-架构总览.md   →   02-开发规范.md   →   01-分阶段开发计划.md（按 Phase 逐段执行）   →   03-隐患修复方案.md（随查）
  （先懂全局）          （再记红线）               （然后按阶段干活）                          （遇到隐患再查）
```

| 顺序 | 文档 | 为什么这个顺序 |
|---|---|---|
| ① | `00-架构总览.md` | 先建立全局认知：引擎三层结构（DSL 模型层 → 图编译层 → 注册运行时层）、模块职责边界、K1..K10 契约。不懂架构就动手，必然跑偏。 |
| ② | `02-开发规范.md` | 在写任何代码前记住红线 R1..R10 与编码/测试/日志/提交规范。红线是「禁止项」，成本最低的学习时机是动手之前。 |
| ③ | `01-分阶段开发计划.md` | 按 Phase 0..9 顺序逐阶段执行；每个 Phase 有独立 DoD，前一阶段 DoD 未过不进入下一阶段。 |
| ④ | `03-隐患修复方案.md` | 不要求通读背诵，但当某阶段任务标注「涉及隐患 Hx」或评审发现疑似问题时，按编号随查根因与修复方案。 |

**按角色的最小阅读集**：

| 角色 | 必读 | 选读 |
|---|---|---|
| 架构评审 | 00、03 | 01、02 |
| 阶段开发执行（人类或 AI） | 00、02、01 的当前 Phase | 03 中当前 Phase 关联的 Hx |
| 代码评审 | 02、03 | 00、01 |
| 新加入成员 | 本 README → 00 → 02 | 其余 |

---

## 3. 快速开始（AI 协作最小指引）

本节是 AI 协作代理的**最小操作回路**。每执行一个 Phase，严格走以下五步：

```text
┌─────────────────────────────────────────────────────────────────┐
│ 每个 Phase 的标准回路                                             │
│                                                                   │
│  Step 1  复读 00-架构总览.md（全局）+ 02-开发规范.md（红线）        │
│  Step 2  打开 01-分阶段开发计划.md，定位当前 Phase 的任务清单与 DoD  │
│  Step 3  若任务标注「涉及 Hx / Cx」→ 查 03-隐患修复方案.md 对应条目  │
│  Step 4  严格 TDD 执行：RED → GREEN → REFACTOR（见 02 规范）        │
│  Step 5  对照 DoD 自查 + 跑 pytest + 覆盖率检查，通过后提交         │
└─────────────────────────────────────────────────────────────────┘
```

**每个 Phase 开工前的自查清单**（可复制使用）：

```markdown
- [ ] 已重读 00-架构总览.md 中与本阶段相关的模块职责与契约（K1..K10）
- [ ] 已重读 02-开发规范.md 红线 R1..R10（尤其 R1 范围、R2 reducer、R3 不可变、R5 密钥、R6 错误处理）
- [ ] 已从 01-分阶段开发计划.md 抄出本阶段任务清单与 DoD
- [ ] 已确认本阶段涉及的隐患编号（H1..H7）并在 03-隐患修复方案.md 中读过其修复方案
- [ ] 未引入任何契约之外的节点类型 / 领域字段 / 领域逻辑（R1、R2）
- [ ] 本次改动全部走 TDD，新代码有测试，覆盖率 >= 80%（R7）
```

**契约冲突处理规则**（AI 协作守则核心，完整版见 `02-开发规范.md`）：

1. 发现任务与 K/C/H/R 任一条款冲突 → **先停下来提问，不要自行裁决**。
2. 不扩大范围：不在计划外新增文件、节点类型、抽象层或依赖。
3. 原始代码仅供参考；任何与契约冲突的「原代码惯例」一律以契约为准。
4. 每阶段结束回报：改了哪些文件、DoD 达成情况、遗留问题。

---

## 4. 范围声明（本期仅 BaseNode / LLMNode / HTTPNode）

### 4.1 本期交付边界

| 维度 | 本期范围（做） | 本期范围外（不做，列为未来扩展） |
|---|---|---|
| 节点类型 | `BaseNode`（抽象）、`LLMNode`、`HTTPNode` | 其余 17 种节点：plan / worker / reflection / llm_reflection / agent / tool / merge / extract / dispatcher / collector / subgraph / triage / device_work / controller_work / aggregate / score / output |
| 图能力 | 线性边、条件边路由、compile 执行 | 基于 `langgraph.types.Send` 的并行扇出（dispatcher/triage）、子图嵌套 |
| 外部依赖 | LLM（openai/anthropic）、HTTP（httpx） | game_agent/、node4j/、MCP client、Neo4j 计划生成与故障领域 YAML |
| 工具层 | 仅 2 个状态工具函数 | utils 里的 `LLMHelper` 单例与 Neo4j 逻辑（全部丢弃） |
| 入口 | 最小入口（FastAPI router 或 CLI）+ 结构化日志 | 完整产品化功能（多租户、持久化、UI 等） |

### 4.2 保留设计速览（K1..K10，详见 `00-架构总览.md`）

| 编号 | 保留项（一句话） |
|---|---|
| K1 | 声明式 YAML DSL 五元组：`workflow_id` / `entry_point` / `nodes` / `edges` / `state_schema` |
| K2 | `pydantic.create_model` 动态合成状态模型，基类 `ConfigDict(extra="allow")`（Dify 风格） |
| K3 | state channel 的 reducer 机制：`reducer="add"` → `Annotated[list, operator.add]`；`reducer="last"` → `Annotated[T, _last]` |
| K4 | `BaseNode` 契约：`build_runnable() -> RunnableLambda(func).with_config(tags=[name])`；`func(state)->dict`；`validate_config()`；内置 `ExecutionLog` 累积 |
| K5 | 插件式节点工厂：`register_node_type(name, cls)` + `create_node(definition, operator_log=None)`（先查注册表再内置分发） |
| K6 | `GraphBuilder.build_graph` 七步：`_validate_definition` → `create_state_model` → `StateGraph(state)` → `_add_nodes` → `_add_edges` → `set_entry_point` → `compile()` |
| K7 | 条件边路由器：`EdgeDefinition.condition` 编译为 `router(state)->str` 闭包，经 `graph.add_conditional_edges` 接入 |
| K8 | `WorkflowRegistry`：register / get / has / list / execute（`execute_workflow` 返回 `RunResult`）+ 查询接口（operator_logs、execution_history、按节点查询） |
| K9 | utils 两个状态工具：`convert_state_to_dict` 与 `map_output_to_state`（Dify 双写：`{node_name}_result` 整包 + 逐字段平铺） |
| K10 | `LLMNode` 多供应商（openai/anthropic，懒加载）；`HTTPNode` 模板化（占位符渲染 + `response_path` 提取） |

### 4.3 清理优化速览（C1..C9，详见 `03-隐患修复方案.md` 与 `00-架构总览.md`）

| 编号 | 原代码问题 → 新实现修正 |
|---|---|
| C1 | 814 行混杂的 `graph_builder.py` → 拆为 `state.py`（StateModelFactory）+ `graph_builder.py`（GraphBuilder）+ `registry.py`（WorkflowRegistry）三个单一职责模块 |
| C2 | 移除 `create_state_model` 中硬编码领域字段名分支 → reducer 只由 YAML 显式声明驱动 + 默认 LastValue 后写覆盖；`history` 自动注入语义明确 |
| C3 | 条件路由器 print 调试 → logging；「无条件命中」必须显式可配置（raise `ConditionNotMatchedError` 或 `default_edge`），禁止静默落到最后一条边 |
| C4 | `map_output_to_state` 的 history 追加消除 str/list 二义性 → 约定 history 为 list channel（reducer add）才自动追加；双写可配置（`dual_write` 默认开） |
| C5 | `_validate_definition` 移除 dispatcher 字符串字面量豁免 → 干净完整的校验（workflow_id / nodes / entry_point / edges 端点存在性） |
| C6 | 合并 `unregister_workflow` 与 `delete_workflow` 为单一 `delete_workflow` → `_registry` / `_definitions` / `_nodes_map` 三张映射同增同删 |
| C7 | utils 瘦身 → 只保留 `convert_state_to_dict` / `map_output_to_state`；LLM 逻辑全部内聚到 `LLMNode` |
| C8 | `NodeType` 精简 → 本期至少 LLM、HTTP，保留注册机制，不携带 19 种领域枚举 |
| C9 | 密钥与日志卫生 → 密钥一律环境变量；禁止完整 state / 密钥入日志；结构化 logger，不用 print |

### 4.4 防跑偏红线速览（R1..R10，全文见 `02-开发规范.md`）

| 编号 | 红线（一句话） |
|---|---|
| R1 | 本期只实现 BaseNode / LLMNode / HTTPNode，禁止擅自新增节点类型或领域逻辑 |
| R2 | 引擎保持通用，reducer 只能来自 YAML 显式声明，禁止在引擎里硬编码业务字段名 |
| R3 | 节点遵循 `build_runnable` + `convert_state_to_dict` 进 / `map_output_to_state` 出 的契约，不得自行 mutate 输入 state（不可变） |
| R4 | 新节点必须通过 `register_node_type` 注册，禁止在 `create_node` 里堆 if/elif 领域分支 |
| R5 | 所有密钥走 env，禁止硬编码，禁止把完整 state / 密钥写日志 |
| R6 | 禁止死 try/except，错误必须显式处理（记录 + 降级或重抛） |
| R7 | 严格 TDD（RED-GREEN-REFACTOR），单测 + 集成，覆盖率 >= 80% |
| R8 | 小文件（< 400 行）小函数（< 50 行），按领域组织 |
| R9 | pydantic v2 全量类型标注，边界处校验 |
| R10 | 任何缓存必须有上限 + 失效 + 开关（本期默认不引入缓存） |

---

## 5. 阶段总览（Phase 0..9）

完整任务清单、TDD 步骤与验收测试要点见 `01-分阶段开发计划.md`。本表仅用于全局进度感与依赖感。

| 阶段 | 主题 | 关键交付（模块） | DoD 摘要 | 涉及隐患/清理项 |
|---|---|---|---|---|
| Phase 0 | 项目脚手架与基础设施 | 包结构、`pyproject`/`requirements`、pytest 配置（unit/integration 标记 + cov）、`logging_conf.py`、`.env` 加载、CI 占位 | 空包可导入，pytest 跑绿 | — |
| Phase 1 | 数据模型层（DSL & Models） | `models.py` 全部 pydantic 模型 + 精简 `NodeType`；YAML → `WorkflowDefinition` 解析 | 能解析示例 YAML 并校验 | C8 |
| Phase 2 | State 自动生成 | `state.py` 的 `StateModelFactory.create_state_model`；TYPE_MAP；reducer 显式声明驱动；history 自动注入；`extra=allow` | state_schema → 可用 Pydantic 模型，reducer/extra/history 行为有测试 | C2 |
| Phase 3 | 节点基础设施 | `nodes/base.py`（`BaseNode`）、`nodes/factory.py`（`register_node_type` + `create_node`）、`utils.py`（两个状态工具） | 可注册/创建节点，状态转换与输出映射有测试 | C4、C7 |
| Phase 4 | LLMNode | `nodes/llm_node.py` + `LLMConfig`；多供应商、env 密钥、懒加载、429 指数退避重试、显式错误处理 | mock LLM 下调用/映射/重试/失败路径全绿 | H2、H6 |
| Phase 5 | HTTPNode | `nodes/http_node.py` + `HTTPNodeConfig`；占位符渲染、method/headers/body_template、`response_path` 提取、显式可配置 retry 与 mock | mock httpx 下渲染/调用/提取/重试/失败路径全绿 | H2、H4、H6 |
| Phase 6 | 图构建器 | `graph_builder.py` 的 `GraphBuilder` 七步；条件路由器（logging、显式 no-match）；普通边；compile | YAML → 编译图可执行，含一条条件分支的 2~3 节点图跑通 | C1、C3、C5 |
| Phase 7 | 注册表与运行时 | `registry.py` 的 `WorkflowRegistry`（线程安全锁）+ 运行级日志收集 + `load_definitions_from_dir` | 注册/执行/并发安全/日志收集/统一 delete 全绿 | H1、H3、H7 |
| Phase 8 | 入口集成 | 入口模块 `cli.py` / `__main__.py` / `api.py`（Phase 8 新增，FastAPI router + CLI）串联 加载 → 注册 → 执行 → 响应；结构化日志与脱敏落地 | 端到端请求跑通并返回规范信封（注：CLI stdout 用 CONTRACT §4.12 信封 `{success,data,error,metadata}`；HTTP 出口经 `api.py` 出口映射投影为宿主统一信封 `{code,message,data}`，见 spec-08 §6） | H6 |
| Phase 9 | 加固与交付 | 安全审计、并发压测、覆盖率达标（>= 80%）、示例 YAML 与 README、文档校对 | 加固清单全过，示例可运行 | 全部复核 |

**阶段依赖**：Phase 0 → 1 → 2 → 3 为主干基础；Phase 4、5 依赖 Phase 3；Phase 6 依赖 Phase 2/3；Phase 7 依赖 Phase 6；Phase 8 依赖 Phase 7；Phase 9 为最终收口。Phase 4 与 Phase 5 相互独立，可并行。

---

## 6. 隐患速查表（H1..H7）

完整「根因 → 修复 → 验证 → 所属阶段」档案见 `03-隐患修复方案.md`。开发时若任务标注涉及某 Hx，必须先查档案再动手。

| 编号 | 一句话根因 | 修复方向 | 所属阶段 |
|---|---|---|---|
| H1 | Registry 进程级单例被线程池共享，`execute_workflow` 非原子，且在共享节点实例上就地收集日志 → 并发互串 | 每个 workflow_id 一把 `threading.RLock` 串行化执行；日志收集改为运行级 `RunLogCollector`（ContextVar 方案，01 口径） | Phase 7 |
| H2 | 死 try/except（except 分支逻辑不可靠且从未被测试覆盖）+ 降级路径无配置开关、硬编码 mock 返回 | 禁止死 try/except；降级路径必须有测试；HTTPNode retry/mock 必须显式配置开关 | Phase 4 / Phase 5 |
| H3 | 执行日志收集不全：独立节点实例不纳入 `_nodes_map`，子图日志永远收不上来 | 由运行级 `RunLogCollector`（ContextVar）统一收集，覆盖「本次运行实际执行的所有节点」，与节点创建方式解耦，预留可扩展接口 | Phase 7 |
| H4 | 无界缓存：模块级 dict 无上限、永不失效、跨请求重放陈旧结果 | 本期默认不引入缓存；若引入必须 maxsize + TTL + 显式开关 + 正确 key 设计 | 规范约束（Phase 4/5 若引入） |
| H5 | 构建期快照依赖：构建时拷贝 registry 快照，之后注册的工作流不可见 | 禁止对可变注册表做构建期快照；需要引用其它工作流时运行期惰性解析 | 规范约束 |
| H6 | 安全/日志卫生：YAML 硬编码 API key、默认密码、路由闭包 print 完整 state、异常返回硬编码 mock | 密钥一律 env；日志结构化 + redact 脱敏过滤器；禁止未配置开关的硬编码 mock | Phase 8 + Phase 9 |
| H7 | Registry API 不一致：`unregister_workflow` 与 `delete_workflow` 语义不一致导致内部映射泄漏 | 合并为单一 `delete_workflow`，三张映射（`_registry`/`_definitions`/`_nodes_map`）同删 | Phase 7 |

---

## 7. 目标包结构（统一口径）

> 所有文档与代码必须与此结构一致。包名 `workflow_engine` 为**占位符**，项目落地时可整体改名（改名时全文档统一替换，不改结构）。

```text
workflow_engine/
  __init__.py
  models.py          # DSL: WorkflowDefinition / NodeDefinition / EdgeDefinition / StateFieldSchema / OperatorLog / ExecutionLog + 精简 NodeType
  state.py           # StateModelFactory.create_state_model(state_schema) -> 动态 Pydantic 模型
  graph_builder.py   # GraphBuilder: build_graph / _validate_definition / _add_nodes / _add_edges / 条件路由 / compile
  registry.py        # WorkflowRegistry（线程安全）+ YAML 配置加载 load_definitions_from_dir
  utils.py           # convert_state_to_dict / map_output_to_state（仅状态工具，瘦身）
  logging_conf.py    # 结构化日志初始化 + 脱敏过滤器
  cli.py             # CLI 入口（Phase 8 新增）
  __main__.py        # python -m 入口（Phase 8 新增）
  api.py             # FastAPI router 入口（Phase 8 新增）
  nodes/
    __init__.py
    base.py          # BaseNode（抽象）
    factory.py       # create_node + register_node_type（插件注册表真正接线）
    llm_node.py      # LLMNode + LLMConfig
    http_node.py     # HTTPNode + HTTPNodeConfig
  config/examples/   # 示例 YAML
tests/               # 镜像包结构的测试
```

**模块职责一句话口径**（详见 `00-架构总览.md`）：

| 模块 | 单一职责 |
|---|---|
| `models.py` | 只定义 DSL 与日志的 pydantic v2 数据模型，不含行为逻辑 |
| `state.py` | 只负责 `state_schema` → 动态状态模型（reducer / extra=allow / history 注入） |
| `graph_builder.py` | 只负责「定义 → LangGraph 编译图」，含校验、加节点、加边、条件路由、compile |
| `registry.py` | 只负责进程级注册运行时：注册/查询/执行/统一删除 + YAML 目录加载 |
| `utils.py` | 只放两个状态工具函数，不放任何 LLM/HTTP/领域逻辑 |
| `logging_conf.py` | 只负责日志初始化与脱敏过滤器 |
| `cli.py` / `__main__.py` / `api.py` | Phase 8 新增入口模块：CLI 与 FastAPI router，串联 加载 → 注册 → 执行 → 响应信封（CLI 输出 CONTRACT §4.12 信封且冻结不变；`api.py` 在 HTTP 出口做 `{code,message,data}` 宿主统一信封投影，见 spec-08 §6） |
| `nodes/base.py` | 节点抽象契约（K4） |
| `nodes/factory.py` | 插件注册表 + `create_node` 分发（K5、R4） |
| `nodes/llm_node.py` | LLM 调用全部内聚于此（含重试、多供应商、env 密钥） |
| `nodes/http_node.py` | HTTP 调用全部内聚于此（含模板渲染、提取、可配置 retry/mock） |

---

## 8. 技术选型速查

| 层面 | 选型 | 备注 |
|---|---|---|
| 语言 | Python 3.11+ | 全量类型标注（R9） |
| 图引擎 | `langgraph`（`StateGraph` / `START` / `END`）+ `langchain_core`（`Runnable` / `RunnableLambda`） | 节点经 `RunnableLambda(func).with_config(tags=[name])` 接入（K4） |
| 模型层 | pydantic v2 | `create_model` 动态状态模型（K2）；`ConfigDict(extra="allow")` |
| HTTP 客户端 | `httpx` | 本期同步即可，代码结构预留异步 |
| LLM 客户端 | `langchain_openai` / `langchain_anthropic` | 按 `llm_type` 选择，懒加载实例（K10） |
| 测试 | `pytest`（unit / integration 标记）+ `pytest-cov` | 覆盖率 >= 80%（R7） |
| 格式化/排序/lint | `black` / `isort` / `ruff` | 提交前必须通过（见 `02-开发规范.md`） |
| 密钥管理 | 一律环境变量（`.env` 加载） | 禁止硬编码（R5、C9、H6） |

---

## 9. 编号与术语约定

本套文档所有交叉引用**必须**使用下列编号，禁止自造同义异名（例如不得把「红线」写成「准则/原则」，不得把「隐患」写成「风险点/坑」另起编号）：

| 前缀 | 含义 | 范围 | 权威出处 |
|---|---|---|---|
| K1..K10 | **保留项**：原 `workflow_v2` 中生产级、必须沿用的设计 | 10 条 | CONTRACT / `00-架构总览.md` |
| C1..C9 | **清理项**：原代码逻辑混乱处，新实现必须修正 | 9 条 | CONTRACT / `03-隐患修复方案.md` |
| H1..H7 | **隐患**：已识别的生产事故级缺陷，含根因/修复/验证/阶段 | 7 条 | CONTRACT / `03-隐患修复方案.md` |
| R1..R10 | **红线**：开发规范中的禁止项（防跑偏） | 10 条 | CONTRACT / `02-开发规范.md` |
| Phase 0..9 | **阶段**：实施计划编号，固定 10 个阶段 | 10 个 | CONTRACT / `01-分阶段开发计划.md` |

**核心术语口径**（统一用法，不另起别名）：

| 术语 | 定义 |
|---|---|
| 声明式 YAML DSL | 五元组：`workflow_id` / `entry_point` / `nodes` / `edges` / `state_schema`（K1） |
| 状态模型 | 由 `StateModelFactory.create_state_model(state_schema)` 动态合成的 pydantic 模型（K2） |
| reducer | state channel 的合并策略，只由 YAML 显式 `reducer` 字段驱动（K3、C2、R2）：`"add"` → `operator.add`；`"last"` → `_last`；未声明 → LangGraph LastValue 后写覆盖 |
| 双写 | `map_output_to_state` 同时写 `{node_name}_result` 整包 + 逐字段平铺（K9、C4），由 `dual_write` 开关控制（默认开） |
| 运行级日志收集 | 每次 `execute_workflow` 用 run-scoped 结构收集 `ExecutionLog`，不在共享节点实例上就地 mutate（H1、H3） |
| 条件路由器 | `EdgeDefinition.condition`（点路径，支持 `"a.b == 'x'"` 等值比较与纯路径真值判断）编译成的 `router(state)->str` 闭包（K7、C3） |

---

## 10. 与原始代码的关系

- **参考路径**（仅供引用真实签名/行号来支撑「保留生产代码」的论证）：

  ```text
  /Users/gaorenjie/Documents/Coding/Company/PML/aiops-workflow/backend/app/workflow_v2/
    ├── graph_builder.py   # 原 814 行混合模块（C1 的拆分对象）
    ├── models.py          # DSL 模型（K1 来源）
    ├── node/base.py       # BaseNode 契约（K4 来源）
    ├── node/factory.py    # 插件工厂（K5 来源）
    ├── node/llm_node.py   # 多供应商 LLM（K10 来源）
    ├── node/http.py       # 模板化 HTTP（K10 来源）
    └── utils.py           # 状态工具（K9 来源）+ 待丢弃的 LLMHelper/Neo4j 逻辑
  ```

- **决策优先级**：CONTRACT（含 K/C/H/R） > 本套文档 > 原始代码惯例。原始代码中与契约冲突之处（硬编码领域字段、dispatcher 豁免、print 调试、硬编码密钥、死 try/except、无界缓存、构建期快照、API 不一致）一律按 C1..C9 与 H1..H7 修正，**不得**以「原代码就是这么写的」为由保留。

---

## 11. 文档维护约定

1. **单一事实源**：契约条款（K/C/H/R、Phase 划分、包结构、技术选型）只在 CONTRACT 中定义；四份文档是其在不同维度的展开，不得各自新增版本。
2. **同步修改**：任何条款变更必须同时更新本 README 的速查表与对应正文文档，保持编号、命名、阶段划分完全一致。
3. **不增编号**：确需新增条目时，沿用既有编号序列追加（如 K11、H8），并在四份文档中统一登记；禁止在某个文档里私自引入局部编号体系。
4. **语言口径**：中文叙述，代码标识符/路径/配置键保留英文；表格与代码块优先于长段落。
5. **可执行性**：`01-分阶段开发计划.md` 中每个 Phase 的任务必须是可被 AI 代理直接执行的条目（含输入模块、输出文件、测试命令、DoD），避免空泛描述。

---

*下一步：打开 `00-架构总览.md` 开始阅读。*
