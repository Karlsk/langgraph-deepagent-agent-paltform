// @vitest-environment happy-dom
/**
 * NodePalette 组件测试（spec-11，S18/S22 修订）：
 * - 验证 palette 项白名单为 llm/http/python（`python` 自 S22 起有真沙箱执行路径，故可编排）；
 * - 验证拖拽事件（dragstart 写入 dataTransfer）；
 * - 验证 readonly 模式（draggable=false + aria-disabled=true）；
 * - 验证点击回退路径（emit add-node）；
 * - 验证 DEFAULT_CONFIGS.python 只暴露 code（无 entry / sandboxed，§5.1）；
 * - 验证 nextNodeName 纯函数（唯一、递增、填补空缺）。
 */
import { describe, expect, it, vi } from 'vitest'
import { mount } from '@vue/test-utils'
import type { Component } from 'vue'

import NodePalette from '@/views/workflow/canvas/NodePalette.vue'
import { DEFAULT_CONFIGS, nextNodeName, PALETTE_ITEMS } from '@/views/workflow/canvas/nodeCatalog'

function mountPalette(extraProps: Record<string, unknown> = {}) {
  const wrapper = mount(NodePalette as Component, {
    props: {
      ...extraProps,
    },
  })
  return wrapper
}

describe('NodePalette 节点面板', () => {
  it('渲染恰好 5 个可拖项（llm / http / python / subworkflow / react）', () => {
    const wrapper = mountPalette()
    const items = wrapper.findAll('.node-palette__item')
    expect(items).toHaveLength(5)
  })

  it('S18 白名单：palette 项为 llm / http / python / subworkflow / react', () => {
    const types = PALETTE_ITEMS.map(item => item.type)
    expect(types).toEqual(['llm', 'http', 'python', 'subworkflow', 'react'])
  })

  it('DEFAULT_CONFIGS.python 含 code + inputs，不含 entry / sandboxed（§5.1 + Dify-style inputs）', () => {
    expect(Object.keys(DEFAULT_CONFIGS.python!).sort()).toEqual(['code', 'inputs'])
    expect(DEFAULT_CONFIGS.python).not.toHaveProperty('entry')
    expect(DEFAULT_CONFIGS.python).not.toHaveProperty('sandboxed')
  })

  it('DEFAULT_CONFIGS.subworkflow 为 workflow_id / input_map / inherit_input（§5.1）', () => {
    expect(DEFAULT_CONFIGS.subworkflow).toEqual({
      workflow_id: '',
      input_map: {},
      inherit_input: false,
    })
  })

  it('DEFAULT_CONFIGS.http 含空 headers，新拖出的节点即可编辑请求头（§5.1）', () => {
    expect(DEFAULT_CONFIGS.http).toHaveProperty('headers')
    expect(DEFAULT_CONFIGS.http!.headers).toEqual({})
  })

  it('拖拽项 dragstart → dataTransfer.setData 写入 type', async () => {
    const wrapper = mountPalette()
    const llmItem = wrapper.findAll('.node-palette__item')[0]
    
    const mockDataTransfer = {
      setData: vi.fn(),
      getData: vi.fn(),
    }
    
    const dragEvent = new Event('dragstart', { bubbles: true }) as DragEvent
    Object.defineProperty(dragEvent, 'dataTransfer', {
      value: mockDataTransfer,
    })
    
    llmItem.element.dispatchEvent(dragEvent)
    
    expect(mockDataTransfer.setData).toHaveBeenCalledWith(
      'application/json',
      JSON.stringify({ type: 'llm' }),
    )
  })

  it('readonly=true → draggable=false + aria-disabled=true', () => {
    const wrapper = mountPalette({ readonly: true })
    const items = wrapper.findAll('.node-palette__item')
    
    items.forEach(item => {
      expect(item.attributes('draggable')).toBe('false')
      expect(item.attributes('aria-disabled')).toBe('true')
    })
  })

  it('readonly=false（默认）→ draggable=true + aria-disabled=false', () => {
    const wrapper = mountPalette()
    const items = wrapper.findAll('.node-palette__item')
    
    items.forEach(item => {
      expect(item.attributes('draggable')).toBe('true')
      expect(item.attributes('aria-disabled')).toBe('false')
    })
  })

  it('点击项（回退路径）→ emit add-node({ type, position })', async () => {
    const wrapper = mountPalette()
    const httpItem = wrapper.findAll('.node-palette__item')[1]
    
    await httpItem.trigger('click')
    
    expect(wrapper.emitted('add-node')).toHaveLength(1)
    expect(wrapper.emitted('add-node')![0]).toEqual([
      { type: 'http', position: { x: 0, y: 0 } },
    ])
  })

  it('点击 python 项 → emit add-node({ type: "python" })', async () => {
    const wrapper = mountPalette()
    const pythonItem = wrapper.findAll('.node-palette__item')[2]

    await pythonItem.trigger('click')

    expect(wrapper.emitted('add-node')![0]).toEqual([
      { type: 'python', position: { x: 0, y: 0 } },
    ])
  })

  it('点击 subworkflow 项 → emit add-node({ type: "subworkflow" })', async () => {
    const wrapper = mountPalette()
    const subItem = wrapper.findAll('.node-palette__item')[3]

    await subItem.trigger('click')

    expect(wrapper.emitted('add-node')![0]).toEqual([
      { type: 'subworkflow', position: { x: 0, y: 0 } },
    ])
  })

  it('readonly=true → 点击不触发 emit', async () => {
    const wrapper = mountPalette({ readonly: true })
    const llmItem = wrapper.findAll('.node-palette__item')[0]
    
    await llmItem.trigger('click')
    
    expect(wrapper.emitted('add-node')).toBeUndefined()
  })
})

describe('nextNodeName 纯函数', () => {
  it('空列表 → 返回 type_1', () => {
    expect(nextNodeName('llm', [])).toBe('llm_1')
    expect(nextNodeName('http', [])).toBe('http_1')
    expect(nextNodeName('python', [])).toBe('python_1')
    expect(nextNodeName('subworkflow', [])).toBe('subworkflow_1')
  })

  it('已有 type_1 → 返回 type_2', () => {
    expect(nextNodeName('llm', ['llm_1'])).toBe('llm_2')
    expect(nextNodeName('http', ['http_1'])).toBe('http_2')
    expect(nextNodeName('python', ['python_1'])).toBe('python_2')
  })

  it('混合类型 → 仅计数同类型', () => {
    expect(nextNodeName('http', ['llm_1', 'http_1'])).toBe('http_2')
  })

  it('填补空缺 → 返回最小可用编号', () => {
    expect(nextNodeName('llm', ['llm_1', 'llm_3'])).toBe('llm_2')
    expect(nextNodeName('llm', ['llm_2', 'llm_3'])).toBe('llm_1')
  })

  it('连续编号 → 返回下一个', () => {
    expect(nextNodeName('llm', ['llm_1', 'llm_2', 'llm_3'])).toBe('llm_4')
  })
})
