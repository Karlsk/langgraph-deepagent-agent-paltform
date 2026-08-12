# Claude Agent SDK 调研文档 · 导航（README）

> **一句话定位**：本套文档是对 Anthropic 官方 [Claude Agent SDK for Python](https://github.com/anthropics/claude-agent-sdk-python)（以子进程驱动 Claude Code CLI 的 Python agent 构建工具包）的源码级调研，目标是评估其在本项目（pml-langgraph-agent）中的集成与借鉴价值。

- 文档全部用**中文**书写；代码标识符、文件路径、环境变量保留**英文**。
- 本 README 只做**导航与速查**；细节一律以对应章节文档为准。
- 所有关键结论均以 `claude-agent-sdk 0.2.135` 源码与官方文档为基准，逐条标注出处（官方文档页 URL 或 GitHub 源码文件路径）。

---

## 目录

1. [文档清单](#1-文档清单)
2. [推荐阅读顺序](#2-推荐阅读顺序)
3. [版本锚点](#3-版本锚点)
4. [更名与迁移速查](#4-更名与迁移速查)
5. [实现状态图例（三档标注）](#5-实现状态图例三档标注)
6. [本地依赖速查](#6-本地依赖速查)

---

## 1. 文档清单

| 文件 | 内容（一句话） | 状态 |
|---|---|---|
| `README.md`（本文件） | 文档导航、阅读顺序、版本锚点、更名迁移速查、实现状态图例、本地依赖速查 | ✅ |
| `00-安装与快速入门.md` | 安装方式（uv / pip）、捆绑原生 CLI 机制三态、`ANTHROPIC_API_KEY` 配置、最小 `query()` quickstart、独立 venv 验证方案 | ✅ |
| `01-架构总览与运行时数据流.md` | 子进程架构（SDK → SubprocessCLITransport → NDJSON → CLI）、`query()` vs `ClaudeSDKClient` 选型、进程模型与孤儿清理、消息生命周期、异常族 | ✅ |
| `02-LLM集成与成本控制.md` | 模型选择与别名、重试/超时（env 注入）、thinking / effort / beta、网关接入（Bedrock / Vertex / Foundry）、成本控制与用量观测 | ✅ |
| `03-System-Prompt系统.md` | `system_prompt` 三形态、默认 minimal 提示语义、`exclude_dynamic_sections` 与 prompt cache、`setting_sources` 与 CLAUDE.md | ✅ |
| `04-Sub-Agent架构.md` | `AgentDefinition` 声明式子代理、CLI 侧 Agent 工具委派、权限继承 | ✅ |
| `05-Skill系统.md` | skills 加载规则（目录名匹配、`plugin:skill` 限定名）、`allowed_tools` 中的 `Skill(...)` 规则 | ✅ |
| `06-MCP集成.md` | 外部 MCP server 配置（stdio / SSE / HTTP）、进程内 SDK MCP server（`create_sdk_mcp_server` + `@tool`）、server 状态管理 | ✅ |
| `07-流式输出与交互式会话.md` | streaming vs single mode、`ClaudeSDKClient` 双向交互、sessions（resume / fork）、hooks 回调、FastAPI SSE 对接 | ✅ |
| `08-API参考.md` | `query()` / `ClaudeSDKClient` / `ClaudeAgentOptions` 全签名、公共导出清单、类型与默认值 | ✅ |
| `09-高级用法与二次开发最佳实践.md` | 过时信息防坑、hooks / permissions 实战、多租户 prompt-cache 与成本治理、与本项目的集成评估 | ✅ |
| `examples/` | 示例集（按 claude-agent-sdk 0.2.135 API 编写，须在独立 venv 运行） | ✅ |

> 全部章节已定稿；文中交叉链接均以磁盘实际文件名为准。

**示例 ↔ 章节对照表**：

| 示例文件 | 对应章节 | 演示要点 |
|---|---|---|
| `examples/01_quickstart.py` | `00-安装与快速入门.md` | `query()` + 显式 `model` 的最小闭环 |
| `examples/02_model_retry_fallback.py` | `02-LLM集成与成本控制.md` | 模型选择与重试/回退策略（env 注入重试参数） |
| `examples/03_mcp_tools.py` | `06-MCP集成.md` | 外部 MCP server 接入 + 进程内 SDK MCP server |
| `examples/04_subagents.py` | `04-Sub-Agent架构.md` | `AgentDefinition` 声明式子代理与委派 |
| `examples/05_skills_agent.py` + `examples/05_skills/demo-skill/SKILL.md` | `05-Skill系统.md` | skills 目录加载（`setting_sources` + `cwd` 发现路径） |
| `examples/06_streaming.py` | `07-流式输出与交互式会话.md` | streaming 模式下的增量消息消费 |
| `examples/07_interactive_client.py` | `07-流式输出与交互式会话.md` | `ClaudeSDKClient` 多轮交互（interrupt / hooks） |
| `examples/08_sessions_resume_fork.py` | `07-流式输出与交互式会话.md` | sessions 持久化、resume 与 fork |
| `examples/09_fastapi_sse_integration.py` | `07-流式输出与交互式会话.md`、`09-高级用法与二次开发最佳实践.md` | SDK 消息流 → FastAPI SSE 帧的集成骨架 |

---

## 2. 推荐阅读顺序

**快速上手路线**（约 30 分钟建立可用认知）：

```text
README.md（本文件）  →  00-安装与快速入门.md  →  01-架构总览与运行时数据流.md
  （先看导航与依赖速查）   （跑通最小 query()）      （理解子进程架构与双 API 选型）
```

**按角色的最小阅读集**：

| 角色 | 必读 | 选读 |
|---|---|---|
| 集成评估（技术决策） | 本 README（尤其第 4、6 节）、01 | 09 |
| 快速验证（跑 demo） | 00 | 08 |
| 二次开发（自定义工具 / hooks / transport） | 01、09 | 06、07 |
| 功能深挖（sub-agent / skills / MCP / sessions） | 04、05、06、07 | 03 |
| 新加入成员 | 本 README → 00 → 01 | 其余按需 |

---

## 3. 版本锚点

> **本套文档基于 `claude-agent-sdk 0.2.135` 源码与官方文档核实，调研日期 2026-08-11。**

| 项 | 值 |
|---|---|
| 包版本 | `0.2.135`（2026-08-10 发布，PyPI） |
| PyPI 包名 | `claude-agent-sdk` |
| 捆绑 CLI 要求 | Claude Code CLI `>=2.0.0`（出处：`src/claude_agent_sdk/_internal/transport/subprocess_cli.py::MINIMUM_CLAUDE_CODE_VERSION`） |
| 许可证 | MIT |
| 成熟度 | Alpha（`Development Status :: 3 - Alpha`） |
| Python 要求 | `>=3.10` |
| 运行时依赖 | `anyio>=4.0`、`mcp>=1.23,<2.0`、`sniffio` |
| 官方仓库 | <https://github.com/anthropics/claude-agent-sdk-python> |
| 官方文档 | <https://code.claude.com/docs/en/agent-sdk/overview> |

---

## 4. 更名与迁移速查

旧包 **`claude-code-sdk`** 已于终版 `0.0.25`（2025-09-29）停更；新包 `claude-agent-sdk` 自 `0.1.0` 起完成破坏性更名（出处：官方文档 <https://code.claude.com/docs/en/agent-sdk/python> 迁移节、PyPI 版本历史）。

| 变更项 | 旧（claude-code-sdk） | 新（claude-agent-sdk ≥0.1.0） |
|---|---|---|
| 选项类 | `ClaudeCodeOptions` | `ClaudeAgentOptions` |
| system prompt 字段 | `custom_system_prompt` / `append_system_prompt` 两个字段 | 合并为单一 `system_prompt`，支持三种形态：字符串 = 整体替换；`SystemPromptPreset`（`{"type": "preset", "preset": "claude_code", "append": ...}`）= Claude Code 预设提示 + 追加；`SystemPromptFile`（`{"type": "file", "path": ...}`）= 从文件加载（出处：`src/claude_agent_sdk/types.py::SystemPromptPreset / SystemPromptFile`） |
| 默认提示 | 完整 Claude Code 系统提示 | 默认改为 **minimal**（精简提示，更适合通用 agent 场景） |

> 迁移结论：从 `claude-code-sdk` 迁入时，除包名与选项类更名外，**必须复核 system prompt 语义**——默认提示从完整变为 minimal，若依赖原 Claude Code 全量提示行为，需显式选用 `claude_code` preset。

---

## 5. 实现状态图例（三档标注）

后续所有章节在描述某项能力时，统一使用以下三档标注：

| 标注 | 含义 | 判定口径 |
|---|---|---|
| ✅ **官方已实现** | claude-agent-sdk（`src/claude_agent_sdk/`）或其捆绑的 Claude Code CLI 直接提供 | 能在 0.2.135 源码或官方文档中找到对应实现/说明 |
| 🔶 **生态库组合实现** | 需借助 Claude Code CLI 配置生态（hooks 配置文件、plugins、MCP 生态）或第三方库组合达成 | SDK 未直接提供，但有明确的官方生态路径 |
| 🔧 **需自行实现** | 官方与生态均无现成方案，落地需自行开发 | 调研结论中给出缺口说明与建议实现方向 |

示例：「进程内自定义工具（`create_sdk_mcp_server`）——✅ 官方已实现」「跨进程会话持久化网关——🔧 需自行实现」。

> 📌 **本次调研结论说明**：六大主题章节中暂无能力落入 🔶 档——Claude Agent SDK 的各项能力要么为官方内置（✅），要么为明确缺口需自行实现（🔧）；🔶 档图例保留，供后续版本调研补充。

> 📌 **本次调研结论说明**：六大主题章节中暂无能力落入 🔶 档——Claude Agent SDK 的各项能力要么为官方内置（✅），要么为明确缺口需自行实现（🔧）；🔶 档图例保留，供后续版本调研补充。

---

## 6. 本地依赖速查

**本项目与 claude-agent-sdk 0.2.135 不存在版本冲突，但引入新依赖 `mcp`，且运行时要求独立 CLI 子进程与 API key**；为不触碰本项目主依赖，**所有示例必须在独立虚拟环境中验证**（操作步骤见 `00-安装与快速入门.md` 第 5 节），不修改 `pyproject.toml`。

| 项 | 本项目现状 | claude-agent-sdk 0.2.135 要求 | 判定 |
|---|---|---|---|
| Python | 3.13 | `>=3.10` | ✅ 满足 |
| `anyio` | 已在锁文件（传递依赖） | `>=4.0` | ✅ 兼容 |
| `sniffio` | 已在锁文件（传递依赖） | 无下限要求 | ✅ 兼容 |
| `mcp` | **未引入** | `>=1.23,<2.0` | ⚠️ 新增依赖（集成时须评估） |
| Claude Code CLI 子进程 | 无 | wheel 捆绑原生 CLI（`>=2.0.0`），运行时 spawn 子进程 | ⚠️ 新增运行时形态（进程管理、日志 stderr 化需评估） |
| API key | 经 `.env.*` 注入 | `ANTHROPIC_API_KEY` 环境变量（SDK **不自动读 `.env`**） | ⚠️ 需在宿主侧显式注入 |

> 结论：无版本冲突，集成门槛集中在「新增 `mcp` 依赖 + 子进程运行时形态」。是否纳入主依赖由 09 章集成评估给出建议；在此之前示例一律独立 venv 验证。

---

*下一步：打开 `00-安装与快速入门.md` 开始阅读。*
