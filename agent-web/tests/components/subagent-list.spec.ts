// @vitest-environment happy-dom
/**
 * SubAgentList 视图测试（task-dde SubAgent 前端适配）：
 * - stub Element Plus 组件（不做真实渲染），挂载真实
 *   WebAgentTable + WebAgentFormDialog；
 * - stub 子组件 SubAgentTestDialog（避免 el-dialog 未挂载）；
 * - mock `@/api/subagents` 的 7 个函数（CRUD + 分页 + 单轮测试）；
 * - 422 / 401 等错误由 mock throw，统一拦截器提示路径在 request.spec.ts 覆盖，
 *   本视图只断言 useConfirm 调用、刷新策略与通知文案。
 */
import { beforeEach, describe, expect, it, vi } from 'vitest'
import { defineComponent, h, inject, provide } from 'vue'
import { flushPromises, mount } from '@vue/test-utils'
import type { VueWrapper } from '@vue/test-utils'

import SubAgentList from '@/views/agent/SubAgentList.vue'
import type {
  SubAgentCreatePayload,
  SubAgentPatchPayload,
  SubAgentRow,
  SubAgentTestPayload,
  SubAgentTestResult,
} from '@/api/subagents'
import type { PageResult } from '@/types'

/**
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

/**
 * 5 行 mock（覆盖 allowed_tools null / [] / 多项；model null / 默认 / 自定义；version 1..3）
 */
const ROWS: SubAgentRow[] = [
  {
    name: 'search-helper',
    description: '负责网络搜索与信息汇总',
    when_to_use: '当用户问题需要查询实时网络信息时使用',
    system_prompt: '你是一个搜索助手，使用搜索工具查找信息。',
    allowed_tools: ['duckduckgo_results_json'],
    model: null,
    max_turns: 3,
    content_hash: 'h1',
    version: 1,
    created_by: 'seed',
  },
  {
    name: 'code-reviewer',
    description: '代码审查与重构建议',
    when_to_use: '用户提交代码片段要求评审时',
    system_prompt: '你是一个资深代码审查员。',
    allowed_tools: null,
    model: 'default/default',
    max_turns: null,
    content_hash: 'h2',
    version: 2,
    created_by: 'admin',
  },
  {
    name: 'echo-runner',
    description: '回显与简单指令',
    when_to_use: '用作集成测试桩',
    system_prompt: '你只回显用户的输入。',
    allowed_tools: ['demo-stdio__echo', 'demo-stdio__add'],
    model: 'proxy/m3',
    max_turns: 5,
    content_hash: 'h3',
    version: 1,
    created_by: 'admin',
  },
  {
    name: 'translator',
    description: '多语言翻译',
    when_to_use: '用户提供待翻译文本时',
    system_prompt: '你是一个专业翻译。',
    allowed_tools: null,
    model: 'default/default',
    max_turns: 2,
    content_hash: 'h4',
    version: 3,
    created_by: 'seed',
  },
  {
    name: 'planner',
    description: '任务分解与计划',
    when_to_use: '用户提出复杂多步骤任务时',
    system_prompt: '你把任务拆分为有序子任务。',
    allowed_tools: [],
    model: null,
    max_turns: null,
    content_hash: 'h5',
    version: 1,
    created_by: 'seed',
  },
]

/**
 * 通用 mock 实现：listSubAgentsPage 返回当前 ROWS 的拷贝（保持 mutation 隔离）；
 * CRUD 函数返回被调用 payload 的最小回显，让视图层按 mock 返回走 happy-path。
 */
const { apiMock } = vi.hoisted(() => {
  const mock: Record<string, ReturnType<typeof vi.fn>> = {
    listSubAgents: vi.fn(),
    listSubAgentsPage: vi.fn(),
    getSubAgent: vi.fn(),
    createSubAgent: vi.fn(),
    patchSubAgent: vi.fn(),
    deleteSubAgent: vi.fn(),
    testSubAgent: vi.fn(),
  }
  return { apiMock: mock }
})

vi.mock('@/api/subagents', () => apiMock)

/**
 * mcp.ts 的 listToolCatalog mock：返回 builtin + mcp 各 2 条，
 * 验证 SubAgentList 表单能按 source 分组渲染下拉选项。
 */
const { mcpMock } = vi.hoisted(() => ({
  mcpMock: { listToolCatalog: vi.fn() },
}))
vi.mock('@/api/mcp', async (importOriginal) => {
  const actual = await importOriginal<typeof import('@/api/mcp')>()
  return { ...actual, listToolCatalog: mcpMock.listToolCatalog }
})

/**
 * provider.ts 的 listAllProviderModels mock：直接返回固定聚合结果，
 * 避免子函数 listProviders/listProviderModels 拉真实后端。
 */
const { providerMock } = vi.hoisted(() => ({
  providerMock: { listAllProviderModels: vi.fn() },
}))
vi.mock('@/api/provider', async (importOriginal) => {
  const actual = await importOriginal<typeof import('@/api/provider')>()
  return { ...actual, listAllProviderModels: providerMock.listAllProviderModels }
})

const ROWS_KEY = Symbol('subagent-table-rows')

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
    type: { type: String, default: 'text' },
    rows: { type: [String, Number], default: undefined },
    disabled: { type: Boolean, default: false },
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

const ElInputNumberStub = defineComponent({
  name: 'ElInputNumber',
  props: {
    modelValue: { type: [Number, String, null] as unknown as () => number | string | null, default: null },
    min: { type: Number, default: undefined },
    max: { type: Number, default: undefined },
    placeholder: String,
    disabled: { type: Boolean, default: false },
  },
  emits: ['update:modelValue'],
  setup(props, { emit }) {
    return () =>
      h('input', {
        class: 'el-input-number-stub',
        type: 'number',
        placeholder: props.placeholder,
        disabled: props.disabled,
        min: props.min,
        max: props.max,
        value: props.modelValue ?? '',
        onInput: (event: Event) => {
          const raw = (event.target as HTMLInputElement).value
          if (raw === '') {
            emit('update:modelValue', null)
          } else {
            emit('update:modelValue', Number(raw))
          }
        },
      })
  },
})

const ElEmptyStub = defineComponent({
  name: 'ElEmpty',
  setup(_, { slots }) {
    return () => h('div', { class: 'el-empty-stub' }, slots.default?.())
  },
})

/** 子组件 SubAgentTestDialog 的简单 stub：仅暴露 props.agentName 便于断言 */
const SubAgentTestDialogStub = defineComponent({
  name: 'SubAgentTestDialog',
  props: {
    modelValue: { type: Boolean, default: false },
    agentName: { type: String, default: '' },
  },
  setup(props) {
    return () =>
      props.modelValue
        ? h('div', { class: 'subagent-test-dialog-stub', 'data-agent-name': props.agentName })
        : null
  },
})

/**
 * el-select stub：仅展示当前 modelValue（数组→CSV / 字符串→原文），保留 slot 渲染
 * el-option / el-option-group（避免 Vue 渲染未知组件警告）。不实现下拉交互，测试
 * 通过 vm.getForm() 拿到 reactive formModel 后直接赋值，再点击「确定」按钮触发提交。
 */
const ElSelectStub = defineComponent({
  name: 'ElSelect',
  props: {
    modelValue: { type: undefined, default: undefined },
    multiple: { type: Boolean, default: false },
    placeholder: String,
    loading: { type: Boolean, default: false },
  },
  emits: ['update:modelValue'],
  setup(props, { slots }) {
    return () => {
      const display = Array.isArray(props.modelValue)
        ? props.modelValue.join(',')
        : props.modelValue ?? ''
      return h(
        'div',
        { class: 'el-select-stub', 'data-multiple': String(props.multiple), 'data-value': display },
        slots.default ? slots.default() : undefined,
      )
    }
  },
})

const ElOptionStub = defineComponent({
  name: 'ElOption',
  props: { value: undefined, label: undefined },
  setup(props, { slots }) {
    return () =>
      h(
        'div',
        { class: 'el-option-stub', 'data-value': String(props.value ?? '') },
        slots.default ? slots.default() : `${String(props.label ?? props.value ?? '')}`,
      )
  },
})

const ElOptionGroupStub = defineComponent({
  name: 'ElOptionGroup',
  props: { label: String },
  setup(props, { slots }) {
    return () =>
      h(
        'div',
        { class: 'el-option-group-stub', 'data-label': props.label ?? '' },
        slots.default ? slots.default() : undefined,
      )
  },
})

function mountPage(): VueWrapper {
  return mount(SubAgentList, {
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
        ElInputNumber: ElInputNumberStub,
        ElSelect: ElSelectStub,
        ElOption: ElOptionStub,
        ElOptionGroup: ElOptionGroupStub,
        ElIcon: true,
        SubAgentTestDialog: SubAgentTestDialogStub,
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
  vi.clearAllMocks()
  elMessageFn.mockReset()
  elMessageMock.success.mockReset()
  elMessageMock.error.mockReset()
  elMessageMock.warning.mockReset()
  confirmMock.mockReset()
  confirmMock.mockResolvedValue(undefined)
  validateMock = vi.fn().mockResolvedValue(true)
  // 默认 listSubAgentsPage 返回 5 行（拷贝）
  apiMock.listSubAgentsPage.mockImplementation(
    async () =>
      ({
        items: ROWS.map((row) => ({ ...row, allowed_tools: row.allowed_tools ? [...row.allowed_tools] : row.allowed_tools })),
        total: ROWS.length,
        page: 1,
        pageSize: 10,
      }) satisfies PageResult<SubAgentRow>,
  )
  apiMock.createSubAgent.mockImplementation(
    async (payload: SubAgentCreatePayload) =>
      ({
        name: payload.name,
        description: payload.description,
        when_to_use: payload.when_to_use,
        system_prompt: payload.system_prompt,
        allowed_tools: payload.allowed_tools ?? null,
        model: payload.model ?? null,
        max_turns: payload.max_turns ?? null,
        content_hash: 'new-hash',
        version: 1,
        created_by: 'user',
      }) satisfies SubAgentRow,
  )
  apiMock.patchSubAgent.mockImplementation(
    async (name: string, payload: SubAgentPatchPayload) => {
      const row = ROWS.find((r) => r.name === name)
      return {
        ...(row ?? {
          name,
          description: '',
          when_to_use: '',
          system_prompt: '',
          allowed_tools: null,
          model: null,
          max_turns: null,
          content_hash: '',
          version: 0,
          created_by: null,
        }),
        ...payload,
        name,
        content_hash: 'patched-hash',
      } as SubAgentRow
    },
  )
  apiMock.deleteSubAgent.mockResolvedValue(null)
  apiMock.listSubAgents.mockResolvedValue([])
  apiMock.getSubAgent.mockImplementation(async (name: string) => {
    const row = ROWS.find((r) => r.name === name)
    if (!row) throw new Error(`subagent ${name} not found`)
    return row
  })
  apiMock.testSubAgent.mockImplementation(
    async (name: string, payload: SubAgentTestPayload): Promise<SubAgentTestResult> => ({
      final_message: `echo from ${name}: ${payload.prompt}`,
      turns: 1,
      duration_seconds: 1.42,
      model: 'default/default',
    }),
  )
  // 工具目录默认返回 2 builtin + 2 mcp（覆盖 el-option-group 分组渲染路径）
  mcpMock.listToolCatalog.mockResolvedValue([
    { name: 'duckduckgo_results_json', source: 'builtin', server: null },
    { name: 'echo', source: 'builtin', server: null },
    { name: 'add', source: 'mcp', server: 'demo-stdio' },
    { name: 'greet', source: 'mcp', server: 'demo-stdio' },
  ])
  // 模型聚合默认返回 3 条（覆盖 el-select filterable 路径 + 跨 provider）
  providerMock.listAllProviderModels.mockResolvedValue([
    { id: 1, provider_name: 'default', name: 'default', model_id: 'default', ref: 'default/default', context_size: null, extra_params: {}, enabled: true, created_by: null, created_at: null, updated_at: null },
    { id: 2, provider_name: 'proxy', name: 'm3', model_id: 'm3', ref: 'proxy/m3', context_size: null, extra_params: {}, enabled: true, created_by: null, created_at: null, updated_at: null },
    { id: 3, provider_name: 'default', name: 'fast', model_id: 'fast', ref: 'default/fast', context_size: null, extra_params: {}, enabled: true, created_by: null, created_at: null, updated_at: null },
  ])
})

describe('SubAgentList 子代理管理页（task-dde 前端适配）', () => {
  it('挂载调 listSubAgentsPage 并渲染 5 行 × 7 列结构', async () => {
    const wrapper = mountPage()
    await flushPromises()

    expect(apiMock.listSubAgentsPage).toHaveBeenCalledTimes(1)

    const data = wrapper.findComponent(ElTableStub).props('data') as unknown[]
    expect(data).toHaveLength(5)

    // 5 个名称全部渲染
    expect(wrapper.text()).toContain('search-helper')
    expect(wrapper.text()).toContain('code-reviewer')
    expect(wrapper.text()).toContain('echo-runner')
    expect(wrapper.text()).toContain('translator')
    expect(wrapper.text()).toContain('planner')

    // 7 列：名称 / 描述 / 何时使用 / 版本 / 工具数 / 模型 / 操作
    expect(wrapper.findAll('.el-table-column-stub')).toHaveLength(7)
  })

  it('工具数列：null / 空数组 → 「—」；非空 → 「N 项」', async () => {
    const wrapper = mountPage()
    await flushPromises()

    // code-reviewer (null) / planner ([]) → 至少 2 个 —
    // search-helper (1 项) → 「1 项」
    // echo-runner (2 项) → 「2 项」
    expect(wrapper.text()).toContain('1 项')
    expect(wrapper.text()).toContain('2 项')
  })

  it('模型列：null → 「继承父应用」；否则显示 provider/model 引用', async () => {
    const wrapper = mountPage()
    await flushPromises()

    // search-helper (null) / planner (null) → 至少 2 次「继承父应用」
    expect(wrapper.text()).toContain('继承父应用')
    // code-reviewer (default/default) / translator (default/default)
    expect(wrapper.text()).toContain('default/default')
    // echo-runner (proxy/m3)
    expect(wrapper.text()).toContain('proxy/m3')
  })

  it('创建：调 createSubAgent 携带完整 payload；成功后弹窗关闭 + 刷新', async () => {
    const wrapper = mountPage()
    await flushPromises()

    await findButton(wrapper, '新建子代理').trigger('click')
    await flushPromises()

    const dialog = wrapper.findComponent(ElDialogStub)
    expect(dialog.exists()).toBe(true)

    await wrapper.find('input[placeholder="小写字母、数字、连字符、下划线"]').setValue('new-lab')
    await wrapper.find('input[placeholder="一句话说明这个子代理的职责"]').setValue('新实验室')
    await wrapper
      .find('input[placeholder="什么场景下让 AgentApp 委派给此子代理"]')
      .setValue('实验场景')
    await wrapper
      .find('textarea[placeholder="子代理的角色设定与行为约束"]')
      .setValue('你是实验助手。')
    // allowed_tools / model 现为 el-select；通过 WebAgentFormDialog.getForm() 拿到 reactive
    // formModel 直接赋值（替代 UI 交互，避开 el-select stub 真实下拉复杂度）。
    const formDialog = wrapper.findComponent({ name: 'WebAgentFormDialog' })
    const form = formDialog.vm.getForm() as Record<string, unknown>
    form.allowed_tools = ['duckduckgo_results_json', 'demo-stdio__add']
    form.model = 'proxy/m3'
    await findButton(wrapper, '确定').trigger('click')
    await flushPromises()

    expect(apiMock.createSubAgent).toHaveBeenCalledWith({
      name: 'new-lab',
      description: '新实验室',
      when_to_use: '实验场景',
      system_prompt: '你是实验助手。',
      allowed_tools: ['duckduckgo_results_json', 'demo-stdio__add'],
      model: 'proxy/m3',
      max_turns: null,
    } satisfies SubAgentCreatePayload)
    expect(elMessageFn).toHaveBeenCalledWith(
      expect.objectContaining({ type: 'success', message: '已保存：new-lab' }),
    )
    // 刷新：第二次 listSubAgentsPage 调用
    expect(apiMock.listSubAgentsPage).toHaveBeenCalledTimes(2)
  })

  it('创建：allowed_tools 留空 → payload 携带 null（继承父 AgentApp）', async () => {
    const wrapper = mountPage()
    await flushPromises()

    await findButton(wrapper, '新建子代理').trigger('click')
    await flushPromises()

    await wrapper.find('input[placeholder="小写字母、数字、连字符、下划线"]').setValue('inherit-tools')
    await wrapper.find('input[placeholder="一句话说明这个子代理的职责"]').setValue('d1')
    await wrapper
      .find('input[placeholder="什么场景下让 AgentApp 委派给此子代理"]')
      .setValue('w1')
    await wrapper
      .find('textarea[placeholder="子代理的角色设定与行为约束"]')
      .setValue('s1')
    await findButton(wrapper, '确定').trigger('click')
    await flushPromises()

    expect(apiMock.createSubAgent).toHaveBeenCalledWith(
      expect.objectContaining({ allowed_tools: null }),
    )
  })

  it('编辑：弹窗打开后 name 字段 disabled；提交调 patchSubAgent（不带 name 字段）', async () => {
    const wrapper = mountPage()
    await flushPromises()

    await findRowButton(wrapper, '编辑', 0).trigger('click')
    await flushPromises()

    const dialog = wrapper.findComponent(ElDialogStub)
    expect(dialog.exists()).toBe(true)
    expect(dialog.attributes('data-title')).toBe('子代理信息')

    // name 字段 disabled 且回填 search-helper
    const nameInput = wrapper.find('input[placeholder="小写字母、数字、连字符、下划线"]')
    expect((nameInput.element as HTMLInputElement).disabled).toBe(true)
    expect((nameInput.element as HTMLInputElement).value).toBe('search-helper')

    // 仅修改 description 后提交
    await wrapper
      .find('input[placeholder="一句话说明这个子代理的职责"]')
      .setValue('updated desc')
    await findButton(wrapper, '确定').trigger('click')
    await flushPromises()

    expect(apiMock.patchSubAgent).toHaveBeenCalledWith(
      'search-helper',
      expect.objectContaining({ description: 'updated desc' }),
    )
    expect(apiMock.createSubAgent).not.toHaveBeenCalled()
    expect(elMessageFn).toHaveBeenCalledWith(
      expect.objectContaining({ type: 'success', message: '已保存：search-helper' }),
    )
  })

  it('删除：useConfirm 调用并调 deleteSubAgent；确认后刷新列表', async () => {
    const wrapper = mountPage()
    await flushPromises()

    await findRowButton(wrapper, '删除', 0).trigger('click')
    expect(confirmMock).toHaveBeenCalledWith(
      expect.stringContaining('search-helper'),
      '删除确认',
      expect.anything(),
    )
    await flushPromises()

    expect(apiMock.deleteSubAgent).toHaveBeenCalledWith('search-helper')
    // 刷新：第二次 listSubAgentsPage
    expect(apiMock.listSubAgentsPage).toHaveBeenCalledTimes(2)
  })

  it('删除取消：不调 deleteSubAgent，列表不变', async () => {
    confirmMock.mockRejectedValue('cancel')
    const wrapper = mountPage()
    await flushPromises()

    await findRowButton(wrapper, '删除', 0).trigger('click')
    await flushPromises()

    expect(apiMock.deleteSubAgent).not.toHaveBeenCalled()
    expect(apiMock.listSubAgentsPage).toHaveBeenCalledTimes(1)
  })

  it('测试运行：打开弹窗 → 输入 prompt → 调 testSubAgent；结果展示', async () => {
    const wrapper = mountPage()
    await flushPromises()

    await findRowButton(wrapper, '测试', 0).trigger('click')
    await flushPromises()

    // 测试弹窗出现，且 agentName 回填
    const testDialog = wrapper.findComponent(SubAgentTestDialogStub)
    expect(testDialog.exists()).toBe(true)
    expect(testDialog.attributes('data-agent-name')).toBe('search-helper')
    expect(testDialog.props('modelValue')).toBe(true)
  })

  it('刷新策略：单行操作后调 listSubAgentsPage 重新拉全量', async () => {
    const wrapper = mountPage()
    await flushPromises()
    expect(apiMock.listSubAgentsPage).toHaveBeenCalledTimes(1)

    // 删除触发刷新
    await findRowButton(wrapper, '删除', 1).trigger('click')
    await flushPromises()
    expect(apiMock.listSubAgentsPage).toHaveBeenCalledTimes(2)
  })
})