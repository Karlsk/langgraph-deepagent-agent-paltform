import { describe, expect, it, vi } from 'vitest'

vi.mock('vue-router', async (importOriginal) => {
  const router = await importOriginal<typeof import('vue-router')>()
  return { ...router, createWebHistory: router.createMemoryHistory }
})

import router from '@/router'

describe('控制台导航路由', () => {
  it('暴露对话、Agent、技能、MCP 和模型管理五个导航页面', () => {
    const routes = router
      .getRoutes()
      .filter((route) => route.name !== undefined)
      .map((route) => ({ name: route.name, path: route.path }))
      .sort((left, right) => String(left.name).localeCompare(String(right.name)))

    expect(routes).toEqual([
      { name: 'agent', path: '/agent' },
      { name: 'chat', path: '/chat' },
      { name: 'llm', path: '/llm' },
      { name: 'mcp', path: '/mcp' },
      { name: 'skill', path: '/skill' },
    ])
  })
})
