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
