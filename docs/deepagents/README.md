# deepagents SDK 调研文档 · 导航（README）

> **一句话定位**：本套文档是对 LangChain 官方 [deepagents](https://github.com/langchain-ai/deepagents) SDK（生产级、可扩展的 Agent harness：内置文件系统与上下文管理、子代理委派、skills、长期记忆）的源码级调研，目标是评估其在本项目（pml-langgraph-agent）中的集成与借鉴价值。

- 文档全部用**中文**书写；代码标识符、文件路径、配置键保留**英文**。
- 本 README 只做**导航与速查**；细节一律以对应章节文档为准。
- 所有关键结论均以 `deepagents 0.7.5`（monorepo `libs/deepagents/`）源码为基准，逐条标注出处文件。

---

## 目录

1. [文档清单](#1-文档清单)
2. [推荐阅读顺序](#2-推荐阅读顺序)
3. [版本锚点](#3-版本锚点)
4. [实现状态图例（三档标注）](#4-实现状态图例三档标注)
5. [本地依赖冲突速查](#5-本地依赖冲突速查)

---

## 1. 文档清单

| 文件 | 内容（一句话） | 状态 |
|---|---|---|
| `README.md`（本文件） | 文档导航、阅读顺序、版本锚点、实现状态图例、本地依赖冲突速查 | ✅ |
| `00-安装与快速入门.md` | 安装方式（uv / pip）、extras、环境变量、10 行最小 quickstart、与本项目依赖冲突的验证方案 | ✅ |
| `01-架构总览与运行时数据流.md` | 三层架构（Deep Agents → LangChain create_agent → LangGraph）、`create_deep_agent` 组装流程、默认中间件栈、运行时数据流、backends 协议族、profiles 机制 | ✅ |
| `02-LLM集成.md` | 模型解析（`resolve_model`、`provider:model` 字符串）、provider profiles、prompt caching、默认模型弃用史 | ✅ |
| `03-System-Prompt系统.md` | prompt 组装（`USER` → `BASE` → `SUFFIX`）、profile prompt 叠加、`SystemMessage` 与 `cache_control` 语义 | ✅ |
| `04-Sub-Agent架构.md` | `SubAgent` / `CompiledSubAgent` / `AsyncSubAgent` 三种形态、`task` 工具、general-purpose 默认子代理、权限与 interrupt 继承 | ✅ |
| `05-Skill系统.md` | `SkillsMiddleware`、skill 源路径加载规则（后写覆盖）、与 backend 的关系 | ✅ |
| `06-MCP集成.md` | deepagents 与 MCP 的集成路径、生态现状与缺口 | ✅ |
| `07-流式输出.md` | 基于 LangGraph 运行时的 streaming 模式与 `DeltaChannel` 增量 checkpoint 对流的收益 | ✅ |
| `08-API参考.md` | `create_deep_agent` 全参数表、公共导出清单（`deepagents/__init__.py`）、类型与默认值 | ✅ |
| `09-高级用法与二次开发最佳实践.md` | 自定义 `BackendProtocol`、自定义中间件、`HarnessProfile` / `ProviderProfile` 注册、`state_schema` 扩展、与本项目的集成建议 | ✅ |
| `examples/` | 示例集（按 deepagents 0.7.5 API 编写，未在本仓运行；头部 docstring 标注依赖与环境变量） | ✅ |

> 各章节均已定稿；`examples/` 内示例为结构级参考，运行需在独立虚拟环境中安装对应依赖（见 `00-安装与快速入门.md`）。

**示例 ↔ 章节对照表**：

| 示例文件 | 对应章节 | 演示要点 |
|---|---|---|
| `examples/01_quickstart.py` | `00-安装与快速入门.md` | `create_deep_agent` + `ainvoke` 最小闭环 |
| `examples/02_mcp_tools.py` | `06-MCP集成.md` | `MultiServerMCPClient` stateless 拉取 MCP 工具并注入 agent |
| `examples/03_subagents.py` | `04-Sub-Agent架构.md` | 声明式 `SubAgent`：`task` 委派 + `response_format` 结构化回传 |
| `examples/04_skills_agent.py` | `05-Skill系统.md` | FilesystemBackend 读盘加载 skills（docstring 附 StateBackend 播种写法） |
| `examples/05_streaming.py` | `07-流式输出.md` | `astream_events(version="v3")` 类型化投影 + `asyncio.gather` 并发消费 |
| `examples/06_retry_fallback_model.py` | `02-LLM集成.md` 第 4 节 | `init_chat_model` + `with_retry` + `with_fallbacks` 直传实例 |
| `examples/07_fastapi_sse_integration.py` | `07-流式输出.md` 第 6 节、`09-高级用法与二次开发最佳实践.md` | v3 流式投影 → FastAPI SSE 帧的集成骨架 |
| `examples/08_system_prompt_cache_control.py` | `03-System-Prompt系统.md` 第 3 节 | `SystemMessage` + `cache_control` ephemeral 断点（Anthropic prompt cache） |

---

## 2. 推荐阅读顺序

**快速上手路线**（约 30 分钟建立可用认知）：

```text
README.md（本文件）  →  00-安装与快速入门.md  →  01-架构总览与运行时数据流.md
  （先看导航与依赖冲突）    （跑通最小示例）           （理解三层架构与中间件栈）
```

**按角色的最小阅读集**：

| 角色 | 必读 | 选读 |
|---|---|---|
| 集成评估（技术决策） | 本 README（尤其第 5 节依赖冲突）、01 | 09 |
| 快速验证（跑 demo） | 00 | 08 |
| 二次开发（自定义 backend / 中间件 / profile） | 01、09 | 02、03、04 |
| 功能深挖（sub-agent / skills / memory） | 04、05、03 | 06、07 |
| 新加入成员 | 本 README → 00 → 01 | 其余按需 |

---

## 3. 版本锚点

> **本套文档基于 `deepagents 0.7.5` 源码核实，调研日期 2026-08-11。**

| 项 | 值 |
|---|---|
| 包版本 | `0.7.5`（2026-08-06 发布，与 GitHub `main` 分支一致） |
| PyPI 包名 | `deepagents` |
| monorepo 位置 | `libs/deepagents/`（另有 `libs/cli`、`libs/code`、`libs/acp`、`libs/evals`、`libs/partners`） |
| 许可证 | MIT |
| 成熟度 | Beta（`Development Status :: 4 - Beta`） |
| Python 要求 | `>=3.11,<4.0` |
| 官方仓库 | <https://github.com/langchain-ai/deepagents> |
| 官方文档 | <https://docs.langchain.com/oss/python/deepagents> |
| 架构依据 | `libs/ARCHITECTURE.md`（三层架构的官方口径） |

---

## 4. 实现状态图例（三档标注）

后续所有章节在描述某项能力时，统一使用以下三档标注：

| 标注 | 含义 | 判定口径 |
|---|---|---|
| ✅ **官方已实现** | deepagents SDK（`libs/deepagents/`）源码中直接提供 | 能在 0.7.5 源码中找到对应实现与公共导出 |
| 🔶 **生态库组合实现** | 需借助 LangChain / LangGraph / 其他 `libs/*` 或第三方生态库组合达成 | deepagents 未直接提供，但有明确的官方生态路径 |
| 🔧 **需自行实现** | 官方与生态均无现成方案，落地需自行开发 | 调研结论中给出缺口说明与建议实现方向 |

示例：「文件系统权限（`FilesystemPermission`）——✅ 官方已实现」「ACP 协议接入——🔶 生态库组合实现（`libs/acp`）」。

---

## 5. 本地依赖冲突速查

**本项目 `pyproject.toml` 与 deepagents 0.7.5 的依赖要求存在版本抬升冲突**，直接 `uv add deepagents` 会导致解析失败或被迫升级核心依赖，影响现有 `app/` 代码。**所有示例必须在独立虚拟环境中验证**（操作步骤见 `00-安装与快速入门.md` 第 4 节）。

| 依赖 | 本项目当前要求 | deepagents 0.7.5 要求 | 冲突判定 |
|---|---|---|---|
| `langchain` | `>=1.0.5` | `>=1.3.14,<2.0.0` | ⚠️ 需抬升 minor 版本 |
| `langchain-anthropic` | `>=1.0,<1.1` | `>=1.5.4,<2.0.0` | ⚠️ 冲突（上限 `<1.1` 与下限 `>=1.5.4` 无交集） |
| `langchain-core` | 随 `langchain` 传递 | `>=1.5.0,<2.0.0` | ⚠️ 需抬升 |
| Python | 3.13（本项目运行环境） | `>=3.11` | ✅ 满足 |

> 结论：冲突集中在 LangChain 系依赖的版本区间，Python 版本不构成障碍。集成评估时须将「LangChain 全家桶抬升到 1.3.x / 1.5.x 的回归风险」纳入决策（详见 09 章）。

---

*下一步：打开 `00-安装与快速入门.md` 开始阅读。*
