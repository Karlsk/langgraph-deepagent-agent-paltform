# 03 · System Prompt 系统

> 本章覆盖：`system_prompt` 三形态（自定义字符串 / `claude_code` preset / 文件加载）、默认 minimal 提示的防踩坑语义、`exclude_dynamic_sections` 与 prompt cache、`setting_sources` 与 CLAUDE.md 注入路径、output styles 的现状。
>
> 版本基准：`claude-agent-sdk 0.2.135`（调研日期 2026-08-11）。导航见 [README.md](./README.md)。
> 主要出处：官方文档 <https://code.claude.com/docs/en/agent-sdk/modifying-system-prompts>、官方 Python 参考页 <https://code.claude.com/docs/en/agent-sdk/python>、`src/claude_agent_sdk/types.py`。

---

## 目录

1. [system_prompt 三形态](#1-system_prompt-三形态)
2. [默认提示是 minimal——防踩坑专列](#2-默认提示是-minimal防踩坑专列)
3. [preset 定制：append 与 exclude_dynamic_sections（prompt cache）](#3-preset-定制append-与-exclude_dynamic_sectionsprompt-cache)
4. [setting_sources、CLAUDE.md 与 output styles](#4-setting_sourcesclaudemd-与-output-styles)
5. [后续章节导航](#5-后续章节导航)

---

## 1. system_prompt 三形态

出处：`src/claude_agent_sdk/types.py::SystemPromptPreset / SystemPromptFile`、官方 modifying-system-prompts 页「How system prompts work」节。

`ClaudeAgentOptions.system_prompt: str | SystemPromptPreset | SystemPromptFile | None`，三种形态语义截然不同：

| 形态 | 写法 | 语义 |
|---|---|---|
| ① 自定义字符串 | `system_prompt="..."` | **完全自定义**：SDK 只发你写的内容，不含任何 Claude Code 内置指引；工具使用与安全指引须自行补齐 |
| ② preset | `system_prompt={"type": "preset", "preset": "claude_code", "append": "..."}` | Claude Code **全量提示**（工具指引、代码风格、安全规则、环境上下文），可选 `append` 追加自定义段落 |
| ③ 文件加载 | `system_prompt={"type": "file", "path": "/path/to/prompt.md"}` | 从文件加载自定义提示，映射为 CLI `--system-prompt-file` |

```python
from claude_agent_sdk import ClaudeAgentOptions

# ① 完全自定义（SDK 只发你写的字符串）
opts_custom = ClaudeAgentOptions(system_prompt="You are a SQL review bot. ...")

# ② Claude Code 预设 + 追加（最低风险的定制方式）
opts_preset = ClaudeAgentOptions(
    system_prompt={"type": "preset", "preset": "claude_code",
                   "append": "Always respond in zh-CN."},
)

# ③ 文件形态（长提示必备）
opts_file = ClaudeAgentOptions(
    system_prompt={"type": "file", "path": "./prompts/agent.md"},
)
```

**为什么要用文件形态（argv 长度限制）**：字符串形态经 CLI 子进程 argv 传递，受 OS 命令行长度限制约束——**Linux 单参数约 128 KB 超限即 spawn 失败（`Argument list too long`）；Windows 整个命令行约 32 KB 上限**，失败发生在任何 API 请求发出之前（出处：官方 Python 参考页 `SystemPromptFile` 节）。长提示一律用文件形态。

---

## 2. 默认提示是 minimal——防踩坑专列

> ⚠️ **不设 `system_prompt` 时，SDK 使用 minimal 提示**：只覆盖工具调用支持，**不含** Claude Code 的编码指引、响应风格与项目上下文；这与 `claude -p`（CLI 直接用全量 Claude Code 提示）行为不同。从 CLI 迁移想要对齐行为，必须显式选 `claude_code` preset（出处：官方 modifying-system-prompts 页「How system prompts work」节）。

选型速查（出处同上「decision table」）：

| 你在构建 | 选择 | 得到什么 |
|---|---|---|
| 有人值守的 CLI/IDE 式编码工具 | `claude_code` preset | 全量 Claude Code 提示 |
| 同上 + 产品专属规则 | preset + `append` | 全量提示 + 你的追加段（不删任何东西，风险最低） |
| 不同界面/身份/权限模型的 agent | 自定义字符串 | 只有你写的内容；工具与安全指引自负 |
| 无 agent 人格的薄工具调用循环 | 不设 `system_prompt` | minimal 默认：仅工具调用支持 |

---

## 3. preset 定制：append 与 exclude_dynamic_sections（prompt cache）

出处：官方 Python 参考页 `SystemPromptPreset` 节、官方 modifying-system-prompts 页「Improve prompt caching across users and machines」节。

```python
class SystemPromptPreset(TypedDict):
    type: Literal["preset"]
    preset: Literal["claude_code"]
    append: NotRequired[str]
    exclude_dynamic_sections: NotRequired[bool]
```

- `append`：追加到 preset 末尾，preset 内容不删减。
- `exclude_dynamic_sections=True`（**Python SDK `>=0.1.58` 支持**）：把每会话动态段落（工作目录、git 仓库标志、auto-memory 路径、平台等）从 system prompt **移入首条 user message**。

**为什么这是多租户场景的成本杠杆**：system prompt 越稳定，跨用户/跨机器的 **prompt cache 命中率越高**；动态段落移入 user message 后，system prompt 前缀对所有会话一致，缓存复用显著提升（出处：官方 Python 参考页 `exclude_dynamic_sections` 行）。

```python
opts = ClaudeAgentOptions(
    system_prompt={
        "type": "preset",
        "preset": "claude_code",
        "exclude_dynamic_sections": True,   # 动态段移入首条 user message
    },
)
```

---

## 4. setting_sources、CLAUDE.md 与 output styles

出处：官方 Python 参考页（`setting_sources` 行）、官方 modifying-system-prompts 页「CLAUDE.md files」节。

### 4.1 setting_sources 三态

`setting_sources: list[SettingSource] | None`，`SettingSource = "user" | "project" | "local"`：

| 取值 | 语义 |
|---|---|
| `None`（默认） | **CLI 默认行为：加载全部来源**（当前官方 Python 参考页口径） |
| `["project"]` 等 | 只加载所列来源 |
| `[]` | 禁用 user / project / local 全部文件系统设置 |

> ⚠️ **版本争议专列**：`setting_sources` 的默认语义曾有变动——`0.1.0` 更名时期一度改为「默认隔离（不加载）」；**当前官方 Python 参考页口径为 `None` = CLI defaults: all sources（默认加载全部）**。本文以官方最新口径为准（出处：官方 Python 参考页 `setting_sources` 行）。依赖此行为时建议锁定 SDK 版本并实测复核。
>
> ⚠️ **已知 bug**：`py <=0.1.59` 上 `setting_sources=[]`（空列表禁用）不生效；需要禁用语义时升级到更新版本（调研结论，落地前实测复核）。

### 4.2 CLAUDE.md 的注入路径（不影响 prompt cache）

**CLAUDE.md 被注入会话上下文（conversation），而非 system prompt**——因此它与任何 system prompt 配置兼容，也不参与 system prompt 的 prompt cache 前缀（出处：官方 modifying-system-prompts 页「Customize agent behavior」/「CLAUDE.md files」节）。

- `'project'` 来源加载工作目录的 `CLAUDE.md` 或 `.claude/CLAUDE.md`；`'user'` 来源加载 `~/.claude/CLAUDE.md`。
- CLAUDE.md 加载由 setting sources 控制，**与是否使用 `claude_code` preset 无关**。

```python
opts = ClaudeAgentOptions(
    system_prompt={"type": "preset", "preset": "claude_code"},
    setting_sources=["project"],   # 加载项目级 CLAUDE.md
)
```

### 4.3 output styles（现状）

output styles **只能通过文件/设置加载**（文件系统 settings 路径），Python SDK **无编程式选项**（🔧 缺口）——需要定制输出风格时，走 `claude_code` preset + `append` 或自定义字符串（出处：官方 modifying-system-prompts 页「Customize agent behavior」节）。

### 4.4 本章一分钟结论

| 目标 | 做法 |
|---|---|
| 编码 agent、想复用 Claude Code 全套行为 | preset（可选 `append`） |
| 多租户降本 | preset + `exclude_dynamic_sections=True`（提升 prompt cache 命中） |
| 超长提示 | 文件形态（规避 argv 限制） |
| 项目约定注入 | CLAUDE.md + `setting_sources` 含 `"project"`（走会话上下文，不碰 system prompt） |
| 完全确定性、零文件系统依赖 | 自定义字符串 + `setting_sources=[]`（注意 0.1.59 及以下空列表 bug） |

> 示例集见 [`examples/`](./examples/)（示例↔章节对照表见 [README.md 第 1 节](./README.md#1-文档清单)）。

---

## 5. 后续章节导航

| 下一步 | 文档 |
|---|---|
| 子代理的 system prompt（`AgentDefinition.prompt`）与继承关系 | [04-Sub-Agent架构.md](./04-Sub-Agent架构.md) |
| skills 发现路径对 `setting_sources` 的依赖 | [05-Skill系统.md](./05-Skill系统.md) |
| 上一章：模型选择与成本控制 | [02-LLM集成与成本控制.md](./02-LLM集成与成本控制.md) |
| 返回导航 | [README.md](./README.md) |
