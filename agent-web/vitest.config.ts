import { fileURLToPath, URL } from 'node:url'

import vue from '@vitejs/plugin-vue'
import { defineConfig } from 'vitest/config'

// 独立于 vite.config.ts：测试无需 dev server 代理。
// vue 插件仅供组件测试导入 SFC；纯逻辑测试仍以 vi.mock 掉 element-plus，
// environment 保持 node，需要 DOM 的组件测试用文件级 @vitest-environment 指令。
export default defineConfig({
  plugins: [vue()],
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
