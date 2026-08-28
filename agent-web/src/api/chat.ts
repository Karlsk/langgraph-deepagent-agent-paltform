/**
 * Chat API 模块：对接后端 G4 交互端点（`app/api/v1/chat.py`，spec-g4-chat
 * §3/§10.3）。四端点均走 `X-Session-Id` header 寻址（交互面端点族统一
 * header；管理面 CRUD 仍在 /sessions）。
 *
 * 模块职责：
 * - 非流式回合：`POST /chat`（auto-approve 语义；超限时信封 message
 *   携带 `auto_approve_limit_exceeded`）
 * - 历史拉取：`GET /messages`（L2 行投影 + pending_interrupt 拉齐）
 * - 灾难重建：`POST /rebuild`（422 无可读行 / 409 pending 中断）
 * - 运行轨迹：`GET /chat/traces`（created_at 倒序，默认 limit 100）
 *
 * SSE 流式端点（`POST /chat/stream`）不在此模块：信封豁免 + 长连接，
 * 由 `utils/sse.ts` + `composables/useChatStream.ts` 直接消费。
 */
import { get, post } from '@/utils/request'
import type {
  ChatMessage,
  ChatResponseData,
  ChatTraceItem,
  MessagesResponse,
  RebuildResult,
} from '@/types'

/** X-Session-Id 寻址 header（强制必填，缺失 422） */
function sessionHeader(sessionId: string): Record<string, string> {
  return { 'X-Session-Id': sessionId }
}

/** 非流式回合：POST /chat（interrupt 自动批准；返回本轮回复） */
export function sendChat(
  sessionId: string,
  messages: ChatMessage[],
): Promise<ChatResponseData> {
  return post<ChatResponseData>('/chat', { messages }, {
    headers: sessionHeader(sessionId),
  })
}

/** 历史拉取：GET /messages（L2 行投影 + pending 中断拉齐） */
export function fetchMessages(sessionId: string): Promise<MessagesResponse> {
  return get<MessagesResponse>('/messages', { headers: sessionHeader(sessionId) })
}

/** 灾难重建：POST /rebuild（L2 → L1 checkpoint 重灌） */
export function rebuildSession(sessionId: string): Promise<RebuildResult> {
  return post<RebuildResult>('/rebuild', undefined, {
    headers: sessionHeader(sessionId),
  })
}

/** 运行轨迹：GET /chat/traces（source=chat 行，倒序） */
export function fetchChatTraces(sessionId: string): Promise<ChatTraceItem[]> {
  return get<ChatTraceItem[]>('/chat/traces', {
    headers: sessionHeader(sessionId),
  })
}
