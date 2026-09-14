// @vitest-environment happy-dom
/**
 * SubWorkflowNodeForm 组件测试（S23 / S24，前端 spec §5.1）。
 *
 * 零真实网络：`vi.mock('@/api/workflow')`，Element Plus 一律 stub。
 *
 * 验证：
 *   - 被引用工作流下拉的选项来自 `listWorkflows()`，且**排除当前正在编辑的 id**
 *     （自引用是最常见误操作；这是体验层，后端 S23 运行栈环检测才是权威）；
 *   - 提交体键恰为 `{workflow_id, input_map, inherit_input}`，不夹带其它键；
 *   - `input_map` 以 JSON 文本编辑（复用 http 节点 dict 字段的既有模式）：
 *     合法对象才 emit，非法 JSON 不 emit（不静默写坏 config）；
 *   - **不在前端校验被引用工作流是否存在**——存在性是运行期检查（S18）；
 *   - 拉取失败不崩溃；readonly 门禁；props.config 外部变化同步。
 */
import { beforeEach, describe, expect, it, vi } from 'vitest'
import { defineComponent, h } from 'vue'
import { flushPromises, mount } from '@vue/test-utils'

import SubWorkflowNodeForm from '@/views/workflow/panel/SubWorkflowNodeForm.vue'
import type { WorkflowSummary } from '@/api/workflow'

const listWorkflowsMock = vi.fn()
vi.mock('@/api/workflow', async (importOriginal) => {
  const actual = await importOriginal<typeof import('@/api/workflow')>()
  return { ...actual, listWorkflows: () => listWorkflowsMock() }
})

function makeSummary(id: string): WorkflowSummary {
  return { workflow_id: id, node_count: 2, entry_point: 'a', description: `${id} desc` }
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
    modelValue: { type: String, default: '' },
    filterable: { type: Boolean, default: false },
    clearable: { type: Boolean, default: false },
    placeholder: String,
    noDataText: String,
    loading: { type: Boolean, default: false },
  },
  emits: ['update:modelValue', 'change'],
  setup(props, { slots }) {
    return () =>
      h(
        'div',
        {
          class: 'el-select-stub',
          'data-value': props.modelValue ?? '',
          'data-no-data-text': props.noDataText ?? '',
          'data-loading': String(props.loading),
        },
        slots.default?.(),
      )
  },
})

const ElOptionStub = defineComponent({
  name: 'ElOption',
  props: { value: String, label: String },
  setup(props, { slots }) {
    return () =>
      h(
        'div',
        { class: 'el-option-stub', 'data-value': props.value ?? '' },
        slots.default ? slots.default() : String(props.label ?? props.value ?? ''),
      )
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

const ElInputStub = defineComponent({
  name: 'ElInput',
  props: { modelValue: String, type: String, rows: Number, placeholder: String },
  emits: ['update:modelValue', 'change'],
  setup(props) {
    return () =>
      h('textarea', {
        class: 'el-input-stub',
        'data-type': props.type ?? 'text',
        value: props.modelValue ?? '',
      })
  },
})

const STUBS = {
  ElForm: ElFormStub,
  ElFormItem: ElFormItemStub,
  ElSelect: ElSelectStub,
  ElOption: ElOptionStub,
  ElSwitch: ElSwitchStub,
  ElInput: ElInputStub,
}

function mountForm(
  config: Record<string, unknown> = {},
  extra: { readonly?: boolean; excludeWorkflowId?: string } = {},
) {
  return mount(SubWorkflowNodeForm, {
    props: { config, readonly: extra.readonly ?? false, excludeWorkflowId: extra.excludeWorkflowId ?? '' },
    global: { stubs: STUBS },
  })
}

function optionValues(wrapper: ReturnType<typeof mountForm>): string[] {
  return wrapper.findAllComponents(ElOptionStub).map((o) => o.attributes('data-value') ?? '')
}

function lastConfig(wrapper: ReturnType<typeof mountForm>): Record<string, unknown> {
  const emitted = wrapper.emitted('update:config')
  expect(emitted).toBeTruthy()
  return emitted![emitted!.length - 1]![0] as Record<string, unknown>
}

beforeEach(() => {
  vi.clearAllMocks()
  listWorkflowsMock.mockResolvedValue([makeSummary('wf_inner'), makeSummary('wf_other'), makeSummary('wf_outer')])
})

describe('SubWorkflowNodeForm 被引用工作流下拉', () => {
  it('选项来自 listWorkflows()', async () => {
    const wrapper = mountForm({ workflow_id: '' })
    await flushPromises()
    expect(listWorkflowsMock).toHaveBeenCalledTimes(1)
    expect(optionValues(wrapper)).toEqual(['wf_inner', 'wf_other', 'wf_outer'])
  })

  it('排除当前正在编辑的 id（防自引用，体验层）', async () => {
    const wrapper = mountForm({ workflow_id: '' }, { excludeWorkflowId: 'wf_outer' })
    await flushPromises()
    expect(optionValues(wrapper)).toEqual(['wf_inner', 'wf_other'])
    expect(optionValues(wrapper)).not.toContain('wf_outer')
  })

  it('回填已保存的 workflow_id', async () => {
    const wrapper = mountForm({ workflow_id: 'wf_inner' })
    await flushPromises()
    expect(wrapper.findComponent(ElSelectStub).attributes('data-value')).toBe('wf_inner')
  })

  it('选中 → emit update:config 带 workflow_id', async () => {
    const wrapper = mountForm({ workflow_id: '' })
    await flushPromises()
    await wrapper.findComponent(ElSelectStub).vm.$emit('update:modelValue', 'wf_inner')
    expect(lastConfig(wrapper).workflow_id).toBe('wf_inner')
  })

  it('拉取失败不崩溃，给出清晰的 no-data 文案', async () => {
    listWorkflowsMock.mockRejectedValue(new Error('network down'))
    const wrapper = mountForm({ workflow_id: 'wf_inner' })
    await flushPromises()
    expect(wrapper.find('.el-form-stub').exists()).toBe(true)
    expect(optionValues(wrapper)).toEqual([])
    expect(wrapper.findComponent(ElSelectStub).attributes('data-no-data-text')).toContain('暂无')
    // 已保存的值不因目录拉取失败而丢失
    expect(wrapper.findComponent(ElSelectStub).attributes('data-value')).toBe('wf_inner')
  })

  it('不做前端存在性校验：未出现在目录里的已存值照样保留（S18 存在性是运行期检查）', async () => {
    listWorkflowsMock.mockResolvedValue([makeSummary('wf_inner')])
    const wrapper = mountForm({ workflow_id: 'wf_saved_but_unlisted' })
    await flushPromises()
    expect(wrapper.findComponent(ElSelectStub).attributes('data-value')).toBe('wf_saved_but_unlisted')
  })
})

describe('SubWorkflowNodeForm 输入映射与继承', () => {
  it('inherit_input switch → emit update:config', async () => {
    const wrapper = mountForm({ workflow_id: 'wf_inner' })
    await flushPromises()
    await wrapper.findComponent(ElSwitchStub).vm.$emit('update:modelValue', true)
    expect(lastConfig(wrapper).inherit_input).toBe(true)
  })

  it('input_map 合法 JSON 对象 → emit 解析后的对象', async () => {
    const wrapper = mountForm({ workflow_id: 'wf_inner', input_map: {} })
    await flushPromises()
    const textarea = wrapper.findComponent(ElInputStub)
    await textarea.vm.$emit('update:modelValue', '{"query": "input"}')
    await textarea.vm.$emit('change', '{"query": "input"}')
    expect(lastConfig(wrapper).input_map).toEqual({ query: 'input' })
  })

  it('input_map 非法 JSON → 不 emit（不静默写坏 config）', async () => {
    const wrapper = mountForm({ workflow_id: 'wf_inner', input_map: { query: 'input' } })
    await flushPromises()
    const textarea = wrapper.findComponent(ElInputStub)
    await textarea.vm.$emit('update:modelValue', '{ not json')
    await textarea.vm.$emit('change', '{ not json')
    expect(wrapper.emitted('update:config')).toBeUndefined()
  })

  it('input_map 是非对象的合法 JSON（数组）→ 不 emit', async () => {
    const wrapper = mountForm({ workflow_id: 'wf_inner', input_map: {} })
    await flushPromises()
    const textarea = wrapper.findComponent(ElInputStub)
    await textarea.vm.$emit('update:modelValue', '[1, 2]')
    await textarea.vm.$emit('change', '[1, 2]')
    expect(wrapper.emitted('update:config')).toBeUndefined()
  })

  it('回填既有 input_map 为格式化 JSON 文本', async () => {
    const wrapper = mountForm({ workflow_id: 'wf_inner', input_map: { query: 'input' } })
    await flushPromises()
    expect((wrapper.findComponent(ElInputStub).element as HTMLTextAreaElement).value).toContain('"query"')
  })
})

describe('SubWorkflowNodeForm 提交体与门禁', () => {
  it('提交体键恰为 workflow_id / input_map / inherit_input', async () => {
    const wrapper = mountForm({ workflow_id: '' })
    await flushPromises()
    await wrapper.findComponent(ElSelectStub).vm.$emit('update:modelValue', 'wf_inner')
    expect(Object.keys(lastConfig(wrapper)).sort()).toEqual(['inherit_input', 'input_map', 'workflow_id'])
  })

  it('回填含未知键的旧 config → 提交体剔除（不夹带后端会拒的 extra，S14 forbid）', async () => {
    const wrapper = mountForm({ workflow_id: 'wf_inner', bogus_legacy_key: 1 })
    await flushPromises()
    await wrapper.findComponent(ElSwitchStub).vm.$emit('update:modelValue', true)
    expect(lastConfig(wrapper)).not.toHaveProperty('bogus_legacy_key')
  })

  it('readonly=true → 整个表单禁用', async () => {
    const wrapper = mountForm({ workflow_id: 'wf_inner' }, { readonly: true })
    await flushPromises()
    expect(wrapper.find('.el-form-stub').attributes('data-disabled')).toBe('true')
  })

  it('props.config 外部变化 → 表单同步（切换节点）', async () => {
    const wrapper = mountForm({ workflow_id: 'wf_inner' })
    await flushPromises()
    await wrapper.setProps({ config: { workflow_id: 'wf_other', inherit_input: true } })
    expect(wrapper.findComponent(ElSelectStub).attributes('data-value')).toBe('wf_other')
    expect(wrapper.findComponent(ElSwitchStub).attributes('data-value')).toBe('true')
  })
})
