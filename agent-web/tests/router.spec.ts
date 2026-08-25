import { describe, expect, it, vi } from 'vitest'

/**
 * useAuth 模块单例 mock：守卫依赖 hasUserToken() 返回值；
 * 让每个用例显式注入登录态，避免模块副作用污染。
 *
 * Phase 1 G1：路由守卫以 ``hasUserToken()``（access_token）为准。
 */
const hasUserTokenMock = vi.fn()
vi.mock('@/composables/useAuth', () => ({
  hasUserToken: () => hasUserTokenMock(),
}))

vi.mock('vue-router', async (importOriginal) => {
  const router = await importOriginal<typeof import('vue-router')>()
  return { ...router, createWebHistory: router.createMemoryHistory }
})

import router from '@/router'

describe('控制台导航路由', () => {
  it('暴露登录页与五大业务导航页面', () => {
    const routes = router
      .getRoutes()
      .filter((route) => route.name !== undefined)
      .map((route) => ({ name: route.name, path: route.path }))
      .sort((left, right) => String(left.name).localeCompare(String(right.name)))

    expect(routes).toEqual([
      { name: 'agent', path: '/agent' },
      { name: 'chat', path: '/chat' },
      { name: 'llm', path: '/llm' },
      { name: 'llm-trash', path: '/llm/trash' },
      { name: 'login', path: '/login' },
      { name: 'mcp', path: '/mcp' },
      { name: 'register', path: '/register' },
      { name: 'skill', path: '/skill' },
      { name: 'subagent', path: '/subagent' },
    ])
  })

  it('回收站路由 /llm/trash 挂载 ProviderTrashList 且受认证守卫保护', { timeout: 15_000 }, async () => {
    hasUserTokenMock.mockReturnValue(false)
    await router.push('/llm/trash')
    expect(router.currentRoute.value.name).toBe('login')
    expect(router.currentRoute.value.query.redirect).toBe('/llm/trash')

    hasUserTokenMock.mockReturnValue(true)
    await router.push('/llm/trash')
    expect(router.currentRoute.value.name).toBe('llm-trash')
    expect(router.currentRoute.value.meta.title).toBe('提供商回收站')
  })
})

describe('认证路由守卫', () => {
  it('未登录访问受保护路由（/llm） → 重定向到 /login 并携带 redirect 参数', async () => {
    hasUserTokenMock.mockReturnValue(false)
    await router.push('/llm')
    expect(router.currentRoute.value.name).toBe('login')
    expect(router.currentRoute.value.query.redirect).toBe('/llm')
  })

  it('未登录访问 /login → 正常放行（不走重定向）', async () => {
    hasUserTokenMock.mockReturnValue(false)
    await router.push({ name: 'login' })
    expect(router.currentRoute.value.name).toBe('login')
  })

  it('已登录访问 /login → 重定向到 redirect 或默认 /llm', async () => {
    hasUserTokenMock.mockReturnValue(true)
    await router.push({ name: 'login', query: { redirect: '/agent' } })
    expect(router.currentRoute.value.path).toBe('/agent')

    await router.push({ name: 'login' })
    expect(router.currentRoute.value.path).toBe('/llm')
  })

  it('已登录访问受保护路由 → 正常放行', async () => {
    hasUserTokenMock.mockReturnValue(true)
    await router.push({ name: 'agent' })
    expect(router.currentRoute.value.name).toBe('agent')
  })

  it('未登录访问 /register → 正常放行（不走重定向）', async () => {
    hasUserTokenMock.mockReturnValue(false)
    await router.push({ name: 'register' })
    expect(router.currentRoute.value.name).toBe('register')
  })

  it('已登录访问 /register?redirect=/agent → 重定向到 /agent', async () => {
    hasUserTokenMock.mockReturnValue(true)
    await router.push({ name: 'register', query: { redirect: '/agent' } })
    expect(router.currentRoute.value.name).toBe('agent')
  })

  it('已登录访问 /register → 重定向到默认 /llm', async () => {
    hasUserTokenMock.mockReturnValue(true)
    await router.push({ name: 'register' })
    expect(router.currentRoute.value.name).toBe('llm')
  })
})