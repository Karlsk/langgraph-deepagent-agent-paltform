// @vitest-environment happy-dom
/**
 * StateSchemaPanel 组件测试（spec-13）：
 * - 验证 schema → 渲染对应行数 + 字段值；
 * - 验证新增字段（默认 type=str）→ emit 含新键；
 * - 验证删除字段 → emit 去除该键；
 * - 验证改 type / reducer → emit 更新；reducer 仅 add/last/null；
 * - 验证字段名重复 / 非法标识符 → 内联校验错误，不发无效 emit；
 * - 验证 readonly=true → 全控件禁用、增删按钮隐藏。
 */
import { describe, expect, it } from 'vitest'
import { mount } from '@vue/test-utils'
import type { Component } from 'vue'

import StateSchemaPanel from '@/views/workflow/panel/StateSchemaPanel.vue'
import type { StateFieldDTO } from '@/api/workflow'

function makeSchema(overrides: Record<string, Partial<StateFieldDTO>> = {}): Record<string, StateFieldDTO> {
  const base: Record<string, StateFieldDTO> = {
    messages: { type: 'list', default: [], description: '对话消息', reducer: 'add' },
    user_input: { type: 'str', description: '用户输入' },
  }
  for (const [key, partial] of Object.entries(overrides)) {
    base[key] = { type: 'str', ...partial }
  }
  return base
}

function mountPanel(props: Record<string, unknown> = {}) {
  return mount(StateSchemaPanel as Component, {
    props: {
      modelValue: makeSchema(),
      ...props,
    },
  })
}

describe('StateSchemaPanel state_schema 编辑', () => {
  it('传入 schema → 渲染对应行数', () => {
    const wrapper = mountPanel()
    const rows = wrapper.findAll('.state-schema-panel__row')
    expect(rows).toHaveLength(2)
  })

  it('渲染字段名 + type + reducer 值', () => {
    const wrapper = mountPanel()
    const nameInputs = wrapper.findAll('.state-schema-panel__name-input input')
    expect(nameInputs).toHaveLength(2)
    expect((nameInputs[0].element as HTMLInputElement).value).toBe('messages')
    expect((nameInputs[1].element as HTMLInputElement).value).toBe('user_input')
  })

  it('新增字段 → emit 含新键（默认 type=str）', async () => {
    const wrapper = mountPanel()
    const nameInput = wrapper.find('.state-schema-panel__add-name-input input')
    await nameInput.setValue('new_field')
    const addBtn = wrapper.find('.state-schema-panel__add-btn')
    await addBtn.trigger('click')

    const emitted = wrapper.emitted('update:modelValue')
    expect(emitted).toBeTruthy()
    const lastEmit = emitted![emitted!.length - 1][0] as Record<string, StateFieldDTO>
    expect(lastEmit).toHaveProperty('new_field')
    expect(lastEmit.new_field.type).toBe('str')
    expect(Object.keys(lastEmit)).toHaveLength(3)
  })

  it('删除字段 → emit 去除该键', async () => {
    const wrapper = mountPanel()
    const deleteBtns = wrapper.findAll('.state-schema-panel__delete-btn')
    expect(deleteBtns).toHaveLength(2)
    await deleteBtns[0].trigger('click')

    const emitted = wrapper.emitted('update:modelValue')
    expect(emitted).toBeTruthy()
    const lastEmit = emitted![emitted!.length - 1][0] as Record<string, StateFieldDTO>
    expect(lastEmit).not.toHaveProperty('messages')
    expect(Object.keys(lastEmit)).toHaveLength(1)
  })

  it('改 type → emit 更新', async () => {
    const wrapper = mountPanel()
    const typeSelects = wrapper.findAll('.state-schema-panel__type-select select')
    expect(typeSelects.length).toBeGreaterThanOrEqual(1)
    await typeSelects[1].setValue('int')

    const emitted = wrapper.emitted('update:modelValue')
    expect(emitted).toBeTruthy()
    const lastEmit = emitted![emitted!.length - 1][0] as Record<string, StateFieldDTO>
    expect(lastEmit.user_input.type).toBe('int')
  })

  it('改 reducer → emit 更新；reducer 仅 add/last/null', async () => {
    const wrapper = mountPanel()
    const reducerSelects = wrapper.findAll('.state-schema-panel__reducer-select select')
    expect(reducerSelects.length).toBeGreaterThanOrEqual(1)
    await reducerSelects[0].setValue('last')

    const emitted = wrapper.emitted('update:modelValue')
    expect(emitted).toBeTruthy()
    const lastEmit = emitted![emitted!.length - 1][0] as Record<string, StateFieldDTO>
    expect(lastEmit.messages.reducer).toBe('last')
  })

  it('字段名重复 → 内联校验错误，不发无效 emit', async () => {
    const wrapper = mountPanel()
    const nameInputs = wrapper.findAll('.state-schema-panel__name-input input')
    await nameInputs[1].setValue('messages')

    const error = wrapper.find('.state-schema-panel__error')
    expect(error.exists()).toBe(true)

    const emitted = wrapper.emitted('update:modelValue')
    expect(emitted).toBeUndefined()
  })

  it('字段名非法标识符（空格）→ 内联校验错误', async () => {
    const wrapper = mountPanel()
    const nameInputs = wrapper.findAll('.state-schema-panel__name-input input')
    await nameInputs[0].setValue('bad name')

    const error = wrapper.find('.state-schema-panel__error')
    expect(error.exists()).toBe(true)
  })

  it('字段名非法标识符（数字开头）→ 内联校验错误', async () => {
    const wrapper = mountPanel()
    const nameInputs = wrapper.findAll('.state-schema-panel__name-input input')
    await nameInputs[0].setValue('1field')

    const error = wrapper.find('.state-schema-panel__error')
    expect(error.exists()).toBe(true)
  })

  it('readonly=true → 全控件禁用、增删按钮隐藏', () => {
    const wrapper = mountPanel({ readonly: true })
    const addBtn = wrapper.find('.state-schema-panel__add-btn')
    expect(addBtn.exists()).toBe(false)

    const deleteBtns = wrapper.findAll('.state-schema-panel__delete-btn')
    expect(deleteBtns).toHaveLength(0)

    const nameInputs = wrapper.findAll('.state-schema-panel__name-input input')
    nameInputs.forEach(input => {
      expect((input.element as HTMLInputElement).disabled).toBe(true)
    })
  })
})
