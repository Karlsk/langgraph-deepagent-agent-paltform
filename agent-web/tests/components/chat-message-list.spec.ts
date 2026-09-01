// @vitest-environment happy-dom
/**
 * ChatMessageList 消息流渲染测试（G4 spec-g4-chat §9.3 P0 清单）：
 * 纯展示组件（零 Element Plus 依赖、零 API 调用），覆盖气泡对齐修饰、
 * subagent_run 执行卡片（名称 / 运行态 / 摘要 / 点击 emit）、streaming 光标、
 * tool_call 折叠展开、summary 细条、decisions 胶囊。
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

  it('subagent_run 渲染执行卡片：名称徽标 + 摘要 + 查看详情', () => {
    const items: ChatViewItem[] = [
      { kind: 'subagent_run', source: 'writer', content: '子代理输出内容', running: false, toolCalls: [], closed: false },
    ]
    const wrapper = mount(ChatMessageList, { props: { items } })

    const card = wrapper.get('.chat-message-list__run')
    expect(card.text()).toContain('writer')
    expect(card.text()).toContain('子代理输出内容')
    expect(card.text()).toContain('查看详情')
    expect(wrapper.find('.chat-message-list__run-status--running').exists()).toBe(false)
    expect(wrapper.find('.chat-message-list__run-tools').exists()).toBe(false)
  })

  it('subagent_run 带工具调用时显示计数徽标', () => {
    const items: ChatViewItem[] = [
      {
        kind: 'subagent_run',
        source: 'writer',
        content: '正文',
        running: false,
        toolCalls: [
          { name: 'a', content: 'out1' },
          { name: 'b', content: 'out2' },
        ],
        closed: true,
      },
    ]
    const wrapper = mount(ChatMessageList, { props: { items } })

    expect(wrapper.get('.chat-message-list__run-tools').text()).toContain('2 次工具调用')
  })

  it('subagent_run 长内容摘要截断并带总字数', () => {
    const content = '长'.repeat(120)
    const items: ChatViewItem[] = [
      { kind: 'subagent_run', source: 'writer', content, running: false, toolCalls: [], closed: false },
    ]
    const wrapper = mount(ChatMessageList, { props: { items } })

    const summary = wrapper.get('.chat-message-list__run-summary').text()
    expect(summary).toContain('…')
    expect(summary).toContain('共 120 字')
  })

  it('running 卡片仅流式中显示「执行中…」；点击整卡 emit open-run 索引', async () => {
    const items: ChatViewItem[] = [
      { kind: 'message', role: 'user', content: 'q' },
      { kind: 'subagent_run', source: 'writer', content: '部分', running: true, toolCalls: [], closed: false },
    ]

    const live = mount(ChatMessageList, { props: { items, streaming: true } })
    expect(live.find('.chat-message-list__run-status--running').exists()).toBe(true)

    const idle = mount(ChatMessageList, { props: { items, streaming: false } })
    expect(idle.find('.chat-message-list__run-status--running').exists()).toBe(false)

    await live.get('.chat-message-list__run').trigger('click')
    expect(live.emitted('open-run')).toEqual([[1]])
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
