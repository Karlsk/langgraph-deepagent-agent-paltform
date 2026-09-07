# spec-06 · WorkflowListView 列表页

| 里程碑 | 端 | 人日 | 依赖 | 契约编号 |
| --- | --- | --- | --- | --- |
| M1 | 前端 | 2 | spec-05（api/路由） | frontend-development-guide（WebAgentTable/useRequest/useConfirm/notify） |

## 1. 目标

实现 workflow 列表页，展示已注册 workflow 摘要，提供「设计 / 执行 / 删除」入口，遵循项目资产页范式（`WebAgentTable` + `useRequest` + `useConfirm` + `notify`）。

## 2. 范围（In / Out）

- **In**：`WorkflowListView.vue`（表格 + 操作列 + 空态 + 加载/错误三态）；删除走 `useConfirm` + `deleteWorkflow`；「设计」跳 `/workflow/:id/design`；「执行」打开 spec-07 对话框。
- **Out**：执行对话框 / 轨迹抽屉本体（spec-07）；设计器（spec-15）。

## 3. 接口 / 组件契约

- 数据源：`api(query) => listWorkflows()`（服务端暂无分页则前端本地分页，沿用 ProviderList 模式）。
- columns：`workflow_id`、`node_count`、`entry_point`、`description?`、操作（设计 / 执行 / 删除）。
- 删除成功后 `tableRef.refresh()` + `notifySuccess`；失败由统一通知层展示（request.ts 422/404）。

## 4. TDD · RED（测试先行）

新增 `agent-web/tests/workflow-list-view.spec.ts`（`vi.mock('@/api/workflow')` + stub Element Plus + happy-dom）：

- [ ] 挂载 → 调 `listWorkflows()`，渲染返回行（workflow_id / node_count / entry_point）。
- [ ] 空数组 → 展示空态文案，不报错。
- [ ] `listWorkflows` reject → 错误态 + `notify` 被调用（不白屏）。
- [ ] 点「删除」→ 触发 `useConfirm`；确认后调 `deleteWorkflow(id)`，成功 refresh。
- [ ] 点「设计」→ `router.push('/workflow/{id}/design')`（mock router 断言）。
- [ ] 点「执行」→ emit / 打开对话框（stub 子组件，断言 props 传入 workflow_id）。

## 5. GREEN（最小实现）

- 用 `WebAgentTable` + `useRequest` 装配列表；操作列按钮绑定路由跳转 / 对话框 / 删除确认。

## 6. REFACTOR

- 列定义、操作按钮抽为常量 / 小组件；删除确认文案与 provider 页保持一致风格。

## 7. 验收门限（DoD Gate）

- [ ] RED 用例全绿；`npm run type-check` 零错误；`npm test` 通过。
- [ ] 严格走 `WebAgentTable`/`useRequest`/`useConfirm`/`notify` 五基建，无自造表格 / 弹窗。
- [ ] 三态（加载 / 空 / 错误）齐备；颜色 / 间距走 design token，不硬编码。
- [ ] 提交 `feat(web): add workflow list view`。

## 8. 交付物清单

- 新：`agent-web/src/views/workflow/WorkflowListView.vue`
- 改：路由指向真实组件（替换 spec-05 占位）
- 新：`agent-web/tests/workflow-list-view.spec.ts`
