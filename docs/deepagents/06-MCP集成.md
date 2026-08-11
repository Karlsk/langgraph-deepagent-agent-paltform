# 06 · MCP 集成

> 本章覆盖：deepagents 核心包与 MCP 的关系（**核心无内置 MCP 代码**）、官方集成路径（`langchain-mcp-adapters` 组合接入）、`MultiServerMCPClient` 最小示例、连接生命周期管理（stateless vs 持久 session）、部署侧 MCP server 注册、与本项目 FastAPI 宿主结合的连接管理建议。
>
> 版本基准：`deepagents 0.7.5`（调研日期 2026-08-11）。导航见 [README.md](./README.md)。
> 核心出处：`libs/deepagents/pyproject.toml`（依赖清单实测）、官方文档 <https://docs.langchain.com/oss/python/deepagents/mcp>（实测拉取）。

---

## 目录

1. [结论先行：核心无 MCP，走生态组合](#1-结论先行核心无-mcp走生态组合)
2. [官方集成路径：langchain-mcp-adapters](#2-官方集成路径langchain-mcp-adapters)
3. [MultiServerMCPClient 最小示例](#3-multiservermcpclient-最小示例)
4. [连接生命周期管理](#4-连接生命周期管理)
5. [错误处理与工具拦截器](#5-错误处理与工具拦截器)
6. [部署侧：MCP server 注册](#6-部署侧mcp-server-注册)
7. [与本项目 FastAPI 宿主结合的建议](#7-与本项目-fastapi-宿主结合的建议)
8. [小结](#8-小结)

---

## 1. 结论先行：核心无 MCP，走生态组合

> **⚠️ 章首声明**：deepagents 核心包**没有任何 MCP 代码**——`libs/deepagents/pyproject.toml` 的 `dependencies` 仅有 `langchain` / `langchain-core` / `langchain-anthropic` / `langchain-google-genai` / `langsmith` / `packaging` / `wcmatch`，optional extras 仅 `aws` / `quickjs` / `video`，**无 `langchain-mcp-adapters`、无 `mcp` SDK**（出处：`libs/deepagents/pyproject.toml` 实测）。
>
> 官方集成路径为 **🔶 生态库组合实现**：安装 `langchain-mcp-adapters`，用 `MultiServerMCPClient({...}).get_tools()` 取得 LangChain 工具，再经 `create_deep_agent(tools=...)` 注入。deepagents 侧无需任何专门适配——MCP 工具就是普通的 LangChain tools。

这条路径之所以零适配，源于 [01 章](./01-架构总览与运行时数据流.md) 的三层架构：`create_deep_agent` 把 `tools` 原样交给 `langchain.agents.create_agent`，工具层的来源（本地函数、LangChain tool、MCP 适配器产物）对 harness 完全透明。

| 环节 | 状态 | 出处 |
|---|---|---|
| deepagents 内置 MCP client/server | ❌ 无（核心与 extras 依赖中均无 MCP 相关包） | `libs/deepagents/pyproject.toml` |
| MCP 工具接入 agent | 🔶 生态库组合实现（`langchain-mcp-adapters`） | 官方文档 deepagents/mcp 页 |
| MCP 工具的重试/回退 | 🔧 需自行实现（deepagents 核心无内置重试/回退；适配层可用 interceptors 自写，见第 5 节） | — |

---

## 2. 官方集成路径：langchain-mcp-adapters

官方文档（<https://docs.langchain.com/oss/python/deepagents/mcp>）给出的接入三步：

```bash
uv add langchain-mcp-adapters   # 或 pip install langchain-mcp-adapters
```

```python
from langchain_mcp_adapters.client import MultiServerMCPClient
from deepagents import create_deep_agent

client = MultiServerMCPClient({...})       # 声明一组 MCP server
tools = await client.get_tools()           # 拉取全部 server 的工具定义
agent = create_deep_agent(
    model="anthropic:claude-sonnet-4-6",
    tools=tools,                           # 与内置文件工具/task 工具叠加，不会顶掉内置工具
)
```

两点补充（出处：`graph.py::create_deep_agent` 的 `tools` docstring + 官方 MCP 文档）：

- `tools` 参数是**叠加式**的：MCP 工具与内置文件工具、`execute`、`task` 共存；要裁剪内置工具得走 `HarnessProfile.excluded_tools`。
- MCP 的 tools / resources / prompts 三类能力中，适配库只把 **tools** 转成 agent 可用工具；resources 转为 `Blob`（`client.get_resources()`）、prompts 转为 messages（`client.get_prompt()`），需要调用方自行接入对话（出处：官方 MCP 文档「Resources」「Prompts」节）。

---

## 3. MultiServerMCPClient 最小示例

构造参数是一个 `{server_name: connection_config}` 字典，transport 各一个最小示例（出处：官方 MCP 文档「Quickstart」「Transports」节，片段做了面向 deepagents 的改写）：

```python
# streamable HTTP transport（远程/托管 MCP server）
client = MultiServerMCPClient(
    {
        "weather": {
            "transport": "http",                 # 即 streamable-http
            "url": "http://localhost:8000/mcp",  # 或托管地址，如 https://docs.langchain.com/mcp
            "headers": {"Authorization": "Bearer <token>"},  # 可选：鉴权/追踪头
        },
        # stdio transport（本地子进程 MCP server）
        "math": {
            "transport": "stdio",
            "command": "python",
            "args": ["/abs/path/to/math_server.py"],
        },
    }
)
tools = await client.get_tools()
```

transport 要点（出处：官方 MCP 文档）：

| transport | 通信方式 | 备注 |
|---|---|---|
| `http`（streamable-http） | HTTP 请求 | 支持 `headers` 传鉴权；`sse` transport 已被 MCP 规范标记弃用；自定义鉴权可实现 MCP SDK 的 `httpx.Auth` 接口传 `auth=` |
| `stdio` | 客户端拉起 server 子进程，经 stdin/stdout 通信 | 适合本地工具；连接本身天然带状态（子进程随 client 存活），但默认每次工具调用仍新建 session（见下节） |

---

## 4. 连接生命周期管理

这是生产接入的核心取舍。`MultiServerMCPClient` **默认是无状态的**：每次工具调用都新建一个 MCP `ClientSession`，执行完即清理（出处：官方 MCP 文档「Stateful sessions」节原话「stateless by default」）。

| 模式 | 写法 | 语义 | 适用 |
|---|---|---|---|
| **无状态（默认）** | `tools = await client.get_tools()`，工具随 agent 运行按需建连 | 每次调用独立 session，无跨调用上下文；无需管理连接生命周期 | 无状态工具（查询、检索类）；server 不维护会话上下文 |
| **持久 session** | `async with client.session("server_name") as session:`，再用 `load_mcp_tools(session)` 在作用域内加载工具 | session 在 `async with` 作用域内存活，server 可跨调用维护上下文（MCP lifecycle 语义） | 有状态 server（登录态、游标、工作目录等跨调用上下文） |

```python
# 持久 session 模式（出处：官方 MCP 文档「Stateful sessions」节）
from langchain_mcp_adapters.tools import load_mcp_tools

client = MultiServerMCPClient({...})
async with client.session("server_name") as session:
    tools = await load_mcp_tools(session)
    agent = create_deep_agent(model="anthropic:claude-sonnet-4-6", tools=tools)
    result = await agent.ainvoke({...})   # 必须发生在作用域内
```

注意事项：

- **持久 session 的工具必须在作用域内使用**——`async with` 退出后 session 关闭，绑定该 session 的工具再调用会失败。对长驻 Web 服务而言，这意味着 session 生命周期要么绑定到单次请求、要么绑定到应用进程（见第 7 节）。
- stdio transport 即使用无状态模式，子进程也会随 client 对象存活；client 被回收后子进程随之退出——**不要在高并发路径里反复构造/丢弃 client**。
- `stdio` 连接「inherently stateful」，但官方文档明确：不显式管理 session 时每次工具调用仍新建 session（出处：官方 MCP 文档「stdio」节的 Note）。

---

## 5. 错误处理与工具拦截器

一个必须记住的事实：**deepagents 核心没有内置的工具级重试/回退机制**（🔧 需自行实现）。MCP 场景下有两个可用抓手（均出自适配库，出处：官方 MCP 文档「Loading tools」「Tool interceptors」节）：

- **错误回传而非抛异常**：默认情况下 MCP 工具执行失败（`CallToolResult(isError=True)`）会作为 `status="error"` 的 tool message 回传给模型，让 agent 自行读错误并重试；传 `handle_tool_errors=False` 才改为抛异常。要求 `langchain-mcp-adapters>=0.3.0`。注意这只覆盖工具执行错误；transport / session / 内容转换失败**总是抛异常**。
- **tool interceptors**：`MultiServerMCPClient({...}, tool_interceptors=[...])` 接受一组 async 拦截器（洋葱模型，列表首个为最外层），可修改请求（`request.override(args=...)`）、实现重试（官方文档给出指数退避示例）、返回 fallback、注入运行时上下文（`request.runtime` 可拿到 state / store / context / tool_call_id），甚至返回 `Command` 控制图流转。

对本项目的含义：AGENTS.md 要求「所有重试必须用 tenacity」——在 MCP 场景应把 tenacity 包在 interceptor 或自建的工具包装层里，而不是期待 deepagents 提供重试。

---

## 6. 部署侧：MCP server 注册

若走 LangSmith / LangGraph Platform 部署路线，官方部署工具链提供工作区级的 MCP server 注册命令（形如 `deepagents mcp-servers add --url ... --name ...`，注册信息落在工作区级 `tools.json`）。**本次源码调研未逐条核实该命令的参数细节与文件结构，实际使用以官方部署文档为准**（<https://docs.langchain.com/oss/python/deepagents> 部署相关章节）。

需要明确的是：该注册属于 **LangSmith 部署侧能力，不在 `libs/deepagents` 核心包内**——自建部署（如本项目直接以 FastAPI 进程承载 agent）不会用到它，应走第 7 节的自管 client 路线。

---

## 7. 与本项目 FastAPI 宿主结合的建议

结合本项目现有形态（`app/api/v1/chatbot.py` 中模块级单例 `agent = LangGraphAgent()`，见 [07 章](./07-流式输出.md) 的 SSE 对接小节），推荐的 MCP 接入模式：

1. **应用启动期建 client、拉工具、构建单例 agent**（lifespan 或启动钩子内）：

   ```python
   # 伪代码：FastAPI lifespan 中完成一次性装配
   client = MultiServerMCPClient(MCP_SERVER_CONFIG)
   mcp_tools = await client.get_tools()
   agent = create_deep_agent(model=..., tools=[*local_tools, *mcp_tools])
   ```

   理由：工具定义在启动期冻结进单例 agent，避免每请求重复 `get_tools()` 的握手开销；client 与进程同生命周期，stdio 子进程不反复拉起。

2. **默认走无状态模式**；仅当某个 server 明确需要跨调用上下文时，才为该 server 单独维护持久 session，并把 session 生命周期绑定到应用进程（lifespan 内 `async with` 的等价物——需在 shutdown 钩子中显式关闭）。

3. **transport 选型**：容器化部署优先 `http`（server 独立伸缩、无子进程管理负担）；`stdio` 仅用于与 API 进程同机、可信的本地工具。

4. **错误与重试**：工具执行错误交给默认的「错误回传模型」语义；transport 级故障按本项目规范用 tenacity 包裹（interceptor 或外层重试），并按 AGENTS.md 记 structlog 结构化日志。

5. **可观测**：MCP 工具调用经 LangChain 工具抽象走，Langfuse tracing 天然覆盖（与本项目「所有 LLM 操作须有 Langfuse tracing」的要求一致，无需额外适配）。

---

## 8. 小结

| 要点 | 结论 |
|---|---|
| deepagents 核心 MCP 能力 | ❌ 无（pyproject 依赖实测，无任何 MCP 包） |
| 官方路径 | 🔶 `langchain-mcp-adapters`：`MultiServerMCPClient({...}).get_tools()` → `create_deep_agent(tools=...)` |
| 适配成本 | 零 deepagents 侧改动——MCP 工具即普通 LangChain tools |
| 连接管理 | 默认 stateless（每次调用新 session）；需跨调用上下文时用 `client.session(...)` 作用域 + `load_mcp_tools(session)` |
| 重试/回退 | 🔧 核心无内置；用 interceptors + tenacity 自建 |
| 部署侧注册 | `deepagents mcp-servers add`（LangSmith 部署侧，细节以官方部署文档为准） |
| 本项目建议 | FastAPI lifespan 期建 client → 工具注入单例 agent；默认 stateless，http transport 优先 |

---

*上一章：[05-Skill系统.md](./05-Skill系统.md) · 返回导航：[README.md](./README.md) · 下一章：[07-流式输出.md](./07-流式输出.md)*
