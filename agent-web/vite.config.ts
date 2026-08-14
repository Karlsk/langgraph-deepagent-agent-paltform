import { fileURLToPath, URL } from 'node:url'

import vue from '@vitejs/plugin-vue'
import { defineConfig, loadEnv } from 'vite'

// https://vite.dev/config/
export default defineConfig(({ mode }) => {
  // loadEnv 第三个参数 '' 表示全量加载（含非 VITE_ 前缀变量）。
  // BACKEND_URL 取值优先级：shell 环境变量 > .env 文件（如 .env.docker）> 默认值，
  // 与 dotenv 惯例一致，因此 process.env 覆盖在 loadEnv 结果之上。
  const fileEnv = loadEnv(mode, process.cwd(), '')
  const env = {
    ...fileEnv,
    ...Object.fromEntries(
      Object.entries(process.env).filter(([, v]) => v !== undefined),
    ),
  }

  return {
    plugins: [vue()],
    resolve: {
      alias: {
        '@': fileURLToPath(new URL('./src', import.meta.url)),
      },
    },
    server: {
      port: 5173,
      proxy: {
        // 后端 API 前缀为 /api/v1（app/core/config.py API_V1_STR），故不做 rewrite
        '/api': {
          target: env.BACKEND_URL || 'http://localhost:8000',
          changeOrigin: true,
        },
      },
    },
  }
})
