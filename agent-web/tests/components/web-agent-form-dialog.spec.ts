// @vitest-environment happy-dom
/**
 * WebAgentFormDialog 组件测试：stub 掉 Element Plus 组件（不做真实渲染），
 * 以作用域插槽探针观察 formModel 与 mode（零真实网络、零真实校验器）。
 */
import { beforeEach, describe, expect, it, vi } from 'vitest'
import { defineComponent, h } from 'vue'
import { flushPromises, mount } from '@vue/test-utils'
import type { VueWrapper } from '@vue/test-utils'

import WebAgentFormDialog from '@/components/WebAgentFormDialog.vue'

const ElDialogStub = defineComponent({
  name: 'ElDialog',
  props: { modelValue: Boolean, title: String, width: String },
  emits: ['update:modelValue', 'close'],
  setup(props, { slots }) {
    return () =>
      props.modelValue
        ? h(
            'div',
            { class: 'el-dialog-stub', 'data-title': props.title },
            [
              slots.default ? slots.default() : undefined,
              slots.footer ? slots.footer() : undefined,
            ],
          )
        : null
  },
})

let validateMock: ReturnType<typeof vi.fn>

const ElFormStub = defineComponent({
  name: 'ElForm',
  setup(_, { expose, slots }) {
    expose({
      validate: () => validateMock(),
      clearValidate: () => undefined,
    })
    return () => h('form', { class: 'el-form-stub' }, slots.default ? slots.default() : undefined)
  },
})

const ElButtonStub = defineComponent({
  name: 'ElButton',
  props: { loading: Boolean },
  emits: ['click'],
  setup(props, { emit, slots, attrs }) {
    return () =>
      h(
        'button',
        {
          class: attrs.class,
          'data-loading': props.loading ? 'true' : 'false',
          onClick: () => emit('click'),
        },
        slots.default ? slots.default() : undefined,
      )
  },
})

interface SlotProbe {
  form: Record<string, unknown>
  mode: 'create' | 'edit'
}

let slotProbe: SlotProbe | null = null

interface DialogExposed {
  open: (data?: Record<string, unknown>) => void
  close: () => void
  setSubmitting: (value: boolean) => void
}

function mountDialog(props: Record<string, unknown> = {}): VueWrapper {
  return mount(WebAgentFormDialog, {
    props: { modelValue: true, title: '测试弹窗', ...props },
    slots: {
      default: (params: SlotProbe) => {
        slotProbe = params
        return h('input', {
          class: 'probe',
          value: (params.form.name as string) ?? '',
          onInput: (event: Event) => {
            params.form.name = (event.target as HTMLInputElement).value
          },
        })
      },
    },
    global: {
      stubs: { ElDialog: ElDialogStub, ElForm: ElFormStub, ElButton: ElButtonStub },
    },
  })
}

function confirmButton(wrapper: VueWrapper) {
  const button = wrapper
    .findAll('button')
    .find((item) => item.classes().includes('app-btn--primary'))
  if (!button) {
    throw new Error('confirm button not found')
  }
  return button
}

beforeEach(() => {
  validateMock = vi.fn().mockResolvedValue(true)
  slotProbe = null
})

describe('WebAgentFormDialog 通用表单弹窗', () => {
  it('open() 新增模式：mode=create、确认后提交空对象', async () => {
    const wrapper = mountDialog()
    ;(wrapper.vm as unknown as DialogExposed).open()
    await flushPromises()

    expect(wrapper.emitted('update:modelValue')?.[0]).toEqual([true])
    expect(slotProbe?.mode).toBe('create')

    await confirmButton(wrapper).trigger('click')
    expect(wrapper.emitted('submit')?.[0]).toEqual([{}])
  })

  it('open(data) 编辑模式：回填表单、mode=edit、提交表单数据', async () => {
    const wrapper = mountDialog()
    ;(wrapper.vm as unknown as DialogExposed).open({ name: 'demo' })
    await flushPromises()

    expect(slotProbe?.mode).toBe('edit')
    expect(slotProbe?.form).toEqual({ name: 'demo' })

    await confirmButton(wrapper).trigger('click')
    expect(wrapper.emitted('submit')?.[0]).toEqual([{ name: 'demo' }])
  })

  it('校验失败不触发 submit', async () => {
    validateMock.mockRejectedValue(new Error('invalid field'))
    const wrapper = mountDialog()
    ;(wrapper.vm as unknown as DialogExposed).open({ name: 'demo' })
    await flushPromises()

    await confirmButton(wrapper).trigger('click')
    await flushPromises()

    expect(validateMock).toHaveBeenCalled()
    expect(wrapper.emitted('submit')).toBeUndefined()
  })

  it('关闭后自动重置：表单恢复 open() 时的初始快照', async () => {
    const wrapper = mountDialog()
    ;(wrapper.vm as unknown as DialogExposed).open({ name: 'demo' })
    await flushPromises()

    // 模拟用户修改表单字段
    await wrapper.find('input.probe').setValue('changed')
    expect(slotProbe?.form.name).toBe('changed')

    // 触发弹窗关闭（模拟 ElDialog 关闭事件）
    wrapper.findComponent(ElDialogStub).vm.$emit('close')
    await flushPromises()

    expect(slotProbe?.form).toEqual({ name: 'demo' })
    await confirmButton(wrapper).trigger('click')
    expect(wrapper.emitted('submit')?.[0]).toEqual([{ name: 'demo' }])
  })

  it('setSubmitting 驱动确认按钮 loading', async () => {
    const wrapper = mountDialog()
    ;(wrapper.vm as unknown as DialogExposed).open()
    await flushPromises()

    expect(confirmButton(wrapper).attributes('data-loading')).toBe('false')
    ;(wrapper.vm as unknown as DialogExposed).setSubmitting(true)
    await flushPromises()
    expect(confirmButton(wrapper).attributes('data-loading')).toBe('true')
    ;(wrapper.vm as unknown as DialogExposed).setSubmitting(false)
    await flushPromises()
    expect(confirmButton(wrapper).attributes('data-loading')).toBe('false')
  })

  it('取消按钮触发关闭（emit update:modelValue false）', async () => {
    const wrapper = mountDialog()
    ;(wrapper.vm as unknown as DialogExposed).open()
    await flushPromises()

    const cancel = wrapper
      .findAll('button')
      .find((item) => item.classes().includes('app-btn--secondary'))
    await cancel?.trigger('click')

    expect(wrapper.emitted('update:modelValue')?.at(-1)).toEqual([false])
  })
})
