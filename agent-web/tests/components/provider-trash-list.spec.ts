// @vitest-environment happy-dom
/**
 * ProviderTrashList 视图测试（task-4e6 前端适配）：
 * - stub Element Plus 组件，挂载真实 WebAgentTable + ProviderTrashDetailDialog；
 * - mock `@/utils/request` 的 get/del（保留 @/api/provider 真实现），
 *   使断言可直达 axios config —— 硬删必须携带 params.hard=true 与
 *   X-Confirm-Hard-Delete 头；
 * - 硬删二次确认走 ElMessageBox.prompt（输入精确名称才放行）。
 */
import { beforeEach, describe, expect, it, vi } from 'vitest'
import { defineComponent, h, inject, provide } from 'vue'
import { flushPromises, mount } from '@vue/test-utils'
import type { VueWrapper } from '@vue/test-utils'

import ProviderTrashList from '@/views/provider/ProviderTrashList.vue'
import type {
  DeletedModelConfigRow,
  DeletedProviderRow,
} from '@/api/provider'
import type { PageQuery, PageResult } from '@/types'

/**
 * element-plus mock：ElMessage 既可函数调用（notify.ts 走 ElMessage({ type })）
 * 也暴露静态方法；ElMessageBox 提供 prompt（trash 硬删二次确认）。
 */
const { elMessageMock, elMessageBoxMock } = vi.hoisted(() => {
  const fn = vi.fn()
  return {
    elMessageMock: Object.assign(fn, {
      error: vi.fn(),
      success: vi.fn(),
      warning: vi.fn(),
    }),
    elMessageBoxMock: { confirm: vi.fn(), prompt: vi.fn() },
  }
})

vi.mock('element-plus', () => ({
  ElMessage: elMessageMock,
  ElMessageBox: elMessageBoxMock,
}))

const promptMock = elMessageBoxMock.prompt
const elMessageFn = elMessageMock

/**
 * 请求层 mock：@/api/provider 保持真实现，
 * 使 hardDeleteProvider 的 params/headers 透传可被直接断言。
 */
const { requestMock } = vi.hoisted(() => ({
  requestMock: {
    get: vi.fn(),
    post: vi.fn(),
    patch: vi.fn(),
    del: vi.fn(),
  },
}))

vi.mock('@/utils/request', () => requestMock)

/** 2 条墓碑 mock（形状与后端 _provider_trash_read 投影一致，无 auth_config 键） */
const TRASH_ROWS: DeletedProviderRow[] = [
  {
    id: 11,
    name: 'openai-old',
    type: 'OPENAI',
    base_url: 'https://api.openai.com/v1',
    api_key_masked: '****old1',
    enabled: true,
    deleted: true,
    created_by: 'seed',
    created_at: '2026-08-01T09:00:00',
    updated_at: '2026-08-10T12:00:00',
  },
  {
    id: 12,
    name: 'anthropic-old',
    type: 'ANTHROPIC',
    base_url: 'https://api.anthropic.com',
    api_key_masked: '****old2',
    enabled: false,
    deleted: true,
    created_by: 'seed',
    created_at: '2026-08-02T09:00:00',
    updated_at: '2026-08-11T12:00:00',
  },
]

/** 墓碑下的 model 清单（含 deleted 标记） */
const TRASH_MODELS: DeletedModelConfigRow[] = [
  {
    id: 101,
    provider_name: 'openai-old',
    name: 'gpt-old',
    model_id: 'gpt-old',
    ref: 'openai-old/gpt-old',
    context_size: null,
    extra_params: {},
    enabled: true,
    created_by: 'seed',
    created_at: '2026-08-01T09:00:00',
    updated_at: '2026-08-10T12:00:00',
    deleted: true,
  },
]

const ROWS_KEY = Symbol('table-rows')

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

const ElDividerStub = defineComponent({
  name: 'ElDivider',
  setup(_, { slots }) {
    return () => h('div', { class: 'el-divider-stub' }, slots.default?.())
  },
})

const ElInputStub = defineComponent({
  name: 'ElInput',
  props: {
    modelValue: { type: [String, Number], default: '' },
    placeholder: String,
    clearable: Boolean,
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
  return mount(ProviderTrashList, {
    global: {
      stubs: {
        ElTable: ElTableStub,
        ElTableColumn: ElTableColumnStub,
        ElPagination: ElPaginationStub,
        ElEmpty: true,
        ElTag: ElTagStub,
        ElButton: ElButtonStub,
        ElDialog: ElDialogStub,
        ElDivider: ElDividerStub,
        ElInput: ElInputStub,
        ElSelect: ElSelectStub,
        ElOption: ElOptionStub,
      },
      directives: { loading: () => undefined },
    },
  })
}

/** 定位操作列中第 rowIdx 行的目标按钮 */
function findRowButton(
  wrapper: VueWrapper,
  text: string,
  rowIdx: number,
): ReturnType<typeof wrapper.findAll>[number] {
  const actionsColumn = wrapper
    .findAll('.el-table-column-stub')
    .find((col) => col.findAll('button').some((b) => b.text().includes(text)))
  if (!actionsColumn) {
    throw new Error(`actions column with button "${text}" not found`)
  }
  const candidates = actionsColumn.findAll('button').filter((b) => b.text().includes(text))
  const target = candidates[rowIdx]
  if (!target) {
    throw new Error(`row ${rowIdx} button "${text}" not found`)
  }
  return target
}

beforeEach(() => {
  vi.clearAllMocks()
  elMessageFn.mockReset()
  promptMock.mockReset()
  // get 按 URL 分发：trash 列表 / trash 详情 model 清单
  requestMock.get.mockImplementation(async (url: string) => {
    if (url === '/providers/deleted') {
      return TRASH_ROWS.map((row) => ({ ...row }))
    }
    if (url === '/providers/deleted/openai-old/models') {
      return TRASH_MODELS.map((row) => ({ ...row }))
    }
    return []
  })
  requestMock.del.mockResolvedValue(null)
})

describe('ProviderTrashList 提供商回收站页', () => {
  it('挂载拉取 /providers/deleted 并渲染 2 条墓碑行', async () => {
    const wrapper = mountPage()
    await flushPromises()

    expect(requestMock.get).toHaveBeenCalledWith('/providers/deleted')

    const data = wrapper.findComponent(ElTableStub).props('data') as unknown[]
    expect(data).toHaveLength(2)
    expect(wrapper.text()).toContain('openai-old')
    expect(wrapper.text()).toContain('anthropic-old')
  })

  it('关键字过滤：输入 openai 后仅保留名称匹配的墓碑行', async () => {
    const wrapper = mountPage()
    await flushPromises()

    await wrapper.find('input[placeholder="按名称搜索"]').setValue('openai')
    await flushPromises()

    const data = wrapper.findComponent(ElTableStub).props('data') as DeletedProviderRow[]
    expect(data).toHaveLength(1)
    expect(data[0]?.name).toBe('openai-old')
  })

  it('类型过滤：api 透传 type=OPENAI 时仅返回 OPENAI 墓碑行', async () => {
    const wrapper = mountPage()
    await flushPromises()

    const table = wrapper.findComponent({ name: 'WebAgentTable' })
    /** type 是 query 通道透传的扩展键（PageQuery 之外），此处放宽断言签名 */
    const apiFn = table.props('api') as (
      query: PageQuery & { type?: string },
    ) => Promise<PageResult<DeletedProviderRow>>
    const result = await apiFn({ page: 1, pageSize: 10, type: 'OPENAI' })
    expect(result.items).toHaveLength(1)
    expect(result.items[0]?.type).toBe('OPENAI')

    const anthroOnly = await apiFn({ page: 1, pageSize: 10, type: 'ANTHROPIC' })
    expect(anthroOnly.items).toHaveLength(1)
    expect(anthroOnly.items[0]?.name).toBe('anthropic-old')
  })

  it('查看详情：打开弹窗并加载墓碑下的 model 清单', async () => {
    const wrapper = mountPage()
    await flushPromises()

    await findRowButton(wrapper, '查看详情', 0).trigger('click')
    await flushPromises()

    const dialog = wrapper.findComponent(ElDialogStub)
    expect(dialog.exists()).toBe(true)
    expect(dialog.attributes('data-title')).toContain('openai-old')

    expect(requestMock.get).toHaveBeenCalledWith(
      '/providers/deleted/openai-old/models',
    )
    // 弹窗渲染 model 行与详情字段
    expect(wrapper.text()).toContain('gpt-old')
    expect(wrapper.text()).toContain('openai-old/gpt-old')
  })

  it('永久删除：输入错误名称不发起硬删请求', async () => {
    promptMock.mockResolvedValue({ value: 'wrong-name' })
    const wrapper = mountPage()
    await flushPromises()

    await findRowButton(wrapper, '永久删除', 0).trigger('click')
    await flushPromises()

    expect(promptMock).toHaveBeenCalledTimes(1)
    const options = promptMock.mock.calls[0]?.[2] as {
      inputValidator?: (v: string) => string | boolean
    }
    // validator 拦截不匹配输入
    expect(options.inputValidator?.('wrong-name')).toBe('名称不匹配')
    expect(options.inputValidator?.('openai-old')).toBe(true)
    expect(requestMock.del).not.toHaveBeenCalled()
  })

  it('永久删除：输入精确名称后以 hard=true + X-Confirm-Hard-Delete 头发起 DELETE', async () => {
    promptMock.mockResolvedValue({ value: 'openai-old' })
    const wrapper = mountPage()
    await flushPromises()

    await findRowButton(wrapper, '永久删除', 0).trigger('click')
    await flushPromises()

    expect(requestMock.del).toHaveBeenCalledTimes(1)
    const [url, config] = requestMock.del.mock.calls[0] as [
      string,
      { params?: { hard?: boolean }; headers?: Record<string, string> },
    ]
    expect(url).toBe('/providers/openai-old')
    expect(config.params?.hard).toBe(true)
    expect(config.headers?.['X-Confirm-Hard-Delete']).toBe('true')
  })

  it('硬删成功后刷新列表并弹出成功提示', async () => {
    promptMock.mockResolvedValue({ value: 'openai-old' })
    const wrapper = mountPage()
    await flushPromises()
    expect(requestMock.get).toHaveBeenCalledTimes(1)

    await findRowButton(wrapper, '永久删除', 0).trigger('click')
    await flushPromises()

    expect(requestMock.del).toHaveBeenCalledTimes(1)
    expect(elMessageFn).toHaveBeenCalledWith(
      expect.objectContaining({
        type: 'success',
        message: '已永久删除：openai-old',
      }),
    )
    // tableRef.refresh() 触发第二次 trash 列表拉取
    expect(requestMock.get).toHaveBeenCalledTimes(2)
  })

  it('trash 响应无 auth_config 键时正常渲染脱敏行（安全契约）', async () => {
    const wrapper = mountPage()
    await flushPromises()

    // mock 数据不含 auth_config 键；组件读取脱敏投影不抛错
    expect(wrapper.text()).toContain('****old1')
    expect(wrapper.text()).toContain('****old2')
    expect(wrapper.text()).not.toContain('auth_config')
  })
})
