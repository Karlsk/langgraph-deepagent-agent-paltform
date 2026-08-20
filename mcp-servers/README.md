# stdio MCP Server Manifests

本目录是 stdio MCP server 的 manifest 注册目录（`MCP_STDIO_ROOT`，容器内为
`/app/mcp-servers`）。每个 `*.json` 文件描述一台 stdio MCP server，
Docker 部署时把本目录挂载进容器，然后调用一次同步接口即可批量注册：

```bash
# 预览（dry-run，不写库不探测）：会创建/更新/无变化/无效 明细
curl -H "Authorization: Bearer <chat-session-token>" \
  http://localhost:8000/api/v1/mcp-servers/stdio-manifests

# 执行同步（新增 server 会先 probe + 冲突检查，失败仅跳过并记入报告）
curl -X POST -H "Authorization: Bearer <chat-session-token>" \
  http://localhost:8000/api/v1/mcp-servers/stdio-sync
```

## Manifest 格式

文件名任意（`*.json`）；`name` 省略时取文件名 stem。字段：

```json
{
  "name": "filesystem",
  "command": "npx",
  "args": ["-y", "@modelcontextprotocol/server-filesystem", "/data"],
  "env": { "FS_API_TOKEN": "${FS_API_TOKEN}" },
  "enabled": true,
  "description": "Read/write access to /data"
}
```

| 字段 | 类型 | 说明 |
| --- | --- | --- |
| `name` | str | 可选；server 唯一名，禁止包含 `__`（保留为 `{server}__{tool}` 命名空间分隔符） |
| `command` | str | 必填；可执行文件名，必须在 `MCP_STDIO_ALLOWED_COMMANDS` 白名单内（默认 `python,node,uvx,npx`） |
| `args` | list[str] | 命令参数；禁止 `python -c/-m`、`node -e/--eval` 等内联执行模式 |
| `env` | dict[str, str] | 子进程环境变量；值只允许 `${ENV_VAR}` 占位符，明文密钥会被拒绝 |
| `enabled` | bool | 可选，默认 `true` |
| `description` | str | 可选，默认空 |

## 同步语义

- 按 `name` upsert：新 server 创建（`created_by="stdio-registry"`，内容有变化才更新
  并刷新 `content_hash`）；目录中不存在的存量 server 不受影响。
- 坏文件（非法 JSON、策略违规、重名）逐个降级记录到报告的 `invalid` 列表，
  不阻塞其他 manifest。
- 同步产生创建/更新后服务端自动失效 MCP 会话缓存；删除 manifest 不会删除
  已注册的 server（请走 `DELETE /mcp-servers/{name}`）。

> 注意：本目录不要提交真实业务 manifest（可能包含内网地址等环境细节）；
> 上线环境由部署方在宿主机维护目录内容。
