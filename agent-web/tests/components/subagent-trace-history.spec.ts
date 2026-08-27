// @vitest-environment happy-dom
/**
 * SubAgentTraceHistoryDialog 视图测试（测试历史弹窗）：
 * - stub Element Plus 组件（不做真实渲染）；
 * - mock `@/api/subagents` 的 listSubAgentTraces（零网络）；
 * - 覆盖：打开拉第 1 页、翻页带 page 参数、详情上抛、失败收敛为空数据。
 */
import { beforeEach, describe, expect, it, vi } from 'vitest'
import { defineComponent, h, inject, provide } from 'vue'
import { flushPromises, mount } from '@vue/test-utils'
import type { VueWrapper } from '@vue/test-utils'

import SubAgentTraceHistoryDialog from '@/views/agent/SubAgentTraceHistoryDialog.vue'
import type { SubAgentTraceSummary } from '@/api/subagents'
import type { PageResult } from '@/types'

const { apiMock } = vi.hoisted(() => ({
  apiMock: { listSubAgentTraces: vi.fn() },
}))
vi.mock('@/api/subagents', () => apiMock)

const TRACE_ROWS: SubAgentTraceSummary[] = [
  {
    id: 2,
    status: 'success',
    prompt: '请用一句话介绍你自己',
    model: 'deepseek-v4-flash',
    turns: 1,
    duration_seconds: 3.32,
    final_message: '我是 ISIS 配置排查专家。',
    error: null,
    created_by: '6',
    created_at: '2026-08-24T08:32:15',
  },
  {
    id: 1,
    status: 'error',
    prompt: '触发失败的 prompt',
    model: 'deepseek-v4-flash',
    turns: 0,
    duration_seconds: 0.42,
    final_message: '',
    error: 'RuntimeError: boom',
    created_by: null,
    created_at: '2026-08-24T07:00:00',
  },
]

const ROWS_KEY = Symbol('history-table-rows')

/** el-table stub：渲染默认插槽（列定义），向列 provide 当前 data */
const ElTableStub = defineComponent({
  name: 'ElTable',
  props: { data: { type: Array, default: () => [] as unknown[] } },
  setup(props, { slots }) {
    provide(ROWS_KEY, props)
    return () => h('div', { class: 'el-table-stub' }, slots.default ? slots.default() : undefined)
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
  emits: ['current-change'],
  setup(props, { emit }) {
    return () =>
      h('button', {
        class: 'el-pagination-stub',
        'data-total': String(props.total),
        'data-current': String(props.currentPage),
        onClick: () => emit('current-change', (props.currentPage ?? 1) + 1),
      })
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
          onClick: () => emit('click'),
        },
        slots.default ? slots.default() : undefined,
      )
  },
})

const ElDialogStub = defineComponent({
  name: 'ElDialog',
  props: { modelValue: Boolean, title: String, width: String },
  emits: ['update:modelValue'],
  setup(props, { slots, emit }) {
    return () =>
      props.modelValue
        ? h(
            'div',
            { class: 'el-dialog-stub', 'data-title': props.title },
            [
              slots.default ? slots.default() : undefined,
              slots.footer ? slots.footer() : undefined,
              h('button', {
                class: 'dialog-close-stub',
                onClick: () => emit('update:modelValue', false),
              }),
            ],
          )
        : null
  },
})

const ElTagStub = defineComponent({
  name: 'ElTag',
  props: { type: String, size: String },
  setup(props, { slots }) {
    return () =>
      h('span', { class: 'el-tag-stub', 'data-type': props.type ?? '' }, slots.default ? slots.default() : undefined)
  },
})

function mountDialog(visible = true): VueWrapper {
  return mount(SubAgentTraceHistoryDialog, {
    props: { modelValue: visible, agentName: 'isis-config-debug' },
    global: {
      stubs: {
        ElTable: ElTableStub,
        ElTableColumn: ElTableColumnStub,
        ElPagination: ElPaginationStub,
        ElButton: ElButtonStub,
        ElDialog: ElDialogStub,
        ElTag: ElTagStub,
      },
      directives: { loading: () => undefined },
    },
  })
}

function pageResult(items: SubAgentTraceSummary[], page = 1, total = items.length): PageResult<SubAgentTraceSummary> {
  return { items, total, page, pageSize: 10 }
}

beforeEach(() => {
  vi.clearAllMocks()
  apiMock.listSubAgentTraces.mockResolvedValue(pageResult(TRACE_ROWS.map((row) => ({ ...row }))))
})

describe('SubAgentTraceHistoryDialog 测试历史弹窗', () => {
  it('打开即拉第 1 页并渲染摘要列（含状态标签二分）', async () => {
    const wrapper = mountDialog()
    await flushPromises()

    expect(apiMock.listSubAgentTraces).toHaveBeenCalledWith('isis-config-debug', {
      page: 1,
      pageSize: 10,
    })
    expect(wrapper.find('.el-dialog-stub').attributes('data-title')).toContain('isis-config-debug')
    // prompt / 模型渲染
    expect(wrapper.text()).toContain('请用一句话介绍你自己')
    expect(wrapper.text()).toContain('deepseek-v4-flash')
    // 状态标签：成功 / 失败
    const tags = wrapper.findAll('.el-tag-stub')
    expect(tags.map((tag) => tag.attributes('data-type'))).toEqual(['success', 'danger'])
    expect(wrapper.text()).toContain('成功')
    expect(wrapper.text()).toContain('失败')
  })

  it('关闭状态不拉数据', async () => {
    mountDialog(false)
    await flushPromises()

    expect(apiMock.listSubAgentTraces).not.toHaveBeenCalled()
  })

  it('翻页以目标 page 调 listSubAgentTraces', async () => {
    apiMock.listSubAgentTraces
      .mockResolvedValueOnce(pageResult(TRACE_ROWS.map((row) => ({ ...row })), 1, 12))
      .mockResolvedValueOnce(pageResult(TRACE_ROWS.map((row) => ({ ...row })), 2, 12))
    const wrapper = mountDialog()
    await flushPromises()

    await wrapper.find('.el-pagination-stub').trigger('click')
    await flushPromises()

    expect(apiMock.listSubAgentTraces).toHaveBeenLastCalledWith('isis-config-debug', {
      page: 2,
      pageSize: 10,
    })
    expect(wrapper.find('.el-pagination-stub').attributes('data-total')).toBe('12')
    expect(wrapper.find('.el-pagination-stub').attributes('data-current')).toBe('2')
  })

  it('点击行内「详情」上抛 open-detail 携带该行 trace id', async () => {
    const wrapper = mountDialog()
    await flushPromises()

    const detailButtons = wrapper.findAll('button').filter((b) => b.text().includes('详情'))
    expect(detailButtons).toHaveLength(2)
    await detailButtons[0].trigger('click')

    expect(wrapper.emitted('open-detail')).toEqual([[2]])
  })

  it('请求失败收敛为空数据且不重复 toast（无 ElMessage 依赖）', async () => {
    apiMock.listSubAgentTraces.mockRejectedValue(new Error('500'))
    const wrapper = mountDialog()
    await flushPromises()

    const data = wrapper.findComponent(ElTableStub).props('data') as unknown[]
    expect(data).toHaveLength(0)
    expect(wrapper.find('.el-pagination-stub').attributes('data-total')).toBe('0')
  })

  it('关闭按钮上抛 update:modelValue=false', async () => {
    const wrapper = mountDialog()
    await flushPromises()

    await wrapper.find('.dialog-close-stub').trigger('click')
    expect(wrapper.emitted('update:modelValue')).toEqual([[false]])
  })
})
