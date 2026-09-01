// @vitest-environment happy-dom
/**
 * ChatTraceDrawer 运行轨迹抽屉测试（G4 spec-g4-chat §9.4）：
 * 打开即拉取 traces；行摘要（状态 / 轮次 / 耗时 / 时间倒序由后端保证）；
 * 点击行展开事件流——逐事件折叠（默认全收起，头部仅显摘要），
 * 点击事件头展开分类型关键字段；agent 字段区分 coordinator / subagent；
 * error 行展示失败原因；关闭 emit update:modelValue。
 */
import { beforeEach, describe, expect, it, vi } from 'vitest'
import { defineComponent, h } from 'vue'
import { flushPromises, mount } from '@vue/test-utils'

import ChatTraceDrawer from '@/views/chat/ChatTraceDrawer.vue'
import type { ChatTraceItem } from '@/types'

const fetchChatTracesMock = vi.fn()
vi.mock('@/api/chat', () => ({
  fetchChatTraces: (...args: unknown[]) => fetchChatTracesMock(...args),
}))

const ElDrawerStub = defineComponent({
  name: 'ElDrawer',
  props: { modelValue: Boolean, title: String },
  emits: ['update:modelValue'],
  setup(props, { slots }) {
    return () =>
      props.modelValue
        ? h('div', { class: 'el-drawer-stub' }, slots.default?.())
        : null
  },
})

const ElTagStub = defineComponent({
  name: 'ElTag',
  props: { type: String, size: String },
  setup(props, { slots }) {
    return () =>
      h('span', { class: 'el-tag-stub', 'data-type': props.type ?? '' }, slots.default?.())
  },
})

const ElButtonStub = defineComponent({
  name: 'ElButton',
  emits: ['click'],
  setup(_, { emit, slots }) {
    return () =>
      h('button', { class: 'el-button-stub', onClick: () => emit('click') }, slots.default?.())
  },
})

const TRACES: ChatTraceItem[] = [
  {
    id: 2,
    status: 'error',
    turns: 3,
    duration_seconds: 1.5,
    error: 'tool blew up',
    created_at: '2026-08-27T10:00:00+00:00',
    events: [],
  },
  {
    id: 1,
    status: 'success',
    turns: 2,
    duration_seconds: 0.5,
    error: null,
    created_at: '2026-08-27T09:00:00+00:00',
    events: [
      {
        seq: 1,
        type: 'llm_call',
        agent: 'coordinator',
        model: 'gpt-test',
        status: 'success',
        duration_seconds: 0.3,
        output_text: '模型输出',
        input_messages: [{ type: 'user', content: 'hi' }],
        token_usage: { input_tokens: 10, output_tokens: 5, total_tokens: 15 },
      },
      {
        seq: 2,
        type: 'tool_call',
        agent: 'writer',
        tool: 'write_file',
        status: 'success',
        arguments: { path: 'a.txt' },
        output: 'wrote a.txt',
      },
    ],
  },
]

function mountDrawer(props: { modelValue: boolean; sessionId: string }) {
  return mount(ChatTraceDrawer, {
    props,
    global: {
      stubs: { ElDrawer: ElDrawerStub, ElTag: ElTagStub, ElButton: ElButtonStub },
    },
  })
}

beforeEach(() => {
  vi.clearAllMocks()
  fetchChatTracesMock.mockResolvedValue(TRACES)
})

describe('ChatTraceDrawer', () => {
  it('打开即拉取 traces 并渲染行摘要（状态 / 轮次 / 耗时 / 时间）', async () => {
    const wrapper = mountDrawer({ modelValue: true, sessionId: 's-1' })
    await flushPromises()

    expect(fetchChatTracesMock).toHaveBeenCalledWith('s-1')

    const headers = wrapper.findAll('.chat-trace-drawer__item-header')
    expect(headers).toHaveLength(2)
    const first = headers[0].text()
    expect(first).toContain('失败')
    expect(first).toContain('3')
    expect(first).toContain('1.5')
    expect(first).toContain('2026-08-27 10:00:00')
    expect(headers[1].text()).toContain('成功')
  })

  it('点击行展开事件流摘要头，默认全收起不显正文', async () => {
    const wrapper = mountDrawer({ modelValue: true, sessionId: 's-1' })
    await flushPromises()

    expect(wrapper.find('.chat-trace-drawer__events').exists()).toBe(false)

    const headers = wrapper.findAll('.chat-trace-drawer__item-header')
    await headers[1].trigger('click')

    const events = wrapper.findAll('.chat-trace-drawer__event')
    expect(events).toHaveLength(2)
    expect(events[0].text()).toContain('LLM')
    expect(events[0].text()).toContain('coordinator')
    expect(events[0].text()).toContain('gpt-test')
    expect(events[1].text()).toContain('工具')
    expect(events[1].text()).toContain('writer')
    // 默认全收起：事件正文不渲染（原始 prompt/输出不可见）
    expect(wrapper.find('.chat-trace-drawer__event-body').exists()).toBe(false)
    expect(wrapper.text()).not.toContain('模型输出')
    expect(wrapper.text()).not.toContain('wrote a.txt')

    await headers[1].trigger('click')
    expect(wrapper.find('.chat-trace-drawer__events').exists()).toBe(false)
  })

  it('事件头逐层折叠：点击展开分类型关键字段，再点收起', async () => {
    const wrapper = mountDrawer({ modelValue: true, sessionId: 's-1' })
    await flushPromises()
    await wrapper.findAll('.chat-trace-drawer__item-header')[1].trigger('click')

    const eventHeads = wrapper.findAll('.chat-trace-drawer__event-head')

    // 展开 llm_call：token / 输入消息 / 输出
    await eventHeads[0].trigger('click')
    const bodies = wrapper.findAll('.chat-trace-drawer__event-body')
    expect(bodies).toHaveLength(1)
    expect(bodies[0].text()).toContain('token：10 in / 5 out')
    expect(bodies[0].text()).toContain('输入消息（1）')
    expect(bodies[0].text()).toContain('模型输出')
    expect(wrapper.text()).not.toContain('wrote a.txt')

    // 展开 tool_call：参数 / 返回值；两事件可同时展开（互不影响）
    await eventHeads[1].trigger('click')
    expect(wrapper.findAll('.chat-trace-drawer__event-body')).toHaveLength(2)
    expect(wrapper.text()).toContain('wrote a.txt')

    // 收起 llm_call：仅 tool_call 保持展开
    await eventHeads[0].trigger('click')
    expect(wrapper.findAll('.chat-trace-drawer__event-body')).toHaveLength(1)
    expect(wrapper.text()).not.toContain('模型输出')
  })

  it('error 行展开后展示失败原因', async () => {
    const wrapper = mountDrawer({ modelValue: true, sessionId: 's-1' })
    await flushPromises()

    await wrapper.findAll('.chat-trace-drawer__item-header')[0].trigger('click')
    expect(wrapper.get('.chat-trace-drawer__error').text()).toContain('tool blew up')
  })

  it('关闭按钮 emit update:modelValue false', async () => {
    const wrapper = mountDrawer({ modelValue: true, sessionId: 's-1' })
    await flushPromises()

    await wrapper.get('.chat-trace-drawer__close').trigger('click')
    expect(wrapper.emitted('update:modelValue')).toEqual([[false]])
  })
})
