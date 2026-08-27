/**
 * 应用路由表（任务 #23 新增认证守卫 + /login；task-024 新增 /register 与 hideShell 元数据）。
 *
 * - `/login` / `/register`：公共路由（hideShell），无需受保护守卫；
 * - 其他路由：`beforeEach` 守卫检查 `useAuth().hasUserToken()`，未登录时跳转
 *   `/login?redirect=<原路径>`；
 * - 已登录但访问 `/login` 或 `/register` 时，重定向到 redirect 参数或默认
 *   `DEFAULT_REDIRECT`（避免重复登录/注册）。
 *
 * Phase 1 G1：守卫以 `hasUserToken()`（access_token）为准；refresh_token
 * 不影响路由可达性。
 *
 * 注意：useAuth 是模块单例，导航守卫中调用 hasUserToken() 不会引入循环。
 */
import { createRouter, createWebHistory } from 'vue-router'

import { hasUserToken } from '@/composables/useAuth'
import { DEFAULT_REDIRECT } from '@/composables/useRedirectTarget'

const router = createRouter({
  history: createWebHistory(),
  routes: [
    { path: '/', redirect: '/chat' },
    {
      path: '/login',
      name: 'login',
      component: () => import('@/views/auth/Login.vue'),
      meta: { title: '登录', hideShell: true },
    },
    {
      path: '/register',
      name: 'register',
      component: () => import('@/views/auth/Register.vue'),
      meta: { title: '注册', hideShell: true },
    },
    {
      path: '/chat',
      name: 'chat',
      component: () => import('@/views/chat/ChatView.vue'),
      meta: { title: '对话' },
    },
    {
      path: '/agent',
      name: 'agent',
      component: () => import('@/views/agent/AgentList.vue'),
      meta: { title: 'Agent 管理' },
    },
    {
      path: '/agentapp',
      name: 'agentapp',
      component: () => import('@/views/agent/AgentAppOverview.vue'),
      meta: { title: 'AgentApp 总览' },
    },
    {
      path: '/subagent',
      name: 'subagent',
      component: () => import('@/views/agent/SubAgentList.vue'),
      meta: { title: '子代理管理' },
    },
    {
      path: '/skill',
      name: 'skill',
      component: () => import('@/views/skill/SkillList.vue'),
      meta: { title: '技能管理' },
    },
    {
      path: '/mcp',
      name: 'mcp',
      component: () => import('@/views/mcp/McpList.vue'),
      meta: { title: 'MCP 管理' },
    },
    {
      path: '/llm',
      name: 'llm',
      component: () => import('@/views/provider/ProviderList.vue'),
      meta: { title: '模型管理' },
    },
    {
      path: '/llm/trash',
      name: 'llm-trash',
      component: () => import('@/views/provider/ProviderTrashList.vue'),
      meta: { title: '提供商回收站' },
    },
  ],
})

router.beforeEach((to) => {
  // 公共路由：登录页 + 注册页（均 hideShell）
  if (to.name === 'login' || to.name === 'register') {
    // 已登录用户访问：直接重定向到目标或默认 DEFAULT_REDIRECT
    if (hasUserToken()) {
      const raw = to.query.redirect
      const redirect = typeof raw === 'string' && raw.length > 0 ? raw : DEFAULT_REDIRECT
      return { path: redirect }
    }
    return true
  }
  // 受保护路由：未登录跳 /login，并携带 redirect 用于登录后回跳
  if (!hasUserToken()) {
    return { name: 'login', query: { redirect: to.fullPath } }
  }
  return true
})

export default router
