/**
 * useAuth composable 测试（任务 #23 + G1 Auth Phase 1）。
 *
 * 零真实网络：mock `@/api/auth` 的所有认证 API、mock `@/utils/authStorage`，
 * 验证 Phase 1 G1 流程：
 *   - login → userToken 持久化 + refreshToken 内存态 + setUser；
 *   - refreshUserToken 主动旋转：成功更新本地态、失败清空全部；
 *   - logout 调用 logoutApi + 本地清空（best-effort）；
 *   - bootstrap 恢复本地态、JWT 解码降级路径。
 *
 * 注意：useAuth.login 通过 `await import('@/api/auth')` 动态导入（避免与
 * request.ts 的循环依赖），authStorage 仍走静态 import；
 * vi.mock 工厂对静态 + 动态 import 同时生效。
 */
import { beforeEach, describe, expect, it, vi } from 'vitest'

const authApiMock = {
  login: vi.fn(),
  refreshTokenApi: vi.fn(),
  logoutApi: vi.fn(),
}

vi.mock('@/api/auth', () => ({
  login: (...args: unknown[]) => authApiMock.login(...args),
  refreshTokenApi: (...args: unknown[]) => authApiMock.refreshTokenApi(...args),
  logoutApi: (...args: unknown[]) => authApiMock.logoutApi(...args),
}))

const storageMock = {
  clearAuth: vi.fn(),
  getRefreshToken: vi.fn(),
  getUser: vi.fn(),
  getUserToken: vi.fn(),
  setRefreshToken: vi.fn(),
  setUser: vi.fn(),
  setUserToken: vi.fn(),
}

vi.mock('@/utils/authStorage', () => storageMock)

// 每个用例前重置模块单例 + mock 计数
let useAuthModule: typeof import('@/composables/useAuth')

beforeEach(async () => {
  vi.clearAllMocks()
  storageMock.getUserToken.mockReturnValue(null)
  storageMock.getUser.mockReturnValue(null)
  storageMock.getRefreshToken.mockReturnValue(null)
  // vi.resetModules 确保每个文件重新加载 useAuth 模块（避免 user/userToken ref 跨用例污染）
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
  it('模块首次加载时 currentUser() / hasUserToken() 与本地存储一致', async () => {
    storageMock.getUserToken.mockReturnValue('seeded-access')
    storageMock.getUser.mockReturnValue({ id: 5, email: 'a@b', username: 'a' })
    vi.resetModules()
    const { currentUser, hasUserToken } = await import('@/composables/useAuth')

    expect(hasUserToken()).toBe(true)
    expect(currentUser()).toEqual({ id: 5, email: 'a@b', username: 'a' })
  })

  it('bootstrap：从 authStorage 重新同步内存态', () => {
    storageMock.getUserToken.mockReturnValueOnce('after-bootstrap-access')
    storageMock.getUser.mockReturnValueOnce({ id: 7, email: 'c@d', username: null })

    useAuthModule.bootstrap()

    expect(useAuthModule.hasUserToken()).toBe(true)
    expect(useAuthModule.currentUser()).toEqual({ id: 7, email: 'c@d', username: null })
  })
})

describe('useAuth login 流程（Phase 1 G1 双 token）', () => {
  it('登录成功：loginApi 拿 access + refresh → 持久化 userToken + 内存态 refreshToken + setUser', async () => {
    const accessToken = fakeJwt(42)
    authApiMock.login.mockResolvedValueOnce({
      access_token: accessToken,
      refresh_token: 'raw-refresh-token-1',
      token_type: 'bearer',
      expires_at: '2099-01-01T00:00:00Z',
    })
    // 登录成功后 useAuth 重新读取 getUserToken + getUser
    storageMock.getUserToken.mockReturnValueOnce(accessToken)
    storageMock.getUser.mockReturnValueOnce({ id: 42, email: 'user@example.com', username: null })

    await useAuthModule.login('user@example.com', 'pw')

    expect(authApiMock.login).toHaveBeenCalledWith('user@example.com', 'pw')
    // access_token 持久化到 localStorage
    expect(storageMock.setUserToken).toHaveBeenCalledWith(accessToken)
    // refresh_token 仅内存态（无 localStorage 写入）
    expect(storageMock.setRefreshToken).toHaveBeenCalledWith('raw-refresh-token-1')
    expect(storageMock.setUser).toHaveBeenCalledWith({
      id: 42,
      email: 'user@example.com',
      username: null,
    })
    expect(useAuthModule.hasUserToken()).toBe(true)
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
    expect(storageMock.setUserToken).not.toHaveBeenCalled()
    expect(storageMock.setRefreshToken).not.toHaveBeenCalled()
    expect(storageMock.setUser).not.toHaveBeenCalled()
    expect(useAuthModule.hasUserToken()).toBe(false)
    expect(useAuthModule.currentUser()).toBeNull()
  })
})

describe('useAuth refreshUserToken 主动旋转', () => {
  it('成功：refreshTokenApi 拿到新 token 对 → 更新本地态 → 返回新 access_token', async () => {
    storageMock.getRefreshToken.mockReturnValueOnce('old-refresh')
    const newAccess = fakeJwt(42)
    authApiMock.refreshTokenApi.mockResolvedValueOnce({
      access_token: newAccess,
      refresh_token: 'new-refresh',
      token_type: 'bearer',
      expires_at: '2099-02-01T00:00:00Z',
    })
    // 旋转成功后 useAuth 重新读 userToken
    storageMock.getUserToken.mockReturnValueOnce(newAccess)

    const result = await useAuthModule.refreshUserToken()

    expect(result).toBe(newAccess)
    expect(authApiMock.refreshTokenApi).toHaveBeenCalledWith('old-refresh')
    expect(storageMock.setUserToken).toHaveBeenCalledWith(newAccess)
    expect(storageMock.setRefreshToken).toHaveBeenCalledWith('new-refresh')
    expect(useAuthModule.hasUserToken()).toBe(true)
  })

  it('失败：refresh API 抛错 → clearAuth + 内存态清空 + 返回 null', async () => {
    storageMock.getRefreshToken.mockReturnValueOnce('stale-refresh')
    authApiMock.refreshTokenApi.mockRejectedValueOnce(new Error('INVALID_REFRESH_TOKEN'))

    const result = await useAuthModule.refreshUserToken()

    expect(result).toBeNull()
    expect(storageMock.clearAuth).toHaveBeenCalledTimes(1)
    expect(useAuthModule.hasUserToken()).toBe(false)
    expect(useAuthModule.currentUser()).toBeNull()
  })

  it('无 refresh_token 时：直接返回 null（不调 API、不报错）', async () => {
    storageMock.getRefreshToken.mockReturnValueOnce(null)

    const result = await useAuthModule.refreshUserToken()

    expect(result).toBeNull()
    expect(authApiMock.refreshTokenApi).not.toHaveBeenCalled()
    expect(storageMock.clearAuth).not.toHaveBeenCalled()
  })
})

describe('useAuth logout', () => {
  it('有 refresh_token：logoutApi 撤销 + 本地清空', async () => {
    storageMock.getRefreshToken.mockReturnValueOnce('to-revoke')
    authApiMock.logoutApi.mockResolvedValueOnce(null)

    await useAuthModule.logout()

    expect(authApiMock.logoutApi).toHaveBeenCalledWith('to-revoke')
    expect(storageMock.clearAuth).toHaveBeenCalledTimes(1)
    expect(useAuthModule.hasUserToken()).toBe(false)
    expect(useAuthModule.currentUser()).toBeNull()
  })

  it('无 refresh_token：跳过 logoutApi，直接本地清空', async () => {
    storageMock.getRefreshToken.mockReturnValueOnce(null)

    await useAuthModule.logout()

    expect(authApiMock.logoutApi).not.toHaveBeenCalled()
    expect(storageMock.clearAuth).toHaveBeenCalledTimes(1)
  })

  it('logoutApi 失败（网络抖动）：仍本地清空（best-effort）', async () => {
    storageMock.getRefreshToken.mockReturnValueOnce('to-revoke')
    authApiMock.logoutApi.mockRejectedValueOnce(new Error('network error'))

    await expect(useAuthModule.logout()).resolves.toBeUndefined()
    expect(storageMock.clearAuth).toHaveBeenCalledTimes(1)
    expect(useAuthModule.hasUserToken()).toBe(false)
  })
})

describe('useAuth JWT 解码降级', () => {
  it('JWT payload 缺少 sub 数字字段时：持久化 user.id = 0（仍走登录成功流程）', async () => {
    const header = Buffer.from(JSON.stringify({ alg: 'HS256' })).toString('base64url')
    const payload = Buffer.from(JSON.stringify({ name: 'no-sub' })).toString('base64url')
    const tokenNoSub = `${header}.${payload}.sig`

    authApiMock.login.mockResolvedValueOnce({
      access_token: tokenNoSub,
      refresh_token: 'refresh-no-sub',
      token_type: 'bearer',
      expires_at: '2099-01-01T00:00:00Z',
    })
    storageMock.getUserToken.mockReturnValueOnce(tokenNoSub)
    storageMock.getUser.mockReturnValueOnce({ id: 0, email: 'user@example.com', username: null })

    await useAuthModule.login('user@example.com', 'pw')

    expect(storageMock.setUser).toHaveBeenCalledWith({
      id: 0,
      email: 'user@example.com',
      username: null,
    })
  })

  it('JWT payload.sub 为字符串数字（后端传 string sub）时：正确解析为 number', async () => {
    const header = Buffer.from(JSON.stringify({ alg: 'HS256' })).toString('base64url')
    const payload = Buffer.from(JSON.stringify({ sub: '42' })).toString('base64url')
    const tokenStringSub = `${header}.${payload}.sig`

    authApiMock.login.mockResolvedValueOnce({
      access_token: tokenStringSub,
      refresh_token: 'refresh-string-sub',
      token_type: 'bearer',
      expires_at: '2099-01-01T00:00:00Z',
    })
    storageMock.getUserToken.mockReturnValueOnce(tokenStringSub)
    storageMock.getUser.mockReturnValueOnce({ id: 42, email: 'user@example.com', username: null })

    await useAuthModule.login('user@example.com', 'pw')

    expect(storageMock.setUser).toHaveBeenCalledWith({
      id: 42,
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
      refresh_token: 'refresh-bad-shape',
      token_type: 'bearer',
      expires_at: '2099-01-01T00:00:00Z',
    })
    storageMock.getUserToken.mockReturnValueOnce(tokenShape)
    storageMock.getUser.mockReturnValueOnce({ id: 0, email: 'user@example.com', username: null })

    await useAuthModule.login('user@example.com', 'pw')

    expect(storageMock.setUser).toHaveBeenCalledWith({
      id: 0,
      email: 'user@example.com',
      username: null,
    })
  })
})