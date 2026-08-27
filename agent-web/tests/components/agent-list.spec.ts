// @vitest-environment happy-dom
/**
 * AgentList 视图测试（AgentApp 前端管理页 / agent 引擎类型）：
 * - stub Element Plus 组件（不做真实渲染），挂载真实
 *   WebAgentTable + WebAgentFormDialog；
 * - mock `@/api/agentapps` 全部端点 + 表单选项来源四模块（零网络）；
 * - 覆盖：挂载渲染 / 创建（含空选择 → null、[] 语义）/ 编辑回填与
 *   已发布回退提示 / 发布成功与失败 / 删除确认与取消 / 刷新策略。
 */
import { beforeEach, describe, expect, it, vi } from 'vitest'
import { defineComponent, h, inject, provide } from 'vue'
import { flushPromises, mount } from '@vue/test-utils'
import type { VueWrapper } from '@vue/test-utils'

import AgentList from '@/views/agent/AgentList.vue'
import type {
  AgentAppCreatePayload,
  AgentAppPatchPayload,
  AgentAppRow,
} from '@/api/agentapps'
import type { PageResult } from '@/types'
import type { SkillRow } from '@/api/assets'
import type { SubAgentRow } from '@/api/subagents'

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
 * 3 行 mock（覆盖 published / draft；allowed_tools null / 非空；
 * model null / 自定义；绑定数 0 / N，验证列展示分支）
 */
const ROWS: AgentAppRow[] = [
  {
    id: 1,
    name: 'customer-support',
    system_prompt: '你是客服助手。',
    allowed_tools: ['duckduckgo_results_json'],
    model: 'default/default',
    skill_names: ['pdf-export'],
    subagent_names: ['search-helper'],
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
    name: 'data-runner',
    system_prompt: '你负责数据处理。',
    allowed_tools: ['demo-stdio__add'],
    model: 'proxy/m3',
    skill_names: ['pdf-export', 'csv-clean'],
    subagent_names: [],
    interrupt_on: {},
    engine: 'deepagents',
    status: 'published',
    published_hash: 'ph-3',
    agent_dir: 'agents/data-runner',
    workspace_hash: 'wh-3',
    agent_workspace_status: 'ready',
    version: 3,
    created_by: 'seed',
  },
]

/** 全局 skill 资产（表单「关联技能」下拉选项来源） */
const SKILL_ROWS: SkillRow[] = [
  { name: 'pdf-export', description: '导出 PDF', content_hash: 'spdf', version: 1, created_by: 'seed' },
  { name: 'csv-clean', description: '清洗 CSV', content_hash: 'scsv', version: 1, created_by: 'seed' },
]

/** 子代理配置（表单「关联子代理」下拉选项来源） */
const SUBAGENT_ROWS: SubAgentRow[] = [
  {
    name: 'search-helper',
    description: '搜索助手',
    when_to_use: '需要实时搜索时',
    system_prompt: '你是搜索助手。',
    allowed_tools: null,
    model: null,
    max_turns: null,
    skill_names: null,
    content_hash: 'sh',
    version: 1,
    created_by: 'seed',
  },
]

const { apiMock } = vi.hoisted(() => {
  const mock: Record<string, ReturnType<typeof vi.fn>> = {
    listAgentApps: vi.fn(),
    listAgentAppsPage: vi.fn(),
    listPublishedAgentApps: vi.fn(),
    getAgentApp: vi.fn(),
    createAgentApp: vi.fn(),
    patchAgentApp: vi.fn(),
    deleteAgentApp: vi.fn(),
    publishAgentApp: vi.fn(),
    associateAppUser: vi.fn(),
    disassociateAppUser: vi.fn(),
  }
  return { apiMock: mock }
})

vi.mock('@/api/agentapps', () => apiMock)

/** mcp.ts 的 listToolCatalog mock：builtin + mcp 各 2 条（分组渲染路径） */
const { mcpMock } = vi.hoisted(() => ({
  mcpMock: { listToolCatalog: vi.fn() },
}))
vi.mock('@/api/mcp', async (importOriginal) => {
  const actual = await importOriginal<typeof import('@/api/mcp')>()
  return { ...actual, listToolCatalog: mcpMock.listToolCatalog }
})

/** provider.ts 的 listAllProviderModels mock：固定聚合结果，零真实后端 */
const { providerMock } = vi.hoisted(() => ({
  providerMock: { listAllProviderModels: vi.fn() },
}))
vi.mock('@/api/provider', async (importOriginal) => {
  const actual = await importOriginal<typeof import('@/api/provider')>()
  return { ...actual, listAllProviderModels: providerMock.listAllProviderModels }
})

/** assets.ts 的 listSkills mock（仅 AgentList 用到的一项） */
const { assetsMock } = vi.hoisted(() => ({
  assetsMock: { listSkills: vi.fn() },
}))
vi.mock('@/api/assets', async (importOriginal) => {
  const actual = await importOriginal<typeof import('@/api/assets')>()
  return { ...actual, listSkills: assetsMock.listSkills }
})

/** subagents.ts 的 listSubAgents mock（仅 AgentList 用到的一项） */
const { subagentsMock } = vi.hoisted(() => ({
  subagentsMock: { listSubAgents: vi.fn() },
}))
vi.mock('@/api/subagents', async (importOriginal) => {
  const actual = await importOriginal<typeof import('@/api/subagents')>()
  return { ...actual, listSubAgents: subagentsMock.listSubAgents }
})

const ROWS_KEY = Symbol('agent-table-rows')

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

const ElEmptyStub = defineComponent({
  name: 'ElEmpty',
  setup(_, { slots }) {
    return () => h('div', { class: 'el-empty-stub' }, slots.default?.())
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

/**
 * el-select stub：仅展示当前 modelValue（数组→CSV / 字符串→原文），保留 slot
 * 渲染 el-option / el-option-group。测试通过 vm.getForm() 拿到 reactive
 * formModel 后直接赋值，再点击「确定」按钮触发提交。
 */
const ElSelectStub = defineComponent({
  name: 'ElSelect',
  props: {
    modelValue: {
      type: [String, Number, Boolean, Array, Object] as unknown as () => unknown,
      default: undefined,
    },
    multiple: { type: Boolean, default: false },
    placeholder: String,
    loading: { type: Boolean, default: false },
  },
  emits: ['update:modelValue'],
  setup(props, { slots }) {
    return () => {
      const display = Array.isArray(props.modelValue)
        ? (props.modelValue as unknown[]).map((v) => String(v ?? '')).join(',')
        : props.modelValue != null
          ? String(props.modelValue)
          : ''
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
  props: {
    value: { type: [String, Number, Boolean, Object, Array] as unknown as () => unknown, default: undefined },
    label: { type: [String, Number] as unknown as () => string | number, default: undefined },
    disabled: { type: Boolean, default: false },
  },
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
  return mount(AgentList, {
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
        ElOptionGroup: ElOptionGroupStub,
        ElTag: ElTagStub,
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

/** 行数据拷贝（保持 mutation 隔离） */
function rowsCopy(): AgentAppRow[] {
  return ROWS.map((row) => ({
    ...row,
    allowed_tools: row.allowed_tools ? [...row.allowed_tools] : row.allowed_tools,
    skill_names: [...row.skill_names],
    subagent_names: [...row.subagent_names],
  }))
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

  // 默认 listAgentAppsPage 返回 3 行（拷贝）
  apiMock.listAgentAppsPage.mockImplementation(
    async () =>
      ({
        items: rowsCopy(),
        total: ROWS.length,
        page: 1,
        pageSize: 10,
      }) satisfies PageResult<AgentAppRow>,
  )
  apiMock.createAgentApp.mockImplementation(
    async (payload: AgentAppCreatePayload) =>
      ({
        id: 99,
        name: payload.name,
        system_prompt: payload.system_prompt,
        allowed_tools: payload.allowed_tools ?? null,
        model: payload.model ?? null,
        skill_names: payload.skill_names ?? [],
        subagent_names: payload.subagent_names ?? [],
        interrupt_on: {},
        engine: 'deepagents',
        status: 'draft',
        published_hash: null,
        agent_dir: null,
        workspace_hash: null,
        agent_workspace_status: 'pending',
        version: 1,
        created_by: 'user',
      }) satisfies AgentAppRow,
  )
  apiMock.patchAgentApp.mockImplementation(
    async (appId: number, payload: AgentAppPatchPayload) => {
      const row = ROWS.find((r) => r.id === appId)
      return {
        ...(row ?? ROWS[0]),
        ...payload,
        id: appId,
        version: (row?.version ?? 0) + 1,
      } as AgentAppRow
    },
  )
  apiMock.deleteAgentApp.mockResolvedValue(null)
  apiMock.publishAgentApp.mockImplementation(async (appId: number) => {
    const row = ROWS.find((r) => r.id === appId)
    return { ...(row ?? ROWS[0]), id: appId, status: 'published' } as AgentAppRow
  })
  // 工具目录：2 builtin + 2 mcp（覆盖 el-option-group 分组渲染路径）。
  // mcp 条目 name 与真实后端一致：已是 `${server}__${tool}` 命名空间名。
  mcpMock.listToolCatalog.mockResolvedValue([
    { name: 'duckduckgo_results_json', source: 'builtin', server: null },
    { name: 'echo', source: 'builtin', server: null },
    { name: 'demo-stdio__add', source: 'mcp', server: 'demo-stdio' },
    { name: 'demo-stdio__greet', source: 'mcp', server: 'demo-stdio' },
  ])
  providerMock.listAllProviderModels.mockResolvedValue([
    { id: 1, provider_name: 'default', name: 'default', model_id: 'default', ref: 'default/default', context_size: null, extra_params: {}, enabled: true, created_by: null, created_at: null, updated_at: null },
    { id: 2, provider_name: 'proxy', name: 'm3', model_id: 'm3', ref: 'proxy/m3', context_size: null, extra_params: {}, enabled: true, created_by: null, created_at: null, updated_at: null },
  ])
  assetsMock.listSkills.mockResolvedValue(SKILL_ROWS.map((row) => ({ ...row })))
  subagentsMock.listSubAgents.mockResolvedValue(SUBAGENT_ROWS.map((row) => ({ ...row })))
})

describe('AgentList Agent 管理页（AgentApp agent 引擎类型 CRUD + 发布）', () => {
  it('挂载调 listAgentAppsPage 并渲染 3 行 × 7 列结构', async () => {
    const wrapper = mountPage()
    await flushPromises()

    expect(apiMock.listAgentAppsPage).toHaveBeenCalledTimes(1)

    const data = wrapper.findComponent(ElTableStub).props('data') as unknown[]
    expect(data).toHaveLength(3)

    // 3 个名称全部渲染
    expect(wrapper.text()).toContain('customer-support')
    expect(wrapper.text()).toContain('code-helper')
    expect(wrapper.text()).toContain('data-runner')

    // 7 列：名称 / 系统提示 / 状态 / 模型 / 技能与子代理 / 版本 / 操作
    expect(wrapper.findAll('.el-table-column-stub')).toHaveLength(7)

    // 状态列：2 已发布 + 1 草稿
    expect(wrapper.text()).toContain('已发布')
    expect(wrapper.text()).toContain('草稿')
    // 绑定列：「1 技能 · 1 子代理」
    expect(wrapper.text()).toContain('1 技能 · 1 子代理')
  })

  it('创建：调 createAgentApp 携带完整 payload；成功后弹窗关闭 + 刷新', async () => {
    const wrapper = mountPage()
    await flushPromises()

    await findButton(wrapper, '新建 Agent').trigger('click')
    await flushPromises()

    const dialog = wrapper.findComponent(ElDialogStub)
    expect(dialog.exists()).toBe(true)
    expect(dialog.attributes('data-title')).toBe('新建 Agent')

    await wrapper.find('input[placeholder="小写字母、数字、连字符、下划线"]').setValue('new-app')
    await wrapper.find('textarea[placeholder="Agent 的角色设定与行为约束"]').setValue('你是新应用。')
    // el-select 字段通过 WebAgentFormDialog.getForm() 拿 reactive formModel 直接赋值
    const formDialog = wrapper.findComponent({ name: 'WebAgentFormDialog' })
    const form = formDialog.vm.getForm() as Record<string, unknown>
    form.allowed_tools = ['duckduckgo_results_json', 'demo-stdio__add']
    form.model = 'proxy/m3'
    form.skill_names = ['pdf-export']
    form.subagent_names = ['search-helper']
    await findButton(wrapper, '确定').trigger('click')
    await flushPromises()

    expect(apiMock.createAgentApp).toHaveBeenCalledWith({
      name: 'new-app',
      system_prompt: '你是新应用。',
      allowed_tools: ['duckduckgo_results_json', 'demo-stdio__add'],
      model: 'proxy/m3',
      skill_names: ['pdf-export'],
      subagent_names: ['search-helper'],
    } satisfies AgentAppCreatePayload)
    expect(elMessageFn).toHaveBeenCalledWith(
      expect.objectContaining({ type: 'success', message: '已保存：new-app' }),
    )
    // 刷新：第二次 listAgentAppsPage 调用
    expect(apiMock.listAgentAppsPage).toHaveBeenCalledTimes(2)
  })

  it('创建：选择器全留空 → allowed_tools/model 为 null，绑定列表为空数组', async () => {
    const wrapper = mountPage()
    await flushPromises()

    await findButton(wrapper, '新建 Agent').trigger('click')
    await flushPromises()

    await wrapper.find('input[placeholder="小写字母、数字、连字符、下划线"]').setValue('bare-app')
    await wrapper.find('textarea[placeholder="Agent 的角色设定与行为约束"]').setValue('最小配置。')
    await findButton(wrapper, '确定').trigger('click')
    await flushPromises()

    expect(apiMock.createAgentApp).toHaveBeenCalledWith({
      name: 'bare-app',
      system_prompt: '最小配置。',
      allowed_tools: null,
      model: null,
      skill_names: [],
      subagent_names: [],
    } satisfies AgentAppCreatePayload)
  })

  it('编辑：弹窗回填 + name disabled；已发布应用显示回退提示', async () => {
    const wrapper = mountPage()
    await flushPromises()

    await findRowButton(wrapper, '编辑', 0).trigger('click')
    await flushPromises()

    const dialog = wrapper.findComponent(ElDialogStub)
    expect(dialog.exists()).toBe(true)
    expect(dialog.attributes('data-title')).toBe('编辑 Agent')

    // name 字段 disabled 且回填 customer-support
    const nameInput = wrapper.find('input[placeholder="小写字母、数字、连字符、下划线"]')
    expect((nameInput.element as HTMLInputElement).disabled).toBe(true)
    expect((nameInput.element as HTMLInputElement).value).toBe('customer-support')

    // 系统提示回填
    const promptInput = wrapper.find('textarea[placeholder="Agent 的角色设定与行为约束"]')
    expect((promptInput.element as HTMLTextAreaElement).value).toBe('你是客服助手。')

    // 已发布应用编辑回退提示
    expect(wrapper.text()).toContain('保存后将回退为草稿')

    // 表单回填绑定数组
    const formDialog = wrapper.findComponent({ name: 'WebAgentFormDialog' })
    const form = formDialog.vm.getForm() as Record<string, unknown>
    expect(form.skill_names).toEqual(['pdf-export'])
    expect(form.subagent_names).toEqual(['search-helper'])
    expect(form.allowed_tools).toEqual(['duckduckgo_results_json'])
    expect(form.model).toBe('default/default')
  })

  it('编辑：提交调 patchAgentApp（id + 全字段），不触发创建', async () => {
    const wrapper = mountPage()
    await flushPromises()

    await findRowButton(wrapper, '编辑', 1).trigger('click')
    await flushPromises()

    // draft 应用编辑不显示回退提示
    expect(wrapper.text()).not.toContain('保存后将回退为草稿')

    await wrapper
      .find('textarea[placeholder="Agent 的角色设定与行为约束"]')
      .setValue('你是更新后的代码助手。')
    await findButton(wrapper, '确定').trigger('click')
    await flushPromises()

    expect(apiMock.patchAgentApp).toHaveBeenCalledWith(
      2,
      expect.objectContaining({ system_prompt: '你是更新后的代码助手。' }),
    )
    expect(apiMock.createAgentApp).not.toHaveBeenCalled()
    expect(elMessageFn).toHaveBeenCalledWith(
      expect.objectContaining({ type: 'success', message: '已保存：code-helper' }),
    )
  })

  it('编辑：清空绑定列表 → patch 携带空数组（后端禁显式 null，空数组即清空）', async () => {
    const wrapper = mountPage()
    await flushPromises()

    // 编辑 data-runner（skill_names=['pdf-export', 'csv-clean']）
    await findRowButton(wrapper, '编辑', 2).trigger('click')
    await flushPromises()

    const formDialog = wrapper.findComponent({ name: 'WebAgentFormDialog' })
    const form = formDialog.vm.getForm() as Record<string, unknown>
    form.skill_names = []
    await findButton(wrapper, '确定').trigger('click')
    await flushPromises()

    expect(apiMock.patchAgentApp).toHaveBeenCalledWith(
      3,
      expect.objectContaining({ skill_names: [] }),
    )
  })

  it('发布：调 publishAgentApp；成功后通知 + 刷新', async () => {
    const wrapper = mountPage()
    await flushPromises()

    await findRowButton(wrapper, '发布', 1).trigger('click')
    await flushPromises()

    expect(apiMock.publishAgentApp).toHaveBeenCalledWith(2)
    expect(elMessageFn).toHaveBeenCalledWith(
      expect.objectContaining({ type: 'success', message: '已发布：code-helper' }),
    )
    expect(apiMock.listAgentAppsPage).toHaveBeenCalledTimes(2)
  })

  it('发布失败：不通知、不刷新（错误提示由全局拦截器弹）', async () => {
    apiMock.publishAgentApp.mockRejectedValue(new Error('422 tool not in catalog'))
    const wrapper = mountPage()
    await flushPromises()

    await findRowButton(wrapper, '发布', 1).trigger('click')
    await flushPromises()

    expect(apiMock.publishAgentApp).toHaveBeenCalledWith(2)
    expect(elMessageFn).not.toHaveBeenCalledWith(
      expect.objectContaining({ type: 'success' }),
    )
    expect(apiMock.listAgentAppsPage).toHaveBeenCalledTimes(1)
  })

  it('删除：useConfirm 调用并调 deleteAgentApp；确认后刷新列表', async () => {
    const wrapper = mountPage()
    await flushPromises()

    await findRowButton(wrapper, '删除', 1).trigger('click')
    expect(confirmMock).toHaveBeenCalledWith(
      expect.stringContaining('code-helper'),
      '删除确认',
      expect.anything(),
    )
    await flushPromises()

    expect(apiMock.deleteAgentApp).toHaveBeenCalledWith(2)
    // 刷新：第二次 listAgentAppsPage
    expect(apiMock.listAgentAppsPage).toHaveBeenCalledTimes(2)
  })

  it('删除取消：不调 deleteAgentApp，列表不变', async () => {
    confirmMock.mockRejectedValue('cancel')
    const wrapper = mountPage()
    await flushPromises()

    await findRowButton(wrapper, '删除', 0).trigger('click')
    await flushPromises()

    expect(apiMock.deleteAgentApp).not.toHaveBeenCalled()
    expect(apiMock.listAgentAppsPage).toHaveBeenCalledTimes(1)
  })

  it('工具下拉 value：mcp 条目直接用命名空间名，不得二次拼接前缀', async () => {
    const wrapper = mountPage()
    await flushPromises()

    await findButton(wrapper, '新建 Agent').trigger('click')
    await flushPromises()

    const optionValues = wrapper
      .findAll('.el-option-stub')
      .map((option) => option.attributes('data-value'))
    expect(optionValues).toContain('demo-stdio__add')
    expect(optionValues).toContain('demo-stdio__greet')
    expect(optionValues.join('\n')).not.toContain('demo-stdio__demo-stdio__')
  })
})
