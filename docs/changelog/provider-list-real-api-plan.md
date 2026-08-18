# 前端改造计划：ProviderList.vue 对接真实 Provider API

> 状态：**计划（本期不实现代码）**。后端 Provider / Model 体系重构已上线
> （`app/api/v1/providers.py`，`llm-configs` API 已下线）；本文档规划
> `agent-web/src/views/provider/ProviderList.vue` 从本地 mock 切换到真实 API 的改造方案。
> 编码规范遵循 [`agent-web/README.md`](../../agent-web/README.md)，视觉与现有资产页保持一致。

## 1. 现状与差距

| 项 | 现状（mock） | 目标（真实 API） |
|---|---|---|
| 数据源 | 组件内 `providers` ref + `paginateLocal` | `GET /providers/page`（服务端分页） |
| 类型枚举 | `OpenAI \| Claude \| Gemini \| Ollama` | 后端 `OPENAI \| ANTHROPIC \| OLLAMA \| OPENAI_COMPATIBLE` |
| API Key | 明文存 mock 行 | 后端恒返回 `api_key_masked`，表单编辑省略 = 保留原值 |
| 模型清单 | 无 | 行内展开/弹窗列 `GET /providers/{name}/models` |
| 健康状态 | 无 | `health` 快照 tag + 「测试连接」按钮（`POST /providers/{name}/test`） |
| 行字段风格 | camelCase | snake_case（与 assets.ts 既有行类型一致） |

## 2. 对接的 API 契约（后端已实现）

统一信封 `{code, message, data}` 由 `src/utils/request.ts` 拦截器解包；
分页参数别名 `pageSize`（`PageQuery` 契约已支持）。

| 方法 | 端点 | 说明 |
|---|---|---|
| GET | `/providers` | 全量列表（裸数组） |
| GET | `/providers/page?page=&pageSize=&keyword=` | 分页；items 为 `ProviderRowWithMeta`（provider + model_count + health） |
| POST | `/providers` | 201 创建；非 OLLAMA 必须带 `auth_config.api_key` |
| GET | `/providers/{name}` | 详情（恒掩码） |
| PATCH | `/providers/{name}` | 局部更新；`auth_config` 省略 = 保留原值；name 不可改 |
| DELETE | `/providers/{name}` | 软删 + 级联软删 models；`default` 禁删；被资产引用 422 |
| GET | `/providers/{name}/models` | 模型清单（`ModelConfigRead[]`） |
| POST | `/providers/{name}/models` | 201 创建模型 |
| PATCH | `/providers/{name}/models/{model}` | 局部更新模型 |
| DELETE | `/providers/{name}/models/{model}` | 软删模型；`default/default` 禁删；被引用 422 |
| POST | `/providers/{name}/test` | 按需连通性探测，返回 `{status, latency_ms, error_message}`；disabled → 422 |

## 3. 新增 `src/api/provider.ts` 方法签名

```ts
import { get, post, patch, del } from '@/utils/request'
import type { PageQuery, PageResult } from '@/types'

/** 提供商行（对应后端 ProviderRead，api_key 物理剔除仅返回脱敏值） */
export interface ProviderRow {
  id: number
  name: string
  type: 'OPENAI' | 'ANTHROPIC' | 'OLLAMA' | 'OPENAI_COMPATIBLE'
  base_url: string
  api_key_masked: string
  enabled: boolean
  created_by: string | null
  created_at: string | null
  updated_at: string | null
}

/** 健康快照（对应后端 ProviderHealthRead） */
export interface ProviderHealthSnapshot {
  status: 'UP' | 'DOWN' | 'DEGRADED' | 'UNKNOWN'
  last_check_at: string | null
  last_success_at: string | null
  fail_count: number
  latency_ms: number | null
  error_message: string | null
}

/** 分页行（对应后端 ProviderRowWithMeta） */
export interface ProviderRowWithMeta {
  provider: ProviderRow
  model_count: number
  health: ProviderHealthSnapshot
}

/** 模型配置行（对应后端 ModelConfigRead） */
export interface ModelConfigRow {
  id: number
  provider_name: string
  name: string
  model_id: string
  ref: string
  context_size: number | null
  extra_params: Record<string, unknown>
  enabled: boolean
  created_by: string | null
  created_at: string | null
  updated_at: string | null
}

/** 连通性探测结果（对应后端 ConnectionTestResult） */
export interface ConnectionTestResult {
  status: 'UP' | 'DOWN' | 'DEGRADED'
  latency_ms: number | null
  error_message: string | null
}

export interface ProviderCreatePayload {
  name: string
  type: ProviderRow['type']
  base_url?: string
  auth_config?: { api_key?: string }
  enabled?: boolean
}

export interface ModelConfigCreatePayload {
  name: string
  model_id: string
  context_size?: number | null
  extra_params?: Record<string, unknown>
  enabled?: boolean
}

export function listProvidersPage(query: PageQuery): Promise<PageResult<ProviderRowWithMeta>>
export function createProvider(payload: ProviderCreatePayload): Promise<ProviderRow>
export function updateProvider(name: string, payload: Partial<ProviderCreatePayload>): Promise<ProviderRow>
export function deleteProvider(name: string): Promise<null>
export function listProviderModels(name: string): Promise<ModelConfigRow[]>
export function createProviderModel(name: string, payload: ModelConfigCreatePayload): Promise<ModelConfigRow>
export function updateProviderModel(name: string, model: string, payload: Partial<ModelConfigCreatePayload>): Promise<ModelConfigRow>
export function deleteProviderModel(name: string, model: string): Promise<null>
export function testProviderConnection(name: string): Promise<ConnectionTestResult>
```

要点：

- 行类型一律 **snake_case**（与 `assets.ts` 既有约定一致），仅 `PageResult` 的
  `pageSize` 保持驼峰（后端别名）。
- 编辑时 API Key 输入框留空 = PATCH 不带 `auth_config`（保留原值）；填值 = 整体替换。
- 422 错误文案（引用保护、default 禁删、缺 api_key）由 request.ts 统一通知层展示，无需页面重复处理。

## 4. ProviderList.vue 改造点

1. **数据源**：`api(query)` 改为 `listProvidersPage(query)`（服务端分页），删除
   `providers` ref、`paginateLocal` 与模拟延时。
2. **列定义**（`columns`）：
   - `名称`（provider.name）
   - `类型`（type 枚举串映射中文标签：OPENAI→OpenAI、ANTHROPIC→Anthropic、OLLAMA→Ollama、OPENAI_COMPATIBLE→OpenAI 兼容）
   - `Base URL`（base_url，空串显示 `—`）
   - `API Key`（恒显 `api_key_masked`，只读）
   - `模型数`（model_count；点击展开模型清单，见下）
   - `健康状态`（health.status tag）
   - `状态`（enabled 启用/禁用 tag）
   - `操作`（编辑 / 测试连接 / 删除）
3. **健康状态 tag 映射**（Element Plus `el-tag` type）：

   | status | tag type | 文案 |
   |---|---|---|
   | `UP` | `success` | 正常 |
   | `DOWN` | `danger` | 不可用 |
   | `DEGRADED` | `warning` | 缓慢 |
   | `UNKNOWN` | `info` | 未探测 |

   tag 上可挂 tooltip 展示 `latency_ms` / `error_message` / `last_check_at`。
4. **测试连接**：操作列新增「测试连接」按钮 → `testProviderConnection(name)` →
   成功后 `notifySuccess` 展示 `status + latency_ms` 并 `tableRef.refresh()`
   （健康列随之刷新）；disabled 行禁用该按钮（后端 422）。
5. **模型数展开列交互**：
   - 方案：`el-table` 展开行（type="expand"）或 model_count 单元格点击弹出小表格；
   - 展开时按需 `listProviderModels(name)`（懒加载，缓存于该行生命周期）；
   - 模型小表格列：`name`、`model_id`、`ref`、`context_size`、`enabled` tag、操作（编辑/删除，均走 `/providers/{name}/models/*`）；
   - 本期可先只做只读展示 + 模型创建/编辑弹窗作为二期拆分项。
6. **表单弹窗**（WebAgentFormDialog）：
   - 类型选项改后端枚举串；
   - API Key 字段：创建时必填（OLLAMA 可空），编辑时占位提示「留空保持不变」；
   - 提交 payload 组装 `auth_config: { api_key }`（留空则不带该键）；
   - name 字段编辑态禁用（后端 immutable）。
7. **删除**：保持 `useConfirm` 交互；后端软删 + 级联，422 文案（被引用/default 禁删）
   由统一通知层展示。

## 5. 测试改造点（tests/）

- 新增 `tests/provider.spec.ts`（或并入视图测试）：
  - `listProvidersPage` 请求路径与 `pageSize` 别名透传；
  - 健康 tag 映射四态；
  - 编辑提交时 API Key 留空不携带 `auth_config` 键。
- 沿用 happy-dom + vi.mock(`@/utils/request`) 模式（参照 `tests/request.spec.ts`）。

## 6. 认证接入 TODO

`src/utils/request.ts` 的 token 注入目前为 TODO 占位；providers 全端点要求
**会话 token**（`get_current_session`）。本改造落地前必须先完成：

- [ ] 登录/会话流程页面（或至少 token 注入拦截器）
- [ ] 401 统一跳转/提示

在该 TODO 关闭前，ProviderList 对接真实 API 会在所有请求上收到 401。

## 7. 验收清单

- [ ] `npm run type-check` 与 `npm test` 通过
- [ ] 列表分页/keyword 过滤由服务端生效（pageSize 上限 100 越界 422）
- [ ] 任何响应渲染路径不出现明文 api_key（仅 `api_key_masked`）
- [ ] 健康四态 tag 与「测试连接」写回后的刷新可见
- [ ] default provider 与 default/default model 删除被 422 拦截并有可读提示
