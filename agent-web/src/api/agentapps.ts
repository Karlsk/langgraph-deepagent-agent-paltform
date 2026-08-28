/**
 * AgentApp API 模块：对接后端 `/apps` 前缀的 AgentApp 资源全部端点
 * （`app/api/v1/apps.py`，10 个端点）。AgentApp 是平台实体（伞概念），
 * `engine` 字段区分类型（当前恒 `deepagents`，未来 `workflow`）。
 *
 * 约定：
 * - 响应信封 {code, message, data} 已由 request.ts 拦截器解包，本模块函数
 *   返回值即 data 载荷；
 * - 全量列表端点（GET /apps、GET /apps/published）返回裸数组；分页端点
 *   （GET /apps/page）返回 PageResult<AgentAppRow>（后端字段 pageSize 为
 *   驼峰，其余行字段为 snake_case）；
 * - 路径参数 app_id 为整数主键，无需 encodeURIComponent；
 * - 预留点：后端 /apps/page 暂无 `engine` 过滤参数，未来 workflow 引擎
 *   落地时需加 `?engine=` 查询（后端一行改动），各引擎管理页各自过滤。
 *
 * 生命周期语义（与后端一致）：
 * - 创建后 status='draft'、engine='deepagents'；
 * - 发布（POST /apps/{app_id}/publish）：校验引用完整性 + 工具白名单后
 *   置 published 并物化 Agent 层 Workspace；
 * - 已发布应用编辑（PATCH）会回退 status='draft'、清空 workspace_hash、
 *   agent_workspace_status='pending' 并 version+1，需重新发布；
 * - 系统 default 应用禁删（后端 422）；删除会级联清理 Agent 层 Workspace。
 */
import { del, get, patch, post } from '@/utils/request'
import type { PageQuery, PageResult } from '@/types'

/** AgentApp 生命周期状态（后端 status 字段） */
export type AgentAppStatus = 'draft' | 'published'

/** AgentApp 资产行（对应后端 AgentAppRead 全字段） */
export interface AgentAppRow {
  id: number
  name: string
  system_prompt: string
  allowed_tools: string[] | null
  model: string | null
  skill_names: string[]
  subagent_names: string[]
  interrupt_on: Record<string, boolean>
  engine: string
  status: AgentAppStatus
  published_hash: string | null
  /** 发布时打印的 Agent 层 Workspace 目录 */
  agent_dir: string | null
  /** Agent Workspace skill 文件内容哈希 */
  workspace_hash: string | null
  /** Agent Workspace 物化状态（pending|ready|stale） */
  agent_workspace_status: string
  version: number
  created_by: string | null
}

/**
 * AgentApp 创建 payload（POST /apps）。
 * name + system_prompt 必填；可选字段缺省：
 * allowed_tools=null（引擎默认）、model=null、skill_names=[]、
 * subagent_names=[]、interrupt_on={}。
 */
export interface AgentAppCreatePayload {
  name: string
  system_prompt: string
  allowed_tools?: string[] | null
  model?: string | null
  skill_names?: string[]
  subagent_names?: string[]
  /** 工具审批开关：key=工具名，value=true 表示该工具需人工审批 */
  interrupt_on?: Record<string, boolean>
}

/**
 * AgentApp 部分更新 payload（PATCH /apps/{app_id}，name 不可改）。
 *
 * null 语义（后端校验）：
 * - `skill_names` / `subagent_names` 显式传 `null` 会被 422 拒绝
 *   （"must not be null; pass an empty list to clear it"）——清空必须传 `[]`；
 * - `allowed_tools: null` 合法（重置为引擎默认）；
 * - 空 payload（全部省略）会被 422 拒绝（"nothing to update"）。
 */
export interface AgentAppPatchPayload {
  system_prompt?: string
  allowed_tools?: string[] | null
  model?: string | null
  skill_names?: string[]
  subagent_names?: string[]
  /** 工具审批开关：key=工具名，value=true 表示该工具需人工审批 */
  interrupt_on?: Record<string, boolean>
}

/** 把 PageQuery 透传为后端查询参数（page/pageSize/keyword） */
function toParams(query: PageQuery): Record<string, unknown> {
  return { page: query.page, pageSize: query.pageSize, keyword: query.keyword }
}

/** 全量列表：GET /apps（按 id 升序的裸数组） */
export function listAgentApps(): Promise<AgentAppRow[]> {
  return get<AgentAppRow[]>('/apps')
}

/** 分页列表：GET /apps/page（keyword 模糊匹配 name/system_prompt） */
export function listAgentAppsPage(query: PageQuery = {}): Promise<PageResult<AgentAppRow>> {
  return get<PageResult<AgentAppRow>>('/apps/page', { params: toParams(query) })
}

/** 仅已发布列表：GET /apps/published（会话创建下拉的选项来源） */
export function listPublishedAgentApps(): Promise<AgentAppRow[]> {
  return get<AgentAppRow[]>('/apps/published')
}

/** 单条详情：GET /apps/{app_id}（不存在 404） */
export function getAgentApp(appId: number): Promise<AgentAppRow> {
  return get<AgentAppRow>(`/apps/${appId}`)
}

/** 创建应用：POST /apps（201；重名 422 "agent app '<name>' already exists"） */
export function createAgentApp(payload: AgentAppCreatePayload): Promise<AgentAppRow> {
  return post<AgentAppRow>('/apps', payload)
}

/** 部分更新：PATCH /apps/{app_id}（已发布应用编辑回退 draft，见模块注释） */
export function patchAgentApp(appId: number, payload: AgentAppPatchPayload): Promise<AgentAppRow> {
  return patch<AgentAppRow>(`/apps/${appId}`, payload)
}

/**
 * 删除应用：DELETE /apps/{app_id}。
 * 级联清理 Agent 层 Workspace；系统 default 应用后端 422 拒删。
 */
export function deleteAgentApp(appId: number): Promise<null> {
  return del<null>(`/apps/${appId}`)
}

/**
 * 发布应用：POST /apps/{app_id}/publish。
 * 校验引用完整性（skill/subagent 存在）+ 工具白名单（工具目录内）后
 * 置 published、打印 agent_dir 并物化 Agent 层 Workspace；重复发布幂等。
 */
export function publishAgentApp(appId: number): Promise<AgentAppRow> {
  return post<AgentAppRow>(`/apps/${appId}/publish`)
}

/** 用户关联（运维端点，无 UI 入口）：POST /apps/{app_id}/associate-user/{user_id} */
export function associateAppUser(appId: number, userId: number): Promise<null> {
  return post<null>(`/apps/${appId}/associate-user/${userId}`)
}

/** 用户解绑（运维端点，无 UI 入口）：DELETE /apps/{app_id}/associate-user/{user_id} */
export function disassociateAppUser(appId: number, userId: number): Promise<null> {
  return del<null>(`/apps/${appId}/associate-user/${userId}`)
}
