# spec-10 · WorkflowCanvas + WorkflowNode 自定义节点

| 里程碑 | 端 | 人日 | 依赖 | 契约编号 |
| --- | --- | --- | --- | --- |
| M2 | 前端 | 2 | spec-08（Vue Flow）、spec-09（序列化） | D1；design tokens（画布色） |

## 1. 目标

实现画布容器 `WorkflowCanvas.vue`（封装 Vue Flow：渲染 / 选中 / 连线 / 删除 / 缩放）与自定义节点 `WorkflowNode.vue`（按 `type` 显示图标 + 名称 + 状态色），以及 `END` 终止节点，替换 spec-08 冒烟组件。

## 2. 范围（In / Out）

- **In**：`WorkflowCanvas.vue`（`v-model` 式 nodes/edges 双向绑定 + emits 选中/连线/删除）；`WorkflowNode.vue`（自定义节点类型 `workflow`）；`EndNode.vue`；接入 `@vue-flow/background|controls|minimap`。
- **Out**：palette 拖拽新增（spec-11）；节点配置表单（spec-12）；条件边对话框（spec-14）；序列化逻辑（spec-09 已提供，本 spec 只调用）。

## 3. 接口 / 组件契约

```ts
// WorkflowCanvas.vue
props:  { nodes: Node[]; edges: Edge[]; readonly?: boolean }
emits:  {
  'update:nodes': (nodes: Node[]) => void
  'update:edges': (edges: Edge[]) => void
  'select-node':  (id: string | null) => void
  'connect':      (edge: { source: string; target: string }) => void   // 新连线
  'remove-node':  (id: string) => void
  'remove-edge':  (id: string) => void
}
```

- 节点类型注册：`{ workflow: WorkflowNode, end: EndNode }`。
- `WorkflowNode` 展示：类型图标（llm/http）+ `data.name` + 边框色走 `--color-node-{type}` token；选中态高亮。
- `readonly` 为真时禁用连线 / 删除 / 拖拽（供 spec-19 角色门禁复用）。

## 4. TDD · RED（测试先行）

> Vue Flow 依赖 DOM 测量，happy-dom 下 stub `@vue-flow/core`（`vi.mock`），测**接线与 emits 契约**而非渲染像素。

新增 `tests/components/workflow-canvas.spec.ts`：

- [ ] 传入 nodes/edges props → 透传给 stub 的 `<VueFlow>`（props 断言）。
- [ ] stub 触发 `connect` 事件 → 组件 emit `connect({source,target})`。
- [ ] stub 触发节点选中 → emit `select-node(id)`；取消选中 → `select-node(null)`。
- [ ] `readonly=true` → 传给 VueFlow 的 `nodes-draggable/connectable=false`、`elements-selectable` 相应禁用。
- `WorkflowNode` 单测（可真实渲染，不含 VueFlow 容器）：
  - [ ] `data.type='llm'` vs `'http'` → 应用不同 token class / 图标。
  - [ ] 渲染 `data.name` 文本。

## 5. GREEN（最小实现）

- 用 `<VueFlow v-model:nodes v-model:edges :node-types>` 装配；`@connect`/`@node-click`/`@pane-click` 转 emit；`Background`/`Controls`/`MiniMap` 挂载。
- 自定义节点用 `<Handle>` 提供连接桩。

## 6. REFACTOR

- 节点类型 → token class 映射抽为常量表；emits 事件名与设计器（spec-15）约定统一，避免适配层。

## 7. 验收门限（DoD Gate）

- [ ] RED 用例全绿；`npm run type-check` 零错误；`npm test` 通过。
- [ ] 节点 / 边颜色全部走 spec-08 token，无硬编码 hex；`readonly` 门禁生效（为 spec-19 复用）。
- [ ] 删除 spec-08 冒烟组件 `WorkflowCanvasSmoke.vue`（DoD：不再存在）。
- [ ] 提交 `feat(web): add workflow canvas and custom nodes`。

## 8. 交付物清单

- 新：`agent-web/src/views/workflow/canvas/WorkflowCanvas.vue`
- 新：`agent-web/src/views/workflow/canvas/WorkflowNode.vue`、`EndNode.vue`
- 删：`agent-web/src/views/workflow/WorkflowCanvasSmoke.vue`
- 新：`agent-web/tests/components/workflow-canvas.spec.ts`
