# spec-08 · Vue Flow 基建引入 + 画布 design tokens

| 里程碑 | 端 | 人日 | 依赖 | 契约编号 |
| --- | --- | --- | --- | --- |
| M2 | 前端 | 2 | spec-05；D1（Vue Flow 已批准） | 前端红线例外（已核准）；design-tokens |

## 1. 目标

引入 `@vue-flow/core`（+ 可选 `@vue-flow/background`、`@vue-flow/controls`、`@vue-flow/minimap`）作为画布库（**前端红线例外，2026-09-07 已批准 D1**），并扩展 `src/styles/index.css` 的画布语义 token，建立最小可渲染画布冒烟。

## 2. 范围（In / Out）

- **In**：`package.json` 增 Vue Flow 依赖；引入其样式；`index.css` 增画布 token（节点类型色 / 边色 / 选中态 / 画布背景，复用既有语义 token）；最小画布冒烟组件（渲染 2 个静态节点 + 1 条边，验证依赖可用）；扩展 `tests/design-tokens.spec.ts`。
- **Out**：完整画布交互（spec-10）；序列化（spec-09）；palette（spec-11）。

## 3. 接口 / 组件契约

- 依赖：`@vue-flow/core`（固定主版本，锁 `package-lock.json`）。
- 新增 token（示例，复用既有 A-palette）：
  ```css
  --color-node-llm: var(--color-primary-500);
  --color-node-http: var(--color-accent-500);
  --color-node-python: var(--color-danger-600); /* 仅用于禁用/告警展示，palette 不提供 */
  --color-edge-default: var(--color-border-default);
  --color-edge-conditional: var(--color-warning-600);
  --color-canvas-bg: var(--color-bg-canvas);
  ```
- 冒烟组件 `WorkflowCanvasSmoke.vue`（仅测试/验证用，后续被 spec-10 取代）。

## 4. TDD · RED（测试先行）

- [ ] `tests/design-tokens.spec.ts` 扩展：断言 `index.css` 含上述画布 token（读文件 `toContain`，与既有守卫同款）。
- [ ] 新增 `tests/components/workflow-canvas-smoke.spec.ts`：Vue Flow 在 happy-dom 下**不真实测量布局**，故用 `vi.mock('@vue-flow/core')` stub，断言冒烟组件把 nodes/edges props 传入 stub（验证接线，不验证渲染像素）。
- [ ] 依赖存在性：`package.json` 含 `@vue-flow/core`（守卫测试或 CI `npm ls`）。

## 5. GREEN（最小实现）

- `npm i @vue-flow/core @vue-flow/background @vue-flow/controls @vue-flow/minimap`；`main.ts` 或组件内引入 Vue Flow 样式。
- `index.css` 落地画布 token；写冒烟组件（`<VueFlow :nodes :edges>`）。

## 6. REFACTOR

- token 命名归入既有语义体系（不新造硬编码 hex）；冒烟组件标注 `@deprecated`（spec-10 替换）。

## 7. 验收门限（DoD Gate）

- [ ] RED 用例全绿；`npm run type-check` 零错误；`npm test` 通过；`npm run build` 成功（Vue Flow 打包无 externals 报错）。
- [ ] 画布颜色**全部走 token**，`index.css` 无新增硬编码 hex（复用 A-palette）。
- [ ] `package.json` / `package-lock.json` 严格 JSON（无注释）；未引入 Pinia / SSR。
- [ ] D1 批准记录写入 overview（已含）；提交 `feat(web): scaffold vue-flow canvas and canvas design tokens`。

## 8. 交付物清单

- 改：`agent-web/package.json`、`package-lock.json`（Vue Flow 依赖）
- 改：`agent-web/src/styles/index.css`（画布 token）
- 改：`agent-web/src/main.ts`（如需引入 Vue Flow 样式）
- 新：`agent-web/src/views/workflow/WorkflowCanvasSmoke.vue`（临时）
- 改：`agent-web/tests/design-tokens.spec.ts`；新：`tests/components/workflow-canvas-smoke.spec.ts`
