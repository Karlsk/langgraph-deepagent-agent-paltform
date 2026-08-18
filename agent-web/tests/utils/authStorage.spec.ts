/**
 * authStorage 本地存储契约（任务 #23）。
 *
 * 零真实网络：覆盖 set / get / clear、JSON 解析失败回退、键名稳定、
 * SSR / Node 测试场景（localStorage 缺失时回退到内存 Map）。
 *
 * vitest 默认 node 环境不提供 localStorage，用 in-memory stubGlobal 注入；
 * 同步测试 authStorage 的内存 fallback 行为，保证 SSR / 测试期间不抛错。
 */
import { beforeEach, describe, expect, it, vi } from 'vitest'

import {
  clearAuth,
  clearSessionToken,
  clearUser,
  getSessionToken,
  getUser,
  setSessionToken,
  setUser,
} from '@/utils/authStorage'

/** in-memory localStorage 桩：覆盖 Storage 接口最小子集 */
function createLocalStorageStub(): Storage {
  const store = new Map<string, string>()
  return {
    get length() {
      return store.size
    },
    clear() {
      store.clear()
    },
    getItem(key: string) {
      return store.get(key) ?? null
    },
    key(index: number) {
      return Array.from(store.keys())[index] ?? null
    },
    removeItem(key: string) {
      store.delete(key)
    },
    setItem(key: string, value: string) {
      store.set(key, value)
    },
  }
}

beforeEach(() => {
  vi.stubGlobal('localStorage', createLocalStorageStub())
})

describe('authStorage 会话 token', () => {
  it('set + get：写入并读取同一值', () => {
    setSessionToken('session-abc')
    expect(getSessionToken()).toBe('session-abc')
    expect(localStorage.getItem('auth.sessionToken')).toBe('session-abc')
  })

  it('clear：清空后 get 返回 null 且 localStorage key 移除', () => {
    setSessionToken('to-be-cleared')
    clearSessionToken()
    expect(getSessionToken()).toBeNull()
    expect(localStorage.getItem('auth.sessionToken')).toBeNull()
  })

  it('空字符串视为合法值（与"未设置"区分）', () => {
    setSessionToken('')
    expect(getSessionToken()).toBe('')
  })
})

describe('authStorage 登录用户态', () => {
  it('set + get：序列化 JSON 后还原', () => {
    setUser({ id: 42, email: 'user@example.com', username: 'alice' })
    expect(getUser()).toEqual({ id: 42, email: 'user@example.com', username: 'alice' })
  })

  it('username 为 null 时正常持久化与还原', () => {
    setUser({ id: 7, email: 'b@example.com', username: null })
    expect(getUser()).toEqual({ id: 7, email: 'b@example.com', username: null })
  })

  it('JSON 解析失败时回退为 null（不抛错）', () => {
    localStorage.setItem('auth.user', '{not-json')
    expect(getUser()).toBeNull()
  })

  it('字段缺失时（id / email 不存在）回退为 null', () => {
    localStorage.setItem('auth.user', JSON.stringify({ username: 'no-id' }))
    expect(getUser()).toBeNull()
  })

  it('字段类型错误（id 非 number）时回退为 null', () => {
    localStorage.setItem(
      'auth.user',
      JSON.stringify({ id: '42', email: 'x@example.com', username: null }),
    )
    expect(getUser()).toBeNull()
  })

  it('clearUser：清空后 get 返回 null', () => {
    setUser({ id: 1, email: 'a@a', username: null })
    clearUser()
    expect(getUser()).toBeNull()
  })
})

describe('authStorage clearAuth', () => {
  it('同时清空会话 token 与用户态', () => {
    setSessionToken('session-xyz')
    setUser({ id: 1, email: 'x@y.com', username: 'x' })

    clearAuth()

    expect(getSessionToken()).toBeNull()
    expect(getUser()).toBeNull()
    expect(localStorage.getItem('auth.sessionToken')).toBeNull()
    expect(localStorage.getItem('auth.user')).toBeNull()
  })
})

describe('authStorage 键名稳定性', () => {
  it('localStorage key 前缀固定为 auth.（避免改名破坏已部署用户的会话）', () => {
    setSessionToken('k')
    expect(localStorage.getItem('auth.sessionToken')).toBe('k')
    setUser({ id: 1, email: 'a@b', username: null })
    expect(localStorage.getItem('auth.user')).not.toBeNull()
  })
})

describe('authStorage SSR 兼容（localStorage undefined）', () => {
  it('localStorage 缺失时：get/set/clear 走内存 Map 兜底', () => {
    vi.stubGlobal('localStorage', undefined)
    // 触发内存兜底写入
    setSessionToken('memory-token')
    expect(getSessionToken()).toBe('memory-token')
    setUser({ id: 9, email: 'ssr@example.com', username: null })
    expect(getUser()).toEqual({ id: 9, email: 'ssr@example.com', username: null })
    // clearAuth 在内存态上同样有效
    clearAuth()
    expect(getSessionToken()).toBeNull()
    expect(getUser()).toBeNull()
  })
})