// @vitest-environment happy-dom
/**
 * ExecuteInputFields 组件测试（S21 前端侧）。
 *
 * 纯原生控件（与 StateSchemaPanel 一致），不依赖 Element Plus：
 *   - 按 control 渲染对应控件类型
 *   - 输入回写 modelValue（字符串原样，解析留给 composable）
 *   - 展示对应字段的错误文案
 *   - 无字段时降级提示
 */
import { describe, expect, it } from 'vitest'
import { mount } from '@vue/test-utils'

import ExecuteInputFields from '@/views/workflow/panel/ExecuteInputFields.vue'
import type { ExecuteField } from '@/composables/useExecuteForm'

const FIELDS: ExecuteField[] = [
  { key: 'user_id', type: 'str', control: 'text', description: '调用方标识' },
  { key: 'max_tokens', type: 'int', control: 'number', description: '' },
  { key: 'verbose', type: 'bool', control: 'checkbox', description: '' },
  { key: 'payload', type: 'dict', control: 'json', description: '透传载荷' },
]

function mountFields(props?: Record<string, unknown>) {
  return mount(ExecuteInputFields, {
    props: { fields: FIELDS, modelValue: {}, ...props },
  })
}

describe('ExecuteInputFields', () => {
  it('每个字段渲染一行，并带字段名标签', () => {
    const wrapper = mountFields()
    const rows = wrapper.findAll('.execute-field')
    expect(rows).toHaveLength(4)
    expect(rows[0].text()).toContain('user_id')
    expect(rows[0].text()).toContain('调用方标识')
  })

  it('按 control 渲染控件类型', () => {
    const wrapper = mountFields()
    expect(wrapper.find('.execute-field__text').exists()).toBe(true)
    expect(wrapper.find('.execute-field__number').exists()).toBe(true)
    expect(wrapper.find('.execute-field__checkbox').exists()).toBe(true)
    expect(wrapper.find('.execute-field__json').exists()).toBe(true)
  })

  it('文本输入 → emit update:modelValue 合并该键', async () => {
    const wrapper = mountFields({ modelValue: { payload: '{}' } })
    await wrapper.find('.execute-field__text').setValue('u-001')

    const emitted = wrapper.emitted('update:modelValue')
    expect(emitted).toBeTruthy()
    expect(emitted!.at(-1)![0]).toEqual({ payload: '{}', user_id: 'u-001' })
  })

  it('勾选 checkbox → emit "true"，取消 → "false"', async () => {
    const wrapper = mountFields()
    const checkbox = wrapper.find('.execute-field__checkbox')
    await checkbox.setValue(true)
    expect(wrapper.emitted('update:modelValue')!.at(-1)![0]).toEqual({ verbose: 'true' })

    await checkbox.setValue(false)
    expect(wrapper.emitted('update:modelValue')!.at(-1)![0]).toEqual({ verbose: 'false' })
  })

  it('已传入的值回填到控件', () => {
    const wrapper = mountFields({ modelValue: { user_id: 'u-001', verbose: 'true' } })
    expect((wrapper.find('.execute-field__text').element as HTMLInputElement).value).toBe('u-001')
    expect((wrapper.find('.execute-field__checkbox').element as HTMLInputElement).checked).toBe(true)
  })

  it('errors 命中字段 → 展示错误文案与错误样式', () => {
    const wrapper = mountFields({ errors: { payload: '必须是合法 JSON' } })
    const error = wrapper.find('.execute-field__error')
    expect(error.exists()).toBe(true)
    expect(error.text()).toContain('必须是合法 JSON')
    expect(wrapper.findAll('.execute-field--error')).toHaveLength(1)
  })

  it('无字段 → 降级提示，不渲染输入行', () => {
    const wrapper = mountFields({ fields: [] })
    expect(wrapper.findAll('.execute-field')).toHaveLength(0)
    expect(wrapper.text()).toMatch(/没有|无自定义|自定义字段/)
  })
})
