# 08 · API 参考

> 本章覆盖：`create_deep_agent` 全参数表（逐条对应 `graph.py` docstring，**写作前已从 GitHub main 分支源码实测核对**）、`deepagents/__init__.py` 公共导出清单、内置工具清单、弃用时间线汇总。
>
> 版本基准：`deepagents 0.7.5`（调研日期 2026-08-11）。导航见 [README.md](./README.md)。
> 核心出处：`libs/deepagents/deepagents/graph.py`（函数签名 + docstring 实测）、`libs/deepagents/deepagents/__init__.py`（`__all__` 实测）、`libs/deepagents/CHANGELOG.md`（实测拉取）。

---

## 目录

1. [create_deep_agent 全参数表](#1-create_deep_agent-全参数表)
2. [返回值与异常](#2-返回值与异常)
3. [公共导出清单（\_\_init\_\_.py）](#3-公共导出清单__init__py)
4. [内置工具清单](#4-内置工具清单)
5. [弃用时间线汇总](#5-弃用时间线汇总)

---

## 1. create_deep_agent 全参数表

入口：`libs/deepagents/deepagents/graph.py::create_deep_agent()`。✅ 官方已实现。签名形态：`model` 与 `tools` 为位置参数，其余均为 keyword-only（`*` 之后）。

| 参数 | 类型 | 默认值 | 说明（docstring 要点） |
|---|---|---|---|
| `model` | `str \| BaseChatModel \| None` | `None` | 模型。接受 `provider:model` 字符串（如 `openai:gpt-5.5`，经 `resolve_model` → `init_chat_model` 解析）或预初始化的 `BaseChatModel` 实例。⚠️ `model=None` 自 `0.5.3` 弃用、`1.0.0` 移除（回退默认 `claude-sonnet-4-6`），届时类型收窄为 `BaseChatModel \| str`。OpenAI 模型默认走 Responses API |
| `tools` | `Sequence[BaseTool \| Callable \| dict[str, Any]] \| None` | `None` | 额外工具，与内置工具族（文件工具 / `execute` / `task`）**叠加合并**，不会移除内置项；裁剪内置工具须用 `HarnessProfile.excluded_tools` |
| `system_prompt` | `str \| SystemMessage \| None` | `None` | 调用方撰写指令（`USER` 段），置首；最终组装为 `USER` → `BASE` → `SUFFIX`（`BASE`/`SUFFIX` 来自激活的 HarnessProfile，空行分隔）。传 `SystemMessage` 时保留既有 content blocks 上的 `cache_control` 标记 |
| `middleware` | `Sequence[AgentMiddleware[StateT_co, ContextT]]` | `()` | 用户中间件：与既有项同名则**原位替换**，否则插入核心栈之后、尾部栈（profile / prompt-caching / memory）之前。完整装配顺序见 [01 章](./01-架构总览与运行时数据流.md) 第 3 节 |
| `subagents` | `Sequence[SubAgent \| CompiledSubAgent \| AsyncSubAgent] \| None` | `None` | 子代理规格三形态：声明式同步 `SubAgent`（可覆盖 `tools`/`model`/`middleware`/`interrupt_on`/`skills`/`permissions`/`response_format`）、预编译 `CompiledSubAgent`、远程/后台 `AsyncSubAgent`（按 `graph_id` 识别，路由到 `AsyncSubAgentMiddleware`）。未提供名为 `general-purpose` 的子代理且 profile 未禁用时自动注入默认通用子代理 |
| `skills` | `list[str] \| None` | `None` | skill source 路径列表（POSIX 路径，相对 backend 根），如 `["/skills/user/", "/skills/project/"]`。StateBackend 下经 `invoke(files={...})` 播种；FilesystemBackend 下相对 `root_dir` 读盘；同名 skill 后写覆盖（last-wins）。详见 [05 章](./05-Skill系统.md) |
| `memory` | `list[str] \| None` | `None` | 记忆文件（`AGENTS.md`）路径列表，如 `["/memory/AGENTS.md"]`；显示名自动从路径推导；启动时加载并注入 system prompt（`MemoryMiddleware`） |
| `permissions` | `list[FilesystemPermission] \| None` | `None` | 文件系统权限规则，按声明顺序评估、**首个匹配生效**，无匹配则放行。`mode`：`"allow"`（默认）/ `"deny"` / `"interrupt"`（暂停等人审，自动安装 `HumanInTheLoopMiddleware`，与 `interrupt_on` 合并时用户项按工具名优先）。子代理默认继承，自带 `permissions` 则整体替换。注意：权限在**工具层**执行，直接调用 backend 不受约束 |
| `backend` | `BackendProtocol \| None` | `None`（运行期缺省 `StateBackend()`） | 文件存储与执行后端；执行能力要求实现 `SandboxBackendProtocol`。backends 协议族见 [01 章](./01-架构总览与运行时数据流.md) 第 5 节 |
| `interrupt_on` | `dict[str, bool \| InterruptOnConfig] \| None` | `None` | 工具名 → 中断配置，在指定工具调用前暂停等待人审（需 checkpointer），如 `{"edit_file": True}`。声明式 `SubAgent` 默认继承、自带则覆盖；`CompiledSubAgent` / `AsyncSubAgent` **不继承** |
| `response_format` | `ResponseFormat[ResponseT] \| type[ResponseT] \| dict[str, Any] \| None` | `None` | 结构化输出格式 |
| `state_schema` | `type[DeepAgentState] \| None` | `None`（缺省 `DeepAgentState`） | 自定义 state，必须是 `DeepAgentState` 的 `TypedDict` 子类（以保留 `messages` 的 `DeltaChannel` 增量 reducer）。会随声明式 `SubAgent` 编译时下发；官方建议优先用中间件扩展 state 字段。该约束仅靠类型标注表达，运行时不做 `issubclass` 校验 |
| `context_schema` | `type[ContextT] \| None` | `None` | 运行作用域不可变 context 的 schema 类；透传给 `create_agent` |
| `checkpointer` | `Checkpointer \| None` | `None` | 跨 run 持久化 agent state；透传给 `create_agent` |
| `store` | `BaseStore \| None` | `None` | 持久化存储（backend 使用 `StoreBackend` 时必需）；透传给 `create_agent` |
| `debug` | `bool` | `False` | 调试模式；透传给 `create_agent` |
| `name` | `str \| None` | `None` | agent 名称；透传给 `create_agent`，并写入运行 metadata 的 `lc_agent_name` |
| `cache` | `BaseCache \| None` | `None` | agent 用缓存（`langgraph.cache.base.BaseCache`）；透传给 `create_agent` |

---

## 2. 返回值与异常

**返回**：`CompiledStateGraph[AgentState[ResponseT], ContextT, InputAgentState, OutputAgentState[ResponseT]]`——即 LangGraph 编译图，经 `.with_config()` 附加（出处：`graph.py` 函数体末尾）：

- `recursion_limit=9999`
- `metadata = {"ls_integration": "deepagents", "lc_versions": {"deepagents": ...}, "lc_agent_name": name}`

**Raises**（出处：`graph.py` docstring Raises 节）：

| 异常 | 触发条件 |
|---|---|
| `ImportError` | 缺少必需 provider 包或版本低于最低支持（如 `langchain-openrouter`） |
| `ValueError` | 激活 profile 的 `excluded_middleware`：触碰受保护脚手架（`FilesystemMiddleware` / `SubAgentMiddleware`，登记于 `_REQUIRED_MIDDLEWARE`）、使用下划线前缀私有名、匹配到多个不同中间件类、或在装配栈中未命中任何条目 |

---

## 3. 公共导出清单（\_\_init\_\_.py）

出处：`libs/deepagents/deepagents/__init__.py` 的 `__all__`（实测，共 19 个符号 + 版本号），按分组：

| 分组 | 导出 |
|---|---|
| 入口与状态 | `create_deep_agent`、`DeepAgentState`、`__version__` |
| 子代理 | `SubAgent`、`CompiledSubAgent`、`SubAgentMiddleware`、`AsyncSubAgent`、`AsyncSubAgentMiddleware` |
| 文件系统 | `FilesystemMiddleware`、`FilesystemPermission`、`FsToolName` |
| 记忆与评估 | `MemoryMiddleware`、`RubricMiddleware` |
| profiles | `HarnessProfile`、`HarnessProfileConfig`、`GeneralPurposeSubagentProfile`、`register_harness_profile`、`ProviderProfile`、`register_provider_profile` |

补充说明：

- `SkillsMiddleware` / `SkillMetadata` **不在顶层 `__all__` 中**，从 `deepagents.middleware.skills` 导入（该模块自身 `__all__ = ["SkillMetadata", "SkillsMiddleware"]`，出处：`middleware/skills.py` 末尾）。常规用法无需直接导入——传 `create_deep_agent(skills=[...])` 即可自动装配。
- `DeepAgentState` 的唯一增量：`messages` 使用 `DeltaChannel(_messages_delta_reducer, snapshot_frequency=50)`，checkpoint 增长从 O(N²) 降到 O(N)（出处：`graph.py::DeepAgentState`）。
- `get_default_model`（`graph.py`，返回 `ChatAnthropic(model_name="claude-sonnet-4-6")`）自 `0.5.3` 弃用、`1.0.0` 移除，且不在顶层导出。

---

## 4. 内置工具清单

出处：`graph.py::create_deep_agent` docstring 首段（「By default, this agent has access to the following tools」）与 [01 章](./01-架构总览与运行时数据流.md) 中间件栈表。

| 工具 | 职责 | 提供方 | 条件 |
|---|---|---|---|
| `ls` | 列目录 | `FilesystemMiddleware` | 始终可用 |
| `read_file` | 读文件（默认 100 行窗口） | `FilesystemMiddleware` | 始终可用 |
| `write_file` | 创建/整体覆盖写文件（0.7.0 起不再报「文件已存在」错误） | `FilesystemMiddleware` | 始终可用 |
| `edit_file` | 精确字符串替换式编辑 | `FilesystemMiddleware` | 始终可用 |
| `glob` | 文件名模式搜索 | `FilesystemMiddleware` | 始终可用 |
| `grep` | 内容正则搜索 | `FilesystemMiddleware` | 始终可用 |
| `execute` | 执行 shell 命令 | `FilesystemMiddleware`（backend 执行语义） | backend 须实现 `SandboxBackendProtocol`；否则调用返回错误信息 |
| `task` | 委派子代理 | `SubAgentMiddleware` | 存在任一同步子代理时（未提供则自动注入 general-purpose；彻底关闭后 `task` 不暴露） |

补充（出处：`CHANGELOG.md` 0.7.0 BREAKING CHANGES）：0.7.0 起 backend 支持时还会暴露破坏性、递归的 `delete` 文件工具，且权限体系把 `delete` 归为写操作——既有的写放行规则同样授权递归删除该子树，需要收紧时须显式补 deny / interrupt 规则或从 `FilesystemMiddleware(tools=...)` 中剔除 `delete`。

---

## 5. 弃用时间线汇总

出处：`graph.py` 的 `warn_deprecated` / `@deprecated` 标注（源码实测）+ `libs/deepagents/CHANGELOG.md`（实测拉取）。

### 5.1 当前生效的弃用（0.7.5 下会告警）

| 项 | 弃用版本 | 移除版本 | 替代方案 |
|---|---|---|---|
| `BASE_AGENT_PROMPT`（模块属性，经 `__getattr__` 兼容访问） | `0.7.0` | `0.9.0` | Deep Agents 不再提供撰写版 base prompt；需要旧行为可显式 `create_deep_agent(system_prompt=BASE_AGENT_PROMPT)`（出处：`graph.py::__getattr__`） |
| `model=None` / 依赖默认模型（`claude-sonnet-4-6`） | `0.5.3` | `1.0.0` | 显式构造模型（如 `ChatAnthropic(model_name=...)`）；届时 `model` 类型收窄为 `BaseChatModel \| str`（出处：`graph.py` 内 `warn_deprecated` 与 `get_default_model` 装饰器） |

### 5.2 近期已执行的移除（升级 0.7.x 时的破坏性清单，出处：CHANGELOG 0.7.0）

| 项 | 状态 | 说明 |
|---|---|---|
| `TodoListMiddleware` 默认装配（`write_todos` 工具 / `todos` state 通道 / todo prompt） | 0.7.0 移除默认 | 需显式 `middleware=[TodoListMiddleware()]` 恢复 |
| 内置工具 prompt 常量 `TASK_SYSTEM_PROMPT` / `ASYNC_TASK_SYSTEM_PROMPT` / `SUMMARIZATION_SYSTEM_PROMPT` / `FILESYSTEM_SYSTEM_PROMPT` / `EXECUTION_SYSTEM_PROMPT` | 0.7.0 移除 | 相应中间件 `system_prompt` 默认改为 `None`（不注入文案） |
| backend 兼容垫片（工厂式 backend、无 `namespace` 的 `StoreBackend`、旧 `ls`/`glob`/`grep`/`ReadResult` API） | 0.7.0 移除 | 必须传具体 `BackendProtocol` 实例 |
| `WriteResult.files_update` / `EditResult.files_update` | 0.7.0 移除 | state 写入由 `StateBackend` 直接发出 |
| `BackendProtocol.ls_info` / `als_info` / `glob_info` / `aglob_info` / `grep_raw` / `agrep_raw` | 0.7.0 移除 | 改用 `ls` / `glob` / `grep` 及异步对应 |
| `SummarizationMiddleware(history_path_prefix=...)` | 0.7.0 移除（调用即抛 `TypeError`） | 改用 `CompositeBackend(artifacts_root=...)` |
| `FilesystemBackend` / `LocalShellBackend` 未指定 `virtual_mode` 的弃用告警 | 0.7.0 收敛 | 默认 `virtual_mode=True`（路径锚定 `root_dir`、拒绝 `..` 逃逸）；旧行为须显式 `virtual_mode=False` |

### 5.3 行为变更备忘（非弃用但影响兼容，出处：CHANGELOG 0.7.0）

- `write_file` 变为「不存在即创建、存在即整体覆盖」，无 create-only 兼容模式。
- `ls` / `glob` 工具输出空结果渲染为 `No files found` 而非 `[]`（backend API 仍返回结构化空值）。
- `read_file` 的行号渲染格式变更（`LINE_NUMBER_WIDTH` 常量移除），解析原始输出的代码需更新。

---

*上一章：[07-流式输出.md](./07-流式输出.md) · 返回导航：[README.md](./README.md) · 下一章：[09-高级用法与二次开发最佳实践.md](./09-高级用法与二次开发最佳实践.md)*
