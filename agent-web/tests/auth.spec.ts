/**
 * src/api/auth.ts 模块测试（task-024 + G1 Auth Phase 1）。
 *
 * 覆盖 register() / login() / refreshTokenApi() / logoutApi() 四个端点：
 *  - register: 注册成功后端返回 LoginResponse（access + refresh 双 token）；
 *  - login: 表单请求 + LoginResponse；
 *  - refreshTokenApi: POST /auth/refresh + {refresh_token}；
 *  - logoutApi: POST /auth/logout + {refresh_token}（best-effort 幂等）。
 *
 * 后端 422 / 400 / 网络错误抛错由调用方处理（与现有统一拦截器约定一致）。
 *
 * 零真实网络：mock @/utils/request 的 post。
 */
import { beforeEach, describe, expect, it, vi } from 'vitest'

const postMock = vi.fn()

vi.mock('@/utils/request', () => ({
  post: (...args: unknown[]) => postMock(...args),
}))

import {
  login,
  logoutApi,
  refreshTokenApi,
  register,
  type LoginResponse,
  type RegisterPayload,
} from '@/api/auth'

const SAMPLE_PAYLOAD: RegisterPayload = {
  email: 'new@example.com',
  password: 'Ab1!xyzwa',
  username: 'new-user',
}

/** Phase 1 G1 登录/注册统一响应：access_token + refresh_token。 */
const SAMPLE_LOGIN: LoginResponse = {
  access_token: 'jwt-access-abc',
  refresh_token: 'raw-refresh-xyz',
  token_type: 'bearer',
  expires_at: '2099-01-01T00:00:00Z',
}

beforeEach(() => {
  postMock.mockReset()
})

describe('auth.register 注册 API', () => {
  it('成功：POST /auth/register，body 含 email/password/username，返回 LoginResponse', async () => {
    postMock.mockResolvedValueOnce(SAMPLE_LOGIN)

    const result = await register(SAMPLE_PAYLOAD)

    expect(postMock).toHaveBeenCalledTimes(1)
    expect(postMock).toHaveBeenCalledWith('/auth/register', SAMPLE_PAYLOAD)
    expect(result).toEqual(SAMPLE_LOGIN)
    expect(result.refresh_token).toBe('raw-refresh-xyz')
  })

  it('可选字段 username 为 undefined 时：原样传入（不补充为 null）', async () => {
    postMock.mockResolvedValueOnce(SAMPLE_LOGIN)

    await register({ email: SAMPLE_PAYLOAD.email, password: SAMPLE_PAYLOAD.password })

    const [, payloadArg] = postMock.mock.calls[0] ?? []
    expect(payloadArg).toEqual({ email: SAMPLE_PAYLOAD.email, password: SAMPLE_PAYLOAD.password })
    expect((payloadArg as RegisterPayload).username).toBeUndefined()
  })

  it('后端 422 校验失败：post reject 后 register 抛错', async () => {
    const boom = new Error('Password must contain at least one uppercase letter')
    postMock.mockRejectedValueOnce(boom)

    await expect(register(SAMPLE_PAYLOAD)).rejects.toBe(boom)
  })

  it('后端 400 邮箱已注册：post reject 后 register 抛错', async () => {
    const boom = new Error('Email already registered')
    postMock.mockRejectedValueOnce(boom)

    await expect(register(SAMPLE_PAYLOAD)).rejects.toBe(boom)
  })
})

describe('auth.login 登录 API', () => {
  it('成功：以 form 表单提交 POST /auth/login，返回 LoginResponse', async () => {
    postMock.mockResolvedValueOnce(SAMPLE_LOGIN)

    const result = await login('user@example.com', 'pw')

    expect(postMock).toHaveBeenCalledTimes(1)
    const [url, body, config] = postMock.mock.calls[0] ?? []
    expect(url).toBe('/auth/login')
    // body 必须是 URLSearchParams（FastAPI OAuth2PasswordRequestForm 期望）
    expect(body).toBeInstanceOf(URLSearchParams)
    expect((body as URLSearchParams).get('email')).toBe('user@example.com')
    expect((body as URLSearchParams).get('password')).toBe('pw')
    expect((body as URLSearchParams).get('grant_type')).toBe('password')
    expect((config as Record<string, unknown>).headers).toMatchObject({
      'Content-Type': 'application/x-www-form-urlencoded',
    })
    expect(result).toEqual(SAMPLE_LOGIN)
    expect(result.access_token).toBe('jwt-access-abc')
    expect(result.refresh_token).toBe('raw-refresh-xyz')
  })

  it('后端 401（密码错误）：post reject 后 login 抛错', async () => {
    const boom = new Error('Incorrect email or password')
    postMock.mockRejectedValueOnce(boom)

    await expect(login('user@example.com', 'wrong')).rejects.toBe(boom)
  })
})

describe('auth.refreshTokenApi 刷新 token', () => {
  it('成功：POST /auth/refresh + {refresh_token}，返回新 LoginResponse', async () => {
    const rotated: LoginResponse = {
      access_token: 'jwt-access-rotated',
      refresh_token: 'raw-refresh-rotated',
      token_type: 'bearer',
      expires_at: '2099-02-01T00:00:00Z',
    }
    postMock.mockResolvedValueOnce(rotated)

    const result = await refreshTokenApi('old-refresh-token')

    expect(postMock).toHaveBeenCalledTimes(1)
    expect(postMock).toHaveBeenCalledWith('/auth/refresh', {
      refresh_token: 'old-refresh-token',
    })
    expect(result).toEqual(rotated)
  })

  it('后端 401（INVALID_REFRESH_TOKEN / REPLAY）：post reject 后抛错', async () => {
    const boom = new Error('INVALID_REFRESH_TOKEN')
    postMock.mockRejectedValueOnce(boom)

    await expect(refreshTokenApi('unknown-token')).rejects.toBe(boom)
  })

  it('后端 429（限流）：post reject 后抛错（由上层拦截器统一处理）', async () => {
    const boom = new Error('Rate limit exceeded')
    postMock.mockRejectedValueOnce(boom)

    await expect(refreshTokenApi('any-token')).rejects.toBe(boom)
  })
})

describe('auth.logoutApi 注销 refresh token', () => {
  it('成功：POST /auth/logout + {refresh_token}，返回 null（best-effort 幂等）', async () => {
    postMock.mockResolvedValueOnce(null)

    const result = await logoutApi('to-revoke-refresh')

    expect(postMock).toHaveBeenCalledTimes(1)
    expect(postMock).toHaveBeenCalledWith('/auth/logout', {
      refresh_token: 'to-revoke-refresh',
    })
    expect(result).toBeNull()
  })

  it('后端错误（网络抖动）：post reject 后仍抛错（best-effort 由 useAuth.logout 兜底）', async () => {
    const boom = new Error('network error')
    postMock.mockRejectedValueOnce(boom)

    await expect(logoutApi('token')).rejects.toBe(boom)
  })
})