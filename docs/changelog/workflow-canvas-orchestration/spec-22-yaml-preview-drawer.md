# spec-22 · YamlPreviewDrawer 只读 YAML 预览

| 里程碑 | 端 | 人日 | 依赖 | 契约编号 |
| --- | --- | --- | --- | --- |
| M3 | 前端 | 1 | spec-15（设计器）、spec-03（`getWorkflow(id,'yaml')`） | **D5**（YAML 由后端返回，前端不生成本地 YAML）；D2 |

## 1. 目标

实现 `YamlPreviewDrawer.vue`：在设计器中打开抽屉，展示当前 workflow 的 YAML 文本（只读），让用户确认画布编排最终落盘的 DSL 形态。遵循 **D5：YAML 由后端 `format=yaml` 返回，前端不引 js-yaml、不本地生成**。

## 2. 范围（In / Out）

- **In**：设计器工具栏「预览 YAML」按钮 → 打开抽屉；调 `getWorkflow(id, 'yaml')` 取 `yaml_text`；只读代码块展示（等宽字体 + 行号可选）；复制按钮。
- **Out**：本地由画布 JSON 生成 YAML（违反 D5，禁止）；编辑 YAML（只读）；无状态预览端点（未保存草稿的实时预览，列为后续增强）。

## 3. 接口 / 组件契约

```ts
// YamlPreviewDrawer.vue
props: { modelValue: boolean; workflowId: string; dirty?: boolean }
emits: { 'update:modelValue': (v: boolean) => void }
// 打开时 getWorkflow(workflowId, 'yaml') -> { yaml_text: string }
```

- **未保存提示**：`dirty=true` 时抽屉顶部提示「预览为**已保存**版本，未保存的画布改动不包含在内；保存后再预览可看到最新 YAML」（D5 约束下前端不本地生成）。
- 新建且从未保存（无 id / 404）→ 提示「请先保存后再预览 YAML」，不报错。

## 4. TDD · RED（测试先行）

新增 `tests/components/yaml-preview-drawer.spec.ts`（`vi.mock('@/api/workflow')` + stub Element Plus）：

- [ ] 打开抽屉（已保存 id）→ 调 `getWorkflow(id, 'yaml')`；渲染返回的 `yaml_text`。
- [ ] `dirty=true` → 展示「已保存版本」提示文案。
- [ ] `getWorkflow` 404（新建未保存）→ 展示「请先保存」提示，不崩溃。
- [ ] `getWorkflow` reject（网络）→ 错误态 + notify，不白屏。
- [ ] 复制按钮 → 调用 clipboard（stub）写入 `yaml_text`。
- [ ] **守卫**：组件不 import `js-yaml` / 不调用任何本地 YAML 序列化（D5 grep / 依赖断言）。

## 5. GREEN（最小实现）

- `el-drawer` + `useRequest` 拉 yaml_text + `<pre>` 只读展示；dirty / 404 / 错误分支文案；复制用既有 clipboard util。

## 6. REFACTOR

- 只读代码块抽 `YamlBlock` 小组件（与 spec-07 `JsonBlock` 风格统一）；提示文案归入 i18n。

## 7. 验收门限（DoD Gate）

- [ ] RED 用例全绿（含 dirty / 404 / 错误 / D5 守卫）；`npm run type-check` 零错误；`npm test` 通过。
- [ ] grep 确认前端**无** `js-yaml` / 本地 YAML 生成（D5 硬门）；YAML 全部来自后端 `format=yaml`。
- [ ] 只读（不可编辑）；未保存 / 未创建分支有清晰提示，无异常。
- [ ] 提交 `feat(web): add read-only yaml preview drawer`。

## 8. 交付物清单

- 新：`agent-web/src/views/workflow/YamlPreviewDrawer.vue`
- 改：`WorkflowDesignerView.vue`（预览按钮 + 抽屉接线）
- 新：`agent-web/tests/components/yaml-preview-drawer.spec.ts`
