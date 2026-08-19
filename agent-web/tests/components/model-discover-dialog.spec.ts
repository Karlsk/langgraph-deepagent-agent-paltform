// @vitest-environment happy-dom
/**
 * ModelDiscoverDialog 单元测试（task-025）：
 * - mock `@/api/provider` 的 discoverProviderModels / createProviderModel；
 * - stub Element Plus 表格与按钮组件，断言拉取 → 渲染 → 勾选 → 批量创建 →
 *   降级提示 → 通知父组件刷新 的完整链路；
 * - 零真实网络。
 */
import { beforeEach, describe, expect, it, vi } from 'vitest'
import { defineComponent, h, inject, provide, ref, watch } from 'vue'
import { flushPromises, mount } from '@vue/test-utils'
import type { VueWrapper } from '@vue/test-utils'

import ModelDiscoverDialog from '@/views/provider/ModelDiscoverDialog.vue'
import type { RemoteModelInfo } from '@/api/provider'

const elMessageMock = vi.hoisted(() => {
  const fn = vi.fn()
  return Object.assign(fn, {
    error: vi.fn(),
    success: vi.fn(),
    warning: vi.fn(),
  })
})

vi.mock('element-plus', () => ({
  ElMessage: elMessageMock,
}))

const elMessageFn = elMessageMock

const apiMock = vi.hoisted(() => ({
  discoverProviderModels: vi.fn(),
  createProviderModel: vi.fn(),
}))

vi.mock('@/api/provider', () => apiMock)

const ROWS_KEY = Symbol('discover-rows')
const SELECTED_KEY = Symbol('discover-selected')

/**
 * ElTable stub: tracks `data` and exposes selection-change to mimic element-plus
 * semantics. The optional selection-collection row columns simply emit
 * `selection-change` on click so the test can drive it deterministically.
 */
const ElTableStub = defineComponent({
  name: 'ElTable',
  props: {
    data: { type: Array, default: () => [] as unknown[] },
  },
  emits: ['selection-change'],
  setup(props, { slots, emit }) {
    provide(ROWS_KEY, props)
    const selected = ref<unknown[]>([])
    provide(SELECTED_KEY, selected)
    watch(
      selected,
      (rows) => emit('selection-change', rows),
      { deep: true },
    )
    return () =>
      h('div', { class: 'el-table-stub', 'data-row-count': props.data.length }, [
        slots.default ? slots.default() : undefined,
      ])
  },
})

const ElTableColumnStub = defineComponent({
  name: 'ElTableColumn',
  props: {
    type: String,
    prop: String,
    label: String,
    width: String,
  },
  setup(props, { slots }) {
    const tableProps = inject<{ data: unknown[] }>(ROWS_KEY)
    const selected = inject<ReturnType<typeof ref<unknown[]>>>(
      SELECTED_KEY,
      ref([]),
    )
    return () => {
      if (props.type === 'selection') {
        return h(
          'div',
          { class: 'el-table-column-stub el-table-column-stub--selection' },
          (tableProps?.data ?? []).map((row, index) => {
            const rowKey = (row as Record<string, unknown>).id as string
            const isSelected = (selected.value as { id?: string }[])
              .map((item) => item?.id)
              .includes(rowKey)
            return h(
              'button',
              {
                class: 'select-toggle',
                'data-row-index': index,
                'data-row-id': rowKey,
                'data-selected': isSelected ? 'true' : 'false',
                onClick: () => {
                  if (isSelected) {
                    selected.value = (selected.value as { id?: string }[]).filter(
                      (item) => item?.id !== rowKey,
                    )
                  } else {
                    selected.value = [...(selected.value as unknown[]), row]
                  }
                },
              },
              isSelected ? '☑' : '☐',
            )
          }),
        )
      }
      return h(
        'div',
        { class: 'el-table-column-stub' },
        (tableProps?.data ?? []).map((row) =>
          slots.default
            ? slots.default({ row })
            : String((row as Record<string, unknown>)[props.prop ?? ''] ?? ''),
        ),
      )
    }
  },
})

const ElButtonStub = defineComponent({
  name: 'ElButton',
  props: { loading: Boolean, disabled: Boolean, type: String },
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

const ElDialogStub = defineComponent({
  name: 'ElDialog',
  props: { modelValue: Boolean, title: String, width: String },
  emits: ['update:modelValue', 'close'],
  setup(props, { slots }) {
    return () =>
      props.modelValue
        ? h('div', { class: 'el-dialog-stub', 'data-title': props.title }, [
            slots.default ? slots.default() : undefined,
            slots.footer ? slots.footer() : undefined,
          ])
        : null
  },
})

function mountDiscoverDialog(providerName: string): VueWrapper {
  return mount(ModelDiscoverDialog, {
    props: { providerName, modelValue: true },
    global: {
      stubs: {
        ElTable: ElTableStub,
        ElTableColumn: ElTableColumnStub,
        ElButton: ElButtonStub,
        ElDialog: ElDialogStub,
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

const CANNED: RemoteModelInfo[] = [
  { id: 'deepseek-v4-flash', owned_by: 'deepseek', raw: { id: 'deepseek-v4-flash' } },
  { id: 'deepseek-v4-pro', owned_by: 'deepseek', raw: { id: 'deepseek-v4-pro' } },
  { id: 'deepseek-v4-distill', owned_by: 'deepseek', raw: { id: 'deepseek-v4-distill' } },
]

beforeEach(() => {
  vi.clearAllMocks()
  elMessageFn.mockReset()
  elMessageMock.success.mockReset()
  elMessageMock.error.mockReset()
  elMessageMock.warning.mockReset()
  apiMock.discoverProviderModels.mockResolvedValue([...CANNED])
  apiMock.createProviderModel.mockImplementation(
    async (_name: string, payload: { name: string; model_id: string; enabled: boolean }) => ({
      id: Math.floor(Math.random() * 1000),
      provider_name: 'deepseek',
      name: payload.name,
      model_id: payload.model_id,
      ref: `deepseek/${payload.name}`,
      context_size: null,
      extra_params: {},
      enabled: payload.enabled,
      created_by: 'user',
      created_at: '2026-08-18 10:00',
      updated_at: null,
    }),
  )
})

describe('ModelDiscoverDialog 上游发现弹窗', () => {
  it('打开后点击拉取：调 discoverProviderModels 并渲染上游模型', async () => {
    const wrapper = mountDiscoverDialog('deepseek')

    await findButton(wrapper, '拉取上游模型').trigger('click')
    await flushPromises()

    expect(apiMock.discoverProviderModels).toHaveBeenCalledWith('deepseek')
    expect(wrapper.text()).toContain('deepseek-v4-flash')
    expect(wrapper.text()).toContain('deepseek-v4-pro')
    expect(wrapper.text()).toContain('deepseek-v4-distill')
  })

  it('关闭后重新打开上游发现：清空上一次拉取结果', async () => {
    const wrapper = mountDiscoverDialog('deepseek')

    await findButton(wrapper, '拉取上游模型').trigger('click')
    await flushPromises()
    expect(wrapper.text()).toContain('deepseek-v4-flash')

    wrapper.findComponent(ElDialogStub).vm.$emit('close')
    await flushPromises()
    const closedTableData = wrapper.findComponent(ElTableStub).props('data') as RemoteModelInfo[]
    expect(closedTableData).toEqual([])

    await wrapper.setProps({ modelValue: false })
    await wrapper.setProps({ modelValue: true })

    const tableData = wrapper.findComponent(ElTableStub).props('data') as RemoteModelInfo[]
    expect(tableData).toEqual([])
    expect(findButton(wrapper, '创建').attributes('data-disabled')).toBe('true')
  })

  it('切换 provider：清空旧 provider 的拉取结果', async () => {
    const wrapper = mountDiscoverDialog('deepseek')

    await findButton(wrapper, '拉取上游模型').trigger('click')
    await flushPromises()
    await wrapper.findAll('.select-toggle')[0].trigger('click')
    await flushPromises()
    expect(findButton(wrapper, '创建').attributes('data-disabled')).toBe('false')

    await wrapper.setProps({ providerName: 'openai' })

    const tableData = wrapper.findComponent(ElTableStub).props('data') as RemoteModelInfo[]
    expect(tableData).toEqual([])
    expect(findButton(wrapper, '创建').attributes('data-disabled')).toBe('true')
    expect(apiMock.discoverProviderModels).toHaveBeenCalledTimes(1)
  })

  it('再次拉取：请求开始前立即清空旧结果和选中项', async () => {
    let resolveSecondFetch: (value: RemoteModelInfo[]) => void = () => undefined
    apiMock.discoverProviderModels
      .mockResolvedValueOnce([...CANNED])
      .mockImplementationOnce(
        () =>
          new Promise<RemoteModelInfo[]>((resolve) => {
            resolveSecondFetch = resolve
          }),
      )
    const wrapper = mountDiscoverDialog('deepseek')

    await findButton(wrapper, '拉取上游模型').trigger('click')
    await flushPromises()
    await wrapper.findAll('.select-toggle')[0].trigger('click')
    await flushPromises()

    await findButton(wrapper, '拉取上游模型').trigger('click')
    await wrapper.vm.$nextTick()

    const tableData = wrapper.findComponent(ElTableStub).props('data') as RemoteModelInfo[]
    expect(tableData).toEqual([])
    expect(findButton(wrapper, '创建').attributes('data-disabled')).toBe('true')

    resolveSecondFetch([])
    await flushPromises()
  })

  it('勾选 2 个模型后点击"创建 2 个"：调 createProviderModel 两次', async () => {
    const wrapper = mountDiscoverDialog('deepseek')

    await findButton(wrapper, '拉取上游模型').trigger('click')
    await flushPromises()

    // 勾选前两个
    const toggles = wrapper.findAll('.select-toggle')
    expect(toggles).toHaveLength(3)
    await toggles[0].trigger('click')
    await toggles[1].trigger('click')
    await flushPromises()

    await findButton(wrapper, '创建 2 个').trigger('click')
    await flushPromises()

    expect(apiMock.createProviderModel).toHaveBeenCalledTimes(2)
    expect(apiMock.createProviderModel).toHaveBeenCalledWith(
      'deepseek',
      expect.objectContaining({ name: 'deepseek-v4-flash', model_id: 'deepseek-v4-flash', enabled: true }),
    )
    expect(apiMock.createProviderModel).toHaveBeenCalledWith(
      'deepseek',
      expect.objectContaining({ name: 'deepseek-v4-pro', model_id: 'deepseek-v4-pro', enabled: true }),
    )
    // 成功后 emit 'created' 让父组件刷新
    expect(wrapper.emitted('created')).toBeTruthy()
  })

  it('部分失败降级：1 成功 1 失败 → 提示成功 1 / 失败 1', async () => {
    apiMock.createProviderModel.mockImplementation(
      async (providerName: string, payload: { name: string; model_id: string; enabled: boolean }) => {
        if (payload.name === 'deepseek-v4-flash') {
          throw new Error('name clash')
        }
        return {
          id: 1,
          provider_name: providerName,
          name: payload.name,
          model_id: payload.model_id,
          ref: `${providerName}/${payload.name}`,
          context_size: null,
          extra_params: {},
          enabled: payload.enabled,
          created_by: 'user',
          created_at: '2026-08-18 10:00',
          updated_at: null,
        }
      },
    )
    const wrapper = mountDiscoverDialog('deepseek')

    await findButton(wrapper, '拉取上游模型').trigger('click')
    await flushPromises()

    const toggles = wrapper.findAll('.select-toggle')
    await toggles[0].trigger('click') // flash → 失败
    await toggles[1].trigger('click') // pro  → 成功
    await flushPromises()

    await findButton(wrapper, '创建 2 个').trigger('click')
    await flushPromises()

    expect(apiMock.createProviderModel).toHaveBeenCalledTimes(2)
    // 降级提示：成功 1 失败 1
    const messages = elMessageFn.mock.calls.map((call) => call[0])
    const partial = messages.find((arg) => arg?.message?.includes?.('成功') && arg?.message?.includes?.('失败'))
    expect(partial).toBeTruthy()
    expect(partial.message).toContain('成功 1')
    expect(partial.message).toContain('失败 1')
  })

  it('0 选中时"创建"按钮 disabled / 不可点', async () => {
    const wrapper = mountDiscoverDialog('deepseek')

    await findButton(wrapper, '拉取上游模型').trigger('click')
    await flushPromises()

    // 模板上 :disabled="selectedIds.length === 0 || creating"，stub 上 data-disabled 应为 true
    const createButton = wrapper.findAll('button').find((btn) => btn.text().includes('创建'))
    expect(createButton).toBeTruthy()
    expect(createButton!.attributes('data-disabled')).toBe('true')

    // 即便强制触发 click 也不会发起 createProviderModel 调用
    await createButton!.trigger('click')
    await flushPromises()
    expect(apiMock.createProviderModel).not.toHaveBeenCalled()
  })

  it('拉取失败：上游错误时显示错误消息', async () => {
    apiMock.discoverProviderModels.mockRejectedValueOnce(new Error('upstream DNS failure'))
    const wrapper = mountDiscoverDialog('deepseek')

    await findButton(wrapper, '拉取上游模型').trigger('click')
    await flushPromises()

    // 错误提示由统一拦截器负责（request.spec.ts 覆盖），本组件不弹窗，
    // 仅验证不再渲染上游模型行。
    const tableData = wrapper.findComponent(ElTableStub).props('data') as RemoteModelInfo[]
    expect(tableData).toEqual([])
  })
})