import axios, { type AxiosRequestConfig } from 'axios'
import { ElMessage } from 'element-plus'
import type { ApiResponse } from '@/types'
import { clearAuth, getUserToken } from '@/utils/authStorage'
import { refreshUserToken } from '@/composables/useAuth'

/**
 * 统一 axios 实例：后端 API 前缀为 /api/v1（开发态经 Vite 代理转发，无 rewrite）。
 */
const request = axios.create({
  baseURL: '/api/v1',
  timeout: 15000,
})

request.interceptors.request.use((config) => {
  // 用户 token 注入：受保护资源端点（/providers、/skills 等）均需用户 token，
  // 由后端 get_current_user 依赖读取；未登录态不写入 Authorization，
  // 让 /auth/login 与 /auth/register 走 OAuth2 form 流程。
  const token = getUserToken()
  if (token) {
    // AxiosHeaders 或普通对象都允许；缺失时 fallback 为空对象（兼容测试场景
    // 与真实 axios 自动注入空 headers 两种情形），运行期以 set() 为主，
    // 缺失时 fallback 为赋值；最后把 headers 写回 config，
    // 便于下游适配器（mock 或真实 axios）观察到注入结果。
    const headers = (config.headers ?? {}) as unknown as {
      set?: (k: string, v: string) => void
      [k: string]: unknown
    }
    if (typeof headers.set === 'function') {
      headers.set('Authorization', `Bearer ${token}`)
    } else {
      headers['Authorization'] = `Bearer ${token}`
    }
    if (config.headers === undefined || config.headers === null) {
      config.headers = headers as never
    }
  }
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

/** 同名冲突错误文案判定：命中时在 422 主提示后追加回收站清理引导 */
const DUPLICATE_NAME_HINT_RE = /已存在|already exists|exists|taken|唯一/

/**
 * Phase 1 G1：判断一个 401 响应是否来自 ``/auth/refresh`` 自身。
 * refresh 端点自身 401（INVALID_REFRESH_TOKEN / REFRESH_TOKEN_REPLAY）不应
 * 触发递归 refresh；直接走"清空 + 跳 login"路径。
 */
function isRefreshEndpointPath(path: unknown): boolean {
  if (typeof path !== 'string') return false
  // 兼容绝对 / 相对路径；``/api/v1`` 前缀由 baseURL 注入，运行时不一定存在。
  return /(^|\/)auth\/refresh(\?|$)/.test(path)
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
  async (error: unknown) => {
    if (axios.isAxiosError(error)) {
      const status = error.response?.status
      const originalConfig = error.config as
        | (AxiosRequestConfig & { _retried?: boolean })
        | undefined

      if (status === 401) {
        // refresh 端点自身 401：直接走清空 + 跳 login，**不**触发递归 refresh。
        const requestUrl = originalConfig?.url ?? ''
        if (isRefreshEndpointPath(requestUrl)) {
          clearAuth()
          void (async (): Promise<void> => {
            const { default: router } = await import('@/router')
            if (router.currentRoute.value.name === 'login') return
            const redirect = router.currentRoute.value.fullPath
            await router.replace({ name: 'login', query: { redirect, reason: 'expired' } })
          })()
          ElMessage.error('会话已失效，请重新登录')
          return Promise.reject(error)
        }

        // 当前已在登录页（如密码错误）：不刷新、不清 token、不跳路由，
        // 仅 ElMessage 弹错后 reject，把控制权交回调用方。
        const { default: router } = await import('@/router')
        if (router.currentRoute.value.name === 'login') {
          ElMessage.error(
            (error.response?.data as { message?: string; detail?: string } | undefined)?.message ??
              (error.response?.data as { detail?: string } | undefined)?.detail ??
              error.message ??
              '请求失败',
          )
          return Promise.reject(error)
        }

        // 其它受保护端点 401：尝试 refresh + 重发原请求。
        if (originalConfig && !originalConfig._retried) {
          originalConfig._retried = true
          const newToken = await refreshUserToken()
          if (newToken) {
            const headers = (originalConfig.headers ?? {}) as unknown as {
              set?: (k: string, v: string) => void
              [k: string]: unknown
            }
            if (typeof headers.set === 'function') {
              headers.set('Authorization', `Bearer ${newToken}`)
            } else {
              headers['Authorization'] = `Bearer ${newToken}`
            }
            originalConfig.headers = headers as never
            return axios.request(originalConfig)
          }
        }

        // refresh 失败或已是第二次 401：清空 + 跳 login。
        clearAuth()
        void (async (): Promise<void> => {
          const { default: router } = await import('@/router')
          if (router.currentRoute.value.name === 'login') {
            // 当前已在登录页（如密码错误）：不跳不丢 token，仅弹错。
            return
          }
          const redirect = router.currentRoute.value.fullPath
          await router.replace({
            name: 'login',
            query: { redirect, reason: 'expired' },
          })
        })()
        ElMessage.error('会话已失效，请重新登录')
        return Promise.reject(error)
      }
      // 错误体兼容两种形态：新统一信封 { code, message, data } 与旧 FastAPI { detail }
      const message = extractErrorMessage(
        error.response?.data,
        error.message || '请求失败',
      )
      ElMessage.error(message)
      // 同名重建引导：422 唯一索引冲突（provider/model 等）时追加回收站提示，
      // 延迟弹出避免与主错误消息重叠。关键字匹配后端稳定错误文案
      // （"provider 'x' already exists" / "model 'x' already exists ..."）。
      if (status === 422 && DUPLICATE_NAME_HINT_RE.test(message)) {
        setTimeout(() => {
          ElMessage.error('如名称已被软删占用，可前往「模型管理 → 回收站」永久清理后重建。')
        }, 200)
      }
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
