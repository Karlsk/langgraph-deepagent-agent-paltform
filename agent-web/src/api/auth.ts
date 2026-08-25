/**
 * 认证 API 模块：对接后端 `/auth/login`、`/auth/register`、`/auth/refresh`、
 * `/auth/logout`（Phase 1 G1 单层 + Refresh Token）。
 *
 * 约定（与 assets.ts / provider.ts 一致）：
 * - 响应信封 {code, message, data} 已由 request.ts 拦截器解包，本模块函数
 *   返回值即 data 载荷；
 * - `login` 用 form 表单（非 JSON），与后端 OAuth2PasswordRequestForm 对齐；
 * - `refreshTokenApi` / `logoutApi` 用 JSON body（{refresh_token}）；
 * - request.ts 请求拦截器自动从 authStorage 读 userToken 并注入
 *   Authorization 头；refresh 拦截器自动处理 401 → /auth/refresh → 重发。
 *
 * 注意：本模块不直接写本地存储，token 持久化由 authStorage.ts 承担，
 * useAuth.ts / Login.vue 串联两端流程。
 *
 * Phase 1 G1 收尾：原 ``/auth/session`` + ``createSession()`` + ``SessionResponse``
 * 双轨认证入口随 chatbot runtime 一起废弃（chatbot 端点不再注册），保留
 * 单层 ``LoginResponse`` + ``register / login / refresh / logout``。
 */
import { post } from '@/utils/request'

/** /auth/refresh + /auth/login + /auth/register 的统一响应形状（Phase 1 G1）。 */
export interface LoginResponse {
  access_token: string
  refresh_token: string
  token_type: string
  expires_at: string
}

/**
 * 用户登录：form 表单提交（注意：FastAPI OAuth2 表单，不是 JSON）。
 * 返回 envelope data 为 ``LoginResponse``（access + refresh 双 token）。
 */
export function login(email: string, password: string): Promise<LoginResponse> {
  const form = new URLSearchParams()
  form.set('email', email)
  form.set('password', password)
  form.set('grant_type', 'password')
  return post<LoginResponse>('/auth/login', form, {
    headers: { 'Content-Type': 'application/x-www-form-urlencoded' },
  })
}

/**
 * 用 refresh_token 旋转出新的 access + refresh 对。
 * 返回 envelope data 为 ``LoginResponse``（新 token 对）。
 * 失败由统一拦截器处理（401 → INVALID_REFRESH_TOKEN / REFRESH_TOKEN_REPLAY 等）。
 */
export function refreshTokenApi(refreshToken: string): Promise<LoginResponse> {
  return post<LoginResponse>('/auth/refresh', { refresh_token: refreshToken })
}

/**
 * 撤销一个 refresh_token（best-effort，幂等）。
 * 返回 envelope data 为 null（data 字段恒为 null）。
 */
export function logoutApi(refreshToken: string): Promise<null> {
  return post<null>('/auth/logout', { refresh_token: refreshToken })
}

/**
 * 用户注册：JSON body，对齐后端 `UserCreate` 契约。
 *
 * - 后端 `POST /auth/register` 返回 `LoginResponse`（Phase 1 G1 对齐 /auth/login）：
 *   access + refresh 双 token 直接下发，省去注册后立即再登录的 round-trip；
 * - `password` 在前端经 `validatePasswordStrength` 预校验后发送，避免 422 往返；
 * - `username` 可选；前端传 null / undefined 时不补充字段（保持接口契约）。
 */
export interface RegisterPayload {
  email: string
  password: string
  username?: string | null
}

export function register(payload: RegisterPayload): Promise<LoginResponse> {
  return post<LoginResponse>('/auth/register', payload)
}
