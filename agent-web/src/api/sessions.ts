/**
 * Session API 模块：对接后端 /sessions 端点（见 `app/api/v1/sessions.py`）
 * 与 `app/schemas/session.py` 的 Pydantic DTO（G3 spec-g3-session §11.6）。
 *
 * 模块职责：
 * - 分页列表：`GET /sessions`（资源根直接分页，无 `/page` 后缀——议题 3
 *   RESTful 定案，对齐 LangGraph `/threads` 风格；支持 `agent_app_id` 过滤）
 * - 元数据详情：`GET /sessions/{session_id}`（含 message_count）
 * - 创建：`POST /sessions`（agent_app_id 必填；后端自动 associate，201）
 * - 重命名：`PATCH /sessions/{session_id}`（仅 name 可改）
 * - 级联删除：`DELETE /sessions/{session_id}`（L1 checkpoint → L2 JSONL → L0 行）
 * - 历史导出：`GET /sessions/{session_id}/export?format=json|jsonl`
 *   （非信封文件下载，blob 接收；项目首个下载类端点，§11.5.3 先例）
 *
 * 约定：
 * - 响应信封 {code, message, data} 已由 request.ts 拦截器解包（export 豁免：
 *   裸文件响应不套信封，isEnvelope 判定不命中而原样透传）；
 * - 越权 / 不存在的 session 一律 404（后端防枚举，不出 403）。
 */
import { del, get, patch, post } from '@/utils/request'
import type { PageQuery, PageResult } from '@/types'

// ---------------------------------------------------------------------------
// 类型（与后端 Pydantic DTO 1:1 对齐）
// ---------------------------------------------------------------------------

/** 会话元数据行（对应后端 SessionRead） */
export interface SessionRead {
  session_id: string
  name: string
  /** 绑定的 AgentApp id；历史行为可能为 null（新建必填） */
  agent_app_id: number | null
  created_at: string
  /** 重命名时间戳；未重命名过为 null */
  updated_at: string | null
  /** 仅详情端点填充（列表为 null，避免 N+1 反序列化 checkpoint） */
  message_count: number | null
}

/** 创建 payload（对应后端 SessionCreate；agent_app_id 必填） */
export interface SessionCreatePayload {
  agent_app_id: number
  name: string
}

/** 重命名 payload（对应后端 SessionUpdate；仅 name） */
export interface SessionPatchPayload {
  name: string
}

// ---------------------------------------------------------------------------
// 通用 helper
// ---------------------------------------------------------------------------

/** 把 PageQuery（+ session 特有过滤）透传为后端查询参数 */
function toParams(
  query: PageQuery & { agentAppId?: number },
): Record<string, unknown> {
  return {
    page: query.page,
    pageSize: query.pageSize,
    agent_app_id: query.agentAppId,
  }
}

// ---------------------------------------------------------------------------
// CRUD
// ---------------------------------------------------------------------------

/**
 * 分页列表：GET /sessions — created_at 倒序；`agentAppId` 映射为后端
 * `agent_app_id` 过滤参数。列表行不含 message_count（后端保持 null）。
 */
export function listSessions(
  query: PageQuery & { agentAppId?: number } = {},
): Promise<PageResult<SessionRead>> {
  return get<PageResult<SessionRead>>('/sessions', { params: toParams(query) })
}

/** 单条详情：GET /sessions/{session_id}（含 message_count） */
export function getSession(sessionId: string): Promise<SessionRead> {
  return get<SessionRead>(`/sessions/${encodeURIComponent(sessionId)}`)
}

/** 创建：POST /sessions — 201；后端先幂等 associate 再建行 */
export function createSession(payload: SessionCreatePayload): Promise<SessionRead> {
  return post<SessionRead>('/sessions', payload)
}

/** 重命名：PATCH /sessions/{session_id}（name 1-100 字，后端同步更新 updated_at） */
export function updateSession(
  sessionId: string,
  payload: SessionPatchPayload,
): Promise<SessionRead> {
  return patch<SessionRead>(`/sessions/${encodeURIComponent(sessionId)}`, payload)
}

/** 级联删除：DELETE /sessions/{session_id}（信封 data 恒为 null，以 void 承接） */
export function deleteSession(sessionId: string): Promise<void> {
  return del<void>(`/sessions/${encodeURIComponent(sessionId)}`)
}

// ---------------------------------------------------------------------------
// 历史导出（非信封文件下载）
// ---------------------------------------------------------------------------

/**
 * 导出会话记录：GET /sessions/{session_id}/export?format=json|jsonl。
 *
 * json 返回带元数据头的单文档；jsonl 一行一条消息。响应为裸文件
 * （Content-Disposition attachment），以 `responseType: 'blob'` 接收后
 * 由调用方经 a[download] 触发浏览器保存。
 */
export function exportSessionHistory(
  sessionId: string,
  format: 'json' | 'jsonl' = 'json',
): Promise<Blob> {
  return get<Blob>(`/sessions/${encodeURIComponent(sessionId)}/export`, {
    params: { format },
    responseType: 'blob',
  })
}
