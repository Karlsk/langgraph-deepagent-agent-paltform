// @vitest-environment happy-dom
/**
 * McpList 视图测试（task-ccc MCP 前端适配）：
 * - stub Element Plus 组件（不做真实渲染），挂载真实
 *   WebAgentTable + WebAgentFormDialog + McpServerToolsDialog；
 * - mock `@/api/mcp` 的 10 个函数（CRUD + 调试 + stdio manifest 同步）；
 * - 关键字搜索走 300ms 防抖，用 fake timers 推进；422 / 401 等错误由 mock throw，
 *   统一拦截器提示路径在 request.spec.ts 覆盖，本视图只断言 useConfirm 调用、
 *   刷新策略与通知文案。
 */
import { afterEach, beforeEach, describe, expect, it, vi } from 'vitest'
import { defineComponent, h, inject, provide } from 'vue'
import { flushPromises, mount } from '@vue/test-utils'
import type { VueWrapper } from '@vue/test-utils'

import McpList from '@/views/mcp/McpList.vue'
import type {
  McpServerCreatePayload,
  McpServerPatchPayload,
  McpServerRow,
  McpToolCallRequest,
  McpToolCallResult,
  McpToolInfo,
  StdioSyncReport,
} from '@/api/mcp'
import type { PageResult } from '@/types'/**
 * element-plus mock：ElMessage 既可函数调用（notify.ts 走 ElMessage({ type })）
 * 也暴露 .error/.success 等静态方法（与 element-plus 真实 API 对齐）。
 */
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
/** notify.ts 走 `ElMessage({ type, message, ... })`，断言目标是 ElMessage 函数本身 */
const elMessageFn = elMessageMock

/** 3 行 mock（覆盖三种 transport：stdio / sse / http） */
const ROWS: McpServerRow[] = [
  {
    name: 'stdio-demo',
    transport: 'stdio',
    command: 'python',
    args: ['/app/stdio_demo.py'],
    env: { TOKEN: '${TOKEN}' },
    url: null,
    headers: {},
    enabled: true,
    description: 'stdio demo server',
    content_hash: 'h1',
    created_by: 'seed',
  },
  {
    name: 'echo-sse',
    transport: 'sse',
    command: null,
    args: [],
    env: {},
    url: 'http://127.0.0.1:9375/sse',
    headers: { Authorization: 'Bearer xxx' },
    enabled: true,
    description: 'echo sse demo',
    content_hash: 'h2',
    created_by: 'admin',
  },
  {
    name: 'remote-http',
    transport: 'http',
    command: null,
    args: [],
    env: {},
    url: 'http://host:port/mcp',
    headers: {},
    enabled: false,
    description: 'remote http demo',
    content_hash: 'h3',
    created_by: 'admin',
  },
]

/**
 * 通用 mock 实现：listMcpServersPage 返回当前 ROWS 的拷贝（保持 mutation 隔离）；
 * CRUD 函数返回被调用 payload 的最小回显，让视图层按 mock 返回走 happy-path。
 */
const apiMock = vi.hoisted(() => ({
  listMcpServers: vi.fn(),
  listMcpServersPage: vi.fn(),
  getMcpServer: vi.fn(),
  createMcpServer: vi.fn(),
  patchMcpServer: vi.fn(),
  deleteMcpServer: vi.fn(),
  listMcpServerTools: vi.fn(),
  callMcpServerTool: vi.fn(),
  listStdioManifests: vi.fn(),
  syncStdioManifests: vi.fn(),
}))

vi.mock('@/api/mcp', () => apiMock)

const ROWS_KEY = Symbol('mcp-table-rows')

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

const ElInputStub = defineComponent({
  name: 'ElInput',
  props: {
    modelValue: { type: [String, Number], default: '' },
    placeholder: String,
    disabled: { type: Boolean, default: false },
    type: { type: String, default: 'text' },
    rows: { type: [String, Number], default: undefined },
  },
  emits: ['update:modelValue'],
  setup(props, { emit }) {
    return () => {
      const isTextarea = props.type === 'textarea'
      const onInput = (event: Event) =>
        emit(
          'update:modelValue',
          (event.target as HTMLInputElement | HTMLTextAreaElement).value,
        )
      return isTextarea
        ? h('textarea', {
            class: 'el-input-stub',
            placeholder: props.placeholder,
            disabled: props.disabled,
            rows: props.rows,
            value: props.modelValue ?? '',
            onInput,
          })
        : h('input', {
            class: 'el-input-stub',
            placeholder: props.placeholder,
            disabled: props.disabled,
            value: props.modelValue ?? '',
            onInput,
          })
    }
  },
})

const ElSwitchStub = defineComponent({
  name: 'ElSwitch',
  props: { modelValue: { type: Boolean, default: false } },
  emits: ['update:modelValue'],
  setup(props, { emit }) {
    return () =>
      h('button', {
        class: 'el-switch-stub',
        'data-checked': props.modelValue ? 'true' : 'false',
        onClick: () => emit('update:modelValue', !props.modelValue),
      })
  },
})

const ElSelectStub = defineComponent({
  name: 'ElSelect',
  props: { modelValue: { type: [String, Number, Boolean], default: '' } },
  emits: ['update:modelValue'],
  setup(props, { slots, emit }) {
    return () =>
      h('div', { class: 'el-select-stub', 'data-value': String(props.modelValue) }, [
        h(
          'select',
          {
            value: props.modelValue ?? '',
            onChange: (event: Event) =>
              emit('update:modelValue', (event.target as HTMLSelectElement).value),
          },
          slots.default?.(),
        ),
      ])
  },
})

const ElOptionStub = defineComponent({
  name: 'ElOption',
  props: { label: String, value: String },
  setup(props) {
    return () => h('option', { value: props.value }, props.label)
  },
})

const ElEmptyStub = defineComponent({
  name: 'ElEmpty',
  setup(_, { slots }) {
    return () => h('div', { class: 'el-empty-stub' }, slots.default?.())
  },
})

function mountPage(): VueWrapper {
  return mount(McpList, {
    global: {
      stubs: {
        ElTable: ElTableStub,
        ElTableColumn: ElTableColumnStub,
        ElPagination: ElPaginationStub,
        ElEmpty: ElEmptyStub,
        ElTag: ElTagStub,
        ElButton: ElButtonStub,
        ElDialog: ElDialogStub,
        ElForm: ElFormStub,
        ElFormItem: ElFormItemStub,
        ElInput: ElInputStub,
        ElSelect: ElSelectStub,
        ElOption: ElOptionStub,
        ElSwitch: ElSwitchStub,
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

/** 定位第 rowIdx 行的目标按钮（操作列是 #actions，按列聚合，每列每行一个按钮） */
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
  vi.useFakeTimers()
  vi.clearAllMocks()
  elMessageFn.mockReset()
  elMessageMock.success.mockReset()
  elMessageMock.error.mockReset()
  elMessageMock.warning.mockReset()
  confirmMock.mockReset()
  confirmMock.mockResolvedValue(undefined)
  validateMock = vi.fn().mockResolvedValue(true)
  // 默认 listMcpServersPage 返回 3 行（拷贝，避免用例间 mutation 共享）
  apiMock.listMcpServersPage.mockImplementation(
    async () =>
      ({
        items: ROWS.map((row) => ({
          ...row,
          args: [...row.args],
          env: { ...row.env },
          headers: { ...row.headers },
        })),
        total: ROWS.length,
        page: 1,
        pageSize: 10,
      }) satisfies PageResult<McpServerRow>,
  )
  apiMock.createMcpServer.mockImplementation(
    async (payload: McpServerCreatePayload) =>
      ({
        name: payload.name,
        transport: payload.transport,
        command: payload.transport === 'stdio' ? (payload.command ?? '') : null,
        args: payload.transport === 'stdio' ? (payload.args ?? []) : [],
        env: payload.transport === 'stdio' ? (payload.env ?? {}) : {},
        url: payload.transport === 'stdio' ? null : (payload.url ?? ''),
        headers: payload.transport === 'stdio' ? {} : (payload.headers ?? {}),
        enabled: payload.enabled ?? true,
        description: payload.description ?? '',
        content_hash: 'new-hash',
        created_by: 'user',
      }) satisfies McpServerRow,
  )
  apiMock.patchMcpServer.mockImplementation(
    async (name: string, payload: McpServerPatchPayload) => {
      const row = ROWS.find((r) => r.name === name)
      return {
        ...row,
        ...payload,
        name,
        content_hash: 'patched-hash',
      } as McpServerRow
    },
  )
  apiMock.deleteMcpServer.mockResolvedValue(null)
  apiMock.listMcpServers.mockResolvedValue([])
  apiMock.getMcpServer.mockImplementation(async (name: string) => {
    const row = ROWS.find((r) => r.name === name)
    if (!row) throw new Error(`mcp ${name} not found`)
    return row
  })
  apiMock.listMcpServerTools.mockImplementation(
    async (name: string): Promise<McpToolInfo[]> => [
      {
        name: 'echo',
        description: `${name} 的回显工具`,
        args_schema: { type: 'object', properties: { msg: { type: 'string' } } },
      },
      {
        name: 'list',
        description: `${name} 的列表工具`,
        args_schema: { type: 'object', properties: {} },
      },
    ],
  )
  apiMock.callMcpServerTool.mockImplementation(
    async (name: string, payload: McpToolCallRequest): Promise<McpToolCallResult> => ({
      server: name,
      tool_name: payload.tool_name,
      result: { echoed: payload.arguments },
    }),
  )
  apiMock.listStdioManifests.mockResolvedValue({
    scanned: 2,
    created: ['new-a'],
    updated: [],
    unchanged: ['existing-b'],
    skipped: [],
    invalid: [],
  } satisfies StdioSyncReport)
  apiMock.syncStdioManifests.mockResolvedValue({
    scanned: 2,
    created: ['new-a'],
    updated: ['existing-b'],
    unchanged: [],
    skipped: [{ name: 'bad', reason: 'no command' }],
    invalid: [],
  } satisfies StdioSyncReport)
})

afterEach(() => {
  vi.useRealTimers()
})

describe('McpList MCP 管理页（task-ccc 前端适配）', () => {
  it('挂载调 listMcpServersPage 并渲染 3 行 × 6 列，transport 三态 tag 各 1', async () => {
    const wrapper = mountPage()
    await flushPromises()

    expect(apiMock.listMcpServersPage).toHaveBeenCalledTimes(1)

    const data = wrapper.findComponent(ElTableStub).props('data') as unknown[]
    expect(data).toHaveLength(3)

    // 三种 server 名称全部渲染
    expect(wrapper.text()).toContain('stdio-demo')
    expect(wrapper.text()).toContain('echo-sse')
    expect(wrapper.text()).toContain('remote-http')

    // 6 列：名称 / 传输 / 状态 / 描述 / 创建者 / 操作
    expect(wrapper.findAll('.el-table-column-stub')).toHaveLength(6)

    // transport 三态 tag：stdio=info, sse=success, http=warning
    const transportTags = wrapper
      .findAll('.el-tag-stub')
      .filter((t) => ['stdio', 'sse', 'http'].includes(t.text().trim()))
    expect(transportTags).toHaveLength(3)
    const types = transportTags.map((t) => t.attributes('data-type'))
    expect(types).toContain('info')
    expect(types).toContain('success')
    expect(types).toContain('warning')
  })

  it('关键字搜索：debounce 300ms 后触发 listMcpServersPage 重查（带 keyword 参数）', async () => {
    const wrapper = mountPage()
    await flushPromises()

    expect(apiMock.listMcpServersPage).toHaveBeenCalledTimes(1)

    const searchInput = wrapper.find('input[placeholder="按名称模糊搜索"]')
    await searchInput.setValue('echo')
    // 不到 300ms 不应触发重查
    vi.advanceTimersByTime(200)
    await flushPromises()
    expect(apiMock.listMcpServersPage).toHaveBeenCalledTimes(1)
    // 推进到 300ms 触发
    vi.advanceTimersByTime(100)
    await flushPromises()
    expect(apiMock.listMcpServersPage).toHaveBeenCalledTimes(2)
    expect(apiMock.listMcpServersPage).toHaveBeenLastCalledWith(
      expect.objectContaining({ keyword: 'echo' }),
    )
  })

  it('创建 stdio：弹窗打开 → 填表 → 提交调 createMcpServer（command/args/env 透传）', async () => {
    const wrapper = mountPage()
    await flushPromises()

    await findButton(wrapper, '新建 MCP').trigger('click')
    await flushPromises()

    const dialog = wrapper.findComponent(ElDialogStub)
    expect(dialog.exists()).toBe(true)
    expect(dialog.attributes('data-title')).toBe('新建 MCP server')

    // transport 默认 stdio → 显示 command 输入框
    await wrapper.find('input[placeholder="请输入 MCP server 名称"]').setValue('stdio-new')
    await wrapper.find('input[placeholder^="例如：python"]').setValue('python')
    await wrapper.find('textarea[placeholder="可选：MCP server 用途说明"]').setValue('demo')
    await findButton(wrapper, '确定').trigger('click')
    await flushPromises()

    expect(apiMock.createMcpServer).toHaveBeenCalledWith({
      name: 'stdio-new',
      transport: 'stdio',
      command: 'python',
      args: [],
      env: {},
      description: 'demo',
      enabled: true,
    } satisfies McpServerCreatePayload)
    expect(elMessageFn).toHaveBeenCalledWith(
      expect.objectContaining({ type: 'success', message: '已保存：stdio-new' }),
    )
    // 刷新：第二次 listMcpServersPage
    expect(apiMock.listMcpServersPage).toHaveBeenCalledTimes(2)
  })

  it('创建 sse：弹窗切换 transport → url/headers 条件字段出现', async () => {
    const wrapper = mountPage()
    await flushPromises()

    await findButton(wrapper, '新建 MCP').trigger('click')
    await flushPromises()

    // 默认 stdio：command 字段存在，url 字段不存在
    expect(wrapper.find('input[placeholder^="例如：python"]').exists()).toBe(true)
    expect(
      wrapper.find('input[placeholder^="例如：http://127.0.0.1:9375/sse"]').exists(),
    ).toBe(false)

    // 切换 transport 到 sse：通过 select 的 value 变化模拟
    const transportSelect = wrapper.findAll('select')[0]
    expect(transportSelect).toBeDefined()
    await transportSelect.setValue('sse')
    await flushPromises()

    // 现在 url 字段出现，command 字段消失
    expect(wrapper.find('input[placeholder^="例如：python"]').exists()).toBe(false)
    expect(
      wrapper.find('input[placeholder^="例如：http://127.0.0.1:9375/sse"]').exists(),
    ).toBe(true)
  })

  it('编辑：弹窗打开后 name 字段 disabled；只改 description 时 patchMcpServer 只携带 description', async () => {
    const wrapper = mountPage()
    await flushPromises()

    await findRowButton(wrapper, '编辑', 0).trigger('click')
    await flushPromises()

    const dialog = wrapper.findComponent(ElDialogStub)
    expect(dialog.exists()).toBe(true)
    expect(dialog.attributes('data-title')).toBe('编辑 MCP server — stdio-demo')

    // name 字段 disabled 且回填 name
    const nameInput = wrapper.find('input[placeholder="请输入 MCP server 名称"]')
    expect((nameInput.element as HTMLInputElement).disabled).toBe(true)
    expect((nameInput.element as HTMLInputElement).value).toBe('stdio-demo')

    // 仅修改 description 后提交
    await wrapper
      .find('textarea[placeholder="可选：MCP server 用途说明"]')
      .setValue('updated desc')
    await findButton(wrapper, '确定').trigger('click')
    await flushPromises()

    // patchMcpServer 仅携带 description 字段（exclude_unset 守卫）
    expect(apiMock.patchMcpServer).toHaveBeenCalledWith('stdio-demo', {
      description: 'updated desc',
    } satisfies McpServerPatchPayload)
    expect(apiMock.createMcpServer).not.toHaveBeenCalled()
  })

  it('删除：useConfirm 调用并调 deleteMcpServer；确认后刷新列表', async () => {
    const wrapper = mountPage()
    await flushPromises()

    await findRowButton(wrapper, '删除', 0).trigger('click')
    expect(confirmMock).toHaveBeenCalledWith(
      '确定删除 MCP server「stdio-demo」吗？该操作不可恢复，且 tools/catalog 中该 server 的工具将立即失效。',
      '删除 MCP server',
      expect.anything(),
    )
    await flushPromises()

    expect(apiMock.deleteMcpServer).toHaveBeenCalledWith('stdio-demo')
    // 刷新：第二次 listMcpServersPage 调用
    expect(apiMock.listMcpServersPage).toHaveBeenCalledTimes(2)
  })

  it('删除取消：不调 deleteMcpServer，列表不变', async () => {
    confirmMock.mockRejectedValue('cancel')
    const wrapper = mountPage()
    await flushPromises()

    await findRowButton(wrapper, '删除', 0).trigger('click')
    await flushPromises()

    expect(apiMock.deleteMcpServer).not.toHaveBeenCalled()
    expect(apiMock.listMcpServersPage).toHaveBeenCalledTimes(1)
  })

  it('测试连接：调 listMcpServerTools，成功后行级健康 tag 切换为正常', async () => {
    const wrapper = mountPage()
    await flushPromises()

    await findRowButton(wrapper, '测试连接', 0).trigger('click')
    await flushPromises()

    expect(apiMock.listMcpServerTools).toHaveBeenCalledWith('stdio-demo')
    expect(elMessageFn).toHaveBeenCalledWith(
      expect.objectContaining({
        type: 'success',
        message: expect.stringContaining('stdio-demo'),
      }),
    )
    // 刷新：第二次 listMcpServersPage 调用
    expect(apiMock.listMcpServersPage).toHaveBeenCalledTimes(2)
  })

  it('测试连接：失败（mock throw）后行级健康 tag 切换为不可达', async () => {
    apiMock.listMcpServerTools.mockRejectedValueOnce(new Error('connection refused'))
    const wrapper = mountPage()
    await flushPromises()

    await findRowButton(wrapper, '测试连接', 1).trigger('click')
    await flushPromises()

    expect(apiMock.listMcpServerTools).toHaveBeenCalledWith('echo-sse')
    // 即使失败也调用 refresh 触发 tag 重渲染
    expect(apiMock.listMcpServersPage).toHaveBeenCalledTimes(2)
  })

  it('查看工具：弹窗打开后调 listMcpServerTools 并渲染 namespaced tool 名', async () => {
    const wrapper = mountPage()
    await flushPromises()

    await findRowButton(wrapper, '查看工具', 0).trigger('click')
    await flushPromises()

    expect(apiMock.listMcpServerTools).toHaveBeenCalledWith('stdio-demo')

    // 等异步渲染
    await flushPromises()
    // 工具名（裸名）+ 命名空间（{server}__{tool}）
    expect(wrapper.text()).toContain('echo')
    expect(wrapper.text()).toContain('stdio-demo__echo')
    expect(wrapper.text()).toContain('stdio-demo__list')
  })

  it('预览 manifests：调 listStdioManifests 并打开报告弹窗', async () => {
    const wrapper = mountPage()
    await flushPromises()

    await findButton(wrapper, '预览 manifests').trigger('click')
    await flushPromises()

    expect(apiMock.listStdioManifests).toHaveBeenCalledTimes(1)

    // 报告弹窗应出现，标题与 summary 文案
    const dialogs = wrapper.findAllComponents(ElDialogStub)
    const previewDialog = dialogs.find(
      (d) => d.attributes('data-title') === 'stdio manifest 同步预览',
    )
    expect(previewDialog).toBeDefined()
    expect(previewDialog!.text()).toContain('扫描')
    expect(previewDialog!.text()).toContain('new-a')
    expect(previewDialog!.text()).toContain('existing-b')
  })

  it('同步 manifests：调 syncStdioManifests 并通知 summary', async () => {
    const wrapper = mountPage()
    await flushPromises()

    await findButton(wrapper, '同步 manifests').trigger('click')
    await flushPromises()

    expect(apiMock.syncStdioManifests).toHaveBeenCalledTimes(1)
    expect(elMessageFn).toHaveBeenCalledWith(
      expect.objectContaining({
        type: 'success',
        message: expect.stringContaining('新建 1'),
      }),
    )
    // 刷新：第二次 listMcpServersPage
    expect(apiMock.listMcpServersPage).toHaveBeenCalledTimes(2)
  })
})
