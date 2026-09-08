// @vitest-environment happy-dom
/**
 * WorkflowListView 视图测试（spec-06）：
 * - stub Element Plus 组件（不做真实渲染），挂载真实 WebAgentTable；
 * - mock `@/api/workflow` 的 listWorkflows / deleteWorkflow；
 * - 验证三态（加载 / 空 / 错误）、操作列（设计 / 执行 / 删除）行为。
 */
import { beforeEach, describe, expect, it, vi } from 'vitest'
import { defineComponent, h, inject, provide } from 'vue'
import { flushPromises, mount } from '@vue/test-utils'
import type { VueWrapper } from '@vue/test-utils'

import WorkflowList from '@/views/workflow/WorkflowListView.vue'
import type { WorkflowSummary } from '@/api/workflow'

const { elMessageMock, elMessageBoxMock } = vi.hoisted(() => {
  const fn = vi.fn()
  return {
    elMessageMock: Object.assign(fn, {
      error: vi.fn(),
      success: vi.fn(),
      warning: vi.fn(),
    }),
    elMessageBoxMock: { confirm: vi.fn() },
  }
})

vi.mock('element-plus', () => ({
  ElMessage: elMessageMock,
  ElMessageBox: elMessageBoxMock,
}))

const confirmMock = elMessageBoxMock.confirm
const elMessageFn = elMessageMock

const { apiMock } = vi.hoisted(() => ({
  apiMock: {
    listWorkflows: vi.fn(),
    deleteWorkflow: vi.fn(),
  },
}))

vi.mock('@/api/workflow', () => apiMock)

const routerPushMock = vi.fn()
vi.mock('vue-router', () => ({
  useRouter: () => ({ push: routerPushMock }),
}))

const ROWS: WorkflowSummary[] = [
  { workflow_id: 'wf-alpha', node_count: 3, entry_point: 'start', description: 'Alpha workflow' },
  { workflow_id: 'wf-beta', node_count: 5, entry_point: 'init' },
  { workflow_id: 'wf-gamma', node_count: 1, entry_point: 'run', description: 'Gamma pipeline' },
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

const ElEmptyStub = defineComponent({
  name: 'ElEmpty',
  props: { description: String },
  setup(props) {
    return () => h('div', { class: 'el-empty-stub' }, props.description ?? '')
  },
})

function mountPage(): VueWrapper {
  return mount(WorkflowList, {
    global: {
      stubs: {
        ElTable: ElTableStub,
        ElTableColumn: ElTableColumnStub,
        ElPagination: ElPaginationStub,
        ElEmpty: ElEmptyStub,
        ElButton: ElButtonStub,
        ElIcon: true,
        WorkflowExecuteDialog: true,
      },
      directives: { loading: () => undefined },
    },
  })
}

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
  elMessageMock.success.mockReset()
  confirmMock.mockReset()
  confirmMock.mockResolvedValue(undefined)
  apiMock.listWorkflows.mockResolvedValue(ROWS.map((r) => ({ ...r })))
  apiMock.deleteWorkflow.mockResolvedValue(null)
})

describe('WorkflowListView 列表页（spec-06）', () => {
  it('挂载调 listWorkflows() 并渲染返回行（workflow_id / node_count / entry_point）', async () => {
    const wrapper = mountPage()
    await flushPromises()

    expect(apiMock.listWorkflows).toHaveBeenCalledTimes(1)

    const data = wrapper.findComponent(ElTableStub).props('data') as unknown[]
    expect(data).toHaveLength(3)

    expect(wrapper.text()).toContain('wf-alpha')
    expect(wrapper.text()).toContain('wf-beta')
    expect(wrapper.text()).toContain('wf-gamma')
    expect(wrapper.text()).toContain('start')
    expect(wrapper.text()).toContain('init')
  })

  it('空数组 → 展示空态文案，不报错', async () => {
    apiMock.listWorkflows.mockResolvedValue([])
    const wrapper = mountPage()
    await flushPromises()

    expect(wrapper.find('.el-empty-stub').exists()).toBe(true)
    expect(wrapper.findComponent(ElTableStub).props('data')).toHaveLength(0)
  })

  it('listWorkflows reject → 不白屏，收敛为空数据', async () => {
    apiMock.listWorkflows.mockRejectedValue(new Error('network error'))
    const wrapper = mountPage()
    await flushPromises()

    expect(wrapper.findComponent(ElTableStub).props('data')).toHaveLength(0)
    expect(wrapper.find('.el-empty-stub').exists()).toBe(true)
  })

  it('点「删除」→ useConfirm 确认后调 deleteWorkflow(id)，成功 refresh', async () => {
    const wrapper = mountPage()
    await flushPromises()

    await findRowButton(wrapper, '删除', 0).trigger('click')
    expect(confirmMock).toHaveBeenCalledWith(
      expect.stringContaining('wf-alpha'),
      expect.any(String),
      expect.anything(),
    )
    await flushPromises()

    expect(apiMock.deleteWorkflow).toHaveBeenCalledWith('wf-alpha')
    expect(apiMock.listWorkflows).toHaveBeenCalledTimes(2)
  })

  it('删除取消：不调 deleteWorkflow，列表不变', async () => {
    confirmMock.mockRejectedValue('cancel')
    const wrapper = mountPage()
    await flushPromises()

    await findRowButton(wrapper, '删除', 0).trigger('click')
    await flushPromises()

    expect(apiMock.deleteWorkflow).not.toHaveBeenCalled()
    expect(apiMock.listWorkflows).toHaveBeenCalledTimes(1)
  })

  it('点「设计」→ router.push("/workflow/{id}/design")', async () => {
    const wrapper = mountPage()
    await flushPromises()

    await findRowButton(wrapper, '设计', 1).trigger('click')
    expect(routerPushMock).toHaveBeenCalledWith('/workflow/wf-beta/design')
  })

  it('点「执行」→ 打开对话框并传入 workflow_id', async () => {
    const wrapper = mountPage()
    await flushPromises()

    await findRowButton(wrapper, '执行', 0).trigger('click')
    await flushPromises()

    const dialog = wrapper.findComponent({ name: 'WorkflowExecuteDialog' })
    expect(dialog.exists()).toBe(true)
    expect(dialog.props('workflowId')).toBe('wf-alpha')
  })

  it('description 为空时显示 —', async () => {
    const wrapper = mountPage()
    await flushPromises()

    const data = wrapper.findComponent(ElTableStub).props('data') as WorkflowSummary[]
    expect(data[1].description).toBeUndefined()
    expect(wrapper.text()).toContain('—')
  })
})
