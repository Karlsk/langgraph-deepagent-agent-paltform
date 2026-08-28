// @vitest-environment happy-dom
/**
 * ChatHilCard HIL 审批卡片测试（G4 spec-g4-chat §5.2/§9.3）：
 * action_requests 逐卡列出（tool 名 + args 折叠查看）、每卡批准 / 拒绝
 * 独立选择（默认全部批准）、提交 emit 布尔数组、submitting 锁提交钮。
 */
import { describe, expect, it } from 'vitest'
import { defineComponent, h } from 'vue'
import { mount } from '@vue/test-utils'

import ChatHilCard from '@/views/chat/ChatHilCard.vue'
import type { InterruptPayload } from '@/types'

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

const INTERRUPT: InterruptPayload = {
  action_requests: [
    { tool: 'write_file', args: { path: 'a.txt' } },
    { tool: 'run_command', args: { cmd: 'ls' } },
  ],
}

function mountCard(props: {
  interrupt: InterruptPayload
  submitting?: boolean
}) {
  return mount(ChatHilCard, {
    props,
    global: { stubs: { ElButton: ElButtonStub } },
  })
}

/** 定位文本匹配的按钮（批准 / 拒绝 / 提交决定） */
function findButton(wrapper: ReturnType<typeof mountCard>, text: string) {
  const button = wrapper.findAll('button').find((item) => item.text() === text)
  if (!button) throw new Error(`button "${text}" not found`)
  return button
}

describe('ChatHilCard', () => {
  it('逐卡列出 action_requests 的 tool 名与参数', () => {
    const wrapper = mountCard({ interrupt: INTERRUPT })

    const tools = wrapper.findAll('.chat-hil-card__tool')
    expect(tools.map((node) => node.text())).toEqual(['write_file', 'run_command'])
    const args = wrapper.findAll('.chat-hil-card__args')
    expect(args[0].text()).toContain('path')
    expect(args[1].text()).toContain('cmd')
  })

  it('默认全部批准：提交 emit 全 true 数组', async () => {
    const wrapper = mountCard({ interrupt: INTERRUPT })

    await findButton(wrapper, '提交决定').trigger('click')
    expect(wrapper.emitted('submit')).toEqual([[[true, true]]])
  })

  it('逐卡独立选择：第二项切拒绝后提交 emit [true, false]', async () => {
    const wrapper = mountCard({ interrupt: INTERRUPT })

    const rejectButtons = wrapper
      .findAll('button')
      .filter((node) => node.text() === '拒绝')
    await rejectButtons[1].trigger('click')

    await findButton(wrapper, '提交决定').trigger('click')
    expect(wrapper.emitted('submit')).toEqual([[[true, false]]])
  })

  it('submitting 时提交按钮禁用且点击不 emit', async () => {
    const wrapper = mountCard({ interrupt: INTERRUPT, submitting: true })

    const submit = findButton(wrapper, '提交决定')
    expect(submit.attributes('data-disabled')).toBe('true')

    await submit.trigger('click')
    expect(wrapper.emitted('submit')).toBeUndefined()
  })
})
