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

// 拦截器在模块加载时一次性注册；提前捕获处理器，
// 避免 beforeEach 的 clearAllMocks 清空注册记录后无法取回。
const [onFulfilled, onRejected] = fakeInstance.interceptors.response.use.mock
  .calls[0] as [ResponseHandler, ErrorHandler]

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
 * adapter 成功 -> 响应成功回调；adapter 拒绝 -> 响应错误回调；
 * 成功回调自身返回的 reject（非 2xx 防御分支）直接透传给调用方。
 */
async function dispatch(config: Record<string, unknown>): Promise<unknown> {
  const adapter = fakeInstance.adapter
  if (!adapter) {
    throw new Error('adapter not stubbed')
  }
  let response: unknown
  try {
    response = await adapter(config)
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
  it('通过统一请求层请求相对的 health 路径，并返回裸健康状态', async () => {
    const health = { status: 'healthy', version: '1.0.0' }
    stubAdapter(axiosResponse(health))

    await expect(getHealth()).resolves.toEqual(health)
    expect(fakeInstance.get).toHaveBeenCalledWith('health', undefined)
  })
})
