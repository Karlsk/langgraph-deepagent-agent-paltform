// @vitest-environment happy-dom
/**
 * HttpNodeForm 组件测试（前端 spec §5.1，contract-change spec-01 §2.4）。
 *
 * 零真实网络：纯表单组件，Element Plus 一律 stub。
 *
 * 重点验证 headers 键值行编辑：
 *   - 回填：config.headers 的每个键渲染成一行 key/value；
 *   - 新增一行 → emit 的 config.headers 含 {K: V}；
 *   - 删除一行 → 该键从 config.headers 消失；
 *   - 键为空的行不写进 config（不产生脏键）；
 *   - config.headers 缺失时不崩且初始为空；
 *   - readonly 下输入禁用、新增/删除按钮隐藏；
 *   - 编辑 headers 不得丢掉表单不认识的既有键（timeout / max_retries 等）。
 */
import { describe, expect, it } from 'vitest'
import { defineComponent, h } from 'vue'
import type { Component } from 'vue'
import { mount } from '@vue/test-utils'

import HttpNodeForm from '@/views/workflow/panel/HttpNodeForm.vue'

const ElFormStub = defineComponent({
  name: 'ElForm',
  props: { labelPosition: String, disabled: { type: Boolean, default: false } },
  setup(props, { slots }) {
    return () => h('div', { class: 'el-form-stub', 'data-disabled': String(props.disabled) }, slots.default?.())
  },
})

const ElFormItemStub = defineComponent({
  name: 'ElFormItem',
  props: { label: String, prop: String },
  setup(props, { slots }) {
    return () => h('div', { class: 'el-form-item-stub', 'data-label': props.label ?? '' }, slots.default?.())
  },
})

const ElInputStub = defineComponent({
  name: 'ElInput',
  props: { modelValue: String, type: String, rows: Number, placeholder: String, disabled: Boolean },
  emits: ['update:modelValue', 'change'],
  setup(props) {
    return () =>
      h('input', {
        class: 'el-input-stub',
        'data-type': props.type ?? 'text',
        'data-disabled': String(props.disabled === true),
        value: props.modelValue ?? '',
        placeholder: props.placeholder ?? '',
      })
  },
})

const ElSelectStub = defineComponent({
  name: 'ElSelect',
  props: { modelValue: String, disabled: Boolean },
  emits: ['update:modelValue', 'change'],
  setup(props, { slots }) {
    return () => h('div', { class: 'el-select-stub', 'data-value': props.modelValue ?? '' }, slots.default?.())
  },
})

const ElOptionStub = defineComponent({
  name: 'ElOption',
  props: { value: String, label: String },
  setup(props) {
    return () => h('div', { class: 'el-option-stub', 'data-value': props.value ?? '' })
  },
})

const ElSwitchStub = defineComponent({
  name: 'ElSwitch',
  props: { modelValue: { type: Boolean, default: false } },
  emits: ['update:modelValue', 'change'],
  setup(props) {
    return () => h('div', { class: 'el-switch-stub', 'data-value': String(props.modelValue) })
  },
})

const STUBS = {
  ElForm: ElFormStub,
  ElFormItem: ElFormItemStub,
  ElInput: ElInputStub,
  ElSelect: ElSelectStub,
  ElOption: ElOptionStub,
  ElSwitch: ElSwitchStub,
}

function mountForm(config: Record<string, unknown> = {}, readonly = false) {
  return mount(HttpNodeForm as Component, {
    props: { config, readonly },
    global: { stubs: STUBS },
  })
}

type Wrapper = ReturnType<typeof mountForm>

/** 按 data-testid 取第 index 个 header 输入框（key 与 value 各成一列）。 */
function headerInput(wrapper: Wrapper, testId: string, index: number) {
  const matches = wrapper
    .findAllComponents(ElInputStub)
    .filter((input) => input.attributes('data-testid') === testId)
  return matches[index]
}

async function typeInto(input: ReturnType<typeof headerInput>, value: string) {
  await input!.vm.$emit('change', value)
}

function headerRows(wrapper: Wrapper) {
  return wrapper.findAll('.http-node-form__header-row')
}

function lastConfig(wrapper: Wrapper): Record<string, unknown> {
  const events = wrapper.emitted('update:config')
  expect(events, 'expected an update:config emit').toBeTruthy()
  return events![events!.length - 1]![0] as Record<string, unknown>
}

const BASE_CONFIG = {
  url: '{sdn_base_url}/oauth/token',
  method: 'POST',
  body_template: '{"username": "{username}"}',
  response_path: 'data',
  mock_enabled: false,
  mock_responses: {},
}

describe('HttpNodeForm headers 回填', () => {
  it('config.headers 的每个键渲染成一行 key/value', () => {
    const wrapper = mountForm({ ...BASE_CONFIG, headers: { Accept: 'application/json', 'X-Trace': '1' } })
    expect(headerRows(wrapper)).toHaveLength(2)
    expect(headerInput(wrapper, 'header-key', 0)!.props('modelValue')).toBe('Accept')
    expect(headerInput(wrapper, 'header-value', 0)!.props('modelValue')).toBe('application/json')
    expect(headerInput(wrapper, 'header-key', 1)!.props('modelValue')).toBe('X-Trace')
  })

  it('config.headers 为 undefined 时不崩且初始为空', () => {
    const wrapper = mountForm({ ...BASE_CONFIG })
    expect(headerRows(wrapper)).toHaveLength(0)
    expect(wrapper.find('.http-node-form').exists()).toBe(true)
  })

  it('config.headers 为非对象脏值时按空处理', () => {
    const wrapper = mountForm({ ...BASE_CONFIG, headers: 'not-an-object' })
    expect(headerRows(wrapper)).toHaveLength(0)
  })
})

describe('HttpNodeForm headers 编辑', () => {
  it('新增一行并填入 K/V → emit 的 config.headers 含该键', async () => {
    const wrapper = mountForm({ ...BASE_CONFIG })
    await wrapper.find('.http-node-form__header-add').trigger('click')
    expect(headerRows(wrapper)).toHaveLength(1)

    await typeInto(headerInput(wrapper, 'header-key', 0), 'Authorization')
    await typeInto(headerInput(wrapper, 'header-value', 0), 'Bearer {token}')

    expect(lastConfig(wrapper).headers).toEqual({ Authorization: 'Bearer {token}' })
  })

  it('删除一行 → 该键从 config.headers 消失，其余保留', async () => {
    const wrapper = mountForm({ ...BASE_CONFIG, headers: { Accept: 'application/json', 'X-Trace': '1' } })
    await headerRows(wrapper)[0]!.find('.http-node-form__header-delete').trigger('click')

    expect(lastConfig(wrapper).headers).toEqual({ 'X-Trace': '1' })
  })

  it('改键名 → 旧键消失、新键带上原值', async () => {
    const wrapper = mountForm({ ...BASE_CONFIG, headers: { Accept: 'application/json' } })
    await typeInto(headerInput(wrapper, 'header-key', 0), 'Content-Type')

    expect(lastConfig(wrapper).headers).toEqual({ 'Content-Type': 'application/json' })
  })

  it('键为空的行不写进 config（不产生脏键）', async () => {
    const wrapper = mountForm({ ...BASE_CONFIG, headers: { Accept: 'application/json' } })
    await wrapper.find('.http-node-form__header-add').trigger('click')
    await typeInto(headerInput(wrapper, 'header-value', 1), 'orphan')

    expect(lastConfig(wrapper).headers).toEqual({ Accept: 'application/json' })
  })

  it('重复键后者覆盖前者，config.headers 始终是对象而非行数组', async () => {
    const wrapper = mountForm({ ...BASE_CONFIG, headers: { Accept: 'text/plain' } })
    await wrapper.find('.http-node-form__header-add').trigger('click')
    await typeInto(headerInput(wrapper, 'header-key', 1), 'Accept')
    await typeInto(headerInput(wrapper, 'header-value', 1), 'application/json')

    expect(lastConfig(wrapper).headers).toEqual({ Accept: 'application/json' })
  })

  it('编辑 headers 不丢掉表单不认识的既有键（timeout / max_retries）', async () => {
    const wrapper = mountForm({ ...BASE_CONFIG, timeout: 12.5, max_retries: 2, headers: { Accept: '*/*' } })
    await wrapper.find('.http-node-form__header-add').trigger('click')
    await typeInto(headerInput(wrapper, 'header-key', 1), 'X-Trace')
    await typeInto(headerInput(wrapper, 'header-value', 1), '1')

    const config = lastConfig(wrapper)
    expect(config.timeout).toBe(12.5)
    expect(config.max_retries).toBe(2)
    expect(config.url).toBe('{sdn_base_url}/oauth/token')
  })

  it('提交体不夹带行结构（rows / id 等内部状态不出现在 config 里）', async () => {
    const wrapper = mountForm({ ...BASE_CONFIG, headers: { Accept: '*/*' } })
    await typeInto(headerInput(wrapper, 'header-value', 0), 'application/json')

    const config = lastConfig(wrapper)
    expect(Object.keys(config)).not.toContain('headerRows')
    expect(Object.keys(config)).not.toContain('rows')
    expect(config.headers).toEqual({ Accept: 'application/json' })
  })
})

describe('HttpNodeForm headers readonly 门禁', () => {
  it('readonly=true → key/value 输入禁用，且无新增/删除按钮', () => {
    const wrapper = mountForm({ ...BASE_CONFIG, headers: { Accept: '*/*' } }, true)
    expect(headerInput(wrapper, 'header-key', 0)!.props('disabled')).toBe(true)
    expect(headerInput(wrapper, 'header-value', 0)!.props('disabled')).toBe(true)
    expect(wrapper.find('.http-node-form__header-add').exists()).toBe(false)
    expect(wrapper.find('.http-node-form__header-delete').exists()).toBe(false)
  })

  it('readonly=false（默认）→ 新增与删除按钮可见', () => {
    const wrapper = mountForm({ ...BASE_CONFIG, headers: { Accept: '*/*' } })
    expect(wrapper.find('.http-node-form__header-add').exists()).toBe(true)
    expect(wrapper.find('.http-node-form__header-delete').exists()).toBe(true)
  })
})
