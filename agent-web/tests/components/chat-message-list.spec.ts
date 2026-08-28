// @vitest-environment happy-dom
/**
 * ChatMessageList 消息流渲染测试（G4 spec-g4-chat §9.3 P0 清单）：
 * 纯展示组件（零 Element Plus 依赖、零 API 调用），覆盖气泡对齐修饰、
 * source 胶囊（coordinator 不显示）、streaming 光标、tool_call 折叠展开、
 * summary 细条、decisions 胶囊。
 */
import { describe, expect, it } from 'vitest'
import { mount } from '@vue/test-utils'

import ChatMessageList from '@/views/chat/ChatMessageList.vue'
import type { ChatViewItem } from '@/composables/useChatStream'

describe('ChatMessageList', () => {
  it('user 气泡带 user 修饰 class，assistant 不带', () => {
    const items: ChatViewItem[] = [
      { kind: 'message', role: 'user', content: '你好' },
      { kind: 'message', role: 'assistant', content: '回复' },
    ]
    const wrapper = mount(ChatMessageList, { props: { items } })

    const rows = wrapper.findAll('.chat-message-list__row')
    expect(rows[0].classes()).toContain('chat-message-list__row--user')
    expect(rows[1].classes()).toContain('chat-message-list__row--assistant')
    expect(rows[0].text()).toContain('你好')
  })

  it('subagent source 渲染胶囊，coordinator（null）不渲染', () => {
    const items: ChatViewItem[] = [
      { kind: 'message', role: 'assistant', content: '正文', source: null },
      { kind: 'message', role: 'assistant', content: '子代理输出', source: 'writer' },
    ]
    const wrapper = mount(ChatMessageList, { props: { items } })

    const badges = wrapper.findAll('.chat-message-list__source')
    expect(badges).toHaveLength(1)
    expect(badges[0].text()).toBe('writer')
  })

  it('streaming 时光标只挂在末尾 assistant 气泡', () => {
    const items: ChatViewItem[] = [
      { kind: 'message', role: 'user', content: 'q' },
      { kind: 'message', role: 'assistant', content: 'a', source: null },
    ]
    const wrapper = mount(ChatMessageList, {
      props: { items, streaming: true },
    })

    const cursors = wrapper.findAll('.chat-message-list__cursor')
    expect(cursors).toHaveLength(1)

    const idle = mount(ChatMessageList, { props: { items, streaming: false } })
    expect(idle.findAll('.chat-message-list__cursor')).toHaveLength(0)
  })

  it('tool_call 默认折叠，点击 header 展开输出，再点收起', async () => {
    const items: ChatViewItem[] = [
      { kind: 'tool_call', name: 'echo', content: 'echo out' },
    ]
    const wrapper = mount(ChatMessageList, { props: { items } })

    const header = wrapper.get('.chat-message-list__tool-header')
    expect(header.text()).toContain('echo')
    expect(header.text()).toContain('echo out')
    expect(wrapper.find('.chat-message-list__tool-body').exists()).toBe(false)

    await header.trigger('click')
    expect(wrapper.get('.chat-message-list__tool-body').text()).toBe('echo out')

    await header.trigger('click')
    expect(wrapper.find('.chat-message-list__tool-body').exists()).toBe(false)
  })

  it('summary 行渲染「上下文已压缩」细条与摘要文本', () => {
    const items: ChatViewItem[] = [{ kind: 'summary', content: '保留了任务目标' }]
    const wrapper = mount(ChatMessageList, { props: { items } })

    const bar = wrapper.get('.chat-message-list__summary')
    expect(bar.text()).toContain('上下文已压缩')
    expect(bar.text()).toContain('保留了任务目标')
  })

  it('decision 胶囊展示批准 / 拒绝计数', () => {
    const items: ChatViewItem[] = [
      { kind: 'decision', approved: 1, rejected: 1 },
    ]
    const wrapper = mount(ChatMessageList, { props: { items } })

    const pill = wrapper.get('.chat-message-list__decision')
    expect(pill.text()).toContain('已批准 1')
    expect(pill.text()).toContain('已拒绝 1')
  })
})
