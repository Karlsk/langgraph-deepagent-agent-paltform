# spec-11 · NodePalette 拖拽新增节点

| 里程碑 | 端 | 人日 | 依赖 | 契约编号 |
| --- | --- | --- | --- | --- |
| M2 | 前端 | 1 | spec-09、spec-10 | S15 / C8（palette 仅 llm/http，禁 python） |

## 1. 目标

实现左侧节点面板 `NodePalette.vue`，提供可拖拽 / 点击新增的节点类型（**仅 `llm` 与 `http`**），拖入画布生成新节点（唯一 name + 默认 config）。

## 2. 范围（In / Out）

- **In**：`NodePalette.vue`（HTML5 draggable 项 + 点击新增回退）；新节点 name 自动生成（`llm_1`/`http_2`，去重）；默认 config 骨架（LLM: `llm_type/model_name/system_prompt`；HTTP: `url/method/response_path`）。
- **Out**：节点 config 编辑（spec-12）；画布渲染（spec-10）。

## 3. 接口 / 组件契约

```ts
// NodePalette.vue
props: { readonly?: boolean }
emits: { 'add-node': (payload: { type: 'llm' | 'http'; position: { x: number; y: number } }) => void }
// 拖拽 dataTransfer 携带 type；drop 到画布由 spec-15 设计器换算 position 后 emit add-node
```

- **palette 项白名单**：`[{ type: 'llm', label: 'LLM 节点' }, { type: 'http', label: 'HTTP 节点' }]`——**不含 python**（S15：进程内非沙箱执行 = RCE，禁止经画布创建）。
- 默认 config 骨架与 `docs/workflow-node-development.md` 字段一致（LLM 无 api_key，密钥 env-only H6）。

## 4. TDD · RED（测试先行）

新增 `tests/components/node-palette.spec.ts`：

- [ ] 渲染恰好 2 个可拖项（llm / http）；**断言不存在 python 项**（S15 守卫）。
- [ ] 拖拽项 `dragstart` → `dataTransfer.setData` 写入 type（stub dataTransfer 断言）。
- [ ] `readonly=true` → 拖拽禁用 / 项置灰不可点。
- [ ] 点击项（回退路径）→ emit `add-node({ type, position })`（position 由父层给默认）。
- 纯函数：新节点 name 生成器 `nextNodeName(type, existingNames)` → 唯一、递增、不与现有重名。

## 5. GREEN（最小实现）

- 面板项 `draggable`，`dragstart` 写 type；`nextNodeName` 计数去重；默认 config 常量表。

## 6. REFACTOR

- palette 项定义与默认 config 骨架抽为 `nodeCatalog.ts` 常量（供 spec-12 表单默认值复用），单一真相源。

## 7. 验收门限（DoD Gate）

- [ ] RED 用例全绿；**「无 python 项」断言通过**（S15 硬门）；`npm run type-check` 零错误；`npm test` 通过。
- [ ] 默认 config 字段与 node-development 文档一致；LLM 默认 config **不含 api_key**（H6）。
- [ ] name 生成保证唯一（与 spec-09 validateGraph 重名校验协同）。
- [ ] 提交 `feat(web): add workflow node palette (llm/http only)`。

## 8. 交付物清单

- 新：`agent-web/src/views/workflow/canvas/NodePalette.vue`
- 新：`agent-web/src/views/workflow/canvas/nodeCatalog.ts`（palette 项 + 默认 config + `nextNodeName`）
- 新：`agent-web/tests/components/node-palette.spec.ts`
