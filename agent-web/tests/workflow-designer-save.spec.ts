// @vitest-environment happy-dom
/**
 * WorkflowDesignerView save flow 集成测试（spec-21）：
 * - 保存按钮点击 → saveWorkflow 调用
 * - 本地校验失败 → 不调 API，inline 错误展示
 * - saveWorkflow 成功 → isDirty=false + notifySuccess；新建模式 → router.replace
 * - saveWorkflow 422 → inline 错误映射
 * - saveWorkflow 403 → 无 inline 错误，不崩溃
 * - beforeunload → isDirty 时 preventDefault
 * - readonly → 保存按钮隐藏
 */
import { describe, expect, it, vi, beforeEach } from 'vitest'
import { mount, flushPromises } from '@vue/test-utils'
import type { Component } from 'vue'

const {
  mockGetWorkflow,
  mockGetWorkflowCapabilities,
  mockSaveWorkflow,
  mockElMessage,
  mockElMessageBoxConfirm,
  mockNotifySuccess,
  mockNotifyError,
  routeState,
  mockRouterPush,
  mockRouterReplace,
  mockOnBeforeRouteLeave,
  paletteHandlers,
  NodePaletteStub,
  WorkflowCanvasStub,
  NodeConfigPanelStub,
  StateSchemaPanelStub,
  ConditionEdgeDialogStub,
} = vi.hoisted(() => {
  const mockGetWorkflow = vi.fn()
  const mockGetWorkflowCapabilities = vi.fn()
  const mockSaveWorkflow = vi.fn()
  const mockElMessage = vi.fn()
  const mockElMessageBoxConfirm = vi.fn()
  const mockNotifySuccess = vi.fn()
  const mockNotifyError = vi.fn()
  const mockRouterPush = vi.fn()
  const mockRouterReplace = vi.fn()
  const mockOnBeforeRouteLeave = vi.fn()

  const routeState: { name: string; params: Record<string, string> } = {
    name: 'workflow-design',
    params: { workflowId: 'wf1' },
  }

  const canvasProps: Record<string, unknown> = {}
  const canvasHandlers: Record<string, (...args: unknown[]) => void> = {}
  const WorkflowCanvasStub = {
    name: 'WorkflowCanvas',
    props: {
      nodes: { type: Array, default: () => [] },
      edges: { type: Array, default: () => [] },
      readonly: { type: Boolean, default: false },
    },
    emits: ['update:nodes', 'update:edges', 'select-node', 'connect', 'remove-node', 'remove-edge'],
    template: '<div class="workflow-canvas-stub" />',
    setup(p: Record<string, unknown>, { emit: e }: { emit: (...args: unknown[]) => void }) {
      Object.assign(canvasProps, p)
      canvasHandlers.connect = (edge: unknown) => e('connect', edge)
      canvasHandlers.selectNode = (id: unknown) => e('select-node', id)
      canvasHandlers.updateNodes = (nodes: unknown) => e('update:nodes', nodes)
      canvasHandlers.updateEdges = (edges: unknown) => e('update:edges', edges)
    },
  }

  const paletteHandlers: Record<string, (...args: unknown[]) => void> = {}
  const NodePaletteStub = {
    name: 'NodePalette',
    props: { readonly: { type: Boolean, default: false } },
    emits: ['add-node'],
    template: '<div class="node-palette-stub" />',
    setup(_: unknown, { emit: e }: { emit: (...args: unknown[]) => void }) {
      paletteHandlers.addNode = (payload: unknown) => e('add-node', payload)
    },
  }

  const configPanelHandlers: Record<string, (...args: unknown[]) => void> = {}
  const NodeConfigPanelStub = {
    name: 'NodeConfigPanel',
    props: {
      node: { type: Object, default: null },
      readonly: { type: Boolean, default: false },
    },
    emits: ['update:node', 'remove-node'],
    template: '<div class="node-config-panel-stub" />',
    setup(_: unknown, { emit: e }: { emit: (...args: unknown[]) => void }) {
      configPanelHandlers.updateNode = (patch: unknown) => e('update:node', patch)
      configPanelHandlers.removeNode = (id: unknown) => e('remove-node', id)
    },
  }

  const StateSchemaPanelStub = {
    name: 'StateSchemaPanel',
    props: {
      modelValue: { type: Object, default: () => ({}) },
      readonly: { type: Boolean, default: false },
    },
    emits: ['update:modelValue'],
    template: '<div class="state-schema-panel-stub" />',
  }

  const conditionDialogHandlers: Record<string, (...args: unknown[]) => void> = {}
  const ConditionEdgeDialogStub = {
    name: 'ConditionEdgeDialog',
    props: {
      modelValue: { type: Boolean, default: false },
      edge: { type: Object, required: true },
      nodeNames: { type: Array, default: () => [] },
      stateChannels: { type: Array, default: () => [] },
      readonly: { type: Boolean, default: false },
    },
    emits: ['update:modelValue', 'confirm'],
    template: '<div class="condition-edge-dialog-stub" />',
    setup(_: unknown, { emit: e }: { emit: (...args: unknown[]) => void }) {
      conditionDialogHandlers.confirm = (payload: unknown) => e('confirm', payload)
      conditionDialogHandlers.close = () => e('update:modelValue', false)
    },
  }

  return {
    mockGetWorkflow,
    mockGetWorkflowCapabilities,
    mockSaveWorkflow,
    mockElMessage,
    mockElMessageBoxConfirm,
    mockNotifySuccess,
    mockNotifyError,
    routeState,
    mockRouterPush,
    mockRouterReplace,
    mockOnBeforeRouteLeave,
    WorkflowCanvasStub,
    paletteHandlers,
    NodePaletteStub,
    NodeConfigPanelStub,
    StateSchemaPanelStub,
    ConditionEdgeDialogStub,
  }
})

vi.mock('@/api/workflow', () => ({
  getWorkflow: (...args: unknown[]) => mockGetWorkflow(...args),
  getWorkflowCapabilities: (...args: unknown[]) => mockGetWorkflowCapabilities(...args),
  saveWorkflow: (...args: unknown[]) => mockSaveWorkflow(...args),
}))

vi.mock('vue-router', () => ({
  useRoute: () => routeState,
  useRouter: () => ({ push: mockRouterPush, replace: mockRouterReplace }),
  onBeforeRouteLeave: (...args: unknown[]) => mockOnBeforeRouteLeave(...args),
}))

vi.mock('element-plus', () => ({
  ElMessage: mockElMessage,
  ElMessageBox: { confirm: (...args: unknown[]) => mockElMessageBoxConfirm(...args) },
}))

vi.mock('@/utils/notify', () => ({
  notifySuccess: (...args: unknown[]) => mockNotifySuccess(...args),
  notifyError: (...args: unknown[]) => mockNotifyError(...args),
}))

vi.mock('@/views/workflow/canvas/WorkflowCanvas.vue', () => ({
  default: WorkflowCanvasStub,
}))

vi.mock('@/views/workflow/canvas/NodePalette.vue', () => ({
  default: NodePaletteStub,
}))

vi.mock('@/views/workflow/panel/NodeConfigPanel.vue', () => ({
  default: NodeConfigPanelStub,
}))

vi.mock('@/views/workflow/panel/StateSchemaPanel.vue', () => ({
  default: StateSchemaPanelStub,
}))

vi.mock('@/views/workflow/canvas/ConditionEdgeDialog.vue', () => ({
  default: ConditionEdgeDialogStub,
}))

import WorkflowDesignerView from '@/views/workflow/WorkflowDesignerView.vue'
import type { WorkflowDefinitionDTO } from '@/api/workflow'

const sampleDTO: WorkflowDefinitionDTO = {
  workflow_id: 'wf1',
  entry_point: 'llm_1',
  nodes: [
    { name: 'llm_1', type: 'llm', config: { model_name: 'gpt-4o-mini' } },
  ],
  edges: [],
  state_schema: {},
  ui_layout: { nodes: {} },
}

function mountView() {
  return mount(WorkflowDesignerView as Component)
}

beforeEach(() => {
  vi.clearAllMocks()
  routeState.name = 'workflow-design'
  routeState.params = { workflowId: 'wf1' }
  mockGetWorkflow.mockReset()
  mockGetWorkflowCapabilities.mockReset()
  mockSaveWorkflow.mockReset()
  mockGetWorkflowCapabilities.mockResolvedValue({ can_edit: true })
  mockElMessageBoxConfirm.mockReset()
})

describe('WorkflowDesignerView save flow（spec-21）', () => {
  it('保存按钮点击（有效图）→ saveWorkflow 被调用', async () => {
    mockGetWorkflow.mockResolvedValue(sampleDTO)
    mockSaveWorkflow.mockResolvedValue(sampleDTO)

    const wrapper = mountView()
    await flushPromises()
    await wrapper.vm.$nextTick()

    const saveBtn = wrapper.find('[data-testid="save-button"]')
    expect(saveBtn.exists()).toBe(true)
    await saveBtn.trigger('click')
    await flushPromises()

    expect(mockSaveWorkflow).toHaveBeenCalledWith('wf1', expect.objectContaining({
      workflow_id: 'wf1',
      entry_point: 'llm_1',
    }))
  })

  it('本地校验失败（空 nodes）→ saveWorkflow 不调用，inline 错误展示', async () => {
    routeState.name = 'workflow-new-design'
    routeState.params = {}
    mockSaveWorkflow.mockResolvedValue(sampleDTO)

    const wrapper = mountView()
    await flushPromises()
    await wrapper.vm.$nextTick()

    const saveBtn = wrapper.find('[data-testid="save-button"]')
    await saveBtn.trigger('click')
    await wrapper.vm.$nextTick()

    expect(mockSaveWorkflow).not.toHaveBeenCalled()

    const errorBar = wrapper.find('[data-testid="field-errors"]')
    expect(errorBar.exists()).toBe(true)
    expect(errorBar.text()).toContain('node')
  })

  it('saveWorkflow 成功 → notifySuccess 调用；新建模式 → router.replace', async () => {
    routeState.name = 'workflow-new-design'
    routeState.params = {}
    mockSaveWorkflow.mockResolvedValue(sampleDTO)

    const wrapper = mountView()
    await flushPromises()
    await wrapper.vm.$nextTick()

    const wfIdInput = wrapper.find('[data-testid="workflow-id-input"]')
    await wfIdInput.setValue('wf1')
    await wrapper.vm.$nextTick()

    paletteHandlers.addNode({ type: 'llm', position: { x: 0, y: 0 } })
    await wrapper.vm.$nextTick()

    const entrySelect = wrapper.find('[data-testid="entry-point-select"]')
    await entrySelect.setValue('llm_1')
    await wrapper.vm.$nextTick()

    const saveBtn = wrapper.find('[data-testid="save-button"]')
    await saveBtn.trigger('click')
    await flushPromises()

    expect(mockNotifySuccess).toHaveBeenCalledWith('保存成功')
    expect(mockRouterReplace).toHaveBeenCalledWith({
      name: 'workflow-design',
      params: { workflowId: 'wf1' },
    })
  })

  it('saveWorkflow 422 → inline 错误展示', async () => {
    mockGetWorkflow.mockResolvedValue(sampleDTO)
    const error422 = new Error('Request failed') as Error & {
      isAxiosError: boolean
      response?: { status: number; data: unknown }
    }
    error422.isAxiosError = true
    error422.response = {
      status: 422,
      data: { code: 422, message: "invalid definition: Node 'llm_1' config error", data: null },
    }
    mockSaveWorkflow.mockRejectedValueOnce(error422)

    const wrapper = mountView()
    await flushPromises()
    await wrapper.vm.$nextTick()

    const saveBtn = wrapper.find('[data-testid="save-button"]')
    await saveBtn.trigger('click')
    await flushPromises()
    await wrapper.vm.$nextTick()

    const errorBar = wrapper.find('[data-testid="field-errors"]')
    expect(errorBar.exists()).toBe(true)
    expect(errorBar.text()).toContain('llm_1')
  })

  it('saveWorkflow 403 → 无 inline 错误，不崩溃', async () => {
    mockGetWorkflow.mockResolvedValue(sampleDTO)
    const error403 = new Error('Forbidden') as Error & {
      isAxiosError: boolean
      response?: { status: number; data: unknown }
    }
    error403.isAxiosError = true
    error403.response = {
      status: 403,
      data: { code: 403, message: 'workflow write requires admin', data: null },
    }
    mockSaveWorkflow.mockRejectedValueOnce(error403)

    const wrapper = mountView()
    await flushPromises()
    await wrapper.vm.$nextTick()

    const saveBtn = wrapper.find('[data-testid="save-button"]')
    await saveBtn.trigger('click')
    await flushPromises()
    await wrapper.vm.$nextTick()

    const errorBar = wrapper.find('[data-testid="field-errors"]')
    expect(errorBar.exists()).toBe(false)
  })

  it('isDirty=true + beforeunload → preventDefault 被调用', async () => {
    mockGetWorkflow.mockResolvedValue(sampleDTO)

    const wrapper = mountView()
    await flushPromises()
    await wrapper.vm.$nextTick()

    paletteHandlers.addNode({ type: 'llm', position: { x: 0, y: 0 } })
    await wrapper.vm.$nextTick()

    const event = new Event('beforeunload')
    const preventDefaultSpy = vi.spyOn(event, 'preventDefault')
    window.dispatchEvent(event)

    expect(preventDefaultSpy).toHaveBeenCalled()
  })

  it('readonly（canEdit=false）→ 保存按钮隐藏', async () => {
    mockGetWorkflow.mockResolvedValue(sampleDTO)
    mockGetWorkflowCapabilities.mockResolvedValue({ can_edit: false })

    const wrapper = mountView()
    await flushPromises()
    await wrapper.vm.$nextTick()

    const saveBtn = wrapper.find('[data-testid="save-button"]')
    expect(saveBtn.exists()).toBe(false)
  })

  it('isDirty=true + route leave → confirm 离开', async () => {
    mockGetWorkflow.mockResolvedValue(sampleDTO)
    mockElMessageBoxConfirm.mockResolvedValue('confirm')

    const wrapper = mountView()
    await flushPromises()
    await wrapper.vm.$nextTick()

    paletteHandlers.addNode({ type: 'llm', position: { x: 0, y: 0 } })
    await wrapper.vm.$nextTick()

    const guard = mockOnBeforeRouteLeave.mock.calls[0][0] as () => Promise<boolean>
    const result = await guard()

    expect(mockElMessageBoxConfirm).toHaveBeenCalled()
    expect(result).toBe(true)
  })

  it('isDirty=true + route leave → cancel 留下', async () => {
    mockGetWorkflow.mockResolvedValue(sampleDTO)
    mockElMessageBoxConfirm.mockRejectedValue('cancel')

    const wrapper = mountView()
    await flushPromises()
    await wrapper.vm.$nextTick()

    paletteHandlers.addNode({ type: 'llm', position: { x: 0, y: 0 } })
    await wrapper.vm.$nextTick()

    const guard = mockOnBeforeRouteLeave.mock.calls[0][0] as () => Promise<boolean>
    const result = await guard()

    expect(mockElMessageBoxConfirm).toHaveBeenCalled()
    expect(result).toBe(false)
  })

  it('isDirty=false + route leave → 直接离开，不弹确认', async () => {
    mockGetWorkflow.mockResolvedValue(sampleDTO)

    const wrapper = mountView()
    await flushPromises()
    await wrapper.vm.$nextTick()

    const guard = mockOnBeforeRouteLeave.mock.calls[0][0] as () => Promise<boolean>
    const result = await guard()

    expect(mockElMessageBoxConfirm).not.toHaveBeenCalled()
    expect(result).toBe(true)
  })
})
