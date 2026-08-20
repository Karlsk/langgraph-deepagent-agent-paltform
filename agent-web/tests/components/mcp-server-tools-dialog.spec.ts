// @vitest-environment happy-dom
/**
 * McpServerToolsDialog 组件测试（task-ccc MCP 前端适配 + 工具调用 UX 改进）：
 * - stub Element Plus（不做真实渲染），组件模板内联渲染；
 * - mock `@/api/mcp` 的 `listMcpServerTools`；
 * - 「调用」按钮 → 触发 McpServerToolCallDialog（独立弹窗）的 mount；
 * - 行内 result/error/JSON 校验/清空 等行为已迁移到新弹窗 spec，本文件不再覆盖。
 */
import { afterEach, beforeEach, describe, expect, it, vi } from 'vitest'
import { defineComponent, h, inject, provide, ref } from 'vue'
import { flushPromises, mount } from '@vue/test-utils'
import type { VueWrapper } from '@vue/test-utils'

import McpServerToolsDialog from '@/views/mcp/McpServerToolsDialog.vue'
import McpServerToolCallDialogStub from '@/views/mcp/__mcp_tool_call_dialog_stub__.vue'
import type { McpToolInfo } from '@/api/mcp'

/** element-plus stub：仅占位（错误提示由 request 拦截器统一处理） */
const elMessageMock = vi.hoisted(() => {
  const fn = vi.fn()
  return Object.assign(fn, { error: vi.fn(), success: vi.fn(), warning: vi.fn() })
})
vi.mock('element-plus', () => ({ ElMessage: elMessageMock }))

const apiMock = vi.hoisted(() => ({
  listMcpServerTools: vi.fn(),
  callMcpServerTool: vi.fn(),
}))
vi.mock('@/api/mcp', () => apiMock)

/** 2 个工具 mock：覆盖普通 string 必填 + 无参 */
const TOOLS: McpToolInfo[] = [
  {
    name: 'echo',
    description: '回显参数',
    args_schema: {
      type: 'object',
      properties: { text: { type: 'string' } },
      required: ['text'],
    },
  },
  {
    name: 'noop',
    description: '无参数工具',
    args_schema: { type: 'object', properties: {} },
  },
]

/* -------------------------------------------------------------------------- */
/*  Element Plus stub 集合（按需，不做完整实现）                              */
/* -------------------------------------------------------------------------- */

const ElDialogStub = defineComponent({
  name: 'ElDialog',
  props: { modelValue: Boolean, title: String, width: String },
  emits: ['update:modelValue', 'close'],
  setup(props, { slots }) {
    return () =>
      props.modelValue
        ? h('div', { class: 'el-dialog-stub', 'data-title': props.title }, [
            slots.default?.(),
            slots.footer?.(),
            ...(slots.default?.() ? [] : []),
          ])
        : null
  },
})

const ElButtonStub = defineComponent({
  name: 'ElButton',
  props: { loading: Boolean, disabled: Boolean, type: String, size: String, link: Boolean },
  emits: ['click'],
  setup(props, { emit, slots, attrs }) {
    return () =>
      h(
        'button',
        {
          class: ['el-button-stub', attrs.class],
          'data-loading': props.loading ? 'true' : 'false',
          'data-disabled': props.disabled ? 'true' : 'false',
          'data-type': props.type ?? 'default',
          'data-size': props.size ?? 'default',
          onClick: () => emit('click'),
        },
        slots.default ? slots.default() : undefined,
      )
  },
})

const ElInputStub = defineComponent({
  name: 'ElInput',
  props: {
    modelValue: { type: [String, Number], default: '' },
    type: { type: String, default: 'text' },
    autosize: { type: Object, default: () => ({}) },
    placeholder: String,
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
            value: String(props.modelValue ?? ''),
            rows: 4,
            onInput,
          })
        : h('input', {
            class: 'el-input-stub',
            placeholder: props.placeholder,
            value: String(props.modelValue ?? ''),
            onInput,
          })
    }
  },
})

const TABLE_PROVIDE_KEY = Symbol('mcp-tools-table-rows')
interface ToolTableProps {
  data: McpToolInfo[]
}

const ElTableColumnStub = defineComponent({
  name: 'ElTableColumn',
  props: { label: String, prop: String, minWidth: String, width: String, fixed: String },
  setup(_, { slots }) {
    const tableProps = inject<ToolTableProps | null>(TABLE_PROVIDE_KEY, null)
    return () =>
      h(
        'div',
        { class: 'el-table-column-stub', 'data-label': _.label },
        (tableProps?.data ?? []).map((row: McpToolInfo, index: number) =>
          slots.default ? slots.default({ row, $index: index }) : null,
        ),
      )
  },
})

const ElTableStubWithProvide = defineComponent({
  name: 'ElTable',
  props: { data: { type: Array, default: () => [] }, rowKey: String },
  setup(props, { slots }) {
    provide(TABLE_PROVIDE_KEY, props as unknown as ToolTableProps)
    return () =>
      h('div', { class: 'el-table-stub' }, [
        (props.data as unknown[]).length === 0 && slots.empty ? slots.empty() : undefined,
        slots.default ? slots.default() : undefined,
      ])
  },
})

function mountDialog(visible = true): VueWrapper {
  const wrapper = mount(McpServerToolsDialog, {
    props: {
      serverName: 'echo-server',
      modelValue: visible,
    },
    global: {
      provide: { [TABLE_PROVIDE_KEY as symbol]: undefined },
      stubs: {
        ElDialog: ElDialogStub,
        ElButton: ElButtonStub,
        ElInput: ElInputStub,
        ElTable: ElTableStubWithProvide,
        ElTableColumn: ElTableColumnStub,
        ElIcon: true,
        McpServerToolCallDialog: McpServerToolCallDialogStub,
      },
      directives: { loading: () => undefined },
    },
  })
  return wrapper
}

/* -------------------------------------------------------------------------- */
/*  Tests                                                                       */
/* -------------------------------------------------------------------------- */

describe('McpServerToolsDialog', () => {
  beforeEach(() => {
    apiMock.listMcpServerTools.mockReset()
    apiMock.callMcpServerTool.mockReset()
    apiMock.listMcpServerTools.mockResolvedValue(TOOLS)
  })

  afterEach(() => {
    vi.clearAllMocks()
  })

  it('打开弹窗时自动调 listMcpServerTools 拉取工具', async () => {
    mountDialog()
    await flushPromises()
    expect(apiMock.listMcpServerTools).toHaveBeenCalledTimes(1)
    expect(apiMock.listMcpServerTools).toHaveBeenCalledWith('echo-server')
  })

  it('每行展示「调用」按钮（实心 primary），点击触发 McpServerToolCallDialog 挂载', async () => {
    const wrapper = mountDialog()
    await flushPromises()

    // 行数 = 2 个工具，每行都有"调用"按钮
    const callButtons = wrapper.findAll('button').filter((b) => b.text().includes('调用'))
    expect(callButtons.length).toBeGreaterThanOrEqual(2)

    // 按钮类型 = primary 且带 size="small"（不再是 link）
    const firstBtn = callButtons[0]
    expect(firstBtn.attributes('data-type')).toBe('primary')
    expect(firstBtn.attributes('data-size')).toBe('small')

    // 初始：McpServerToolCallDialog 尚未挂载
    expect(wrapper.findComponent(McpServerToolCallDialogStub).exists()).toBe(true)
    expect(wrapper.findComponent(McpServerToolCallDialogStub).props('modelValue')).toBe(false)
    expect(wrapper.findComponent(McpServerToolCallDialogStub).props('tool')).toBe(null)

    // 点击第 0 行 "调用"
    await firstBtn.trigger('click')
    await flushPromises()

    // 弹窗打开 + tool 传入
    const sub = wrapper.findComponent(McpServerToolCallDialogStub)
    expect(sub.props('modelValue')).toBe(true)
    expect(sub.props('tool')).toEqual(TOOLS[0])
    expect(sub.props('serverName')).toBe('echo-server')
  })

  it('关闭弹窗时同步关闭 McpServerToolCallDialog（关闭 = 终止调试会话）', async () => {
    const wrapper = mountDialog()
    await flushPromises()

    const callButtons = wrapper.findAll('button').filter((b) => b.text().includes('调用'))
    await callButtons[0].trigger('click')
    await flushPromises()
    expect(wrapper.findComponent(McpServerToolCallDialogStub).props('modelValue')).toBe(true)

    await wrapper.setProps({ modelValue: false })
    await flushPromises()
    expect(wrapper.find('.el-dialog-stub').exists()).toBe(false)
  })
})

// stub provider 用：导出一个 ref 变量避免 TS 报"未使用"
const _stubRef = ref(0)
void _stubRef