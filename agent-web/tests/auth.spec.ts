/**
 * src/api/auth.ts 模块测试（task-024）。
 *
 * 覆盖 register() 函数：成功调用 POST /auth/register 并返回解包后 data；
 * 后端 422 / 400 / 网络错误抛错由调用方处理（与现有统一拦截器约定一致）。
 *
 * 零真实网络：mock @/utils/request 的 post。
 */
import { beforeEach, describe, expect, it, vi } from 'vitest'

const postMock = vi.fn()

vi.mock('@/utils/request', () => ({
  post: (...args: unknown[]) => postMock(...args),
}))

import { register, type RegisterPayload } from '@/api/auth'
import type { UserResponse } from '@/api/auth'

const SAMPLE_PAYLOAD: RegisterPayload = {
  email: 'new@example.com',
  password: 'Ab1!xyzwa',
  username: 'new-user',
}

const SAMPLE_RESPONSE: UserResponse = {
  id: 99,
  email: 'new@example.com',
  username: 'new-user',
  token: {
    access_token: 'token-abc',
    token_type: 'bearer',
    expires_at: '2099-01-01T00:00:00Z',
  },
}

beforeEach(() => {
  postMock.mockReset()
})

describe('auth.register 注册 API', () => {
  it('成功：POST /auth/register，body 含 email/password/username，返回解包后 data', async () => {
    postMock.mockResolvedValueOnce(SAMPLE_RESPONSE)

    const result = await register(SAMPLE_PAYLOAD)

    expect(postMock).toHaveBeenCalledTimes(1)
    expect(postMock).toHaveBeenCalledWith('/auth/register', SAMPLE_PAYLOAD)
    expect(result).toEqual(SAMPLE_RESPONSE)
  })

  it('可选字段 username 为 undefined 时：原样传入（不补充为 null）', async () => {
    postMock.mockResolvedValueOnce({ ...SAMPLE_RESPONSE, username: null })

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