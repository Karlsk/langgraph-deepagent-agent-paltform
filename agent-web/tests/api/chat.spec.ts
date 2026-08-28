/**
 * src/api/chat.ts API 模块契约测试（G4 spec-g4-chat §9.6）：
 * - mock `@/utils/request` 的 get/post，断言各函数的 URL / X-Session-Id
 *   header 与载荷透传；
 * - 不发起真实网络请求。
 */
import { beforeEach, describe, expect, it, vi } from 'vitest'

import {
  fetchChatTraces,
  fetchMessages,
  rebuildSession,
  sendChat,
} from '@/api/chat'

const requestMock = vi.hoisted(() => ({
  get: vi.fn(),
  post: vi.fn(),
}))

vi.mock('@/utils/request', () => requestMock)

beforeEach(() => {
  vi.clearAllMocks()
})

describe('chat.ts 端点契约', () => {
  it('sendChat 发 POST /chat，body 带 messages、header 带 X-Session-Id', async () => {
    const data = { messages: [{ role: 'assistant', content: 'ok' }], interrupt: null }
    requestMock.post.mockResolvedValue(data)

    const result = await sendChat('s-1', [
      { role: 'user', content: 'hi' },
    ])

    expect(result).toBe(data)
    const [url, body, config] = requestMock.post.mock.calls[0] as [
      string,
      unknown,
      { headers: Record<string, string> },
    ]
    expect(url).toBe('/chat')
    expect(body).toEqual({ messages: [{ role: 'user', content: 'hi' }] })
    expect(config.headers['X-Session-Id']).toBe('s-1')
  })

  it('fetchMessages 发 GET /messages 带 X-Session-Id', async () => {
    const data = { messages: [], pending_interrupt: null }
    requestMock.get.mockResolvedValue(data)

    const result = await fetchMessages('s-1')

    expect(result).toBe(data)
    const [url, config] = requestMock.get.mock.calls[0] as [
      string,
      { headers: Record<string, string> },
    ]
    expect(url).toBe('/messages')
    expect(config.headers['X-Session-Id']).toBe('s-1')
  })

  it('rebuildSession 发 POST /rebuild 无 body', async () => {
    const data = { rebuilt_messages: 3, skipped_tool_calls: 1, l2_source_lines: 4 }
    requestMock.post.mockResolvedValue(data)

    const result = await rebuildSession('s-1')

    expect(result).toBe(data)
    const [url, body, config] = requestMock.post.mock.calls[0] as [
      string,
      unknown,
      { headers: Record<string, string> },
    ]
    expect(url).toBe('/rebuild')
    expect(body).toBeUndefined()
    expect(config.headers['X-Session-Id']).toBe('s-1')
  })

  it('fetchChatTraces 发 GET /chat/traces', async () => {
    const data = [
      {
        id: 1,
        status: 'success',
        turns: 2,
        duration_seconds: 0.5,
        error: null,
        created_at: '2026-01-01T00:00:00+00:00',
        events: [{ seq: 1, agent: 'coordinator' }],
      },
    ]
    requestMock.get.mockResolvedValue(data)

    const result = await fetchChatTraces('s-1')

    expect(result).toBe(data)
    const [url, config] = requestMock.get.mock.calls[0] as [
      string,
      { headers: Record<string, string> },
    ]
    expect(url).toBe('/chat/traces')
    expect(config.headers['X-Session-Id']).toBe('s-1')
  })
})
