// @vitest-environment happy-dom
/**
 * ProviderModelDialog 最小测试（task-023）：
 * - mock `@/api/provider` 提供 listProviderModels / CRUD 函数；
 * - 验证打开 → 拉模型清单 → 渲染；
 * - 验证新增 / 编辑 / 删除走真实 API mock 并刷新列表；
 * - 零真实网络。
 */
import { beforeEach, describe, expect, it, vi } from 'vitest'
import { defineComponent, h, inject, provide } from 'vue'
import { flushPromises, mount } from '@vue/test-utils'
import type { VueWrapper } from '@vue/test-utils'

import ProviderModelDialog from '@/views/provider/ProviderModelDialog.vue'
import type { ModelConfigRow } from '@/api/provider'

const elMessageMock = vi.hoisted(() => {
  const fn = vi.fn()
  return Object.assign(fn, {
    error: vi.fn(),
    success: vi.fn(),
    warning: vi.fn(),
  })
})
const elMessageBoxMock = vi.hoisted(() => ({ confirm: vi.fn() }))

vi.mock('element-plus', () => ({
  ElMessage: elMessageMock,
  ElMessageBox: elMessageBoxMock,
}))

const confirmMock = elMessageBoxMock.confirm
const elMessageFn = elMessageMock

const MODELS: ModelConfigRow[] = [
  {
    id: 1,
    provider_name: 'openai-prod',
    name: 'gpt-4o',
    model_id: 'gpt-4o-2024-08-06',
    ref: 'openai-prod/gpt-4o',
    context_size: 128000,
    extra_params: {},
    enabled: true,
    created_by: 'seed',
    created_at: '2026-06-11 09:20',
    updated_at: null,
  },
  {
    id: 2,
    provider_name: 'openai-prod',
    name: 'gpt-4o-mini',
    model_id: 'gpt-4o-mini-2024-07-18',
    ref: 'openai-prod/gpt-4o-mini',
    context_size: 128000,
    extra_params: {},
    enabled: false,
    created_by: 'seed',
    created_at: '2026-06-12 10:00',
    updated_at: null,
  },
]

const apiMock = vi.hoisted(() => ({
  listProviders: vi.fn(),
  listProvidersPage: vi.fn(),
  getProvider: vi.fn(),
  createProvider: vi.fn(),
  updateProvider: vi.fn(),
  deleteProvider: vi.fn(),
  testProviderConnection: vi.fn(),
  listProviderModels: vi.fn(),
  createProviderModel: vi.fn(),
  updateProviderModel: vi.fn(),
  deleteProviderModel: vi.fn(),
}))

vi.mock('@/api/provider', () => apiMock)

const ROWS_KEY = Symbol('table-rows')

const ElTableStub = defineComponent({
  name: 'ElTable',
  props: { data: { type: Array, default: () => [] as unknown[] } },
  setup(props, { slots }) {
    provide(ROWS_KEY, props)
    return () =>
      h('div', { class: 'el-table-stub' }, [
        slots.default ? slots.default() : undefined,
      ])
  },
})

const ElTableColumnStub = defineComponent({
  name: 'ElTableColumn',
  props: { prop: String },
  setup(props, { slots }) {
    const tableProps = inject<{ data: unknown[] }>(ROWS_KEY)
    return () =>
      h(
        'div',
        { class: 'el-table-column-stub' },
        (tableProps?.data ?? []).map((row, index) =>
          slots.default
            ? slots.default({ row, $index: index })
            : String((row as Record<string, unknown>)[props.prop ?? ''] ?? ''),
        ),
      )
  },
})

const ElButtonStub = defineComponent({
  name: 'ElButton',
  props: { loading: Boolean, disabled: Boolean },
  emits: ['click'],
  setup(props, { emit, slots, attrs }) {
    return () =>
      h(
        'button',
        {
          class: attrs.class,
          'data-loading': props.loading ? 'true' : 'false',
          'data-disabled': props.disabled ? 'true' : 'false',
          onClick: () => emit('click'),
        },
        slots.default ? slots.default() : undefined,
      )
  },
})

const ElTagStub = defineComponent({
  name: 'ElTag',
  props: { type: String, size: String },
  setup(props, { slots }) {
    return () =>
      h('span', { class: 'el-tag-stub', 'data-type': props.type }, slots.default?.())
  },
})

const ElInputStub = defineComponent({
  name: 'ElInput',
  props: {
    modelValue: { type: [String, Number], default: '' },
    placeholder: String,
    showPassword: Boolean,
    disabled: { type: Boolean, default: false },
  },
  emits: ['update:modelValue'],
  setup(props, { emit }) {
    return () =>
      h('input', {
        class: 'el-input-stub',
        placeholder: props.placeholder,
        disabled: props.disabled,
        value: props.modelValue ?? '',
        onInput: (event: Event) =>
          emit('update:modelValue', (event.target as HTMLInputElement).value),
      })
  },
})

const ElInputNumberStub = defineComponent({
  name: 'ElInputNumber',
  props: { modelValue: { type: [Number, String, null], default: null }, min: Number },
  emits: ['update:modelValue'],
  setup(props, { emit }) {
    return () =>
      h('input', {
        class: 'el-input-number-stub',
        type: 'number',
        value: props.modelValue ?? '',
        onInput: (event: Event) => {
          const raw = (event.target as HTMLInputElement).value
          emit('update:modelValue', raw === '' ? null : Number(raw))
        },
      })
  },
})

const ElSwitchStub = defineComponent({
  name: 'ElSwitch',
  props: { modelValue: { type: Boolean, default: false } },
  emits: ['update:modelValue'],
  setup(props, { emit }) {
    return () =>
      h('input', {
        class: 'el-switch-stub',
        type: 'checkbox',
        checked: props.modelValue,
        onChange: (event: Event) =>
          emit('update:modelValue', (event.target as HTMLInputElement).checked),
      })
  },
})

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
    return () => h('form', { class: 'el-form-stub' }, slots.default?.())
  },
})

const ElFormItemStub = defineComponent({
  name: 'ElFormItem',
  props: { label: String, prop: String },
  setup(_, { slots }) {
    return () => h('div', { class: 'el-form-item-stub' }, slots.default?.())
  },
})

function mountDialog(
  providerName: string,
  modelValue: boolean,
): VueWrapper {
  return mount(ProviderModelDialog, {
    props: { providerName, modelValue },
    global: {
      stubs: {
        ElTable: ElTableStub,
        ElTableColumn: ElTableColumnStub,
        ElButton: ElButtonStub,
        ElTag: ElTagStub,
        ElInput: ElInputStub,
        ElInputNumber: ElInputNumberStub,
        ElSwitch: ElSwitchStub,
        ElDialog: ElDialogStub,
        ElForm: ElFormStub,
        ElFormItem: ElFormItemStub,
      },
      directives: { loading: () => undefined },
    },
  })
}

function findButton(wrapper: VueWrapper, text: string) {
  const button = wrapper.findAll('button').find((item) => item.text().includes(text))
  if (!button) {
    throw new Error(`button "${text}" not found`)
  }
  return button
}

function findRowButton(
  wrapper: VueWrapper,
  text: string,
  rowIdx: number,
): ReturnType<typeof wrapper.findAll>[number] {
  const column = wrapper
    .findAll('.el-table-column-stub')
    .find((col) => col.findAll('button').some((b) => b.text().includes(text)))
  if (!column) {
    throw new Error(`column with button "${text}" not found`)
  }
  const candidates = column.findAll('button').filter((b) => b.text().includes(text))
  const target = candidates[rowIdx]
  if (!target) {
    throw new Error(`row ${rowIdx} button "${text}" not found`)
  }
  return target
}

beforeEach(() => {
  vi.clearAllMocks()
  elMessageFn.mockReset()
  elMessageMock.success.mockReset()
  elMessageMock.error.mockReset()
  elMessageMock.warning.mockReset()
  confirmMock.mockReset()
  confirmMock.mockResolvedValue(undefined)
  validateMock = vi.fn().mockResolvedValue(true)
  apiMock.listProviderModels.mockResolvedValue([...MODELS])
  apiMock.createProviderModel.mockResolvedValue({
    id: 99,
    provider_name: 'openai-prod',
    name: 'new-model',
    model_id: 'new-model-id',
    ref: 'openai-prod/new-model',
    context_size: null,
    extra_params: {},
    enabled: true,
    created_by: 'user',
    created_at: '2026-08-18 10:00',
    updated_at: null,
  } satisfies ModelConfigRow)
  apiMock.updateProviderModel.mockImplementation(
    async (_name: string, model: string, payload: Partial<ModelConfigRow>) => ({
      ...MODELS[0],
      name: model,
      model_id: payload.model_id ?? MODELS[0].model_id,
      enabled: payload.enabled ?? MODELS[0].enabled,
    }),
  )
  apiMock.deleteProviderModel.mockResolvedValue(null)
})

describe('ProviderModelDialog 模型管理弹窗', () => {
  it('打开弹窗：调 listProviderModels 并渲染模型表格行', async () => {
    const wrapper = mountDialog('openai-prod', true)
    await flushPromises()

    expect(apiMock.listProviderModels).toHaveBeenCalledWith('openai-prod')
    const tableData = wrapper.findComponent(ElTableStub).props('data') as ModelConfigRow[]
    expect(tableData).toHaveLength(2)
    expect(wrapper.text()).toContain('gpt-4o')
    expect(wrapper.text()).toContain('gpt-4o-mini')
    expect(wrapper.text()).toContain('gpt-4o-2024-08-06')
  })

  it('弹窗关闭时不会自动拉取（watch 守卫）', async () => {
    mountDialog('openai-prod', false)
    await flushPromises()
    expect(apiMock.listProviderModels).not.toHaveBeenCalled()
  })

  it('新增模型：调 createProviderModel 并刷新列表', async () => {
    // 第一次 listProviderModels 返回初始 2 行；创建成功后 refresh 再拉一次
    apiMock.listProviderModels
      .mockResolvedValueOnce([...MODELS])
      .mockResolvedValueOnce([
        ...MODELS,
        {
          id: 99,
          provider_name: 'openai-prod',
          name: 'new-model',
          model_id: 'new-model-id',
          ref: 'openai-prod/new-model',
          context_size: null,
          extra_params: {},
          enabled: true,
          created_by: 'user',
          created_at: '2026-08-18 10:00',
          updated_at: null,
        },
      ])

    const wrapper = mountDialog('openai-prod', true)
    await flushPromises()

    await findButton(wrapper, '新增模型').trigger('click')
    await flushPromises()

    // 填入 form 字段（WebAgentFormDialog 内部 formModel）
    await wrapper
      .find('input[placeholder="请输入模型名称"]')
      .setValue('new-model')
    await wrapper
      .find('input[placeholder="请输入模型 ID"]')
      .setValue('new-model-id')

    // 触发内部 WebAgentFormDialog 的"确定"
    await findButton(wrapper, '确定').trigger('click')
    await flushPromises()

    expect(apiMock.createProviderModel).toHaveBeenCalledWith(
      'openai-prod',
      expect.objectContaining({ name: 'new-model', model_id: 'new-model-id' }),
    )
    // refresh 后表格行数扩展为 3
    expect(apiMock.listProviderModels).toHaveBeenCalledTimes(2)
    const tableData = wrapper.findComponent(ElTableStub).props('data') as ModelConfigRow[]
    expect(tableData).toHaveLength(3)
  })

  it('删除模型：useConfirm → deleteProviderModel 并刷新列表', async () => {
    const wrapper = mountDialog('openai-prod', true)
    await flushPromises()

    await findRowButton(wrapper, '删除', 0).trigger('click')
    await flushPromises()

    expect(confirmMock).toHaveBeenCalledWith(
      '确定删除模型「gpt-4o」吗？',
      '删除确认',
      expect.anything(),
    )
    expect(apiMock.deleteProviderModel).toHaveBeenCalledWith('openai-prod', 'gpt-4o')
    // 删除成功后 refresh：listProviderModels 第二次调用
    expect(apiMock.listProviderModels).toHaveBeenCalledTimes(2)
  })

  it('删除取消：不调 deleteProviderModel', async () => {
    confirmMock.mockRejectedValue('cancel')
    const wrapper = mountDialog('openai-prod', true)
    await flushPromises()

    await findRowButton(wrapper, '删除', 0).trigger('click')
    await flushPromises()

    expect(apiMock.deleteProviderModel).not.toHaveBeenCalled()
    expect(apiMock.listProviderModels).toHaveBeenCalledTimes(1)
  })

  it('编辑模型：调 updateProviderModel 并刷新列表', async () => {
    apiMock.listProviderModels
      .mockResolvedValueOnce([...MODELS])
      .mockResolvedValueOnce([...MODELS])
    const wrapper = mountDialog('openai-prod', true)
    await flushPromises()

    await findRowButton(wrapper, '编辑', 0).trigger('click')
    await flushPromises()
    // 弹窗打开后修改 model_id（编辑占位字段）
    const modelIdInput = wrapper.find('input[placeholder="请输入模型 ID"]')
    expect(modelIdInput.exists()).toBe(true)
    await modelIdInput.setValue('gpt-4o-2024-12-01')
    await findButton(wrapper, '确定').trigger('click')
    await flushPromises()

    expect(apiMock.updateProviderModel).toHaveBeenCalledWith(
      'openai-prod',
      'gpt-4o',
      expect.objectContaining({ model_id: 'gpt-4o-2024-12-01' }),
    )
    expect(elMessageFn).toHaveBeenCalledWith(
      expect.objectContaining({ type: 'success', message: '已保存：gpt-4o' }),
    )
    expect(apiMock.listProviderModels).toHaveBeenCalledTimes(2)
  })
})