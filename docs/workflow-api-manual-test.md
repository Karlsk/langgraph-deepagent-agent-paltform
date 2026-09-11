# Workflow API 手动测试文档

> 基于 2026-09-11 Docker 全栈环境（`make docker-up ENV=development MODE=full`）实测结果生成。
> 后端 `http://localhost:8000`，前端 `http://localhost:80`。

## 前置条件

1. Docker 全栈已启动：`make docker-up ENV=development MODE=full`
2. 健康检查通过：`curl http://localhost:8000/health` → `{"status": "healthy"}`
3. 已注册用户并获取 JWT token（见下方「认证」节）
4. `.env.development` 中设置 `WORKFLOW_ADMIN_USERNAMES=admin`（写端点需要管理员权限）

## 认证

```bash
# 注册（密码需含特殊字符）
curl -s http://localhost:8000/api/v1/auth/register \
  -H "Content-Type: application/json" \
  -d '{"username":"admin","email":"admin@test.com","password":"Test1234!"}'

# 响应中 data.access_token 即为 JWT，后续请求携带：
# -H "Authorization: Bearer <token>"
```

---

## 1. GET /api/v1/workflows — 列表

```bash
curl -s http://localhost:8000/api/v1/workflows \
  -H "Authorization: Bearer $TOKEN"
```

**预期响应**：
```json
{
  "code": 200,
  "message": "success",
  "data": [
    {"workflow_id": "condition_branch_demo", "node_count": 3, "entry_point": "check"},
    {"workflow_id": "demo_http", "node_count": 1, "entry_point": "fetch"},
    {"workflow_id": "demo_minimal", "node_count": 1, "entry_point": "greet"}
  ]
}
```

**验证点**：
- [ ] 返回 `code: 200`，`data` 为数组
- [ ] 每条含 `workflow_id`、`node_count`、`entry_point`
- [ ] 按 `workflow_id` 升序排列
- [ ] 无 token → `code: 403`

---

## 2. GET /api/v1/workflows/{id} — 查定义（JSON）

```bash
curl -s http://localhost:8000/api/v1/workflows/demo_minimal \
  -H "Authorization: Bearer $TOKEN"
```

**预期响应**：
```json
{
  "code": 200,
  "data": {
    "workflow_id": "demo_minimal",
    "entry_point": "greet",
    "nodes": [{"name": "greet", "type": "llm", "config": {...}}],
    "edges": [{"source": "greet", "target": "END", "condition": null}],
    "state_schema": {"input": {"type": "str", ...}, "messages": {"type": "list", ...}},
    "ui_layout": null,
    "operator_logs": {...}
  }
}
```

**验证点**：
- [ ] 含 `nodes`、`edges`、`state_schema`、`entry_point`
- [ ] **不含** `execution_history`（运行期字段被排除）
- [ ] `ui_layout` 字段存在（可为 null 或对象，D4）
- [ ] 未知 id → `code: 404`

---

## 3. GET /api/v1/workflows/{id}?format=yaml — 查定义（YAML）

```bash
curl -s "http://localhost:8000/api/v1/workflows/demo_minimal?format=yaml" \
  -H "Authorization: Bearer $TOKEN"
```

**预期响应**：
```json
{
  "code": 200,
  "data": {
    "yaml_text": "workflow_id: demo_minimal\nentry_point: greet\n..."
  }
}
```

**验证点**：
- [ ] `data.yaml_text` 为字符串
- [ ] `yaml.safe_load(yaml_text)` 回读 == JSON 投影（往返一致）
- [ ] YAML 中含 `ui_layout`（D4 持久化）

---

## 4. GET /api/v1/workflows/capabilities — 能力查询

```bash
curl -s http://localhost:8000/api/v1/workflows/capabilities \
  -H "Authorization: Bearer $TOKEN"
```

**预期响应（管理员）**：
```json
{"code": 200, "data": {"can_edit": true, "metadata": {}}}
```

**预期响应（非管理员）**：
```json
{"code": 200, "data": {"can_edit": false, "metadata": {}}}
```

**验证点**：
- [ ] 管理员用户 → `can_edit: true`
- [ ] 非管理员 → `can_edit: false`
- [ ] 无 token → `code: 403`
- [ ] 空白名单（默认）→ 所有人 `can_edit: false`（安全默认）

---

## 5. PUT /api/v1/workflows/{id} — 全量保存/注册

```bash
curl -s -X PUT http://localhost:8000/api/v1/workflows/test-flow \
  -H "Authorization: Bearer $TOKEN" \
  -H "Content-Type: application/json" \
  -d '{
    "workflow_id": "test-flow",
    "entry_point": "greet",
    "nodes": [
      {"name": "greet", "type": "llm", "config": {"llm_type": "openai", "model_name": "gpt-4o-mini", "system_prompt": "Say hello."}}
    ],
    "edges": [{"source": "greet", "target": "END"}],
    "state_schema": {"input": {"type": "str"}},
    "ui_layout": {"nodes": {"greet": {"x": 100, "y": 50}}}
  }'
```

**预期响应**：`code: 200`，`data` 为保存后的定义（含 `ui_layout`）。

**验证点**：
- [ ] 新建保存 → `code: 200`
- [ ] 重复 PUT（改内容）→ 原子替换（S13），节点数更新，不翻倍
- [ ] `ui_layout` 往返保留（D4）：PUT 含 ui_layout → GET 返回相同 ui_layout
- [ ] `type: "python"` → `code: 422`（S15 RCE 防护）
- [ ] `workflow_id` 与路径不一致 → `code: 422`
- [ ] 悬空边 / entry_point 不存在 → `code: 422`（S6 构建期校验）
- [ ] 非管理员 → `code: 403`
- [ ] 保存后 YAML 文件落盘：`docker exec deploy-app-1 ls /app/app/workflow/config/user/`

---

## 6. POST /api/v1/workflows/{id}/execute — 执行

```bash
# 注意：请求体为扁平 state 字段字典（非 {"input": {...}} 包装）
curl -s -X POST http://localhost:8000/api/v1/workflows/demo_minimal/execute \
  -H "Authorization: Bearer $TOKEN" \
  -H "Content-Type: application/json" \
  -d '{"messages": [{"role": "user", "content": "Say hi"}]}' \
  --max-time 120
```

**预期响应（成功）**：
```json
{
  "code": 200,
  "data": {
    "output": {...},
    "metadata": {
      "workflow_id": "demo_minimal",
      "success": true,
      "node_count": 1,
      "execution_time_ms": 1234,
      "execution_logs": [
        {"node_name": "greet", "node_type": "llm", "timestamp": "...", "input_data": {...}, "output_data": {...}, "execution_time_ms": 1200, "error": null}
      ]
    }
  }
}
```

**验证点**：
- [ ] 成功执行 → `metadata.execution_logs` 存在，条数 == 执行节点数
- [ ] 每条 log 含 7 字段：`node_name`、`node_type`、`timestamp`、`input_data`、`output_data`、`execution_time_ms`、`error`
- [ ] 敏感值被脱敏（H6 redact，max_len=500）
- [ ] 失败执行 → `code: 500`，`data: null`，**不含** execution_logs
- [ ] 未知 id → `code: 404`
- [ ] 超时 600s（前端 api 层设置）

**Mock 节点执行（零网络，S9）**：
```bash
# 先 PUT 一个 mock_enabled=true 的 HTTP 节点 workflow，再 execute
# mock 节点不发起真实网络请求，直接返回 mock_responses 中的值
```

> **已知问题**：前端 `executeWorkflow()` 发送 `{"input": {...}}` 包装格式，
> 但后端 `execute_workflow` 将整个请求体作为 state 传入（`input_data = payload or {}`）。
> 当 state_schema 含 `input: str` 字段时，包装格式导致类型校验失败。
> **临时绕过**：直接发送扁平 state 字段（如 `{"messages": [...]}`）。
> **修复建议**：后端改为 `input_data = payload.get("input", payload) if payload else {}`，
> 或前端改为直接发送 state 字段字典。

---

## 7. DELETE /api/v1/workflows/{id} — 删除

```bash
curl -s -X DELETE http://localhost:8000/api/v1/workflows/test-flow \
  -H "Authorization: Bearer $TOKEN"
```

**预期响应**：
```json
{"code": 200, "message": "success", "data": {"result": null, "metadata": {}}}
```

**验证点**：
- [ ] 已注册 workflow → `code: 200`，`data.result: null`
- [ ] 删除后 GET 列表不含该 id
- [ ] 删除后 GET 详情 → `code: 404`
- [ ] 重复 DELETE → `code: 404`（幂等边界）
- [ ] 磁盘 YAML 文件同步删除
- [ ] 非管理员 → `code: 403`

---

## 8. SSRF 防护（spec-20）

```bash
# 私网 URL → 422
curl -s -X PUT http://localhost:8000/api/v1/workflows/ssrf-test \
  -H "Authorization: Bearer $TOKEN" \
  -H "Content-Type: application/json" \
  -d '{
    "workflow_id": "ssrf-test", "entry_point": "fetch",
    "nodes": [{"name": "fetch", "type": "http", "config": {"url": "http://127.0.0.1/secret", "method": "GET"}}],
    "edges": [{"source": "fetch", "target": "END"}], "state_schema": {}
  }'
```

**验证点**：
- [ ] `http://127.0.0.1`、`http://localhost`、`http://10.0.0.1`、`http://192.168.1.1`、`http://169.254.169.254` → 全部 `code: 422`
- [ ] `file:///etc/passwd`、`gopher://...` → `code: 422`（协议白名单）
- [ ] `https://example.com/api`（公网）→ 通过
- [ ] `mock_enabled: true` + 私网 URL → `code: 200`（S9 豁免）
- [ ] 执行期二次校验：即使注册绕过，执行前仍拦截

---

## 9. 鉴权门禁（spec-19）

| 端点 | 无 token | 普通用户 | 管理员 |
|------|----------|----------|--------|
| GET /workflows | 403 | 200 | 200 |
| GET /workflows/{id} | 403 | 200 | 200 |
| POST /workflows/{id}/execute | 403 | 200 | 200 |
| GET /workflows/capabilities | 403 | `{can_edit: false}` | `{can_edit: true}` |
| PUT /workflows/{id} | 403 | 403 | 200 |
| DELETE /workflows/{id} | 403 | 403 | 200 |

---

## 测试结果汇总（2026-09-11 实测）

| # | 端点 | 结果 | 备注 |
|---|------|------|------|
| 1 | GET /workflows | ✅ | 返回 3 个示例 workflow |
| 2 | GET /workflows/{id} (json) | ✅ | 含 ui_layout，不含 execution_history |
| 3 | GET /workflows/{id} (yaml) | ✅ | yaml_text 往返一致 |
| 4 | GET /workflows/capabilities | ✅ | 管理员 can_edit: true |
| 5 | PUT /workflows/{id} (新建) | ✅ | ui_layout 保留 |
| 6 | PUT /workflows/{id} (更新) | ✅ | S13 原子替换 |
| 7 | PUT python type | ✅ | 422 (S15) |
| 8 | PUT id mismatch | ✅ | 422 |
| 9 | PUT SSRF url | ✅ | 422 (spec-20) |
| 10 | PUT mock + private url | ✅ | 200 (S9 豁免) |
| 11 | POST execute (mock) | ✅ | execution_logs 含 1 条 |
| 12 | POST execute (LLM) | ⚠️ | 状态校验通过；LLM 调用失败（环境模型配置问题，非代码 bug） |
| 13 | DELETE | ✅ | 200，列表移除 |
| 14 | DELETE 重复 | ✅ | 404 幂等 |
| 15 | 无 token | ✅ | 403 |

**已知问题**：execute 端点输入格式 — 前端发送 `{"input": {...}}` 包装，后端期望扁平 state 字典。详见第 6 节。
