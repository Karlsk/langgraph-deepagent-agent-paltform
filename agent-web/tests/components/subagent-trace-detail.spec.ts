// @vitest-environment happy-dom
/**
 * SubAgentTraceDetailDialog 视图测试（追踪详情弹窗）：
 * - stub Element Plus 组件（不做真实渲染）；
 * - mock `@/api/subagents` 的 getSubAgentTestTrace（零网络）；
 * - fixture 事件结构与基线真实 trace（isis-config-debug）及后端
 *   run_tracer 契约对齐：llm_call / tool_call / run_finished；
 * - 覆盖：事件流顺序与类型徽标、折叠展开、总 token 求和、
 *   final_message / error 展示、失败收敛。
 */
import { beforeEach, describe, expect, it, vi } from 'vitest'
import { defineComponent, h } from 'vue'
import { flushPromises, mount } from '@vue/test-utils'
import type { VueWrapper } from '@vue/test-utils'

import SubAgentTraceDetailDialog from '@/views/agent/SubAgentTraceDetailDialog.vue'
import type { SubAgentTraceDetail } from '@/api/subagents'

const { apiMock } = vi.hoisted(() => ({
  apiMock: { getSubAgentTestTrace: vi.fn() },
}))
vi.mock('@/api/subagents', () => apiMock)

/** 含工具链的完整事件流（结构来自基线真实 trace + run_tracer 契约） */
const DETAIL: SubAgentTraceDetail = {
  id: 1,
  status: 'success',
  prompt: '请用一句话介绍你自己',
  model: 'deepseek-v4-flash',
  turns: 2,
  duration_seconds: 3.32,
  final_message: '我是 ISIS 配置排查专家。',
  error: null,
  created_by: '6',
  created_at: '2026-08-24T08:32:15',
  events: [
    {
      seq: 1,
      type: 'llm_call',
      started_at: '2026-08-24T08:32:12',
      duration_seconds: 1.2,
      status: 'success',
      error: null,
      model: 'deepseek-v4-flash',
      input_messages: [
        { type: 'system', content: '你是 ISIS 配置排查专家。' },
        { type: 'human', content: '请用一句话介绍你自己' },
      ],
      output_text: '我先查一下配置。',
      tool_calls: [{ name: 'read_global', args: { key: 'isis' }, id: 'call-1' }],
      token_usage: { input_tokens: 4213, output_tokens: 259, total_tokens: 4472 },
    },
    {
      seq: 2,
      type: 'tool_call',
      started_at: '2026-08-24T08:32:13',
      duration_seconds: 0.01,
      status: 'success',
      error: null,
      tool: 'read_global',
      arguments: { key: 'isis' },
      output: '{"net": "49.0001"}',
    },
    {
      seq: 3,
      type: 'llm_call',
      started_at: '2026-08-24T08:32:13.5',
      duration_seconds: 1.8,
      status: 'success',
      error: null,
      model: 'deepseek-v4-flash',
      input_messages: [{ type: 'tool', content: '{"net": "49.0001"}' }],
      output_text: '我是 ISIS 配置排查专家。',
      tool_calls: [],
      token_usage: { input_tokens: 100, output_tokens: 28, total_tokens: 128 },
    },
    {
      seq: 4,
      type: 'run_finished',
      started_at: '2026-08-24T08:32:15',
      duration_seconds: 3.32,
      status: 'success',
      error: null,
      turns: 2,
      final_messages: [{ type: 'ai', content: '我是 ISIS 配置排查专家。' }],
    },
  ],
}

const ElDialogStub = defineComponent({
  name: 'ElDialog',
  props: { modelValue: Boolean, title: String, width: String },
  setup(props, { slots }) {
    return () =>
      props.modelValue
        ? h(
            'div',
            { class: 'el-dialog-stub', 'data-title': props.title },
            [slots.default ? slots.default() : undefined, slots.footer ? slots.footer() : undefined],
          )
        : null
  },
})

const ElButtonStub = defineComponent({
  name: 'ElButton',
  props: { loading: Boolean, disabled: Boolean },
  emits: ['click'],
  setup(_, { emit, slots, attrs }) {
    return () =>
      h(
        'button',
        { class: attrs.class, onClick: () => emit('click') },
        slots.default ? slots.default() : undefined,
      )
  },
})

const ElTagStub = defineComponent({
  name: 'ElTag',
  props: { type: String, size: String },
  setup(props, { slots }) {
    return () =>
      h('span', { class: 'el-tag-stub', 'data-type': props.type ?? '' }, slots.default ? slots.default() : undefined)
  },
})

function mountDetail(traceId: number | null = 1, visible = true): VueWrapper {
  return mount(SubAgentTraceDetailDialog, {
    props: { modelValue: visible, agentName: 'isis-config-debug', traceId },
    global: {
      stubs: {
        ElDialog: ElDialogStub,
        ElButton: ElButtonStub,
        ElTag: ElTagStub,
      },
      directives: { loading: () => undefined },
    },
  })
}

beforeEach(() => {
  vi.clearAllMocks()
  apiMock.getSubAgentTestTrace.mockResolvedValue({ ...DETAIL, events: DETAIL.events.map((e) => ({ ...e })) })
})

describe('SubAgentTraceDetailDialog 追踪详情弹窗', () => {
  it('打开即以 (agentName, traceId) 拉取详情', async () => {
    mountDetail()
    await flushPromises()

    expect(apiMock.getSubAgentTestTrace).toHaveBeenCalledWith('isis-config-debug', 1)
  })

  it('traceId 为 null 或弹窗关闭时不发请求', async () => {
    mountDetail(null)
    await flushPromises()
    expect(apiMock.getSubAgentTestTrace).not.toHaveBeenCalled()

    mountDetail(1, false)
    await flushPromises()
    expect(apiMock.getSubAgentTestTrace).not.toHaveBeenCalled()
  })

  it('事件流按 seq 升序渲染且徽标类型正确（LLM / 工具 / 结束）', async () => {
    const wrapper = mountDetail()
    await flushPromises()

    const badges = wrapper.findAll('[class*="subagent-trace-detail__event-badge"]')
    expect(badges.map((badge) => badge.text())).toEqual(['LLM', '工具', 'LLM', '结束'])
    const seqs = wrapper.findAll('.subagent-trace-detail__event-seq').map((item) => item.text())
    expect(seqs).toEqual(['#1', '#2', '#3', '#4'])
    // tool_call 行头显示工具名
    expect(wrapper.text()).toContain('read_global')
  })

  it('事件体默认折叠，点击行头展开/再点折叠', async () => {
    const wrapper = mountDetail()
    await flushPromises()

    expect(wrapper.findAll('.subagent-trace-detail__event-body')).toHaveLength(0)

    const firstHeader = wrapper.findAll('.subagent-trace-detail__event-header')[0]
    await firstHeader.trigger('click')
    expect(wrapper.findAll('.subagent-trace-detail__event-body')).toHaveLength(1)
    // llm_call 展开体含输入消息与输出
    expect(wrapper.text()).toContain('输入消息（2）')
    expect(wrapper.text()).toContain('你是 ISIS 配置排查专家。')

    await firstHeader.trigger('click')
    expect(wrapper.findAll('.subagent-trace-detail__event-body')).toHaveLength(0)
  })

  it('展开 tool_call 事件渲染参数与返回值', async () => {
    const wrapper = mountDetail()
    await flushPromises()

    await wrapper.findAll('.subagent-trace-detail__event-header')[1].trigger('click')
    expect(wrapper.text()).toContain('参数')
    expect(wrapper.text()).toContain('返回值')
    expect(wrapper.text()).toContain('{"net": "49.0001"}')
  })

  it('右栏概览：总 token 为全部 llm_call 求和、展示 final_message 与状态', async () => {
    const wrapper = mountDetail()
    await flushPromises()

    // 4472 + 128 = 4600
    expect(wrapper.text()).toContain('总 token：4600')
    expect(wrapper.text()).toContain('我是 ISIS 配置排查专家。')
    expect(wrapper.find('.el-tag-stub').attributes('data-type')).toBe('success')
    expect(wrapper.text()).toContain('轮次：2')
  })

  it('error 路径：状态标签 danger + 事件错误文案红色展示', async () => {
    apiMock.getSubAgentTestTrace.mockResolvedValue({
      ...DETAIL,
      status: 'error',
      error: 'RuntimeError: boom',
      final_message: '',
      events: [
        { ...DETAIL.events[0], seq: 1, status: 'error', error: 'ModelTimeout' },
        { ...DETAIL.events[3], seq: 2, status: 'error' },
      ],
    })
    const wrapper = mountDetail()
    await flushPromises()

    expect(wrapper.find('.el-tag-stub').attributes('data-type')).toBe('danger')
    expect(wrapper.text()).toContain('RuntimeError: boom')
    expect(wrapper.text()).toContain('（无）')
    // 错误事件带 --error 修饰类
    expect(wrapper.find('.subagent-trace-detail__event--error').exists()).toBe(true)
    // 展开错误事件后错误文案可见
    await wrapper.findAll('.subagent-trace-detail__event-header')[0].trigger('click')
    expect(wrapper.text()).toContain('ModelTimeout')
  })

  it('请求失败收敛：不展示事件流且不抛异常', async () => {
    apiMock.getSubAgentTestTrace.mockRejectedValue(new Error('404'))
    const wrapper = mountDetail()
    await flushPromises()

    expect(wrapper.text()).toContain('未加载到追踪数据。')
    expect(wrapper.find('.subagent-trace-detail__event-list').exists()).toBe(false)
  })
})
