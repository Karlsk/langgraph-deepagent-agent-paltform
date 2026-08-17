// @vitest-environment happy-dom
/**
 * ProviderList 视图测试：stub 掉 Element Plus 组件（不做真实渲染），
 * 挂载真实 WebAgentTable + WebAgentFormDialog，验证 mock CRUD 全流程
 * （5 条渲染 / 新增 / 编辑回填 / 删除确认与取消），零真实网络。
 */
import { beforeEach, describe, expect, it, vi } from 'vitest'
import { defineComponent, h, inject, provide } from 'vue'
import { flushPromises, mount } from '@vue/test-utils'
import type { VueWrapper } from '@vue/test-utils'

import ProviderList from '@/views/provider/ProviderList.vue'

/** element-plus 仅保留 ElMessage / ElMessageBox（notify 与 useConfirm 依赖） */
vi.mock('element-plus', () => ({
  ElMessage: vi.fn(),
  ElMessageBox: { confirm: vi.fn() },
}))

import { ElMessageBox } from 'element-plus'

const confirmMock = ElMessageBox.confirm as unknown as ReturnType<typeof vi.fn>

const ROWS_KEY = Symbol('table-rows')

/** 渲染默认插槽（列定义）并向列 provide 当前 data，供单元格插槽按行渲染 */
const ElTableStub = defineComponent({
  name: 'ElTable',
  props: { data: { type: Array, default: () => [] as unknown[] } },
  setup(props, { slots }) {
    provide(ROWS_KEY, props)
    return () =>
      h('div', { class: 'el-table-stub' }, [
        props.data.length === 0 && slots.empty ? slots.empty() : undefined,
        slots.default ? slots.default() : undefined,
      ])
  },
})

/** 按行渲染单元格：slot 列走作用域插槽，普通列回退渲染 row[prop] 文本 */
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

const ElPaginationStub = defineComponent({
  name: 'ElPagination',
  props: { currentPage: Number, pageSize: Number, total: Number, layout: String },
  setup(props) {
    return () =>
      h('div', { class: 'el-pagination-stub', 'data-total': String(props.total) })
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

/** 渲染为真实 input（透传 placeholder 便于按占位符定位），双向绑定 modelValue */
const ElInputStub = defineComponent({
  name: 'ElInput',
  props: {
    modelValue: { type: [String, Number], default: '' },
    placeholder: String,
    showPassword: Boolean,
  },
  emits: ['update:modelValue'],
  setup(props, { emit }) {
    return () =>
      h('input', {
        class: 'el-input-stub',
        placeholder: props.placeholder,
        value: props.modelValue ?? '',
        onInput: (event: Event) =>
          emit('update:modelValue', (event.target as HTMLInputElement).value),
      })
  },
})

const ElSelectStub = defineComponent({
  name: 'ElSelect',
  setup(_, { slots }) {
    return () => h('div', { class: 'el-select-stub' }, slots.default?.())
  },
})

const ElOptionStub = defineComponent({
  name: 'ElOption',
  props: { label: String, value: String },
  setup(props) {
    return () => h('div', { class: 'el-option-stub' }, props.label)
  },
})

function mountPage(): VueWrapper {
  return mount(ProviderList, {
    global: {
      stubs: {
        ElTable: ElTableStub,
        ElTableColumn: ElTableColumnStub,
        ElPagination: ElPaginationStub,
        ElEmpty: true,
        ElTag: ElTagStub,
        ElButton: ElButtonStub,
        ElDialog: ElDialogStub,
        ElForm: ElFormStub,
        ElFormItem: ElFormItemStub,
        ElInput: ElInputStub,
        ElSelect: ElSelectStub,
        ElOption: ElOptionStub,
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

beforeEach(() => {
  vi.useFakeTimers()
  validateMock = vi.fn().mockResolvedValue(true)
  confirmMock.mockReset()
  confirmMock.mockResolvedValue(undefined)
})

describe('ProviderList 模型提供商管理页', () => {
  it('挂载渲染 5 条 mock 提供商与状态 tag', async () => {
    const wrapper = mountPage()
    await vi.advanceTimersByTimeAsync(300)

    const data = wrapper.findComponent(ElTableStub).props('data') as unknown[]
    expect(data).toHaveLength(5)
    expect(wrapper.text()).toContain('openai-prod')
    expect(wrapper.text()).toContain('ollama-local')
    expect(wrapper.text()).toContain('禁用')
    expect(wrapper.findAll('.el-tag-stub')).toHaveLength(5)
  })

  it('新增提供商：弹窗提交后列表变 6 条且默认启用', async () => {
    const wrapper = mountPage()
    await vi.advanceTimersByTimeAsync(300)

    await findButton(wrapper, '新增提供商').trigger('click')
    expect(wrapper.findComponent(ElDialogStub).exists()).toBe(true)

    await wrapper.find('input[placeholder="请输入提供商名称"]').setValue('new-provider')
    await findButton(wrapper, '确定').trigger('click')
    // 300ms 模拟提交 + 刷新后 api 的 200ms 延迟
    await vi.advanceTimersByTimeAsync(600)

    const data = wrapper.findComponent(ElTableStub).props('data') as Array<{
      name: string
      enabled: boolean
    }>
    expect(data).toHaveLength(6)
    const created = data.find((item) => item.name === 'new-provider')
    expect(created?.enabled).toBe(true)
    expect(wrapper.text()).toContain('new-provider')
  })

  it('编辑：弹窗回填行数据，提交后行数据更新且状态保留', async () => {
    const wrapper = mountPage()
    await vi.advanceTimersByTimeAsync(300)

    await findButton(wrapper, '编辑').trigger('click')
    expect(
      (wrapper.find('input[placeholder="请输入提供商名称"]').element as HTMLInputElement)
        .value,
    ).toBe('openai-prod')

    await wrapper.find('input[placeholder="请输入提供商名称"]').setValue('renamed')
    await findButton(wrapper, '确定').trigger('click')
    await vi.advanceTimersByTimeAsync(600)

    expect(wrapper.text()).toContain('renamed')
    expect(wrapper.text()).not.toContain('openai-prod')
  })

  it('删除确认后从列表移除并刷新', async () => {
    const wrapper = mountPage()
    await vi.advanceTimersByTimeAsync(300)

    await findButton(wrapper, '删除').trigger('click')
    expect(confirmMock).toHaveBeenCalledWith(
      '确定删除提供商「openai-prod」吗？',
      '删除确认',
      expect.anything(),
    )
    await flushPromises()
    await vi.advanceTimersByTimeAsync(300)

    expect(wrapper.findComponent(ElTableStub).props('data')).toHaveLength(4)
    expect(wrapper.text()).not.toContain('openai-prod')
  })

  it('删除取消后列表保持不变', async () => {
    confirmMock.mockRejectedValue('cancel')
    const wrapper = mountPage()
    await vi.advanceTimersByTimeAsync(300)

    await findButton(wrapper, '删除').trigger('click')
    await flushPromises()
    await vi.advanceTimersByTimeAsync(300)

    expect(wrapper.findComponent(ElTableStub).props('data')).toHaveLength(5)
  })
})
