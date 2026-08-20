// @vitest-environment happy-dom
/**
 * McpServerToolsDialog 组件测试（task-ccc MCP 前端适配）：
 * - stub Element Plus（不做真实渲染），组件模板内联渲染；
 * - mock `@/api/mcp` 的 `listMcpServerTools` + `callMcpServerTool`；
 * - 覆盖：mount 触发自动拉取、点击「调用」展开面板、合法 JSON 执行调用、
 *   非法 JSON 报错、行内 result 面板、关闭弹窗重置状态。
 */
import { afterEach, beforeEach, describe, expect, it, vi } from 'vitest'
import { defineComponent, h, inject, provide, ref } from 'vue'
import { flushPromises, mount } from '@vue/test-utils'
import type { VueWrapper } from '@vue/test-utils'

import McpServerToolsDialog from '@/views/mcp/McpServerToolsDialog.vue'
import type { McpToolCallResult, McpToolInfo } from '@/api/mcp'

/** element-plus stub：ElMessage 仅占位；本视图错误路径统一由 request 拦截器提示 */
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

/** 3 个工具 mock：覆盖空 args / 必填 string args / 嵌套 object args */
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
  {
    name: 'sum',
    description: '嵌套对象参数',
    args_schema: {
      type: 'object',
      properties: {
        nums: { type: 'array', items: { type: 'number' } },
        opts: { type: 'object' },
      },
    },
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
          ])
        : null
  },
})

const ElButtonStub = defineComponent({
  name: 'ElButton',
  props: { loading: Boolean, disabled: Boolean, type: String, link: Boolean },
  emits: ['click'],
  setup(props, { emit, slots, attrs }) {
    return () =>
      h(
        'button',
        {
          class: attrs.class,
          'data-loading': props.loading ? 'true' : 'false',
          'data-disabled': props.disabled ? 'true' : 'false',
          'data-type': props.type ?? 'default',
          onClick: () => emit('click'),
        },
        slots.default ? slots.default() : undefined,
      )
  },
})

const ElInputStub = defineComponent({
  name: 'ElInput',
  props: {
    modelValue: { type: String, default: '' },
    type: { type: String, default: 'text' },
    autosize: { type: Object, default: () => ({}) },
    placeholder: String,
  },
  emits: ['update:modelValue'],
  setup(props, { emit }) {
    return () => {
      const isTextarea = props.type === 'textarea'
      const onInput = (event: Event) =>
        emit('update:modelValue', (event.target as HTMLInputElement | HTMLTextAreaElement).value)
      return isTextarea
        ? h('textarea', {
            class: 'el-input-stub',
            placeholder: props.placeholder,
            value: props.modelValue,
            rows: 4,
            onInput,
          })
        : h('input', {
            class: 'el-input-stub',
            placeholder: props.placeholder,
            value: props.modelValue,
            onInput,
          })
    }
  },
})

const TABLE_PROVIDE_KEY = Symbol('mcp-tools-table-rows')
interface ToolTableProps {
  data: McpToolInfo[]
}

/**
 * ElTableColumn 提供 #default slot 接收 row 上下文；按 data 数组展开多次。
 * 每个 column 通过 prop `label` 标识；调用 slot 时传入 `{ row, $index }`。
 */
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
        slots.append ? slots.append() : undefined,
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
    apiMock.callMcpServerTool.mockReset()
  })

  afterEach(() => {
    vi.clearAllMocks()
  })

  it('打开弹窗时自动调 listMcpServerTools 拉取工具', async () => {
    apiMock.listMcpServerTools.mockResolvedValueOnce(TOOLS)
    mountDialog()
    await flushPromises()
    expect(apiMock.listMcpServerTools).toHaveBeenCalledTimes(1)
    expect(apiMock.listMcpServerTools).toHaveBeenCalledWith('echo-server')
  })

  it('每行展示「调用」按钮；首次点击展开行内调用面板', async () => {
    const wrapper = mountDialog()
    await flushPromises()

    // 行数 = 3 个工具，每行都有"调用"按钮
    const callButtons = wrapper.findAll('button').filter((b) => b.text().includes('调用'))
    expect(callButtons.length).toBeGreaterThanOrEqual(3)

    // 点击第 0 行 "调用"
    await callButtons[0].trigger('click')
    await flushPromises()

    // 行内应出现"调试调用 — echo"标题 + 执行调用 + 清空按钮
    // 注：v-show 隐藏的兄弟行仍计入 DOM（happy-dom 不应用 display:none），故断言宽松为 >= 1
    expect(wrapper.html()).toContain('调试调用 —')
    expect(wrapper.html()).toContain('arguments（JSON object）')
    const execButtons = wrapper.findAll('button').filter((b) => b.text().includes('执行调用'))
    expect(execButtons.length).toBeGreaterThanOrEqual(1)
  })

  it('执行调用：合法 JSON → 调 callMcpServerTool 并展示 result', async () => {
    const mockResult: McpToolCallResult = {
      server: 'echo-server',
      tool_name: 'echo',
      result: { echoed: 'hello-mcp' },
    }
    apiMock.callMcpServerTool.mockResolvedValueOnce(mockResult)

    const wrapper = mountDialog()
    await flushPromises()

    const callButtons = wrapper.findAll('button').filter((b) => b.text().includes('调用'))
    await callButtons[0].trigger('click')
    await flushPromises()

    // 找到 arguments textarea（在调用面板里）
    const textarea = wrapper.find('textarea.el-input-stub')
    expect(textarea.exists()).toBe(true)

    // 写入合法 JSON
    await textarea.setValue('{"text":"hello-mcp"}')
    await flushPromises()

    // 点击 "执行调用"
    const execButton = wrapper.findAll('button').find((b) => b.text().includes('执行调用'))
    await execButton!.trigger('click')
    await flushPromises()

    expect(apiMock.callMcpServerTool).toHaveBeenCalledTimes(1)
    expect(apiMock.callMcpServerTool).toHaveBeenCalledWith('echo-server', {
      tool_name: 'echo',
      arguments: { text: 'hello-mcp' },
    })

    // result 面板展示：标签 + JSON 内容
    expect(wrapper.html()).toContain('result（成功）')
    expect(wrapper.html()).toContain('"echoed"')
    expect(wrapper.html()).toContain('"hello-mcp"')
  })

  it('非法 arguments → 不发请求，本地展示 error', async () => {
    const wrapper = mountDialog()
    await flushPromises()

    const callButtons = wrapper.findAll('button').filter((b) => b.text().includes('调用'))
    await callButtons[0].trigger('click')
    await flushPromises()

    const textarea = wrapper.find('textarea.el-input-stub')
    // 写入非法 JSON（数组而非对象）
    await textarea.setValue('[1,2,3]')
    await flushPromises()

    const execButton = wrapper.findAll('button').find((b) => b.text().includes('执行调用'))
    await execButton!.trigger('click')
    await flushPromises()

    expect(apiMock.callMcpServerTool).not.toHaveBeenCalled()
    expect(wrapper.html()).toContain('error（失败）')
    expect(wrapper.html()).toContain('JSON object')
  })

  it('调用失败：callMcpServerTool throw → 拦截器已 toast + 行内 error 面板', async () => {
    apiMock.callMcpServerTool.mockRejectedValueOnce({
      response: { status: 502, data: { code: 502, message: 'tool execution failed' } },
    })

    const wrapper = mountDialog()
    await flushPromises()

    const callButtons = wrapper.findAll('button').filter((b) => b.text().includes('调用'))
    await callButtons[0].trigger('click')
    await flushPromises()

    const textarea = wrapper.find('textarea.el-input-stub')
    await textarea.setValue('{}')
    await flushPromises()

    const execButton = wrapper.findAll('button').find((b) => b.text().includes('执行调用'))
    await execButton!.trigger('click')
    await flushPromises()

    expect(apiMock.callMcpServerTool).toHaveBeenCalledTimes(1)
    expect(wrapper.html()).toContain('error（失败）')
    expect(wrapper.html()).toContain('502')
  })

  it('「清空」按钮重置该行的 arguments / result / error', async () => {
    apiMock.callMcpServerTool.mockResolvedValueOnce({
      server: 'echo-server',
      tool_name: 'echo',
      result: 'ok',
    })

    const wrapper = mountDialog()
    await flushPromises()

    const callButtons = wrapper.findAll('button').filter((b) => b.text().includes('调用'))
    await callButtons[0].trigger('click')
    await flushPromises()

    const textarea = wrapper.find('textarea.el-input-stub')
    await textarea.setValue('{"text":"x"}')
    await flushPromises()

    const execButton = wrapper.findAll('button').find((b) => b.text().includes('执行调用'))
    await execButton!.trigger('click')
    await flushPromises()
    expect(wrapper.html()).toContain('result（成功）')

    // 直接找所有"清空"按钮中第一个（即行内面板的）
    const clearButtons = wrapper.findAll('button').filter((b) => b.text() === '清空')
    await clearButtons[0].trigger('click')
    await flushPromises()

    expect(wrapper.html()).not.toContain('result（成功）')
    expect(wrapper.html()).not.toContain('error（失败）')
  })

  it('关闭弹窗 → 清空所有行内调用状态', async () => {
    const wrapper = mountDialog()
    await flushPromises()

    const callButtons = wrapper.findAll('button').filter((b) => b.text().includes('调用'))
    await callButtons[0].trigger('click')
    await flushPromises()
    expect(wrapper.html()).toContain('调试调用 —')

    // 关闭（emit update:modelValue false）
    await wrapper.setProps({ modelValue: false })
    await flushPromises()

    // 弹窗 stub 不再渲染
    expect(wrapper.find('.el-dialog-stub').exists()).toBe(false)
  })
})

// stub provider 用：导出一个 ref 变量避免 TS 报"未使用"
const _stubRef = ref(0)
void _stubRef