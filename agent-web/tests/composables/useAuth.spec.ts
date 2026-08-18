/**
 * useAuth composable 测试（任务 #23）。
 *
 * 零真实网络：mock `@/api/auth` 的 login API、mock `@/utils/authStorage`，
 * 验证 login → sessionToken 流程、模块单例状态共享、bootstrap 恢复、
 * logout 清理、JWT 解码降级路径。
 */
import { beforeEach, describe, expect, it, vi } from 'vitest'

/** 独立 axios 实例 mock：与 request.ts 拦截器解耦，仅承载 /auth/session 调用 */
const authOnlyPost = vi.fn()

vi.mock('axios', () => ({
  default: {
    create: vi.fn(() => ({
      interceptors: {
        response: { use: vi.fn() },
      },
      post: authOnlyPost,
    })),
    isAxiosError: (error: unknown): boolean =>
      typeof error === 'object' &&
      error !== null &&
      (error as { isAxiosError?: unknown }).isAxiosError === true,
  },
}))

const authApiMock = {
  login: vi.fn(),
}

vi.mock('@/api/auth', () => ({
  login: (...args: unknown[]) => authApiMock.login(...args),
}))

const storageMock = {
  clearAuth: vi.fn(),
  getSessionToken: vi.fn(),
  getUser: vi.fn(),
  setSessionToken: vi.fn(),
  setUser: vi.fn(),
}

vi.mock('@/utils/authStorage', () => storageMock)

// 每个用例前重置模块单例 + mock 计数
let useAuthModule: typeof import('@/composables/useAuth')

beforeEach(async () => {
  vi.clearAllMocks()
  storageMock.getSessionToken.mockReturnValue(null)
  storageMock.getUser.mockReturnValue(null)
  // vi.resetModules 确保每个文件重新加载 useAuth 模块（避免 user/sessionToken ref 跨用例污染）
  vi.resetModules()
  useAuthModule = await import('@/composables/useAuth')
})

/** 构造一个伪 JWT：header.payload.signature；payload 中 sub 是数字 */
function fakeJwt(sub: number): string {
  const header = Buffer.from(JSON.stringify({ alg: 'HS256', typ: 'JWT' })).toString('base64url')
  const payload = Buffer.from(JSON.stringify({ sub, exp: 9_999_999_999 })).toString('base64url')
  return `${header}.${payload}.sig`
}

describe('useAuth 启动态', () => {
  it('模块首次加载时 currentUser() / hasSession() 与本地存储一致', async () => {
    storageMock.getSessionToken.mockReturnValue('seeded-token')
    storageMock.getUser.mockReturnValue({ id: 5, email: 'a@b', username: 'a' })
    vi.resetModules()
    const { currentUser, hasSession } = await import('@/composables/useAuth')

    expect(hasSession()).toBe(true)
    expect(currentUser()).toEqual({ id: 5, email: 'a@b', username: 'a' })
  })

  it('bootstrap：从 authStorage 重新同步内存态', () => {
    storageMock.getSessionToken.mockReturnValueOnce('after-bootstrap-token')
    storageMock.getUser.mockReturnValueOnce({ id: 7, email: 'c@d', username: null })

    useAuthModule.bootstrap()

    expect(useAuthModule.hasSession()).toBe(true)
    expect(useAuthModule.currentUser()).toEqual({ id: 7, email: 'c@d', username: null })
  })
})

describe('useAuth login 流程', () => {
  it('登录成功：loginApi 拿用户 token → exchangeSession 拿会话 token → 持久化', async () => {
    const userToken = fakeJwt(42)
    authApiMock.login.mockResolvedValueOnce({
      access_token: userToken,
      token_type: 'bearer',
      expires_at: '2099-01-01T00:00:00Z',
    })
    authOnlyPost.mockResolvedValueOnce({
      session_id: 'sess-1',
      name: 'session-name',
      token: {
        access_token: 'session-token-xyz',
        token_type: 'bearer',
        expires_at: '2099-01-01T00:00:00Z',
      },
    })
    // 登录成功后 useAuth 重新读取 getSessionToken，需要返回写入值
    storageMock.getSessionToken.mockReturnValueOnce('session-token-xyz')
    storageMock.getUser.mockReturnValueOnce({ id: 42, email: 'user@example.com', username: null })

    await useAuthModule.login('user@example.com', 'pw')

    expect(authApiMock.login).toHaveBeenCalledWith('user@example.com', 'pw')
    // exchangeSession 显式带用户 token 的 Authorization 头，避免被 request.ts 拦截器覆盖
    expect(authOnlyPost).toHaveBeenCalledWith('/auth/session', null, {
      headers: { Authorization: `Bearer ${userToken}` },
    })
    expect(storageMock.setSessionToken).toHaveBeenCalledWith('session-token-xyz')
    expect(storageMock.setUser).toHaveBeenCalledWith({
      id: 42,
      email: 'user@example.com',
      username: null,
    })
    expect(useAuthModule.hasSession()).toBe(true)
    expect(useAuthModule.currentUser()).toEqual({
      id: 42,
      email: 'user@example.com',
      username: null,
    })
  })

  it('登录失败（401 等）：不持久化、不污染内存态', async () => {
    const boom = new Error('Incorrect email or password')
    authApiMock.login.mockRejectedValueOnce(boom)

    await expect(useAuthModule.login('x@x', 'wrong')).rejects.toBe(boom)
    expect(storageMock.setSessionToken).not.toHaveBeenCalled()
    expect(storageMock.setUser).not.toHaveBeenCalled()
    expect(useAuthModule.hasSession()).toBe(false)
    expect(useAuthModule.currentUser()).toBeNull()
  })
})

describe('useAuth logout', () => {
  it('清空本地态与内存态', () => {
    storageMock.getSessionToken.mockReturnValueOnce('to-clear')
    storageMock.getUser.mockReturnValueOnce({ id: 1, email: 'x', username: null })
    useAuthModule.bootstrap()
    expect(useAuthModule.hasSession()).toBe(true)

    useAuthModule.logout()

    expect(storageMock.clearAuth).toHaveBeenCalledTimes(1)
    expect(useAuthModule.hasSession()).toBe(false)
    expect(useAuthModule.currentUser()).toBeNull()
  })
})

describe('useAuth JWT 解码降级', () => {
  it('JWT payload 缺少 sub 数字字段时：持久化 user.id = 0（仍走登录成功流程）', async () => {
    const header = Buffer.from(JSON.stringify({ alg: 'HS256' })).toString('base64url')
    const payload = Buffer.from(JSON.stringify({ name: 'no-sub' })).toString('base64url')
    const tokenNoSub = `${header}.${payload}.sig`

    authApiMock.login.mockResolvedValueOnce({
      access_token: tokenNoSub,
      token_type: 'bearer',
      expires_at: '2099-01-01T00:00:00Z',
    })
    authOnlyPost.mockResolvedValueOnce({
      session_id: 'sess-2',
      name: 'session',
      token: {
        access_token: 'session-token',
        token_type: 'bearer',
        expires_at: '2099-01-01T00:00:00Z',
      },
    })
    storageMock.getSessionToken.mockReturnValueOnce('session-token')

    await useAuthModule.login('user@example.com', 'pw')

    expect(storageMock.setUser).toHaveBeenCalledWith({
      id: 0,
      email: 'user@example.com',
      username: null,
    })
  })

  it('JWT 形状非法（仅两段）时：user.id 降级为 0', async () => {
    const header = Buffer.from(JSON.stringify({ alg: 'HS256' })).toString('base64url')
    const payload = Buffer.from(JSON.stringify({ sub: 'not-a-number' })).toString('base64url')
    const tokenShape = `${header}.${payload}` // 缺少签名段

    authApiMock.login.mockResolvedValueOnce({
      access_token: tokenShape,
      token_type: 'bearer',
      expires_at: '2099-01-01T00:00:00Z',
    })
    authOnlyPost.mockResolvedValueOnce({
      session_id: 'sess-3',
      name: 'session',
      token: {
        access_token: 'session-token',
        token_type: 'bearer',
        expires_at: '2099-01-01T00:00:00Z',
      },
    })
    storageMock.getSessionToken.mockReturnValueOnce('session-token')

    await useAuthModule.login('user@example.com', 'pw')

    expect(storageMock.setUser).toHaveBeenCalledWith({
      id: 0,
      email: 'user@example.com',
      username: null,
    })
  })
})