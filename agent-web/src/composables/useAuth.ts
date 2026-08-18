/**
 * 认证 composable：在不引入 Pinia 的前提下提供登录态共享。
 *
 * 设计要点：
 * - 模块单例：`user` / `sessionToken` 在模块级 ref 中维护，所有使用方共享
 *   同一份响应式状态（与 useRequest 等其他 composable 一致的轻量策略）；
 * - 启动时 `bootstrap()` 从 authStorage 同步恢复（无需后端校验，避免阻塞首屏）；
 * - `login` 流程：先调 `/auth/login` 拿用户 token，再调 `/auth/session` 换会话
 *   token；会话 token 由 request.ts 拦截器自动注入到后续所有请求；
 * - `logout` 清空 localStorage 与内存态，但不主动跳转——让调用方决定
 *   跳转路径（App.vue 顶栏注销 vs 401 触发跳转）。
 */
import { ref } from 'vue'

import axios from 'axios'

import {
  clearAuth,
  getSessionToken,
  getUser,
  setSessionToken,
  setUser,
  type StoredUser,
} from '@/utils/authStorage'
import { login as loginApi, type TokenResponse } from '@/api/auth'

const user = ref<StoredUser | null>(getUser())
const sessionToken = ref<string | null>(getSessionToken())

/** 启动时同步恢复本地 token + 用户态（避免 SSR / 测试期间抛错） */
export function bootstrap(): void {
  user.value = getUser()
  sessionToken.value = getSessionToken()
}

/** 当前用户态（响应式）；null 表示未登录 */
export function currentUser(): StoredUser | null {
  return user.value
}

/** 当前会话 token 是否存在（响应式） */
export function hasSession(): boolean {
  return sessionToken.value !== null
}

/**
 * 独立 axios 实例：仅用于 /auth/session（带用户 token 头）。
 * 与 request.ts 共用信封解包（手写响应拦截器），但**不**注入会话 token，
 * 避免循环（用户 token 还没换成会话 token 时，sessionToken 为 null 也行，
 * 因为 session 端点要求的是用户 token，必须显式 Authorization）。
 */
const authOnlyRequest = axios.create({
  baseURL: '/api/v1',
  timeout: 15000,
})

authOnlyRequest.interceptors.response.use(
  (response) => {
    const body: unknown = response.data
    if (
      typeof body === 'object' &&
      body !== null &&
      typeof (body as { code?: unknown }).code === 'number' &&
      typeof (body as { message?: unknown }).message === 'string' &&
      'data' in (body as Record<string, unknown>)
    ) {
      const envelope = body as { code: number; message: string; data: unknown }
      if (envelope.code >= 200 && envelope.code < 300) {
        return envelope.data as unknown as typeof response
      }
      return Promise.reject(new Error(envelope.message))
    }
    return body as unknown as typeof response
  },
  (error: unknown) => {
    if (axios.isAxiosError(error) && error.response?.data) {
      const detail = (error.response.data as { detail?: unknown }).detail
      if (typeof detail === 'string') return Promise.reject(new Error(detail))
    }
    return Promise.reject(error)
  },
)

/**
 * 用用户 token 换取会话 token。
 * 与 auth.ts.createSession 不同：本函数显式传入 Authorization 头，避免
 * 被 request.ts 拦截器覆盖为旧 sessionToken。
 */
async function exchangeSession(userToken: string): Promise<{
  session_id: string
  name: string
  token: TokenResponse
}> {
  return authOnlyRequest.post('/auth/session', null, {
    headers: { Authorization: `Bearer ${userToken}` },
  }) as unknown as {
    session_id: string
    name: string
    token: TokenResponse
  }
}

/**
 * 登录：先 /auth/login 拿用户 token，再用用户 token 调 /auth/session 换取会话 token。
 * 持久化：会话 token 写入 authStorage；用户态单独持久化（id / email / username）。
 *
 * 后端 401（密码错误）等错误由统一请求层拦截器抛错；本函数不再做二次包装。
 */
export async function login(email: string, password: string): Promise<void> {
  // 1) /auth/login：使用 loginApi（request.ts 不注入会话 token，getSessionToken 为 null）
  const userTokenResp = await loginApi(email, password)
  // 2) /auth/session：使用独立的 authOnlyRequest，显式带用户 token
  const sessionResp = await exchangeSession(userTokenResp.access_token)
  setSessionToken(sessionResp.token.access_token)
  setUser({
    id: extractUserId(userTokenResp.access_token),
    email,
    username: null,
  })
  user.value = getUser()
  sessionToken.value = getSessionToken()
}

/**
 * 注销：清空本地态。
 * 调用方按需跳转（App.vue 顶栏注销按钮跳 /login）。
 */
export function logout(): void {
  clearAuth()
  user.value = null
  sessionToken.value = null
}

/** JWT payload 解码（仅取 sub 数字）。失败返回 0，由调用方走兜底。 */
function extractUserId(token: string): number {
  const part = token.split('.')[1]
  if (!part) return 0
  try {
    const padded = part.replace(/-/g, '+').replace(/_/g, '/')
    const decoded =
      typeof atob === 'function'
        ? atob(padded)
        : Buffer.from(padded, 'base64').toString('binary')
    const payload = JSON.parse(decoded) as { sub?: unknown }
    return typeof payload.sub === 'number' ? payload.sub : 0
  } catch {
    return 0
  }
}