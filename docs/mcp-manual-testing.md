# MCP 功能手动测试指南（改造后）

本文档指导你手动验证 MCP 功能改造后的全链路：**统一 core 层客户端 + 长连 session 池**、
**sse / streamable-http transport**、**`{server}__{tool}` 工具命名空间**、**工具调试端点**、
**stdio manifest 自动发现**。

> **核实基线**：本文所有端点、脚本与行为均对照以下源码**并实际运行验证过**（2026-08）：
> `app/core/mcp_client.py`、`app/services/agents/mcp_manager.py`、
> `app/services/agents/mcp_stdio_registry.py`、`app/api/v1/mcp_servers.py`、
> `app/schemas/agent_apps.py`、`app/core/config.py`、`docker-compose.yml`、
> `mcp-servers/README.md`。若代码变更，请以源码为准。
> 全功能（Skill/SubAgent/AgentApp/Chat）测试见 [agentapp-manual-testing.md](agentapp-manual-testing.md)。

## 全文约定

* 所有 API URL 使用 `http://localhost:8000/api/v1/...`（本地直跑示例用 `http://127.0.0.1:8010`，以实际启动方式为准）。

* curl 示例统一使用 `$BASE` 与 `$TOKEN` 两个 shell 变量（token 获取方式见
  [agentapp-manual-testing.md](agentapp-manual-testing.md) 第 1–2 节：`POST /auth/register` →
  `POST /auth/session`，`/mcp-servers/*` 全部端点必须使用**会话 token**）：

```bash
export BASE=http://localhost:8000/api/v1
export TOKEN=<chat-session-token>
```

* **统一响应信封**：`{code, message, data}`，`code` 与 HTTP status 一致。创建类成功为
  HTTP 201；请求体校验失败为 `{code:422, message:"Validation error", data:[错误列表]}`；
  业务规则拒绝 / 客户端可判定错误为 `{code:422, message:"<文案>", data:null}`。

* **MCP 工具命名空间**：catalog、AgentApp/SubAgent 的 `allowed_tools` 与 LLM 可见工具名
  统一为 `{server_name}__{tool_name}`（双下划线分隔）；builtin 工具保持裸名。
  因此 **server `name` 禁止包含 `__`**（422）。

* 限流（可同名环境变量覆盖）：`mcp_server` 类端点 60/min；调试端点
  `tools` / `call-tool` 30/min（`RATE_LIMIT_MCP_TOOLS_DEBUG`）。

* 调试端点超时：list 30s、call 60s（服务端常量，超时映射 504）。

***

## 0. 前置条件

| 依赖             | 说明                                                                                               |
| -------------- | ------------------------------------------------------------------------------------------------ |
| 服务已启动          | docker compose（`make docker-up`）或本地直跑（`POSTGRES_HOST=127.0.0.1 POSTGRES_PORT=15432 ... uvicorn`） |
| `mcp` Python 库 | 演示 server 脚本依赖（项目 venv 自带：`.venv/bin/python`）                                                    |
| 会话 token       | 见上文约定                                                                                            |

**本次改造相关的环境变量**（`.env.example` 均有注释，一般无需修改）：

| 变量                           | 默认值                   | 说明                                                              |
| ---------------------------- | --------------------- | --------------------------------------------------------------- |
| `MCP_STDIO_ALLOWED_COMMANDS` | `python,node,uvx,npx` | stdio 命令 basename 白名单；shell 解释器无条件禁止                            |
| `MCP_SESSION_IDLE_TTL`       | `240`                 | 池化 session 空闲回收秒数（刻意小于 SSE 300s read timeout）                   |
| `MCP_SESSION_STOP_TIMEOUT`   | `10`                  | 关闭/关机时等待 session worker 优雅退出的宽限秒数，超时兜底 cancel（亲和性安全）          |
| `MCP_STDIO_ROOT`             | `./mcp-servers`       | stdio manifest 目录（compose 已挂载 `./mcp-servers:/app/mcp-servers`） |
| `RATE_LIMIT_MCP_TOOLS_DEBUG` | `30 per minute`       | 调试端点限流                                                          |

***

## 1. 准备演示 MCP server（可选，零外部依赖）

三个最小 server 脚本（项目 venv 自带 `mcp` 库，无需额外安装）。**本地直跑**时保存到任意路径；
**Docker 部署**时注意脚本须在容器内可达（stdio：放进挂载目录或镜像；sse/http：容器需能访问宿主机地址 `host.docker.internal`）。

### 1.1 stdio 版（`stdio_demo.py`，默认 transport 即 stdio）

```python
"""Minimal stdio MCP server (echo tool)."""

from mcp.server.fastmcp import FastMCP

mcp = FastMCP("stdio-demo")


@mcp.tool()
def echo(text: str) -> str:
    """Echo the input text back prefixed with 'echo:'."""
    return f"echo: {text}"


if __name__ == "__main__":
    mcp.run()
```

### 1.2 sse 版（`sse_demo.py`）

```python
"""Minimal SSE MCP server."""

from mcp.server.fastmcp import FastMCP

mcp = FastMCP("sse-demo")


@mcp.tool()
def echo(text: str) -> str:
    """Echo the input text back prefixed with 'echo:'."""
    return f"echo: {text}"


if __name__ == "__main__":
    mcp.settings.host = "0.0.0.0"
    mcp.settings.port = 9375
    mcp.run(transport="sse")
```

启动：`.venv/bin/python sse_demo.py`（监听 `0.0.0.0:9375`，SSE 端点为 **`/sse`**）。

### 1.3 streamable-http 版（`http_demo.py`）

同上，把端口改为 `9376`、`mcp.run(transport="streamable-http")`。
启动后端点为 **`/mcp`**（即 `http://127.0.0.1:9376/mcp`）。

***

## 2. MCP server 注册（三种 transport）

端点：`POST / GET / GET page / PATCH / DELETE $BASE/mcp-servers...`。

### 2.1 stdio 注册

```bash
curl -s -X POST "$BASE/mcp-servers" \
  -H "Authorization: Bearer $TOKEN" \
  -H "Content-Type: application/json" \
  -d '{
    "name": "demo-stdio",
    "transport": "stdio",
    "command": "/app/.venv/bin/python",
    "args": ["/path/to/stdio_demo.py"],
    "description": "本地演示 stdio server"
  }'
```

**预期**：HTTP 201，`data` 为 `McpServerRead`（含 `content_hash`、`enabled=true`、`created_by`）。
注册时会**探活**（30s 上限）并做工具名冲突校验；探活失败/超时**静默降级**为跳过冲突检查，
不阻断创建（日志 `mcp_server_tool_probe_timeout` / `mcp_server_tool_probe_failed`）。

**命令约束**：basename 必须在 `MCP_STDIO_ALLOWED_COMMANDS` 白名单；`bash/sh/zsh` 等无条件禁止；
`python -c/-m`、`node -e/--eval` 内联模式禁止（均 422）。

### 2.2 sse 注册（本次新增）

```bash
curl -s -X POST "$BASE/mcp-servers" \
  -H "Authorization: Bearer $TOKEN" \
  -H "Content-Type: application/json" \
  -d '{
    "name": "demo-sse",
    "transport": "sse",
    "url": "http://127.0.0.1:9375/sse",
    "description": "本地 SSE 演示 server"
  }'
```

**预期**：HTTP 201（Docker 内跑 API 时 url 用 `http://host.docker.internal:9375/sse`）。

### 2.3 http（streamable-http 别名）注册

```bash
curl -s -X POST "$BASE/mcp-servers" \
  -H "Authorization: Bearer $TOKEN" \
  -H "Content-Type: application/json" \
  -d '{
    "name": "demo-http",
    "transport": "http",
    "url": "http://172.17.1.227:8001/mcp",
    "description": "bn-mcp streamable-http 演示 server"
  }'
```

**预期**：HTTP 201。`http` 是 `streamable_http` 的运行时别名，行为与 sse 节一致。

### 2.4 transport 配对与命名负向用例（全部 422）

| 用例              | 请求要点                                          | 预期 message（`Validation error` + `data` 列表形态）                                                     |
| --------------- | --------------------------------------------- | ------------------------------------------------------------------------------------------------ |
| name 含 `__`     | `"name": "bad__name"` + 任意 transport          | `Value error, name must not contain '__' (reserved as the server__tool namespace separator)`     |
| sse 缺 url       | `"transport": "sse"` 不带 `url`                 | `Value error, url is required for sse transport`                                                 |
| sse 带 command   | sse + `url` + `"command": "python"`           | `Value error, command must not be set for sse transport`                                         |
| stdio 缺 command | `"transport": "stdio"` 不带 `command`           | `Value error, command is required for stdio transport`                                           |
| 明文 secret       | `headers: {"Authorization": "Bearer sk-..."}` | 业务 422：`headers.Authorization must be a ${ENV_VAR} placeholder; plaintext secrets are forbidden` |
| shell 命令        | `"command": "bash", "args": ["-c", "..."]`    | 业务 422：`stdio command 'bash' is a forbidden shell interpreter`                                   |
| 死端点注册           | `url` 指向不存在的端口                                | **201**（探活降级，见 2.1）；后续 `GET .../tools` 返回 502                                                    |

示例（name 含 `__`）：

```bash
curl -s -X POST "$BASE/mcp-servers" \
  -H "Authorization: Bearer $TOKEN" -H "Content-Type: application/json" \
  -d '{"name": "bad__name", "transport": "sse", "url": "http://127.0.0.1:9375/sse"}'
# → 422 {"code":422,"message":"Validation error","data":[{"field":"","message":"Value error, name must not contain '__' ..."}]}
```

### 2.5 读 / 改 / 删

```bash
curl -s "$BASE/mcp-servers" -H "Authorization: Bearer $TOKEN"                       # 全量（按 name 排序）
curl -s "$BASE/mcp-servers/page?page=1&pageSize=10&keyword=demo" -H "Authorization: Bearer $TOKEN"
curl -s "$BASE/mcp-servers/demo-sse" -H "Authorization: Bearer $TOKEN"              # 详情；不存在 404
curl -s -X PATCH "$BASE/mcp-servers/demo-sse" -H "Authorization: Bearer $TOKEN" \
  -H "Content-Type: application/json" -d '{"enabled": false}'                       # 改后刷新 content_hash
curl -s -X DELETE "$BASE/mcp-servers/dead-sse" -H "Authorization: Bearer $TOKEN"    # data=null
```

PATCH 语义：`name` 不可改；显式切换 `transport` 时 command/url 从 payload 重建；
修改会触发缓存失效（`shutdown_mcp_clients`）。空 payload 返回 422 `nothing to update`。

***

## 3. 工具目录与命名空间

```bash
curl -s "$BASE/tools/catalog" -H "Authorization: Bearer $TOKEN" \
  | python3 -c 'import sys,json;[print(e["name"], "|", e["source"], "|", e["server"]) for e in json.load(sys.stdin)["data"]]'
```

**预期**（以 demo-sse/demo-http/doc-stdio 为例）：

```
duckduckgo_results_json | builtin | None
ask_human | builtin | None
demo-sse__echo | mcp | demo-sse
demo-http__echo | mcp | demo-http
doc-stdio__echo | mcp | doc-stdio
```

要点：

* MCP 工具统一 `{server}__{tool}`；**跨 server 同名工具并存**（`demo-sse__echo` 与
  `demo-http__echo` 同时在册，不再报冲突——冲突检测按 namespaced 名进行）。

* builtin 工具保持裸名：`duckduckgo_results_json`（注意不是 `duckduckgo_search`）与 `ask_human`。

* **AgentApp / SubAgent 的 `allowed_tools` 必须使用 namespaced 名**（如 `demo-sse__echo`）；
  旧数据中的裸 MCP 工具名属于硬切换范围：publish 时 fail-fast，返回 422 并列出未知工具名。

***

## 4. 工具调试端点（实时探测，不读缓存）

### 4.1 `GET /mcp-servers/{name}/tools` —— 工具清单

```bash
curl -s "$BASE/mcp-servers/demo-sse/tools" -H "Authorization: Bearer $TOKEN"
```

**预期 200**：`data` 为数组，元素 `{name, description, args_schema}`，`name` 为**裸工具名**
（无 `{server}__` 前缀），`args_schema` 为 JSON Schema dict（含 `properties`/`required`）。
`enabled=false` 的 server 也可列出（只读调试）。

| 场景                  | 预期                                                            |
| ------------------- | ------------------------------------------------------------- |
| server 不存在          | 404 `mcp server 'x' not found`                                |
| 配置含未解析 `${ENV_VAR}` | 422 `... unresolved ${ENV_VAR} placeholder or invalid config` |
| 端点不可达 / server 报错   | 502 `mcp server 'x' failed to list tools: ...`                |
| server 30s 内无响应     | 504 `mcp server 'x' timed out listing tools`                  |

### 4.2 `POST /mcp-servers/{name}/call-tool` —— 工具调用

```bash
curl -s -X POST "$BASE/mcp-servers/demo-sse/call-tool" \
  -H "Authorization: Bearer $TOKEN" -H "Content-Type: application/json" \
  -d '{"tool_name": "echo", "arguments": {"text": "hello"}}'
```

**预期 200**：

```json
{"code":200,"message":"success","data":{
  "server":"demo-sse","tool_name":"echo",
  "result":[{"type":"text","text":"echo: hello","id":"lc_..."}]}}
```

`tool_name` 使用**裸工具名**；`arguments` 缺省 `{}`。

| 场景                | 预期                                                                                  |
| ----------------- | ----------------------------------------------------------------------------------- |
| server 不存在        | 404                                                                                 |
| 未知工具名             | 422 `unknown tool 'nope' on mcp server 'demo-sse'; known tools: echo`               |
| 缺必填参数（客户端守卫）      | 422 `missing required argument(s) for tool 'echo' on mcp server 'demo-sse': 'text'` |
| 参数类型错误（server 权威） | 502（server 端校验拒绝以 MCP `isError` 返回，语义为上游失败）                                         |
| 端点不可达 / 工具执行失败    | 502                                                                                 |
| 60s 超时            | 504 `mcp tool 'echo' timed out`                                                     |

> 设计说明：未知工具与缺必填参数属于**客户端可判定**的请求错误（422）；类型校验的权威在
> server 端，其拒绝按上游失败处理（502）。调试端点走独立临时 session，不影响长连池。

***

## 5. stdio manifest 自动发现（目录同步）

目录：`MCP_STDIO_ROOT`（默认 `./mcp-servers`，compose 挂载 `./mcp-servers:/app/mcp-servers`）。
每个 `*.json` 是一台 stdio server；格式与字段说明见 [mcp-servers/README.md](../mcp-servers/README.md)。
**目录中不要提交真实业务 manifest。**

示例 `mcp-servers/echo-doc.json`（本地直跑时 command 用 venv 绝对路径）：

```json
{
  "name": "doc-stdio",
  "command": "/abs/path/to/.venv/bin/python",
  "args": ["/abs/path/to/stdio_demo.py"],
  "description": "manual-testing doc stdio demo"
}
```

再放一个坏文件 `mcp-servers/bad-doc.json`（`"command": "bash"`）验证降级。

### 5.1 dry-run 预览（不写库、不探测）

```bash
curl -s "$BASE/mcp-servers/stdio-manifests" -H "Authorization: Bearer $TOKEN"
```

**预期 200**，报告结构：

```json
{"code":200,"message":"success","data":{
  "scanned": 2,
  "created": ["doc-stdio"],
  "updated": [], "unchanged": [], "skipped": [],
  "invalid": [{"file": "bad-doc.json", "reason": "stdio command 'bash' is a forbidden shell interpreter"}]}}
```

字段语义：`scanned` 扫到的文件数；`created/updated/unchanged` 为 server 名列表
（按 `content_hash` 对比）；`skipped` 为有效但未应用（新 server 探活/冲突失败）的
`{name, reason}`；`invalid` 为坏文件的 `{file, reason}`（逐文件降级，不阻塞）。

### 5.2 执行同步（幂等）

```bash
curl -s -X POST "$BASE/mcp-servers/stdio-sync" -H "Authorization: Bearer $TOKEN"
```

**预期**：首次 `created:["doc-stdio"]`（新 server 逐一 probe 30s + 冲突检查，失败仅跳过并记
`skipped`）；再执行一次 `unchanged:["doc-stdio"]`；修改 manifest 的 `description` 后再执行
`updated:["doc-stdio"]`（刷新 `content_hash`）。目录中不存在的存量 server **不受影响**。
产生 created/updated 后服务端自动失效 MCP 会话缓存。

同步后验证：`GET $BASE/tools/catalog` 出现 `doc-stdio__echo`。

**路由顺序说明**：`stdio-manifests` / `stdio-sync` 注册在 `/{name}` 字面路由之前，
不会被当作 server 名（`GET /mcp-servers/stdio-manifests` 永远是预览端点）。

***

## 6. 长连 session 池与可观测性

改造后**每台 server 一个进程级长连 session**（stdio 复用子进程，sse/http 复用 TCP 连接），
被全部 agent 共享；调用失败自动失效→重建→重试一次；空闲超过 `MCP_SESSION_IDLE_TTL`
（默认 240s）在下一次使用时惰性回收；配置变更（PATCH/同步/删除）整体失效缓存。

**Worker 所有权模型**（2026-08-24 事故修复，详见
[changelog](changelog/2026-08-24-mcp-session-worker-ownership.md)）：每个池化 session 由
专属长驻任务 `mcp-session-{server}` 持有，session 上下文的打开与关闭只发生在该任务内
（anyio cancel scope / task group 禁止跨任务退出）。所有关闭路径（TTL 回收 / 配置变更 /
失效重建 / 关机）只设置 stop 事件并等待 worker 自行退出（宽限
`MCP_SESSION_STOP_TIMEOUT` 秒，超时兜底 cancel 仍在 worker 任务内投递）；工具调用仍由
调用方直连 session（跨任务安全，无代理层）。

### 6.1 复用验证（日志）

连续请求两次工具目录，观察日志（docker：`make docker-logs`；本地直跑看控制台）：

```bash
curl -s -o /dev/null "$BASE/tools/catalog" -H "Authorization: Bearer $TOKEN"
curl -s -o /dev/null "$BASE/tools/catalog" -H "Authorization: Bearer $TOKEN"
```

**预期**：每台 server 只有**一次** `mcp_client_built` 日志（第二次直接命中池）：

```
mcp_client_built  server=demo-sse  reason=new  spec_hash=...
```

### 6.2 重建指标（/metrics）

```bash
curl -s http://localhost:8000/metrics | grep mcp_client_rebuild_total
```

**预期**：

```
mcp_client_rebuild_total{reason="new"} <N>
mcp_client_rebuild_total{reason="config_changed"} <M>
mcp_client_rebuild_total{reason="recovered"} <K>
```

`reason` 语义：`new` 首次/缓存被清空后重建；`config_changed` content\_hash 变化；
`recovered` 失效后自愈重建。注意：PATCH / stdio-sync / DELETE 会清空整池，之后的重建计 `new`。

### 6.3 session 停止指标（/metrics）

```bash
curl -s http://localhost:8000/metrics | grep mcp_session_stop_total
```

**预期**（正常运行期不增长；TTL 回收 / 关机时递增）：

```
mcp_session_stop_total{outcome="graceful"} <N>
mcp_session_stop_total{outcome="timeout_cancelled"} <M>
```

`outcome` 语义：`graceful` worker 收到 stop 后自行干净退出；`timeout_cancelled` 关闭
挂死被兜底 cancel（应关注并排查上游 server）；`crashed` / `cancelled` / `foreign_loop`
为罕见兜底路径。正常关机（Ctrl-C）后每台 server 至少一次 `graceful`。

***

## 7. 行为速查表

| 操作                                        | 端点                                        | 预期                                            |
| ----------------------------------------- | ----------------------------------------- | --------------------------------------------- |
| 注册 stdio/sse/http                         | `POST /mcp-servers`                       | 201（探活失败也 201，静默降级）                           |
| name 含 `__` / 配对错误 / 明文 secret / shell 命令 | 同上                                        | 422                                           |
| 工具目录                                      | `GET /tools/catalog`                      | builtin 裸名 + `{server}__{tool}`；跨 server 同名并存 |
| 工具清单（调试）                                  | `GET /mcp-servers/{name}/tools`           | 200 裸名+schema；404/422/502/504                 |
| 工具调用（调试）                                  | `POST /mcp-servers/{name}/call-tool`      | 200；404；422（未知工具/缺必填参数）；502（执行失败/类型错）；504     |
| manifest 预览 / 同步                          | `GET stdio-manifests` / `POST stdio-sync` | created/updated/unchanged/skipped/invalid 报告  |
| 池化复用                                      | 连续两次 catalog                              | 每台 server 仅一次 `mcp_client_built`              |

## 8. 常见问题排查

| 现象                                         | 排查                                                                                 |
| ------------------------------------------ | ---------------------------------------------------------------------------------- |
| 注册 422 `not in MCP_STDIO_ALLOWED_COMMANDS` | command 的 basename 不在白名单；用绝对路径时 basename 仍取文件名（如 `.venv/bin/python` → `python`，合法） |
| 注册成功但 catalog 看不到工具                        | 查日志 `mcp_tools_load_failed` / `mcp_server_tool_probe_failed`；stdio 常见为脚本路径在容器内不存在  |
| `GET .../tools` 502                        | server 端点不可达或协议错误；docker 内访问宿主机服务用 `host.docker.internal`                          |
| call-tool 422 `unknown tool`               | `tool_name` 应为**裸名**（不带 `{server}__` 前缀）                                           |
| publish 422 列出未知工具                         | `allowed_tools` 引用了旧裸名；改为 `{server}__{tool}` 命名                                    |
| stdio-sync `skipped: probe_failed`         | 新 manifest 的 server 无法启动/连接（看服务日志 `mcp_server_tool_probe_failed`）                  |
| 同步后行为未变                                    | 确认返回报告含 created/updated（缓存已自动失效）；unchanged 不会触发失效                                  |
