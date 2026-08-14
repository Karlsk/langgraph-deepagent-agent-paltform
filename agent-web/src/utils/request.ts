import axios from 'axios'
import { ElMessage } from 'element-plus'

/**
 * 统一 axios 实例：后端 API 前缀为 /api/v1（开发态经 Vite 代理转发，无 rewrite）。
 */
const request = axios.create({
  baseURL: '/api/v1',
  timeout: 15000,
})

request.interceptors.request.use((config) => {
  // TODO(token 注入): 骨架占位 —— 待接入认证体系后注入请求头，
  // 例如: config.headers.Authorization = `Bearer ${token}`
  return config
})

request.interceptors.response.use(
  (response) => response.data,
  (error: unknown) => {
    if (axios.isAxiosError(error)) {
      const status = error.response?.status
      if (status === 401) {
        // TODO(401 处理): 骨架占位 —— 待接入登录态后处理过期跳转 / 清理会话
      }
      // FastAPI 契约：HTTPException 返回 { detail }，RequestValidationError 返回 { detail, errors }
      const data = error.response?.data as
        | { detail?: string; message?: string }
        | undefined
      const message =
        data?.detail ?? data?.message ?? error.message ?? '请求失败'
      ElMessage.error(message)
    } else {
      ElMessage.error('请求失败')
    }
    return Promise.reject(error)
  },
)

export default request
