# Workflow 画布编排 · 细粒度 Spec 集总览（2026-09-07）

> **状态**：**计划（本期仅落盘 spec，不实现代码）**。本目录把 `docs/workflow-frontend-spec.md` 定义的
> 「Dify 式画布编排 → 保存全量落 YAML」拆成 **22 份 1-2 人天的细粒度 spec**，按**开发顺序**编号 `spec-01..22`，
> 每份含 **TDD（RED → GREEN → REFACTOR）** 与 **最终验收门限（DoD Gate）**。
> **决策已批准（2026-09-07）**：D1 引入 Vue Flow（前端红线例外，已核准）、D2 前端传 JSON / 后端落 YAML、
> D3 全量更新、D4 `ui_layout` 注解键、D5 YAML 预览由后端返回、D6 画布不设 per-workflow `no_match_policy`。

---

## 1. 文档导航

| 文件 | 里程碑 | 端 | 人日 | 主题 |
| --- | --- | --- | --- | --- |
| `overview.md`（本文） | — | — | — | 导航 / 里程碑 / 人日 / 依赖 / 决策 / TDD 门限模板 |
| `spec-01-contract-change.md` | M0 | 后端 | 1 | CONTRACT §11 契约变更（新端点 + execution_logs 语义 + 落盘 + 白名单） |
| `spec-02-list-workflows-api.md` | M1 | 后端 | 1 | `GET /api/v1/workflows` 列表端点 |
| `spec-03-get-workflow-api.md` | M1 | 后端 | 1 | `GET /api/v1/workflows/{id}` 查定义端点（json/yaml） |
| `spec-04-execute-embed-logs.md` | M1 | 后端 | 1 | execute 响应 `metadata` 内嵌 `execution_logs`（§7.1 方案 A） |
| `spec-05-frontend-api-routing.md` | M1 | 前端 | 2 | `src/api/workflow.ts` 类型 + 函数 + 路由 + 侧边栏菜单 |
| `spec-06-workflow-list-view.md` | M1 | 前端 | 2 | `WorkflowListView.vue` 列表页（WebAgentTable） |
| `spec-07-execute-dialog-trace-drawer.md` | M1 | 前端 | 2 | `WorkflowExecuteDialog` + `WorkflowTraceDrawer` |
| `spec-08-vueflow-scaffold-tokens.md` | M2 | 前端 | 2 | 引入 Vue Flow 基建 + 画布 design tokens 扩展 |
| `spec-09-graph-serialization.md` | M2 | 前端 | 2 | `useWorkflowGraph` 画布 ↔ DSL 序列化 + 前端预校验 |
| `spec-10-workflow-canvas.md` | M2 | 前端 | 2 | `WorkflowCanvas` + `WorkflowNode` 自定义节点 + END |
| `spec-11-node-palette.md` | M2 | 前端 | 1 | `NodePalette` 拖拽新增（llm/http 白名单） |
| `spec-12-node-config-forms.md` | M2 | 前端 | 2 | `NodeConfigPanel` + `LlmNodeForm` + `HttpNodeForm` |
| `spec-13-state-schema-panel.md` | M2 | 前端 | 1 | `StateSchemaPanel` state_schema 编辑 |
| `spec-14-condition-edge-dialog.md` | M2 | 前端 | 2 | `ConditionEdgeDialog`（S7 结构化条件文法） |
| `spec-15-designer-integration.md` | M2 | 前端 | 2 | `WorkflowDesignerView` 集成 + 载入既有定义 |
| `spec-16-save-register-api.md` | M3 | 后端 | 2 | `PUT /api/v1/workflows/{id}` 全量注册（原子替换 + 构建期校验 422） |
| `spec-17-yaml-persistence.md` | M3 | 后端 | 2 | YAML 落盘持久化（user 目录 + 文件名白名单 + 启动扫描） |
| `spec-18-delete-workflow-api.md` | M3 | 后端 | 1 | `DELETE /api/v1/workflows/{id}`（唯一删除入口 + 删文件） |
| `spec-19-write-auth-gate.md` | M3 | 前后端 | 2 | 写端点鉴权（管理员）+ 前端角色门禁 UI |
| `spec-20-http-ssrf-guard.md` | M3 | 后端 | 1 | http 节点 SSRF 防护（host 白名单 / 内网拦截） |
| `spec-21-designer-save-flow.md` | M3 | 前端 | 2 | 设计器保存流程 + 422 内联回显 + 未保存离开拦截 |
| `spec-22-yaml-preview-drawer.md` | M3 | 前端 | 1 | `YamlPreviewDrawer` 只读 YAML 预览 |

**合计：35 人日**（后端 12 + 前端 23）。单人串行约 35 人日；前后端两人并行关键路径约 **23 人日**（前端）。

---

## 2. 里程碑

| 里程碑 | 目标 | 含 spec | 后端/前端人日 |
| --- | --- | --- | --- |
| **M0 契约先行** | CONTRACT §11 变更记录（禁先改代码后补契约） | 01 | 1 / 0 |
| **M1 只读 + 执行 + 轨迹**（P1） | 列表 / 查定义 / execute 内嵌 logs + 前端列表 / 执行 / 轨迹 | 02-07 | 3 / 6 |
| **M2 画布设计器**（P2，纯前端可先行） | Vue Flow 画布 + 序列化 + 节点/schema/条件面板 + 设计器集成 | 08-15 | 0 / 14 |
| **M3 保存注册写路径**（P3） | 全量保存 + 落盘 + 删除 + 鉴权 + SSRF + 保存流程 + YAML 预览 | 16-22 | 8 / 3 |

> **关键路径**：M0 → M1 → M3（写路径强依赖后端）；**M2 可与 M1 并行**（纯前端，无写端点依赖，仅 spec-15 载入依赖 spec-03）。

---

## 3. 依赖关系

```
spec-01(契约)
  ├─► spec-02(list) spec-03(get) spec-04(logs)
  │        └─► spec-05(api/路由) ─► spec-06(列表页) ─► spec-07(执行+轨迹)          [M1]
  │
  ├─► spec-08(VueFlow基建) ─► spec-09(序列化) ─► spec-10(画布)
  │                                    ├─► spec-11(palette) spec-12(节点表单)
  │                                    ├─► spec-13(schema) spec-14(条件边)
  │                                    └─► spec-15(设计器集成，载入依赖 spec-03)     [M2]
  │
  └─► spec-16(PUT注册) ─► spec-17(落盘) ─► spec-18(DELETE)
             ├─► spec-19(鉴权门禁) spec-20(SSRF)
             └─► spec-21(保存流程) spec-22(YAML预览)                                 [M3]
```

---

## 4. 每份 Spec 的统一结构（TDD + 验收门限模板）

所有 `spec-NN-*.md` 遵循同一骨架：

1. **头部表**：里程碑 / 端 / 人日 / 依赖 / 涉及契约编号。
2. **§1 目标**：一句话交付目标。
3. **§2 范围（In / Out）**：明确边界，防范围蔓延。
4. **§3 接口 / 组件契约**：函数签名 / 端点契约 / props-emits / 类型（冻结，跨 spec 不得擅改）。
5. **§4 TDD · RED（测试先行）**：**先写失败测试**清单——列出必须先行编写、当前必然失败的测试用例（覆盖正常 + 边界 + 错误分支）。
6. **§5 GREEN（最小实现）**：让 RED 测试转绿的最小实现要点。
7. **§6 REFACTOR**：去重 / 命名 / 结构优化，测试保持绿。
8. **§7 验收门限（DoD Gate）**：**最终门限**——全绿才算完成（测试通过 + lint/type-check + 安全/契约红线 + 无回归）。
9. **§8 交付物清单**：新增 / 改动文件列表。

### 4.1 全局 TDD 红线（适用每份 spec）

- **严格 RED → GREEN → REFACTOR**：先提交失败测试，再最小实现，禁止先写实现后补测试。
- **零真实网络 / 零真实 LLM**：后端测试用 fake / mock（`respx`、fake LLM）；前端测试 `vi.mock('@/api/workflow')` + happy-dom + stub Element Plus。
- **前端画布**：Vue Flow 依赖 DOM 测量，happy-dom 下**不真实渲染画布**；重点测**纯逻辑**（序列化 / 校验），画布组件测试 stub 掉 `WorkflowCanvas`。

### 4.2 全局验收门限（每份 spec 的 DoD 至少含）

- 后端：`make lint` / `ruff format --check` / `make typecheck` / `make test` 全绿；覆盖率不降；相关 grep 安全闸门（H4 无 lru_cache、H1 registry 无 clear_execution_history、S16 无 yaml.load）零违例。
- 前端：`npm run type-check` 零错误；`npm test` 全绿；不违反前端红线（无 Pinia/SSR、JSON 配置无注释、颜色走 token 不硬编码）。
- 契约：涉及签名 / 语义变更者，CONTRACT.md 已在 spec-01 同步（禁先改代码后补契约）。

---

## 5. 关键约束（各 spec 反复引用）

| 编号 | 约束 | 影响 spec |
| --- | --- | --- |
| S13 | `register_workflow` 原子替换（重复注册先 delete） | 16 |
| S15 / C8 | `python` 节点非沙箱 RCE，画布 palette 仅 `llm`/`http`，后端服务端二次拒绝 | 11, 16 |
| S7 | 条件表达式仅 `path == '字面量'` / 真值，绝不 eval | 14, 16 |
| S6 | 构建期校验失败优于运行期（悬空边 / 缺入口 / 非法条件 → 422） | 16, 21 |
| S16 | `load_definitions_from_dir` fail-fast + 仅 `yaml.safe_load` | 17 |
| H4 | 引擎无模块级缓存 / 可变全局 | 02, 03, 17 |
| H6 | 密钥 env-only + 出口 `redact`（`max_len=500`）脱敏 | 04, 12, 16 |
| H7 / C6 | `delete_workflow` 唯一删除入口，四表同步 | 18 |
| AD-02 | 引擎自包含，不 import `app.core.*`（api.py 例外） | 02-04, 16-20 |
| AD-10 | 同步内核经 `run_in_threadpool`；slowapi 限流；DI | 02-04, 16, 18 |
| §7.1 | execution_logs 内嵌 execute 响应 metadata（方案 A） | 04, 07 |

---

## 6. 相关文档

- 前端开发规格（本 spec 集的上游蓝图）：[`docs/workflow-frontend-spec.md`](../../workflow-frontend-spec.md)
- 后端 API 与 Trace：[`docs/workflow-api-and-trace.md`](../../workflow-api-and-trace.md)
- 前端开发规范（组件 / 请求层 / 测试）：[`docs/frontend-development-guide.md`](../../frontend-development-guide.md)
- Node 开发规范（LLM/HTTP config 字段）：[`docs/workflow-node-development.md`](../../workflow-node-development.md)
- 编码契约（§4 / §11 变更流程）：[`spec/CONTRACT.md`](../../workflow-reimpl-plan/spec/CONTRACT.md)
- 既有 spec 集范式参考：[`docs/changelog/agentapp-three-layer-refactor/`](../agentapp-three-layer-refactor/)
