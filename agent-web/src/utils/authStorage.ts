/**
 * 认证态本地存储：用户 token + refresh token + 用户信息（Phase 1 G1 单层）。
 *
 * 设计要点：
 * - access_token（用户 token）持久化到 localStorage ``auth.userToken``（7 天）；
 *   注入到 request.ts 请求拦截器，所有受保护端点（/providers、/skills 等）都
 *   使用它携带 ``Authorization: Bearer ...``。
 * - refresh_token（30 天）**仅内存态**，不持久化（spec §5.2 + R7）：
 *   关闭/刷新页面即丢失，避免 XSS 直接盗用长期凭据。
 *   ``auth.refreshToken`` key 在 localStorage 中**永不写入**（保持 API 一致性），
 *   实现是模块级 ``inMemoryRefreshToken`` 变量。
 * - 用户信息（id / email / username）来自 ``/auth/login`` 的 ``LoginResponse``；
 *   由于 access_token 是 JWT，user.id 由前端 JWT 解码推断（见 ``useAuth.extractUserId``）。
 *   用户信息持久化到 localStorage ``auth.user``，供 App.vue 顶栏展示。
 *
 * Phase 1 G1 收尾：旧的 ``auth.sessionToken`` 存储 / getter / setter / clearer
 * 已随 chatbot runtime 一起废弃（chatbot 端点不再注册），不再保留兼容入口。
 * 调用方应统一使用 ``getUserToken / getRefreshToken / clearUserToken /
 * clearRefreshToken / clearAuth``。
 *
 * 风险点：localStorage 仅在浏览器存在，本模块通过惰性判断在加载阶段不抛错，
 * 供 main.ts 启动时安全调用。
 */

const USER_TOKEN_KEY = 'auth.userToken'
const USER_KEY = 'auth.user'

/** 内存 fallback：localStorage 不可用（如 SSR / Node 测试）时使用 */
const memoryStore: Map<string, string> = new Map()

/**
 * refresh_token 内存态（不持久化）。
 * 模块级闭包变量：刷新页面即丢失，需重新登录。
 * 与 userToken 的 localStorage 持久化形成对照。
 */
let inMemoryRefreshToken: string | null = null

function safeGetItem(key: string): string | null {
  try {
    if (typeof localStorage === 'undefined') {
      return memoryStore.get(key) ?? null
    }
    return localStorage.getItem(key)
  } catch {
    return memoryStore.get(key) ?? null
  }
}

function safeSetItem(key: string, value: string): void {
  try {
    if (typeof localStorage === 'undefined') {
      memoryStore.set(key, value)
      return
    }
    localStorage.setItem(key, value)
  } catch {
    memoryStore.set(key, value)
  }
}

function safeRemoveItem(key: string): void {
  try {
    if (typeof localStorage === 'undefined') {
      memoryStore.delete(key)
      return
    }
    localStorage.removeItem(key)
  } catch {
    memoryStore.delete(key)
  }
}

export interface StoredUser {
  id: number
  email: string
  username: string | null
}

/** 读取当前 access_token / 用户 token（供 request.ts 拦截器使用）。 */
export function getUserToken(): string | null {
  return safeGetItem(USER_TOKEN_KEY)
}

/** 写入 access_token / 用户 token（登录成功或 refresh 时调用）。 */
export function setUserToken(token: string): void {
  safeSetItem(USER_TOKEN_KEY, token)
}

/** 清除 access_token / 用户 token（注销 / 401 过期）。 */
export function clearUserToken(): void {
  safeRemoveItem(USER_TOKEN_KEY)
}

/** 读取 refresh_token（仅内存态）。 */
export function getRefreshToken(): string | null {
  return inMemoryRefreshToken
}

/** 写入 refresh_token（仅内存态）。 */
export function setRefreshToken(token: string): void {
  inMemoryRefreshToken = token
}

/** 清除 refresh_token（仅内存态）。 */
export function clearRefreshToken(): void {
  inMemoryRefreshToken = null
}

/** 读取当前登录用户态 */
export function getUser(): StoredUser | null {
  const raw = safeGetItem(USER_KEY)
  if (!raw) {
    return null
  }
  try {
    const parsed = JSON.parse(raw) as Partial<StoredUser>
    if (
      typeof parsed === 'object' &&
      parsed !== null &&
      typeof parsed.id === 'number' &&
      typeof parsed.email === 'string'
    ) {
      return {
        id: parsed.id,
        email: parsed.email,
        username: typeof parsed.username === 'string' ? parsed.username : null,
      }
    }
  } catch {
    // 解析失败：当作未登录
  }
  return null
}

/** 写入登录用户态 */
export function setUser(user: StoredUser): void {
  safeSetItem(USER_KEY, JSON.stringify(user))
}

/** 清除登录用户态 */
export function clearUser(): void {
  safeRemoveItem(USER_KEY)
}

/**
 * 同时清空用户 token + refresh token + 用户态
 * （注销 / 401 过期统一入口）。
 */
export function clearAuth(): void {
  clearUserToken()
  clearRefreshToken()
  clearUser()
}
