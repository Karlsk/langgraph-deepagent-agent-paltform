/**
 * src/utils/request.ts 统一请求层运行时测试（任务 #16）。
 *
 * 零真实网络：mock axios 实例（以受控 adapter 驱动响应拦截器），
 * mock element-plus 的 ElMessage（不做真实渲染，故无需 DOM 环境）。
 */
import { beforeEach, describe, expect, it, vi } from 'vitest'

import { ElMessage } from 'element-plus'
import { getHealth } from '@/api/health'
import { del, get, post, put } from '@/utils/request'

vi.mock('@/utils/authStorage', () => ({
  getSessionToken: vi.fn(),
  setSessionToken: vi.fn(),
  clearSessionToken: vi.fn(),
  clearAuth: vi.fn(),
}))

type ResponseHandler = (response: unknown) => unknown
type ErrorHandler = (error: unknown) => unknown
type Adapter = (config: Record<string, unknown>) => Promise<unknown>

interface FakeAxiosInstance {
  interceptors: {
    request: { use: ReturnType<typeof vi.fn> }
    response: { use: ReturnType<typeof vi.fn> }
  }
  get: ReturnType<typeof vi.fn>
  post: ReturnType<typeof vi.fn>
  put: ReturnType<typeof vi.fn>
  delete: ReturnType<typeof vi.fn>
  adapter: Adapter | undefined
}

const fakeInstance = vi.hoisted<FakeAxiosInstance>(() => ({
  interceptors: {
    request: { use: vi.fn() },
    response: { use: vi.fn() },
  },
  get: vi.fn(),
  post: vi.fn(),
  put: vi.fn(),
  delete: vi.fn(),
  adapter: undefined,
}))

vi.mock('axios', () => ({
  default: {
    create: vi.fn(() => fakeInstance),
    // 与真实 axios 一致：以实例标志位判定 AxiosError
    isAxiosError: (error: unknown): boolean =>
      typeof error === 'object' &&
      error !== null &&
      (error as { isAxiosError?: unknown }).isAxiosError === true,
  },
}))

vi.mock('element-plus', () => ({
  ElMessage: { error: vi.fn() },
}))

const authStorage = await import('@/utils/authStorage')
const getSessionTokenMock = authStorage.getSessionToken as unknown as ReturnType<typeof vi.fn>
const clearAuthMock = authStorage.clearAuth as unknown as ReturnType<typeof vi.fn>

/** 模拟 vue-router：捕获 replace 调用并暴露当前路由 */
const routerMock = {
  currentRoute: { value: { name: 'llm', fullPath: '/llm' } },
  replace: vi.fn(),
}
vi.mock('@/router', () => ({ default: routerMock }))

// 拦截器在模块加载时一次性注册；提前捕获处理器，
// 避免 beforeEach 的 clearAllMocks 清空注册记录后无法取回。
const [onFulfilled, onRejected] = fakeInstance.interceptors.response.use.mock
  .calls[0] as [ResponseHandler, ErrorHandler]
const onRequestFulfilled = fakeInstance.interceptors.request.use.mock
  .calls[0]?.[0] as (config: Record<string, unknown>) => Record<string, unknown> | undefined

/** 构造 AxiosResponse 形状的响应对象 */
function axiosResponse(data: unknown, status = 200): Record<string, unknown> {
  return { data, status, statusText: '', headers: {}, config: {} }
}

/** 构造带 isAxiosError 标志的错误（与 mock 的 axios.isAxiosError 判定呼应） */
function makeAxiosError(
  response: Record<string, unknown> | undefined,
  message = 'Request failed with status code 404',
): Error {
  const error = new Error(message) as Error & {
    isAxiosError: boolean
    response?: Record<string, unknown>
  }
  error.isAxiosError = true
  error.response = response
  return error
}

/**
 * 受控 adapter：模拟 axios 的拦截链语义 ——
 * 先走请求拦截器（与 axios 真实行为一致），再调 adapter；
 * adapter 成功 -> 响应成功回调；adapter 拒绝 -> 响应错误回调；
 * 成功回调自身返回的 reject（非 2xx 防御分支）直接透传给调用方。
 */
async function dispatch(config: Record<string, unknown>): Promise<unknown> {
  const adapter = fakeInstance.adapter
  if (!adapter) {
    throw new Error('adapter not stubbed')
  }
  // 手动跑请求拦截器，让 mock 调用方能观察到请求头被写入
  const intercepted = onRequestFulfilled
    ? onRequestFulfilled(config) ?? config
    : config
  let response: unknown
  try {
    response = await adapter(intercepted)
  } catch (error: unknown) {
    return onRejected(error)
  }
  return onFulfilled(response)
}

function stubAdapter(result: unknown): void {
  fakeInstance.adapter = vi.fn(async () => result)
}

function stubAdapterReject(error: unknown): void {
  fakeInstance.adapter = vi.fn(async () => {
    throw error
  })
}

beforeEach(() => {
  vi.clearAllMocks()
  getSessionTokenMock.mockReturnValue(null)
  routerMock.currentRoute.value = { name: 'llm', fullPath: '/llm' }
  routerMock.replace.mockReset()
  fakeInstance.get.mockImplementation((url: string, config?: unknown) =>
    dispatch({ ...(config as object), method: 'get', url }),
  )
  fakeInstance.post.mockImplementation(
    (url: string, data?: unknown, config?: unknown) =>
      dispatch({ ...(config as object), method: 'post', url, data }),
  )
  fakeInstance.put.mockImplementation(
    (url: string, data?: unknown, config?: unknown) =>
      dispatch({ ...(config as object), method: 'put', url, data }),
  )
  fakeInstance.delete.mockImplementation((url: string, config?: unknown) =>
    dispatch({ ...(config as object), method: 'delete', url }),
  )
})

describe('统一信封解包（成功分支）', () => {
  it('code=200 信封：get/post/put 返回解包后的 data', async () => {
    const payload = { id: 1, name: 'demo' }
    stubAdapter(
      axiosResponse({ code: 200, message: 'success', data: payload }),
    )

    await expect(get('/things')).resolves.toEqual(payload)
    await expect(post('/things', payload)).resolves.toEqual(payload)
    await expect(put('/things/1', payload)).resolves.toEqual(payload)
    expect(ElMessage.error).not.toHaveBeenCalled()
  })

  it('code=201 信封（创建类端点）：同样解包成功', async () => {
    const payload = { id: 9 }
    stubAdapter(
      axiosResponse({ code: 201, message: 'created', data: payload }, 201),
    )

    await expect(post('/things', { name: 'demo' })).resolves.toEqual(payload)
    expect(ElMessage.error).not.toHaveBeenCalled()
  })

  it('DELETE 信封：data 为 null 时原样解包', async () => {
    stubAdapter(axiosResponse({ code: 200, message: 'success', data: null }))

    await expect(del('/things/1')).resolves.toBeNull()
  })
})

describe('非 2xx 信封', () => {
  it('成功回调中的非 2xx code（防御分支）：reject 且提示后端 message', async () => {
    stubAdapter(
      axiosResponse({ code: 404, message: 'resource_not_found', data: null }),
    )

    await expect(get('/missing')).rejects.toThrow('resource_not_found')
    expect(ElMessage.error).toHaveBeenCalledWith('resource_not_found')
    expect(ElMessage.error).toHaveBeenCalledTimes(1)
  })
})

describe('HTTP 错误响应（error.response 存在）', () => {
  it('信封形态错误体：reject 且提示信封 message', async () => {
    const error = makeAxiosError(
      axiosResponse(
        { code: 404, message: 'thing_not_found', data: null },
        404,
      ),
    )
    stubAdapterReject(error)

    await expect(get('/things/404')).rejects.toBe(error)
    expect(ElMessage.error).toHaveBeenCalledWith('thing_not_found')
    expect(ElMessage.error).toHaveBeenCalledTimes(1)
  })

  it('旧 FastAPI detail 形态回退：提示 detail 文案', async () => {
    const error = makeAxiosError(
      axiosResponse({ detail: 'Item not found' }, 404),
    )
    stubAdapterReject(error)

    await expect(get('/legacy')).rejects.toBe(error)
    expect(ElMessage.error).toHaveBeenCalledWith('Item not found')
  })
})

describe('裸响应透传（豁免端点）', () => {
  it('无 code 字段的裸响应（/health 形状）：原样透传不解包', async () => {
    const raw = { status: 'ok', version: '1.0' }
    stubAdapter(axiosResponse(raw))

    await expect(get('/health')).resolves.toEqual(raw)
    expect(ElMessage.error).not.toHaveBeenCalled()
  })

  it('形状碰撞回归：{ code, message } 且无 data 键 → 透传，不解包出 undefined', async () => {
    const collision = { code: 1, message: 'x' }
    stubAdapter(axiosResponse(collision))

    // 锁定 isEnvelope 加固：缺 data 键不视为信封，不得解包出 undefined
    await expect(get('/weird')).resolves.toEqual(collision)
    await expect(get('/weird')).resolves.not.toBeUndefined()
    expect(ElMessage.error).not.toHaveBeenCalled()
  })
})

describe('健康检查 API', () => {
  it('通过统一请求层请求相对的 health 端点，并返回裸健康状态', async () => {
    const health = { status: 'healthy', version: '1.0.0' }
    stubAdapter(axiosResponse(health))

    await expect(getHealth()).resolves.toEqual(health)
    expect(fakeInstance.get).toHaveBeenCalledWith('health', undefined)
  })
})

describe('认证 token 注入', () => {
  it('有会话 token 时请求携带 Authorization: Bearer xxx', async () => {
    getSessionTokenMock.mockReturnValue('session-abc')
    const observed: Array<Record<string, unknown>> = []
    fakeInstance.adapter = vi.fn(async (config: Record<string, unknown>) => {
      observed.push(config)
      return axiosResponse({ status: 'ok' })
    })

    await expect(get('/protected')).resolves.toEqual({ status: 'ok' })
    const observedHeaders = (observed[0]?.headers ?? {}) as Record<string, unknown>
    // request.ts 会同时兼容 AxiosHeaders.set 与普通对象赋值，提取字符串 Authorization
    const headerValue =
      (typeof observedHeaders.get === 'function'
        ? (observedHeaders.get as (k: string) => unknown)('Authorization')
        : observedHeaders.Authorization) ?? null
    expect(headerValue).toBe('Bearer session-abc')
  })

  it('无会话 token 时不写入 Authorization 头', async () => {
    getSessionTokenMock.mockReturnValue(null)
    const observed: Array<Record<string, unknown>> = []
    fakeInstance.adapter = vi.fn(async (config: Record<string, unknown>) => {
      observed.push(config)
      return axiosResponse({ status: 'ok' })
    })

    await expect(get('/public')).resolves.toEqual({ status: 'ok' })
    const headers = (observed[0]?.headers ?? {}) as Record<string, unknown>
    expect(headers.Authorization).toBeUndefined()
  })
})

describe('401 会话过期处理', () => {
  it('非登录页 401：清除 token 并跳转 /login?reason=expired', async () => {
    getSessionTokenMock.mockReturnValue('stale-token')
    const error = makeAxiosError(
      axiosResponse({ code: 401, message: 'expired', data: null }, 401),
      'Request failed with status code 401',
    )
    stubAdapterReject(error)

    await expect(get('/protected')).rejects.toBe(error)
    // 让 request.ts 中 401 分支的动态 import + router.replace 异步任务完成
    await new Promise<void>((resolve) => setTimeout(resolve, 0))
    expect(clearAuthMock).toHaveBeenCalledTimes(1)
    expect(routerMock.replace).toHaveBeenCalledWith({
      name: 'login',
      query: { redirect: '/llm', reason: 'expired' },
    })
    expect(ElMessage.error).toHaveBeenCalledWith('会话已失效，请重新登录')
  })

  it('登录页 401（密码错误）：不跳转、不清 token', async () => {
    routerMock.currentRoute.value = { name: 'login', fullPath: '/login' }
    const error = makeAxiosError(
      axiosResponse({ detail: 'Incorrect email or password' }, 401),
      'Request failed with status code 401',
    )
    stubAdapterReject(error)

    await expect(get('/auth/login')).rejects.toBe(error)
    await new Promise<void>((resolve) => setTimeout(resolve, 0))
    expect(clearAuthMock).not.toHaveBeenCalled()
    expect(routerMock.replace).not.toHaveBeenCalled()
  })
})
