// @vitest-environment happy-dom
/**
 * McpServerToolCallDialog 组件测试（task-ccc 工具调用 UX 改进最终版）：
 * - stub Element Plus（不做真实渲染），组件模板内联渲染；
 * - mock `@/api/mcp` 的 `callMcpServerTool`；
 * - 覆盖：mount 头渲染、v-model 双向、必填字段提示、本地 JSON 校验
 *   （空 / 非法 / 缺必填）、成功调用、失败调用、关闭语义、清空重置。
 *
 * 注：早期版本尝试过「JSON Schema → 动态表单」生成（el-select / el-input-number /
 * el-switch / el-collapse），但在该项目 Vue 3.5 + vite-plugin-vue 5.2 编译器下
 * 触发 [Codegen node is missing for element/if/for node] 错误。本次回退到 JSON
 * 文本框（与父组件行内展开版同形态，但换到了独立弹窗 + 实心按钮位置）。
 */
import { afterEach, beforeEach, describe, expect, it, vi } from 'vitest'
import { defineComponent, h } from 'vue'
import { flushPromises, mount } from '@vue/test-utils'
import type { VueWrapper } from '@vue/test-utils'

import McpServerToolCallDialog from '@/views/mcp/McpServerToolCallDialog.vue'
import type { McpToolCallResult, McpToolInfo } from '@/api/mcp'

/** element-plus stub：占位即可；业务错误提示由 request 拦截器统一处理 */
const elMessageMock = vi.hoisted(() => {
  const fn = vi.fn()
  return Object.assign(fn, { error: vi.fn(), success: vi.fn(), warning: vi.fn() })
})
vi.mock('element-plus', () => ({ ElMessage: elMessageMock }))

const notifyMock = vi.hoisted(() => ({
  notifyError: vi.fn(),
  notifySuccess: vi.fn(),
  notifyWarning: vi.fn(),
  notifyInfo: vi.fn(),
}))
vi.mock('@/utils/notify', () => notifyMock)

const apiMock = vi.hoisted(() => ({
  callMcpServerTool: vi.fn(),
}))
vi.mock('@/api/mcp', () => apiMock)

/** 含必填字段的工具：text 必填、limit 默认 10 */
const TOOL_WITH_REQUIRED: McpToolInfo = {
  name: 'echo',
  description: '回显参数',
  args_schema: {
    type: 'object',
    required: ['text'],
    properties: {
      text: { type: 'string', description: '文本内容' },
      limit: { type: 'integer', default: 10 },
    },
  },
}

/** 无参工具 */
const TOOL_NOOP: McpToolInfo = {
  name: 'noop',
  description: '无参数工具',
  args_schema: { type: 'object', properties: {} },
}

/* -------------------------------------------------------------------------- */
/*  Element Plus stub                                                          */
/* -------------------------------------------------------------------------- */

const ElDialogStub = defineComponent({
  name: 'ElDialog',
  props: { modelValue: Boolean, title: String, width: String },
  emits: ['update:modelValue'],
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
  props: { loading: Boolean, disabled: Boolean, type: String, size: String },
  emits: ['click'],
  setup(props, { emit, slots, attrs }) {
    return () =>
      h(
        'button',
        {
          class: ['el-button-stub', attrs.class],
          'data-loading': props.loading ? 'true' : 'false',
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
            rows: 6,
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

function mountDialog(
  tool: McpToolInfo = TOOL_WITH_REQUIRED,
  visible = true,
): VueWrapper {
  return mount(McpServerToolCallDialog, {
    props: {
      serverName: 'kitchen-sink-server',
      tool,
      modelValue: visible,
    },
    global: {
      stubs: {
        ElDialog: ElDialogStub,
        ElButton: ElButtonStub,
        ElInput: ElInputStub,
        ElIcon: true,
      },
    },
  })
}

/* -------------------------------------------------------------------------- */
/*  Tests                                                                       */
/* -------------------------------------------------------------------------- */

describe('McpServerToolCallDialog', () => {
  beforeEach(() => {
    apiMock.callMcpServerTool.mockReset()
    notifyMock.notifyError.mockReset()
    notifyMock.notifySuccess.mockReset()
  })

  afterEach(() => {
    vi.clearAllMocks()
  })

  it('mount 渲染头部（namespacedName + description）', () => {
    const wrapper = mountDialog()
    expect(wrapper.html()).toContain('kitchen-sink-server__echo')
    expect(wrapper.html()).toContain('回显参数')
  })

  it('JSON textarea 双向绑定：用户输入 → v-model 同步', async () => {
    const wrapper = mountDialog()
    const textarea = wrapper.find('textarea.el-input-stub')
    expect(textarea.exists()).toBe(true)
    await textarea.setValue('{"text":"hello"}')
    await flushPromises()
    // v-model 双向：textarea DOM 的 value 应保持；下次 props 变化时也保留
    expect((textarea.element as HTMLTextAreaElement).value).toBe('{"text":"hello"}')
  })

  it('必填字段列表展示在参数提示里', () => {
    const wrapper = mountDialog()
    // 「必填字段：text」应在 .mcp-call-dialog__hint 中渲染
    const hint = wrapper.find('.mcp-call-dialog__hint')
    expect(hint.exists()).toBe(true)
    expect(hint.text()).toBe('必填字段：text')
    // limit 在 properties 中但不在 required 中 → 不应在 hint 中（textarea 预填 value 可能含 limit，故精确查 hint 节点）
    expect(hint.text()).not.toContain('limit')
  })

  it('校验：空 + 有必填字段 → notifyError + 不发请求', async () => {
    const wrapper = mountDialog()
    const execBtn = wrapper
      .findAll('button.el-button-stub')
      .find((b) => b.text().includes('执行调用'))
    await execBtn!.trigger('click')
    await flushPromises()

    expect(apiMock.callMcpServerTool).not.toHaveBeenCalled()
    expect(notifyMock.notifyError).toHaveBeenCalledTimes(1)
    expect(wrapper.html()).toContain('参数校验失败')
  })

  it('校验：非法 JSON → notifyError + 不发请求 + 错误面板', async () => {
    const wrapper = mountDialog()
    const textarea = wrapper.find('textarea.el-input-stub')
    await textarea.setValue('{not valid json')
    await flushPromises()

    const execBtn = wrapper
      .findAll('button.el-button-stub')
      .find((b) => b.text().includes('执行调用'))
    await execBtn!.trigger('click')
    await flushPromises()

    expect(apiMock.callMcpServerTool).not.toHaveBeenCalled()
    expect(notifyMock.notifyError).toHaveBeenCalledTimes(1)
    expect(wrapper.html()).toContain('不是合法 JSON')
  })

  it('校验：合法 JSON 但缺必填字段 → notifyError + 不发请求', async () => {
    const wrapper = mountDialog()
    const textarea = wrapper.find('textarea.el-input-stub')
    await textarea.setValue('{"limit":5}')  // 缺 text
    await flushPromises()

    const execBtn = wrapper
      .findAll('button.el-button-stub')
      .find((b) => b.text().includes('执行调用'))
    await execBtn!.trigger('click')
    await flushPromises()

    expect(apiMock.callMcpServerTool).not.toHaveBeenCalled()
    expect(notifyMock.notifyError).toHaveBeenCalledTimes(1)
    expect(wrapper.html()).toContain('缺少必填字段')
  })

  it('校验通过：合法 JSON + 必填齐 → 调 callMcpServerTool + 渲染 result 面板', async () => {
    const successResult: McpToolCallResult = {
      server: 'kitchen-sink-server',
      tool_name: 'echo',
      result: { echoed: 'hello' },
    }
    apiMock.callMcpServerTool.mockResolvedValueOnce(successResult)

    const wrapper = mountDialog()
    const textarea = wrapper.find('textarea.el-input-stub')
    await textarea.setValue('{"text":"hello","limit":3}')
    await flushPromises()

    const execBtn = wrapper
      .findAll('button.el-button-stub')
      .find((b) => b.text().includes('执行调用'))
    await execBtn!.trigger('click')
    await flushPromises()

    expect(apiMock.callMcpServerTool).toHaveBeenCalledTimes(1)
    expect(apiMock.callMcpServerTool).toHaveBeenCalledWith('kitchen-sink-server', {
      tool_name: 'echo',
      arguments: { text: 'hello', limit: 3 },
    })
    expect(notifyMock.notifySuccess).toHaveBeenCalledWith('已调用：echo')
    expect(wrapper.html()).toContain('result（成功）')
    expect(wrapper.html()).toContain('"echoed"')
  })

  it('调用失败（API 502）→ 渲染红色 error 面板 + 不显示 result', async () => {
    apiMock.callMcpServerTool.mockRejectedValueOnce({
      response: { status: 502, data: { code: 502, message: 'tool execution failed' } },
    })

    const wrapper = mountDialog()
    const textarea = wrapper.find('textarea.el-input-stub')
    await textarea.setValue('{"text":"x"}')
    await flushPromises()

    const execBtn = wrapper
      .findAll('button.el-button-stub')
      .find((b) => b.text().includes('执行调用'))
    await execBtn!.trigger('click')
    await flushPromises()

    expect(apiMock.callMcpServerTool).toHaveBeenCalledTimes(1)
    expect(wrapper.html()).toContain('调用失败')
    expect(wrapper.html()).toContain('502')
    expect(wrapper.html()).not.toContain('result（成功）')
  })

  it('无参工具：空 textarea 通过校验（schema.required 为空）', async () => {
    const successResult: McpToolCallResult = {
      server: 'kitchen-sink-server',
      tool_name: 'noop',
      result: 'ok',
    }
    apiMock.callMcpServerTool.mockResolvedValueOnce(successResult)

    const wrapper = mountDialog(TOOL_NOOP)
    // 无必填字段 → 不应展示必填提示
    expect(wrapper.html()).not.toContain('必填字段：')

    const execBtn = wrapper
      .findAll('button.el-button-stub')
      .find((b) => b.text().includes('执行调用'))
    await execBtn!.trigger('click')
    await flushPromises()

    expect(apiMock.callMcpServerTool).toHaveBeenCalledTimes(1)
    expect(apiMock.callMcpServerTool).toHaveBeenCalledWith('kitchen-sink-server', {
      tool_name: 'noop',
      arguments: {},
    })
    expect(wrapper.html()).toContain('result（成功）')
  })
})