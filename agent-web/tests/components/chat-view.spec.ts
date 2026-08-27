// @vitest-environment happy-dom
/**
 * ChatView 会话列表页测试（G3 spec-g3-session §11.6/§11.8）：
 * - stub Element Plus 组件（不做真实渲染），挂载真实
 *   WebAgentTable + WebAgentFormDialog；
 * - mock `@/api/sessions` 6 函数与 `@/api/assets` 的 listAgentApps（零网络）；
 * - 覆盖：首次分页加载 / agent_app 过滤 / 新建 / 重命名 / 删除确认（级联文案）/
 *   导出下载（json + jsonl，blob → a[download]）/ 越权 404 失败不刷新。
 */
import { afterEach, beforeEach, describe, expect, it, vi } from 'vitest'
import { defineComponent, h, inject, provide } from 'vue'
import { flushPromises, mount } from '@vue/test-utils'
import type { VueWrapper } from '@vue/test-utils'

import ChatView from '@/views/chat/ChatView.vue'
import type { SessionRead } from '@/api/sessions'
import type { AgentAppRow } from '@/api/assets'
import type { PageResult } from '@/types'

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

const { sessionsMock, assetsMock } = vi.hoisted(() => ({
  sessionsMock: {
    listSessions: vi.fn(),
    getSession: vi.fn(),
    createSession: vi.fn(),
    updateSession: vi.fn(),
    deleteSession: vi.fn(),
    exportSessionHistory: vi.fn(),
  },
  assetsMock: {
    listAgentApps: vi.fn(),
  },
}))

vi.mock('@/api/sessions', () => sessionsMock)
vi.mock('@/api/assets', () => assetsMock)

const ROWS: SessionRead[] = [
  {
    session_id: 's-001',
    name: '需求评审对话',
    agent_app_id: 1,
    created_at: '2026-08-27T08:00:00+00:00',
    updated_at: null,
    message_count: null,
  },
  {
    session_id: 's-002',
    name: '',
    agent_app_id: 2,
    created_at: '2026-08-26T09:30:00+00:00',
    updated_at: '2026-08-26T10:00:00+00:00',
    message_count: null,
  },
]

const APPS: AgentAppRow[] = [
  {
    id: 1,
    name: 'chat-assistant',
    system_prompt: '',
    allowed_tools: null,
    model: null,
    skill_names: [],
    subagent_names: [],
    interrupt_on: null,
    engine: 'deepagents',
    status: 'published',
    published_hash: 'abc',
    version: 1,
    created_by: 'admin',
  },
  {
    id: 2,
    name: 'code-helper',
    system_prompt: '',
    allowed_tools: null,
    model: null,
    skill_names: [],
    subagent_names: [],
    interrupt_on: null,
    engine: 'deepagents',
    status: 'published',
    published_hash: 'def',
    version: 1,
    created_by: 'admin',
  },
  {
    id: 3,
    name: 'draft-app',
    system_prompt: '',
    allowed_tools: null,
    model: null,
    skill_names: [],
    subagent_names: [],
    interrupt_on: null,
    engine: 'deepagents',
    status: 'draft',
    published_hash: null,
    version: 1,
    created_by: 'admin',
  },
]

// ---------------------------------------------------------------------------
// Element Plus stubs（列表 / 分页 / 弹窗 / 下拉）
// ---------------------------------------------------------------------------

const ROWS_KEY = Symbol('chat-table-rows')

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
  props: { currentPage: Number, pageSize: Number, total: Number },
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
          'data-disabled': props.disabled ? 'true' : 'false',
          onClick: () => emit('click'),
        },
        slots.default ? slots.default() : undefined,
      )
  },
})

const ElDialogStub = defineComponent({
  name: 'ElDialog',
  props: { modelValue: Boolean, title: String },
  emits: ['update:modelValue', 'close'],
  setup(props, { slots }) {
    return () =>
      props.modelValue
        ? h('div', { class: 'el-dialog-stub' }, [
            slots.default ? slots.default() : undefined,
            slots.footer ? slots.footer() : undefined,
          ])
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
  setup(_, { slots }) {
    return () => h('div', { class: 'el-form-item-stub' }, slots.default?.())
  },
})

const ElInputStub = defineComponent({
  name: 'ElInput',
  props: { modelValue: { type: [String, Number], default: '' } },
  emits: ['update:modelValue'],
  setup(props, { emit }) {
    return () =>
      h('input', {
        class: 'el-input-stub',
        value: props.modelValue ?? '',
        onInput: (event: Event) =>
          emit('update:modelValue', (event.target as HTMLInputElement).value),
      })
  },
})

/** el-select stub：向子 option provide 选中回调（v-model 更新） */
const SELECT_KEY = Symbol('chat-select-model')

const ElSelectStub = defineComponent({
  name: 'ElSelect',
  props: {
    modelValue: { type: [Number, String], default: null },
    disabled: { type: Boolean, default: false },
  },
  emits: ['update:modelValue'],
  setup(props, { emit, slots }) {
    provide(SELECT_KEY, {
      props,
      select: (value: unknown) => emit('update:modelValue', value),
    })
    return () =>
      h(
        'div',
        {
          class: 'el-select-stub',
          'data-disabled': props.disabled ? 'true' : 'false',
          'data-model-value': String(props.modelValue ?? ''),
        },
        slots.default?.(),
      )
  },
})

const ElOptionStub = defineComponent({
  name: 'ElOption',
  props: { label: String, value: { type: [Number, String], default: null } },
  setup(props, { slots }) {
    const host = inject<{ select: (value: unknown) => void }>(SELECT_KEY)
    return () =>
      h(
        'button',
        {
          class: 'el-option-stub',
          onClick: () => host?.select(props.value),
        },
        [props.label, slots.default ? slots.default() : undefined],
      )
  },
})

const ElEmptyStub = defineComponent({
  name: 'ElEmpty',
  setup() {
    return () => h('div', { class: 'el-empty-stub' })
  },
})

/** el-dropdown stub：item 点击经 provide/inject 冒泡 command 到 dropdown */
const COMMAND_KEY = Symbol('chat-dropdown-command')

const ElDropdownStub = defineComponent({
  name: 'ElDropdown',
  emits: ['command'],
  setup(_, { emit, slots }) {
    provide(COMMAND_KEY, (command: string) => emit('command', command))
    return () =>
      h('div', { class: 'el-dropdown-stub' }, [
        slots.default ? slots.default() : undefined,
        slots.dropdown ? slots.dropdown() : undefined,
      ])
  },
})

const ElDropdownMenuStub = defineComponent({
  name: 'ElDropdownMenu',
  setup(_, { slots }) {
    return () => h('div', { class: 'el-dropdown-menu-stub' }, slots.default?.())
  },
})

const ElDropdownItemStub = defineComponent({
  name: 'ElDropdownItem',
  props: { command: String },
  setup(props, { slots }) {
    const emitCommand = inject<(command: string) => void>(COMMAND_KEY)
    return () =>
      h(
        'button',
        {
          class: 'el-dropdown-item-stub',
          onClick: () => emitCommand?.(props.command ?? ''),
        },
        slots.default?.(),
      )
  },
})

function mountPage(): VueWrapper {
  return mount(ChatView, {
    global: {
      stubs: {
        ElTable: ElTableStub,
        ElTableColumn: ElTableColumnStub,
        ElPagination: ElPaginationStub,
        ElEmpty: ElEmptyStub,
        ElButton: ElButtonStub,
        ElDialog: ElDialogStub,
        ElForm: ElFormStub,
        ElFormItem: ElFormItemStub,
        ElInput: ElInputStub,
        ElSelect: ElSelectStub,
        ElOption: ElOptionStub,
        ElDropdown: ElDropdownStub,
        ElDropdownMenu: ElDropdownMenuStub,
        ElDropdownItem: ElDropdownItemStub,
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

/** 定位第 rowIdx 行的目标按钮（操作列按列聚合，每列每行一个按钮） */
function findRowButton(wrapper: VueWrapper, text: string, rowIdx: number) {
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
  confirmMock.mockReset()
  confirmMock.mockResolvedValue(undefined)
  validateMock = vi.fn().mockResolvedValue(true)

  sessionsMock.listSessions.mockImplementation(
    async () =>
      ({
        items: ROWS.map((row) => ({ ...row })),
        total: ROWS.length,
        page: 1,
        pageSize: 10,
      }) satisfies PageResult<SessionRead>,
  )
  sessionsMock.getSession.mockImplementation(async (sessionId: string) => {
    const row = ROWS.find((r) => r.session_id === sessionId)
    if (!row) throw new Error('404')
    return { ...row, message_count: 3 }
  })
  sessionsMock.createSession.mockImplementation(async (payload) => ({
    session_id: 's-new',
    name: payload.name,
    agent_app_id: payload.agent_app_id,
    created_at: '2026-08-27T10:00:00+00:00',
    updated_at: null,
    message_count: null,
  }))
  sessionsMock.updateSession.mockImplementation(async (sessionId: string, payload) => {
    const row = ROWS.find((r) => r.session_id === sessionId) ?? ROWS[0]
    return { ...row, name: payload.name, updated_at: '2026-08-27T11:00:00+00:00' }
  })
  sessionsMock.deleteSession.mockResolvedValue(null)
  sessionsMock.exportSessionHistory.mockResolvedValue(new Blob(['{}']))

  assetsMock.listAgentApps.mockResolvedValue(APPS.map((row) => ({ ...row })))
})

afterEach(() => {
  vi.restoreAllMocks()
})

describe('ChatView 列表加载与过滤', () => {
  it('挂载即拉第一页（默认 pageSize=10），仅展示 published 应用选项', async () => {
    const wrapper = mountPage()
    await flushPromises()

    expect(sessionsMock.listSessions).toHaveBeenCalledWith({
      page: 1,
      pageSize: 10,
      agentAppId: undefined,
    })
    // draft 应用不进下拉（头部过滤 + 表单选项共用 appOptions）
    const optionTexts = wrapper.findAll('.el-option-stub').map((item) => item.text())
    expect(optionTexts.join()).toContain('chat-assistant')
    expect(optionTexts.join()).not.toContain('draft-app')
    // 空名会话展示「未命名会话」占位
    expect(wrapper.text()).toContain('未命名会话')
  })

  it('选择过滤应用后以 agentAppId 重新请求并回到第一页', async () => {
    const wrapper = mountPage()
    await flushPromises()
    sessionsMock.listSessions.mockClear()

    // 头部过滤下拉是第一个 el-select；点击 code-helper 选项（value=2）
    const filterOptions = wrapper.findAll('.el-select-stub')[0].findAll('.el-option-stub')
    await filterOptions[1].trigger('click')
    await flushPromises()

    expect(sessionsMock.listSessions).toHaveBeenCalledWith({
      page: 1,
      pageSize: 10,
      agentAppId: 2,
    })
  })
})

describe('ChatView 新建与重命名', () => {
  it('新建会话：选应用 + 名称后 POST，成功后刷新列表', async () => {
    const wrapper = mountPage()
    await flushPromises()
    sessionsMock.listSessions.mockClear()

    await findButton(wrapper, '新建会话').trigger('click')
    await flushPromises()

    // 表单内 el-select 是弹窗里的第二个；选中 chat-assistant（value=1）
    const selects = wrapper.findAll('.el-select-stub')
    const dialogSelect = selects[selects.length - 1]
    await dialogSelect.findAll('.el-option-stub')[0].trigger('click')
    await wrapper.find('.el-input-stub').setValue('评审跟进')
    await findButton(wrapper, '确定').trigger('click')
    await flushPromises()

    expect(sessionsMock.createSession).toHaveBeenCalledWith({
      agent_app_id: 1,
      name: '评审跟进',
    })
    expect(sessionsMock.listSessions).toHaveBeenCalled()
  })

  it('重命名：编辑模式锁定应用下拉，仅提交 name', async () => {
    const wrapper = mountPage()
    await flushPromises()

    await findRowButton(wrapper, '重命名', 0).trigger('click')
    await flushPromises()

    // 编辑模式应用下拉禁用
    const selects = wrapper.findAll('.el-select-stub')
    const dialogSelect = selects[selects.length - 1]
    expect(dialogSelect.attributes('data-disabled')).toBe('true')

    await wrapper.find('.el-input-stub').setValue('新会话名')
    await findButton(wrapper, '确定').trigger('click')
    await flushPromises()

    expect(sessionsMock.updateSession).toHaveBeenCalledWith('s-001', { name: '新会话名' })
    expect(sessionsMock.createSession).not.toHaveBeenCalled()
  })
})

describe('ChatView 删除确认（级联提示）', () => {
  it('确认后调 DELETE 并刷新；提示文案含级联删除说明', async () => {
    const wrapper = mountPage()
    await flushPromises()
    sessionsMock.listSessions.mockClear()

    await findRowButton(wrapper, '删除', 0).trigger('click')
    await flushPromises()

    expect(confirmMock).toHaveBeenCalledOnce()
    const message = confirmMock.mock.calls[0][0] as string
    expect(message).toContain('级联删除对话记录')
    expect(sessionsMock.deleteSession).toHaveBeenCalledWith('s-001')
    expect(sessionsMock.listSessions).toHaveBeenCalled()
  })

  it('取消确认不调 API 也不刷新', async () => {
    confirmMock.mockRejectedValue(new Error('cancel'))
    const wrapper = mountPage()
    await flushPromises()
    sessionsMock.listSessions.mockClear()

    await findRowButton(wrapper, '删除', 0).trigger('click')
    await flushPromises()

    expect(sessionsMock.deleteSession).not.toHaveBeenCalled()
    expect(sessionsMock.listSessions).not.toHaveBeenCalled()
  })

  it('越权 / 不存在（后端 404）：删除失败不刷新列表', async () => {
    sessionsMock.deleteSession.mockRejectedValue(new Error('404'))
    const wrapper = mountPage()
    await flushPromises()
    sessionsMock.listSessions.mockClear()

    await findRowButton(wrapper, '删除', 0).trigger('click')
    await flushPromises()

    expect(sessionsMock.deleteSession).toHaveBeenCalledWith('s-001')
    expect(sessionsMock.listSessions).not.toHaveBeenCalled()
  })
})

describe('ChatView 导出（非信封文件下载）', () => {
  const createObjectURL = vi.fn(() => 'blob:session-export')
  const revokeObjectURL = vi.fn()
  let clickSpy: ReturnType<typeof vi.spyOn>

  beforeEach(() => {
    URL.createObjectURL = createObjectURL
    URL.revokeObjectURL = revokeObjectURL
    createObjectURL.mockClear()
    revokeObjectURL.mockClear()
    clickSpy = vi
      .spyOn(HTMLAnchorElement.prototype, 'click')
      .mockImplementation(() => undefined)
  })

  it('导出 JSON：blob 经 a[download] 触发保存，文件名带 .json 后缀', async () => {
    const wrapper = mountPage()
    await flushPromises()

    // 第一行的导出下拉 → JSON 项
    const dropdowns = wrapper.findAll('.el-dropdown-stub')
    const items = dropdowns[0].findAll('.el-dropdown-item-stub')
    await items[0].trigger('click')
    await flushPromises()

    expect(sessionsMock.exportSessionHistory).toHaveBeenCalledWith('s-001', 'json')
    expect(createObjectURL).toHaveBeenCalled()
    const anchor = clickSpy.mock.instances[0] as HTMLAnchorElement
    expect(anchor.download).toBe('s-001.json')
    expect(revokeObjectURL).toHaveBeenCalledWith('blob:session-export')
  })

  it('导出 JSONL：format 参数透传且后缀为 .jsonl', async () => {
    const wrapper = mountPage()
    await flushPromises()

    const dropdowns = wrapper.findAll('.el-dropdown-stub')
    const items = dropdowns[1].findAll('.el-dropdown-item-stub')
    await items[1].trigger('click')
    await flushPromises()

    expect(sessionsMock.exportSessionHistory).toHaveBeenCalledWith('s-002', 'jsonl')
    const anchor = clickSpy.mock.instances[0] as HTMLAnchorElement
    expect(anchor.download).toBe('s-002.jsonl')
  })

  it('越权 / 不存在（后端 404）：导出失败不触发下载', async () => {
    sessionsMock.exportSessionHistory.mockRejectedValue(new Error('404'))
    const wrapper = mountPage()
    await flushPromises()

    const dropdowns = wrapper.findAll('.el-dropdown-stub')
    await dropdowns[0].findAll('.el-dropdown-item-stub')[0].trigger('click')
    await flushPromises()

    expect(createObjectURL).not.toHaveBeenCalled()
    expect(clickSpy).not.toHaveBeenCalled()
  })
})

describe('ChatView 列展示', () => {
  it('列表行展示应用名与创建时间；消息数列表态为 —', async () => {
    const wrapper = mountPage()
    await flushPromises()

    expect(wrapper.text()).toContain('chat-assistant')
    expect(wrapper.text()).toContain('code-helper')
    expect(wrapper.text()).toContain('2026-08-27 08:00:00')
    // message_count 列表恒 null → 占位「—」
    const text = wrapper.text()
    expect(text.includes('—')).toBe(true)
  })
})
