# spec-14 · ConditionEdgeDialog（S7 结构化条件文法）

| 里程碑 | 端 | 人日 | 依赖 | 契约编号 |
| --- | --- | --- | --- | --- |
| M2 | 前端 | 2 | spec-09（S7 解析器）、spec-10 | **S7**（条件仅 `path == '字面量'` / 真值，绝不 eval）；D6（no_match_policy 全局，画布不设） |

## 1. 目标

实现 `ConditionEdgeDialog.vue`，用**结构化控件**（而非自由文本）编辑边的 `condition`，只生成符合 S7 文法的条件串，从源头杜绝非法表达式与注入。

## 2. 范围（In / Out）

- **In**：边的目标选择（现有节点 + `END`）；条件类型三选（无条件 / 等值 `path == 'literal'` / 真值 `path`）；path 选择器（从 state_schema 通道 + 上游节点 `{node}_result` 推导）；literal 输入；生成 condition 串并用 spec-09 解析器校验。
- **Out**：`no_match_policy` / `default_edges` 编辑（**D6：全局/注册期参数，画布不提供**）；后端二次校验（spec-16）。

## 3. 接口 / 组件契约

```ts
// ConditionEdgeDialog.vue
props: {
  modelValue: boolean
  edge: { source: string; target: string; condition?: string | null }
  nodeNames: string[]          // 供 target 选择
  stateChannels: string[]      // state_schema 键 + 上游 {node}_result，供 path 选择
  readonly?: boolean
}
emits: { 'update:modelValue': (v: boolean) => void; 'confirm': (e: { target: string; condition: string | null }) => void }
```

**条件类型 → 生成串**：

| 类型 | 控件 | 生成的 condition |
| --- | --- | --- |
| 无条件 | 仅选 target | `null` |
| 等值 | path + literal | `"<path> == '<literal>'"` |
| 真值 | path | `"<path>"` |

- **S7 硬约束**：literal 单引号包裹并转义；path 限定为合法点路径（`[A-Za-z_][A-Za-z0-9_]*(\.[A-Za-z_][A-Za-z0-9_]*)*`）；**任何情况不拼接 eval / 函数**。
- 顶部提示：「条件边默认 `no_match_policy='raise'`——所有条件均不命中会运行期报错；如需兜底分支，请保证条件穷尽或由管理员在宿主配置（画布不提供）」。

## 4. TDD · RED（测试先行）

新增 `tests/components/condition-edge-dialog.spec.ts`（stub Element Plus）：

- [ ] 类型=无条件 → confirm 发 `{ target, condition: null }`。
- [ ] 类型=等值，path=`check_result.response`、literal=`OK` → condition == `"check_result.response == 'OK'"`。
- [ ] 类型=真值 → condition == `"<path>"`。
- [ ] literal 含单引号 → 正确转义，生成的串仍被 spec-09 解析器判为**合法**。
- [ ] path 非法（空格 / 运算符 / 数字开头）→ 内联错误，confirm 禁用。
- [ ] 任意输入组合下，生成串经 S7 解析器校验通过（property 断言：绝不产非法串）。
- [ ] `readonly=true` → 控件禁用、confirm 隐藏。

## 5. GREEN（最小实现）

- 三类型 radio + 条件字段；path 用 `el-select`（可搜索，选项来自 stateChannels）；literal `el-input`；confirm 前调 spec-09 解析器。

## 6. REFACTOR

- 条件生成 / 解析统一走 spec-09 抽出的 S7 解析器（单一真相源），对话框只负责 UI 编排；path 选项推导抽 util（复用于 spec-15）。

## 7. 验收门限（DoD Gate）

- [ ] RED 用例全绿（含转义 / 非法 path / property 断言）；`npm run type-check` 零错误；`npm test` 通过。
- [ ] grep 确认无 `eval` / `new Function`（S7 红线）；生成串 100% 经解析器校验。
- [ ] 画布**不出现** no_match_policy / default_edges 编辑控件（D6 守卫断言）。
- [ ] 提交 `feat(web): add condition edge dialog with S7-safe grammar`。

## 8. 交付物清单

- 新：`agent-web/src/views/workflow/canvas/ConditionEdgeDialog.vue`
- 新：`agent-web/tests/components/condition-edge-dialog.spec.ts`
