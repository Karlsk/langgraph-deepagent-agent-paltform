# spec-19 · 写端点鉴权 + 前端角色门禁

| 里程碑 | 端 | 人日 | 依赖 | 契约编号 |
| --- | --- | --- | --- | --- |
| M3 | 前后端 | 2 | spec-16、spec-18；`app/api/v1/auth.py::get_current_user` | H6（env-only）；frontend-spec §5.2；AD-10 |

## 1. 目标

为写端点（PUT/DELETE）加鉴权与「管理员」门禁，并让前端按能力（`can_edit`）门禁设计器编辑 / 保存 / 删除。**现状：`User` 模型无 role/is_admin 字段、无 `require_admin` 依赖**——本 spec 采用 **env 白名单**（不引入 DB 迁移），把范围控制在 1-2 人天。

## 2. 范围（In / Out）

- **In（后端）**：`require_workflow_admin` 依赖（`get_current_user` + `settings.WORKFLOW_ADMIN_USERNAMES` 白名单，非白名单 → 403）；只读端点（list/get/execute）要求 `get_current_user`（已登录）；新增 `GET /api/v1/workflows/capabilities -> {can_edit: bool}`（供前端门禁）。
- **In（前端）**：`useWorkflowCapabilities` composable（拉 capabilities）；设计器 / 列表按 `can_edit` 传 `readonly` 给 spec-10/12/13/14、隐藏保存 / 删除。
- **Out**：DB 级角色 / RBAC 体系（后续独立演进）；per-workflow owner 归属（MVP 全局共享）。

## 3. 接口 / 组件契约

```python
# 后端（app/workflow/api.py 或 app/api/deps）
async def require_workflow_admin(user: User = Depends(get_current_user)) -> User:
    if user.username not in settings.WORKFLOW_ADMIN_USERNAMES:  # env-only 白名单（H6：不入 DB/代码）
        raise HTTPException(403, "workflow write requires admin")
    return user

@router.get("/workflows/capabilities")
async def workflow_capabilities(user: User = Depends(get_current_user)) -> JSONResponse:
    ...  # data = {"can_edit": user.username in settings.WORKFLOW_ADMIN_USERNAMES}
```

```ts
// 前端 src/composables/useWorkflowCapabilities.ts
export function useWorkflowCapabilities(): { canEdit: Ref<boolean>; loaded: Ref<boolean>; refresh: () => Promise<void> }
// api: getWorkflowCapabilities(): Promise<{ can_edit: boolean }>
```

- PUT（spec-16）/ DELETE（spec-18）改挂 `Depends(require_workflow_admin)`；list/get/execute 挂 `Depends(get_current_user)`。

## 4. TDD · RED（测试先行）

**后端** `tests/unit/workflow/test_api_auth.py`：

- [ ] 未带 token 调 PUT/DELETE → 401。
- [ ] 非白名单用户调 PUT/DELETE → **403**；白名单用户 → 放行（进入 spec-16/18 逻辑）。
- [ ] `GET /workflows/capabilities`：白名单用户 → `{can_edit: true}`；普通用户 → `{can_edit: false}`。
- [ ] 只读端点 list/get/execute：未登录 → 401；已登录（任意角色）→ 放行。
- [ ] 白名单来自 `settings`（env），代码 / DB 无硬编码用户名（H6 grep）。

**前端** `tests/composables/use-workflow-capabilities.spec.ts` + 门禁组件测试：

- [ ] `can_edit=false` → 设计器画布 / 面板收到 `readonly=true`；保存 / 删除按钮隐藏或禁用。
- [ ] `can_edit=true` → 可编辑、保存 / 删除可见。
- [ ] capabilities 请求失败 → 保守降级为 `canEdit=false`（只读，最小权限）。

## 5. GREEN（最小实现）

- 后端：`require_workflow_admin` + capabilities 端点；`settings` 增 `WORKFLOW_ADMIN_USERNAMES`（env，默认空 = 无人可写，保守）。
- 前端：composable + 把 `canEdit` 透传为各组件 `readonly`。

## 6. REFACTOR

- 门禁判定集中到 `useWorkflowCapabilities`，组件只消费 `readonly`；后端白名单判定抽 `_is_workflow_admin(user)` 供依赖与 capabilities 共用。

## 7. 验收门限（DoD Gate）

- [ ] 前后端 RED 用例全绿；`make test` / `npm test` / `npm run type-check` / `make typecheck` 全绿。
- [ ] 默认空白名单 = 默认只读（安全默认）；非白名单写请求 403；能力失败降级只读。
- [ ] grep：白名单仅来自 env/settings，无硬编码用户名 / 密钥（H6）。
- [ ] 契约补充（`require_workflow_admin` + `GET /workflows/capabilities`）已在 CONTRACT §11 记录（随 spec-01 流程）。
- [ ] 提交 `feat(workflow): gate write endpoints by admin allowlist and expose capabilities`。

## 8. 交付物清单

- 改：`app/workflow/api.py`（依赖挂载 + capabilities 端点）、`app/core/config.py`（`WORKFLOW_ADMIN_USERNAMES`）
- 新：`agent-web/src/composables/useWorkflowCapabilities.ts`；`agent-web/src/api/workflow.ts` 增 `getWorkflowCapabilities`
- 改：`WorkflowDesignerView.vue` / `WorkflowListView.vue`（门禁接线）
- 新：`tests/unit/workflow/test_api_auth.py`、`agent-web/tests/composables/use-workflow-capabilities.spec.ts`
