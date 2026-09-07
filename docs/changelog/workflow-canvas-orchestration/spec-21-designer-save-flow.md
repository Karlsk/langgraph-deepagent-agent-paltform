# spec-21 · 设计器保存流程 + 422 内联回显 + 未保存离开拦截

| 里程碑 | 端 | 人日 | 依赖 | 契约编号 |
| --- | --- | --- | --- | --- |
| M3 | 前端 | 2 | spec-15（设计器）、spec-09（序列化/校验）、spec-16（PUT）、spec-19（门禁） | D3（全量更新）；S6（构建期校验）；frontend-spec §10 |

## 1. 目标

打通设计器「保存」闭环：本地预校验 → `graphToDefinition` 全量序列化 → `saveWorkflow`（PUT，D3 全量替换）→ 成功反馈 / 失败把后端 422 校验错误**内联回显**到对应节点 / 字段；并在有未保存改动时拦截路由 / 刷新离开。

## 2. 范围（In / Out）

- **In**：保存动作接线（spec-15 工具栏保存按钮）；前端预校验（spec-09 `validateGraph`）先行拦截；`saveWorkflow` 调用 + loading；422 错误映射（pydantic loc → 节点 / 字段高亮 + 内联错误）；成功后 `isDirty=false` + notify + 新建态改路由到 `/workflow/:id/design`；未保存离开拦截（`onBeforeRouteLeave` + `beforeunload`）。
- **Out**：后端校验实现（spec-16）；门禁 readonly（spec-19，本 spec 消费）；YAML 预览（spec-22）。

## 3. 接口 / 组件契约

- 保存流程（`useWorkflowDesigner` 扩展）：
  ```ts
  async function save(): Promise<void> {
    const def = graphToDefinition(meta, nodes.value, edges.value)   // spec-09
    const localErrors = validateGraph(def)                          // 前端预校验
    if (localErrors.length) { fieldErrors.value = mapErrors(localErrors); return }  // 不发请求
    try { await saveWorkflow(def.workflow_id, def); isDirty.value = false; notifySuccess(...) }
    catch (e) { fieldErrors.value = mapHttp422(e) }                 // 后端 422 内联回显
  }
  ```
- 错误映射：`GraphValidationError[]` / HTTP 422 detail（pydantic `loc`）→ `{ nodeId?, field, message }`，驱动 spec-10 节点高亮 + spec-12/13 字段内联错误。
- 离开拦截：`isDirty` 为真时 `onBeforeRouteLeave` 弹 `useConfirm`（放弃 / 取消）；`beforeunload` 阻止刷新。

## 4. TDD · RED（测试先行）

新增 `tests/workflow-designer-save.spec.ts`（`vi.mock('@/api/workflow')` + stub 子组件 + mock router + fake timers）：

- [ ] 点保存（图合法）→ 先 `validateGraph`（无错）→ 调 `saveWorkflow(id, def)`（def 由 `graphToDefinition` 产出，全量 body，D3）。
- [ ] 本地预校验有错（悬空边 / 缺 entry）→ **不调** `saveWorkflow`，对应节点 / 字段标红 + 内联错误。
- [ ] `saveWorkflow` 成功 → `isDirty=false` + notifySuccess；新建态 → `router.replace('/workflow/{id}/design')`。
- [ ] `saveWorkflow` 抛 422（pydantic loc）→ 错误映射到对应节点 / 字段内联回显，`isDirty` 保持 true。
- [ ] `saveWorkflow` 抛 403（非管理员，spec-19）→ notify 权限错误，不崩溃。
- [ ] `isDirty=true` 时路由离开 → 触发 `useConfirm`；确认放弃才离开，取消则停留。
- [ ] `isDirty=true` 时 `beforeunload` → 调用 `preventDefault`（拦截刷新）。
- [ ] `readonly`（can_edit=false）→ 保存按钮禁用，`save()` 不发请求。

## 5. GREEN（最小实现）

- 在 `useWorkflowDesigner` 增 `save()` / `fieldErrors` / 离开拦截；工具栏保存按钮接 `save()`；错误映射函数 `mapHttp422`。

## 6. REFACTOR

- 错误映射（本地 + 422）统一为 `toFieldErrors(source)`；节点高亮 / 字段错误经 spec-10/12/13 既有 `readonly`/error props 消费，避免新增通道。

## 7. 验收门限（DoD Gate）

- [ ] RED 用例全绿（预校验拦截 / 全量 PUT / 422 内联 / 403 / 离开拦截 / readonly）；`npm run type-check` 零错误；`npm test` 通过。
- [ ] 保存走 `saveWorkflow`（PUT 全量，D3），无 partial patch；前端预校验复用 spec-09（不重复实现）。
- [ ] 422 错误可定位到具体节点 / 字段（内联，非仅 toast）；未保存离开有拦截。
- [ ] 无 Pinia；不硬编码颜色（错误高亮走 token）。
- [ ] 提交 `feat(web): wire workflow designer save flow with inline 422 and leave guard`。

## 8. 交付物清单

- 改：`agent-web/src/composables/useWorkflowDesigner.ts`（`save`/`fieldErrors`/离开拦截）
- 改：`agent-web/src/views/workflow/WorkflowDesignerView.vue`（保存按钮 + 错误回显接线）
- 新：`agent-web/tests/workflow-designer-save.spec.ts`
