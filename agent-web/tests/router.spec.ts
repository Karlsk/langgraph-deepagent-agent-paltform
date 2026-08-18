import { describe, expect, it, vi } from 'vitest'

/**
 * useAuth 模块单例 mock：守卫依赖 hasSession() 返回值；
 * 让每个用例显式注入登录态，避免模块副作用污染。
 */
const hasSessionMock = vi.fn()
vi.mock('@/composables/useAuth', () => ({
  hasSession: () => hasSessionMock(),
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
      { name: 'login', path: '/login' },
      { name: 'mcp', path: '/mcp' },
      { name: 'skill', path: '/skill' },
    ])
  })
})

describe('认证路由守卫', () => {
  it('未登录访问受保护路由（/llm） → 重定向到 /login 并携带 redirect 参数', async () => {
    hasSessionMock.mockReturnValue(false)
    await router.push('/llm')
    expect(router.currentRoute.value.name).toBe('login')
    expect(router.currentRoute.value.query.redirect).toBe('/llm')
  })

  it('未登录访问 /login → 正常放行（不走重定向）', async () => {
    hasSessionMock.mockReturnValue(false)
    await router.push({ name: 'login' })
    expect(router.currentRoute.value.name).toBe('login')
  })

  it('已登录访问 /login → 重定向到 redirect 或默认 /llm', async () => {
    hasSessionMock.mockReturnValue(true)
    await router.push({ name: 'login', query: { redirect: '/agent' } })
    expect(router.currentRoute.value.path).toBe('/agent')

    await router.push({ name: 'login' })
    expect(router.currentRoute.value.path).toBe('/llm')
  })

  it('已登录访问受保护路由 → 正常放行', async () => {
    hasSessionMock.mockReturnValue(true)
    await router.push({ name: 'agent' })
    expect(router.currentRoute.value.name).toBe('agent')
  })
})