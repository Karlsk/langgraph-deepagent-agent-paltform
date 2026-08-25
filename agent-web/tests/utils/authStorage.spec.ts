/**
 * authStorage 本地存储契约（任务 #23 + G1 Auth Phase 1）。
 *
 * 零真实网络：覆盖 set / get / clear、JSON 解析失败回退、键名稳定、
 * SSR / Node 测试场景（localStorage 缺失时回退到内存 Map）。
 *
 * vitest 默认 node 环境不提供 localStorage，用 in-memory stubGlobal 注入；
 * 同步测试 authStorage 的内存 fallback 行为，保证 SSR / 测试期间不抛错。
 *
 * Phase 1 G1 收尾：旧的 ``auth.sessionToken`` / ``getSessionToken /
 * setSessionToken / clearSessionToken`` 与 chatbot runtime 一起废弃，
 * 本测试不再覆盖这些 API。
 *
 * Phase 1 G1 新增契约：
 * - access_token（用户 token）走 localStorage `auth.userToken`；
 * - refresh_token 仅内存态（模块级 inMemoryRefreshToken 变量），
 *   **永不写入 localStorage** —— 验证 setRefreshToken 不会污染 localStorage。
 *
 * 注意：refresh_token 内存态是模块级闭包，跨 it 用例会共享；
 * 使用 beforeEach 显式 clearRefreshToken 避免顺序依赖。
 */
import { beforeEach, describe, expect, it, vi } from 'vitest'

import {
  clearAuth,
  clearRefreshToken,
  clearUserToken,
  getRefreshToken,
  getUserToken,
  setRefreshToken,
  setUserToken,
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
  // 重置模块级内存态 refresh_token（vitest 默认不在每个用例 reset module）
  clearRefreshToken()
})

describe('authStorage 用户 token（access_token）', () => {
  it('set + get：写入并读取同一值（localStorage 持久化）', () => {
    setUserToken('access-abc')
    expect(getUserToken()).toBe('access-abc')
    expect(localStorage.getItem('auth.userToken')).toBe('access-abc')
  })

  it('clear：清空后 get 返回 null 且 localStorage key 移除', () => {
    setUserToken('to-be-cleared')
    clearUserToken()
    expect(getUserToken()).toBeNull()
    expect(localStorage.getItem('auth.userToken')).toBeNull()
  })

  it('空字符串视为合法值（与"未设置"区分）', () => {
    setUserToken('')
    expect(getUserToken()).toBe('')
  })
})

describe('authStorage refresh_token（仅内存态）', () => {
  it('set + get：写入并读取同一值（不写 localStorage）', () => {
    setRefreshToken('refresh-xyz')
    expect(getRefreshToken()).toBe('refresh-xyz')
    // 关键不变量：localStorage 中不得有 refresh token（spec R7）
    expect(localStorage.getItem('auth.refreshToken')).toBeNull()
  })

  it('clear：清空后 get 返回 null', () => {
    setRefreshToken('to-clear')
    expect(getRefreshToken()).toBe('to-clear')
    clearRefreshToken()
    expect(getRefreshToken()).toBeNull()
  })

  it('"页面刷新"等价 clearRefreshToken：内存态丢失，但 userToken 仍在 localStorage', () => {
    setUserToken('still-persisted')
    setRefreshToken('lost-on-reload')
    // 模拟页面刷新：清掉内存态 + 重新 new 一个 localStorage 实例（SSR 同构场景）
    clearRefreshToken()
    expect(getRefreshToken()).toBeNull()
    expect(getUserToken()).toBe('still-persisted')
  })

  it('空字符串视为合法值', () => {
    setRefreshToken('')
    expect(getRefreshToken()).toBe('')
  })
})

describe('authStorage clearAuth', () => {
  it('同时清空用户 token + refresh token（内存态）', () => {
    setUserToken('access-abc')
    setRefreshToken('refresh-xyz')

    clearAuth()

    expect(getUserToken()).toBeNull()
    expect(getRefreshToken()).toBeNull()
    expect(localStorage.getItem('auth.userToken')).toBeNull()
    // 再次验证 refresh_token 不在 localStorage
    expect(localStorage.getItem('auth.refreshToken')).toBeNull()
    // session token key 不再存在（Phase 1 G1 收尾）
    expect(localStorage.getItem('auth.sessionToken')).toBeNull()
  })

  it('refresh_token 仅内存态：clearAuth 后再次写 refresh 不污染 localStorage', () => {
    setRefreshToken('r1')
    clearAuth()
    expect(getRefreshToken()).toBeNull()
    setRefreshToken('r2')
    expect(getRefreshToken()).toBe('r2')
    expect(localStorage.getItem('auth.refreshToken')).toBeNull()
  })
})

describe('authStorage 键名稳定性', () => {
  it('localStorage key 前缀固定为 auth.（避免改名破坏已部署用户的会话）', () => {
    setUserToken('access-k')
    expect(localStorage.getItem('auth.userToken')).toBe('access-k')
    // refresh_token 在 localStorage 中绝不出现
    expect(localStorage.getItem('auth.refreshToken')).toBeNull()
    // session token key 不再写入（Phase 1 G1 收尾）
    expect(localStorage.getItem('auth.sessionToken')).toBeNull()
  })
})

describe('authStorage SSR 兼容（localStorage undefined）', () => {
  it('localStorage 缺失时：get/set/clear 走内存 Map 兜底；refresh_token 仅内存态', () => {
    vi.stubGlobal('localStorage', undefined)
    setUserToken('memory-user-token')
    expect(getUserToken()).toBe('memory-user-token')
    setRefreshToken('memory-refresh-token')
    expect(getRefreshToken()).toBe('memory-refresh-token')
    // clearAuth 在内存态上同样有效
    clearAuth()
    expect(getUserToken()).toBeNull()
    expect(getRefreshToken()).toBeNull()
  })
})
