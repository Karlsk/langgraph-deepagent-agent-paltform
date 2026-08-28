// @vitest-environment happy-dom
/**
 * ChatSessionView 会话聊天页测试（G4 spec-g4-chat §9.1/§9.3）：
 * 真实 useChatStream 状态机 + mock `@/utils/sse` 与 `@/api/chat`，覆盖
 * 挂载即拉历史 / Enter 发送 / streaming 停止 / interrupt 审批卡与
 * pending placeholder / error 提示 / unmount abort / rebuild 确认流。
 */
import { beforeEach, describe, expect, it, vi } from 'vitest'
import { defineComponent, h } from 'vue'
import { flushPromises, mount } from '@vue/test-utils'

import ChatSessionView from '@/views/chat/ChatSessionView.vue'

// ---------------------------------------------------------------------------
// mocks：sse / chat api / router / element-plus 提示
// ---------------------------------------------------------------------------

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
const rebuildSessionMock = vi.fn()
vi.mock('@/api/chat', () => ({
  fetchMessages: (...args: unknown[]) => fetchMessagesMock(...args),
  rebuildSession: (...args: unknown[]) => rebuildSessionMock(...args),
}))

const pushMock = vi.fn()
vi.mock('vue-router', async (importOriginal) => {
  const actual = await importOriginal<typeof import('vue-router')>()
  return {
    ...actual,
    useRoute: () => ({ params: { sessionId: 's-1' } }),
    useRouter: () => ({ push: pushMock }),
  }
})

const { elMessageMock, elMessageBoxMock } = vi.hoisted(() => {
  const fn = vi.fn()
  return {
    elMessageMock: Object.assign(fn, { success: vi.fn(), error: vi.fn(), warning: vi.fn() }),
    elMessageBoxMock: { confirm: vi.fn() },
  }
})
vi.mock('element-plus', () => ({
  ElMessage: elMessageMock,
  ElMessageBox: elMessageBoxMock,
}))

// ---------------------------------------------------------------------------
// ElButton stub（HilCard / 页面按钮）
// ---------------------------------------------------------------------------

const ElButtonStub = defineComponent({
  name: 'ElButton',
  props: { disabled: Boolean, type: String },
  emits: ['click'],
  setup(props, { emit, slots, attrs }) {
    return () =>
      h(
        'button',
        {
          class: attrs.class,
          'data-disabled': props.disabled ? 'true' : 'false',
          onClick: () => {
            if (!props.disabled) emit('click')
          },
        },
        slots.default ? slots.default() : undefined,
      )
  },
})

function mountView() {
  return mount(ChatSessionView, {
    global: {
      stubs: { ElButton: ElButtonStub },
    },
  })
}

/** 驱动最近一次 SSE 流：逐帧回调 onEvent 后结束（error 可选） */
async function replayFrames(
  frames: Array<Record<string, unknown>>,
): Promise<void> {
  const call = sseFetchMock.mock.calls.at(-1)![0]
  for (const frame of frames) call.onEvent(JSON.stringify(frame))
}

function findButton(wrapper: ReturnType<typeof mountView>, text: string) {
  const button = wrapper.findAll('button').find((item) => item.text().includes(text))
  if (!button) throw new Error(`button "${text}" not found`)
  return button
}

beforeEach(() => {
  vi.clearAllMocks()
  sseFetchMock.mockImplementation(async () => {})
  elMessageBoxMock.confirm.mockReset()
  elMessageBoxMock.confirm.mockResolvedValue(undefined)
  fetchMessagesMock.mockResolvedValue({
    messages: [
      { type: 'message', seq: 1, ts: 't1', role: 'user', content: '你好' },
    ],
    pending_interrupt: null,
  })
  rebuildSessionMock.mockResolvedValue({
    rebuilt_messages: 3,
    skipped_tool_calls: 1,
    l2_source_lines: 4,
  })
})

describe('ChatSessionView', () => {
  it('挂载即拉取历史并渲染消息流；顶栏返回跳列表', async () => {
    const wrapper = mountView()
    await flushPromises()

    expect(fetchMessagesMock).toHaveBeenCalledWith('s-1')
    expect(wrapper.findAll('.chat-message-list__row')).toHaveLength(1)

    await findButton(wrapper, '返回列表').trigger('click')
    expect(pushMock).toHaveBeenCalledWith({ name: 'chat' })
  })

  it('输入后 Enter 发送到流端点且清空输入框', async () => {
    const wrapper = mountView()
    await flushPromises()

    const textarea = wrapper.get('textarea')
    await textarea.setValue('帮我看下这个需求')
    await textarea.trigger('keydown.enter')
    await replayFrames([{ type: 'done' }])
    await flushPromises()

    const options = sseFetchMock.mock.calls[0][0]
    expect(options.url).toBe('/api/v1/chat/stream')
    expect(options.body).toEqual({
      messages: [{ role: 'user', content: '帮我看下这个需求' }],
    })
    expect((wrapper.get('textarea').element as HTMLTextAreaElement).value).toBe('')
  })

  it('streaming 时发送钮变「停止」，点击 abort 当前流', async () => {
    let capturedSignal: AbortSignal | undefined
    sseFetchMock.mockImplementation(async (options) => {
      capturedSignal = options.signal
      await new Promise(() => {}) // 挂起直到 abort
    })
    const wrapper = mountView()
    await flushPromises()

    const textarea = wrapper.get('textarea')
    await textarea.setValue('hi')
    await textarea.trigger('keydown.enter')
    await Promise.resolve()

    const stopButton = findButton(wrapper, '停止')
    await stopButton.trigger('click')
    expect(capturedSignal?.aborted).toBe(true)
  })

  it('interrupt 帧后渲染审批卡，提交走 decisions 通道并切换 placeholder', async () => {
    const wrapper = mountView()
    await flushPromises()

    expect(wrapper.find('.chat-hil-card').exists()).toBe(false)

    const textarea = wrapper.get('textarea')
    expect(textarea.attributes('placeholder')).toContain('Enter')
    await textarea.setValue('hi')
    await textarea.trigger('keydown.enter')
    await replayFrames([
      {
        type: 'interrupt',
        action_requests: [{ tool: 'write_file', args: { path: 'a' } }],
      },
      { type: 'done', interrupted: true },
    ])
    await flushPromises()

    expect(wrapper.find('.chat-hil-card').exists()).toBe(true)
    expect(textarea.attributes('placeholder')).toContain('拒绝所有待审批操作')

    await findButton(wrapper, '提交决定').trigger('click')
    await replayFrames([{ type: 'done' }])
    await flushPromises()

    const resumeOptions = sseFetchMock.mock.calls.at(-1)![0]
    expect(resumeOptions.body).toEqual({
      messages: [{ role: 'user', content: '{"decisions":[{"type":"approve"}]}' }],
    })
    expect(wrapper.find('.chat-hil-card').exists()).toBe(false)
  })

  it('error 帧渲染错误提示条', async () => {
    const wrapper = mountView()
    await flushPromises()

    const textarea = wrapper.get('textarea')
    await textarea.setValue('hi')
    await textarea.trigger('keydown.enter')
    await replayFrames([
      { type: 'error', message: 'stream blew up' },
      { type: 'done' },
    ])
    await flushPromises()

    expect(wrapper.get('.chat-session__error').text()).toContain('stream blew up')
  })

  it('unmount 时 abort 在途流', async () => {
    let capturedSignal: AbortSignal | undefined
    sseFetchMock.mockImplementation(async (options) => {
      capturedSignal = options.signal
      await new Promise(() => {})
    })
    const wrapper = mountView()
    await flushPromises()

    const textarea = wrapper.get('textarea')
    await textarea.setValue('hi')
    await textarea.trigger('keydown.enter')
    await Promise.resolve()

    wrapper.unmount()
    expect(capturedSignal?.aborted).toBe(true)
  })

  it('rebuild 确认后调用重建接口并刷新历史', async () => {
    const wrapper = mountView()
    await flushPromises()
    expect(fetchMessagesMock).toHaveBeenCalledTimes(1)

    await findButton(wrapper, '重建会话').trigger('click')
    await flushPromises()

    expect(elMessageBoxMock.confirm).toHaveBeenCalledTimes(1)
    expect(rebuildSessionMock).toHaveBeenCalledWith('s-1')
    expect(elMessageMock).toHaveBeenCalledWith(
      expect.objectContaining({ type: 'success', message: '已重建会话状态' }),
    )
    expect(fetchMessagesMock).toHaveBeenCalledTimes(2)
  })
})
