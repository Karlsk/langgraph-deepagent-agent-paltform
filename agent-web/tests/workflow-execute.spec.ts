// @vitest-environment happy-dom
/**
 * WorkflowExecuteDialog + WorkflowTraceDrawer 测试（spec-07 / S21 前端侧）：
 * - stub Element Plus 组件；
 * - mock `@/api/workflow` 的 getWorkflow + executeWorkflow；
 * - 验证简单模式（主输入 + state_schema 驱动的自定义字段）、高级模式裸 JSON、
 *   提交形状、错误可见性、轨迹抽屉渲染。
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
    getWorkflow: vi.fn(),
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

const DEFINITION = {
  workflow_id: 'wf-test',
  entry_point: 'ask',
  nodes: [{ name: 'ask', type: 'llm' as const, config: {} }],
  edges: [{ source: 'ask', target: 'END' }],
  state_schema: {
    input: { type: 'str', description: '用户输入', reducer: null },
    messages: { type: 'list', description: '对话消息', reducer: null },
    history: { type: 'list', description: '执行历史', reducer: null },
    user_id: { type: 'str', description: '调用方标识', reducer: null },
    max_tokens: { type: 'int', description: '上限', reducer: null },
    ask_result: { type: 'any', description: '', reducer: null },
  },
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

function findButton(wrapper: VueWrapper, text: string) {
  const button = wrapper.findAllComponents(ElButtonStub).find((b) => b.text().includes(text))
  expect(button, `expected a button containing ${text}`).toBeDefined()
  return button!
}

async function submit(wrapper: VueWrapper): Promise<void> {
  await findButton(wrapper, '执行').trigger('click')
  await flushPromises()
}

function makeAxiosError(status: number, data: unknown): Error {
  const error = new Error('Request failed with status code ' + status) as Error & {
    isAxiosError: boolean
    response?: { status: number; data: unknown }
  }
  error.isAxiosError = true
  error.response = { status, data }
  return error
}

beforeEach(() => {
  vi.clearAllMocks()
  elMessageMock.mockReset()
  apiMock.executeWorkflow.mockResolvedValue(EXECUTE_RESULT)
  apiMock.getWorkflow.mockResolvedValue(DEFINITION)
})

describe('WorkflowExecuteDialog — 简单模式（S21）', () => {
  it('打开对话框 → 拉取定义，渲染主输入与自定义字段', async () => {
    const wrapper = mountDialog()
    await flushPromises()

    expect(apiMock.getWorkflow).toHaveBeenCalledWith('wf-test', 'json')
    expect(wrapper.find('.execute-dialog__primary .el-input-stub').exists()).toBe(true)
    expect(wrapper.text()).not.toContain('预设示例')
  })

  it('自定义字段来自 state_schema，排除保留键', async () => {
    const wrapper = mountDialog()
    await flushPromises()

    const rows = wrapper.findAll('.execute-field')
    expect(rows).toHaveLength(2)
    expect(rows[0].text()).toContain('user_id')
    expect(rows[1].text()).toContain('max_tokens')

    const text = wrapper.text()
    expect(text).not.toContain('ask_result')
    expect(wrapper.findAll('.execute-field__label').map((l) => l.text())).not.toContain('messages')
  })

  it('主输入 + 自定义字段 → executeWorkflow(id, {input, user_id})', async () => {
    const wrapper = mountDialog()
    await flushPromises()

    await wrapper.find('.execute-dialog__primary .el-input-stub').setValue('hello')
    await wrapper.find('.execute-field__text').setValue('u-001')
    await submit(wrapper)

    expect(apiMock.executeWorkflow).toHaveBeenCalledWith('wf-test', {
      input: 'hello',
      user_id: 'u-001',
    })
  })

  it('主输入留空 → payload 不含 input 键', async () => {
    const wrapper = mountDialog()
    await flushPromises()

    await wrapper.find('.execute-field__text').setValue('u-001')
    await submit(wrapper)

    expect(apiMock.executeWorkflow).toHaveBeenCalledWith('wf-test', { user_id: 'u-001' })
  })

  it('数字字段非法 → 拦截并标红，不调 executeWorkflow', async () => {
    const wrapper = mountDialog()
    await flushPromises()

    await wrapper.find('.execute-dialog__primary .el-input-stub').setValue('hello')
    await wrapper.find('.execute-field__number').setValue('not-a-number')
    await submit(wrapper)

    expect(apiMock.executeWorkflow).not.toHaveBeenCalled()
    expect(wrapper.find('.execute-field__error').exists()).toBe(true)
  })

  it('定义拉取失败 → 简单模式仍可只填主输入执行', async () => {
    apiMock.getWorkflow.mockRejectedValueOnce(new Error('network down'))
    const wrapper = mountDialog()
    await flushPromises()

    expect(wrapper.findAll('.execute-field')).toHaveLength(0)
    await wrapper.find('.execute-dialog__primary .el-input-stub').setValue('hello')
    await submit(wrapper)

    expect(apiMock.executeWorkflow).toHaveBeenCalledWith('wf-test', { input: 'hello' })
  })
})

describe('WorkflowExecuteDialog — 高级模式', () => {
  async function switchToAdvanced(wrapper: VueWrapper): Promise<void> {
    await wrapper.find('input[value="advanced"]').setValue(true)
    await nextTick()
  }

  it('切换到高级模式 → 裸 JSON 文本框，自定义字段隐藏', async () => {
    const wrapper = mountDialog()
    await flushPromises()
    await switchToAdvanced(wrapper)

    expect(wrapper.find('.execute-dialog__advanced .el-input-stub').exists()).toBe(true)
    expect(wrapper.findAll('.execute-field')).toHaveLength(0)
  })

  it('合法 JSON → 原样透传给 executeWorkflow', async () => {
    const wrapper = mountDialog()
    await flushPromises()
    await switchToAdvanced(wrapper)

    const payload = { input: 'hi', messages: [{ role: 'user', content: 'hi' }] }
    await wrapper.find('.execute-dialog__advanced .el-input-stub').setValue(JSON.stringify(payload))
    await submit(wrapper)

    expect(apiMock.executeWorkflow).toHaveBeenCalledWith('wf-test', payload)
  })

  it('非法 JSON → 前端拦截，展示校验错误，不调 executeWorkflow', async () => {
    const wrapper = mountDialog()
    await flushPromises()
    await switchToAdvanced(wrapper)

    await wrapper.find('.execute-dialog__advanced .el-input-stub').setValue('{invalid json}')
    await submit(wrapper)

    expect(apiMock.executeWorkflow).not.toHaveBeenCalled()
    expect(wrapper.text()).toMatch(/json|格式|校验|非法|解析/i)
  })

  it('切回简单模式 → 恢复主输入与自定义字段', async () => {
    const wrapper = mountDialog()
    await flushPromises()
    await switchToAdvanced(wrapper)
    await wrapper.find('input[value="simple"]').setValue(true)
    await nextTick()

    expect(wrapper.find('.execute-dialog__primary .el-input-stub').exists()).toBe(true)
    expect(wrapper.findAll('.execute-field')).toHaveLength(2)
  })
})

describe('WorkflowExecuteDialog — 结果与错误', () => {
  it('执行成功 → 渲染 output 与耗时', async () => {
    const wrapper = mountDialog()
    await flushPromises()

    await wrapper.find('.execute-dialog__primary .el-input-stub').setValue('hello')
    await submit(wrapper)

    expect(wrapper.text()).toContain('done')
    expect(wrapper.text()).toContain('520')
  })

  it('执行失败 → 对话框内展示后端错误摘要，不崩', async () => {
    apiMock.executeWorkflow.mockRejectedValue(
      makeAxiosError(500, {
        code: 500,
        message: "workflow execution failed for 'wf-test': ConditionNotMatchedError: no branch matched",
        data: null,
      }),
    )
    const wrapper = mountDialog()
    await flushPromises()

    await wrapper.find('.execute-dialog__primary .el-input-stub').setValue('hello')
    await submit(wrapper)

    expect(wrapper.find('.el-dialog-stub').exists()).toBe(true)
    expect(wrapper.find('.execute-dialog__error').text()).toContain('ConditionNotMatchedError')
  })

  it('执行成功后点击「查看轨迹」→ 打开 WorkflowTraceDrawer', async () => {
    const wrapper = mountDialog()
    await flushPromises()

    await wrapper.find('.execute-dialog__primary .el-input-stub').setValue('hello')
    await submit(wrapper)

    await findButton(wrapper, '轨迹').trigger('click')
    await flushPromises()

    expect(wrapper.findComponent({ name: 'ElDrawer' }).exists()).toBe(true)
  })

  it('再次提交校验失败 → 清除上一次结果，避免把旧输出误读为本次结果', async () => {
    const wrapper = mountDialog()
    await flushPromises()

    await wrapper.find('.execute-dialog__primary .el-input-stub').setValue('hello')
    await submit(wrapper)
    expect(wrapper.find('.execute-dialog__result').exists()).toBe(true)

    await wrapper.find('input[value="advanced"]').setValue(true)
    await nextTick()
    await wrapper.find('.execute-dialog__advanced .el-input-stub').setValue('{invalid json}')
    await submit(wrapper)

    expect(wrapper.find('.execute-dialog__json-error').exists()).toBe(true)
    expect(wrapper.find('.execute-dialog__result').exists()).toBe(false)
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
