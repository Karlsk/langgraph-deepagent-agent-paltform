# spec-12 · NodeConfigPanel + LlmNodeForm + HttpNodeForm

| 里程碑 | 端 | 人日 | 依赖 | 契约编号 |
| --- | --- | --- | --- | --- |
| M2 | 前端 | 2 | spec-10、spec-11（nodeCatalog 默认值） | H6（密钥 env-only）；node-development 字段 |

## 1. 目标

实现右侧节点配置面板 `NodeConfigPanel.vue`（按选中节点 `type` 切换表单），含 `LlmNodeForm` 与 `HttpNodeForm`，编辑节点 `config`，双向绑定回画布节点 `data.config`。

## 2. 范围（In / Out）

- **In**：`NodeConfigPanel.vue`（type 驱动 + 节点重命名 + 删除节点）；`LlmNodeForm.vue`（`llm_type` / `model_name` / `temperature` / `system_prompt`）；`HttpNodeForm.vue`（`url` / `method` / `body_template` / `response_path` / `mock_enabled` / `mock_responses`）。
- **Out**：state_schema 编辑（spec-13）；条件边（spec-14）；SSRF 服务端校验（spec-20，前端仅提示）。

## 3. 接口 / 组件契约

```ts
// NodeConfigPanel.vue
props: { node: Node | null; readonly?: boolean }
emits: { 'update:node': (patch: { name?: string; config?: Record<string, unknown> }) => void; 'remove-node': (id: string) => void }
```

**字段（严格对齐示例 YAML，见 `config/examples/`）**：

| 表单 | 字段 | 控件 | 约束 |
| --- | --- | --- | --- |
| LLM | `llm_type` | select（openai/...） | 必填 |
| LLM | `model_name` | input | 必填（如 gpt-4o-mini） |
| LLM | `temperature` | number(0-2) | 选填 |
| LLM | `system_prompt` | textarea | 选填 |
| HTTP | `url` | input | 必填；前端提示「服务端将做 SSRF 白名单校验」（spec-20） |
| HTTP | `method` | select(GET/POST/PUT/PATCH/DELETE) | 必填 |
| HTTP | `body_template` | textarea | 支持 `{input}` 占位 |
| HTTP | `response_path` | input | 选填 |
| HTTP | `mock_enabled` | switch | 演示用途（S9 显式开关，零网络） |
| HTTP | `mock_responses` | key-value 编辑器 | 键 `"{method} {url}"` |

> **H6 红线**：LLM 表单**绝不提供 api_key 字段**——密钥 env-only，config 中不承载任何密钥。

## 4. TDD · RED（测试先行）

新增 `tests/components/node-config-panel.spec.ts`（stub Element Plus）：

- [ ] 选中 `type='llm'` 节点 → 渲染 LlmNodeForm；`type='http'` → HttpNodeForm；`null` → 空态。
- [ ] 改 `model_name` → emit `update:node({ config })` 含新值（不修改原对象，immutable patch）。
- [ ] LLM 表单**断言不存在 api_key 输入**（H6 守卫）。
- [ ] HTTP `mock_enabled` 关闭 → `mock_responses` 编辑器禁用/隐藏。
- [ ] 重命名节点 → emit `update:node({ name })`；空 name / 与兄弟重名 → 内联校验错误（配合 spec-09）。
- [ ] `readonly=true` → 所有控件禁用、删除按钮隐藏。
- [ ] `body_template` 含非法占位 → 提示（非阻断，后端权威）。

## 5. GREEN（最小实现）

- `WebAgentFormDialog` 风格的表单区（内嵌面板而非弹窗）；字段 → config 键映射；`update:node` 发不可变 patch。

## 6. REFACTOR

- 字段定义抽为 schema 驱动（`llmFields` / `httpFields` 配置数组 → 通用渲染），减少重复模板；默认值取自 spec-11 `nodeCatalog`。

## 7. 验收门限（DoD Gate）

- [ ] RED 用例全绿；**「LLM 无 api_key」断言通过**（H6 硬门）；`npm run type-check` 零错误；`npm test` 通过。
- [ ] 字段集与 `config/examples/*.yaml` 完全对应（无多字段 / 无漏字段）；immutable patch（不直接改 props）。
- [ ] `readonly` 门禁生效（spec-19 复用）；无硬编码颜色。
- [ ] 提交 `feat(web): add workflow node config panel and llm/http forms`。

## 8. 交付物清单

- 新：`agent-web/src/views/workflow/panel/NodeConfigPanel.vue`
- 新：`agent-web/src/views/workflow/panel/LlmNodeForm.vue`、`HttpNodeForm.vue`
- 新：`agent-web/tests/components/node-config-panel.spec.ts`
