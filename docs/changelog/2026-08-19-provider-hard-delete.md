# Changelog — Provider 硬删除逃生口 + Trash 视图

> 状态：**已上线**。在保留 `provider.name` 与 `(provider_id, name)` 唯一约束的前提下，为
> `DELETE /providers/{name}` 与 `DELETE /providers/{name}/models/{model}` 增加硬删除
> 逃生口；同时新增三个独立 trash 视图端点，让运维在不消费现有活跃 API 的前提下查询墓碑。

## 1. 行为变更（向后兼容）

| 项 | 旧行为 | 新行为 |
|---|---|---|
| `DELETE /providers/{name}` | 软删 (`deleted=true` + 级联软删 models) | 同前；新增可选硬删分支 |
| `DELETE /providers/{name}/models/{model}` | 软删模型 | 同前；新增可选硬删分支 |
| `POST /providers` (同名重建) | 同名占用唯一槽，重建 422 | 硬删墓碑后同名重建 201 |
| 列表 / 详情 / 创建 / 更新 | 保持不变 | 零回归 |

## 2. 新增能力

### 2.1 硬删除逃生口

| 项 | 值 |
|---|---|
| 触发 | `DELETE /api/v1/providers/{name}?hard=true` 同时携带 `X-Confirm-Hard-Delete: true` 头 |
| 校验 | 必须通过现有 default 保护 + 引用保护（与软删守卫一致） |
| 范围 | provider + 全部 model_config + provider_health 物理删除（同一 commit） |
| 不可逆 | 是 |
| 审计 | `logger.warning("provider_hard_deleted", name, model_count, health_deleted, actor)` + 模型端镜像事件 `model_config_hard_deleted` |
| 失败 | 缺 header → 422；guard 拒绝 → 422；未找到 → 404 |
| 权限 | 与现有 DELETE 一致；后续 admin role 引入时可基于 warning 日志事件收紧 |

#### 危险护栏

- **header 必传**：`X-Confirm-Hard-Delete: true`，缺失或值非 `"true"` 直接 422，避免误触。
- **不可绕过 guard**：default provider 保护、AgentApp / SubAgent 引用保护对硬删同样生效。
- **强告警级别**：成功硬删发 `warning` 级日志（不是 `info`），便于 SIEM / 告警系统抓取。
- **零残留**：provider + models + health 物理删除，唯一索引槽被释放，同名重建立刻 201。

#### 调用示例

```bash
# 硬删除 provider
curl -X DELETE \
  -H "Authorization: Bearer ${TOKEN}" \
  -H "X-Confirm-Hard-Delete: true" \
  "http://localhost:8000/api/v1/providers/openai-proxy?hard=true"

# 硬删除模型
curl -X DELETE \
  -H "Authorization: Bearer ${TOKEN}" \
  -H "X-Confirm-Hard-Delete: true" \
  "http://localhost:8000/api/v1/providers/openai-proxy/models/gpt-4?hard=true"
```

### 2.2 Trash 视图（只读）

| 方法 | 端点 | 用途 |
|---|---|---|
| GET | `/api/v1/providers/deleted` | 列出所有软删墓碑（`updated_at DESC`） |
| GET | `/api/v1/providers/deleted/{name}` | 按 name 查软删 provider；active provider → 404 |
| GET | `/api/v1/providers/deleted/{name}/models` | 查软删 provider 名下的 models |

#### 安全语义

- trash 投影物理剔除 `auth_config` 原始字段，只暴露 `api_key_masked`；这是对软删行的额外安全保护。
- trash 视图只看墓碑：active provider / active model 在 trash 端点里返回 404，避免混淆。
- 三个端点都发 `info` 级审计事件：`provider_trash_listed` / `provider_trash_read` / `model_trash_listed`。

#### 调用示例

```bash
# 列出所有软删墓碑
curl -H "Authorization: Bearer ${TOKEN}" \
  "http://localhost:8000/api/v1/providers/deleted"

# 查特定墓碑
curl -H "Authorization: Bearer ${TOKEN}" \
  "http://localhost:8000/api/v1/providers/deleted/openai-proxy"

# 查墓碑下的 models
curl -H "Authorization: Bearer ${TOKEN}" \
  "http://localhost:8000/api/v1/providers/deleted/openai-proxy/models"
```

## 3. 响应形态

DELETE 响应 `data` 字段仍为 `null`（保持现有活跃端点的形态稳定，避免前端做多余改动）；
强烈建议前端在用户点击「永久删除」按钮前先调用 trash 视图确认目标。

## 4. 影响面

| 层 | 文件 |
|---|---|
| API 端点 | `app/api/v1/providers.py` (`delete_provider`, `delete_provider_model`, 新增 3 个 trash 端点) |
| 业务逻辑 | `app/services/llm/llm_store.py` (`hard_delete_provider`, `list_deleted_providers`, `get_deleted_provider`, `list_models_under_deleted_provider`) |
| 单元测试 | `tests/unit/api/test_providers_api.py` (+ 26 用例覆盖硬删分支 + trash 端点) |
| 集成测试 | `tests/integration/api/test_providers_hard_delete.py` (新增，9 用例覆盖完整闭环) |

## 5. 后续动作

- 当项目引入 admin role 时，基于 `provider_hard_deleted` / `model_config_hard_deleted` warning 日志收紧硬删权限。
- 当前 admin 控制台可消费 trash 视图（`/providers/deleted*`），无需后端新增独立接口。