# Workflow 前端开发规格（agent-web · Dify 式画布编排）

| 项 | 值 |
| --- | --- |
| 文档角色 | agent-web 新增 `workflow` 前端模块的开发蓝图：**画布可视化编排**（拖拽节点 / 连线 / 条件分支）→ 保存序列化为 YAML **全量注册**，并支持执行 + 轨迹展示 |
| 依据文件 | `docs/frontend-spec.md`、`docs/frontend-development-guide.md`、`agent-web/README.md`、`docs/workflow-api-and-trace.md`、`app/workflow/{api,registry,models}.py`、`app/workflow/config/examples/*.yaml`、`spec/CONTRACT.md` |
| 涉及编号 | AD-02/AD-10、H4/H6/H7、C6/C8、S6/S7/S9/S13/S15/S16、§7.1（execution_logs 暴露）、CONTRACT §4.10/§4.12/§11 |
| 适用版本 | `app.workflow.__version__ = 0.1.0`；agent-web（Vue 3.5 + TS 5.7 strict + Vite 6 + Element Plus 2.9） |
| 状态 | **规格待开发**——本文定义目标形态与约束，代码由后续任务实现 |

> **核心范式**：对标 Dify 的工作流画布——用户在画布上拖拽节点、连线、配置条件分支；**保存**时把画布图序列化为
> workflow 定义（`WorkflowDefinition` 形状）并**全量注册替换**（不做 partial patch），后端落成 YAML 持久化。
> **全量更新决策依据**：引擎 `register_workflow` 本身即**原子替换**语义（S13：重复注册先 `delete_workflow` 再整体写入），
> 画布产出的是一张完整图，天然对应「整份定义替换」；partial patch 需在两端做图结构合并 / diff，复杂且易错。

---

## 1. 背景与目标

### 1.1 现状

- **前端**：`agent-web/src/views/` 下有 `agent / auth / bundle / chat / mcp / provider / skill` 七个模块，
  **无 `workflow/`**；`router/index.ts` 无 `/workflow` 路由；无任何画布 / 图编辑能力。
- **后端**：workflow 引擎仅暴露 `POST /api/v1/workflows/{workflow_id}/execute`（宿主统一信封 `{code, message, data}`，
  slowapi `workflows_execute` 默认 `20/min`，AD-10）。`WorkflowRegistry` 进程内持有 `list_workflows` /
  `get_workflow_definition` / `register_workflow` / `delete_workflow`，但**均未经 HTTP 暴露**。
- **轨迹**：`RunResult.execution_logs`（逐节点 `ExecutionLog`）已在内核产出；HTTP 暴露走
  `docs/workflow-api-and-trace.md` §7.1 **方案 A**（execute 响应 `metadata` 内嵌 `execution_logs`，**已决策、代码待实现**）。
- **DSL**（`app/workflow/models.py`）：`WorkflowDefinition{workflow_id, entry_point, nodes[], edges[], state_schema{}}`；
  `NodeDefinition{name, type, config}`；`EdgeDefinition{source, target, condition?}`；`StateFieldSchema{type, default, description, reducer}`。

### 1.2 目标（四层能力，递进交付）

1. **列表**：展示已注册 workflow（`workflow_id` + 节点数等摘要）。
2. **画布编排**：Dify 式设计器——节点面板拖拽、画布连线、节点配置面板、条件分支编辑、state_schema 编辑、YAML 预览。
3. **保存 = 全量注册**：画布图 → 结构化定义 → 后端校验 + 原子替换注册 + 落 YAML 持久化。
4. **执行 + 轨迹**：选 workflow → 填 JSON 输入 → 执行 → 展示输出 state 与逐节点 `execution_logs` 轨迹。

### 1.3 定位

- 本文是**前端开发蓝图**：路由、API 层、画布组件、序列化映射、测试、分阶段计划；
- **不改动引擎内核**（`registry.py` / `nodes/*` / `graph_builder.py` / `models.py` 零改动，AD-02 引擎自包含红线不变）；
- 后端需新增的只读 / 写端点属**后端联动项**（§4），前端 spec 只声明其**契约期望**，不实现后端代码。

---

## 2. 范围与分阶段

### 2.1 In / Out

| 范围 | 内容 |
| --- | --- |
| **In** | `/workflow` 路由与侧边栏菜单项；`src/api/workflow.ts`；`views/workflow/*` 列表页 + 画布设计器 + 面板 / 对话框；画布 ↔ DSL 序列化 composable；对应 Vitest 测试 |
| **In（依赖后端）** | 列表 / 查定义 / 全量保存注册 / 删除端点（§4.2）；execute 响应内嵌 `execution_logs`（§7.1 方案 A）；YAML 落盘持久化 |
| **Out** | 引擎内核改动；SSE 流式执行；多 run 历史回溯（`workflow-api-and-trace.md` §7.2 方案 B）；workflow 版本管理 / diff / 协同编辑 |
| **Out** | `python` 节点的 `entry` 模式与任何客户端可控的 `sandboxed` 开关（S18：`entry` 无法沙箱化、`sandboxed` 由后端强制，见 §5.1）；引入 Pinia / SSR / monorepo（前端红线） |

### 2.2 分阶段（建议按此顺序交付，每阶段可独立验收）

| 阶段 | 交付 | 后端依赖 |
| --- | --- | --- |
| **P1 只读 + 执行 + 轨迹** | 列表页 + 执行对话框（JSON 输入）+ 轨迹抽屉 | `GET /workflows`（列表）、execute 内嵌 `execution_logs`（§7.1） |
| **P2 画布设计器（本地）** | Vue Flow 画布 + 节点面板 + 配置面板 + 条件边 + schema 面板 + 画布↔DSL 序列化 + YAML 预览（纯前端，不落库） | `GET /workflows/{id}`（载入既有定义到画布） |
| **P3 保存注册（写路径）** | 全量保存 → 后端校验 + 原子替换 + 落 YAML；构建期校验错误回显；删除 | `PUT /workflows/{id}`（全量注册）、`DELETE /workflows/{id}`、鉴权 + 安全门禁（§5） |

> P2 可在后端写端点就绪前**纯前端**推进（画布 + 序列化 + 本地 YAML 预览），P3 才强依赖写端点与安全门禁。

---

## 3. 关键架构决策（开发前须确认，见 §16）

| # | 决策 | 推荐 | 理由 / 约束 |
| --- | --- | --- | --- |
| D1 | **画布库** | **Vue Flow**（`@vue-flow/core` + `@vue-flow/background` + `@vue-flow/controls` + `@vue-flow/minimap`） | Dify 用 React Flow；Vue 生态对等物即 Vue Flow。**属前端红线「不引入未评估第三方 UI 库」例外，须决策者批准**（Element Plus 无画布能力） |
| D2 | **序列化边界** | 前端只传**结构化 JSON**（=`WorkflowDefinition` 形状）；**后端**用 pydantic 校验 + `yaml.safe_dump` 落 YAML | 单一 YAML 格式真相源在后端；前端免引 `js-yaml`、免手搓 YAML 缩进 / 顺序；校验统一在 backend |
| D3 | **保存语义** | **全量更新**（`PUT /workflows/{id}`，整份定义替换） | 对齐 `register_workflow` 原子替换（S13）；画布产出完整图，无需 diff/merge |
| D4 | **画布布局持久化** | YAML 里加 `ui_layout` 注解键（节点坐标 / 视口），后端 `WorkflowDefinition` 因 `extra="ignore"`（K1）忽略它，但落盘保留以便往返 | DSL 无坐标字段；不持久化则每次打开画布布局丢失。`extra="ignore"` 天然容忍注解键 |
| D5 | **YAML 预览** | 只读预览，由**后端**返回 `yaml_text`（`GET /workflows/{id}?format=yaml` 或保存响应附带）；前端不本地生成 | 与 D2 一致，避免前端 YAML 库；预览仅供参考，非编辑入口 |
| D6 | **条件路由策略** | 画布**不设** per-workflow `no_match_policy`；仅当宿主 registry 配置为 `default` 时，允许为条件源节点指定 `default_edges` 兜底目标 | `no_match_policy` 是 `WorkflowRegistry.__init__` **全局**参数（非 per-workflow），画布无法覆盖；`default_edges` 是 register 时 per-call 参数 |

---

## 4. 后端契约依赖

### 4.1 现状端点

| 方法 | 路径 | 状态 | 说明 |
| --- | --- | --- | --- |
| POST | `/api/v1/workflows/{workflow_id}/execute` | ✅ 已存在 | 宿主信封，`20/min` 限流，AD-10；`execution_logs` 内嵌为 §7.1 待实现 |

### 4.2 需新增端点（前端 spec 声明契约期望，后端实现为联动任务）

所有端点沿用宿主统一信封 `{code, message, data}`、`get_registry(request)` DI、structlog、slowapi 限流；写端点须鉴权（§5.4）。

| 方法 | 路径 | 用途 | 请求 | 成功 `data` | 错误 |
| --- | --- | --- | --- | --- | --- |
| GET | `/api/v1/workflows` | 列表 | — | `[{workflow_id, node_count, description?, entry_point}]`（读 `get_registry_stats` + definitions） | — |
| GET | `/api/v1/workflows/{id}` | 查定义（载入画布） | `?format=json\|yaml`（默认 json） | json：`WorkflowDefinition` 投影 + `ui_layout`；yaml：`{yaml_text}` | 404 未知 id |
| PUT | `/api/v1/workflows/{id}` | **全量保存注册** | `WorkflowDefinition` JSON（含 `ui_layout` 注解、可选 `default_edges`） | 注册后的定义摘要 + `yaml_text` | 400/422 构建期校验失败（S6，附原因）；403 未授权 |
| DELETE | `/api/v1/workflows/{id}` | 删除 | — | `null` | 404 未知 id；403 未授权 |

契约要点：

- **列表**：读 `registry.list_workflows()` + `get_registry_stats()`，不引入新存储（H4 无模块级缓存）。
- **查定义**：`get_workflow_definition(id)` 已返回 `WorkflowDefinition`；投影为 JSON 时剔除运行期字段
  （`execution_history` 不回传，避免响应膨胀），保留 `ui_layout` 注解供画布还原布局。
- **全量保存**：body（JSON dict）→ `parse_definition(body)`（`WorkflowDefinition.model_validate`）→
  `register_workflow(definition, default_edges=body.get("default_edges"))`（S13 原子替换）→ 落 YAML（§4.3）。
  构建期校验（`GraphBuilder.build_graph`）失败 → 422，`message` 携带脱敏原因（H6），前端内联回显（§11.3）。
- **删除**：走 `delete_workflow(id)`（唯一删除入口，C6/H7：四表同步）。

### 4.3 YAML 落盘持久化（后端联动，影响 D2/D3）

现状 `app.state.workflow_registry` 由 `build_registry(DEFAULT_CONFIG_DIR)` 在启动时从
`app/workflow/config/examples` 一次性构建（H4/G7）。**API 注册的定义仅存于内存，重启即丢**。
「保存后变成 YAML 存入」要求持久化，后端须：

- 新增**用户定义目录**（如 `app/workflow/config/user/`，与只读 `examples/` 分离），`PUT` 成功后
  `yaml.safe_dump(definition.model_dump(...))` 写入 `<workflow_id>.yaml`；
- `build_registry` 启动加载须**同时扫描** `examples/` 与 `user/`（`load_definitions_from_dir` 已递归 `*.yaml`，S16 fail-fast）；
- **文件名安全**：`workflow_id` → 文件名须做白名单校验（仅 `[A-Za-z0-9_-]`），杜绝路径穿越；
- `DELETE` 同步删除对应 YAML 文件；
- 落盘引入写权限 / 并发写 / 磁盘失败处理，属后端设计范畴（前端不感知，仅按信封消费结果）。

---

## 5. 安全红线（写路径必读）

在线编辑 YAML 并注册 = **允许远程定义可执行图**，风险面显著。以下为前端 + 后端协同必须落实的门禁：

### 5.1 节点类型白名单（S18 / S22 / C8，2026-09-14 修订）

- **画布 palette 提供 `llm` / `http` / `python` 三类**。`llm`/`http` 是 `NodeType` 枚举内置集（C8）；
  `python` 是 K5 插件类型，自 S22 起有真沙箱执行路径，故可经画布编排。
- **`python` 节点前端约束**：
  - 只暴露 `code` 模式，**绝不提供 `entry` 输入**（`entry` 可加载任意仓库模块、无法沙箱化，后端一律拒绝）；
  - **不发送 `sandboxed` 字段**——它是安全属性而非用户偏好，由后端强制覆写为 `true`（S18 ③）。前端即使发了也会被忽略；
  - 代码输入用普通 `textarea`（等宽字体），**不做前端 `eval` / 预览执行 / 语法高亮插件引入**；
    校验与报错以后端 422 的 `message`（行号 + 规则名）为准，前端只展示、不二次解释；
  - 面板须明示沙箱限制：无 import、无文件/网络、无 `open`/`exec`/`getattr` 等内建、须 `return` dict、
    超时与内存有上限（S22 细则）。
- 后端 `PUT` 须**服务端二次校验** `node.type ∈ {llm, http, python}` 及上述三条注册期条件，拒绝未知类型
  （**不依赖前端约束**：安全边界始终在后端）。

### 5.2 SSRF 与外呼（http 节点）

- `http` 节点 `url` 由用户填写 → **SSRF 风险**。后端应有 host 白名单 / 内网地址拦截（后端联动项，前端仅提示）。
- `mock_enabled`（S9 显式开关）仅演示用途；生产保存时前端应提示关闭 mock。

### 5.3 密钥 env-only（H6 / ADR-008）

- `llm` 节点密钥只经 `api_key_env` / `base_url_env` **引用环境变量名**，**画布绝不提供明文 `api_key` 输入框**；
- 后端 `LLMConfig` 无明文 `api_key` 字段（`extra="forbid"`），前端不得试图传递；轨迹 / 定义回显经 `redact` 脱敏。

### 5.4 鉴权与授权

- 写端点（`PUT` / `DELETE`）**必须** `Depends(get_current_user)` + **管理员角色**校验（现状 execute 端点无鉴权，需后端补齐策略）；
- 前端 `request.ts` 已统一注入用户 token（`getUserToken`），401 走 refresh / 跳登录；前端按钮按角色禁用（非管理员只读）。

### 5.5 条件表达式文法约束（S7）

- 条件边表达式**仅支持** `<node>_result.<path> == '<字面量>'` 或 `<path>` 真值判断，**绝不 eval**；
- 画布**条件边编辑器用结构化控件**（下拉选节点输出字段 + 运算符 `==` / 真值 + 字面量输入），**不提供自由文本表达式框**，
  从源头杜绝注入非法 / 危险表达式；前端生成表达式字符串，后端 `GraphBuilder` 仍按 S7 文法校验。

---

## 6. 路由与导航

| 路径 | 组件 | meta.title | 说明 |
| --- | --- | --- | --- |
| `/workflow` | `views/workflow/WorkflowListView.vue` | 工作流 | 列表页（WebAgentTable） |
| `/workflow/:workflowId/design` | `views/workflow/WorkflowDesignerView.vue` | 工作流编排 | 画布设计器（全屏，Vue Flow） |
| `/workflow/new/design` | 同上（`workflowId='new'`） | 新建工作流 | 空白画布新建 |

- 全部懒加载（`() => import('@/views/workflow/...')`），沿用 `createWebHistory()`。
- 侧边栏菜单：`App.vue` 的 `el-menu` 增「工作流」项（图标取 `@element-plus/icons-vue`），激活态复用现有样式。
- 设计器为**独立全宽路由页**（非对话框）：画布需要大工作区，`.page-view` 骨架下 body 区撑满。

---

## 7. API 模块设计（`src/api/workflow.ts`）

沿用 `provider.ts` / `subagents.ts` 范式：信封已由 `request.ts` 解包，函数返回值即 `data`；行字段 snake_case。

```ts
import { del, get, post, put } from '@/utils/request'

/** 列表行（对应后端 GET /workflows 投影） */
export interface WorkflowSummary {
  workflow_id: string
  node_count: number
  entry_point: string
  description?: string | null
}

/** state 字段 schema（对应 StateFieldSchema） */
export interface StateFieldDTO {
  type: string
  default?: unknown
  description?: string
  reducer?: 'add' | 'last' | null
}

/** 节点（对应 NodeDefinition；type 仅 'llm' | 'http' | 'python'，见 §5.1） */
export interface NodeDTO {
  name: string
  type: string
  config: Record<string, unknown>
}

/** 边（对应 EdgeDefinition；target 为节点名或 'END'） */
export interface EdgeDTO {
  source: string
  target: string
  condition?: string | null
}

/** 画布布局注解（D4：后端 extra=ignore，仅往返保留） */
export interface UiLayoutDTO {
  nodes?: Record<string, { x: number; y: number }>
  viewport?: { x: number; y: number; zoom: number }
}

/** 完整定义（对应 WorkflowDefinition + 注解 + register 参数） */
export interface WorkflowDefinitionDTO {
  workflow_id: string
  entry_point: string
  nodes: NodeDTO[]
  edges: EdgeDTO[]
  state_schema: Record<string, StateFieldDTO>
  ui_layout?: UiLayoutDTO
  /** 条件源兜底（D6：仅当 registry no_match_policy='default' 时有效） */
  default_edges?: Record<string, string>
}

/** 执行轨迹条目（对应 ExecutionLog；§7.1 内嵌于 execute 响应 metadata） */
export interface ExecutionLogView {
  node_name: string
  node_type: string
  timestamp: string
  input_data: Record<string, unknown>
  output_data: Record<string, unknown>
  execution_time_ms: number
  error?: string | null
}

/** execute 响应 data（dict 输出：state 投影 + 折叠 metadata，见 workflow-api-and-trace §2.3/§7.1） */
export interface WorkflowExecuteResult {
  history?: string[]
  metadata: {
    workflow_id: string
    run_id: string
    duration_ms: number
    node_count: number
    execution_logs?: ExecutionLogView[]  // §7.1 方案 A 落地后存在
  }
  [stateChannel: string]: unknown        // 声明通道 + {node}_result 槽位（EXP-G8/S4）
}

export function listWorkflows(): Promise<WorkflowSummary[]> {
  return get<WorkflowSummary[]>('/workflows')
}

export function getWorkflow(id: string): Promise<WorkflowDefinitionDTO> {
  return get<WorkflowDefinitionDTO>(`/workflows/${encodeURIComponent(id)}`)
}

/** 全量保存注册（D3）；构建期校验失败后端 422，message 携脱敏原因 */
export function saveWorkflow(id: string, definition: WorkflowDefinitionDTO): Promise<{ yaml_text: string }> {
  return put<{ yaml_text: string }>(`/workflows/${encodeURIComponent(id)}`, definition)
}

export function deleteWorkflow(id: string): Promise<null> {
  return del<null>(`/workflows/${encodeURIComponent(id)}`)
}

/**
 * 执行：POST /workflows/{id}/execute。
 * 超时放宽（类比 testSubAgent）：真实 LLM/HTTP 节点累计耗时不可预测，默认 15s 不足。
 */
export function executeWorkflow(id: string, input: Record<string, unknown>): Promise<WorkflowExecuteResult> {
  return post<WorkflowExecuteResult>(
    `/workflows/${encodeURIComponent(id)}/execute`,
    input,
    { timeout: 600_000 },
  )
}
```

约定：

- **禁组件内 `new` axios / `fetch`**，统一走 `request.ts`（前端 guide §4）。
- **execute 超时放宽至 600s**（per-request override），沿用 `subagents.ts::testSubAgent` 策略——真实节点耗时不可预测。
- 错误提示由 `request.ts` 拦截器统一 `ElMessage.error(message)`；业务侧只需 catch 后收敛状态。

---

## 8. 画布 ↔ DSL 序列化映射（核心，`useWorkflowGraph.ts`）

画布内部用 Vue Flow 的 `Node[]` / `Edge[]`；保存 / 载入时在 **画布模型 ↔ `WorkflowDefinitionDTO`** 间双向映射。

### 8.1 映射表

| DSL 字段 | 画布来源 | 说明 |
| --- | --- | --- |
| `workflow_id` | 设计器顶部输入 / 路由参数 | 新建时用户填写，须匹配 `[A-Za-z0-9_-]`（§4.3 文件名安全） |
| `entry_point` | 标记为「入口」的节点 | 画布上唯一节点打入口标记（如起始徽标）；无入口 → 前端校验拦截 |
| `nodes[].name` | Vue Flow `node.id` | 唯一性前端校验（对应 `_validate_nodes` 去重） |
| `nodes[].type` | Vue Flow `node.type`（限 `llm`/`http`/`python`） | §5.1 白名单 |
| `nodes[].config` | 节点配置面板表单值 | 按 type 驱动的表单（§10.3） |
| `edges[].source/target` | Vue Flow `edge.source/target` | 终止边 target = 特殊 `END` 端点节点 |
| `edges[].condition` | 条件边编辑器（结构化控件生成） | §5.5 文法约束；无条件 = 普通顺序边 |
| `state_schema` | State Schema 面板 | `type/default/description/reducer` 逐字段编辑 |
| `ui_layout` | Vue Flow 节点坐标 + 视口 | D4 注解，后端忽略、落盘保留 |
| `default_edges` | 条件源节点的「兜底分支」设置 | D6，仅 registry `no_match_policy='default'` 时可用 |

### 8.2 序列化函数（composable 契约）

```ts
// useWorkflowGraph.ts —— 纯函数，无副作用，便于单测
export function graphToDefinition(nodes: VFNode[], edges: VFEdge[], meta: WorkflowMeta): WorkflowDefinitionDTO
export function definitionToGraph(def: WorkflowDefinitionDTO): { nodes: VFNode[]; edges: VFEdge[]; meta: WorkflowMeta }
export function validateGraph(def: WorkflowDefinitionDTO): string[]   // 前端预校验，返回错误列表
```

- **`END` 端点**：画布渲染一个不可删除的 `END` 终止节点；指向它的边 `target='END'`。
- **前端预校验**（`validateGraph`，提交前拦截，减轻后端往返）：至少 1 个节点、节点名唯一、`entry_point` 存在、
  边的 source/target 均指向真实节点或 `END`、条件边文法合法、`llm` 节点密钥仅 env 引用、
  `node.type ∈ {llm, http, python}`，且 `python` 节点须携非空 `code`、**不得含 `entry`**（§5.1）。
  **后端仍独立校验**（前端校验仅 UX，不作为安全边界，S6 构建期校验为最终真相；AST 预检只在后端做）。

---

## 9. 页面与组件设计

```
views/workflow/
├── WorkflowListView.vue              # /workflow 列表页（WebAgentTable + 操作列）
├── WorkflowDesignerView.vue          # /workflow/:id/design 画布设计器（全宽路由页）
├── WorkflowExecuteDialog.vue         # 执行对话框：简单模式（input + state_schema 派生字段）/ 高级模式 JSON
├── WorkflowTraceDrawer.vue           # 轨迹抽屉：逐节点 execution_logs（类比 ChatTraceDrawer）
├── YamlPreviewDrawer.vue             # 只读 YAML 预览（D5，取后端 yaml_text）
├── canvas/
│   ├── nodeCatalog.ts                # 节点类型目录 + DEFAULT_CONFIGS（§5.1 白名单的前端镜像）
│   ├── NodePalette.vue               # 左侧节点面板：llm/http/python 可拖拽项（§5.1 白名单）
│   ├── WorkflowCanvas.vue            # Vue Flow 画布封装（节点/边/END/连线/minimap/controls）
│   ├── WorkflowNode.vue              # 自定义 Vue Flow 节点渲染（图标 + 名称 + 类型 + 入口徽标）
│   ├── EndNode.vue                   # 不可删除的 END 终止节点
│   └── ConditionEdgeDialog.vue       # 条件边编辑（结构化控件生成 S7 文法表达式，§5.5）
└── panel/
    ├── NodeConfigPanel.vue           # 右侧节点配置面板（按 type 驱动表单）
    ├── LlmNodeForm.vue               # llm 配置：provider_ref 分组下拉/temperature/system_prompt（无明文密钥）
    ├── HttpNodeForm.vue              # http 配置：url/method/body_template/response_path/timeout/max_retries/mock
    ├── PythonNodeForm.vue            # python 配置：**仅** code（等宽 textarea）+ 沙箱限制说明；无 entry、无 sandboxed 开关（§5.1）
    ├── StateSchemaPanel.vue          # state_schema 字段增删改（type/default/description/reducer）
    └── ExecuteInputFields.vue        # 简单模式输入字段（按 state_schema 类型映射控件，保留键除外）
```

### 9.1 列表页 `WorkflowListView.vue`

- 复用 **`WebAgentTable`**：columns = `workflow_id` / `node_count` / `entry_point` / 操作；`api` 传 `listWorkflows`。
- 操作列按钮：`设计`（跳 `/workflow/:id/design`）、`执行`（开 `WorkflowExecuteDialog`）、`删除`（`useConfirm` 二次确认 → `deleteWorkflow`）。
- 顶部 `app-btn` 主按钮「新建工作流」→ 跳 `/workflow/new/design`。
- 数据加载用 `useRequest`（三态收敛，失败保留旧 data）；操作用 `notify` 反馈（禁直调 `ElMessage`）。

### 9.2 设计器 `WorkflowDesignerView.vue`

布局：左 `NodePalette` | 中 `WorkflowCanvas` | 右 `NodeConfigPanel` / `StateSchemaPanel`（Tab 切换）；顶部工具栏。

- **工具栏**：`workflow_id` 输入、`保存`（`saveWorkflow` 全量更新）、`执行`、`YAML 预览`、`校验`（跑 `validateGraph`）、`返回`。
- **拖拽新增**：从 `NodePalette` 拖入画布生成节点（默认名 `<type>_<n>`，可改）。
- **连线**：节点间拖拽连线生成边；选中边可设条件（开 `ConditionEdgeDialog`）。
- **选中节点** → 右侧 `NodeConfigPanel` 按 type 渲染 `LlmNodeForm` / `HttpNodeForm`。
- **保存流程**：`graphToDefinition` → `validateGraph`（前端拦截）→ `saveWorkflow` → 成功 `notify.success`；
  422 构建期校验失败 → 内联回显后端原因（§11.3）。
- 未保存变更离开路由 → `useConfirm` 拦截确认。

### 9.3 节点配置面板（按 type 驱动，对齐节点开发规范）

| type | 表单字段 | 约束 |
| --- | --- | --- |
| `llm` | `llm_type`(openai/anthropic)、`model_name`、`temperature`(0.0-2.0)、`system_prompt`、`api_key_env`、`base_url_env`、`max_retries` | **无明文 api_key**（§5.3）；密钥仅 env 名引用 |
| `http` | `url`、`method`、`body_template`、`response_path`、`timeout`、`max_retries`、`retry_on_status`、`mock_enabled`、`mock_responses` | mock key 格式 `"{METHOD} {url}"`（S9）；生产提示关 mock |

### 9.4 执行 + 轨迹（P1）

- `WorkflowExecuteDialog`：`el-input type=textarea` 填 JSON 输入（附三示例预设：`demo_minimal`/`demo_http`/`condition_branch_demo`）；
  JSON 解析前端校验；`执行` → `executeWorkflow` → 展示 `data`（state 投影 + metadata）。
- `WorkflowTraceDrawer`：读 `result.metadata.execution_logs`（§7.1），按 timestamp 顺序渲染每节点
  `node_name` / `node_type` / `execution_time_ms` / `input_data` / `output_data` / `error`（`error` 非空标红）。
  **`execution_logs` 缺失时**（§7.1 未落地阶段）优雅降级：提示「轨迹待后端暴露」，不报错。
- 复用 `ChatTraceDrawer` / `SubAgentTraceDetailDialog` 的折叠展示范式。

---

## 10. 交互流程

### 10.1 保存（全量注册）

```
设计器编辑 → [保存] → graphToDefinition() → validateGraph()
  ├─ 前端校验失败 → notify.error + 高亮问题节点/边（不发请求）
  └─ 通过 → saveWorkflow(id, definition)  (PUT，全量)
        ├─ 200 → notify.success + 更新 YAML 预览（后端返回 yaml_text）
        ├─ 422 → 构建期校验失败（S6）：内联回显 message（脱敏原因），定位到问题节点/边
        ├─ 403 → 未授权：notify.error（非管理员）
        └─ 其它 → request.ts 拦截器统一 ElMessage.error
```

### 10.2 执行 + 轨迹

```
[执行] → WorkflowExecuteDialog → 填 JSON → executeWorkflow(id, input)  (超时 600s)
  ├─ 200 → 展示 data.state 投影 + metadata；有 execution_logs 则 WorkflowTraceDrawer 可展开逐节点轨迹
  ├─ 404 → 未知 workflow（notify.error）
  └─ 500 → 执行失败（message 为脱敏摘要，data=null）；轨迹缺失降级提示
```

### 10.3 载入既有定义到画布

```
进入 /workflow/:id/design → getWorkflow(id) → definitionToGraph() → 渲染画布（含 ui_layout 还原布局）
```

---

## 11. 设计规范遵循

- **Design Tokens**：颜色一律用 `:root` CSS 变量 / `--el-*`，**禁硬编码色值**；画布节点 / 边配色扩展 token，不写死 hex。
- **布局**：列表页用 `.page-view` + `.content-card`；设计器为全宽工作区（画布撑满 body）。
- **命名**：组件 `WebAgent` 前缀**不强制**（画布组件按业务域命名，如 `WorkflowCanvas`）；基础库组件（Table/Dialog）仍复用 `WebAgent*`。文件 PascalCase，composable `useXxx`。
- **样式**：`<style scoped>` + `:deep()` 穿透 Vue Flow 内部类；共享样式提升到 `styles/index.css`。
- **依赖方向**：`views → components/composables → utils/types` 单向；Vue Flow 仅在 `components/WorkflowCanvas.vue` 及自定义节点内引用，不散落。
- **文案**：UI 中文，标识符英文。

---

## 12. 测试规范（Vitest + happy-dom）

- **Element Plus 组件一律 stub**，不真实渲染（前端 guide §6）；**零真实网络 / 零真实 LLM**（`vi.mock('@/api/workflow')`）。
- **Vue Flow 测试策略**：画布渲染依赖 DOM 测量，happy-dom 下**不真实渲染 Vue Flow 画布**；
  重点测**纯逻辑**——`useWorkflowGraph` 的 `graphToDefinition` / `definitionToGraph` / `validateGraph`（纯函数，无需 DOM）。
  设计器组件测试 stub 掉 `WorkflowCanvas`，只验证工具栏交互 / 保存调用 / 校验拦截。
- **fake timers** 覆盖执行异步时序（600s 超时、loading 态）。
- **覆盖点**：
  1. 列表页挂载渲染 + `listWorkflows` 调用 + 空态；
  2. 画布 ↔ DSL 序列化往返一致（`definitionToGraph(graphToDefinition(x)) == x`）；
  3. `validateGraph` 各失败分支（无节点 / 重名 / 缺入口 / 悬空边 / 非法条件 / 类型不在 `{llm,http,python}` /
     `python` 缺 `code` / `python` 含 `entry` / 明文密钥）；
  4. 保存：前端校验拦截不发请求；通过则 `saveWorkflow` 全量提交；422 回显；
  5. 执行：JSON 输入解析、成功展示、`execution_logs` 轨迹渲染、缺失降级、404/500 分支。
- 范例参照 `tests/components/provider-list.spec.ts` 与 `tests/design-tokens.spec.ts`。

---

## 13. 分阶段实施计划（TDD，RED → GREEN → REFACTOR）

| 阶段 | 任务 | 交付物 |
| --- | --- | --- |
| **P1** | `api/workflow.ts`（list/execute 类型 + 函数）；`WorkflowListView`；`WorkflowExecuteDialog`；`WorkflowTraceDrawer`；路由 + 菜单 | 列表 + 执行 + 轨迹（依赖后端 `GET /workflows` + §7.1） |
| **P2** | 引入 Vue Flow（D1 批准后）；`WorkflowCanvas`/`WorkflowNode`/`NodePalette`；`NodeConfigPanel` + `Llm/HttpNodeForm`；`StateSchemaPanel`；`ConditionEdgeDialog`；`useWorkflowGraph` 序列化；`getWorkflow` 载入 | 画布设计器（本地编排 + 序列化 + 载入），可无写端点独立验收 |
| **P3** | `saveWorkflow`（PUT 全量）+ 构建期校验回显；`deleteWorkflow`；`YamlPreviewDrawer`；鉴权角色门禁；未保存离开拦截 | 保存注册写路径（依赖后端 `PUT`/`DELETE` + 落盘 + 安全门禁） |

每阶段：先写失败测试（RED）→ 最小实现（GREEN）→ 重构（REFACTOR），禁真实网络 / LLM（前端 test 规范）。

---

## 14. 验收标准

- `/workflow` 列表可加载并展示已注册 workflow；执行 `demo_http`（mock，零网络）返回输出并可展开逐节点轨迹。
- 画布可拖拽 `llm`/`http` 节点、连线、设条件分支、编辑 state_schema；`graphToDefinition` 产出的定义可被后端
  `parse_definition` 校验通过（与 `condition_branch.yaml` 等示例结构一致）。
- 保存走**全量更新**（PUT），后端原子替换 + 落 YAML；重新载入画布布局（`ui_layout`）与定义一致。
- 构建期校验失败（如悬空边 / 缺入口 / 非法条件）→ 422 原因内联回显，定位到问题元素。
- 画布可拖拽 `python` 节点，面板只暴露 `code`（无 `entry` 输入、无 `sandboxed` 开关）；提交体不含 `entry`/`sandboxed`
  两键（§5.1）；后端对 `entry` 模式与 AST 非法代码仍 422，且 `message` 含规则名不含代码正文；
  `llm` 节点无明文密钥输入（§5.3）；写端点非管理员 403。
- 全部新增前端测试通过；`npm run type-check` 零错误；不违反前端红线（无 Pinia/SSR，JSON 配置无注释）。

---

## 15. 未决问题（开发前须拍板，见 §3 决策表）

1. **D1 画布库**：是否批准引入 **Vue Flow**（前端红线例外）？若否决，需另评方案（如自研 SVG 画布，成本高）。
2. **后端联动排期**：`GET /workflows`、`GET/PUT/DELETE /workflows/{id}`、YAML 落盘、execute 内嵌 `execution_logs`（§7.1）
   是否本期与前端同步建？前端 P2 可先行，P1/P3 强依赖后端。
3. **鉴权角色**：写端点的「管理员」角色如何界定（现状 execute 无鉴权）？前端按何字段判定只读 / 可写？
4. **YAML 落盘目录与并发**：`app/workflow/config/user/` 的写权限、并发保存同一 id、磁盘失败回滚策略（后端设计）。
5. **`ui_layout` 注解键**：是否接受在 YAML 里保留 `ui_layout`（`extra="ignore"` 容忍）用于画布布局往返（D4）？
   若不接受，则每次打开画布自动布局（丢失手工排版）。
6. **`default_edges` / `no_match_policy`**：确认画布不设 per-workflow `no_match_policy`（引擎全局约束，D6）；
   是否需要宿主把 registry 默认配为 `no_match_policy='default'` 以支持画布兜底分支？

---

## 16. 相关文档

- 前端骨架规范（技术栈 / 代理 / 布局）：[`docs/frontend-spec.md`](frontend-spec.md)
- 前端开发规范（五大组件 / 请求层 / 测试）：[`docs/frontend-development-guide.md`](frontend-development-guide.md)
- 后端 API 与 Trace（execute 信封 / execution_logs §7.1 方案 A）：[`docs/workflow-api-and-trace.md`](workflow-api-and-trace.md)
- Node 开发规范（LLM/HTTP 节点 config 字段）：[`docs/workflow-node-development.md`](workflow-node-development.md)
- 手动测试指南（三示例 workflow 输入）：[`docs/workflow-manual-testing.md`](workflow-manual-testing.md)
- 编码契约（§4.10 registry / §4.12 信封 / §11 变更流程）：[`spec/CONTRACT.md`](workflow-reimpl-plan/spec/CONTRACT.md)
