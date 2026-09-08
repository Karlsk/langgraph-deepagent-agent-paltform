// @vitest-environment happy-dom
/**
 * NodeConfigPanel 组件测试（spec-12）：
 * - 验证 type 驱动表单切换（llm → LlmNodeForm，http → HttpNodeForm，null → 空态）；
 * - 验证 H6 守卫（LLM 表单不含 api_key 输入）；
 * - 验证 immutable patch（emit update:node 含新 config 对象，不修改原 props）；
 * - 验证 readonly 模式（控件禁用、删除按钮隐藏）；
 * - 验证节点重命名（emit update:node({ name })）；
 * - 验证删除节点（emit remove-node(id)）。
 */
import { describe, expect, it } from 'vitest'
import { mount } from '@vue/test-utils'
import type { Component } from 'vue'

import NodeConfigPanel from '@/views/workflow/panel/NodeConfigPanel.vue'

const mockLlmNode = {
  id: 'node-1',
  type: 'workflow',
  data: {
    name: 'llm_1',
    type: 'llm',
    config: {
      llm_type: 'openai',
      model_name: 'gpt-4o-mini',
      temperature: 0.7,
      system_prompt: 'You are helpful.',
    },
  },
}

const mockHttpNode = {
  id: 'node-2',
  type: 'workflow',
  data: {
    name: 'http_1',
    type: 'http',
    config: {
      url: 'https://api.example.com',
      method: 'POST',
      body_template: '{"query": "{input}"}',
      response_path: 'data',
      mock_enabled: false,
      mock_responses: {},
    },
  },
}

function mountPanel(extraProps: Record<string, unknown> = {}) {
  return mount(NodeConfigPanel as Component, {
    props: {
      node: null,
      ...extraProps,
    },
  })
}

describe('NodeConfigPanel 节点配置面板', () => {
  describe('type 驱动表单切换', () => {
    it('node=null → 渲染空态提示', () => {
      const wrapper = mountPanel()
      expect(wrapper.find('.node-config-panel__empty').exists()).toBe(true)
    })

    it('node.type="llm" → 渲染 LlmNodeForm', () => {
      const wrapper = mountPanel({ node: mockLlmNode })
      expect(wrapper.find('.llm-node-form').exists()).toBe(true)
      expect(wrapper.find('.http-node-form').exists()).toBe(false)
    })

    it('node.type="http" → 渲染 HttpNodeForm', () => {
      const wrapper = mountPanel({ node: mockHttpNode })
      expect(wrapper.find('.http-node-form').exists()).toBe(true)
      expect(wrapper.find('.llm-node-form').exists()).toBe(false)
    })
  })

  describe('H6 守卫：LLM 表单不含 api_key', () => {
    it('LLM 表单不存在 api_key 输入字段', () => {
      const wrapper = mountPanel({ node: mockLlmNode })
      const html = wrapper.html()
      expect(html).not.toContain('api_key')
      expect(html).not.toContain('apiKey')
      expect(html).not.toContain('API Key')
    })
  })

  describe('immutable patch（update:node emit）', () => {
    it('修改 model_name → emit update:node({ config }) 含新值', async () => {
      const wrapper = mountPanel({ node: mockLlmNode })
      const form = wrapper.findComponent({ name: 'LlmNodeForm' })
      
      await form.vm.$emit('update:config', {
        ...mockLlmNode.data.config,
        model_name: 'gpt-4o',
      })
      
      expect(wrapper.emitted('update:node')).toHaveLength(1)
      const emitted = wrapper.emitted('update:node')![0][0] as { config: Record<string, unknown> }
      expect(emitted.config.model_name).toBe('gpt-4o')
      expect(emitted.config).not.toBe(mockLlmNode.data.config)
    })

    it('修改 url → emit update:node({ config }) 含新值', async () => {
      const wrapper = mountPanel({ node: mockHttpNode })
      const form = wrapper.findComponent({ name: 'HttpNodeForm' })
      
      await form.vm.$emit('update:config', {
        ...mockHttpNode.data.config,
        url: 'https://new-api.example.com',
      })
      
      expect(wrapper.emitted('update:node')).toHaveLength(1)
      const emitted = wrapper.emitted('update:node')![0][0] as { config: Record<string, unknown> }
      expect(emitted.config.url).toBe('https://new-api.example.com')
      expect(emitted.config).not.toBe(mockHttpNode.data.config)
    })
  })

  describe('节点重命名', () => {
    it('修改 name 输入 → emit update:node({ name })', async () => {
      const wrapper = mountPanel({ node: mockLlmNode })
      const nameInput = wrapper.find('.node-config-panel__name-input input')
      
      await nameInput.setValue('new_name')
      await nameInput.trigger('blur')
      
      expect(wrapper.emitted('update:node')).toHaveLength(1)
      const emitted = wrapper.emitted('update:node')![0][0] as { name: string }
      expect(emitted.name).toBe('new_name')
    })
  })

  describe('删除节点', () => {
    it('点击删除按钮 → emit remove-node(id)', async () => {
      const wrapper = mountPanel({ node: mockLlmNode })
      const deleteBtn = wrapper.find('.node-config-panel__delete-btn')
      
      await deleteBtn.trigger('click')
      
      expect(wrapper.emitted('remove-node')).toHaveLength(1)
      expect(wrapper.emitted('remove-node')![0]).toEqual(['node-1'])
    })
  })

  describe('readonly 模式', () => {
    it('readonly=true → 所有控件禁用、删除按钮隐藏', () => {
      const wrapper = mountPanel({ node: mockLlmNode, readonly: true })
      
      const nameInput = wrapper.find('.node-config-panel__name-input input')
      expect(nameInput.attributes('disabled')).toBeDefined()
      
      const deleteBtn = wrapper.find('.node-config-panel__delete-btn')
      expect(deleteBtn.exists()).toBe(false)
      
      const form = wrapper.findComponent({ name: 'LlmNodeForm' })
      expect(form.props('readonly')).toBe(true)
    })

    it('readonly=false（默认）→ 删除按钮可见', () => {
      const wrapper = mountPanel({ node: mockLlmNode })
      const deleteBtn = wrapper.find('.node-config-panel__delete-btn')
      expect(deleteBtn.exists()).toBe(true)
    })
  })
})
