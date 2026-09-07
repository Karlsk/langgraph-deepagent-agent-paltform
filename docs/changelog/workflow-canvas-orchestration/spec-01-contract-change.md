# spec-01 · CONTRACT §11 契约变更（新端点 + execution_logs 语义）

| 里程碑 | 端 | 人日 | 依赖 | 契约编号 |
| --- | --- | --- | --- | --- |
| M0 | 后端（文档） | 1 | 无（所有后端代码任务的前置） | CONTRACT §4/§6/§11；api-trace §7.1 |

## 1. 目标

在 [`spec/CONTRACT.md`](../../workflow-reimpl-plan/spec/CONTRACT.md) §11 变更记录中，为画布编排所需的**新端点契约**与 **execution_logs 内嵌语义**先行定稿，满足「禁止先改代码后补契约」。

## 2. 范围（In / Out）

- **In**：CONTRACT §11 追加条目；§4 冻结签名补充（新端点投影函数）；§6 语义补充（S 编号）；同步 `docs/workflow-api-and-trace.md` §7.1 与 `docs/workflow-frontend-spec.md` 引用处。
- **Out**：任何代码实现（端点 / 落盘 / 前端）。本 spec 只改契约文档。

## 3. 契约变更清单

1. **§4.12 `ApiResponse.metadata` 语义扩展**：基线四键 `{workflow_id, run_id, duration_ms, node_count}` + 可选第五键 `execution_logs: list[dict]`（仅成功响应、脱敏后）。
2. **新增端点契约签名**（api.py 层，AD-10）：
   - `GET /api/v1/workflows -> list[WorkflowSummary]`
   - `GET /api/v1/workflows/{id}?format=json|yaml -> dict | {yaml_text}`
   - `PUT /api/v1/workflows/{id}`（全量注册，body = `WorkflowDefinition` JSON）
   - `DELETE /api/v1/workflows/{id} -> null`
3. **YAML 落盘持久化 S 语义**：用户定义目录 `app/workflow/config/user/`；`PUT` 全量覆盖写 + 原子替换（S13）；文件名 `workflow_id` 白名单校验（`^[A-Za-z0-9_-]{1,64}$`）；`build_registry` 启动扫描 examples + user（S16 fail-fast）。
4. **节点类型 API 白名单 S 语义**：`PUT` 服务端仅接受 `type ∈ {llm, http}`，拒 `python`（S15 RCE）；构建期校验失败 → HTTP 422（S6）。
5. **鉴权 S 语义**：写端点（PUT/DELETE）要求管理员角色（`get_current_user` + role 门禁）。

## 4. TDD · RED（测试先行）

> 本 spec 为文档变更，无单测；「RED」= 契约评审前的**一致性检查**：

- [ ] grep CONTRACT.md 确认 §4.12 现状仅四键（变更基线存在）。
- [ ] grep `docs/workflow-api-and-trace.md` §7.1 已标「已决策」，与本次契约一致。
- [ ] 列出受影响的下游 spec（02-04、16-20），确认契约覆盖其全部签名需求（无缺口）。

## 5. GREEN（最小实现）

- 在 CONTRACT §11 追加一条变更记录（含上述 1-5），标注生效 spec 编号与日期 2026-09-07。
- §4 增补新端点投影函数签名（与既有 `_project_to_host_envelope` 风格一致）。
- §6 增补 S 语义编号（落盘 / 白名单 / 鉴权）。

## 6. REFACTOR

- 统一术语：`execution_logs` 内嵌、全量更新、白名单，与 api-trace §7.1 / frontend-spec §4.2 措辞对齐，消除歧义。

## 7. 验收门限（DoD Gate）

- [ ] CONTRACT.md §11 含本变更记录，§4/§6 签名与语义完整。
- [ ] `docs/workflow-api-and-trace.md` §7.1、`docs/workflow-frontend-spec.md` §4.2 引用处与契约**零冲突**（grep 校验）。
- [ ] 下游 spec-02..04、16..20 所需签名在契约中均可查到（无「代码先行、契约缺失」）。
- [ ] 提交 `docs: record contract change for workflow canvas orchestration endpoints`。

## 8. 交付物清单

- 改：`docs/workflow-reimpl-plan/spec/CONTRACT.md`（§4/§6/§11）
- 改：`docs/workflow-api-and-trace.md`（§7.1 措辞对齐，如需）
- 改：`docs/workflow-frontend-spec.md`（§4.2 引用对齐，如需）
