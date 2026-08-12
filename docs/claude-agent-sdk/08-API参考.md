# 08 · API 参考

> 本章为速查参考：`query()` 签名、`ClaudeSDKClient` 方法表、`ClaudeAgentOptions` 全字段表、消息/内容块类型清单、错误类型族、hooks 类型、权限评估序与公共导出清单。
>
> 版本基准：`claude-agent-sdk 0.2.135`（调研日期 2026-08-11）。导航见 [README.md](./README.md)。
>
> ⚠️ **字段级细节以官方 Python 参考页为准**：<https://code.claude.com/docs/en/agent-sdk/python>。官方页为滚动更新文档，本章按调研时点快照整理；类型定义同步对照 `src/claude_agent_sdk/types.py`、`src/claude_agent_sdk/client.py`、`src/claude_agent_sdk/__init__.py`。

---

## 目录

1. [query()](#1-query)
2. [ClaudeSDKClient](#2-claudesdkclient)
3. [ClaudeAgentOptions 全字段表](#3-claudeagentoptions-全字段表)
4. [消息与内容块类型清单](#4-消息与内容块类型清单)
5. [错误类型族](#5-错误类型族)
6. [Hooks 类型](#6-hooks-类型)
7. [权限评估](#7-权限评估)
8. [公共导出清单（__all__）](#8-公共导出清单__all__)
9. [后续章节导航](#9-后续章节导航)

---

## 1. query()

出处：`src/claude_agent_sdk/query.py`、官方文档 <https://code.claude.com/docs/en/agent-sdk/python>。

```python
async def query(
    *,
    prompt: str | AsyncIterable[dict[str, Any]],
    options: ClaudeAgentOptions | None = None,
    transport: Transport | None = None,
) -> AsyncIterator[Message]
```

| 参数 | 说明 |
|---|---|
| `prompt` | 字符串 = 单次模式；`AsyncIterable[dict]` = streaming input（仍为单向，见 [07 章第 5 节](./07-流式输出与交互式会话.md#5-输入模式streaming-input-vs-single-message)） |
| `options` | 可选配置；`None` 时等价 `ClaudeAgentOptions()` |
| `transport` | 可选自定义 transport（低层不稳定 API，见 [01 章第 3 节](./01-架构总览与运行时数据流.md#3-进程模型与开销)） |

默认每次调用新开会话；`continue_conversation=True` 或 `resume` 可续接（出处：官方 sessions 页）。语义细节（error result 后 raise、禁止提前 break）见 [07 章第 4 节](./07-流式输出与交互式会话.md#4-迭代纪律禁止提前-break)。

---

## 2. ClaudeSDKClient

出处：`src/claude_agent_sdk/client.py`、官方文档 <https://code.claude.com/docs/en/agent-sdk/python>（Classes 节）。

```python
class ClaudeSDKClient:
    def __init__(self, options: ClaudeAgentOptions | None = None, transport: Transport | None = None)
```

支持 `async with`（进入时 `connect()`、退出时 `disconnect()`）。方法表：

| 方法 | 签名要点 | 说明 |
|---|---|---|
| `connect` | `(prompt: str \| AsyncIterable[dict] \| None = None) -> None` | 建立连接（spawn CLI 子进程）；可携初始 prompt |
| `disconnect` | `() -> None` | 断开连接并回收子进程 |
| `query` | `(prompt: str \| AsyncIterable[dict], session_id: str = "default") -> None` | streaming mode 下发新请求 |
| `receive_messages` | `() -> AsyncIterator[Message]` | 持续接收全部消息（不自动停在 result） |
| `receive_response` | `() -> AsyncIterator[Message]` | 接收直到（含）`ResultMessage`；末条恒为 `ResultMessage` |
| `interrupt` | `() -> None` | 发送中断信号（仅 streaming mode） |
| `set_permission_mode` | `(mode: PermissionMode) -> None` | 会话中途切换权限模式 |
| `set_model` | `(model: str \| None = None) -> None` | 会话中途切换模型；`None` 重置为默认 |
| `rewind_files` | `(user_message_id: str) -> None` | 回滚文件到指定用户消息时点；需 `enable_file_checkpointing=True` |
| `get_mcp_status` | `() -> McpStatusResponse` | 全部 MCP server 连接状态 |
| `reconnect_mcp_server` | `(server_name: str) -> None` | 重连 failed / 断连的 server |
| `toggle_mcp_server` | `(server_name: str, enabled: bool) -> None` | 会话中途启停 server；停用即移除其工具 |
| `stop_task` | `(task_id: str) -> None` | 停止后台任务；随后消息流产出 `status="stopped"` 的 `TaskNotificationMessage` |
| `get_context_usage` | `() -> ContextUsageResponse` | 按类别拆分当前上下文窗口占用（出处：`src/claude_agent_sdk/client.py::get_context_usage`） |
| `get_server_info` | `() -> dict[str, Any] \| None` | server 信息（含 session ID 与 capabilities） |

> 交互方法（interrupt / set_model / set_permission_mode / rewind_files / MCP 管控 / stop_task）均为 streaming mode 专属；消息类型详见 [07 章](./07-流式输出与交互式会话.md)。

---

## 3. ClaudeAgentOptions 全字段表

出处：官方文档 <https://code.claude.com/docs/en/agent-sdk/python>（ClaudeAgentOptions 节）、`src/claude_agent_sdk/types.py::ClaudeAgentOptions`。共 45 字段，按用途分组；标注 *Deprecated* 者为官方明示弃用。

### 3.1 工具与权限

| 字段 | 类型 | 默认值 | 说明 |
|---|---|---|---|
| `tools` | `list[str] \| ToolsPreset \| None` | `None` | 基础内置工具集；`[]` = 全禁；`{"type": "preset", "preset": "claude_code"}` = Claude Code 默认全集 |
| `allowed_tools` | `list[str]` | `[]` | 免提示自动批准的工具；**不是白名单**——未列出的落到权限流，封禁用 `disallowed_tools` |
| `disallowed_tools` | `list[str]` | `[]` | 拒绝规则；裸名（如 `"Bash"`）直接把工具移出上下文，作用域规则（如 `"Bash(rm *)"`）在**所有权限模式**（含 `bypassPermissions`）下拒绝匹配调用 |
| `permission_mode` | `PermissionMode \| None` | `None` | 权限模式（枚举见第 7.2 节） |
| `can_use_tool` | `CanUseTool \| None` | `None` | 权限回调，仅在权限流落到「询问」环节时触发（签名见第 7.3 节） |
| `permission_prompt_tool_name` | `str \| None` | `None` | 用指定 MCP 工具承接权限提示 |
| `hooks` | `dict[HookEvent, list[HookMatcher]] \| None` | `None` | hooks 配置（见第 6 节） |

### 3.2 提示词与输出

| 字段 | 类型 | 默认值 | 说明 |
|---|---|---|---|
| `system_prompt` | `str \| SystemPromptPreset \| SystemPromptFile \| None` | `None` | 字符串 = 整体替换；preset = Claude Code 预设（可 `append`）；file = 从文件加载（大 prompt 规避 argv 长度限制）。详见 [03 章](./03-System-Prompt系统.md) |
| `output_format` | `dict[str, Any] \| None` | `None` | 结构化输出，如 `{"type": "json_schema", "schema": {...}}`；结果落 `ResultMessage.structured_output` |
| `thinking` | `ThinkingConfig \| None` | `None` | 扩展思考配置；优先于 `max_thinking_tokens` |
| `max_thinking_tokens` | `int \| None` | `None` | *Deprecated*——用 `thinking` 替代 |
| `effort` | `EffortLevel \| None` | `None` | 思考深度档位 |
| `betas` | `list[SdkBeta]` | `[]` | 启用的 beta 特性 |
| `task_budget` | `TaskBudget \| None` | `None` | API 侧 token 预算，形如 `{"total": <int>}` |

### 3.3 MCP 与插件

| 字段 | 类型 | 默认值 | 说明 |
|---|---|---|---|
| `mcp_servers` | `dict[str, McpServerConfig] \| str \| Path` | `{}` | MCP server 配置 dict，或 `.mcp.json` 路径（见 [06 章](./06-MCP集成.md)） |
| `strict_mcp_config` | `bool` | `False` | `True` 时只用显式传入的 server，忽略 `.mcp.json` / 用户设置 / plugin / claude.ai connectors |
| `plugins` | `list[SdkPluginConfig]` | `[]` | 从本地路径加载 plugin |
| `skills` | `list[str] \| Literal["all"] \| None` | `None` | 会话可用 skills；设置后 SDK 自动把 Skill 工具加入 `allowed_tools`；非法/通配名在启动前 `ValueError` |

### 3.4 会话控制

| 字段 | 类型 | 默认值 | 说明 |
|---|---|---|---|
| `continue_conversation` | `bool` | `False` | 继续最近一次会话 |
| `resume` | `str \| None` | `None` | 按 session id 恢复 |
| `session_id` | `str \| None` | `None` | 指定 session id（须合法 UUID；与 resume/continue 同用需 `fork_session`） |
| `fork_session` | `bool` | `False` | resume 时分叉为新 session |
| `max_turns` | `int \| None` | `None` | 最大 agentic 轮次（工具往返数） |
| `max_budget_usd` | `float \| None` | `None` | 客户端费用估算达此 USD 值即停止 |
| `enable_file_checkpointing` | `bool` | `False` | 启用文件变更跟踪（`rewind_files` 前置条件） |
| `session_store` | `SessionStore \| None` | `None` | transcript 外部镜像后端（跨主机 resume） |
| `session_store_flush` | `Literal["batched", "eager"]` | `"batched"` | 镜像刷新时机；`session_store` 为 `None` 时忽略 |
| `load_timeout_ms` | `int` | `60000` | resume 物化时 `session_store.load()` / `list_subkeys()` 单次超时（毫秒） |

### 3.5 模型与代理

| 字段 | 类型 | 默认值 | 说明 |
|---|---|---|---|
| `model` | `str \| None` | `None` | 模型别名或全名（见 [02 章](./02-LLM集成与成本控制.md)） |
| `fallback_model` | `str \| None` | `None` | 主模型失败时的回退模型 |
| `agents` | `dict[str, AgentDefinition] \| None` | `None` | 声明式子代理（见 [04 章](./04-Sub-Agent架构.md)） |

### 3.6 消息流开关

| 字段 | 类型 | 默认值 | 说明 |
|---|---|---|---|
| `include_partial_messages` | `bool` | `False` | 产出 token 级 `StreamEvent`（见 [07 章第 3 节](./07-流式输出与交互式会话.md#3-token-级流式streamevent)） |
| `include_hook_events` | `bool` | `False` | hooks 生命周期以 `HookEventMessage` 进入消息流 |
| `user` | `str \| None` | `None` | 用户标识 |

### 3.7 运行时 / 进程 / 文件系统

| 字段 | 类型 | 默认值 | 说明 |
|---|---|---|---|
| `cwd` | `str \| Path \| None` | `None` | 工作目录 |
| `cli_path` | `str \| Path \| None` | `None` | 自定义 CLI 可执行文件路径 |
| `settings` | `str \| None` | `None` | settings 文件路径 |
| `setting_sources` | `list[SettingSource] \| None` | `None`（CLI 默认全量） | 控制加载哪些文件系统设置（`"user"` / `"project"` / `"local"`）；传 `[]` 全禁 |
| `add_dirs` | `list[str \| Path]` | `[]` | 额外可访问目录 |
| `env` | `dict[str, str]` | `{}` | 合并覆盖到继承的进程环境变量（`API_TIMEOUT_MS`、`CLAUDE_CODE_MAX_RETRIES` 等超时变量由此注入） |
| `extra_args` | `dict[str, str \| None]` | `{}` | 直接透传给 CLI 的额外参数 |
| `max_buffer_size` | `int \| None` | `None` | CLI stdout 缓冲字节上限 |
| `debug_stderr` | `Any` | `sys.stderr` | *Deprecated*——用 `stderr` 回调替代 |
| `stderr` | `Callable[[str], None] \| None` | `None` | CLI stderr 输出回调 |
| `sandbox` | `SandboxSettings \| None` | `None` | 沙箱配置 |

---

## 4. 消息与内容块类型清单

字段级详情见 [07 章第 1、2 节](./07-流式输出与交互式会话.md#1-消息类型族全表)；此处仅列清单（出处：`src/claude_agent_sdk/types.py`）。

- **消息**：`SystemMessage`、`AssistantMessage`、`UserMessage`、`ResultMessage`、`StreamEvent`、`RateLimitEvent`（含 `RateLimitInfo` / `RateLimitStatus` / `RateLimitType`）、`TaskStartedMessage` / `TaskProgressMessage` / `TaskUpdatedMessage` / `TaskNotificationMessage`（含 `TaskNotificationStatus` / `TaskUpdatedStatus` / `TaskUsage` / `TERMINAL_TASK_STATUSES`）、`HookEventMessage`；联合类型 `Message`。
- **内容块**：`TextBlock`、`ThinkingBlock`、`ToolUseBlock`、`ToolResultBlock`、`ServerToolUseBlock`、`ServerToolResultBlock`（含 `ServerToolName`）；联合类型 `ContentBlock`；另有 `DeferredToolUse`、`ModelUsage`、`ContextUsageResponse` / `ContextUsageCategory`。
- **思考配置**：`ThinkingConfig = ThinkingConfigEnabled | ThinkingConfigDisabled | ThinkingConfigAdaptive`。

---

## 5. 错误类型族

出处：`src/claude_agent_sdk/_errors.py`（详见 [01 章第 5.2 节](./01-架构总览与运行时数据流.md#52-异常族)）。

| 异常 | 继承 | 触发场景 |
|---|---|---|
| `ClaudeSDKError` | `Exception` | 异常族基类 |
| `CLIConnectionError` | `ClaudeSDKError` | 与 CLI 子进程连接/通信失败 |
| `CLINotFoundError` | `CLIConnectionError` | 找不到 CLI 可执行文件（直接父类是 `CLIConnectionError`，捕获后者会一并兜住它） |
| `ProcessError` | `ClaudeSDKError` | CLI 子进程异常退出（携 `exit_code`、`stderr`） |
| `CLIJSONDecodeError` | `ClaudeSDKError` | stdout NDJSON 解码失败（携 `line`、`original_error`） |

> 补注：`_errors.py` 中另定义有 **`MessageParseError`**（CLI 输出消息解析失败，携 `data`），但**未在 `__init__.py` 公开导出**（不在 `__all__`），宿主代码不应依赖（出处：`src/claude_agent_sdk/_errors.py`、`__init__.py`）。

---

## 6. Hooks 类型

出处：官方文档 <https://code.claude.com/docs/en/agent-sdk/python>（Hook Types 节）、<https://code.claude.com/docs/en/agent-sdk/hooks>、`src/claude_agent_sdk/types.py`。

### 6.1 HookEvent 10 事件表

| 事件 | 触发时机 | 对应输入类型 |
|---|---|---|
| `PreToolUse` | 工具执行前 | `PreToolUseHookInput` |
| `PostToolUse` | 工具执行后 | `PostToolUseHookInput` |
| `PostToolUseFailure` | 工具执行失败后 | `PostToolUseFailureHookInput` |
| `UserPromptSubmit` | 用户提交 prompt 时 | `UserPromptSubmitHookInput` |
| `Stop` | 主会话停止时 | `StopHookInput` |
| `SubagentStop` | 子代理停止时 | `SubagentStopHookInput` |
| `SubagentStart` | 子代理启动时 | `SubagentStartHookInput` |
| `PreCompact` | 消息压缩前 | `PreCompactHookInput` |
| `Notification` | 通知事件 | `NotificationHookInput` |
| `PermissionRequest` | 需要权限决策时 | `PermissionRequestHookInput` |

> ⚠️ **Python 缺口（官方明示）**：TypeScript SDK 支持的部分额外 hook 事件（如 `SessionStart` / `SessionEnd`）**尚未在 Python 提供**（出处：官方 `/agent-sdk/python` 参考页 HookEvent 节 Note、<https://code.claude.com/docs/en/agent-sdk/hooks#available-hooks> 可用性表）。

所有输入类型继承 `BaseHookInput`（`session_id` / `transcript_path` / `cwd` / `permission_mode?`）；联合类型 `HookInput` 按 `hook_event_name` 判别。

### 6.2 回调签名与 HookMatcher

```python
HookCallback = Callable[[HookInput, str | None, HookContext], Awaitable[HookJSONOutput]]
#                        输入      tool_use_id   上下文（含 signal 预留位）

@dataclass
class HookMatcher:
    matcher: str | None = None          # 工具名/模式，如 "Bash"、"Write|Edit"；None = 匹配全部
    hooks: list[HookCallback] = field(default_factory=list)
    timeout: float | None = None        # 秒；省略时用事件默认值（多数事件 600，UserPromptSubmit 30）
```

配置入口：`ClaudeAgentOptions.hooks: dict[HookEvent, list[HookMatcher]]`。hooks 在 `query()` 与 `ClaudeSDKClient` 中**均可用**（0.2.135 内部恒 streaming mode，控制通道恒建立；唯一真实约束见 [06 章第 2.3 节](./06-MCP集成.md#23-可用性事实与真实约束02135-核实)）。

### 6.3 输出 HookJSONOutput

`HookJSONOutput = SyncHookJSONOutput | AsyncHookJSONOutput`。同步输出关键字段（出处：官方 `/agent-sdk/python` 参考页）：

| 字段 | 语义 |
|---|---|
| `continue_` | 是否继续（默认 `True`；Python 用下划线写法，SDK 自动转为 `continue`） |
| `suppressOutput` / `stopReason` | 隐藏 stdout / 停止原因 |
| `decision` / `reason` / `systemMessage` | `"block"` 决策 / 给 Claude 的反馈 / 给用户的警告 |
| `hookSpecificOutput` | 事件专属输出（判别联合） |

事件专属输出的控制字段：

- `PreToolUse`：`permissionDecision: "allow" | "deny" | "ask" | "defer"`（+ `permissionDecisionReason`）、`updatedInput`（改写工具入参）、`additionalContext`。
- `PostToolUse`：`additionalContext`、`updatedToolOutput`（改写工具输出；`updatedMCPToolOutput` 已弃用）。
- `UserPromptSubmit` / `Notification` / `SubagentStart` / `PostToolUseFailure`：`additionalContext`。
- `PermissionRequest`：`decision: dict`（程序化权限决策）。
- 异步形态 `AsyncHookJSONOutput`：`async_: True` + `asyncTimeout`（毫秒），延迟执行。

---

## 7. 权限评估

出处：官方文档 <https://code.claude.com/docs/en/agent-sdk/permissions>（How permissions are evaluated 节）。

### 7.1 六步评估序

Claude 请求工具时按下列顺序评估：

```text
① hooks（PreToolUse/PermissionRequest）— 可直接 deny 或放行；hook 返回 allow 不跳过后续 deny/ask 规则
② deny 规则 — disallowed_tools 与 settings.json；命中即封禁，bypassPermissions 也不例外
             （裸名 deny 在此之前已把工具移出上下文，此步只查作用域规则如 "Bash(rm *)"）
③ ask 规则 — settings.json 的 ask 规则命中则落到 can_use_tool 确认，bypassPermissions 也不例外
             （AskUserQuestion、requiresUserInteraction 的 MCP 工具、组织强制 ask 的 connector 同理）
④ permission_mode — bypassPermissions 批准到达此步的一切；acceptEdits 批准文件操作；plan 把写操作路由到 can_use_tool
⑤ allow 规则 — allowed_tools 与 settings.json 命中即批准
⑥ can_use_tool 回调 — 以上均未决出时落到宿主回调
```

> ⚠️ **`bypassPermissions` 会旁路 `allowed_tools` 语义**：该模式下到达第 ④ 步的调用一律放行，`allowed_tools` 不再起把关作用；要硬性封禁必须用 `disallowed_tools` 的作用域规则（第 ② 步在 bypass 下仍生效，出处：官方 permissions 页与 `disallowed_tools` 字段说明）。

### 7.2 PermissionMode 枚举

出处：`src/claude_agent_sdk/types.py`。

```python
PermissionMode = Literal["default", "acceptEdits", "plan", "bypassPermissions", "dontAsk", "auto"]
```

| 模式 | 语义 |
|---|---|
| `default` | 标准行为，危险操作提示确认 |
| `acceptEdits` | 自动接受文件编辑 |
| `plan` | 规划模式，写操作不自动批准 |
| `bypassPermissions` | 旁路全部权限检查（慎用） |
| `dontAsk` | 从不提示；未预批准的一律拒绝 |
| `auto` | 由模型侧分类器逐次批准/拒绝工具调用（出处：`src/claude_agent_sdk/query.py` docstring） |

### 7.3 CanUseTool 签名

出处：`src/claude_agent_sdk/types.py`。

```python
CanUseTool = Callable[
    [str, dict[str, Any], ToolPermissionContext], Awaitable[PermissionResult]
]
#  工具名    工具入参          上下文（suggestions / blocked_path / agent_id 等）

PermissionResult = PermissionResultAllow | PermissionResultDeny
```

`dontAsk` 模式下需要交互的工具（`AskUserQuestion` 等）直接拒绝而非落到回调（出处：官方 permissions 页）。

---

## 8. 公共导出清单（__all__）

出处：`src/claude_agent_sdk/__init__.py`（调研时点 `__all__`，按用途分组归纳；完整逐字清单以源码为准）。

| 分组 | 导出 |
|---|---|
| 入口 | `query`、`ClaudeSDKClient`、`Transport`、`__version__` |
| 选项与模式 | `ClaudeAgentOptions`、`PermissionMode`、`EffortLevel`、`SettingSource`、`TaskBudget`、`SdkBeta`、`ThinkingConfig` / `ThinkingConfigEnabled` / `ThinkingConfigDisabled` / `ThinkingConfigAdaptive` |
| MCP | `McpServerConfig`、`McpSdkServerConfig`、`McpServerStatus` / `McpServerStatusConfig` / `McpServerConnectionStatus` / `McpServerInfo` / `McpStatusResponse` / `McpToolAnnotations` / `McpToolInfo`、`create_sdk_mcp_server`、`tool`、`SdkMcpTool`、`ToolAnnotations` |
| 消息与内容块 | `Message`、`UserMessage` / `AssistantMessage` / `SystemMessage` / `ResultMessage`、`StreamEvent`、`RateLimitEvent` / `RateLimitInfo` / `RateLimitStatus` / `RateLimitType`、`TaskStartedMessage` / `TaskProgressMessage` / `TaskUpdatedMessage` / `TaskNotificationMessage`（+ 状态与 `TaskUsage` / `TERMINAL_TASK_STATUSES`）、`ModelUsage`、`DeferredToolUse`、`TextBlock` / `ThinkingBlock` / `ToolUseBlock` / `ToolResultBlock` / `ServerToolUseBlock` / `ServerToolResultBlock` / `ServerToolName` / `ContentBlock`、`ContextUsageResponse` / `ContextUsageCategory` |
| 权限回调 | `CanUseTool`、`CanUseToolShadowedWarning`、`ToolPermissionContext`、`PermissionResult` / `PermissionResultAllow` / `PermissionResultDeny`、`PermissionUpdate` |
| hooks | `HookCallback`、`HookContext`、`HookInput`、`BaseHookInput`、`HookEventMessage`、各事件 `*HookInput`（PreToolUse / PostToolUse / PostToolUseFailure / UserPromptSubmit / Stop / SubagentStop / PreCompact / Notification / SubagentStart / PermissionRequest）、`NotificationHookSpecificOutput` / `SubagentStartHookSpecificOutput` / `PermissionRequestHookSpecificOutput` / `PostToolUseFailureHookSpecificOutput`、`HookJSONOutput`、`HookMatcher` |
| 子代理与插件 | `AgentDefinition`、`SdkPluginConfig` |
| sessions（本地） | `list_sessions`、`get_session_info`、`get_session_messages`、`list_subagents`、`get_subagent_messages`、`SDKSessionInfo`、`SessionMessage`、`rename_session`、`tag_session`、`delete_session`、`fork_session`、`ForkSessionResult` |
| sessions（外部存储） | `SessionKey`、`SessionStore`、`SessionStoreEntry`、`SessionStoreFlushMode`、`SessionStoreListEntry`、`SessionSummaryEntry`、`SessionListSubkeysKey`、`InMemorySessionStore`、`fold_session_summary`、`MirrorErrorMessage`、`project_key_for_directory`、`import_session_to_store`、`*_from_store` / `*_via_store` 系列异步变体 |
| 沙箱 | `SandboxSettings`、`SandboxNetworkConfig`、`SandboxIgnoreViolations` |
| 错误 | `ClaudeSDKError`、`CLIConnectionError`、`CLINotFoundError`、`ProcessError`、`CLIJSONDecodeError` |

> 索引：字段级默认值与语义以官方参考页为准——Options：<https://code.claude.com/docs/en/agent-sdk/python>；hooks：<https://code.claude.com/docs/en/agent-sdk/hooks>；权限：<https://code.claude.com/docs/en/agent-sdk/permissions>；sessions：<https://code.claude.com/docs/en/agent-sdk/sessions>、<https://code.claude.com/docs/en/agent-sdk/session-storage>；MCP：<https://code.claude.com/docs/en/agent-sdk/mcp>。

---

## 9. 后续章节导航

| 下一步 | 文档 |
|---|---|
| streaming / 交互客户端 / sessions / FastAPI SSE 对接 | [07-流式输出与交互式会话.md](./07-流式输出与交互式会话.md) |
| 外部与进程内 MCP 集成 | [06-MCP集成.md](./06-MCP集成.md) |
| 自定义 Transport / can_use_tool 实战 / 集成评估 | [09-高级用法与二次开发最佳实践.md](./09-高级用法与二次开发最佳实践.md) |
| 返回导航 | [README.md](./README.md) |
