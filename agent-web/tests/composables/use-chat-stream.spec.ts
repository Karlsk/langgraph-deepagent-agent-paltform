/**
 * src/composables/useChatStream.ts 帧分发状态机测试（G4 spec-g4-chat §9.1/§9.3）。
 *
 * mock `@/utils/sse` 与 `@/api/chat`：覆盖 message 帧同 source 归并 /
 * subagent 切块、subagent tool_call 挂卡并 closed 拆轮、coordinator
 * tool_call 平铺、summary / interrupt / error / done 帧分发、
 * decisions JSON 胶囊投影与 X-Session-Id 透传、abort 停止、loadHistory
 * 的 L2 行投影（与实时帧同 reducer）+ pending 恢复。
 */
import { beforeEach, describe, expect, it, vi } from 'vitest'
import { ref } from 'vue'

import { useChatStream } from '@/composables/useChatStream'

type SseHandler = (options: {
  url: string
  headers?: Record<string, string>
  body?: unknown
  signal?: AbortSignal
  onEvent: (data: string) => void
  onError?: (error: unknown) => void
}) => Promise<void>

const sseFetchMock = vi.fn<SseHandler>()
vi.mock('@/utils/sse', () => ({
  sseFetch: (...args: unknown[]) => sseFetchMock(...args as Parameters<SseHandler>),
}))

const fetchMessagesMock = vi.fn()
vi.mock('@/api/chat', () => ({
  fetchMessages: (...args: unknown[]) => fetchMessagesMock(...args),
}))

/** 驱动一次 mock 流：逐帧回调 onEvent 后正常结束。 */
async function replayFrames(
  frames: Array<Record<string, unknown>>,
  options?: { error?: unknown },
): Promise<void> {
  const call = sseFetchMock.mock.calls.at(-1)![0]
  if (options?.error) {
    call.onError?.(options.error)
    throw options.error
  }
  for (const frame of frames) call.onEvent(JSON.stringify(frame))
}

beforeEach(() => {
  vi.clearAllMocks()
  sseFetchMock.mockImplementation(async (options) => {
    // 默认：不产出帧，直接结束（具体测试用 replayFrames 驱动）
    void options
  })
})

describe('useChatStream 帧分发', () => {
  it('message 帧同 source 归并；subagent 消息归入执行卡片', async () => {
    const { items, send } = useChatStream(ref('s-1'))
    sseFetchMock.mockImplementation(async () => {})
    const sending = send('你好')
    await replayFrames([
      { type: 'message', content: '部分一', source: 'coordinator' },
      { type: 'message', content: '部分二', source: 'coordinator' },
      { type: 'message', content: '子代理说', source: 'writer' },
      { type: 'done', message_count: 3 },
    ])
    await sending

    expect(items.value).toEqual([
      { kind: 'message', role: 'user', content: '你好' },
      { kind: 'message', role: 'assistant', content: '部分一部分二', source: null },
      { kind: 'subagent_run', source: 'writer', content: '子代理说', running: false, toolCalls: [], closed: false },
    ])
  })

  it('subagent 连续块归并同卡；切换开新卡并收尾旧卡', async () => {
    const { items, send } = useChatStream(ref('s-1'))
    sseFetchMock.mockImplementation(async () => {})
    const sending = send('hi')
    await replayFrames([
      { type: 'message', content: '研究', source: 'researcher' },
      { type: 'message', content: '中…', source: 'researcher' },
      { type: 'message', content: '写码', source: 'coder' },
      { type: 'message', content: '总结', source: 'coordinator' },
      { type: 'done', message_count: 4 },
    ])
    await sending

    expect(items.value).toEqual([
      { kind: 'message', role: 'user', content: 'hi' },
      { kind: 'subagent_run', source: 'researcher', content: '研究中…', running: false, toolCalls: [], closed: false },
      { kind: 'subagent_run', source: 'coder', content: '写码', running: false, toolCalls: [], closed: false },
      { kind: 'message', role: 'assistant', content: '总结', source: null },
    ])
  })

  it('流式期间卡片保持 running，coordinator 块到达即收尾', async () => {
    const { items, send } = useChatStream(ref('s-1'))
    sseFetchMock.mockImplementation(async () => {})
    const sending = send('hi')
    await replayFrames([
      { type: 'message', content: '子代理', source: 'writer' },
    ])
    expect(items.value.at(-1)).toEqual({
      kind: 'subagent_run',
      source: 'writer',
      content: '子代理',
      running: true,
      toolCalls: [],
      closed: false,
    })
    await replayFrames([
      { type: 'message', content: '主回复', source: 'coordinator' },
      { type: 'done' },
    ])
    await sending
    expect(items.value[1]).toEqual({
      kind: 'subagent_run',
      source: 'writer',
      content: '子代理',
      running: false,
      toolCalls: [],
      closed: false,
    })
  })

  it('tool_call / summary / interrupt / error 帧各归其位', async () => {
    const { items, pendingInterrupt, errorMessage, send } = useChatStream(ref('s-1'))
    const sending = send('hi')
    await replayFrames([
      { type: 'message', content: '正文', source: 'coordinator' },
      { type: 'tool_call', name: 'echo', content: 'echo out' },
      { type: 'summary', summary_text: '已压缩' },
      { type: 'interrupt', action_requests: [{ tool: 'write_file', args: { path: 'a' } }] },
      { type: 'done', interrupted: true },
    ])
    await sending

    expect(items.value[2]).toEqual({ kind: 'tool_call', name: 'echo', content: 'echo out' })
    expect(items.value[3]).toEqual({ kind: 'summary', content: '已压缩' })
    expect(pendingInterrupt.value?.action_requests[0].tool).toBe('write_file')
    expect(errorMessage.value).toBeNull()
  })

  it('subagent tool_call 挂入当前卡并 closed，随后同 source 文本开新卡', async () => {
    const { items, send } = useChatStream(ref('s-1'))
    const sending = send('hi')
    await replayFrames([
      { type: 'message', content: '思考', source: 'writer' },
      { type: 'tool_call', name: 'echo', content: 'echo out', source: 'writer' },
      { type: 'message', content: '继续', source: 'writer' },
      { type: 'done' },
    ])
    await sending

    expect(items.value).toEqual([
      { kind: 'message', role: 'user', content: 'hi' },
      {
        kind: 'subagent_run',
        source: 'writer',
        content: '思考',
        running: false,
        toolCalls: [{ name: 'echo', content: 'echo out' }],
        closed: true,
      },
      { kind: 'subagent_run', source: 'writer', content: '继续', running: false, toolCalls: [], closed: false },
    ])
  })

  it('error 帧写入 errorMessage 且 done 后退出 streaming', async () => {
    const { errorMessage, streaming, send } = useChatStream(ref('s-1'))
    expect(streaming.value).toBe(false)
    const sending = send('hi')
    expect(streaming.value).toBe(true)
    await replayFrames([
      { type: 'error', message: 'stream blew up' },
      { type: 'done' },
    ])
    await sending
    expect(errorMessage.value).toBe('stream blew up')
    expect(streaming.value).toBe(false)
  })

  it('send 透传 X-Session-Id 与流 URL', async () => {
    const { send } = useChatStream(ref('s-99'))
    const sending = send('hi')
    await replayFrames([{ type: 'done' }])
    await sending
    const options = sseFetchMock.mock.calls[0][0]
    expect(options.url).toBe('/api/v1/chat/stream')
    expect(options.headers?.['X-Session-Id']).toBe('s-99')
    expect(options.body).toEqual({ messages: [{ role: 'user', content: 'hi' }] })
  })

  it('decisions JSON 投影为胶囊并清空 pending', async () => {
    const { items, pendingInterrupt, submitDecisions } = useChatStream(ref('s-1'))
    pendingInterrupt.value = { action_requests: [{ tool: 't', args: {} }] }
    sseFetchMock.mockImplementation(async () => {})
    const sending = submitDecisions([true, false])
    await replayFrames([{ type: 'done' }])
    await sending

    expect(pendingInterrupt.value).toBeNull()
    expect(items.value[0]).toEqual({ kind: 'decision', approved: 1, rejected: 1 })
    const options = sseFetchMock.mock.calls[0][0]
    expect(options.body).toEqual({
      messages: [
        { role: 'user', content: '{"decisions":[{"type":"approve"},{"type":"reject"}]}' },
      ],
    })
  })

  it('stop 调用 AbortController.abort', async () => {
    const { send, stop } = useChatStream(ref('s-1'))
    let capturedSignal: AbortSignal | undefined
    sseFetchMock.mockImplementation(async (options) => {
      capturedSignal = options.signal
      await new Promise(() => {}) // 挂起直到 abort（测试直接返回前手动 stop）
    })
    const sending = send('hi')
    await Promise.resolve()
    stop()
    expect(capturedSignal?.aborted).toBe(true)
    void sending // 挂起的 promise 由 mock 永不结束；测试到此为止
  })

  it('loadHistory 投影 L2 行并恢复 pending 审批卡片', async () => {
    fetchMessagesMock.mockResolvedValue({
      messages: [
        { type: 'message', seq: 1, ts: 't1', role: 'user', content: '你好' },
        {
          type: 'message',
          seq: 2,
          ts: 't2',
          role: 'user',
          content: '{"decisions":[{"type":"approve"},{"type":"reject"}]}',
        },
        { type: 'tool_call', seq: 3, ts: 't3', name: 'echo', summary: 'echo done' },
        { type: 'summary', seq: 4, ts: 't4', content: '压缩摘要' },
        { type: 'message', seq: 5, ts: 't5', role: 'assistant', content: '完成' },
      ],
      pending_interrupt: { action_requests: [{ tool: 'write_file', args: {} }] },
    })
    const { items, pendingInterrupt, loadHistory } = useChatStream(ref('s-1'))
    await loadHistory()

    expect(items.value).toEqual([
      { kind: 'message', role: 'user', content: '你好' },
      { kind: 'decision', approved: 1, rejected: 1 },
      { kind: 'tool_call', name: 'echo', content: 'echo done' },
      { kind: 'summary', content: '压缩摘要' },
      { kind: 'message', role: 'assistant', content: '完成' },
    ])
    expect(pendingInterrupt.value?.action_requests[0].tool).toBe('write_file')
    expect(fetchMessagesMock).toHaveBeenCalledWith('s-1')
  })

  it('loadHistory 将连续同 source 的 assistant 行归并为执行卡片', async () => {
    fetchMessagesMock.mockResolvedValue({
      messages: [
        { type: 'message', seq: 1, ts: 't1', role: 'user', content: '你好' },
        {
          type: 'message',
          seq: 2,
          ts: 't2',
          role: 'assistant',
          content: '研究中…',
          source: 'researcher',
        },
        { type: 'message', seq: 3, ts: 't3', role: 'assistant', content: '完成' },
      ],
      pending_interrupt: null,
    })
    const { items, loadHistory } = useChatStream(ref('s-1'))
    await loadHistory()

    expect(items.value).toEqual([
      { kind: 'message', role: 'user', content: '你好' },
      { kind: 'subagent_run', source: 'researcher', content: '研究中…', running: false, toolCalls: [], closed: false },
      { kind: 'message', role: 'assistant', content: '完成' },
    ])
  })

  it('loadHistory 带 source 的 tool_call 行挂卡，且与实时帧拆分一致', async () => {
    fetchMessagesMock.mockResolvedValue({
      messages: [
        { type: 'message', seq: 1, ts: 't1', role: 'user', content: '你好' },
        { type: 'message', seq: 2, ts: 't2', role: 'assistant', content: '思考', source: 'writer' },
        { type: 'tool_call', seq: 3, ts: 't3', name: 'echo', content: 'echo out', source: 'writer' },
        { type: 'message', seq: 4, ts: 't4', role: 'assistant', content: '继续', source: 'writer' },
        { type: 'message', seq: 5, ts: 't5', role: 'assistant', content: '完成' },
      ],
      pending_interrupt: null,
    })
    const { items, loadHistory } = useChatStream(ref('s-1'))
    await loadHistory()

    expect(items.value).toEqual([
      { kind: 'message', role: 'user', content: '你好' },
      {
        kind: 'subagent_run',
        source: 'writer',
        content: '思考',
        running: false,
        toolCalls: [{ name: 'echo', content: 'echo out' }],
        closed: true,
      },
      { kind: 'subagent_run', source: 'writer', content: '继续', running: false, toolCalls: [], closed: false },
      { kind: 'message', role: 'assistant', content: '完成' },
    ])
  })
})
