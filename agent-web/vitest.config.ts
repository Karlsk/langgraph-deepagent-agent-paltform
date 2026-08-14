import { fileURLToPath, URL } from 'node:url'

import { defineConfig } from 'vitest/config'

// 独立于 vite.config.ts：测试无需 vue 插件与 dev server 代理。
// environment 保持 node —— 测试以 vi.mock 掉 element-plus，不做真实渲染，无需 DOM。
export default defineConfig({
  resolve: {
    alias: {
      '@': fileURLToPath(new URL('./src', import.meta.url)),
    },
  },
  test: {
    environment: 'node',
    include: ['tests/**/*.spec.ts'],
  },
})
