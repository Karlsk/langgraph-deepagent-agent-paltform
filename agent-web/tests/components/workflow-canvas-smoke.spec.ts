// @vitest-environment happy-dom
/**
 * WorkflowCanvasSmoke 冒烟测试（spec-08）：
 * - stub @vue-flow/* 模块（happy-dom 不真实测量布局）；
 * - 断言冒烟组件将正确的 nodes/edges props 传入 VueFlow stub；
 * - 依赖守卫：package.json 含 @vue-flow/core。
 */
import { readFileSync } from 'node:fs'
import { describe, expect, it, vi } from 'vitest'
import { mount } from '@vue/test-utils'

const { vueFlowProps, VueFlowStub, BackgroundStub, ControlsStub, MiniMapStub } = vi.hoisted(() => {
  const props = { nodes: [] as unknown[], edges: [] as unknown[] }
  const VueFlow = {
    name: 'VueFlow',
    props: { nodes: { type: Array, default: () => [] }, edges: { type: Array, default: () => [] } },
    template: '<div class="vue-flow-stub"><slot /></div>',
    setup(p: Record<string, unknown>) {
      props.nodes = p.nodes as unknown[]
      props.edges = p.edges as unknown[]
    },
  }
  const makeStub = (cls: string, label: string) => ({
    name: label,
    template: `<div class="${cls}"></div>`,
  })
  return {
    vueFlowProps: props,
    VueFlowStub: VueFlow,
    BackgroundStub: makeStub('bg-stub', 'Background'),
    ControlsStub: makeStub('controls-stub', 'Controls'),
    MiniMapStub: makeStub('minimap-stub', 'MiniMap'),
  }
})

vi.mock('@vue-flow/core', () => ({
  VueFlow: VueFlowStub,
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

import WorkflowCanvasSmoke from '@/views/workflow/WorkflowCanvasSmoke.vue'

describe('WorkflowCanvasSmoke', () => {
  it('renders the VueFlow stub', () => {
    const wrapper = mount(WorkflowCanvasSmoke)
    expect(wrapper.find('.vue-flow-stub').exists()).toBe(true)
  })

  it('passes 2 nodes to VueFlow', () => {
    mount(WorkflowCanvasSmoke)
    expect(vueFlowProps.nodes).toHaveLength(2)
    expect(vueFlowProps.nodes.map((n: any) => n.id)).toEqual(['1', '2'])
  })

  it('passes 1 edge to VueFlow', () => {
    mount(WorkflowCanvasSmoke)
    expect(vueFlowProps.edges).toHaveLength(1)
    expect((vueFlowProps.edges[0] as any).id).toBe('e1-2')
  })

  it('renders Background, Controls, and MiniMap stubs', () => {
    const wrapper = mount(WorkflowCanvasSmoke)
    expect(wrapper.find('.bg-stub').exists()).toBe(true)
    expect(wrapper.find('.controls-stub').exists()).toBe(true)
    expect(wrapper.find('.minimap-stub').exists()).toBe(true)
  })
})

describe('Vue Flow dependency guard', () => {
  it('package.json contains @vue-flow/core', () => {
    const pkg = JSON.parse(readFileSync('package.json', 'utf8'))
    expect(pkg.dependencies).toHaveProperty('@vue-flow/core')
  })
})
