# spec-07 · WorkflowExecuteDialog + WorkflowTraceDrawer

| 里程碑 | 端 | 人日 | 依赖 | 契约编号 |
| --- | --- | --- | --- | --- |
| M1 | 前端 | 2 | spec-05、spec-06；spec-04（后端内嵌 logs） | api-trace §7.1；EXP-G8 输出投影 |

## 1. 目标

实现「执行」对话框（输入 JSON → 调 `executeWorkflow` → 展示 output + metadata）与「执行轨迹」抽屉（渲染 `metadata.execution_logs` 逐节点过程），让前端可视化 workflow 运行过程。

## 2. 范围（In / Out）

- **In**：`WorkflowExecuteDialog.vue`（JSON 输入 + 三示例预设 + 前端解析校验 + 结果展示）；`WorkflowTraceDrawer.vue`（逐节点 logs 渲染 + error 标红 + logs 缺失降级）。
- **Out**：SSE / 流式实时进度（§7.2 范围外）；轨迹持久化查询。

## 3. 接口 / 组件契约

- `WorkflowExecuteDialog` props：`{ workflowId: string; modelValue: boolean }`；emits：`update:modelValue`、`executed`。
  - 调 `executeWorkflow(workflowId, parsedInput)` → 展示 `output`（JSON 视图）+ `metadata`（含耗时 / 节点数）+「查看轨迹」按钮。
- `WorkflowTraceDrawer` props：`{ logs?: ExecutionLogView[]; modelValue: boolean }`。
  - 按 `timestamp` 升序渲染每节点：`node_name` / `node_type` / `execution_time_ms` / `input_data` / `output_data` / `error`（有则标红）。
  - `logs` 为空 / 缺失 → 展示「本次响应未包含执行轨迹」降级提示，不抛错。

## 4. TDD · RED（测试先行）

新增 `agent-web/tests/workflow-execute.spec.ts`（`vi.mock('@/api/workflow')` + fake timers + stub Element Plus）：

- [ ] 打开对话框 → 加载示例预设；选预设填充输入框。
- [ ] 非法 JSON → 前端拦截，展示校验错误，**不调** `executeWorkflow`。
- [ ] 合法输入提交 → 调 `executeWorkflow(id, input)`；loading 态；成功渲染 output + metadata。
- [ ] `executeWorkflow` reject（404/500）→ 错误提示（notify），对话框不崩。
- 轨迹抽屉：
  - [ ] 传 logs → 按 timestamp 升序渲染 N 个节点条目。
  - [ ] 某节点 `error` 非空 → 该条标红 / 错误样式。
  - [ ] logs 缺失 → 降级提示文案，无异常。

## 5. GREEN（最小实现）

- 对话框：textarea + JSON.parse 校验 + 预设下拉 + 结果区；抽屉：`el-drawer` + 列表渲染 logs。

## 6. REFACTOR

- JSON 展示抽 `JsonBlock` 小组件（output / input_data / output_data 复用）；时间格式化统一 util。

## 7. 验收门限（DoD Gate）

- [ ] RED 用例全绿；`npm run type-check` 零错误；`npm test` 通过。
- [ ] 执行超时依赖 api 层 600s（spec-05）；loading / 错误 / 降级三态齐备。
- [ ] 轨迹渲染字段与后端 `ExecutionLogView`（spec-04）契约一致；不硬编码颜色（error 标红走 token）。
- [ ] 提交 `feat(web): add workflow execute dialog and trace drawer`。

## 8. 交付物清单

- 新：`agent-web/src/views/workflow/WorkflowExecuteDialog.vue`
- 新：`agent-web/src/views/workflow/WorkflowTraceDrawer.vue`
- 改：`WorkflowListView.vue`（接入执行对话框）
- 新：`agent-web/tests/workflow-execute.spec.ts`
