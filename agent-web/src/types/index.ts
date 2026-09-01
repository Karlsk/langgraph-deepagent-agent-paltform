/** 分页查询参数 */
export interface PageQuery {
  /** 页码（从 1 开始） */
  page?: number
  /** 每页条数 */
  pageSize?: number
  /** 模糊搜索关键字 */
  keyword?: string
}

/**
 * 后端统一响应信封：code 数值与 HTTP status 完全一致
 * （成功为 2xx，创建类端点为 201；错误时 code 为对应错误状态码）。
 * 豁免端点（GET /、GET /health、GET /api/v1/health、POST /chatbot/chat/stream）
 * 仍返回裸响应，不套信封。
 */
export interface ApiResponse<T = unknown> {
  /** 业务状态码，数值与 HTTP status 一致；2xx 视为成功（如 200、201） */
  code: number
  /** 提示信息 */
  message: string
  /** 业务数据；成功时承载载荷，错误/DELETE 时为 null（422 时为错误列表） */
  data: T | null
}

/** 分页结果 */
export interface PageResult<T> {
  items: T[]
  total: number
  page: number
  pageSize: number
}

// ---------------------------------------------------------------------------
// Chat 交互域镜像类型（G4 spec-g4-chat §9.6，与 app/schemas/chat.py 对齐）
// ---------------------------------------------------------------------------

/** 聊天消息（对应后端 Message；SSE 审批复用消息通道） */
export interface ChatMessage {
  role: 'user' | 'assistant' | 'system'
  content: string
}

/** interrupt 投影中的单个待审批动作（§4.2 稳定契约：仅 tool + args） */
export interface ActionRequest {
  tool: string
  args: Record<string, unknown>
}

/** interrupt 投影载荷（SSE interrupt 帧与响应共用，§4.2/§4.5） */
export interface InterruptPayload {
  action_requests: ActionRequest[]
}

/** SSE 帧模型（对应后端 StreamEvent；单 schema 可选字段，exclude_none 序列化） */
export interface StreamEvent {
  type: 'message' | 'tool_call' | 'interrupt' | 'summary' | 'error' | 'done'
  /** message 帧：正文分片；tool_call 帧：工具输出 */
  content?: string
  /** 来源标签：subagent 名 / coordinator / system */
  source?: string
  /** tool_call 帧：工具名 */
  name?: string
  /** interrupt 帧：待审批动作列表 */
  action_requests?: ActionRequest[]
  /** summary 帧：压缩摘要文本 */
  summary_text?: string
  /** error 帧：错误文本 */
  message?: string
  /** done 帧：本轮消息计数 */
  message_count?: number
  /** done 帧：本轮是否发生压缩 */
  compressed?: boolean
  /** done 帧：线程是否停在中断态 */
  interrupted?: boolean
}

/** L2 历史行投影（GET /messages；G3 §4.1.1 行类型） */
export interface HistoryItem {
  type: 'message' | 'tool_call' | 'summary'
  seq: number
  ts: string
  /** message 行：user | assistant */
  role?: string | null
  /** message / summary 行文本 */
  content?: string | null
  /** tool_call 行：工具名 */
  name?: string | null
  /** tool_call 行：结果摘要 */
  summary?: string | null
  /** assistant 行：subagent 名（展示专用行，卡片化渲染；coordinator 行为空） */
  source?: string | null
}

/** GET /messages 响应载荷（历史 + pending 中断拉齐，§5.3/§6.1） */
export interface MessagesResponse {
  messages: HistoryItem[]
  pending_interrupt: InterruptPayload | null
}

/** 非流式 POST /chat 响应载荷（§4.5；interrupt 仅超限非空） */
export interface ChatResponseData {
  messages: ChatMessage[]
  interrupt: InterruptPayload | null
}

/** POST /rebuild 结果计数（§6.2） */
export interface RebuildResult {
  rebuilt_messages: number
  skipped_tool_calls: number
  /** 带 source 的 subagent 展示行（不重灌） */
  skipped_subagent_messages: number
  l2_source_lines: number
}

/** GET /chat/traces 行投影（§7.3；events 每项含 agent 字段） */
export interface ChatTraceItem {
  id: number
  status: string
  turns: number
  duration_seconds: number
  error: string | null
  created_at: string
  events: Record<string, unknown>[]
}
