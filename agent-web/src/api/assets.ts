/**
 * Agent 资产 API 模块：对接后端 5 大资产模块（SubAgents / Skills /
 * AgentApps / MCP Servers / LLM Configs）。
 *
 * 约定：
 * - 响应信封 {code, message, data} 已由 request.ts 拦截器解包，本模块函数
 *   返回值即 data 载荷；
 * - 全量列表端点（GET /<module>）返回裸数组；分页端点（GET /<module>/page）
 *   返回 PageResult<T>（后端字段 pageSize 为驼峰，其余行字段为 snake_case）；
 * - 所有端点需登录态（get_current_session），token 注入由 request.ts
 *   拦截器统一处理（当前为 TODO 占位）。
 */
import { get } from '@/utils/request'
import type { PageQuery, PageResult } from '@/types'

/** SubAgent 资产行（对应后端 SubAgentRead） */
export interface SubAgentRow {
  name: string
  description: string
  when_to_use: string
  system_prompt: string
  allowed_tools: string[] | null
  model: string | null
  max_turns: number | null
  content_hash: string
  version: number
  created_by: string | null
}

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

/** MCP Server 资产行（对应后端 McpServerRead） */
export interface McpServerRow {
  name: string
  transport: 'stdio' | 'http'
  command: string | null
  args: string[]
  env: Record<string, string>
  url: string | null
  headers: Record<string, string>
  enabled: boolean
  description: string
  content_hash: string
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
// SubAgents
// ---------------------------------------------------------------------------

/** 全量列表：GET /subagents */
export function listSubAgents(): Promise<SubAgentRow[]> {
  return get<SubAgentRow[]>('/subagents')
}

/** 分页列表：GET /subagents/page */
export function listSubAgentsPage(query: PageQuery = {}): Promise<PageResult<SubAgentRow>> {
  return get<PageResult<SubAgentRow>>('/subagents/page', { params: toParams(query) })
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
// MCP Servers
// ---------------------------------------------------------------------------

/** 全量列表：GET /mcp-servers */
export function listMcpServers(): Promise<McpServerRow[]> {
  return get<McpServerRow[]>('/mcp-servers')
}

/** 分页列表：GET /mcp-servers/page */
export function listMcpServersPage(query: PageQuery = {}): Promise<PageResult<McpServerRow>> {
  return get<PageResult<McpServerRow>>('/mcp-servers/page', { params: toParams(query) })
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
