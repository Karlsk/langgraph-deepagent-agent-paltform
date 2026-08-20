/**
 * SubAgent API 模块：对接后端 /subagents* 端点（见 `app/api/v1/subagents.py`）
 * 与 `app/schemas/agent_apps.py` 的 Pydantic DTO。
 *
 * 模块职责：
 * - CRUD：`POST/GET/PATCH/DELETE /subagents[/<name>]`
 * - 分页：`GET /subagents/page`
 * - 单轮测试：`POST /subagents/<name>/test`（消耗 LLM token）
 *
 * 约定：
 * - 响应信封 {code, message, data} 已由 request.ts 拦截器解包，本模块函数
 *   返回值即 data 载荷；
 * - 全量列表端点（GET /subagents）返回裸数组；分页端点（GET /subagents/page）
 *   返回 PageResult<T>（后端字段 pageSize 为驼峰，其余行字段为 snake_case）；
 * - 所有端点需登录态（get_current_session），token 注入由 request.ts 拦截器
 *   统一处理（当前为 TODO 占位）。
 */
import { del, get, patch, post } from '@/utils/request'
import type { PageQuery, PageResult } from '@/types'

// ---------------------------------------------------------------------------
// 类型（与后端 Pydantic DTO 1:1 对齐）
// ---------------------------------------------------------------------------

/** SubAgent 资产行（对应后端 SubAgentRead） */
export interface SubAgentRow {
  name: string
  description: string
  when_to_use: string
  system_prompt: string
  /** 允许使用的工具名列表（null 表示继承父 AgentApp）；元素为 builtin 裸名或 `{server}__{tool}` 命名空间名 */
  allowed_tools: string[] | null
  /** `provider/model` 引用；null → 运行时回退到 `default/default` */
  model: string | null
  max_turns: number | null
  /**
   * 绑定的 skill 资产名白名单（语义对称 `AgentApp.skill_names`）：
   * - `null`：运行时继承父 AgentApp 的全集；
   * - `[]`：显式不绑定任何 skill；
   * - `[<name>, ...]`：显式白名单。
   * 单轮测试无父级上下文时，`null` 在后端按 `[]` 处理。
   */
  skill_names: string[] | null
  content_hash: string
  version: number
  created_by: string | null
}

/** 创建 payload（对应后端 SubAgentCreate） */
export interface SubAgentCreatePayload {
  name: string
  description: string
  when_to_use: string
  system_prompt: string
  allowed_tools?: string[] | null
  model?: string | null
  max_turns?: number | null
  /**
   * 绑定的 skill 资产名白名单。语义同 `SubAgentRow.skill_names`。
   * 不传时后端默认 `null`（继承父 AgentApp）。
   */
  skill_names?: string[] | null
}

/** 部分更新 payload（对应后端 SubAgentUpdate；name 不可改；空对象会被后端 422 拒绝） */
export interface SubAgentPatchPayload {
  description?: string
  when_to_use?: string
  system_prompt?: string
  allowed_tools?: string[] | null
  model?: string | null
  max_turns?: number | null
  /**
   * 替换为新的 skill 白名单（语义同 `SubAgentRow.skill_names`）。
   * `null` 视为在 PATCH 中未提供；`[]` 显式清空；`[<name>, ...]` 显式白名单。
   */
  skill_names?: string[] | null
}

/** 单轮测试运行请求 payload（对应后端 SubAgentTestRequest） */
export interface SubAgentTestPayload {
  prompt: string
}

/** 单轮测试运行响应（对应后端 SubAgentTestResult） */
export interface SubAgentTestResult {
  final_message: string
  turns: number
  duration_seconds: number
  model: string
}

// ---------------------------------------------------------------------------
// 通用 helper
// ---------------------------------------------------------------------------

/** 把 PageQuery 透传为后端查询参数（page/pageSize/keyword） */
function toParams(query: PageQuery): Record<string, unknown> {
  return { page: query.page, pageSize: query.pageSize, keyword: query.keyword }
}

// ---------------------------------------------------------------------------
// CRUD
// ---------------------------------------------------------------------------

/** 全量列表：GET /subagents */
export function listSubAgents(): Promise<SubAgentRow[]> {
  return get<SubAgentRow[]>('/subagents')
}

/** 分页列表：GET /subagents/page — keyword 对 name 大小写不敏感模糊匹配 */
export function listSubAgentsPage(
  query: PageQuery = {},
): Promise<PageResult<SubAgentRow>> {
  return get<PageResult<SubAgentRow>>('/subagents/page', { params: toParams(query) })
}

/** 单条：GET /subagents/{name} */
export function getSubAgent(name: string): Promise<SubAgentRow> {
  return get<SubAgentRow>(`/subagents/${encodeURIComponent(name)}`)
}

/** 创建：POST /subagents — 201 */
export function createSubAgent(payload: SubAgentCreatePayload): Promise<SubAgentRow> {
  return post<SubAgentRow>('/subagents', payload)
}

/** 局部更新（name 不可改；空 payload 由后端 422 拒绝）：PATCH /subagents/{name} */
export function patchSubAgent(
  name: string,
  payload: SubAgentPatchPayload,
): Promise<SubAgentRow> {
  return patch<SubAgentRow>(`/subagents/${encodeURIComponent(name)}`, payload)
}

/** 物理删除：DELETE /subagents/{name}（无回收站 / 墓碑视图） */
export function deleteSubAgent(name: string): Promise<null> {
  return del<null>(`/subagents/${encodeURIComponent(name)}`)
}

// ---------------------------------------------------------------------------
// 单轮测试运行（⚠️ 需外部资源 / 会消耗 token）
// ---------------------------------------------------------------------------

/**
 * 单轮测试运行：POST /subagents/{name}/test。
 *
 * 不影响任何会话状态；用于在编辑或调试时即时查看 SubAgent 的当前配置表现。
 * 受 `RATE_LIMIT_SUBAGENT_TEST` 限流（默认 5 次/分钟）。
 *
 * 超时配置：600s（10 分钟）。后端测试端点会真实调用 LLM，多轮对话 + 工具调用 +
 * 网络往返累计耗时不可预测；沿用 generateSkill 同款的 per-request 超时放宽策略。
 */
export function testSubAgent(
  name: string,
  payload: SubAgentTestPayload,
): Promise<SubAgentTestResult> {
  return post<SubAgentTestResult>(
    `/subagents/${encodeURIComponent(name)}/test`,
    payload,
    { timeout: 600_000 },
  )
}