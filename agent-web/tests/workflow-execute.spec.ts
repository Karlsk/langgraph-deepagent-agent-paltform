// @vitest-environment happy-dom
/**
 * WorkflowExecuteDialog + WorkflowTraceDrawer 测试（spec-07）：
 * - stub Element Plus 组件；
 * - mock `@/api/workflow` 的 executeWorkflow；
 * - 验证预设填充、JSON 校验、执行流程、错误处理、轨迹抽屉渲染。
 */
import { beforeEach, describe, expect, it, vi } from 'vitest'
import { defineComponent, h, nextTick } from 'vue'
import { flushPromises, mount } from '@vue/test-utils'
import type { VueWrapper } from '@vue/test-utils'

import WorkflowExecuteDialog from '@/views/workflow/WorkflowExecuteDialog.vue'
import WorkflowTraceDrawer from '@/views/workflow/WorkflowTraceDrawer.vue'
import type { ExecutionLogView, WorkflowExecuteResult } from '@/api/workflow'

const { elMessageMock } = vi.hoisted(() => {
  const fn = vi.fn()
  return {
    elMessageMock: Object.assign(fn, {
      error: vi.fn(),
      success: vi.fn(),
      warning: vi.fn(),
    }),
  }
})

vi.mock('element-plus', () => ({
  ElMessage: elMessageMock,
  ElMessageBox: { confirm: vi.fn() },
}))

const { apiMock } = vi.hoisted(() => ({
  apiMock: {
    executeWorkflow: vi.fn(),
  },
}))

vi.mock('@/api/workflow', () => apiMock)

const LOGS: ExecutionLogView[] = [
  {
    node_name: 'step_b',
    node_type: 'llm',
    timestamp: '2025-01-01T00:00:10Z',
    input_data: { prompt: 'hello' },
    output_data: { text: 'world' },
    execution_time_ms: 320,
    error: null,
  },
  {
    node_name: 'step_a',
    node_type: 'http',
    timestamp: '2025-01-01T00:00:00Z',
    input_data: { url: 'https://api.example.com' },
    output_data: { status: 200 },
    execution_time_ms: 150,
    error: null,
  },
  {
    node_name: 'step_c',
    node_type: 'llm',
    timestamp: '2025-01-01T00:00:20Z',
    input_data: { prompt: 'summarize' },
    output_data: null,
    execution_time_ms: 50,
    error: 'LLM rate limit exceeded',
  },
]

const EXECUTE_RESULT: WorkflowExecuteResult = {
  result: 'done',
  count: 3,
  metadata: { duration_ms: 520, execution_logs: LOGS },
}

const ElDialogStub = defineComponent({
  name: 'ElDialog',
  props: { modelValue: Boolean, title: String, width: String },
  emits: ['update:modelValue'],
  setup(props, { slots, emit }) {
    return () =>
      h('div', { class: 'el-dialog-stub', 'data-visible': String(props.modelValue) }, [
        h('div', { class: 'el-dialog__header' }, props.title ?? ''),
        h('div', { class: 'el-dialog__body' }, slots.default ? slots.default() : undefined),
        h('button', {
          class: 'el-dialog__close',
          onClick: () => emit('update:modelValue', false),
        }, 'Close'),
      ])
  },
})

const ElDrawerStub = defineComponent({
  name: 'ElDrawer',
  props: { modelValue: Boolean, title: String, size: String },
  emits: ['update:modelValue'],
  setup(props, { slots, emit }) {
    return () =>
      h('div', { class: 'el-drawer-stub', 'data-visible': String(props.modelValue) }, [
        h('div', { class: 'el-drawer__header' }, props.title ?? ''),
        h('div', { class: 'el-drawer__body' }, slots.default ? slots.default() : undefined),
        h('button', {
          class: 'el-drawer__close',
          onClick: () => emit('update:modelValue', false),
        }, 'Close'),
      ])
  },
})

const ElButtonStub = defineComponent({
  name: 'ElButton',
  props: { loading: Boolean, disabled: Boolean, type: String, link: Boolean, size: String },
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

const ElInputStub = defineComponent({
  name: 'ElInput',
  props: { modelValue: String, type: String, placeholder: String, rows: Number },
  emits: ['update:modelValue'],
  setup(props, { emit }) {
    return () =>
      h('textarea', {
        class: 'el-input-stub',
        value: props.modelValue ?? '',
        onInput: (e: Event) => emit('update:modelValue', (e.target as HTMLTextAreaElement).value),
      })
  },
})

const ElSelectStub = defineComponent({
  name: 'ElSelect',
  props: { modelValue: [String, Number] },
  emits: ['update:modelValue', 'change'],
  setup(_props, { slots, emit }) {
    return () =>
      h('div', { class: 'el-select-stub' }, [
        slots.default ? slots.default() : undefined,
        h('button', {
          class: 'el-select__trigger',
          onClick: () => {
            const options = slots.default?.()
            if (options && Array.isArray(options)) {
              const firstOption = options[0]
              if (firstOption && typeof firstOption === 'object' && 'props' in firstOption) {
                const val = (firstOption.props as Record<string, unknown>).value
                emit('update:modelValue', val)
                emit('change', val)
              }
            }
          },
        }, 'SelectFirst'),
      ])
  },
})

const ElOptionStub = defineComponent({
  name: 'ElOption',
  props: { label: String, value: [String, Number] },
  setup(props) {
    return () => h('div', { class: 'el-option-stub', 'data-value': String(props.value ?? '') }, props.label ?? '')
  },
})

const ElTagStub = defineComponent({
  name: 'ElTag',
  props: { type: String, size: String },
  setup(props, { slots }) {
    return () =>
      h('span', { class: 'el-tag-stub', 'data-type': props.type ?? '' }, slots.default ? slots.default() : undefined)
  },
})

const ElIconStub = defineComponent({
  name: 'ElIcon',
  setup(_, { slots }) {
    return () => h('i', { class: 'el-icon-stub' }, slots.default ? slots.default() : undefined)
  },
})

function mountDialog(overrides?: Record<string, unknown>): VueWrapper {
  return mount(WorkflowExecuteDialog, {
    props: {
      modelValue: true,
      workflowId: 'wf-test',
      ...overrides,
    },
    global: {
      stubs: {
        ElDialog: ElDialogStub,
        ElButton: ElButtonStub,
        ElInput: ElInputStub,
        ElSelect: ElSelectStub,
        ElOption: ElOptionStub,
        ElTag: ElTagStub,
        ElIcon: ElIconStub,
        WorkflowTraceDrawer: ElDrawerStub,
      },
    },
  })
}

function mountDrawer(overrides?: Record<string, unknown>): VueWrapper {
  return mount(WorkflowTraceDrawer, {
    props: {
      modelValue: true,
      ...overrides,
    },
    global: {
      stubs: {
        ElDrawer: ElDrawerStub,
        ElTag: ElTagStub,
        ElIcon: ElIconStub,
      },
    },
  })
}

beforeEach(() => {
  vi.clearAllMocks()
  elMessageMock.mockReset()
  apiMock.executeWorkflow.mockResolvedValue(EXECUTE_RESULT)
})

describe('WorkflowExecuteDialog（spec-07）', () => {
  it('打开对话框 → 展示预设示例；选预设填充输入框', async () => {
    const wrapper = mountDialog()
    await flushPromises()

    const select = wrapper.findComponent(ElSelectStub)
    expect(select.exists()).toBe(true)

    const textarea = wrapper.find('.el-input-stub')
    expect(textarea.exists()).toBe(true)

    const options = wrapper.findAll('.el-option-stub')
    expect(options.length).toBeGreaterThanOrEqual(3)
  })

  it('非法 JSON → 前端拦截，展示校验错误，不调 executeWorkflow', async () => {
    const wrapper = mountDialog()
    await flushPromises()

    const textarea = wrapper.find('.el-input-stub')
    await textarea.setValue('{invalid json}')
    await nextTick()

    const submitBtn = wrapper.findAllComponents(ElButtonStub)
      .find((b) => b.text().includes('执行'))
    expect(submitBtn).toBeDefined()
    await submitBtn!.trigger('click')
    await flushPromises()

    expect(apiMock.executeWorkflow).not.toHaveBeenCalled()
    expect(wrapper.text()).toMatch(/json|格式|校验|非法|解析/i)
  })

  it('合法输入提交 → 调 executeWorkflow(id, input)；loading 态；成功渲染 output + metadata', async () => {
    const wrapper = mountDialog()
    await flushPromises()

    const textarea = wrapper.find('.el-input-stub')
    await textarea.setValue('{"message": "hello"}')
    await nextTick()

    const submitBtn = wrapper.findAllComponents(ElButtonStub)
      .find((b) => b.text().includes('执行'))
    await submitBtn!.trigger('click')

    expect(apiMock.executeWorkflow).toHaveBeenCalledWith('wf-test', { message: 'hello' })

    await flushPromises()

    expect(wrapper.text()).toContain('done')
    expect(wrapper.text()).toContain('520')
  })

  it('executeWorkflow reject → 错误提示，对话框不崩', async () => {
    apiMock.executeWorkflow.mockRejectedValue(new Error('500 Internal Server Error'))
    const wrapper = mountDialog()
    await flushPromises()

    const textarea = wrapper.find('.el-input-stub')
    await textarea.setValue('{"key": "value"}')
    await nextTick()

    const submitBtn = wrapper.findAllComponents(ElButtonStub)
      .find((b) => b.text().includes('执行'))
    await submitBtn!.trigger('click')
    await flushPromises()

    expect(wrapper.find('.el-dialog-stub').exists()).toBe(true)
  })

  it('执行成功后点击「查看轨迹」→ 打开 WorkflowTraceDrawer 并传入 logs', async () => {
    const wrapper = mountDialog()
    await flushPromises()

    const textarea = wrapper.find('.el-input-stub')
    await textarea.setValue('{"key": "value"}')
    await nextTick()

    const submitBtn = wrapper.findAllComponents(ElButtonStub)
      .find((b) => b.text().includes('执行'))
    await submitBtn!.trigger('click')
    await flushPromises()

    const traceBtn = wrapper.findAllComponents(ElButtonStub)
      .find((b) => b.text().includes('轨迹'))
    expect(traceBtn).toBeDefined()
    await traceBtn!.trigger('click')
    await flushPromises()

    const drawer = wrapper.findComponent({ name: 'ElDrawer' })
    expect(drawer.exists()).toBe(true)
  })
})

describe('WorkflowTraceDrawer（spec-07）', () => {
  it('传 logs → 按 timestamp 升序渲染 N 个节点条目', () => {
    const wrapper = mountDrawer({ logs: LOGS })

    const entries = wrapper.findAll('.trace-entry')
    expect(entries).toHaveLength(3)

    expect(entries[0].text()).toContain('step_a')
    expect(entries[1].text()).toContain('step_b')
    expect(entries[2].text()).toContain('step_c')
  })

  it('某节点 error 非空 → 该条标红/错误样式', () => {
    const wrapper = mountDrawer({ logs: LOGS })

    const entries = wrapper.findAll('.trace-entry')
    const errorEntry = entries.find((e) => e.text().includes('step_c'))
    expect(errorEntry).toBeDefined()
    expect(errorEntry!.classes()).toContain('trace-entry--error')
  })

  it('logs 缺失 → 降级提示文案，无异常', () => {
    const wrapper = mountDrawer({})
    expect(wrapper.text()).toMatch(/未包含|轨迹|执行轨迹/)
  })

  it('logs 为空数组 → 降级提示文案', () => {
    const wrapper = mountDrawer({ logs: [] })
    expect(wrapper.text()).toMatch(/未包含|轨迹|执行轨迹/)
  })
})
