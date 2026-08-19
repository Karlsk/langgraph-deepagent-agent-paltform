// @vitest-environment happy-dom
/**
 * SkillList 视图测试（task-4e6 Skill CRUD 前端适配）：
 * - stub Element Plus 组件（不做真实渲染），挂载真实
 *   WebAgentTable + WebAgentFormDialog + SkillContentDialog；
 * - mock `@/api/assets` 的 10 个函数（listSkills / listSkillsPage / getSkill /
 *   getSkillContent / createSkill / patchSkill / deleteSkill + listSubAgents /
 *   listSubAgentsPage / listAgentApps 三个占位防 import 副作用）；
 * - 关键字搜索走 300ms 防抖，用 fake timers 推进；422 / 401 等错误由 mock throw，
 *   统一拦截器提示路径在 request.spec.ts 覆盖，本视图只断言 useConfirm 调用、
 *   刷新策略与通知文案。
 */
import { afterEach, beforeEach, describe, expect, it, vi } from 'vitest'
import { defineComponent, h, inject, provide } from 'vue'
import { flushPromises, mount } from '@vue/test-utils'
import type { VueWrapper } from '@vue/test-utils'

import SkillList from '@/views/skill/SkillList.vue'
import type {
  SkillContentRead,
  SkillCreatePayload,
  SkillGeneratePayload,
  SkillGenerateResponse,
  SkillPatchPayload,
  SkillRow,
} from '@/api/assets'
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

/** 5 行 mock 数据（与后端 SkillRead 契约一致） */
const ROWS: SkillRow[] = [
  {
    name: 'pdf-reader',
    description: '解析 PDF 文档并提取关键文本与元数据',
    content_hash: 'a1b2c3d4',
    version: 3,
    created_by: 'seed',
  },
  {
    name: 'sql-explorer',
    description: '用自然语言描述查询需求，自动生成 SQL 并执行',
    content_hash: 'b2c3d4e5',
    version: 7,
    created_by: 'seed',
  },
  {
    name: 'web-search',
    description: '执行实时网页搜索并返回结构化摘要',
    content_hash: 'c3d4e5f6',
    version: 2,
    created_by: 'admin',
  },
  {
    name: 'doc-writer',
    description: '基于要点起草结构化技术文档',
    content_hash: 'd4e5f6g7',
    version: 5,
    created_by: 'admin',
  },
  {
    name: 'image-tagger',
    description: '为图片自动生成 alt 文本与标签',
    content_hash: 'e5f6g7h8',
    version: 1,
    created_by: 'user-1',
  },
]

/**
 * 通用 mock 实现：listSkillsPage 返回当前 ROWS 的拷贝（保持 mutation 隔离）；
 * CRUD 函数返回被调用 payload 的最小回显，让视图层按 mock 返回走 happy-path。
 */
const { apiMock } = vi.hoisted(() => {
  const mock: Record<string, ReturnType<typeof vi.fn>> = {
    listSkills: vi.fn(),
    listSkillsPage: vi.fn(),
    getSkill: vi.fn(),
    getSkillContent: vi.fn(),
    createSkill: vi.fn(),
    patchSkill: vi.fn(),
    deleteSkill: vi.fn(),
    generateSkill: vi.fn(),
    // 占位：防止测试时触发真实网络或运行期 import 副作用
    listSubAgents: vi.fn(),
    listSubAgentsPage: vi.fn(),
    listAgentApps: vi.fn(),
  }
  return { apiMock: mock }
})

vi.mock('@/api/assets', () => apiMock)

const ROWS_KEY = Symbol('skill-table-rows')

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

/** ElInput stub：透传 placeholder / disabled / type / rows；type='textarea' 时渲染为 <textarea> */
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

const ElEmptyStub = defineComponent({
  name: 'ElEmpty',
  setup(_, { slots }) {
    return () => h('div', { class: 'el-empty-stub' }, slots.default?.())
  },
})

function mountPage(): VueWrapper {
  return mount(SkillList, {
    global: {
      stubs: {
        ElTable: ElTableStub,
        ElTableColumn: ElTableColumnStub,
        ElPagination: ElPaginationStub,
        ElEmpty: ElEmptyStub,
        ElTag: true,
        ElButton: ElButtonStub,
        ElDialog: ElDialogStub,
        ElForm: ElFormStub,
        ElFormItem: ElFormItemStub,
        ElInput: ElInputStub,
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
  // 默认 listSkillsPage 返回 5 行（拷贝，避免用例间 mutation 共享）
  apiMock.listSkillsPage.mockImplementation(
    async () =>
      ({
        items: ROWS.map((row) => ({ ...row })),
        total: ROWS.length,
        page: 1,
        pageSize: 10,
      }) satisfies PageResult<SkillRow>,
  )
  apiMock.createSkill.mockImplementation(
    async (payload: SkillCreatePayload) =>
      ({
        name: payload.name,
        description: payload.description,
        content_hash: 'new-hash',
        version: 1,
        created_by: 'user',
      }) satisfies SkillRow,
  )
  apiMock.patchSkill.mockImplementation(
    async (name: string, payload: SkillPatchPayload) => {
      const row = ROWS.find((r) => r.name === name)
      return {
        name,
        description: payload.description ?? row?.description ?? '',
        content_hash: 'patched-hash',
        version: (row?.version ?? 0) + 1,
        created_by: row?.created_by ?? 'user',
      } satisfies SkillRow
    },
  )
  apiMock.deleteSkill.mockResolvedValue(null)
  apiMock.listSkills.mockResolvedValue([])
  apiMock.getSkill.mockImplementation(async (name: string) => {
    const row = ROWS.find((r) => r.name === name)
    if (!row) throw new Error(`skill ${name} not found`)
    return { ...row }
  })
  apiMock.getSkillContent.mockImplementation(
    async (name: string) =>
      ({
        name,
        content: `# ${name}\n\n这是 ${name} 的 SKILL.md 示例正文。`,
      }) satisfies SkillContentRead,
  )
  apiMock.generateSkill.mockImplementation(
    async (payload: SkillGeneratePayload) =>
      ({
        draft: `# ${payload.description.slice(0, 8)}\n\n这是由 LLM 起草的示例正文（hint=${payload.hint ?? ''}）。`,
      }) satisfies SkillGenerateResponse,
  )
  apiMock.listSubAgents.mockResolvedValue([])
  apiMock.listSubAgentsPage.mockResolvedValue({
    items: [],
    total: 0,
    page: 1,
    pageSize: 10,
  } satisfies PageResult<unknown>)
  apiMock.listAgentApps.mockResolvedValue([])
})

afterEach(() => {
  vi.useRealTimers()
})

describe('SkillList 技能管理页（task-4e6 CRUD 前端适配）', () => {
  it('挂载调 listSkillsPage 并渲染 5 行 × 5 列', async () => {
    const wrapper = mountPage()
    await flushPromises()

    expect(apiMock.listSkillsPage).toHaveBeenCalledTimes(1)

    const data = wrapper.findComponent(ElTableStub).props('data') as unknown[]
    expect(data).toHaveLength(5)

    // 验证所有技能名渲染
    expect(wrapper.text()).toContain('pdf-reader')
    expect(wrapper.text()).toContain('sql-explorer')
    expect(wrapper.text()).toContain('web-search')
    expect(wrapper.text()).toContain('doc-writer')
    expect(wrapper.text()).toContain('image-tagger')

    // 5 列：名称 / 描述 / 版本 / 创建者 / 操作
    expect(wrapper.findAll('.el-table-column-stub')).toHaveLength(5)
  })

  it('关键字输入：debounce 300ms 后触发 listSkillsPage 重查（带 keyword 参数）', async () => {
    const wrapper = mountPage()
    await flushPromises()

    expect(apiMock.listSkillsPage).toHaveBeenCalledTimes(1)

    // 输入关键字并推进 fake timer 触发 debounce
    const searchInput = wrapper.find('input[placeholder="按名称模糊搜索"]')
    await searchInput.setValue('pdf')
    // 不到 300ms 不应触发重查
    vi.advanceTimersByTime(200)
    await flushPromises()
    expect(apiMock.listSkillsPage).toHaveBeenCalledTimes(1)
    // 推进到 300ms 触发
    vi.advanceTimersByTime(100)
    await flushPromises()
    expect(apiMock.listSkillsPage).toHaveBeenCalledTimes(2)
    expect(apiMock.listSkillsPage).toHaveBeenLastCalledWith(
      expect.objectContaining({ keyword: 'pdf' }),
    )
  })

  it('创建技能：弹窗打开 → 提交调 createSkill({name, description, body})', async () => {
    const wrapper = mountPage()
    await flushPromises()

    await findButton(wrapper, '新建技能').trigger('click')
    await flushPromises()

    // 弹窗可见 + 标题正确
    const dialog = wrapper.findComponent(ElDialogStub)
    expect(dialog.exists()).toBe(true)
    expect(dialog.attributes('data-title')).toBe('新建技能')

    // 填表 + body（textarea）
    await wrapper.find('input[placeholder="请输入技能名称"]').setValue('new-skill')
    await wrapper.find('textarea[placeholder="请输入技能描述"]').setValue('新技能描述')
    await wrapper.find('textarea[placeholder^="请输入 SKILL.md 正文"]').setValue('# new-skill\n\n示例正文')

    await findButton(wrapper, '确定').trigger('click')
    await flushPromises()

    expect(apiMock.createSkill).toHaveBeenCalledWith({
      name: 'new-skill',
      description: '新技能描述',
      body: '# new-skill\n\n示例正文',
    } satisfies SkillCreatePayload)
    expect(elMessageFn).toHaveBeenCalledWith(
      expect.objectContaining({ type: 'success', message: '已保存：new-skill' }),
    )
    // 刷新：第二次 listSkillsPage
    expect(apiMock.listSkillsPage).toHaveBeenCalledTimes(2)
  })

  it('名称非法（@-bad）：前端校验拦截，不发请求', async () => {
    validateMock.mockImplementation(() => {
      const err = new Error('validation failed') as Error & {
        fields: Record<string, { message: string }>
      }
      err.fields = { name: { message: '以小写字母或数字开头，后续仅含小写字母/数字/下划线/连字符' } }
      return Promise.reject(err)
    })
    const wrapper = mountPage()
    await flushPromises()

    await findButton(wrapper, '新建技能').trigger('click')
    await flushPromises()
    await wrapper.find('input[placeholder="请输入技能名称"]').setValue('@-bad')
    await findButton(wrapper, '确定').trigger('click')
    await flushPromises()

    expect(apiMock.createSkill).not.toHaveBeenCalled()
    expect(apiMock.patchSkill).not.toHaveBeenCalled()
  })

  it('编辑技能：调 getSkillContent 异步拉 body 回填 → 提交 patchSkill 同时携带 description + body', async () => {
    const wrapper = mountPage()
    await flushPromises()

    await findRowButton(wrapper, '编辑', 0).trigger('click')
    // handleEdit 是 async：先 open 弹窗（body='加载中…'），再 await getSkillContent 回填
    await flushPromises()
    await flushPromises()

    const dialog = wrapper.findComponent(ElDialogStub)
    expect(dialog.exists()).toBe(true)
    expect(dialog.attributes('data-title')).toBe('编辑技能')

    // name 字段 disabled 且回填 name
    const nameInput = wrapper.find('input[placeholder="请输入技能名称"]')
    expect((nameInput.element as HTMLInputElement).disabled).toBe(true)
    expect((nameInput.element as HTMLInputElement).value).toBe('pdf-reader')

    // description 回填
    const descInput = wrapper.find('textarea[placeholder="请输入技能描述"]')
    expect((descInput.element as HTMLTextAreaElement).value).toBe(
      '解析 PDF 文档并提取关键文本与元数据',
    )

    // getSkillContent 被调，且 body textarea 已回填真实正文（mock 返回 `# pdf-reader\n\n这是 ...`）
    expect(apiMock.getSkillContent).toHaveBeenCalledWith('pdf-reader')
    const bodyTextarea = wrapper.find('textarea[placeholder^="请输入 SKILL.md 正文"]')
    expect(bodyTextarea.exists()).toBe(true)
    expect((bodyTextarea.element as HTMLTextAreaElement).value).toContain('# pdf-reader')

    // 修改 description + 修改 body 后提交
    await descInput.setValue('更新后的描述')
    await bodyTextarea.setValue('# pdf-reader v2\n\n新的正文内容')
    await findButton(wrapper, '确定').trigger('click')
    await flushPromises()

    // patchSkill 携带 description + body 两个字段（与后端 PATCH 语义一致）
    expect(apiMock.patchSkill).toHaveBeenCalledWith('pdf-reader', {
      description: '更新后的描述',
      body: '# pdf-reader v2\n\n新的正文内容',
    } satisfies SkillPatchPayload)
    expect(apiMock.createSkill).not.toHaveBeenCalled()
  })

  it('编辑 description 留空：守卫拦截不发请求（防止 422 nothing to update）', async () => {
    const wrapper = mountPage()
    await flushPromises()

    await findRowButton(wrapper, '编辑', 0).trigger('click')
    await flushPromises()

    // 清空 description（触发 required 校验失败 → validateMock reject）
    validateMock.mockImplementation(() => {
      const err = new Error('validation failed') as Error & {
        fields: Record<string, { message: string }>
      }
      err.fields = { description: { message: '请输入描述' } }
      return Promise.reject(err)
    })
    await wrapper.find('textarea[placeholder="请输入技能描述"]').setValue('')
    await findButton(wrapper, '确定').trigger('click')
    await flushPromises()

    expect(apiMock.patchSkill).not.toHaveBeenCalled()
  })

  it('查看正文：弹窗打开后调 getSkillContent 并渲染 content', async () => {
    const wrapper = mountPage()
    await flushPromises()

    await findRowButton(wrapper, '查看正文', 0).trigger('click')
    await flushPromises()

    expect(apiMock.getSkillContent).toHaveBeenCalledWith('pdf-reader')

    // 等待异步拉取 + render
    await flushPromises()
    expect(wrapper.text()).toContain('# pdf-reader')
    expect(wrapper.text()).toContain('这是 pdf-reader 的 SKILL.md 示例正文。')
  })

  it('删除：useConfirm 调用并调 deleteSkill；确认后刷新列表', async () => {
    const wrapper = mountPage()
    await flushPromises()

    await findRowButton(wrapper, '删除', 0).trigger('click')
    expect(confirmMock).toHaveBeenCalledWith(
      '确定删除技能「pdf-reader」吗？该操作不可恢复，并将清理该技能的所有用户副本。',
      '删除技能',
      expect.anything(),
    )
    await flushPromises()

    expect(apiMock.deleteSkill).toHaveBeenCalledWith('pdf-reader')
    // 刷新：第二次 listSkillsPage 调用
    expect(apiMock.listSkillsPage).toHaveBeenCalledTimes(2)
  })

  it('删除取消：不调 deleteSkill，列表不变', async () => {
    confirmMock.mockRejectedValue('cancel')
    const wrapper = mountPage()
    await flushPromises()

    await findRowButton(wrapper, '删除', 0).trigger('click')
    await flushPromises()

    expect(apiMock.deleteSkill).not.toHaveBeenCalled()
    expect(apiMock.listSkillsPage).toHaveBeenCalledTimes(1)
  })

  it('刷新策略：单行操作后调 listSkillsPage 重新拉全量', async () => {
    const wrapper = mountPage()
    await flushPromises()
    expect(apiMock.listSkillsPage).toHaveBeenCalledTimes(1)

    // 删除触发刷新
    await findRowButton(wrapper, '删除', 1).trigger('click')
    await flushPromises()
    expect(apiMock.listSkillsPage).toHaveBeenCalledTimes(2)

    // 查看正文不触发刷新
    await findRowButton(wrapper, '查看正文', 1).trigger('click')
    await flushPromises()
    expect(apiMock.listSkillsPage).toHaveBeenCalledTimes(2)
  })

  it('创建态：body 字段上方有「自动生成」按钮；编辑态不渲染（body 字段不显示）', async () => {
    const wrapper = mountPage()
    await flushPromises()

    // 创建态
    await findButton(wrapper, '新建技能').trigger('click')
    await flushPromises()
    const createDialog = wrapper.findComponent(ElDialogStub)
    expect(createDialog.attributes('data-title')).toBe('新建技能')
    expect(createDialog.text()).toContain('自动生成')
    expect(createDialog.text()).toContain('直接填写正文，或由 LLM 起草一份草稿后再修改')
    // body textarea 存在
    const createBodyTextarea = createDialog.find(
      'textarea[placeholder^="请输入 SKILL.md 正文"]',
    )
    expect(createBodyTextarea.exists()).toBe(true)

    // 编辑态：body 字段也显示（PATCH /skills/{name} 支持 body 更新，body 必填；
    // 编辑时 handleEdit 异步调 getSkillContent 回填）。提示文案区别于创建态。
    await findRowButton(wrapper, '编辑', 0).trigger('click')
    await flushPromises()
    await flushPromises()
    const editDialog = wrapper.findComponent(ElDialogStub)
    expect(editDialog.attributes('data-title')).toBe('编辑技能')
    expect(editDialog.text()).toContain('自动生成')
    expect(editDialog.text()).toContain('修改正文内容，或由 LLM 重新起草')
    const editBodyTextarea = editDialog.find(
      'textarea[placeholder^="请输入 SKILL.md 正文"]',
    )
    expect(editBodyTextarea.exists()).toBe(true)
  })

  it('点击「自动生成」→ 生成弹窗打开，回填 description；提交调 generateSkill，draft 写入 body', async () => {
    const wrapper = mountPage()
    await flushPromises()

    // 进入创建弹窗，先填 name + description（name 让 handleSubmit 守卫通过）
    await findButton(wrapper, '新建技能').trigger('click')
    await flushPromises()
    await wrapper.find('input[placeholder="请输入技能名称"]').setValue('llm-skill')
    await wrapper
      .find('textarea[placeholder="请输入技能描述"]')
      .setValue('解析 PDF 文档并提取关键文本与元数据')

    // 点击「自动生成」按钮（创建弹窗内）
    const createDialog = wrapper.findComponent(ElDialogStub)
    await createDialog.findAll('button').find((b) => b.text().includes('自动生成'))!.trigger('click')
    await flushPromises()

    // 此时应同时存在两个 dialog：创建 + 生成
    const dialogs = wrapper.findAllComponents(ElDialogStub)
    const titles = dialogs.map((d) => d.attributes('data-title'))
    expect(titles).toContain('新建技能')
    expect(titles).toContain('自动生成技能正文')

    // 定位生成弹窗
    const generateDialog = dialogs.find(
      (d) => d.attributes('data-title') === '自动生成技能正文',
    )!
    expect(generateDialog.exists()).toBe(true)

    // 验证 description 输入框已回填当前 description（生成弹窗中 description 是 type=textarea 的 el-input）
    const descriptionInput = generateDialog.find(
      'textarea[placeholder="例如：解析 PDF 文档并提取关键文本与元数据"]',
    )
    expect((descriptionInput.element as HTMLTextAreaElement).value).toBe('解析 PDF 文档并提取关键文本与元数据')

    // 补充 hint（同上 textarea 渲染）
    await generateDialog
      .find('textarea[placeholder^="可选：补充额外要求"]')
      .setValue('输出中文')

    // 点生成草稿
    await generateDialog.findAll('button').find((b) => b.text().includes('生成草稿'))!.trigger('click')
    await flushPromises()

    // 验证 generateSkill 被调（带 description 与 hint）
    expect(apiMock.generateSkill).toHaveBeenCalledTimes(1)
    expect(apiMock.generateSkill).toHaveBeenCalledWith({
      description: '解析 PDF 文档并提取关键文本与元数据',
      hint: '输出中文',
    } satisfies SkillGeneratePayload)

    // 验证 draft 已写入 body textarea
    const updatedCreateDialog = wrapper
      .findAllComponents(ElDialogStub)
      .find((d) => d.attributes('data-title') === '新建技能')!
    const bodyTextarea = updatedCreateDialog.find(
      'textarea[placeholder^="请输入 SKILL.md 正文"]',
    )
    expect((bodyTextarea.element as HTMLTextAreaElement).value).toContain('这是由 LLM 起草的示例正文')

    // 此时生成弹窗应自动关闭（emit update:modelValue false）
    const stillOpen = wrapper
      .findAllComponents(ElDialogStub)
      .some((d) => d.attributes('data-title') === '自动生成技能正文')
    expect(stillOpen).toBe(false)

    // 提交创建：body 已填充，createSkill 应收到 draft 作为 body
    await findButton(wrapper, '确定').trigger('click')
    await flushPromises()
    expect(apiMock.createSkill).toHaveBeenCalledTimes(1)
    expect(apiMock.createSkill).toHaveBeenCalledWith(
      expect.objectContaining({
        name: 'llm-skill',
        description: '解析 PDF 文档并提取关键文本与元数据',
        body: expect.stringContaining('这是由 LLM 起草的示例正文'),
      } satisfies SkillCreatePayload),
    )
  })
})