/**
 * Provider / Model API 模块：对接后端 provider 资源（POST/GET/PATCH/DELETE
 * /providers、/providers/{name}/models、/providers/{name}/test）。
 *
 * 约定（与 assets.ts 一致）：
 * - 响应信封 {code, message, data} 已由 request.ts 拦截器解包，本模块函数
 *   返回值即 data 载荷；
 * - 全量列表端点（GET /providers）返回裸数组；分页端点（GET /providers/page）
 *   返回 PageResult<ProviderRowWithMeta>（行含 provider + model_count + health）；
 * - 行字段一律 snake_case（与 ProviderRead / ModelConfigRead / ProviderHealthRead
 *   后端 Pydantic schema 对齐），仅 PageResult 的 pageSize 保持驼峰（后端别名）；
 * - 所有端点需登录态（get_current_session），token 注入由 request.ts
 *   拦截器统一处理（当前为 TODO 占位）。
 *
 * 本期 ProviderList 视图尚未切换到真实 API（认证 TODO 未关闭），模块仅就位、
 * 编译期类型对齐，下期切换时只需在 ProviderList 内把 api() 切到 listProvidersPage
 * 即可。
 */
import { del, get, patch, post } from '@/utils/request'
import type { PageQuery, PageResult } from '@/types'

/** 后端 Provider 资源类型枚举（与 SQLModel Provider.type 字段对齐） */
export type ProviderType =
  | 'OPENAI'
  | 'ANTHROPIC'
  | 'OLLAMA'
  | 'OPENAI_COMPATIBLE'

/** 提供商行（对应后端 ProviderRead，auth_config 物理剔除仅返回脱敏值 api_key_masked） */
export interface ProviderRow {
  id: number
  name: string
  type: ProviderType
  base_url: string
  api_key_masked: string
  enabled: boolean
  created_by: string | null
  created_at: string | null
  updated_at: string | null
}

/** 健康快照（对应后端 ProviderHealthRead；缺失时 status 默认 UNKNOWN） */
export interface ProviderHealthSnapshot {
  status: 'UP' | 'DOWN' | 'DEGRADED' | 'UNKNOWN'
  last_check_at: string | null
  last_success_at: string | null
  fail_count: number
  latency_ms: number | null
  error_message: string | null
}

/** 分页行（对应后端 ProviderRowWithMeta = provider + model_count + health） */
export interface ProviderRowWithMeta {
  provider: ProviderRow
  model_count: number
  health: ProviderHealthSnapshot
}

/** 模型配置行（对应后端 ModelConfigRead；ref 形如 "<provider_name>/<model_name>"） */
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

/** 连通性探测结果（对应后端 ConnectionTestResult；不持久化在响应里，由 /test 即时返回） */
export interface ConnectionTestResult {
  status: 'UP' | 'DOWN' | 'DEGRADED'
  latency_ms: number | null
  error_message: string | null
}

/** 创建 Provider 的请求载荷（对应后端 ProviderCreate） */
export interface ProviderCreatePayload {
  name: string
  type: ProviderType
  base_url?: string | null
  /**
   * 非 OLLAMA 必填：含 api_key。编辑时省略该键表示保留原值；
   * 显式传 { api_key: '' } 视为清空（仅 OLLAMA 合法）。
   */
  auth_config?: { api_key?: string } | null
  enabled?: boolean
}

/** 创建 Model 的请求载荷（对应后端 ModelConfigCreate） */
export interface ModelConfigCreatePayload {
  name: string
  model_id: string
  context_size?: number | null
  extra_params?: Record<string, unknown>
  enabled?: boolean
}

/** 把 PageQuery 透传为后端查询参数（page / pageSize / keyword） */
function toParams(query: PageQuery): Record<string, unknown> {
  return { page: query.page, pageSize: query.pageSize, keyword: query.keyword }
}

// ---------------------------------------------------------------------------
// Providers
// ---------------------------------------------------------------------------

/** 全量列表：GET /providers */
export function listProviders(): Promise<ProviderRow[]> {
  return get<ProviderRow[]>('/providers')
}

/** 分页列表：GET /providers/page — 行附 model_count + health */
export function listProvidersPage(
  query: PageQuery = {},
): Promise<PageResult<ProviderRowWithMeta>> {
  return get<PageResult<ProviderRowWithMeta>>('/providers/page', {
    params: toParams(query),
  })
}

/** 详情：GET /providers/{name} */
export function getProvider(name: string): Promise<ProviderRow> {
  return get<ProviderRow>(`/providers/${encodeURIComponent(name)}`)
}

/** 创建：POST /providers — 201，api_key 物理脱敏仅返回 api_key_masked */
export function createProvider(payload: ProviderCreatePayload): Promise<ProviderRow> {
  return post<ProviderRow>('/providers', payload)
}

/** 局部更新：PATCH /providers/{name} — auth_config 省略表示保留原值；name 不可改 */
export function updateProvider(
  name: string,
  payload: Partial<Omit<ProviderCreatePayload, 'name'>>,
): Promise<ProviderRow> {
  return patch<ProviderRow>(`/providers/${encodeURIComponent(name)}`, payload)
}

/** 软删：DELETE /providers/{name} — default 保护 / 被引用时 422 */
export function deleteProvider(name: string): Promise<null> {
  return del<null>(`/providers/${encodeURIComponent(name)}`)
}

/** 按需连通性探测：POST /providers/{name}/test — disabled 时 422 */
export function testProviderConnection(name: string): Promise<ConnectionTestResult> {
  return post<ConnectionTestResult>(
    `/providers/${encodeURIComponent(name)}/test`,
  )
}

/** 上游 /models 条目投影（对应后端 RemoteModelInfo）；ANTHROPIC 端点直接 422 不返回 */
export interface RemoteModelInfo {
  id: string
  owned_by: string | null
  raw: Record<string, unknown>
}

/** 从上游发现模型：POST /providers/{name}/discover-models — ANTHROPIC / 上游失败 422/502 */
export function discoverProviderModels(name: string): Promise<RemoteModelInfo[]> {
  return post<RemoteModelInfo[]>(
    `/providers/${encodeURIComponent(name)}/discover-models`,
  )
}

// ---------------------------------------------------------------------------
// Model configs (nested under providers)
// ---------------------------------------------------------------------------

/** 模型清单：GET /providers/{name}/models */
export function listProviderModels(name: string): Promise<ModelConfigRow[]> {
  return get<ModelConfigRow[]>(
    `/providers/${encodeURIComponent(name)}/models`,
  )
}

/** 创建模型：POST /providers/{name}/models — 201 */
export function createProviderModel(
  name: string,
  payload: ModelConfigCreatePayload,
): Promise<ModelConfigRow> {
  return post<ModelConfigRow>(
    `/providers/${encodeURIComponent(name)}/models`,
    payload,
  )
}

/** 局部更新模型：PATCH /providers/{name}/models/{model} */
export function updateProviderModel(
  name: string,
  model: string,
  payload: Partial<ModelConfigCreatePayload>,
): Promise<ModelConfigRow> {
  return patch<ModelConfigRow>(
    `/providers/${encodeURIComponent(name)}/models/${encodeURIComponent(model)}`,
    payload,
  )
}

/** 软删模型：DELETE /providers/{name}/models/{model} — default/default 保护 / 被引用时 422 */
export function deleteProviderModel(name: string, model: string): Promise<null> {
  return del<null>(
    `/providers/${encodeURIComponent(name)}/models/${encodeURIComponent(model)}`,
  )
}