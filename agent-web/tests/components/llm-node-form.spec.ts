// @vitest-environment happy-dom
/**
 * LlmNodeForm 测试（workflow-llm-provider-integration Stage D）。
 *
 * 零真实网络：mock `@/api/provider`，composable 走真实实现（模块单例，
 * 每个用例前 refresh 一次以重置状态）。
 *
 * 验证：
 *   - 模型下拉按 provider 分组，选项来自 provider 系统
 *   - 选中 → emit provider_ref + model_name(=model_id) + llm_type(映射值)
 *   - 手填裸模型名（allow-create）→ provider_ref 置空，走 env 回退
 *   - 回填：provider_ref 命中 / 仅 model_name 反查命中 / 都不中
 *   - 既有 config 键（max_retries 等）编辑后不丢失
 *   - H6 守卫：不出现 api_key
 *   - readonly 门禁
 */
import { beforeEach, describe, expect, it, vi } from 'vitest'
import { defineComponent, h } from 'vue'
import { flushPromises, mount } from '@vue/test-utils'

import LlmNodeForm from '@/views/workflow/panel/LlmNodeForm.vue'
import { useProviderModels } from '@/composables/useProviderModels'
import type { ModelConfigRow, ProviderRow } from '@/api/provider'

const listProvidersMock = vi.fn()
const listAllProviderModelsMock = vi.fn()
vi.mock('@/api/provider', () => ({
  listProviders: () => listProvidersMock(),
  listAllProviderModels: () => listAllProviderModelsMock(),
}))

function makeProvider(name: string, type: ProviderRow['type']): ProviderRow {
  return {
    id: 1,
    name,
    type,
    base_url: '',
    api_key_masked: '****',
    enabled: true,
    created_by: null,
    created_at: null,
    updated_at: null,
  }
}

function makeModel(providerName: string, name: string, modelId: string): ModelConfigRow {
  return {
    id: 1,
    provider_name: providerName,
    name,
    model_id: modelId,
    ref: `${providerName}/${name}`,
    context_size: null,
    extra_params: {},
    enabled: true,
    created_by: null,
    created_at: null,
    updated_at: null,
  }
}

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

const ElSelectStub = defineComponent({
  name: 'ElSelect',
  props: {
    modelValue: { type: [String, Number, Boolean, Array, Object] as unknown as () => unknown, default: undefined },
    filterable: { type: Boolean, default: false },
    clearable: { type: Boolean, default: false },
    allowCreate: { type: Boolean, default: false },
    disabled: { type: Boolean, default: false },
    loading: { type: Boolean, default: false },
    noDataText: String,
    placeholder: String,
  },
  emits: ['update:modelValue', 'change'],
  setup(props, { slots }) {
    return () =>
      h(
        'div',
        {
          class: 'el-select-stub',
          'data-value': String(props.modelValue ?? ''),
          'data-allow-create': String(props.allowCreate),
          'data-disabled': String(props.disabled),
          'data-no-data-text': props.noDataText ?? '',
        },
        slots.default?.(),
      )
  },
})

const ElOptionStub = defineComponent({
  name: 'ElOption',
  props: {
    value: { type: [String, Number, Boolean, Object, Array] as unknown as () => unknown, default: undefined },
    label: { type: [String, Number] as unknown as () => string | number, default: undefined },
  },
  setup(props, { slots }) {
    return () =>
      h(
        'div',
        { class: 'el-option-stub', 'data-value': String(props.value ?? '') },
        slots.default ? slots.default() : String(props.label ?? props.value ?? ''),
      )
  },
})

const ElOptionGroupStub = defineComponent({
  name: 'ElOptionGroup',
  props: { label: String },
  setup(props, { slots }) {
    return () =>
      h('div', { class: 'el-option-group-stub', 'data-label': props.label ?? '' }, slots.default?.())
  },
})

const ElInputStub = defineComponent({
  name: 'ElInput',
  props: { modelValue: String, type: String, rows: Number, placeholder: String },
  emits: ['update:modelValue', 'change'],
  setup(props) {
    return () =>
      h('input', {
        class: 'el-input-stub',
        'data-type': props.type ?? 'text',
        value: props.modelValue ?? '',
        placeholder: props.placeholder ?? '',
      })
  },
})

const ElInputNumberStub = defineComponent({
  name: 'ElInputNumber',
  props: { modelValue: Number, min: Number, max: Number, step: Number, precision: Number },
  emits: ['update:modelValue', 'change'],
  setup(props) {
    return () => h('input', { class: 'el-input-number-stub', value: String(props.modelValue ?? '') })
  },
})

const STUBS = {
  ElForm: ElFormStub,
  ElFormItem: ElFormItemStub,
  ElSelect: ElSelectStub,
  ElOption: ElOptionStub,
  ElOptionGroup: ElOptionGroupStub,
  ElInput: ElInputStub,
  ElInputNumber: ElInputNumberStub,
}

function mountForm(config: Record<string, unknown> = {}, readonly = false) {
  return mount(LlmNodeForm, {
    props: { config, readonly },
    global: { stubs: STUBS },
  })
}

/** 取模型下拉（带 data-testid），避免与 llm_type 下拉混淆 */
function modelSelect(wrapper: ReturnType<typeof mountForm>) {
  const selects = wrapper.findAllComponents(ElSelectStub)
  return selects.find((s) => s.attributes('data-testid') === 'model-select')
}

function llmTypeSelect(wrapper: ReturnType<typeof mountForm>) {
  const selects = wrapper.findAllComponents(ElSelectStub)
  return selects.find((s) => s.attributes('data-testid') === 'llm-type-select')
}

beforeEach(async () => {
  vi.clearAllMocks()
  listProvidersMock.mockResolvedValue([makeProvider('acme', 'OPENAI'), makeProvider('claude-co', 'ANTHROPIC')])
  listAllProviderModelsMock.mockResolvedValue([
    makeModel('acme', 'gpt-4o', 'gpt-4o-2024-08-06'),
    makeModel('acme', 'mini', 'gpt-4o-mini'),
    makeModel('claude-co', 'sonnet', 'claude-sonnet-4-5'),
  ])
  await useProviderModels().refresh()
})

describe('LlmNodeForm 模型下拉（provider 驱动）', () => {
  it('按 provider 分组渲染选项，选项 value 为 ref', () => {
    const wrapper = mountForm({ llm_type: 'openai', model_name: 'gpt-4o-mini' })

    const groupLabels = wrapper.findAllComponents(ElOptionGroupStub).map((g) => g.attributes('data-label'))
    expect(groupLabels).toEqual(['acme', 'claude-co'])

    const optionValues = wrapper.findAllComponents(ElOptionStub).map((o) => o.attributes('data-value'))
    expect(optionValues).toContain('acme/gpt-4o')
    expect(optionValues).toContain('claude-co/sonnet')
  })

  it('选中 provider 模型 → emit provider_ref + model_name(=model_id) + llm_type', async () => {
    const wrapper = mountForm({ llm_type: 'openai', model_name: 'gpt-4o-mini' })

    await modelSelect(wrapper)!.vm.$emit('update:modelValue', 'claude-co/sonnet')

    const emitted = wrapper.emitted('update:config')
    expect(emitted).toBeTruthy()
    const config = emitted![0]![0] as Record<string, unknown>
    expect(config.provider_ref).toBe('claude-co/sonnet')
    expect(config.model_name).toBe('claude-sonnet-4-5')
    expect(config.llm_type).toBe('anthropic')
  })

  it('手填裸模型名 → provider_ref 置空，model_name 为该串（env 回退路径）', async () => {
    const wrapper = mountForm({ llm_type: 'openai', model_name: 'gpt-4o-mini' })

    await modelSelect(wrapper)!.vm.$emit('update:modelValue', 'my-custom-model')

    const config = wrapper.emitted('update:config')![0]![0] as Record<string, unknown>
    expect(config.provider_ref).toBeNull()
    expect(config.model_name).toBe('my-custom-model')
    // llm_type 不被手填路径改写
    expect(config.llm_type).toBe('openai')
  })

  it('模型下拉允许手填（allow-create），保住纯 env 部署的可用性', () => {
    const wrapper = mountForm({})
    expect(modelSelect(wrapper)!.attributes('data-allow-create')).toBe('true')
  })

  it('清空选择 → provider_ref 与 model_name 均为空串', async () => {
    const wrapper = mountForm({ provider_ref: 'acme/gpt-4o', model_name: 'gpt-4o-2024-08-06' })

    await modelSelect(wrapper)!.vm.$emit('update:modelValue', '')

    const config = wrapper.emitted('update:config')![0]![0] as Record<string, unknown>
    expect(config.provider_ref).toBeNull()
    expect(config.model_name).toBe('')
  })
})

describe('LlmNodeForm 回填', () => {
  it('provider_ref 命中目录 → 下拉选中该 ref', () => {
    const wrapper = mountForm({ provider_ref: 'acme/gpt-4o', model_name: 'gpt-4o-2024-08-06' })
    expect(modelSelect(wrapper)!.attributes('data-value')).toBe('acme/gpt-4o')
  })

  it('无 provider_ref 但 model_name 命中某 model_id → 反查选中对应 ref', () => {
    const wrapper = mountForm({ llm_type: 'anthropic', model_name: 'claude-sonnet-4-5' })
    expect(modelSelect(wrapper)!.attributes('data-value')).toBe('claude-co/sonnet')
  })

  it('反查歧义时按 llm_type 消歧', () => {
    // mini 的 model_id 是 gpt-4o-mini，只属 acme；构造一个跨 provider 同 model_id 的场景
    const wrapper = mountForm({ llm_type: 'openai', model_name: 'gpt-4o-mini' })
    expect(modelSelect(wrapper)!.attributes('data-value')).toBe('acme/mini')
  })

  it('都不命中 → 显示原始 model_name（不丢用户数据）', () => {
    const wrapper = mountForm({ llm_type: 'openai', model_name: 'legacy-model-not-in-catalog' })
    expect(modelSelect(wrapper)!.attributes('data-value')).toBe('legacy-model-not-in-catalog')
  })

  it('目录为空 → 有清晰 no-data-text，不崩溃', async () => {
    listProvidersMock.mockResolvedValue([])
    listAllProviderModelsMock.mockResolvedValue([])
    await useProviderModels().refresh()

    const wrapper = mountForm({ model_name: 'gpt-4o-mini' })

    expect(modelSelect(wrapper)!.attributes('data-no-data-text')).toContain('暂无可用模型')
    expect(wrapper.find('.el-form-stub').exists()).toBe(true)
  })
})

describe('LlmNodeForm config 完整性与门禁', () => {
  it('编辑字段不丢弃 config 中其他既有键（max_retries / retry_base_delay）', async () => {
    const wrapper = mountForm({
      llm_type: 'openai',
      model_name: 'gpt-4o-mini',
      temperature: 0.7,
      system_prompt: 'hi',
      max_retries: 5,
      retry_base_delay: 2.5,
    })

    await modelSelect(wrapper)!.vm.$emit('update:modelValue', 'acme/gpt-4o')

    const config = wrapper.emitted('update:config')![0]![0] as Record<string, unknown>
    expect(config.max_retries).toBe(5)
    expect(config.retry_base_delay).toBe(2.5)
    expect(config.system_prompt).toBe('hi')
  })

  it('H6 守卫：不出现任何 api_key 相关字段', () => {
    const wrapper = mountForm({ llm_type: 'openai', model_name: 'gpt-4o-mini' })
    const html = wrapper.html()
    expect(html).not.toContain('api_key')
    expect(html).not.toContain('apiKey')
    expect(html).not.toContain('API Key')
  })

  it('有 provider_ref 时 llm_type 只读（由 provider 决定）', () => {
    const wrapper = mountForm({ provider_ref: 'claude-co/sonnet', model_name: 'claude-sonnet-4-5', llm_type: 'anthropic' })
    expect(llmTypeSelect(wrapper)!.attributes('data-disabled')).toBe('true')
  })

  it('无 provider_ref 时 llm_type 可编辑（env 回退路径）', () => {
    const wrapper = mountForm({ llm_type: 'openai', model_name: 'gpt-4o-mini' })
    expect(llmTypeSelect(wrapper)!.attributes('data-disabled')).not.toBe('true')
  })

  it('readonly=true → 整个表单禁用', () => {
    const wrapper = mountForm({ llm_type: 'openai', model_name: 'gpt-4o-mini' }, true)
    expect(wrapper.find('.el-form-stub').attributes('data-disabled')).toBe('true')
  })

  it('props.config 外部变化 → 表单同步（切换节点）', async () => {
    const wrapper = mountForm({ llm_type: 'openai', model_name: 'gpt-4o-mini' })
    expect(modelSelect(wrapper)!.attributes('data-value')).toBe('acme/mini')

    await wrapper.setProps({ config: { provider_ref: 'claude-co/sonnet', model_name: 'claude-sonnet-4-5' } })
    await flushPromises()

    expect(modelSelect(wrapper)!.attributes('data-value')).toBe('claude-co/sonnet')
  })
})
