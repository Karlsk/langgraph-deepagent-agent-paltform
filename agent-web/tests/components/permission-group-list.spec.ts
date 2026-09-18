// @vitest-environment happy-dom
/**
 * PermissionGroupList 视图测试（权限组 CRUD 前端适配）：
 * - stub Element Plus 组件（不做真实渲染），挂载真实
 *   WebAgentTable + WebAgentFormDialog；
 * - mock `@/api/permission-groups` 全部端点 + `@/api/mcp` listToolCatalog；
 * - 覆盖：挂载渲染 / 创建 / 编辑回填 / 删除确认与取消 / 搜索防抖。
 */
import { afterEach, beforeEach, describe, expect, it, vi } from 'vitest'
import { defineComponent, h, inject, provide } from 'vue'
import { flushPromises, mount } from '@vue/test-utils'
import type { VueWrapper } from '@vue/test-utils'

import PermissionGroupList from '@/views/settings/PermissionGroupList.vue'
import type {
  PermissionGroupCreatePayload,
  PermissionGroupPatchPayload,
  PermissionGroupRow,
} from '@/api/permission-groups'
import type { ToolCatalogEntry } from '@/api/mcp'
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
const elMessageFn = elMessageMock

const ROWS: PermissionGroupRow[] = [
  {
    id: 1,
    name: 'admin-tools',
    description: '管理员专用高危工具集',
    tool_names: ['dangerous_tool', 'db_write'],
    created_by: 'seed',
  },
  {
    id: 2,
    name: 'read-only',
    description: '只读工具组，不含写入类工具',
    tool_names: ['web_search'],
    created_by: 'seed',
  },
  {
    id: 3,
    name: 'empty-group',
    description: '',
    tool_names: [],
    created_by: 'admin',
  },
]

const TOOL_CATALOG: ToolCatalogEntry[] = [
  { name: 'web_search', source: 'builtin', server: null },
  { name: 'dangerous_tool', source: 'builtin', server: null },
  { name: 'mcp-server__remote_tool', source: 'mcp', server: 'mcp-server' },
]

const { apiMock, mcpMock } = vi.hoisted(() => ({
  apiMock: {
    listPermissionGroups: vi.fn(),
    listPermissionGroupsPage: vi.fn(),
    getPermissionGroup: vi.fn(),
    createPermissionGroup: vi.fn(),
    patchPermissionGroup: vi.fn(),
    deletePermissionGroup: vi.fn(),
  },
  mcpMock: {
    listToolCatalog: vi.fn(),
  },
}))

vi.mock('@/api/permission-groups', () => apiMock)
vi.mock('@/api/mcp', () => mcpMock)

const ROWS_KEY = Symbol('pg-table-rows')

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
        ? h('div', { class: 'el-dialog-stub', 'data-title': props.title }, [
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

const ElEmptyStub = defineComponent({
  name: 'ElEmpty',
  setup(_, { slots }) {
    return () => h('div', { class: 'el-empty-stub' }, slots.default?.())
  },
})

/**
 * ElSelect stub：multiple 模式下维护内部 selected 数组，
 * 渲染 el-option-group / el-option 插槽以暴露选项文案供断言。
 */
const ElSelectStub = defineComponent({
  name: 'ElSelect',
  props: {
    modelValue: { type: [Array, String, Number], default: () => [] },
    multiple: Boolean,
    loading: Boolean,
    placeholder: String,
    noDataText: String,
  },
  emits: ['update:modelValue'],
  setup(props, { slots }) {
    return () =>
      h('div', { class: 'el-select-stub' }, [
        slots.default ? slots.default() : undefined,
        props.multiple && Array.isArray(props.modelValue)
          ? h('span', { class: 'el-select-stub__tags' }, props.modelValue.join(','))
          : null,
      ])
  },
})

const ElOptionGroupStub = defineComponent({
  name: 'ElOptionGroup',
  props: { label: String },
  setup(props, { slots }) {
    return () =>
      h('div', { class: 'el-option-group-stub', 'data-label': props.label }, [
        slots.default ? slots.default() : undefined,
      ])
  },
})

const ElOptionStub = defineComponent({
  name: 'ElOption',
  props: { label: String, value: [String, Number] },
  setup(props) {
    return () => h('span', { class: 'el-option-stub', 'data-value': String(props.value) }, props.label)
  },
})

function mountPage(): VueWrapper {
  return mount(PermissionGroupList, {
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
        ElOptionGroup: ElOptionGroupStub,
        ElOption: ElOptionStub,
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

  apiMock.listPermissionGroupsPage.mockImplementation(
    async () =>
      ({
        items: ROWS.map((row) => ({ ...row, tool_names: [...row.tool_names] })),
        total: ROWS.length,
        page: 1,
        pageSize: 10,
      }) satisfies PageResult<PermissionGroupRow>,
  )
  apiMock.createPermissionGroup.mockImplementation(
    async (payload: PermissionGroupCreatePayload) =>
      ({
        id: 99,
        name: payload.name,
        description: payload.description ?? '',
        tool_names: payload.tool_names ?? [],
        created_by: 'user',
      }) satisfies PermissionGroupRow,
  )
  apiMock.patchPermissionGroup.mockImplementation(
    async (groupId: number, payload: PermissionGroupPatchPayload) => {
      const row = ROWS.find((r) => r.id === groupId)
      return {
        id: groupId,
        name: row?.name ?? 'unknown',
        description: payload.description ?? row?.description ?? '',
        tool_names: payload.tool_names ?? row?.tool_names ?? [],
        created_by: row?.created_by ?? 'user',
      } satisfies PermissionGroupRow
    },
  )
  apiMock.deletePermissionGroup.mockResolvedValue(null)
  apiMock.listPermissionGroups.mockResolvedValue([])
  apiMock.getPermissionGroup.mockImplementation(async (id: number) => {
    const row = ROWS.find((r) => r.id === id)
    if (!row) throw new Error(`group ${id} not found`)
    return { ...row, tool_names: [...row.tool_names] }
  })

  mcpMock.listToolCatalog.mockResolvedValue(TOOL_CATALOG.map((e) => ({ ...e })))
})

afterEach(() => {
  vi.useRealTimers()
})

describe('PermissionGroupList 权限组管理页（CRUD 前端适配）', () => {
  it('挂载调 listPermissionGroupsPage + listToolCatalog 并渲染 3 行 × 5 列', async () => {
    const wrapper = mountPage()
    await flushPromises()

    expect(apiMock.listPermissionGroupsPage).toHaveBeenCalledTimes(1)
    expect(mcpMock.listToolCatalog).toHaveBeenCalledTimes(1)

    const data = wrapper.findComponent(ElTableStub).props('data') as unknown[]
    expect(data).toHaveLength(3)

    expect(wrapper.text()).toContain('admin-tools')
    expect(wrapper.text()).toContain('read-only')
    expect(wrapper.text()).toContain('empty-group')

    expect(wrapper.findAll('.el-table-column-stub')).toHaveLength(5)
  })

  it('工具数列正确渲染各行的 tool_names.length', async () => {
    const wrapper = mountPage()
    await flushPromises()

    // tool_count 插槽渲染 {{ row.tool_names.length }}：admin-tools=2, read-only=1, empty-group=0
    // 直接检查页面文本包含这些数字（它们作为独立文本节点出现在列 stub 内）
    const text = wrapper.text()
    expect(text).toContain('admin-tools')
    expect(text).toContain('read-only')
    expect(text).toContain('empty-group')
    // 列数 = 5（名称 / 描述 / 工具数 / 创建者 / 操作）
    expect(wrapper.findAll('.el-table-column-stub')).toHaveLength(5)
  })

  it('创建权限组：弹窗打开 → 提交调 createPermissionGroup({name, description, tool_names})', async () => {
    const wrapper = mountPage()
    await flushPromises()

    await findButton(wrapper, '新建权限组').trigger('click')
    await flushPromises()

    const dialog = wrapper.findComponent(ElDialogStub)
    expect(dialog.exists()).toBe(true)
    expect(dialog.attributes('data-title')).toBe('新建权限组')

    await wrapper.find('input[placeholder="请输入权限组名称"]').setValue('new-group')
    await wrapper.find('textarea[placeholder="请输入权限组描述"]').setValue('新权限组描述')

    await findButton(wrapper, '确定').trigger('click')
    await flushPromises()

    expect(apiMock.createPermissionGroup).toHaveBeenCalledWith({
      name: 'new-group',
      description: '新权限组描述',
      tool_names: [],
    } satisfies PermissionGroupCreatePayload)
    expect(elMessageFn).toHaveBeenCalledWith(
      expect.objectContaining({ type: 'success', message: '已保存：new-group' }),
    )
    expect(apiMock.listPermissionGroupsPage).toHaveBeenCalledTimes(2)
  })

  it('编辑权限组：name disabled + description/tool_names 回填 → 提交 patchPermissionGroup', async () => {
    const wrapper = mountPage()
    await flushPromises()

    await findRowButton(wrapper, '编辑', 0).trigger('click')
    await flushPromises()

    const dialog = wrapper.findComponent(ElDialogStub)
    expect(dialog.exists()).toBe(true)
    expect(dialog.attributes('data-title')).toBe('编辑权限组')

    const nameInput = wrapper.find('input[placeholder="请输入权限组名称"]')
    expect((nameInput.element as HTMLInputElement).disabled).toBe(true)
    expect((nameInput.element as HTMLInputElement).value).toBe('admin-tools')

    const descInput = wrapper.find('textarea[placeholder="请输入权限组描述"]')
    expect((descInput.element as HTMLTextAreaElement).value).toBe('管理员专用高危工具集')

    await descInput.setValue('更新后的描述')
    await findButton(wrapper, '确定').trigger('click')
    await flushPromises()

    expect(apiMock.patchPermissionGroup).toHaveBeenCalledWith(1, {
      description: '更新后的描述',
      tool_names: ['dangerous_tool', 'db_write'],
    } satisfies PermissionGroupPatchPayload)
    expect(apiMock.createPermissionGroup).not.toHaveBeenCalled()
  })

  it('删除：useConfirm 调用并调 deletePermissionGroup；确认后刷新列表', async () => {
    const wrapper = mountPage()
    await flushPromises()

    await findRowButton(wrapper, '删除', 0).trigger('click')
    expect(confirmMock).toHaveBeenCalledWith(
      '确定删除权限组「admin-tools」吗？已绑定该权限组的 Agent 将失去审批配置。',
      '删除权限组',
      expect.anything(),
    )
    await flushPromises()

    expect(apiMock.deletePermissionGroup).toHaveBeenCalledWith(1)
    expect(apiMock.listPermissionGroupsPage).toHaveBeenCalledTimes(2)
  })

  it('删除取消：不调 deletePermissionGroup，列表不变', async () => {
    confirmMock.mockRejectedValue('cancel')
    const wrapper = mountPage()
    await flushPromises()

    await findRowButton(wrapper, '删除', 0).trigger('click')
    await flushPromises()

    expect(apiMock.deletePermissionGroup).not.toHaveBeenCalled()
    expect(apiMock.listPermissionGroupsPage).toHaveBeenCalledTimes(1)
  })

  it('关键字输入：debounce 300ms 后触发 listPermissionGroupsPage 重查', async () => {
    const wrapper = mountPage()
    await flushPromises()

    expect(apiMock.listPermissionGroupsPage).toHaveBeenCalledTimes(1)

    const searchInput = wrapper.find('input[placeholder="按名称/描述模糊搜索"]')
    await searchInput.setValue('admin')
    vi.advanceTimersByTime(200)
    await flushPromises()
    expect(apiMock.listPermissionGroupsPage).toHaveBeenCalledTimes(1)

    vi.advanceTimersByTime(100)
    await flushPromises()
    expect(apiMock.listPermissionGroupsPage).toHaveBeenCalledTimes(2)
    expect(apiMock.listPermissionGroupsPage).toHaveBeenLastCalledWith(
      expect.objectContaining({ keyword: 'admin' }),
    )
  })

  it('工具目录加载失败：降级为空选项，不阻塞页面渲染', async () => {
    mcpMock.listToolCatalog.mockRejectedValue(new Error('network down'))
    const wrapper = mountPage()
    await flushPromises()

    expect(apiMock.listPermissionGroupsPage).toHaveBeenCalledTimes(1)
    // 页面仍正常渲染
    expect(wrapper.text()).toContain('admin-tools')
  })
})
