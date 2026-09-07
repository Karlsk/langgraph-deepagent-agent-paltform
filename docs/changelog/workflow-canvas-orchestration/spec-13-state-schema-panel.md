# spec-13 · StateSchemaPanel（state_schema 编辑）

| 里程碑 | 端 | 人日 | 依赖 | 契约编号 |
| --- | --- | --- | --- | --- |
| M2 | 前端 | 1 | spec-09、spec-15（设计器承载） | models.py StateFieldSchema；EXP-G8 输出投影 |

## 1. 目标

实现 `StateSchemaPanel.vue`，编辑 workflow 级 `state_schema`（字段名 + 类型 + default + description + reducer），因为 `state_schema` 是画布外层 meta（不落在单个节点），且直接决定 EXP-G8 输出投影可见通道。

## 2. 范围（In / Out）

- **In**：字段增 / 删 / 改；`type` 下拉（str/int/float/bool/list/dict 等 StateFieldSchema 允许值）；`reducer` 下拉（`add`/`last`/null）；`default` / `description` 编辑；字段名唯一 + 合法标识符校验。
- **Out**：节点 config（spec-12）；entry_point 选择（spec-15 工具栏）。

## 3. 接口 / 组件契约

```ts
// StateSchemaPanel.vue
props: { modelValue: Record<string, StateFieldDTO>; readonly?: boolean }
emits: { 'update:modelValue': (schema: Record<string, StateFieldDTO>) => void }
```

- 行结构：`{ 字段名, type, default?, description?, reducer? }`，对齐 `StateFieldSchema`。
- 输出提示：面板顶部说明「仅此处声明的通道 + history + 执行过节点的 `{node}_result` 会出现在 execute output（EXP-G8）」。

## 4. TDD · RED（测试先行）

新增 `tests/components/state-schema-panel.spec.ts`（stub Element Plus）：

- [ ] 传入 schema → 渲染对应行数 + 字段值。
- [ ] 新增字段 → emit 含新键的 schema（默认 type=str）。
- [ ] 删除字段 → emit 去除该键的 schema。
- [ ] 改 type / reducer → emit 更新；reducer 仅 `add`/`last`/null 可选。
- [ ] 字段名重复 / 非法标识符（空格、数字开头）→ 内联校验错误，不发无效 emit。
- [ ] `readonly=true` → 全控件禁用、增删按钮隐藏。

## 5. GREEN（最小实现）

- `el-table` 可编辑行 或 卡片列表 + 表单控件；本地草稿 → 校验通过后 emit 全量 schema（不可变）。

## 6. REFACTOR

- 校验（唯一名 / 标识符）复用 util；type / reducer 选项抽常量，与后端 StateFieldSchema 对齐。

## 7. 验收门限（DoD Gate）

- [ ] RED 用例全绿；`npm run type-check` 零错误；`npm test` 通过。
- [ ] 字段结构与 `StateFieldSchema`（models.py）一致；reducer 值域受限（`add`/`last`/null）。
- [ ] `readonly` 门禁生效；无硬编码颜色。
- [ ] 提交 `feat(web): add workflow state schema panel`。

## 8. 交付物清单

- 新：`agent-web/src/views/workflow/panel/StateSchemaPanel.vue`
- 新：`agent-web/tests/components/state-schema-panel.spec.ts`
