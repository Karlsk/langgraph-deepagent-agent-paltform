# 05 · Skill 系统

> 本章覆盖：skills 的仅文件系统形态（🔧 无程序化注册 API）、`SKILL.md` 结构与发现路径、`skills` 选项四态语义、「上下文过滤器 ≠ 沙箱」警示、`allowed-tools` frontmatter 在 SDK 下不生效的陷阱，以及排障清单。
>
> 版本基准：`claude-agent-sdk 0.2.135`（调研日期 2026-08-11）。导航见 [README.md](./README.md)。
> 主要出处：官方文档 <https://code.claude.com/docs/en/agent-sdk/skills>、官方 Python 参考页 <https://code.claude.com/docs/en/agent-sdk/python>（`skills` 行）、`src/claude_agent_sdk/types.py`。

---

## 目录

1. [仅文件系统形态：无程序化注册 API（🔧）](#1-仅文件系统形态无程序化注册-api)
2. [SKILL.md 结构与发现路径](#2-skillmd-结构与发现路径)
3. [skills 选项四态](#3-skills-选项四态)
4. [警示：上下文过滤器 ≠ 沙箱](#4-警示上下文过滤器--沙箱)
5. [排障清单](#5-排障清单)
6. [后续章节导航](#6-后续章节导航)

---

## 1. 仅文件系统形态：无程序化注册 API（🔧）

出处：官方 skills 页「How Skills work in the SDK」节（原文：「Unlike subagents (which can be defined programmatically), Skills must be created as filesystem artifacts. The SDK does not provide a programmatic API for registering Skills.」）。

Skills 在 SDK 中的五个事实：

1. **以文件形态定义**：每个 skill = 独立目录下的 `SKILL.md`，如 `.claude/skills/<name>/SKILL.md`；
2. **从文件系统加载**：加载位置受 `setting_sources` 管辖；
3. **自动发现**：启用文件系统设置后，SDK 启动时从 user / project 目录发现 skill **元数据**，Claude 调用时才加载全文；
4. **模型自主调用**：Claude 依据上下文自行决定何时使用；
5. **经 `skills` 选项过滤**：发现的 skills 默认可用，可传名单 / `"all"` / `[]` 控制会话内可用集。

> 🔧 **缺口结论**：与子代理（`agents` 参数程序化定义）不同，skills **无程序化注册 API**；多租户/动态 skill 场景只能借助文件系统写入、`plugins` 选项（从指定路径加载 skills）或 SDK MCP server 模拟（工具形态非 skill 形态），落地需自行设计。

---

## 2. SKILL.md 结构与发现路径

出处：官方 skills 页「Creating Skills」「How Skills work in the SDK」节。

```markdown
---
name: pdf-processing
description: Extract text and tables from PDF files, fill forms, merge documents.
---

# PDF Processing

When the user asks to process a PDF, ...（Markdown 正文：何时用、怎么用）
```

- 结构：**YAML frontmatter（`name` / `description` 等）+ Markdown 正文**；**`description` 决定 Claude 何时调用该 skill**——写得含糊则触发率低。
- 名字匹配：`skills` 名单按 `SKILL.md` 的 `name` 字段或目录名匹配；plugin 提供的 skill 用 `plugin:skill` 限定名。

**发现路径依赖 `setting_sources`**：

| 来源 | 路径 |
|---|---|
| `user` | `~/.claude/skills/` |
| `project` | `<cwd>/.claude/skills/`，及从 `<cwd>` **向上至仓库根**的各级 `.claude/skills/` |

不设 `setting_sources` 时加载**全部来源（user / project / local，matches CLI defaults）**，skills 自动可发现；**显式设置 `setting_sources` 时须包含 `"user"` / `"project"`**，否则 skills 不加载。另有一个自动收敛：**设置 `skills` 选项且未显式设 `setting_sources` 时，SDK 自动把 `setting_sources` 收敛为 `["user", "project"]`**，使 CLI 无需调用方手工联动两个选项即可发现已安装 skills（出处：官方 skills 页 Note 与「Skills Not Found」节、`src/claude_agent_sdk/_internal/transport/subprocess_cli.py::_apply_skills_defaults`）。

```python
options = ClaudeAgentOptions(
    setting_sources=["user", "project"],   # 文件系统来源：skills 发现的前提
    skills="all",                          # 启用全部发现的 skill
)
```

---

## 3. skills 选项四态

出处：官方 Python 参考页 `skills` 行、官方 skills 页「Filtering skills」相关节。

`skills: list[str] | Literal["all"] | None`：

| 取值 | 语义 |
|---|---|
| `None` | CLI 默认：发现的 skills 全部可用 |
| `"all"` | 显式启用全部发现的 skill（要全量就用 `"all"`，**不接受通配符**） |
| `["a", "b"]` | 精确名单；**非法名（空名、含括号/逗号/控制字符）与通配符形式在启动 CLI 子进程前抛 `ValueError`** |
| `[]` | 禁用全部 skills |

两个联动规则：

- 设置 `skills` 后，**SDK 自动把 `Skill` 工具加入 `allowed_tools`**；若你同时显式传 `tools`，须自行在列表中包含 `"Skill"`。
- 子代理可经 `AgentDefinition.skills` 预载指定 skills（未列出的仍可经 Skill 工具调用），见 [04 章](./04-Sub-Agent架构.md)。

### 3.1 选型对照：skills vs 子代理 vs 进程内工具

| 维度 | skills | 子代理（agents） | 进程内 MCP 工具 |
|---|---|---|---|
| 注册形态 | 仅文件系统（🔧） | 程序化 / 文件系统 | 程序化（`create_sdk_mcp_server`；`query()` / `ClaudeSDKClient` 均可用） |
| 作用 | 按需注入的领域知识/流程 | 隔离上下文的独立 agent | 可调用的函数能力 |
| 触发方 | 模型自主（看 `description`） | 父代理经 Agent 工具委派 | 模型工具调用 |
| 上下文 | 进入当前会话上下文 | 全新上下文，只回最终消息 | 工具结果回注 |

> 简记：**要知识用 skill，要隔离用子代理，要能力用工具**（出处：官方 skills / subagents / custom-tools 页定位综合）。

---

## 4. 警示：上下文过滤器 ≠ 沙箱

出处：官方 skills 页（原文：「The `skills` option is a context filter, not a sandbox. Unlisted Skills are hidden from the model and rejected by the Skill tool, but their files remain on disk and are reachable through Read and Bash.」）。

> ⚠️ **安全警示（专列）**：`skills` 过滤只控制「模型能看见/能经 Skill 工具调用哪些 skill」；**未列出的 skill 文件仍在磁盘上，可被 `Read` / `Bash` 等工具直接访问**。隔离敏感 skill 必须依赖文件系统权限 / 目录隔离，不能指望 `skills` 名单。
>
> 落地建议：含敏感内容的 skill 目录不要放在子进程 `cwd` 可达路径上；配合 `disallowed_tools` / 受限 `allowed_tools` 双闸控制文件访问面。

> ⚠️ **陷阱专列（`allowed-tools` frontmatter）**：`SKILL.md` frontmatter 的 `allowed-tools` 字段**仅在直接使用 Claude Code CLI 时生效，经 SDK 使用时不生效**——SDK 场景的工具访问控制一律用 `ClaudeAgentOptions.allowed_tools`（出处：官方 skills 页「Tool Restrictions」节）。

```python
options = ClaudeAgentOptions(
    setting_sources=["user", "project"],
    skills="all",
    allowed_tools=["Read", "Grep", "Glob"],   # SDK 场景唯一的工具闸（SKILL.md 的 allowed-tools 无效）
    permission_mode="dontAsk",                # 未预批准的一律拒绝，而非交互式提示
)
```

---

## 5. 排障清单

出处：官方 skills 页「Troubleshooting」（「Skills Not Found」等）节。

| 症状 | 排查项 |
|---|---|
| Claude 不使用 skill | ① `setting_sources` 是否含 `user` / `project`；② 目录名/`name` 字段是否与 `skills` 名单精确一致；③ `description` 是否清晰描述触发时机 |
| 启动即 `ValueError` | `skills` 名单含非法名（空串、括号、逗号、控制字符）或通配符；全量请改用 `"all"` |
| skill 被加载但不可列出 | frontmatter `user-invocable: false` 的 skill 不进入 init 消息的 `skills` 数组，但对 Claude 仍可用 |
| Skill 工具被权限拦截 | 显式传了 `tools` 却没包含 `"Skill"`；或 `allowed_tools` 未含 `"Skill"`（未设 `skills` 选项时不会自动加入） |
| 想确认加载结果 | 消息流开头的 `SystemMessage`（subtype `init`）携带 `skills` 数组，可据此断言加载成功 |
| 工具限制不生效 | `SKILL.md` 的 `allowed-tools` frontmatter 在 SDK 下无效；改用 `allowed_tools` 选项 |
| 引用 plugin 提供的 skill | `skills` 名单用 `plugin:skill` 限定名；plugin 路径加载见 `plugins` 选项（详见 [06-MCP集成.md 第 4 节](./06-MCP集成.md#4-plugins-选项本地-plugin-加载)） |

> 完整示例见 [`examples/05_skills_agent.py`](./examples/05_skills_agent.py)（skill 定义见 [`examples/05_skills/demo-skill/SKILL.md`](./examples/05_skills/demo-skill/SKILL.md)）。

---

## 6. 后续章节导航

| 下一步 | 文档 |
|---|---|
| 子代理 skills 预载与 `AgentDefinition` | [04-Sub-Agent架构.md](./04-Sub-Agent架构.md) |
| plugins 加载指定路径 skills / SDK MCP server | [06-MCP集成.md 第 4 节](./06-MCP集成.md#4-plugins-选项本地-plugin-加载) |
| `setting_sources` 语义与版本争议 | [03-System-Prompt系统.md](./03-System-Prompt系统.md) |
| 返回导航 | [README.md](./README.md) |
