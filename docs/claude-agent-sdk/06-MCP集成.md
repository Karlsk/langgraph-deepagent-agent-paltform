# 06 · MCP 集成

> 本章覆盖：MCP 三种接入形态（进程内 SDK MCP server、外部 stdio / SSE / HTTP server、配置文件）、工具命名与放行规则、tool search、连接时序、输出上限与运行中管控。
>
> 版本基准：`claude-agent-sdk 0.2.135`（调研日期 2026-08-11）。导航见 [README.md](./README.md)。
>
> **实现状态声明**：本章涉及的三种 MCP 形态均为 ✅ **官方已实现**——SDK 原生提供全部能力，无需生态库组合（三档图例见 [README.md 第 5 节](./README.md#5-实现状态图例三档标注)）。

---

## 目录

1. [三种形态总览](#1-三种形态总览)
2. [进程内 SDK MCP server（自定义工具）](#2-进程内-sdk-mcp-server自定义工具)
3. [外部 MCP server（stdio / SSE / HTTP）](#3-外部-mcp-serverstdio--sse--http)
4. [Plugins 选项（本地 plugin 加载）](#4-plugins-选项本地-plugin-加载)
5. [工具命名、放行与 tool search](#5-工具命名放行与-tool-search)
6. [连接时序（官方口径）](#6-连接时序官方口径)
7. [工具输出上限](#7-工具输出上限)
8. [运行中管控：状态查询 / 重连 / 启停](#8-运行中管控状态查询--重连--启停)
9. [后续章节导航](#9-后续章节导航)

---

## 1. 三种形态总览

出处：官方文档 <https://code.claude.com/docs/en/agent-sdk/mcp>、<https://code.claude.com/docs/en/agent-sdk/custom-tools>、`src/claude_agent_sdk/types.py::McpServerConfig`。

| 形态 | 配置入口 | 运行位置 | 标注 |
|---|---|---|---|
| 进程内 SDK MCP server | `create_sdk_mcp_server()` + `@tool` | Python 宿主进程内 | ✅ 官方已实现 |
| 外部 server（stdio / SSE / HTTP） | `ClaudeAgentOptions.mcp_servers` dict | 独立子进程 / 远程服务 | ✅ 官方已实现 |
| JSON 配置文件（如 `.mcp.json`） | `mcp_servers` 直接传路径 | 同外部 server | ✅ 官方已实现 |

`mcp_servers` 字段类型为 `dict[str, McpServerConfig] | str | Path`：既接受程序化 dict，也接受配置文件路径（出处：`src/claude_agent_sdk/types.py::ClaudeAgentOptions`）。

---

## 2. 进程内 SDK MCP server（自定义工具）

出处：`src/claude_agent_sdk/__init__.py::tool / create_sdk_mcp_server`、官方文档 <https://code.claude.com/docs/en/agent-sdk/custom-tools>。

### 2.1 `@tool` 装饰器

```python
@tool(name, description, input_schema, annotations=None)
async def handler(args: dict) -> dict: ...
```

- `input_schema` 三种形态：`{参数名: 类型}` 简写 dict、`TypedDict` 类、完整 JSON Schema dict；`Annotated[type, "描述"]` 可为参数附加描述（出处：`src/claude_agent_sdk/__init__.py::tool` docstring）。
- **handler 契约**：`async def handler(args: dict) -> dict`——单参 dict 入参，返回 `{"content": [{"type": "text", "text": ...}, ...], "is_error": bool}`；注意 **Python 侧没有 ToolContext 参数**（与某些 MCP 生态不同）。

### 2.2 `create_sdk_mcp_server()`

```python
server = create_sdk_mcp_server(name="calculator", version="1.0.0", tools=[add, multiply])
# 返回 McpSdkServerConfig（type="sdk"），直接放入 mcp_servers
options = ClaudeAgentOptions(
    mcp_servers={"calc": server},
    allowed_tools=["mcp__calc__add", "mcp__calc__multiply"],
)
```

官方给出的进程内形态优势（出处：`create_sdk_mcp_server` docstring）：**无子进程管理、无 IPC 开销、单进程部署、可直接访问宿主应用状态、永不延迟首轮**（连接时序见第 6 节）。

### 2.3 可用性事实与真实约束（0.2.135 核实）

> ✅ **进程内自定义工具（SDK MCP server）与 hooks 回调在 `query()` 中同样可用**：0.2.135 中 `query()` 与 `ClaudeSDKClient` 共用 `_internal/client.py::_process_query_inner`，内部恒以 streaming mode 构造 Query（源码注释「Always use streaming mode internally」），`subprocess_cli.py` 一律 `--input-format stream-json`，控制通道恒建立；hooks / `sdk_mcp_servers` / `agents` / `skills` 均照常传入并注册。官方 custom-tools 页亦给出「pass to mcpServers in query()」示例（出处：`src/claude_agent_sdk/_internal/client.py`、官方文档 <https://code.claude.com/docs/en/agent-sdk/custom-tools>）。
>
> ⚠️ 唯一真实约束：`can_use_tool` 搭配**字符串 prompt** 会 raise（「can_use_tool callback requires streaming mode」），须改用 `AsyncIterable` prompt（出处：`_internal/client.py::_process_query_inner`）。
>
> `ClaudeSDKClient` 的选型价值在多轮对话、`interrupt()` / `set_model()` / `set_permission_mode()` 等交互能力，而非控制通道有无（见 [01 章第 2 节](./01-架构总览与运行时数据流.md#2-双-apiquery-vs-claudesdkclient-选型)）。

完整示例见 [`examples/03_mcp_tools.py`](./examples/03_mcp_tools.py)。

---

## 3. 外部 MCP server（stdio / SSE / HTTP）

出处：官方文档 <https://code.claude.com/docs/en/agent-sdk/mcp>、`src/claude_agent_sdk/types.py::McpStdioServerConfig / McpSSEServerConfig / McpHTTPServerConfig`。

| 类型 | 必填字段 | 可选字段 | 说明 |
|---|---|---|---|
| `"stdio"` | `command` | `args`、`env` | 本地子进程；`env` 传子进程环境变量 |
| `"sse"` | `url` | `headers` | Server-Sent Events 远程 |
| `"http"` | `url` | `headers` | Streamable HTTP 远程；认证头直接放 `headers` |

```python
options = ClaudeAgentOptions(
    mcp_servers={
        # stdio：本地子进程
        "filesystem": {
            "command": "npx",
            "args": ["-y", "@modelcontextprotocol/server-filesystem", "/path/to/dir"],
            "env": {"API_KEY": os.environ["API_KEY"]},  # 可选
        },
        # http：streamable HTTP 远程
        "secure-api": {
            "type": "http",
            "url": "https://api.example.com/mcp",
            "headers": {"Authorization": f"Bearer {os.environ['API_TOKEN']}"},
        },
    },
    allowed_tools=["mcp__filesystem__*", "mcp__secure-api__*"],
)
```

要点：

- **`"streamable-http"` 别名**：仅 `.mcp.json` 等 JSON 配置文件接受；程序化 `mcp_servers` 只接受 `"http"`（出处：官方 mcp 页）。
- **配置文件直传**：`mcp_servers="/path/to/.mcp.json"`；配置文件中支持 `"${ENV_VAR}"` 运行时展开（出处：官方 mcp 页）。
- **`strict_mcp_config=True`**：只用显式传入的 server，忽略项目 `.mcp.json`、用户设置、plugin 提供的 server 与 claude.ai connectors（出处：`src/claude_agent_sdk/types.py::ClaudeAgentOptions.strict_mcp_config`）。
- **OAuth**：SDK 不跑交互式 OAuth；server 返回授权挑战且无缓存 token 时，会话继续但不加载该 server 工具，状态报 `needs-auth`，token 可由宿主完成授权后经 `headers` 注入（出处：官方 mcp 页 Authentication 节）。

---

## 4. Plugins 选项（本地 plugin 加载）

出处：`src/claude_agent_sdk/types.py::ClaudeAgentOptions.plugins / SdkPluginConfig`（字段与类 docstring）、官方 Python 参考页 <https://code.claude.com/docs/en/agent-sdk/python>（`plugins` 行）。

- **字段形态**：`plugins: list[SdkPluginConfig]`，默认 `[]`；`SdkPluginConfig` 为 TypedDict `{"type": "local", "path": "<本地路径>"}`——**当前仅支持 local 类型**（从本地路径加载）。
- **plugin 提供什么**：plugin 可为会话提供 custom commands、agents、skills、hooks（亦可携带 MCP server）；其中 skills 在 `skills` 名单中以 `plugin:skill` 限定名引用（见 [05 章第 2 节](./05-Skill系统.md#2-skillmd-结构与发现路径)）。
- **与 `strict_mcp_config` 的关系**：`strict_mcp_config=True` 时 plugin 提供的 MCP server 一并被排除（见第 3 节要点与 [08 章 §3.3](./08-API参考.md#33-mcp-与插件)）。

```python
options = ClaudeAgentOptions(
    plugins=[{"type": "local", "path": "/path/to/my-plugin"}],
    skills=["my-plugin:pdf-processing"],   # plugin 提供的 skill 用限定名
)
```

> 定位补充：对 [05 章第 1 节](./05-Skill系统.md#1-仅文件系统形态无程序化注册-api)的 🔧 缺口（skills 无程序化注册 API）而言，plugins 是除文件系统发现路径外的另一条多租户/动态 skill 落地路径；细节以官方 plugins 文档为准，落地前复核。

---

## 5. 工具命名、放行与 tool search

出处：官方文档 <https://code.claude.com/docs/en/agent-sdk/mcp>（Allow MCP tools / MCP tool search 节）。

- **命名约定**：`mcp__<server名>__<tool名>`，如 `mcp__github__list_issues`。
- **必须显式放行**：MCP 工具默认需权限确认；在 `allowed_tools` 中列出才会被自动批准，支持 `mcp__<server>__*` 通配整个 server。未放行时 Claude 能看到工具存在但无法调用。
- **发现工具清单**：检查 `system` init 消息的 `tools` 数组（以 `mcp__` 开头者即 MCP 工具）。
- **tool search 默认开启**：MCP 工具多时，工具定义不再全量占用上下文，而是按需加载每轮所需；细节见官方 <https://code.claude.com/docs/en/agent-sdk/tool-search>。

---

## 6. 连接时序（官方口径）

出处：官方文档 <https://code.claude.com/docs/en/agent-sdk/mcp>（Connection timing 节）。

| server 类型 | 是否延迟首轮 | 首轮等待超时 |
|---|---|---|
| stdio server，或**无缓存工具列表**的 HTTP/SSE server | ✅ 阻塞首轮直到连上 | `MCP_TIMEOUT`（默认 30 秒），到期即连接失败 |
| **有缓存工具列表**的远程 server（Claude Code 上次连接所存） | ❌ 不延迟；缓存工具首轮即可用 | 无；首次调用工具时才懒连接（该次连接另有超时） |
| 进程内 SDK MCP server | ❌ 永不延迟首轮 | 无 |

补充：

- `system` init 消息（`subtype="init"`）在首轮等待结束后发出，其中 `mcp_servers` 数组报告每个 server 的即时状态；判断不可用应查 `failed` / `needs-auth`，而非把所有非 `connected` 都当失败（出处：官方 mcp 页）。
- 需要更早阻塞启动阶段时可用 `MCP_CONNECTION_NONBLOCKING=0`（默认上限 5 秒，可用 `MCP_CONNECT_TIMEOUT_MS` 调整）或在 server 配置上设 `alwaysLoad: true`（出处：官方 mcp 页）。

---

## 7. 工具输出上限

出处：官方文档 <https://code.claude.com/docs/en/agent-sdk/mcp>（Error handling 节）。

- 单个 MCP 工具结果上限 **25,000 token**；超限时完整输出**落盘存文件**，工具结果被替换为指向该文件路径的错误提示，agent 可分段读回。
- 用环境变量 `MAX_MCP_OUTPUT_TOKENS` 提高上限；server 亦可通过工具注解声明更高的单工具上限（`anthropic/maxResultSizeChars`，出处：`src/claude_agent_sdk/__init__.py::_build_meta` 与官方 mcp 输出限制页）。

---

## 8. 运行中管控：状态查询 / 重连 / 启停

出处：`src/claude_agent_sdk/client.py`（`get_mcp_status` / `reconnect_mcp_server` / `toggle_mcp_server`）、`src/claude_agent_sdk/types.py::McpServerConnectionStatus`、官方文档 <https://code.claude.com/docs/en/agent-sdk/python>（Methods 节）。

| 方法（`ClaudeSDKClient`） | 作用 |
|---|---|
| `get_mcp_status()` | 返回全部 server 的当前连接状态（`McpStatusResponse`） |
| `reconnect_mcp_server(server_name)` | 对 failed / 断连的 server 重试连接 |
| `toggle_mcp_server(server_name, enabled)` | 会话中途启用/停用 server；停用即移除其工具 |

状态枚举 `McpServerConnectionStatus`：`connected` / `failed` / `needs-auth` / `pending` / `disabled`（出处：`src/claude_agent_sdk/types.py`）。

> 三者为 streaming mode 交互方法，依赖常驻连接，故属于 `ClaudeSDKClient` 的方法面；`query()` 场景只能在 options 阶段静态配置。

---

## 9. 后续章节导航

| 下一步 | 文档 |
|---|---|
| streaming / 交互客户端 / sessions / FastAPI SSE 对接 | [07-流式输出与交互式会话.md](./07-流式输出与交互式会话.md) |
| 全部 API 速查（Options 全字段、方法表、hooks、权限） | [08-API参考.md](./08-API参考.md) |
| skills 加载规则与 `Skill(...)` 工具规则 | [05-Skill系统.md](./05-Skill系统.md) |
| 返回导航 | [README.md](./README.md) |
