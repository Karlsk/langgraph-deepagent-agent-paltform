# spec-05 · 前端 API 层 + 类型 + 路由/菜单

| 里程碑 | 端 | 人日 | 依赖 | 契约编号 |
| --- | --- | --- | --- | --- |
| M1 | 前端 | 2 | spec-01..04（后端契约）；`docs/workflow-frontend-spec.md` §7 | request.ts 信封；frontend-spec §6 |

## 1. 目标

建立 workflow 前端的数据访问层（`src/api/workflow.ts` + 类型）与入口（路由 + 侧边栏菜单），为列表 / 执行 / 设计器页面提供统一 API 与导航。

## 2. 范围（In / Out）

- **In**：`src/api/workflow.ts`（类型 + list/get/save/delete/execute 函数）；`src/router/index.ts` 增 `/workflow`、`/workflow/:workflowId/design`、`/workflow/new/design` 懒加载路由；`App.vue`（或布局组件）`el-menu` 增「工作流」项。
- **Out**：页面组件本体（spec-06/07/15）；画布（spec-08+）。本 spec 只铺 API + 路由 + 菜单，页面先放占位。

## 3. 接口 / 组件契约

```ts
// src/api/workflow.ts —— 行字段一律 snake_case（与 provider.ts 一致）
import { get, post, put, del } from '@/utils/request'

export interface WorkflowSummary { workflow_id: string; node_count: number; entry_point: string; description?: string }
export interface StateFieldDTO { type: string; default?: unknown; description?: string; reducer?: 'add' | 'last' | null }
export interface NodeDTO { name: string; type: 'llm' | 'http'; config: Record<string, unknown> }
export interface EdgeDTO { source: string; target: string; condition?: string | null }
export interface UiLayoutDTO { nodes?: Record<string, { x: number; y: number }> }
export interface WorkflowDefinitionDTO {
  workflow_id: string; entry_point: string; nodes: NodeDTO[]; edges: EdgeDTO[]
  state_schema: Record<string, StateFieldDTO>; ui_layout?: UiLayoutDTO
}
export interface ExecutionLogView {
  node_name: string; node_type: string; timestamp: string
  input_data: unknown; output_data: unknown; execution_time_ms: number; error: string | null
}
export interface WorkflowExecuteResult { output: Record<string, unknown>; metadata: Record<string, unknown> & { execution_logs?: ExecutionLogView[] } }

export function listWorkflows(): Promise<WorkflowSummary[]>
export function getWorkflow(id: string, format?: 'json' | 'yaml'): Promise<WorkflowDefinitionDTO | { yaml_text: string }>
export function saveWorkflow(id: string, def: WorkflowDefinitionDTO): Promise<WorkflowDefinitionDTO>   // PUT 全量（spec-16）
export function deleteWorkflow(id: string): Promise<null>
export function executeWorkflow(id: string, input: Record<string, unknown>): Promise<WorkflowExecuteResult>  // timeout 600_000
```

- 路由 `meta`：`requiresAuth: true`；设计器路由 `meta.designer: true`。
- 菜单项图标 / 文案与既有资产页一致（走 design token，不硬编码颜色）。

## 4. TDD · RED（测试先行）

新增 `agent-web/tests/workflow-api.spec.ts`（`vi.mock('@/utils/request')`）：

- [ ] `listWorkflows()` → 调 `get('/workflows')`，返回解包后数组。
- [ ] `getWorkflow(id)` → `get('/workflows/{id}', { params: { format } })`；默认 json。
- [ ] `saveWorkflow(id, def)` → `put('/workflows/{id}', def)`（全量 body）。
- [ ] `deleteWorkflow(id)` → `del('/workflows/{id}')`。
- [ ] `executeWorkflow(id, input)` → `post(.../execute, { input }, { timeout: 600000 })`（超时透传断言）。
- 路由冒烟：`/workflow`、`/workflow/:id/design`、`/workflow/new/design` 均可 resolve 到（懒加载）组件。

## 5. GREEN（最小实现）

- 按契约实现 `workflow.ts` 五函数（execute 单独传 `timeout: 600_000`）。
- 注册路由（占位组件）+ 菜单项。

## 6. REFACTOR

- 类型集中导出，页面组件统一 `import type` 复用；避免各页重复定义 DTO。

## 7. 验收门限（DoD Gate）

- [ ] RED 用例全绿；`npm run type-check` 零错误；`npm test` 通过。
- [ ] 所有请求经 `@/utils/request`（baseURL `/api/v1`，2xx 自动解包），无裸 axios / fetch。
- [ ] 行字段 snake_case；execute 超时 600s 生效。
- [ ] 无 Pinia / SSR 引入（前端红线）；JSON 配置无注释；菜单颜色走 token。
- [ ] 提交 `feat(web): add workflow api layer, types and routing`。

## 8. 交付物清单

- 新：`agent-web/src/api/workflow.ts`
- 改：`agent-web/src/router/index.ts`、布局菜单组件（`App.vue` / `layouts`）
- 新：`agent-web/src/views/workflow/`（占位页，供路由 resolve）
- 新：`agent-web/tests/workflow-api.spec.ts`
