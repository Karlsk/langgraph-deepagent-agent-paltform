# spec-15 · WorkflowDesignerView 集成 + 载入既有定义

| 里程碑 | 端 | 人日 | 依赖 | 契约编号 |
| --- | --- | --- | --- | --- |
| M2 | 前端 | 2 | spec-09..14；spec-03（getWorkflow 载入） | D3/D4；EXP-G8；frontend-spec §9/§10 |

## 1. 目标

实现设计器主页面 `WorkflowDesignerView.vue`，把 palette / canvas / 节点配置 / state_schema / 条件边对话框组装为 Dify 式编排界面；支持从 `/workflow/:id/design` **载入既有定义**并还原画布，本地维护编辑态与脏标记。

## 2. 范围（In / Out）

- **In**：三栏布局（左 palette / 中 canvas / 右 面板）；顶部工具栏（workflow_id 只读或新建可填、entry_point 选择、预览按钮、保存按钮占位）；`onMounted` 按路由 id 调 `getWorkflow(id,'json')` → `definitionToGraph` 还原；`/workflow/new/design` 空白起步；接收 canvas/palette emits 更新本地 nodes/edges/schema；脏标记 `isDirty`；drop 换算坐标新增节点。
- **Out**：**保存请求 + 422 回显 + 未保存离开拦截**（spec-21）；YAML 预览抽屉本体（spec-22，本 spec 只留按钮 + 打开逻辑）；角色门禁（spec-19）。

## 3. 接口 / 组件契约

- 路由：`/workflow/:workflowId/design`（编辑）、`/workflow/new/design`（新建）。
- 本地状态（组合式，无 Pinia）：`nodes`/`edges`（Vue Flow）、`meta`（workflow_id/entry_point/state_schema）、`selectedNodeId`、`isDirty`。
- 事件编排：
  - palette `add-node` → 生成唯一 name + 默认 config → push node（`isDirty=true`）。
  - canvas `connect` → 若 target 需条件则打开 ConditionEdgeDialog，否则直接加边。
  - canvas `select-node` → 右侧 NodeConfigPanel 绑定该节点。
  - NodeConfigPanel `update:node` / `remove-node`、StateSchemaPanel `update:modelValue` → 更新本地 + `isDirty`。
- 载入失败（404/网络）→ notify 错误 + 返回列表；载入成功 → 还原画布 + `ui_layout` 位置。

## 4. TDD · RED（测试先行）

新增 `tests/workflow-designer-view.spec.ts`（`vi.mock('@/api/workflow')` + stub 画布/palette/panel 子组件 + mock router）：

- [ ] 路由带 id 挂载 → 调 `getWorkflow(id,'json')`；用返回 DTO 调 `definitionToGraph`，nodes/edges 传入画布 stub。
- [ ] `ui_layout` 存在 → 节点 position 还原（断言传给画布的 nodes position）。
- [ ] `/workflow/new/design` → 不调 getWorkflow；空画布 + workflow_id 可编辑。
- [ ] palette `add-node` → 本地 nodes 增加一个（唯一 name / 默认 config），`isDirty=true`。
- [ ] canvas `connect`（目标需条件）→ 打开 ConditionEdgeDialog；确认后边带 condition。
- [ ] NodeConfigPanel `update:node` → 对应节点 config/name 更新，`isDirty=true`。
- [ ] getWorkflow reject（404）→ notify 错误 + 跳回列表，不白屏。
- [ ] entry_point 选择 → 写入 meta；保存按钮存在但保存流程由 spec-21 接管（此处仅断言 emit 意图）。

## 5. GREEN（最小实现）

- 组合式装配各子组件 + 事件处理器；`onMounted` 载入；drop 坐标换算用 Vue Flow `screenToFlowCoordinate`（stub 下走接口）。

## 6. REFACTOR

- 编辑态收敛为 `useWorkflowDesigner` composable（nodes/edges/meta/isDirty + handlers），页面组件只做布局与接线，便于测试与 spec-21 扩展保存。

## 7. 验收门限（DoD Gate）

- [ ] RED 用例全绿；`npm run type-check` 零错误；`npm test` 通过。
- [ ] 载入 → 还原 → 编辑 → 脏标记链路完整；无 Pinia（组合式本地态）。
- [ ] 子组件（canvas/palette/panels/dialog）契约与 spec-10..14 一致，无重复实现序列化（复用 spec-09）。
- [ ] 保存 / YAML 预览 / 门禁为占位（明确移交 spec-21/22/19），无越权实现。
- [ ] 提交 `feat(web): integrate workflow designer view with load and editing`。

## 8. 交付物清单

- 新：`agent-web/src/views/workflow/WorkflowDesignerView.vue`
- 新：`agent-web/src/composables/useWorkflowDesigner.ts`
- 改：路由指向真实设计器组件（替换 spec-05 占位）
- 新：`agent-web/tests/workflow-designer-view.spec.ts`
