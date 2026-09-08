// @vitest-environment happy-dom
/**
 * NodePalette 组件测试（spec-11）：
 * - 验证 palette 项白名单（仅 llm/http，S15 守卫：不含 python）；
 * - 验证拖拽事件（dragstart 写入 dataTransfer）；
 * - 验证 readonly 模式（draggable=false + aria-disabled=true）；
 * - 验证点击回退路径（emit add-node）；
 * - 验证 nextNodeName 纯函数（唯一、递增、填补空缺）。
 */
import { describe, expect, it, vi } from 'vitest'
import { mount } from '@vue/test-utils'
import type { Component } from 'vue'

import NodePalette from '@/views/workflow/canvas/NodePalette.vue'
import { nextNodeName, PALETTE_ITEMS } from '@/views/workflow/canvas/nodeCatalog'

function mountPalette(extraProps: Record<string, unknown> = {}) {
  const wrapper = mount(NodePalette as Component, {
    props: {
      ...extraProps,
    },
  })
  return wrapper
}

describe('NodePalette 节点面板', () => {
  it('渲染恰好 2 个可拖项（llm / http）', () => {
    const wrapper = mountPalette()
    const items = wrapper.findAll('.node-palette__item')
    expect(items).toHaveLength(2)
  })

  it('S15 守卫：palette 项白名单不含 python', () => {
    const types = PALETTE_ITEMS.map(item => item.type)
    expect(types).toEqual(['llm', 'http'])
    expect(types).not.toContain('python')
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
  })

  it('已有 type_1 → 返回 type_2', () => {
    expect(nextNodeName('llm', ['llm_1'])).toBe('llm_2')
    expect(nextNodeName('http', ['http_1'])).toBe('http_2')
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
