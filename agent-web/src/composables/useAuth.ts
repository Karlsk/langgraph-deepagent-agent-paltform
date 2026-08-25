/**
 * 认证 composable：在不引入 Pinia 的前提下提供登录态共享（Phase 1 G1 单层）。
 *
 * 设计要点：
 * - 模块单例：`user` / `userToken` 在模块级 ref 中维护，所有使用方共享
 *   同一份响应式状态（与 useRequest 等其他 composable 一致的轻量策略）；
 * - 启动时 `bootstrap()` 从 authStorage 同步恢复（无需后端校验，避免阻塞首屏）；
 * - `login` 流程（Phase 1 G1）：调 `/auth/login` 拿 access + refresh 双 token；
 *   access_token 持久化到 localStorage `auth.userToken`，refresh_token 仅
 *   内存态保存（`authStorage.setRefreshToken`），不落 localStorage；
 * - `refreshUserToken()` 主动旋转：在 401 自动 refresh 之外的显式刷新入口；
 *   返回新 access_token；失败时清空全部并返回 null；
 * - `logout` 同步清空后端 refresh_token（best-effort）+ 本地全部态，
 *   由调用方决定跳转路径（App.vue 顶栏注销 vs 401 触发跳转）。
 *
 * refresh_token 拦截器（401 自动 refresh + 重发）由 `utils/request.ts` 承担，
 * 本模块只暴露显式 `refreshUserToken()` 供业务代码主动调用。
 */
import { ref } from 'vue'

import {
  clearAuth,
  getRefreshToken,
  getUser,
  getUserToken,
  setRefreshToken,
  setUser,
  setUserToken,
  type StoredUser,
} from '@/utils/authStorage'
import { logoutApi, refreshTokenApi, type LoginResponse } from '@/api/auth'

const user = ref<StoredUser | null>(getUser())
const userToken = ref<string | null>(getUserToken())

/** 启动时同步恢复本地 token + 用户态（避免 SSR / 测试期间抛错） */
export function bootstrap(): void {
  user.value = getUser()
  userToken.value = getUserToken()
}

/** 当前用户态（响应式）；null 表示未登录 */
export function currentUser(): StoredUser | null {
  return user.value
}

/** 当前用户 token 是否存在（响应式） */
export function hasUserToken(): boolean {
  return userToken.value !== null
}

/**
 * Phase 1 G1 登录：
 * - `loginApi()` 返回 ``LoginResponse{access_token, refresh_token, ...}``；
 * - access_token 持久化到 localStorage；refresh_token 仅内存态；
 * - 用户态从 access_token JWT 的 sub claim 解码出 user.id；
 *   email 直接来自表单输入（与后端 login 响应没有 user.id 字段对齐）。
 */
export async function login(email: string, password: string): Promise<void> {
  const resp: LoginResponse = await import('@/api/auth').then((m) =>
    m.login(email, password),
  )
  setUserToken(resp.access_token)
  setRefreshToken(resp.refresh_token)
  setUser({
    id: extractUserId(resp.access_token),
    email,
    username: null,
  })
  user.value = getUser()
  userToken.value = getUserToken()
}

/**
 * 主动旋转 refresh_token：
 * - 无 refresh_token 时直接返回 null（不报错，配合 401 拦截器使用）；
 * - 成功：更新 access_token + refresh_token 到本地，并返回新 access_token；
 * - 失败：清空全部本地态，返回 null。
 */
export async function refreshUserToken(): Promise<string | null> {
  const rt = getRefreshToken()
  if (!rt) {
    return null
  }
  try {
    const resp: LoginResponse = await refreshTokenApi(rt)
    setUserToken(resp.access_token)
    setRefreshToken(resp.refresh_token)
    userToken.value = getUserToken()
    return resp.access_token
  } catch {
    clearAuth()
    user.value = null
    userToken.value = null
    return null
  }
}

/**
 * 注销：
 * - 尝试撤销后端 refresh_token（best-effort，失败不影响本地清理）；
 * - 清空全部本地态（userToken + refreshToken + user）。
 * 调用方按需跳转（App.vue 顶栏注销按钮跳 /login）。
 */
export async function logout(): Promise<void> {
  const rt = getRefreshToken()
  if (rt) {
    try {
      await logoutApi(rt)
    } catch {
      // best-effort：忽略网络/服务端错误
    }
  }
  clearAuth()
  user.value = null
  userToken.value = null
}

/** JWT payload 解码（仅取 sub 数字）。失败返回 0，由调用方走兜底。 */
export function extractUserId(token: string): number {
  const part = token.split('.')[1]
  if (!part) return 0
  try {
    const padded = part.replace(/-/g, '+').replace(/_/g, '/')
    const decoded =
      typeof atob === 'function'
        ? atob(padded)
        : Buffer.from(padded, 'base64').toString('binary')
    const payload = JSON.parse(decoded) as { sub?: unknown }
    if (typeof payload.sub === 'number') return payload.sub
    if (typeof payload.sub === 'string') {
      const n = Number(payload.sub)
      return Number.isFinite(n) ? n : 0
    }
    return 0
  } catch {
    return 0
  }
}
