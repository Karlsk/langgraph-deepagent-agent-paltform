/**
 * src/utils/sse.ts fetch-based SSE 客户端测试（spec-g4-chat §9.2）。
 *
 * 零真实网络：global.fetch 以受控 ReadableStream 驱动；authStorage 与
 * useAuth 均 mock。覆盖：分帧解析（\n\n 切帧 / 跨 chunk 半帧 / data 多行
 * 拼接）、心跳注释行跳过、token 注入、401 refresh 重试一次、二次失败
 * clearAuth、abort 安静结束、网络错误 onError + rethrow。
 */
import { afterEach, beforeEach, describe, expect, it, vi } from 'vitest'

import { sseFetch } from '@/utils/sse'

const getUserTokenMock = vi.fn()
const clearAuthMock = vi.fn()
vi.mock('@/utils/authStorage', () => ({
  getUserToken: (...args: unknown[]) => getUserTokenMock(...args),
  clearAuth: (...args: unknown[]) => clearAuthMock(...args),
}))

const refreshUserTokenMock = vi.fn()
vi.mock('@/composables/useAuth', () => ({
  refreshUserToken: (...args: unknown[]) => refreshUserTokenMock(...args),
}))

const routerReplaceMock = vi.fn()
vi.mock('@/router', () => ({
  default: {
    currentRoute: { value: { name: 'chatSession', fullPath: '/chat/s1' } },
    replace: (...args: unknown[]) => routerReplaceMock(...args),
  },
}))

function sseResponse(
  chunks: string[],
  init?: { status?: number; contentType?: string },
): Response {
  const encoder = new TextEncoder()
  const stream = new ReadableStream<Uint8Array>({
    start(controller) {
      for (const chunk of chunks) controller.enqueue(encoder.encode(chunk))
      controller.close()
    },
  })
  return {
    ok: (init?.status ?? 200) < 400,
    status: init?.status ?? 200,
    headers: new Headers({ 'content-type': init?.contentType ?? 'text/event-stream' }),
    body: stream,
  } as unknown as Response
}

const fetchMock = vi.fn()

describe('sseFetch', () => {
  beforeEach(() => {
    vi.stubGlobal('fetch', fetchMock)
    getUserTokenMock.mockReturnValue('tok-1')
    clearAuthMock.mockClear()
    refreshUserTokenMock.mockReset()
    routerReplaceMock.mockClear()
    fetchMock.mockReset()
  })

  afterEach(() => {
    vi.unstubAllGlobals()
  })

  it('按 \\n\\n 切帧并跳过心跳注释行', async () => {
    fetchMock.mockResolvedValue(
      sseResponse([': ping\n\ndata: {"a":1}\n\ndata: {"b":2}\n\n']),
    )
    const events: string[] = []
    await sseFetch({
      url: '/api/v1/chat/stream',
      onEvent: (data) => events.push(data),
    })
    expect(events).toEqual(['{"a":1}', '{"b":2}'])
  })

  it('跨 chunk 半帧靠缓冲区拼接', async () => {
    fetchMock.mockResolvedValue(sseResponse(['data: {"par', 'tial":1}\n\n']))
    const events: string[] = []
    await sseFetch({ url: '/u', onEvent: (data) => events.push(data) })
    expect(events).toEqual(['{"partial":1}'])
  })

  it('帧内多条 data: 行按 SSE 规范拼接', async () => {
    fetchMock.mockResolvedValue(sseResponse(['data: line1\ndata: line2\n\n']))
    const events: string[] = []
    await sseFetch({ url: '/u', onEvent: (data) => events.push(data) })
    expect(events).toEqual(['line1\nline2'])
  })

  it('注入 Authorization 与 Accept header', async () => {
    fetchMock.mockResolvedValue(sseResponse(['data: 1\n\n']))
    await sseFetch({ url: '/u', onEvent: () => {} })
    const [, init] = fetchMock.mock.calls[0] as [string, RequestInit]
    expect(init.method).toBe('POST')
    const headers = init.headers as Record<string, string>
    expect(headers.Authorization).toBe('Bearer tok-1')
    expect(headers.Accept).toBe('text/event-stream')
  })

  it('连接 401 时 refresh 成功后用新 token 重试一次', async () => {
    fetchMock
      .mockResolvedValueOnce(sseResponse([], { status: 401 }))
      .mockResolvedValueOnce(sseResponse(['data: ok\n\n']))
    refreshUserTokenMock.mockResolvedValue('tok-2')
    const events: string[] = []
    await sseFetch({ url: '/u', onEvent: (data) => events.push(data) })
    expect(events).toEqual(['ok'])
    expect(fetchMock).toHaveBeenCalledTimes(2)
    const [, retryInit] = fetchMock.mock.calls[1] as [string, RequestInit]
    expect((retryInit.headers as Record<string, string>).Authorization).toBe('Bearer tok-2')
    expect(clearAuthMock).not.toHaveBeenCalled()
  })

  it('二次 401 时 clearAuth 并跳 login', async () => {
    fetchMock
      .mockResolvedValueOnce(sseResponse([], { status: 401 }))
      .mockResolvedValueOnce(sseResponse([], { status: 401 }))
    refreshUserTokenMock.mockResolvedValue('tok-2')
    const onError = vi.fn()
    await expect(
      sseFetch({ url: '/u', onEvent: () => {}, onError }),
    ).rejects.toThrow('SSE connect failed: 401')
    expect(clearAuthMock).toHaveBeenCalledTimes(1)
    expect(onError).toHaveBeenCalledTimes(1)
    await vi.waitFor(() => expect(routerReplaceMock).toHaveBeenCalledTimes(1))
  })

  it('abort 时安静结束不触发 onError', async () => {
    const controller = new AbortController()
    const abortError = new DOMException('aborted', 'AbortError')
    fetchMock.mockImplementation(async () => {
      controller.abort()
      throw abortError
    })
    const onError = vi.fn()
    await sseFetch({
      url: '/u',
      signal: controller.signal,
      onEvent: () => {},
      onError,
    })
    expect(onError).not.toHaveBeenCalled()
  })

  it('网络错误触发 onError 并 rethrow', async () => {
    fetchMock.mockRejectedValue(new TypeError('network down'))
    const onError = vi.fn()
    await expect(
      sseFetch({ url: '/u', onEvent: () => {}, onError }),
    ).rejects.toThrow('network down')
    expect(onError).toHaveBeenCalledTimes(1)
  })
})
