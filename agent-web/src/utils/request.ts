import axios from 'axios'
import type { AxiosRequestConfig } from 'axios'
import { ElMessage } from 'element-plus'
import type { ApiResponse } from '@/types'

/**
 * 统一 axios 实例：后端 API 前缀为 /api/v1（开发态经 Vite 代理转发，无 rewrite）。
 */
const request = axios.create({
  baseURL: '/api/v1',
  timeout: 15000,
})

request.interceptors.request.use((config) => {
  // TODO(token 注入): 骨架占位 —— 待接入认证体系后注入请求头，
  // 例如: config.headers.Authorization = `Bearer ${token}`
  return config
})

/**
 * 判断响应体是否为后端统一信封 { code, message, data }。
 * 豁免端点（/health、SSE 流等）仍返回裸响应，需与之区分。
 * 要求三字段齐全（data 键必须存在，值可为 null）：避免将形状碰撞的
 * 裸响应（如 { code, message } 且无 data 键）误判为信封而解包出 undefined，
 * 与后端 ApiResponse 三字段契约对齐。
 */
function isEnvelope(body: unknown): body is ApiResponse<unknown> {
  return (
    typeof body === 'object' &&
    body !== null &&
    typeof (body as ApiResponse<unknown>).code === 'number' &&
    typeof (body as ApiResponse<unknown>).message === 'string' &&
    'data' in (body as Record<string, unknown>)
  )
}

/** 从任意响应体中提取可读错误文案（信封 message 优先，回退旧 FastAPI detail 形态）。 */
function extractErrorMessage(body: unknown, fallback: string): string {
  if (isEnvelope(body)) {
    return body.message || fallback
  }
  // 过渡兼容：FastAPI 默认错误形态 { detail }（detail 可能是字符串或 422 错误列表）
  if (typeof body === 'object' && body !== null && 'detail' in body) {
    const detail = (body as { detail: unknown }).detail
    if (typeof detail === 'string' && detail.length > 0) {
      return detail
    }
  }
  return fallback
}

request.interceptors.response.use(
  (response) => {
    const body: unknown = response.data
    if (isEnvelope(body)) {
      // 统一信封：code 与 HTTP status 一致；2xx（含创建类端点的 201）视为成功，自动解包 data
      if (body.code >= 200 && body.code < 300) {
        // 拦截器已解包为业务数据（非 AxiosResponse），返回类型经 get/post/put/del 泛型收敛
        return body.data as unknown as typeof response
      }
      // 防御分支：非 2xx code 正常不会进入成功回调（HTTP 非 2xx 会被 axios reject）
      ElMessage.error(body.message || '请求失败')
      return Promise.reject(new Error(body.message || '请求失败'))
    }
    // 豁免端点（如 /health）仍返回裸响应，原样透传
    return body as unknown as typeof response
  },
  (error: unknown) => {
    if (axios.isAxiosError(error)) {
      const status = error.response?.status
      if (status === 401) {
        // TODO(401 处理): 骨架占位 —— 待接入登录态后处理过期跳转 / 清理会话
      }
      // 错误体兼容两种形态：新统一信封 { code, message, data } 与旧 FastAPI { detail }
      const message = extractErrorMessage(
        error.response?.data,
        error.message || '请求失败',
      )
      ElMessage.error(message)
    } else {
      ElMessage.error('请求失败')
    }
    return Promise.reject(error)
  },
)

/** GET 请求，返回解包后的业务数据 */
export function get<T>(url: string, config?: AxiosRequestConfig): Promise<T> {
  // 响应拦截器已把信封解包为 data，此处修正 axios 默认的 AxiosResponse 返回类型
  return request.get(url, config) as unknown as Promise<T>
}

/** POST 请求，返回解包后的业务数据 */
export function post<T>(
  url: string,
  data?: unknown,
  config?: AxiosRequestConfig,
): Promise<T> {
  return request.post(url, data, config) as unknown as Promise<T>
}

/** PUT 请求，返回解包后的业务数据 */
export function put<T>(
  url: string,
  data?: unknown,
  config?: AxiosRequestConfig,
): Promise<T> {
  return request.put(url, data, config) as unknown as Promise<T>
}

/** PATCH 请求（部分更新），返回解包后的业务数据。沿用与 POST 同形态：响应拦截器已统一解包。provider.ts 用于 /providers/{name} 与 /providers/{name}/models/{model} 的局部更新。 */
export function patch<T>(
  url: string,
  data?: unknown,
  config?: AxiosRequestConfig,
): Promise<T> {
  return request.patch(url, data, config) as unknown as Promise<T>
}

/** DELETE 请求（后端 data 恒为 null，通常以 void 承接） */
export function del<T = void>(
  url: string,
  config?: AxiosRequestConfig,
): Promise<T> {
  return request.delete(url, config) as unknown as Promise<T>
}

export default request
