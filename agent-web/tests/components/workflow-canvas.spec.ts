// @vitest-environment happy-dom
/**
 * WorkflowCanvas + WorkflowNode + EndNode 组件测试（spec-10）：
 * - stub @vue-flow/* 模块（happy-dom 不真实测量布局）；
 * - 验证画布容器透传 nodes/edges、事件 emit、readonly 门禁；
 * - 验证自定义节点按 type 应用 token class、渲染 name、包含 Handle。
 */
import { describe, expect, it, vi } from 'vitest'
import { mount } from '@vue/test-utils'
import type { Component } from 'vue'

const {
  vueFlowProps,
  vueFlowHandlers,
  VueFlowStub,
  BackgroundStub,
  ControlsStub,
  MiniMapStub,
  HandleStub,
} = vi.hoisted(() => {
  const props: Record<string, unknown> = {}
  const handlers: Record<string, (...args: unknown[]) => void> = {}

  const VueFlow = {
    name: 'VueFlow',
    props: {
      nodes: { type: Array, default: () => [] },
      edges: { type: Array, default: () => [] },
      nodeTypes: { type: Object, default: () => ({}) },
      nodesDraggable: { type: Boolean, default: true },
      connectable: { type: Boolean, default: true },
      elementsSelectable: { type: Boolean, default: true },
    },
    emits: ['connect', 'node-click', 'pane-click'],
    template: '<div class="vue-flow-stub"><slot /></div>',
    setup(p: Record<string, unknown>, { emit: e }: { emit: (...args: unknown[]) => void }) {
      Object.assign(props, p)
      handlers.connect = (connection: unknown) => e('connect', connection)
      handlers.nodeClick = (nodeMouseEvent: unknown) => e('node-click', nodeMouseEvent)
      handlers.paneClick = () => e('pane-click')
    },
  }

  const makeStub = (cls: string, label: string) => ({
    name: label,
    template: `<div class="${cls}"></div>`,
  })

  const Handle = {
    name: 'Handle',
    props: {
      type: { type: String, default: 'source' },
      position: { type: String, default: 'bottom' },
    },
    template: '<div class="handle-stub" :data-type="type" :data-position="position"></div>',
  }

  return {
    vueFlowProps: props,
    vueFlowHandlers: handlers,
    VueFlowStub: VueFlow,
    BackgroundStub: makeStub('bg-stub', 'Background'),
    ControlsStub: makeStub('controls-stub', 'Controls'),
    MiniMapStub: makeStub('minimap-stub', 'MiniMap'),
    HandleStub: Handle,
  }
})

vi.mock('@vue-flow/core', () => ({
  VueFlow: VueFlowStub,
  Handle: HandleStub,
  Position: { Top: 'top', Bottom: 'bottom', Left: 'left', Right: 'right' },
}))

vi.mock('@vue-flow/background', () => ({
  Background: BackgroundStub,
}))

vi.mock('@vue-flow/controls', () => ({
  Controls: ControlsStub,
}))

vi.mock('@vue-flow/minimap', () => ({
  MiniMap: MiniMapStub,
}))

import WorkflowCanvas from '@/views/workflow/canvas/WorkflowCanvas.vue'
import WorkflowNode from '@/views/workflow/canvas/WorkflowNode.vue'
import EndNode from '@/views/workflow/canvas/EndNode.vue'
import type { WorkflowNodeType } from '@/api/workflow'

const sampleNodes = [
  { id: '1', type: 'workflow', position: { x: 0, y: 0 }, data: { name: 'LLM 节点', type: 'llm', config: {} } },
  { id: '2', type: 'workflow', position: { x: 200, y: 200 }, data: { name: 'HTTP 节点', type: 'http', config: {} } },
]

const sampleEdges = [
  { id: 'e1-2', source: '1', target: '2' },
]

function mountCanvas(extraProps: Record<string, unknown> = {}) {
  const wrapper = mount(WorkflowCanvas as Component, {
    props: {
      nodes: [...sampleNodes],
      edges: [...sampleEdges],
      ...extraProps,
    },
  })
  return wrapper
}

describe('WorkflowCanvas 画布容器', () => {
  it('渲染 VueFlow stub', () => {
    const wrapper = mountCanvas()
    expect(wrapper.find('.vue-flow-stub').exists()).toBe(true)
  })

  it('透传 nodes 和 edges 给 VueFlow', () => {
    mountCanvas()
    expect(vueFlowProps.nodes).toEqual(sampleNodes)
    expect(vueFlowProps.edges).toEqual(sampleEdges)
  })

  it('透传 nodeTypes 给 VueFlow', () => {
    mountCanvas()
    expect(vueFlowProps.nodeTypes).toHaveProperty('workflow')
    expect(vueFlowProps.nodeTypes).toHaveProperty('end')
  })

  it('connect 事件 → emit connect({source, target})', async () => {
    const wrapper = mountCanvas()
    const connection = { source: '1', target: '3' }
    vueFlowHandlers.connect(connection)
    await wrapper.vm.$nextTick()

    expect(wrapper.emitted('connect')).toHaveLength(1)
    expect(wrapper.emitted('connect')![0]).toEqual([{ source: '1', target: '3' }])
  })

  it('node-click 事件 → emit select-node(id)', async () => {
    const wrapper = mountCanvas()
    const node = { id: '1', type: 'workflow' }
    vueFlowHandlers.nodeClick({ event: new MouseEvent('click'), node })
    await wrapper.vm.$nextTick()

    expect(wrapper.emitted('select-node')).toHaveLength(1)
    expect(wrapper.emitted('select-node')![0]).toEqual(['1'])
  })

  it('pane-click 事件 → emit select-node(null)', async () => {
    const wrapper = mountCanvas()
    vueFlowHandlers.paneClick()
    await wrapper.vm.$nextTick()

    expect(wrapper.emitted('select-node')).toHaveLength(1)
    expect(wrapper.emitted('select-node')![0]).toEqual([null])
  })

  it('readonly=true → nodes-draggable/connectable/elements-selectable 为 false', () => {
    mountCanvas({ readonly: true })
    expect(vueFlowProps.nodesDraggable).toBe(false)
    expect(vueFlowProps.connectable).toBe(false)
    expect(vueFlowProps.elementsSelectable).toBe(false)
  })

  it('readonly=false（默认）→ nodes-draggable/connectable/elements-selectable 为 true', () => {
    mountCanvas()
    expect(vueFlowProps.nodesDraggable).toBe(true)
    expect(vueFlowProps.connectable).toBe(true)
    expect(vueFlowProps.elementsSelectable).toBe(true)
  })

  it('渲染 Background、Controls、MiniMap', () => {
    const wrapper = mountCanvas()
    expect(wrapper.find('.bg-stub').exists()).toBe(true)
    expect(wrapper.find('.controls-stub').exists()).toBe(true)
    expect(wrapper.find('.minimap-stub').exists()).toBe(true)
  })
})

describe('WorkflowNode 自定义节点', () => {
  function mountNode(data: { name: string; type: WorkflowNodeType; config: Record<string, unknown> }) {
    return mount(WorkflowNode as Component, {
      props: { data },
    })
  }

  it('type=llm → 应用 workflow-node--llm class', () => {
    const wrapper = mountNode({ name: 'LLM', type: 'llm', config: {} })
    expect(wrapper.find('.workflow-node--llm').exists()).toBe(true)
  })

  it('type=http → 应用 workflow-node--http class', () => {
    const wrapper = mountNode({ name: 'HTTP', type: 'http', config: {} })
    expect(wrapper.find('.workflow-node--http').exists()).toBe(true)
  })

  it('type=python → 应用 workflow-node--python class（S18/S22）', () => {
    const wrapper = mountNode({ name: 'PYTHON', type: 'python', config: { code: 'return {}' } })
    expect(wrapper.find('.workflow-node--python').exists()).toBe(true)
  })

  it('type=subworkflow → 应用 workflow-node--subworkflow class（S23/S24）', () => {
    const wrapper = mountNode({ name: 'SUB', type: 'subworkflow', config: { workflow_id: 'wf_inner' } })
    expect(wrapper.find('.workflow-node--subworkflow').exists()).toBe(true)
  })

  it('渲染 data.name 文本', () => {
    const wrapper = mountNode({ name: '我的节点', type: 'llm', config: {} })
    expect(wrapper.text()).toContain('我的节点')
  })

  it('渲染 Handle 连接桩（target + source）', () => {
    const wrapper = mountNode({ name: 'Test', type: 'llm', config: {} })
    const handles = wrapper.findAll('.handle-stub')
    expect(handles).toHaveLength(2)
    expect(handles[0].attributes('data-type')).toBe('target')
    expect(handles[1].attributes('data-type')).toBe('source')
  })
})

describe('EndNode 终止节点', () => {
  it('渲染 END 标签', () => {
    const wrapper = mount(EndNode as Component)
    expect(wrapper.text()).toContain('END')
  })

  it('仅渲染 target Handle（无 source）', () => {
    const wrapper = mount(EndNode as Component)
    const handles = wrapper.findAll('.handle-stub')
    expect(handles).toHaveLength(1)
    expect(handles[0].attributes('data-type')).toBe('target')
  })
})
