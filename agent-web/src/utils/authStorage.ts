/**
 * 认证态本地存储：会话 token 与登录用户信息。
 *
 * 设计要点：
 * - 会话 token 来自 `/auth/session`，所有受保护端点（/providers、/skills 等）
 *   都必须使用；由 `request.ts` 拦截器统一读取并写入请求头；
 * - 用户信息（id / email / username）来自 `/auth/login` 的 UserResponse，
 *   供 App.vue 顶栏展示；保留在内存与 localStorage，确保刷新页面后仍可见。
 * - 单一 key 前缀 `auth.`，JSON 序列化失败/字段缺失走兜底值；SSR / 测试环境
 *   没有 localStorage 时回退为 noop。
 *
 * 风险点：localStorage 仅在浏览器存在，本模块通过惰性判断在加载阶段不抛错，
 * 供 main.ts 启动时安全调用。
 */

const SESSION_TOKEN_KEY = 'auth.sessionToken'
const USER_KEY = 'auth.user'

/** 内存 fallback：localStorage 不可用（如 SSR / Node 测试）时使用 */
const memoryStore: Map<string, string> = new Map()

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

/** 读取当前会话 token（供 request.ts 拦截器使用） */
export function getSessionToken(): string | null {
  return safeGetItem(SESSION_TOKEN_KEY)
}

/** 写入会话 token（登录成功或 refresh 时调用） */
export function setSessionToken(token: string): void {
  safeSetItem(SESSION_TOKEN_KEY, token)
}

/** 清除会话 token（注销 / 401 过期） */
export function clearSessionToken(): void {
  safeRemoveItem(SESSION_TOKEN_KEY)
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

/** 同时清空会话 token 与用户态（注销 / 401 过期统一入口） */
export function clearAuth(): void {
  clearSessionToken()
  clearUser()
}