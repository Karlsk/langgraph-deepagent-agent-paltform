/**
 * Agent 资产 API 模块：对接后端 3 大资产模块（Skills / AgentApps /
 * LLM Configs）。MCP Servers 已迁移至 `@/api/mcp`，SubAgents 已迁移至
 * `@/api/subagents`（两者均含 CRUD + 调试/测试端点，独立承载更清晰）。
 *
 * 约定：
 * - 响应信封 {code, message, data} 已由 request.ts 拦截器解包，本模块函数
 *   返回值即 data 载荷；
 * - 全量列表端点（GET /<module>）返回裸数组；分页端点（GET /<module>/page）
 *   返回 PageResult<T>（后端字段 pageSize 为驼峰，其余行字段为 snake_case）；
 * - 所有端点需登录态（get_current_session），token 注入由 request.ts
 *   拦截器统一处理（当前为 TODO 占位）。
 */
import { del, get, patch, post } from '@/utils/request'
import type { PageQuery, PageResult } from '@/types'

/** Skill 资产元数据行（对应后端 SkillRead） */
export interface SkillRow {
  name: string
  description: string
  content_hash: string
  version: number
  created_by: string | null
}

/** AgentApp 资产行（对应后端 AgentAppRead） */
export interface AgentAppRow {
  id: number
  name: string
  system_prompt: string
  allowed_tools: string[] | null
  model: string | null
  skill_names: string[]
  subagent_names: string[]
  interrupt_on: Record<string, unknown> | null
  engine: string
  status: 'draft' | 'published'
  published_hash: string | null
  version: number
  created_by: string | null
}

/** LLM 配置行（对应后端 LlmConfigRead，api_key 物理剔除仅返回脱敏值） */
export interface LlmConfigRow {
  name: string
  model_name: string
  api_key_masked: string
  base_url: string | null
  temperature: number | null
  max_tokens: number | null
  enabled: boolean
  description: string
  content_hash: string
  created_by: string | null
}

/** 把 PageQuery 透传为后端查询参数（page/pageSize/keyword） */
function toParams(query: PageQuery): Record<string, unknown> {
  return { page: query.page, pageSize: query.pageSize, keyword: query.keyword }
}

// ---------------------------------------------------------------------------
// Skills
// ---------------------------------------------------------------------------

/** 全量列表：GET /skills */
export function listSkills(): Promise<SkillRow[]> {
  return get<SkillRow[]>('/skills')
}

/** 分页列表：GET /skills/page */
export function listSkillsPage(query: PageQuery = {}): Promise<PageResult<SkillRow>> {
  return get<PageResult<SkillRow>>('/skills/page', { params: toParams(query) })
}

/** Skill 创建 payload（POST /skills） */
export interface SkillCreatePayload {
  name: string
  description: string
  body: string
}

/** Skill 部分更新 payload（PATCH /skills/{name}，name 不可改） */
export interface SkillPatchPayload {
  description?: string
  body?: string
}

/** Skill 正文读取契约（GET /skills/{name}/content） */
export interface SkillContentRead {
  name: string
  content: string
}

/** 单条元数据：GET /skills/{name} */
export function getSkill(name: string): Promise<SkillRow> {
  return get<SkillRow>(`/skills/${encodeURIComponent(name)}`)
}

/** 单条正文：GET /skills/{name}/content */
export function getSkillContent(name: string): Promise<SkillContentRead> {
  return get<SkillContentRead>(`/skills/${encodeURIComponent(name)}/content`)
}

/** 创建技能：POST /skills */
export function createSkill(payload: SkillCreatePayload): Promise<SkillRow> {
  return post<SkillRow>('/skills', payload)
}

/** 局部更新（description / body，name 不可改）：PATCH /skills/{name} */
export function patchSkill(name: string, payload: SkillPatchPayload): Promise<SkillRow> {
  return patch<SkillRow>(`/skills/${encodeURIComponent(name)}`, payload)
}

/** 物理删除（无回收站/墓碑视图）：DELETE /skills/{name} */
export function deleteSkill(name: string): Promise<null> {
  return del<null>(`/skills/${encodeURIComponent(name)}`)
}

/** 单个 skill 的磁盘刷新结果（对应后端 SkillRefreshEntry.action 四态） */
export type SkillRefreshAction = 'rewritten' | 'unchanged' | 'backfilled' | 'missing'

/** 磁盘刷新结果条目（对应后端 SkillRefreshEntry） */
export interface SkillRefreshEntry {
  name: string
  action: SkillRefreshAction
}

/** 磁盘刷新报告（对应后端 SkillRefreshReport）：per-skill 明细 + 四态计数 */
export interface SkillRefreshReport {
  items: SkillRefreshEntry[]
  total: number
  rewritten: number
  unchanged: number
  backfilled: number
  missing: number
}

/**
 * 全量刷新磁盘副本：POST /skills/refresh。
 * DB 正文是真相源，磁盘 SKILL.md 是运行副本；content_hash 一致的条目不动
 * （unchanged），缺失/漂移的从 DB 重写（rewritten），legacy NULL-body 行从
 * 磁盘回填 DB（backfilled），双丢条目报告为 missing。
 */
export function refreshAllSkills(): Promise<SkillRefreshReport> {
  return post<SkillRefreshReport>('/skills/refresh')
}

/** 单条刷新磁盘副本：POST /skills/{name}/refresh（DB 无行时后端 404） */
export function refreshSkill(name: string): Promise<SkillRefreshReport> {
  return post<SkillRefreshReport>(`/skills/${encodeURIComponent(name)}/refresh`)
}

/**
 * LLM 草稿生成 payload（POST /skills/generate）。
 * 与后端 SkillGenerateRequest 对齐：description 必填，hint 可选。
 */
export interface SkillGeneratePayload {
  description: string
  hint?: string
}

/** LLM 草稿生成响应契约：仅返回 draft 字符串 */
export interface SkillGenerateResponse {
  draft: string
}

/**
 * LLM 草稿生成（仅生成，不落库）：POST /skills/generate。
 * 父组件拿到 `draft` 后应让用户在编辑弹窗里继续微调，再走 createSkill 落库。
 *
 * 超时配置：300s（LLM 调用默认 15s 会超时，后端确认可成功；其它端点保持默认 15s 不变）。
 */
export function generateSkill(payload: SkillGeneratePayload): Promise<SkillGenerateResponse> {
  return post<SkillGenerateResponse>('/skills/generate', payload, {
    timeout: 300_000,
  })
}

// ---------------------------------------------------------------------------
// Agent Apps
// ---------------------------------------------------------------------------

/** 全量列表：GET /apps */
export function listAgentApps(): Promise<AgentAppRow[]> {
  return get<AgentAppRow[]>('/apps')
}

/** 分页列表：GET /apps/page */
export function listAgentAppsPage(query: PageQuery = {}): Promise<PageResult<AgentAppRow>> {
  return get<PageResult<AgentAppRow>>('/apps/page', { params: toParams(query) })
}

// ---------------------------------------------------------------------------
// LLM Configs
// ---------------------------------------------------------------------------

/** 全量列表：GET /llm-configs */
export function listLlmConfigs(): Promise<LlmConfigRow[]> {
  return get<LlmConfigRow[]>('/llm-configs')
}

/** 分页列表：GET /llm-configs/page */
export function listLlmConfigsPage(query: PageQuery = {}): Promise<PageResult<LlmConfigRow>> {
  return get<PageResult<LlmConfigRow>>('/llm-configs/page', { params: toParams(query) })
}
