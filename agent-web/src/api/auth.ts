/**
 * 认证 API 模块：对接后端 `/auth/login`（form 表单）、`/auth/session`（Bearer）。
 *
 * 约定（与 assets.ts / provider.ts 一致）：
 * - 响应信封 {code, message, data} 已由 request.ts 拦截器解包，本模块函数
 *   返回值即 data 载荷；
 * - `login` 用 form 表单（非 JSON），与后端 OAuth2PasswordRequestForm 对齐；
 * - `createSession` 用用户 token 换取会话 token，后续受保护资源端点
 *   （/providers、/skills、/apps、/mcp-servers、/tools、/chatbot）必须携带
 *   会话 token（由 request.ts 请求拦截器注入 Authorization 头）。
 *
 * 注意：本模块不直接写本地存储，token 持久化由 authStorage.ts 承担，
 * Login.vue / useAuth.ts 串联两端流程。
 */
import { post } from '@/utils/request'

/** /auth/login 返回的 TokenResponse */
export interface TokenResponse {
  access_token: string
  token_type: string
  expires_at: string
}

/** /auth/register / /auth/login 返回的 UserResponse（含嵌套 token） */
export interface UserResponse {
  id: number
  email: string
  username: string | null
  token: TokenResponse
}

/** /auth/session 返回的 SessionResponse */
export interface SessionResponse {
  session_id: string
  name: string
  token: TokenResponse
}

/**
 * 用户登录：form 表单提交（注意：FastAPI OAuth2 表单，不是 JSON）。
 * 返回 envelope data 为 TokenResponse（用户 token）。
 */
export function login(email: string, password: string): Promise<TokenResponse> {
  const form = new URLSearchParams()
  form.set('email', email)
  form.set('password', password)
  form.set('grant_type', 'password')
  return post<TokenResponse>('/auth/login', form, {
    headers: { 'Content-Type': 'application/x-www-form-urlencoded' },
  })
}

/**
 * 用用户 token 换取会话 token。
 * 返回 envelope data 为 SessionResponse（session_id / name / token）。
 * 后续受保护资源（/providers、/skills、/apps、/mcp-servers、/tools、/chatbot）
 * 都必须使用会话 token。
 */
export function createSession(): Promise<SessionResponse> {
  return post<SessionResponse>('/auth/session')
}

/**
 * 用户注册：JSON body，对齐后端 `UserCreate` 契约（task-024）。
 *
 * - 后端 `POST /auth/register` 返回 `UserResponse`（含嵌套 token，但本任务不消费 token，
 *   由用户后续手动登录拿会话 token）；
 * - `password` 在前端经 `validatePasswordStrength` 预校验后发送，避免 422 往返；
 * - `username` 可选；前端传 null / undefined 时不补充字段（保持接口契约）。
 */
export interface RegisterPayload {
  email: string
  password: string
  username?: string | null
}

export function register(payload: RegisterPayload): Promise<UserResponse> {
  return post<UserResponse>('/auth/register', payload)
}