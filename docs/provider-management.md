# Provider 管理手册

本文档描述 Provider / Model 的删除流程、硬删除逃生口与 trash 视图，
面向运维与前端工程师。

## 1. 软删除（默认）

`DELETE /api/v1/providers/{name}` 默认走软删除路径：

- `provider.deleted = true`
- 该 provider 名下所有 active `model_config.deleted = true`
- `provider_health` 行直接物理删除（保留对 provider 自身的硬删空间）
- 日志：`logger.info("provider_deleted", name=..., model_count=...)`

软删除的副作用：

- 唯一索引 `provider.name` 仍被该墓碑占用；同名重建会被 422 拒绝。
- `GET /providers` / `GET /providers/{name}` 看不到墓碑行；
  列表只会返回 `deleted=false` 的活跃行。

## 2. 硬删除（逃生口）

**危险操作，不可逆**。

### 2.1 触发条件

- 必须同时满足：
  1. query 参数 `?hard=true`
  2. HTTP header `X-Confirm-Hard-Delete: true`
- 必须通过现有 guard：
  1. 不能是 `default` provider
  2. 不能被任何 `AgentApp` / `SubAgent` 引用

任一条件不满足即 422 / 404。

### 2.2 删除范围（同一 commit）

1. 该 provider 名下所有 `model_config` 行（不论 `deleted` 状态）
2. 该 provider 的 `provider_health` 行
3. provider 行本身

事务级联由 `app.services.llm.llm_store.hard_delete_provider` 实现。
任何步骤抛错都会回滚到删前状态。

### 2.3 审计日志

```
WARNING app.core.logging:providers.py provider_hard_deleted
  name=<provider-name>
  model_count=<int>
  health_deleted=<0|1>
  actor=<session username or user_id>
```

模型端的镜像事件：

```
WARNING app.core.logging:providers.py model_config_hard_deleted
  provider=<provider-name>
  model=<model-name>
  actor=<session username or user_id>
```

### 2.4 运维 SOP（推荐）

1. **先看 trash**：`GET /providers/deleted`，确认要清理的墓碑与目标一致；
   `GET /providers/deleted/{name}/models` 看墓碑下要带走的模型清单。
2. **确认无引用**：`GET /apps` / `GET /subagents` 搜索 `model` 字段是否引用
   `"{name}/{model}"` 对。
3. **硬删除**：`DELETE /providers/{name}?hard=true` + `X-Confirm-Hard-Delete: true`。
4. **验证**：再次 `GET /providers/deleted` 应已看不到该墓碑；同名 `POST /providers` 应 201。

## 3. Trash 视图（只读）

| 端点 | 用途 | 404 条件 |
|---|---|---|
| `GET /providers/deleted` | 列出所有软删墓碑（`updated_at DESC`） | 永不 404（空就 `data: []`） |
| `GET /providers/deleted/{name}` | 按 name 查软删 provider | name 在 trash 中找不到（包括 active provider） |
| `GET /providers/deleted/{name}/models` | 查软删 provider 名下的 models | provider 不在 trash 中 |

### 3.1 响应 schema

trash 端点的 `data` 项结构：

```jsonc
{
  "id": 7,
  "name": "openai-proxy",
  "type": "OPENAI",
  "base_url": "https://api.example.com/v1",
  "api_key_masked": "****7890",   // 仅返回掩码，永不返回 auth_config 原始字典
  "enabled": true,
  "deleted": true,                // trash 视图恒为 true
  "created_by": "alice",
  "created_at": "2026-08-01T03:11:42+00:00",
  "updated_at": "2026-08-19T05:22:13+00:00"  // 软删时间（同 onupdate=_utcnow）
}
```

`/providers/deleted/{name}/models` 项额外带 `deleted: true` 字段，schema 沿用
`/providers/{name}/models` 的 `_model_read` 投影。

### 3.2 审计日志

- `GET /providers/deleted` → `provider_trash_listed` info 级（含 count + actor）
- `GET /providers/deleted/{name}` → `provider_trash_read` info 级
- `GET /providers/deleted/{name}/models` → `model_trash_listed` info 级

## 4. 路由顺序注意事项（实现细节）

`/providers/deleted` / `/providers/deleted/{name}` / `/providers/deleted/{name}/models`
**必须**注册在 `/providers/{name}` 之前（FastAPI 按注册顺序匹配路由，否则
`/providers/deleted` 会被 `/providers/{name}` 捕获并以 `name="deleted"` 报 404）。

`app/api/v1/providers.py` 当前已经按这个顺序注册，路由区块顶部有醒目注释提醒。

## 5. 端到端闭环（推荐演示路径）

```
1. POST   /providers              → 201 创建 openai-proxy
2. POST   /providers/openai-proxy/models → 201 创建 gpt-4
3. DELETE /providers/openai-proxy → 200 软删
5. GET    /providers/deleted      → 看到 openai-proxy 墓碑
6. GET    /providers/deleted/openai-proxy/models → 看到 gpt-4 (deleted=true)
7. POST   /providers              → 422 同名占用唯一索引
8. DELETE /providers/openai-proxy?hard=true
         + X-Confirm-Hard-Delete: true → 200 物理删除
9. GET    /providers/deleted      → 已无 openai-proxy
10. POST  /providers              → 201 同名重建成功
```

## 6. 相关文件

- API 端点：`app/api/v1/providers.py`
- 业务逻辑：`app/services/llm/llm_store.py`
- 单元测试：`tests/unit/api/test_providers_api.py`（+26 用例）
- 集成测试：`tests/integration/api/test_providers_hard_delete.py`（+9 用例）
- Changelog：[`docs/changelog/2026-08-19-provider-hard-delete.md`](changelog/2026-08-19-provider-hard-delete.md)