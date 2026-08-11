# 04 · Sub-Agent 架构

> 本章覆盖：`SubAgent` / `CompiledSubAgent` / `AsyncSubAgent` 三种形态、`task` 工具调度机制、状态隔离语义、继承规则、general-purpose 默认子代理、`response_format` 结构化回传。每条关键结论均标注源码出处。
>
> 版本基准：`deepagents 0.7.5`（调研日期 2026-08-11）。导航见 [README.md](./README.md)。
>
> 主要出处：`libs/deepagents/deepagents/middleware/subagents.py`、`libs/deepagents/deepagents/middleware/async_subagents.py`、`libs/deepagents/deepagents/graph.py`。

---

## 目录

1. [三形态对比](#1-三形态对比)
2. [task 工具调度机制](#2-task-工具调度机制)
3. [状态隔离语义](#3-状态隔离语义)
4. [继承规则表](#4-继承规则表)
5. [general-purpose 默认子代理](#5-general-purpose-默认子代理)
6. [response_format 结构化回传](#6-response_format-结构化回传)
7. [AsyncSubAgent 深入](#7-asyncsubagent-深入)

---

## 1. 三形态对比

`create_deep_agent(subagents=[...])` 接受三种形态混排，`graph.py` 按字段探测路由：spec 含 `graph_id` → `AsyncSubAgent`（走 `AsyncSubAgentMiddleware`）；含 `runnable` → `CompiledSubAgent`；其余按声明式 `SubAgent` 补全默认值（出处：`graph.py` 的 subagents 处理段）。

| 维度 | `SubAgent`（声明式） | `CompiledSubAgent`（预编译） | `AsyncSubAgent`（远程/后台） |
|---|---|---|---|
| 本质 | `TypedDict` 声明，由 deepagents 现场编译成 agent | 自带 `runnable`：任意含 `messages` 键的预编译 LangGraph 图 / `create_agent` 产物 | 远程 Agent Protocol 服务器上的 agent（LangSmith deployments / 自托管） |
| 必填字段 | `name`、`description`、`system_prompt` | `name`、`description`、`runnable` | `name`、`description`、`graph_id` |
| 可选字段 | `tools`、`model`、`middleware`、`interrupt_on`、`skills`、`permissions`、`response_format` | —（runnable 原样使用） | `url`、`headers` |
| 调度入口 | `task` 工具（同步阻塞） | `task` 工具（同步阻塞） | `start_async_task` 等一组异步工具（非阻塞，立即返回 task_id） |
| 中间件栈 | **自动获得独立中间件栈**（见下） | 不注入任何 deepagents 中间件，行为完全由调用方的 runnable 决定 | 运行在远端，本地只有任务簿记（`AsyncSubAgentState.async_tasks`） |
| 继承父配置 | 是（见第 4 节） | 否（state_schema / interrupt_on 均不继承） | 否（审批行为在远端 agent 自行配置） |
| 出处 | `middleware/subagents.py::SubAgent` | `middleware/subagents.py::CompiledSubAgent` | `middleware/async_subagents.py::AsyncSubAgent` |

实现状态：三者均 ✅ 官方已实现（`SubAgent`、`CompiledSubAgent`、`AsyncSubAgent` 及两个 Middleware 类均为 `deepagents/__init__.py` 公共导出）。

### 声明式 SubAgent 的独立中间件栈

每个 `SubAgent` 在 `graph.py` 中被补全为完整 spec 时，自动装配一条独立栈（出处：`graph.py` 的 SubAgent 处理段）：

```text
FilesystemMiddleware（共享父 backend，携带子代理 permissions）
  → SummarizationMiddleware（按子代理自身模型创建）
  → PatchToolCallsMiddleware
  → SkillsMiddleware（仅当 spec 声明 skills）
  → 子代理 HarnessProfile 的 extra_middleware
  → prompt-caching 中间件（append_prompt_caching_middleware）
  → excluded_middleware 过滤（前后各一次）
  → spec 自定义 middleware（插入核心栈之后、尾部之前）
  → _ToolExclusionMiddleware（profile 有 excluded_tools 时）
```

即子代理默认就拥有文件系统、摘要压缩与 prompt 缓存能力，且按其**自身模型**匹配 HarnessProfile——父子可以用不同模型、命中不同 profile。

---

## 2. task 工具调度机制

出处：`middleware/subagents.py::_build_task_tool`、`SubAgentMiddleware`。

`SubAgentMiddleware` 向主代理注入唯一的 `task` 工具（`StructuredTool`，同步 `task` + 异步 `atask` 双实现），入参 schema 为 `TaskToolSchema`：

| 参数 | 说明 |
|---|---|
| `description` | 交给子代理自主执行的任务详述（要求包含全部上下文与期望输出格式——子代理看不到对话历史） |
| `subagent_type` | 子代理名称，必须是工具描述中列出的可用类型之一 |

### {available_agents} 占位符

模型如何知道有哪些子代理可用？靠 `TASK_TOOL_DESCRIPTION` 中的 **`{available_agents}` 格式占位符**在构建 `task` 工具时动态注入（出处：`subagents.py::TASK_TOOL_DESCRIPTION` 与 `_build_task_tool`）：

```python
subagent_description_str = "\n".join(
    f"- {s['name']}: {s['description']}" for s in compiled_subagents
)
description = TASK_TOOL_DESCRIPTION.format(available_agents=subagent_description_str)
```

要点：

- 描述模板还包含使用指引：独立任务可在一条消息里并发发起多个 `task` 调用；每次调用无状态；子代理报告不直接展示给用户，需主代理转述等（出处：`TASK_TOOL_DESCRIPTION` 原文）；
- 自定义描述：经 `SubAgentMiddleware(task_description=...)`（`graph.py` 从 profile 的 `tool_description_overrides["task"]` 取）。自定义文本**应包含 `{available_agents}`**，否则模型看不到子代理清单；不含占位符时按原文使用；
- 未知 `subagent_type` 不抛异常，而是返回错误文本并列出合法类型（模型可自我纠正）。

### 调用与回传流程

```text
task(description, subagent_type)
  → 校验类型存在、tool_call_id 非空
  → 准备子代理 state（第 3 节的状态剥离）
  → subagent.invoke(state)          # 带 ls_agent_type="subagent" tracing 标记
  → _return_command_with_state_update(result, tool_call_id)
      → 提取回传内容（structured_response 或最后非空 AIMessage）
      → 返回 Command(update={...子代理状态更新, messages: [ToolMessage(content)]})
```

tracing 细节：子代理运行经 `_subagent_tracing_context()` 打上 LangSmith `ls_agent_type="subagent"` 元数据，父代理的 callbacks/tags/configurable 由 LangGraph `ensure_config` 自动传播（出处：`subagents.py` 注释与 `_subagent_tracing_context`）。

---

## 3. 状态隔离语义

出处：`middleware/subagents.py::_EXCLUDED_STATE_KEYS`、`_validate_and_prepare_state`、`_return_command_with_state_update`。

### 入向：子代理看到什么

```python
_EXCLUDED_STATE_KEYS = {"messages", "todos", "structured_response"}
```

- 子代理**收不到父代理的对话历史**：`messages` 被剥离后替换为单条 `HumanMessage(content=description)`——`task` 的 `description` 是子代理唯一的信息入口；
- `todos` 与 `structured_response` 一并剥离（无 reducer、跨代理传递无意义）；
- 同时剥离 `private_state_keys`：各中间件 state schema 中标记为私有的字段（由 `graph.py` 经 `private_state_field_names(*state_schemas)` 汇总后赋给 `SubAgentMiddleware.private_state_keys`）；
- 其余父状态键（含 `files` 等 backend 数据、自定义 state 字段）原样传入——子代理与父代理**共享 backend**，可读写同一文件空间。

### 出向：子代理回传什么

| 情形 | 回传内容 |
|---|---|
| 子代理产出非 `None` 的 `structured_response` | JSON 序列化后作为 `ToolMessage` 内容（Pydantic 用 `model_dump_json()`，dataclass 用 `asdict`，其余 `json.dumps`） |
| 否则 | 从 `messages` **倒序找最后一条文本非空的 `AIMessage`**（跳过 Anthropic 偶发的空 `end_turn` 尾消息） |

子代理状态更新同样过滤 `_EXCLUDED_STATE_KEYS` 与 `private_state_keys` 后合并回父状态（`Command(update=...)`）——即子代理对自定义 state 键、`files` 的写入能传回父代理，而消息历史只以一条 `ToolMessage` 摘要回流。**这就是子代理压缩上下文的核心机制**：多步中间过程留在子代理隔离的上下文窗口内，父代理只拿最终报告。

`DEFAULT_SUBAGENT_PROMPT` 也明确了这一契约（出处：`subagents.py`）：*“The calling agent only sees your final assistant message … Ensure your final response contains the complete answer.”*

---

## 4. 继承规则表

出处：`graph.py` 的 SubAgent 处理段与 `create_deep_agent` docstring。

| 项 | 默认行为 | 覆盖方式 |
|---|---|---|
| `tools` | **继承**父代理全部 `tools`（含描述覆写） | spec 显式给 `tools`（给 `[]` 即清零） |
| `model` | **继承**父代理模型 | spec 给 `model`（支持 `"provider:model"` 字符串或实例） |
| `permissions` | **继承**父代理规则 | spec 给 `permissions`——**整体替换**，不与父规则合并 |
| `interrupt_on` | **继承**顶层 `interrupt_on` | spec 给 `interrupt_on` 覆盖；最终还会与自身 `permissions` 的 interrupt 模式规则合并（用户项优先，出处：`_merge_fs_interrupt_on`） |
| `skills` | 自定义 `SubAgent` **不继承**（未声明即不装 `SkillsMiddleware`）；仅 general-purpose 默认子代理继承父 `skills` | spec 显式给 `skills` 路径列表 |
| `state_schema` | 声明式 `SubAgent` 继承 `create_deep_agent(state_schema=...)` | `CompiledSubAgent` 不继承（需自行用兼容 schema 编译） |

不继承的两类例外要记牢：

- **`CompiledSubAgent`**：runnable 原样使用，不继承 `state_schema`、`interrupt_on`、任何中间件（出处：`graph.py` docstring 与 `CompiledSubAgent` 类 docstring）；
- **`AsyncSubAgent`**：远程执行，审批与 HITL 行为在远端 agent 自行配置。

---

## 5. general-purpose 默认子代理

出处：`middleware/subagents.py::GENERAL_PURPOSE_SUBAGENT`、`graph.py` 自动注入段。实现状态：✅ 官方已实现。

- 未提供名为 `general-purpose` 的子代理时，`graph.py` **自动注入**默认通用子代理（`insert(0, ...)`，排在清单首位）：继承父模型、父 `tools`（含描述覆写）、父 `permissions`，并装配与主代理同构的中间件栈（含 `SkillsMiddleware`——父级传了 `skills` 时继承）；
- 描述来自 `DEFAULT_GENERAL_PURPOSE_DESCRIPTION`：*“General-purpose agent for researching complex questions, searching for files and content, and executing multi-step tasks …”*；`system_prompt` 为 `DEFAULT_SUBAGENT_PROMPT`，两者均可经 profile 覆盖；
- **关闭方式**：在激活的 `HarnessProfile` 上设 `general_purpose_subagent=GeneralPurposeSubagentProfile(enabled=False)`（`GeneralPurposeSubagentProfile` 另有 `description` / `system_prompt` 覆盖字段，出处：`harness_profiles.py`）；
- 若没有任何同步子代理（未传参且默认已禁用），`SubAgentMiddleware` 不装配，**`task` 工具不暴露**；`AsyncSubAgent` 不受影响（出处：`graph.py` docstring）；
- 显式提供同名 `general-purpose` spec 即为覆盖默认（`graph.py` 注释：explicit spec is how callers override the default）。

---

## 6. response_format 结构化回传

出处：`middleware/subagents.py::SubAgent.response_format`、`create_sub_agent`。实现状态：✅ 官方已实现。

声明式 `SubAgent` 可指定 `response_format`，子代理产出符合 schema 的 `structured_response`，JSON 序列化后作为 `ToolMessage` 内容回传（替代默认的最后消息提取，第 3 节）。接受形态（来自 `langchain.agents.structured_output`）：`ToolStrategy(schema)` / `ProviderStrategy(schema)` / `AutoStrategy(schema)` / 裸 Pydantic `BaseModel`（等价 `AutoStrategy`）/ JSON schema dict。

关键片段（出处：`subagents.py::SubAgent` docstring 示例）：

```python
from pydantic import BaseModel

class Findings(BaseModel):
    findings: str
    confidence: float

analyzer: SubAgent = {
    "name": "analyzer",
    "description": "Analyzes data and returns structured findings",
    "system_prompt": "Analyze the data and return your findings.",
    "model": "openai:gpt-5.5",
    "tools": [],
    "response_format": Findings,
}
```

补充机制：

- **动态覆盖**：`task` 调用方可经 `configurable` 键 `__deepagents_subagent_response_format`（`SUBAGENT_RESPONSE_FORMAT_CONFIG_KEY`）按次指定 response format，触发该子代理的即时重编译；
- 限制：动态 schema 仅适用于声明式 `SubAgent`——对 `CompiledSubAgent` 使用会抛 `ValueError`（出处：`_build_task_tool::_compile_spec`）；
- `CompiledSubAgent` 的结构化回传走另一条路：runnable 自身用 `create_agent(response_format=...)` 或在节点里直接写 `structured_response`（出处：`CompiledSubAgent` docstring 示例）。

---

## 7. AsyncSubAgent 深入

出处：`middleware/async_subagents.py`。实现状态：✅ 官方已实现（依赖 `langgraph-sdk`）。

定位：把**远程/后台** agent 纳入委派体系。经 LangGraph SDK 连接任意 Agent Protocol 兼容服务器（LangGraph Platform / LangSmith Deployment 托管，或自托管），启动后立即返回 task_id，主代理可异步监控、追加指令（出处：模块 docstring）。

| 字段 | 说明 |
|---|---|
| `graph_id` | 远端的 graph 名或 assistant ID（也是 `graph.py` 判定 AsyncSubAgent 的探测字段） |
| `url` | Agent Protocol 服务器地址；缺省用 SDK 默认端点，省略可走 ASGI transport（仅异步调用） |
| `headers` | 附加请求头；`_resolve_headers` 默认补 `x-auth-scheme: langsmith` |

鉴权：LangSmith 部署走 SDK 环境变量（`LANGGRAPH_API_KEY` / `LANGSMITH_API_KEY` / `LANGCHAIN_API_KEY`）；自托管经 `headers` 传自定义鉴权。

`AsyncSubAgentMiddleware` 注入的工具组（各有独立输入 schema）：

| 工具 | 作用 |
|---|---|
| `start_async_task` | 创建远端 thread 并启动后台 run，返回 task_id 并登记到 state |
| `check_async_task` | 查询任务状态/结果 |
| `update_async_task` | 向运行中任务发送后续指令 |
| `cancel_async_task` | 取消任务 |
| `list_async_tasks` | 列出任务（可按 `running/success/error/cancelled/all` 过滤） |

任务簿记：`AsyncSubAgentState.async_tasks`（dict，merge reducer）持久化每个任务的 `task_id`（同 thread_id）、`agent_name`、`run_id`、`status` 与创建/检查/更新时间戳——跨 checkpoint 可恢复跟踪。`start_async_task` 的描述模板同样内置使用约束：启动后立即报告 task_id 并停下，**不要**马上轮询（出处：`ASYNC_TASK_TOOL_DESCRIPTION`）。

与同步 `task` 的关系：两者独立装配（`graph.py` 中 async 清单单独进 `AsyncSubAgentMiddleware`），可共存；异步通道不参与第 4 节的任何继承逻辑。

---

*上一章：[03-System-Prompt系统.md](./03-System-Prompt系统.md) · 返回导航：[README.md](./README.md) · 下一章：[05-Skill系统.md](./05-Skill系统.md)*
