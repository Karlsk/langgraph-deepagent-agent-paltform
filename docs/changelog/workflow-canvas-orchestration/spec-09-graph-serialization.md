# spec-09 · useWorkflowGraph 画布 ↔ DSL 序列化 + 前端预校验

| 里程碑 | 端 | 人日 | 依赖 | 契约编号 |
| --- | --- | --- | --- | --- |
| M2 | 前端 | 2 | spec-05（DTO 类型）、spec-08 | S6（构建期校验前移）；S7（条件文法）；D3/D4；models.py 结构 |

## 1. 目标

实现纯函数序列化 composable `useWorkflowGraph`，在 Vue Flow 图（nodes/edges）与后端 `WorkflowDefinitionDTO` 之间**双向无损映射**，并提供**前端预校验**（把后端 S6 构建期校验前移，给用户即时反馈；后端仍是最终权威）。

## 2. 范围（In / Out）

- **In**：`graphToDefinition` / `definitionToGraph` / `validateGraph` 三个**纯函数**；`ui_layout` 读写（D4）；节点类型白名单（仅 `llm`/`http`）；条件文法校验（S7）。
- **Out**：画布渲染（spec-10）；保存请求（spec-16/21）。**不引 js-yaml**（D2：前端只产 JSON，YAML 由后端生成）。

## 3. 接口契约（冻结）

```ts
// src/composables/useWorkflowGraph.ts
import type { Node, Edge } from '@vue-flow/core'
import type { WorkflowDefinitionDTO, NodeDTO, EdgeDTO } from '@/api/workflow'

export interface GraphValidationError { field: string; message: string; nodeId?: string; edgeId?: string }

export function definitionToGraph(def: WorkflowDefinitionDTO): { nodes: Node[]; edges: Edge[] }
export function graphToDefinition(
  meta: { workflow_id: string; entry_point: string; state_schema: WorkflowDefinitionDTO['state_schema'] },
  nodes: Node[], edges: Edge[],
): WorkflowDefinitionDTO
export function validateGraph(def: WorkflowDefinitionDTO): GraphValidationError[]
```

**映射规则**：

| DSL | Vue Flow | 说明 |
| --- | --- | --- |
| `NodeDefinition{name,type,config}` | `Node{id:name, type:'workflow', position, data:{name,type,config}}` | 节点 `id` == DSL `name`（唯一） |
| `EdgeDefinition{source,target,condition}` | `Edge{source, target, data:{condition}}` | `target` 可为 `"END"`（特殊终止节点） |
| `ui_layout.nodes[name]={x,y}` | `Node.position` | 布局注解；载入还原、保存回写（D4） |
| `entry_point` / `state_schema` | 画布外层 meta | 不落在单个节点上 |

**validateGraph 校验项（镜像后端 S6/S7，前端预校验）**：

1. 至少 1 个节点；节点 `name` 唯一且非空。
2. `entry_point` 必须是存在的节点 name。
3. 每条边 `source` 为存在节点；`target` 为存在节点或 `"END"`（否则悬空边 → 错误）。
4. 节点 `type ∈ {llm, http}`（`python` → 错误，S15）。
5. 边 `condition`（若有）符合 S7 文法：`<path> == '<literal>'` 或纯真值路径；否则错误。

## 4. TDD · RED（测试先行）

新增 `tests/composables/use-workflow-graph.spec.ts`（纯函数，无需 DOM）：

- **往返**：
  - [ ] `definitionToGraph(graphToDefinition(meta, nodes, edges))` 结构等价（含 ui_layout position 还原）。
  - [ ] 用 `condition_branch.yaml` 等价 DTO 做 fixture，往返后 nodes/edges/condition 不丢字段。
- **映射**：
  - [ ] `ui_layout.nodes` 缺失时给默认 position（不崩）；存在时精确还原。
  - [ ] `target: "END"` 正确映射为指向 END 节点的边。
- **validateGraph**：
  - [ ] 合法图 → `[]`（无错误）。
  - [ ] 空节点 / 重名 / entry_point 不存在 / 悬空边 / type=python / 非法 condition → 各返回对应 `field` + `message`。
  - [ ] 条件文法边界：`a.b == 'OK'` 合法；`a.b == ` 非法；`a or b`（非 S7）非法。

## 5. GREEN（最小实现）

- 实现三纯函数；condition 校验用**正则/解析**判定 S7 两类形态，**绝不 eval / new Function**。
- position 默认布局：按拓扑序或网格排布（无 ui_layout 时兜底）。

## 6. REFACTOR

- 校验规则拆为独立小函数（`validateNodes`/`validateEdges`/`validateCondition`），错误信息统一 i18n key；抽取 S7 条件解析器供 spec-14 复用。

## 7. 验收门限（DoD Gate）

- [ ] RED 用例全绿（往返无损 + 6 类校验分支）；`npm run type-check` 零错误；`npm test` 通过。
- [ ] grep 确认无 `eval` / `new Function`（S7 红线）；无 `js-yaml` / `yaml` 依赖引入（D2）。
- [ ] 映射字段与后端 `models.py`（NodeDefinition/EdgeDefinition/StateFieldSchema）一致；ui_layout 走 `extra="ignore"` 容忍键（D4）。
- [ ] 纯函数无副作用（不依赖 Vue 响应式即可单测）。
- [ ] 提交 `feat(web): add workflow graph serialization and validation composable`。

## 8. 交付物清单

- 新：`agent-web/src/composables/useWorkflowGraph.ts`（+ S7 条件解析 util）
- 新：`agent-web/tests/composables/use-workflow-graph.spec.ts`
- 新：`agent-web/tests/fixtures/workflow-definitions.ts`（condition_branch / http_demo 等价 DTO）
