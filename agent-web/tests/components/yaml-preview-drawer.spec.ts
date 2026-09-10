// @vitest-environment happy-dom
/**
 * YamlPreviewDrawer 只读 YAML 预览抽屉测试（spec-22）。
 *
 * 零真实网络：mock `@/api/workflow` 的 getWorkflow。
 * 验证：
 *   - 打开抽屉（已保存 id）→ 调 getWorkflow(id, 'yaml')，渲染 yaml_text
 *   - dirty=true → 展示「已保存版本」提示
 *   - getWorkflow 404 → 展示「请先保存」提示，不崩溃
 *   - getWorkflow reject（网络）→ 错误态 + notifyError
 *   - 复制按钮 → navigator.clipboard.writeText
 *   - D5 守卫：组件不 import js-yaml
 *   - 空 workflowId → 提示「请先保存」，不调 API
 *   - loading 状态
 */
import { beforeEach, describe, expect, it, vi } from 'vitest'
import { defineComponent, h } from 'vue'
import { flushPromises, mount } from '@vue/test-utils'
import { readFileSync } from 'node:fs'
import { resolve } from 'node:path'

import YamlPreviewDrawer from '@/views/workflow/YamlPreviewDrawer.vue'

const getWorkflowMock = vi.fn()
vi.mock('@/api/workflow', () => ({
  getWorkflow: (...args: unknown[]) => getWorkflowMock(...args),
}))

const mockNotifyError = vi.fn()
const mockNotifySuccess = vi.fn()
vi.mock('@/utils/notify', () => ({
  notifyError: (...args: unknown[]) => mockNotifyError(...args),
  notifySuccess: (...args: unknown[]) => mockNotifySuccess(...args),
}))

const ElDrawerStub = defineComponent({
  name: 'ElDrawer',
  props: { modelValue: Boolean, title: String, size: String },
  emits: ['update:modelValue'],
  setup(props, { slots }) {
    return () =>
      props.modelValue
        ? h('div', { class: 'el-drawer-stub' }, slots.default?.())
        : null
  },
})

const YAML_TEXT =
  'workflow_id: demo-flow\nentry_point: start\nnodes:\n  - name: start\n    type: llm\n'

function mountDrawer(props: {
  modelValue?: boolean
  workflowId?: string
  dirty?: boolean
} = {}) {
  return mount(YamlPreviewDrawer, {
    props: {
      modelValue: props.modelValue ?? false,
      workflowId: props.workflowId ?? '',
      dirty: props.dirty ?? false,
    },
    global: { stubs: { ElDrawer: ElDrawerStub } },
  })
}

beforeEach(() => {
  vi.clearAllMocks()
  getWorkflowMock.mockResolvedValue({ yaml_text: YAML_TEXT })
  const mockWriteText = vi.fn().mockResolvedValue(undefined)
  Object.defineProperty(navigator, 'clipboard', {
    value: { writeText: mockWriteText },
    writable: true,
    configurable: true,
  })
})

describe('YamlPreviewDrawer', () => {
  it('打开抽屉（已保存 id）→ 调 getWorkflow(id, yaml) 并渲染 yaml_text', async () => {
    const wrapper = mountDrawer({ modelValue: true, workflowId: 'wf-1' })
    await flushPromises()

    expect(getWorkflowMock).toHaveBeenCalledWith('wf-1', 'yaml')
    expect(wrapper.find('.yaml-preview-drawer__code').text().trim()).toBe(YAML_TEXT.trim())
  })

  it('dirty=true → 展示「已保存版本」提示', async () => {
    const wrapper = mountDrawer({
      modelValue: true,
      workflowId: 'wf-1',
      dirty: true,
    })
    await flushPromises()

    expect(wrapper.find('.yaml-preview-drawer__dirty').exists()).toBe(true)
    expect(wrapper.text()).toContain('已保存版本')
    expect(wrapper.text()).toContain('保存后再预览可看到最新 YAML')
  })

  it('dirty=false → 不展示「已保存版本」提示', async () => {
    const wrapper = mountDrawer({
      modelValue: true,
      workflowId: 'wf-1',
      dirty: false,
    })
    await flushPromises()

    expect(wrapper.find('.yaml-preview-drawer__dirty').exists()).toBe(false)
  })

  it('getWorkflow 404 → 展示「请先保存」提示，不崩溃', async () => {
    const error = Object.assign(new Error('Not Found'), {
      response: { status: 404, data: null },
    })
    getWorkflowMock.mockRejectedValueOnce(error)

    const wrapper = mountDrawer({ modelValue: true, workflowId: 'wf-new' })
    await flushPromises()

    expect(wrapper.find('.yaml-preview-drawer__not-found').exists()).toBe(true)
    expect(wrapper.text()).toContain('请先保存')
    expect(mockNotifyError).not.toHaveBeenCalled()
  })

  it('getWorkflow reject（网络错误）→ notifyError + 错误态', async () => {
    getWorkflowMock.mockRejectedValueOnce(new Error('Network error'))

    const wrapper = mountDrawer({ modelValue: true, workflowId: 'wf-1' })
    await flushPromises()

    expect(mockNotifyError).toHaveBeenCalledWith('加载 YAML 失败')
    expect(wrapper.find('.yaml-preview-drawer__error').exists()).toBe(true)
  })

  it('复制按钮 → navigator.clipboard.writeText(yaml_text)', async () => {
    const wrapper = mountDrawer({ modelValue: true, workflowId: 'wf-1' })
    await flushPromises()

    await wrapper.get('.yaml-preview-drawer__copy').trigger('click')

    expect(navigator.clipboard.writeText).toHaveBeenCalledWith(YAML_TEXT)
    expect(mockNotifySuccess).toHaveBeenCalledWith('已复制到剪贴板')
  })

  it('D5 守卫：组件源码不 import js-yaml', () => {
    const source = readFileSync(
      resolve(__dirname, '../../src/views/workflow/YamlPreviewDrawer.vue'),
      'utf-8',
    )
    expect(source).not.toMatch(/import.*js-yaml/)
    expect(source).not.toMatch(/require\(.*js-yaml/)
  })

  it('空 workflowId → 展示「请先保存」提示，不调 getWorkflow', async () => {
    const wrapper = mountDrawer({ modelValue: true, workflowId: '' })
    await flushPromises()

    expect(getWorkflowMock).not.toHaveBeenCalled()
    expect(wrapper.find('.yaml-preview-drawer__not-found').exists()).toBe(true)
    expect(wrapper.text()).toContain('请先保存')
  })

  it('loading 状态：fetch 期间展示加载中', async () => {
    let resolveFetch!: (v: unknown) => void
    getWorkflowMock.mockReturnValueOnce(
      new Promise((resolve) => {
        resolveFetch = resolve
      }),
    )

    const wrapper = mountDrawer({ modelValue: true, workflowId: 'wf-1' })

    expect(wrapper.find('.yaml-preview-drawer__loading').exists()).toBe(true)

    resolveFetch({ yaml_text: YAML_TEXT })
    await flushPromises()

    expect(wrapper.find('.yaml-preview-drawer__loading').exists()).toBe(false)
    expect(wrapper.find('.yaml-preview-drawer__code').text().trim()).toBe(YAML_TEXT.trim())
  })
})
