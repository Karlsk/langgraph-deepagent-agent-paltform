// @vitest-environment happy-dom
/**
 * AgentAppOverview 总览页测试（/agentapp 只读概念页）：
 * - stub Element Plus 组件，挂载真实 WebAgentTable；
 * - mock `@/api/agentapps` 的 listAgentApps（零网络）与 vue-router.push；
 * - 覆盖：挂载拉取 + 统计卡数值 / 只读清单渲染 / 「管理」跳转分流
 *   （deepagents → /agent；其他引擎类型禁用）/ 刷新重拉。
 */
import { beforeEach, describe, expect, it, vi } from 'vitest'
import { defineComponent, h, inject, provide } from 'vue'
import { flushPromises, mount } from '@vue/test-utils'
import type { VueWrapper } from '@vue/test-utils'

import AgentAppOverview from '@/views/agent/AgentAppOverview.vue'
import type { AgentAppRow } from '@/api/agentapps'

/** vue-router mock：useRouter().push 收敛为 pushMock，断言跳转目标 */
const { pushMock } = vi.hoisted(() => ({ pushMock: vi.fn() }))
vi.mock('vue-router', () => ({
  useRouter: () => ({ push: pushMock }),
}))

const { apiMock } = vi.hoisted(() => ({
  apiMock: { listAgentApps: vi.fn() },
}))
vi.mock('@/api/agentapps', () => apiMock)

/**
 * 3 行 mock：2 已发布 + 1 草稿；含一行非 deepagents 引擎
 * （未来 workflow 类型的「管理」禁用分支）。
 */
const ROWS: AgentAppRow[] = [
  {
    id: 1,
    name: 'customer-support',
    system_prompt: '你是客服助手。',
    allowed_tools: null,
    model: null,
    skill_names: [],
    subagent_names: [],
    interrupt_on: {},
    engine: 'deepagents',
    status: 'published',
    published_hash: 'ph-1',
    agent_dir: 'agents/customer-support',
    workspace_hash: 'wh-1',
    agent_workspace_status: 'ready',
    version: 2,
    created_by: 'admin',
  },
  {
    id: 2,
    name: 'code-helper',
    system_prompt: '你是代码助手。',
    allowed_tools: null,
    model: null,
    skill_names: [],
    subagent_names: [],
    interrupt_on: {},
    engine: 'deepagents',
    status: 'draft',
    published_hash: null,
    agent_dir: null,
    workspace_hash: null,
    agent_workspace_status: 'pending',
    version: 1,
    created_by: 'admin',
  },
  {
    id: 3,
    name: 'flow-runner',
    system_prompt: '工作流引擎应用。',
    allowed_tools: null,
    model: null,
    skill_names: [],
    subagent_names: [],
    interrupt_on: {},
    engine: 'workflow',
    status: 'published',
    published_hash: 'ph-3',
    agent_dir: null,
    workspace_hash: null,
    agent_workspace_status: 'pending',
    version: 1,
    created_by: 'admin',
  },
]

const ROWS_KEY = Symbol('overview-table-rows')

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
      h('span', { class: 'el-tag-stub', 'data-type': props.type ?? '' }, slots.default?.())
  },
})

const ElEmptyStub = defineComponent({
  name: 'ElEmpty',
  setup(_, { slots }) {
    return () => h('div', { class: 'el-empty-stub' }, slots.default?.())
  },
})

function mountPage(): VueWrapper {
  return mount(AgentAppOverview, {
    global: {
      stubs: {
        ElTable: ElTableStub,
        ElTableColumn: ElTableColumnStub,
        ElButton: ElButtonStub,
        ElTag: ElTagStub,
        ElEmpty: ElEmptyStub,
        ElIcon: true,
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

/** 定位第 rowIdx 行的「管理」按钮（操作列按列聚合） */
function findManageButton(wrapper: VueWrapper, rowIdx: number) {
  const actionsColumn = wrapper
    .findAll('.el-table-column-stub')
    .find((col) => col.findAll('button').some((b) => b.text().includes('管理')))
  if (!actionsColumn) {
    throw new Error('actions column with 管理 button not found')
  }
  const candidates = actionsColumn.findAll('button').filter((b) => b.text().includes('管理'))
  const target = candidates[rowIdx]
  if (!target) {
    throw new Error(`row ${rowIdx} 管理 button not found`)
  }
  return target
}

beforeEach(() => {
  vi.clearAllMocks()
  pushMock.mockReset()
  apiMock.listAgentApps.mockImplementation(async () => ROWS.map((row) => ({ ...row })))
})

describe('AgentAppOverview 总览页（只读概念页）', () => {
  it('挂载调 listAgentApps；统计卡数值 = 总数 3 / 已发布 2 / 草稿 1', async () => {
    const wrapper = mountPage()
    await flushPromises()

    expect(apiMock.listAgentApps).toHaveBeenCalledTimes(1)

    // 统计卡：3 张卡依次是 总数 / 已发布 / 草稿
    const cards = wrapper.findAll('.overview-stats__card')
    expect(cards).toHaveLength(3)
    expect(cards[0].text()).toContain('3')
    expect(cards[0].text()).toContain('总数')
    expect(cards[1].text()).toContain('2')
    expect(cards[1].text()).toContain('已发布')
    expect(cards[2].text()).toContain('1')
    expect(cards[2].text()).toContain('草稿')
  })

  it('只读清单渲染 3 行 × 5 列：名称 / 类型标签 / 状态 tag', async () => {
    const wrapper = mountPage()
    await flushPromises()

    const data = wrapper.findComponent(ElTableStub).props('data') as unknown[]
    expect(data).toHaveLength(3)

    // 5 列：名称 / 类型 / 状态 / 版本 / 操作
    expect(wrapper.findAll('.el-table-column-stub')).toHaveLength(5)

    // 名称全部渲染
    expect(wrapper.text()).toContain('customer-support')
    expect(wrapper.text()).toContain('code-helper')
    expect(wrapper.text()).toContain('flow-runner')

    // 类型列：deepagents → 「Agent」；其他引擎原样展示
    expect(wrapper.text()).toContain('Agent')
    expect(wrapper.text()).toContain('workflow')

    // 状态 tag：已发布 / 草稿
    expect(wrapper.text()).toContain('已发布')
    expect(wrapper.text()).toContain('草稿')

    // 只读页不含任何写操作按钮（描述文案提及「发布」属预期，只断言按钮层）
    const buttonTexts = wrapper.findAll('button').map((b) => b.text())
    expect(buttonTexts.some((text) => text.includes('发布'))).toBe(false)
    expect(buttonTexts.some((text) => text.includes('删除'))).toBe(false)
    expect(buttonTexts.some((text) => text.includes('新建'))).toBe(false)
  })

  it('「管理」按钮：deepagents 引擎行跳转 /agent', async () => {
    const wrapper = mountPage()
    await flushPromises()

    const button = findManageButton(wrapper, 0)
    expect(button.attributes('data-disabled')).toBe('false')
    await button.trigger('click')

    expect(pushMock).toHaveBeenCalledWith('/agent')
  })

  it('「管理」按钮：非 deepagents 引擎行禁用，点击不跳转', async () => {
    const wrapper = mountPage()
    await flushPromises()

    const button = findManageButton(wrapper, 2)
    expect(button.attributes('data-disabled')).toBe('true')
    await button.trigger('click')

    expect(pushMock).not.toHaveBeenCalled()
  })

  it('刷新按钮：重新拉取全量并更新统计', async () => {
    const wrapper = mountPage()
    await flushPromises()
    expect(apiMock.listAgentApps).toHaveBeenCalledTimes(1)

    // 模拟后端新增一条草稿后刷新
    apiMock.listAgentApps.mockResolvedValueOnce([
      ...ROWS.map((row) => ({ ...row })),
      { ...ROWS[1], id: 4, name: 'new-draft', status: 'draft' as const },
    ])
    await findButton(wrapper, '刷新').trigger('click')
    await flushPromises()

    expect(apiMock.listAgentApps).toHaveBeenCalledTimes(2)
    const cards = wrapper.findAll('.overview-stats__card')
    expect(cards[0].text()).toContain('4')
    expect(cards[2].text()).toContain('2')
  })
})
