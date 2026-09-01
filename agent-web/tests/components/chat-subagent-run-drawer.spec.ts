// @vitest-environment happy-dom
/**
 * ChatSubAgentRunDrawer 子智能体执行详情抽屉测试：
 * 打开渲染完整收集文本（pre-wrap）；标题带子智能体名；空内容降级提示；
 * 关闭 emit update:modelValue false。
 */
import { describe, expect, it } from 'vitest'
import { defineComponent, h } from 'vue'
import { mount } from '@vue/test-utils'

import ChatSubAgentRunDrawer from '@/views/chat/ChatSubAgentRunDrawer.vue'

const ElDrawerStub = defineComponent({
  name: 'ElDrawer',
  props: { modelValue: Boolean, title: String },
  emits: ['update:modelValue'],
  setup(props, { emit, slots }) {
    return () =>
      props.modelValue
        ? h(
            'div',
            { class: 'el-drawer-stub', 'data-title': props.title ?? '' },
            [
              h('button', {
                class: 'el-drawer-stub__close',
                onClick: () => emit('update:modelValue', false),
              }),
              slots.default?.(),
            ],
          )
        : null
  },
})

function mountDrawer(props: {
  modelValue: boolean
  source: string | null
  content: string
  toolCalls?: Array<{ name: string; content: string }>
}) {
  return mount(ChatSubAgentRunDrawer, {
    props,
    global: { stubs: { ElDrawer: ElDrawerStub } },
  })
}

describe('ChatSubAgentRunDrawer', () => {
  it('打开时渲染完整收集文本且标题带子智能体名', () => {
    const wrapper = mountDrawer({
      modelValue: true,
      source: 'writer',
      content: '第一行输出\n第二行输出',
    })

    const stub = wrapper.get('.el-drawer-stub')
    expect(stub.attributes('data-title')).toBe('子智能体执行 — writer')
    const pre = wrapper.get('.chat-subagent-run-drawer__content')
    expect(pre.text()).toContain('第一行输出')
    expect(pre.text()).toContain('第二行输出')
  })

  it('关闭状态不渲染内容', () => {
    const wrapper = mountDrawer({ modelValue: false, source: 'writer', content: '正文' })

    expect(wrapper.find('.el-drawer-stub').exists()).toBe(false)
  })

  it('内容为空时展示降级提示而非空白正文', () => {
    const wrapper = mountDrawer({ modelValue: true, source: 'writer', content: '   ' })

    expect(wrapper.find('.chat-subagent-run-drawer__content').exists()).toBe(false)
    expect(wrapper.get('.chat-subagent-run-drawer__hint').text()).toContain('尚未产出文本')
  })

  it('工具调用逐条折叠渲染，点击展开输出再点收起', async () => {
    const wrapper = mountDrawer({
      modelValue: true,
      source: 'writer',
      content: '正文',
      toolCalls: [
        { name: 'search', content: 'hit one' },
        { name: 'run', content: 'hit two' },
      ],
    })

    const headers = wrapper.findAll('.chat-subagent-run-drawer__tool-header')
    expect(headers).toHaveLength(2)
    expect(headers[0].text()).toContain('search')
    expect(wrapper.findAll('.chat-subagent-run-drawer__tool-body')).toHaveLength(0)

    await headers[0].trigger('click')
    expect(wrapper.get('.chat-subagent-run-drawer__tool-body').text()).toBe('hit one')

    await wrapper.findAll('.chat-subagent-run-drawer__tool-header')[0].trigger('click')
    expect(wrapper.findAll('.chat-subagent-run-drawer__tool-body')).toHaveLength(0)
  })

  it('文本为空但有工具调用时展示工具面板而非降级提示', () => {
    const wrapper = mountDrawer({
      modelValue: true,
      source: 'writer',
      content: '',
      toolCalls: [{ name: 'search', content: 'hit' }],
    })

    expect(wrapper.find('.chat-subagent-run-drawer__hint').exists()).toBe(false)
    expect(wrapper.findAll('.chat-subagent-run-drawer__tool-header')).toHaveLength(1)
  })

  it('关闭抽屉 emit update:modelValue false', async () => {
    const wrapper = mountDrawer({ modelValue: true, source: 'writer', content: '正文' })

    await wrapper.get('.el-drawer-stub__close').trigger('click')
    expect(wrapper.emitted('update:modelValue')).toEqual([[false]])
  })
})
