/** 分页查询参数 */
export interface PageQuery {
  /** 页码（从 1 开始） */
  page?: number
  /** 每页条数 */
  pageSize?: number
  /** 模糊搜索关键字 */
  keyword?: string
}

/** 后端统一响应包 */
export interface ApiResponse<T = unknown> {
  /** 业务状态码，0 表示成功 */
  code: number
  /** 提示信息 */
  message: string
  /** 业务数据 */
  data: T
}

/** 分页结果 */
export interface PageResult<T> {
  items: T[]
  total: number
  page: number
  pageSize: number
}
