# 04 · Sub-Agent 架构

> 本章覆盖：子代理三种定义方式（程序化 / 文件系统 / 内置 general-purpose）、`AgentDefinition` 字段与 camelCase 陷阱、经 Agent 工具调用的机制、上下文隔离与继承、后台运行与任务消息、嵌套深度与并发、子代理会话恢复。
>
> 版本基准：`claude-agent-sdk 0.2.135`（调研日期 2026-08-11）。导航见 [README.md](./README.md)。
> 主要出处：官方文档 <https://code.claude.com/docs/en/agent-sdk/subagents>、官方 Python 参考页 <https://code.claude.com/docs/en/agent-sdk/python>（`AgentDefinition` 节）、`src/claude_agent_sdk/types.py`。

---

## 目录

1. [三种定义方式](#1-三种定义方式)
2. [AgentDefinition 字段与 camelCase 陷阱](#2-agentdefinition-字段与-camelcase-陷阱)
3. [调用机制：Agent 工具](#3-调用机制agent-工具)
4. [上下文隔离与继承](#4-上下文隔离与继承)
5. [后台运行与任务消息](#5-后台运行与任务消息)
6. [嵌套深度、并发与会话恢复](#6-嵌套深度并发与会话恢复)
7. [后续章节导航](#7-后续章节导航)

---

## 1. 三种定义方式

出处：官方 subagents 页「Ways to define subagents」节。

| 方式 | 形态 | 说明 |
|---|---|---|
| ① **程序化（推荐）** | `ClaudeAgentOptions.agents: dict[str, AgentDefinition]` | SDK 应用的首选；声明即代码，可测试可版本化 |
| ② 文件系统 | `.claude/agents/*.md` | Claude Code 文件式定义；**须 `setting_sources` 含 `project` / `user`** 才会加载 |
| ③ 内置 `general-purpose` | 无需定义 | Claude 可随时经 Agent 工具调用它做研究/探索类委派 |

同名冲突时：**程序化定义覆盖文件定义**（出处：官方 subagents 页「Filesystem-based definition」节）。程序化选项（`agents`、`allowed_tools` 等）覆盖 user/project/local 文件系统设置；但受管策略（managed policy）优先于程序化选项（出处：官方 Python 参考页 `agents` 节示例注释）。

```python
from claude_agent_sdk import AgentDefinition, ClaudeAgentOptions

options = ClaudeAgentOptions(
    agents={
        "code-reviewer": AgentDefinition(
            description="Reviews code changes",                # 必填：何时用它
            prompt="You are a code reviewer. ...",             # 必填：它的 system prompt
            model="inherit",                                   # 继承父代理模型
            maxTurns=10,
        ),
    },
    allowed_tools=["Agent"],   # 让 Agent 工具调用免权限提示
)
```

### 1.1 何时用子代理（官方三类场景）

出处：官方 subagents 页开篇与「Context isolation」「Parallelization」节。

| 场景 | 示例 |
|---|---|
| **上下文隔离** | `research-assistant` 探索几十个文件，内容不累积进主会话，父代理只拿摘要 |
| **并行化** | code review 同时跑 `style-checker` / `security-scanner` / `test-coverage`，总耗时 = 最慢者 |
| **专业化指令** | 领域专家角色不污染主代理 prompt（专属 system prompt + 受限工具集） |

---

## 2. AgentDefinition 字段与 camelCase 陷阱

出处：`src/claude_agent_sdk/types.py::AgentDefinition`、官方 subagents 页字段表、官方 Python 参考页 `AgentDefinition` 节。

| 字段 | 必填 | 语义（一句话） |
|---|---|---|
| `description` | ✅ | 自然语言描述「何时使用该 agent」——父代理据此决定委派 |
| `prompt` | ✅ | 子代理的 system prompt |
| `tools` | — | 允许的工具名单；省略则继承子代理可用的全部工具 |
| `disallowedTools` | — | 从工具集中移除的工具名（支持 `mcp__server` / `mcp__server__*` / `mcp__*` 模式） |
| `model` | — | 模型别名（`opus` / `sonnet` / `haiku` / `inherit`）或完整 ID；省略 = 主模型 |
| `skills` | — | 启动时预载入上下文的 skill 名单；未列出的 skill 仍可经 Skill 工具调用 |
| `memory` | — | 记忆来源：`'user'` / `'project'` / `'local'` |
| `mcpServers` | — | 该 agent 可用的 MCP server（按名称或内联配置） |
| `initialPrompt` | — | 作为**主线程 agent** 运行时自动提交的首轮 user 消息；作为子代理被调用时忽略 |
| `maxTurns` | — | 最大 agentic turn 数 |
| `background` | — | 强制该 agent 以后台任务运行 |
| `effort` | — | 推理深度：`'low' / 'medium' / 'high' / 'xhigh' / 'max'` 或数值 |
| `permissionMode` | — | 该 agent 内工具执行的权限模式 |

> ⚠️ **陷阱专列（camelCase）**：Python SDK 中 `disallowedTools`、`mcpServers`、`initialPrompt`、`maxTurns`、`permissionMode` 等**保留 camelCase wire 格式拼写**——写 snake_case（如 `max_turns`）**会直接 TypeError**，不遵循 Python 惯例是有意为之（出处：官方 subagents 页字段表注记、官方 Python 参考页 `AgentDefinition` 节）。

---

## 3. 调用机制：Agent 工具

出处：官方 subagents 页、官方 Python 参考页。

- 子代理由父代理经 **Agent 工具** 发起调用；`allowed_tools` 中包含 `"Agent"` 可让调用免权限提示自动放行。
- **工具名跨版本兼容注意**：Claude Code **v2.1.63 之前该工具名为 `Task`**；编写跨 CLI 版本的允许/拒绝规则时须**双名匹配**（`"Agent"` 与 `"Task"`）。
- 委派时**唯一传给子代理的内容是 Agent 工具的 prompt 字符串**——需要的文件路径、错误信息、决策结论都要写进该 prompt（见第 4 节）。

---

## 4. 上下文隔离与继承

出处：官方 subagents 页「Context isolation」「What subagents inherit」节。

- **全新上下文窗口**：子代理的上下文从空白开始，无父会话历史；父代理传给它的只有 Agent 工具的 prompt 字符串。
- **子代理的 system prompt = 自己的 `prompt`**；中间工具调用与结果留在子代理内部，**父代理只收到最终消息**（典型场景：`research-assistant` 读几十个文件，父会话只拿一段摘要）。
- **消息归属标识**：子代理内部产生的消息以 `parent_tool_use_id` 标识其来源（宿主过滤/归组子代理消息用，出处：`src/claude_agent_sdk/types.py` 消息字段）。
- **继承规则**（v2.1.198 起）：子代理继承主会话的扩展思考（thinking）配置；模型可用 `model: "inherit"` 显式继承。
- 持有 `SendMessage` 工具的子代理首轮会自动拿到会话内其他命名 agent 名单（需 Claude Code v2.1.206+；fork 除外）。

---

## 5. 后台运行与任务消息

出处：官方 subagents 页（v2.1.198 行为变更）、官方 Python 参考页（`stop_task`、任务消息类型）。

- **v2.1.198 起子代理默认后台运行**：Agent 工具调用省略 `run_in_background` 入参即启动后台子代理；Claude 需要结果再继续时会显式传 `run_in_background: false`。v2.1.198 之前省略 = 同步运行（跨版本行为差异，集成时注意）。
- `AgentDefinition.background=True` 可**强制**后台执行（无视 Claude 的请求）。
- 后台任务生命周期经消息流观测：`TaskStartedMessage` / `TaskProgressMessage` / `TaskNotificationMessage`；`ClaudeSDKClient.stop_task(task_id)` 停止后台任务（随后消息流产出 status 为 `"stopped"` 的 `TaskNotificationMessage`）。

```python
# 强制后台的子代理定义（仅关键片段）
"log-miner": AgentDefinition(
    description="Mines logs for incident root causes",
    prompt="You are a log analysis specialist. ...",
    background=True,     # 无视 Claude 请求，强制后台
),
```
- 后台子代理有停滞看门狗：`CLAUDE_ASYNC_AGENT_STALL_TIMEOUT_MS`（默认 `600000`，每个流事件重置；停滞即中止并上报部分结果）——仅对后台子代理生效（出处：官方 Python 参考页 env 节）。

---

## 6. 嵌套深度、并发与会话恢复

出处：官方 subagents 页（Note 节与并行节）、官方 Python 参考页。

| 主题 | 事实 |
|---|---|
| 嵌套深度 | 默认允许子代理再生子代理，**主会话之下最多 3 层**；`CLAUDE_CODE_MAX_SUBAGENT_SPAWN_DEPTH` 可改（设 `1` 关闭嵌套） |
| 并发委派 | 多个子代理可**并发运行**，独立子任务总耗时 = 最慢者（如 code review 同时跑 `style-checker` / `security-scanner` / `test-coverage`） |
| 会话恢复 | 可经 `agentId` + `resume` 恢复子代理会话（会话机制详见 [07-流式输出与交互式会话.md](./07-流式输出与交互式会话.md)） |

> ⚠️ 并发子代理 = 并发 LLM 调用，成本与限流压力线性放大；配合 [02 章](./02-LLM集成与成本控制.md) 的 `max_turns` / `max_budget_usd` 与 `AgentDefinition.maxTurns` 一起设闸。
>
> 完整示例见 [`examples/04_subagents.py`](./examples/04_subagents.py)。

---

## 7. 后续章节导航

| 下一步 | 文档 |
|---|---|
| skills 系统与 `AgentDefinition.skills` 预载 | [05-Skill系统.md](./05-Skill系统.md) |
| `mcpServers` 字段对应的 MCP 配置 | [06-MCP集成.md](./06-MCP集成.md) |
| 模型别名与成本控制 | [02-LLM集成与成本控制.md](./02-LLM集成与成本控制.md) |
| system prompt 语义（与 `AgentDefinition.prompt` 对照） | [03-System-Prompt系统.md](./03-System-Prompt系统.md) |
| 返回导航 | [README.md](./README.md) |
