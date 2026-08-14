import { createRouter, createWebHistory } from 'vue-router'

const router = createRouter({
  history: createWebHistory(),
  routes: [
    { path: '/', redirect: '/chat' },
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
      meta: { title: '模型服务' },
    },
  ],
})

export default router
