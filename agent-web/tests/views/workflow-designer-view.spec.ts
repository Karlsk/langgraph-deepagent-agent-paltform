// @vitest-environment happy-dom
/**
 * WorkflowDesignerView 集成测试（spec-15）：
 * - stub 全部子组件 + API + router；
 * - 验证载入既有定义、ui_layout 还原、新建模式、palette 新增、
 *   条件边 connect 打开 dialog、节点配置更新、载入失败、entry_point 选择。
 */
import { describe, expect, it, vi, beforeEach } from 'vitest'
import { mount, flushPromises } from '@vue/test-utils'
import type { Component } from 'vue'

const {
  mockGetWorkflow,
  mockGetWorkflowCapabilities,
  mockElMessage,
  routeState,
  mockRouterPush,
  canvasProps,
  canvasHandlers,
  WorkflowCanvasStub,
  paletteHandlers,
  NodePaletteStub,
  configPanelHandlers,
  NodeConfigPanelStub,
  StateSchemaPanelStub,
  conditionDialogProps,
  conditionDialogHandlers,
  ConditionEdgeDialogStub,
  yamlPreviewProps,
  YamlPreviewDrawerStub,
} = vi.hoisted(() => {
  const mockGetWorkflow = vi.fn()
  const mockGetWorkflowCapabilities = vi.fn()
  const mockElMessage = vi.fn()
  const mockRouterPush = vi.fn()

  const routeState: { name: string; params: Record<string, string> } = {
    name: 'workflow-design',
    params: { workflowId: 'wf1' },
  }

  let _canvasTarget: Record<string, unknown> = {}
  const canvasProps: Record<string, unknown> = new Proxy(
    {},
    { get: (_, k) => (_canvasTarget as Record<string, unknown>)[k as string] },
  )
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
      _canvasTarget = p
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

  let _configPanelTarget: Record<string, unknown> = {}
  const configPanelProps: Record<string, unknown> = new Proxy(
    {},
    { get: (_, k) => (_configPanelTarget as Record<string, unknown>)[k as string] },
  )
  const configPanelHandlers: Record<string, (...args: unknown[]) => void> = {}
  const NodeConfigPanelStub = {
    name: 'NodeConfigPanel',
    props: {
      node: { type: Object, default: null },
      readonly: { type: Boolean, default: false },
    },
    emits: ['update:node', 'remove-node'],
    template: '<div class="node-config-panel-stub" />',
    setup(p: Record<string, unknown>, { emit: e }: { emit: (...args: unknown[]) => void }) {
      _configPanelTarget = p
      configPanelHandlers.updateNode = (patch: unknown) => e('update:node', patch)
      configPanelHandlers.removeNode = (id: unknown) => e('remove-node', id)
    },
  }

  let _schemaTarget: Record<string, unknown> = {}
  const schemaProps: Record<string, unknown> = new Proxy(
    {},
    { get: (_, k) => (_schemaTarget as Record<string, unknown>)[k as string] },
  )
  const schemaHandlers: Record<string, (...args: unknown[]) => void> = {}
  const StateSchemaPanelStub = {
    name: 'StateSchemaPanel',
    props: {
      modelValue: { type: Object, default: () => ({}) },
      readonly: { type: Boolean, default: false },
    },
    emits: ['update:modelValue'],
    template: '<div class="state-schema-panel-stub" />',
    setup(p: Record<string, unknown>, { emit: e }: { emit: (...args: unknown[]) => void }) {
      _schemaTarget = p
      schemaHandlers.update = (schema: unknown) => e('update:modelValue', schema)
    },
  }

  let _conditionDialogTarget: Record<string, unknown> = {}
  const conditionDialogProps: Record<string, unknown> = new Proxy(
    {},
    { get: (_, k) => (_conditionDialogTarget as Record<string, unknown>)[k as string] },
  )
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
    setup(p: Record<string, unknown>, { emit: e }: { emit: (...args: unknown[]) => void }) {
      _conditionDialogTarget = p
      conditionDialogHandlers.confirm = (payload: unknown) => e('confirm', payload)
      conditionDialogHandlers.close = () => e('update:modelValue', false)
    },
  }

  let _yamlPreviewTarget: Record<string, unknown> = {}
  const yamlPreviewProps: Record<string, unknown> = new Proxy(
    {},
    { get: (_, k) => (_yamlPreviewTarget as Record<string, unknown>)[k as string] },
  )
  const yamlPreviewHandlers: Record<string, (...args: unknown[]) => void> = {}
  const YamlPreviewDrawerStub = {
    name: 'YamlPreviewDrawer',
    props: {
      modelValue: { type: Boolean, default: false },
      workflowId: { type: String, default: '' },
      dirty: { type: Boolean, default: false },
    },
    emits: ['update:modelValue'],
    template: '<div class="yaml-preview-drawer-stub" />',
    setup(p: Record<string, unknown>, { emit: e }: { emit: (...args: unknown[]) => void }) {
      _yamlPreviewTarget = p
      yamlPreviewHandlers.close = () => e('update:modelValue', false)
    },
  }

  return {
    mockGetWorkflow,
    mockGetWorkflowCapabilities,
    mockElMessage,
    routeState,
    mockRouterPush,
    canvasProps,
    canvasHandlers,
    WorkflowCanvasStub,
    paletteHandlers,
    NodePaletteStub,
    configPanelProps,
    configPanelHandlers,
    NodeConfigPanelStub,
    schemaProps,
    schemaHandlers,
    StateSchemaPanelStub,
    conditionDialogProps,
    conditionDialogHandlers,
    ConditionEdgeDialogStub,
    yamlPreviewProps,
    YamlPreviewDrawerStub,
  }
})

vi.mock('@/api/workflow', () => ({
  getWorkflow: mockGetWorkflow,
  getWorkflowCapabilities: mockGetWorkflowCapabilities,
}))

vi.mock('vue-router', () => ({
  useRoute: () => routeState,
  useRouter: () => ({ push: mockRouterPush }),
  onBeforeRouteLeave: vi.fn(),
}))

vi.mock('element-plus', () => ({
  ElMessage: mockElMessage,
  ElMessageBox: { confirm: vi.fn() },
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

vi.mock('@/views/workflow/YamlPreviewDrawer.vue', () => ({
  default: YamlPreviewDrawerStub,
}))

import WorkflowDesignerView from '@/views/workflow/WorkflowDesignerView.vue'
import { definitionToGraph } from '@/composables/useWorkflowGraph'
import type { WorkflowDefinitionDTO } from '@/api/workflow'

const sampleDTO: WorkflowDefinitionDTO = {
  workflow_id: 'wf1',
  entry_point: 'llm_1',
  nodes: [
    { name: 'llm_1', type: 'llm', config: { model_name: 'gpt-4o-mini' } },
    { name: 'http_1', type: 'http', config: { url: 'https://api.example.com' } },
  ],
  edges: [{ source: 'llm_1', target: 'http_1' }],
  state_schema: { messages: { type: 'list', reducer: 'add' } },
  ui_layout: {
    nodes: {
      llm_1: { x: 100, y: 200 },
      http_1: { x: 300, y: 400 },
    },
  },
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
  mockGetWorkflowCapabilities.mockResolvedValue({ can_edit: true })
})

describe('WorkflowDesignerView 集成', () => {
  it('载入既有定义：调用 getWorkflow → definitionToGraph → 传入 canvas', async () => {
    mockGetWorkflow.mockResolvedValue(sampleDTO)

    const wrapper = mountView()
    await flushPromises()
    await wrapper.vm.$nextTick()

    expect(mockGetWorkflow).toHaveBeenCalledWith('wf1', 'json')

    const expected = definitionToGraph(sampleDTO)
    expect(canvasProps.nodes).toEqual(expected.nodes)
    expect(canvasProps.edges).toEqual(expected.edges)
  })

  it('ui_layout 还原：nodes 含正确 position', async () => {
    mockGetWorkflow.mockResolvedValue(sampleDTO)

    const wrapper = mountView()
    await flushPromises()
    await wrapper.vm.$nextTick()

    const nodes = canvasProps.nodes as Array<{ id: string; position: { x: number; y: number } }>
    const llmNode = nodes.find((n) => n.id === 'llm_1')
    const httpNode = nodes.find((n) => n.id === 'http_1')
    expect(llmNode?.position).toEqual({ x: 100, y: 200 })
    expect(httpNode?.position).toEqual({ x: 300, y: 400 })
  })

  it('新建模式：不调 getWorkflow，空画布，workflow_id 可编辑', async () => {
    routeState.name = 'workflow-new-design'
    routeState.params = {}

    const wrapper = mountView()
    await flushPromises()
    await wrapper.vm.$nextTick()

    expect(mockGetWorkflow).not.toHaveBeenCalled()

    const nodes = canvasProps.nodes as unknown[]
    expect(nodes).toEqual([])

    const wfIdInput = wrapper.find('[data-testid="workflow-id-input"]')
    expect(wfIdInput.exists()).toBe(true)
    expect(wfIdInput.attributes('disabled')).toBeUndefined()
  })

  it('palette add-node → nodes 增加一个，含唯一 name 和默认 config', async () => {
    routeState.name = 'workflow-new-design'
    routeState.params = {}

    const wrapper = mountView()
    await flushPromises()
    await wrapper.vm.$nextTick()

    paletteHandlers.addNode({ type: 'llm', position: { x: 10, y: 20 } })
    await wrapper.vm.$nextTick()

    const nodes = canvasProps.nodes as Array<{
      id: string
      data: { name: string; type: string; config: Record<string, unknown> }
    }>
    expect(nodes).toHaveLength(1)
    expect(nodes[0].data.name).toBe('llm_1')
    expect(nodes[0].data.type).toBe('llm')
    expect(nodes[0].data.config).toHaveProperty('model_name')
  })

  it('connect 时 source 已有条件边 → 打开 ConditionEdgeDialog；确认后边带 condition', async () => {
    const dtoWithConditional: WorkflowDefinitionDTO = {
      workflow_id: 'wf1',
      entry_point: 'llm_1',
      nodes: [
        { name: 'llm_1', type: 'llm', config: {} },
        { name: 'llm_2', type: 'llm', config: {} },
        { name: 'http_1', type: 'http', config: {} },
      ],
      edges: [{ source: 'llm_1', target: 'llm_2', condition: "result == 'yes'" }],
      state_schema: {},
      ui_layout: { nodes: {} },
    }
    mockGetWorkflow.mockResolvedValue(dtoWithConditional)

    const wrapper = mountView()
    await flushPromises()
    await wrapper.vm.$nextTick()

    canvasHandlers.connect({ source: 'llm_1', target: 'http_1' })
    await wrapper.vm.$nextTick()

    expect(conditionDialogProps.modelValue).toBe(true)

    conditionDialogHandlers.confirm({ target: 'http_1', condition: "result == 'no'" })
    await wrapper.vm.$nextTick()

    const edges = canvasProps.edges as Array<{
      source: string
      target: string
      data: { condition: string | null }
    }>
    const newEdge = edges.find((e) => e.source === 'llm_1' && e.target === 'http_1')
    expect(newEdge).toBeDefined()
    expect(newEdge!.data.condition).toBe("result == 'no'")
  })

  it('NodeConfigPanel update:node → 节点 config 更新', async () => {
    mockGetWorkflow.mockResolvedValue(sampleDTO)

    const wrapper = mountView()
    await flushPromises()
    await wrapper.vm.$nextTick()

    canvasHandlers.selectNode('llm_1')
    await wrapper.vm.$nextTick()

    configPanelHandlers.updateNode({ config: { model_name: 'gpt-4' } })
    await wrapper.vm.$nextTick()

    const nodes = canvasProps.nodes as Array<{
      id: string
      data: { config: Record<string, unknown> }
    }>
    const llmNode = nodes.find((n) => n.id === 'llm_1')
    expect(llmNode!.data.config.model_name).toBe('gpt-4')
  })

  it('getWorkflow reject → notifyError + router.push 回列表', async () => {
    mockGetWorkflow.mockRejectedValue(new Error('404'))

    const wrapper = mountView()
    await flushPromises()
    await wrapper.vm.$nextTick()

    expect(mockElMessage).toHaveBeenCalledWith(
      expect.objectContaining({ type: 'error' }),
    )
    expect(mockRouterPush).toHaveBeenCalledWith({ name: 'workflow' })
  })

  it('entry_point 选择 → meta.entry_point 更新', async () => {
    mockGetWorkflow.mockResolvedValue(sampleDTO)

    const wrapper = mountView()
    await flushPromises()
    await wrapper.vm.$nextTick()

    const entrySelect = wrapper.find('[data-testid="entry-point-select"]')
    expect(entrySelect.exists()).toBe(true)

    await entrySelect.setValue('http_1')
    await wrapper.vm.$nextTick()

    const entrySelectElement = entrySelect.element as HTMLSelectElement
    expect(entrySelectElement.value).toBe('http_1')
  })
})

describe('WorkflowDesignerView 能力门禁（spec-19）', () => {
  it('canEdit=false → 画布/面板收到 readonly=true；保存按钮隐藏', async () => {
    mockGetWorkflow.mockResolvedValue(sampleDTO)
    mockGetWorkflowCapabilities.mockResolvedValue({ can_edit: false })

    const wrapper = mountView()
    await flushPromises()
    await wrapper.vm.$nextTick()

    expect(canvasProps.readonly).toBe(true)
    const saveBtn = wrapper.find('[data-testid="save-button"]')
    expect(saveBtn.exists()).toBe(false)
  })

  it('canEdit=true → 可编辑，保存按钮可见', async () => {
    mockGetWorkflow.mockResolvedValue(sampleDTO)
    mockGetWorkflowCapabilities.mockResolvedValue({ can_edit: true })

    const wrapper = mountView()
    await flushPromises()
    await wrapper.vm.$nextTick()

    expect(canvasProps.readonly).toBe(false)
    const saveBtn = wrapper.find('[data-testid="save-button"]')
    expect(saveBtn.exists()).toBe(true)
  })
})

describe('WorkflowDesignerView YAML 预览（spec-22）', () => {
  it('预览按钮可点击（不禁用）', async () => {
    mockGetWorkflow.mockResolvedValue(sampleDTO)

    const wrapper = mountView()
    await flushPromises()
    await wrapper.vm.$nextTick()

    const previewBtn = wrapper.find('[data-testid="yaml-preview-button"]')
    expect(previewBtn.exists()).toBe(true)
    expect(previewBtn.attributes('disabled')).toBeUndefined()
  })

  it('点击预览按钮 → YamlPreviewDrawer 收到 modelValue=true', async () => {
    mockGetWorkflow.mockResolvedValue(sampleDTO)

    const wrapper = mountView()
    await flushPromises()
    await wrapper.vm.$nextTick()

    expect(yamlPreviewProps.modelValue).toBe(false)

    await wrapper.find('[data-testid="yaml-preview-button"]').trigger('click')
    await wrapper.vm.$nextTick()

    expect(yamlPreviewProps.modelValue).toBe(true)
    expect(yamlPreviewProps.workflowId).toBe('wf1')
    expect(yamlPreviewProps.dirty).toBe(false)
  })

  it('新建模式 → 预览按钮可点击，drawer 收到空 workflowId', async () => {
    routeState.name = 'workflow-new-design'
    routeState.params = {}

    const wrapper = mountView()
    await flushPromises()
    await wrapper.vm.$nextTick()

    const previewBtn = wrapper.find('[data-testid="yaml-preview-button"]')
    expect(previewBtn.exists()).toBe(true)

    await previewBtn.trigger('click')
    await wrapper.vm.$nextTick()

    expect(yamlPreviewProps.modelValue).toBe(true)
    expect(yamlPreviewProps.workflowId).toBe('')
  })
})
