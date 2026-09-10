/**
 * useWorkflowDesigner save() 测试（spec-21）。
 *
 * 零真实网络：mock `@/api/workflow` 的 saveWorkflow / getWorkflow，
 * mock `@/utils/notify` 的 notifySuccess / notifyError。
 * 验证 save 流程：
 *   - 本地校验失败 → 不调 saveWorkflow，fieldErrors 填充
 *   - saveWorkflow 成功 → isDirty=false, notifySuccess, fieldErrors 清空
 *   - saveWorkflow 422 → fieldErrors 填充, isDirty 保持 true
 *   - saveWorkflow 403 → fieldErrors 空, isDirty 保持 true
 *   - isSaving 在 save 期间为 true，结束后为 false
 *   - 并发 save 被 isSaving 守卫阻止
 */
import { beforeEach, describe, expect, it, vi } from 'vitest'
import type { Node, Edge } from '@vue-flow/core'

const saveWorkflowMock = vi.fn()
const getWorkflowMock = vi.fn()
const notifySuccessMock = vi.fn()
const notifyErrorMock = vi.fn()

vi.mock('@/api/workflow', () => ({
  getWorkflow: (...args: unknown[]) => getWorkflowMock(...args),
  saveWorkflow: (...args: unknown[]) => saveWorkflowMock(...args),
}))

vi.mock('@/utils/notify', () => ({
  notifySuccess: (...args: unknown[]) => notifySuccessMock(...args),
  notifyError: (...args: unknown[]) => notifyErrorMock(...args),
}))

let useWorkflowDesignerModule: typeof import('@/composables/useWorkflowDesigner')

beforeEach(async () => {
  vi.clearAllMocks()
  vi.resetModules()
  useWorkflowDesignerModule = await import('@/composables/useWorkflowDesigner')
})

function makeValidGraph() {
  const nodes: Node[] = [
    {
      id: 'llm_1',
      type: 'workflow',
      position: { x: 0, y: 0 },
      data: { name: 'llm_1', type: 'llm', config: { model_name: 'gpt-4o-mini' } },
    },
  ]
  const edges: Edge[] = []
  return { nodes, edges }
}

describe('useWorkflowDesigner — save()', () => {
  it('本地校验失败（空 nodes）→ 不调 saveWorkflow，fieldErrors 填充', async () => {
    const { useWorkflowDesigner } = useWorkflowDesignerModule
    const state = useWorkflowDesigner()
    state.meta.value = { workflow_id: 'wf1', entry_point: 'llm_1', state_schema: {} }

    await state.save()

    expect(saveWorkflowMock).not.toHaveBeenCalled()
    expect(state.fieldErrors.value.length).toBeGreaterThan(0)
    expect(state.fieldErrors.value[0].field).toBe('nodes')
  })

  it('saveWorkflow 成功 → isDirty=false, notifySuccess, fieldErrors 清空', async () => {
    saveWorkflowMock.mockResolvedValueOnce({})
    const { useWorkflowDesigner } = useWorkflowDesignerModule
    const state = useWorkflowDesigner()
    const { nodes, edges } = makeValidGraph()
    state.nodes.value = nodes
    state.edges.value = edges
    state.meta.value = { workflow_id: 'wf1', entry_point: 'llm_1', state_schema: {} }
    state.isDirty.value = true

    await state.save()

    expect(saveWorkflowMock).toHaveBeenCalledOnce()
    expect(saveWorkflowMock).toHaveBeenCalledWith('wf1', expect.objectContaining({
      workflow_id: 'wf1',
      entry_point: 'llm_1',
      nodes: expect.any(Array),
      edges: expect.any(Array),
    }))
    expect(state.isDirty.value).toBe(false)
    expect(notifySuccessMock).toHaveBeenCalledWith('保存成功')
    expect(state.fieldErrors.value).toEqual([])
  })

  it('saveWorkflow 422 → fieldErrors 填充, isDirty 保持 true', async () => {
    const error422 = new Error('Request failed') as Error & {
      isAxiosError: boolean
      response?: { status: number; data: unknown }
    }
    error422.isAxiosError = true
    error422.response = {
      status: 422,
      data: { code: 422, message: "invalid definition: Node 'llm_1' config error", data: null },
    }
    saveWorkflowMock.mockRejectedValueOnce(error422)

    const { useWorkflowDesigner } = useWorkflowDesignerModule
    const state = useWorkflowDesigner()
    const { nodes, edges } = makeValidGraph()
    state.nodes.value = nodes
    state.edges.value = edges
    state.meta.value = { workflow_id: 'wf1', entry_point: 'llm_1', state_schema: {} }
    state.isDirty.value = true

    await state.save()

    expect(state.isDirty.value).toBe(true)
    expect(state.fieldErrors.value.length).toBeGreaterThan(0)
    expect(state.fieldErrors.value[0].nodeId).toBe('llm_1')
  })

  it('saveWorkflow 403 → fieldErrors 空, isDirty 保持 true', async () => {
    const error403 = new Error('Forbidden') as Error & {
      isAxiosError: boolean
      response?: { status: number; data: unknown }
    }
    error403.isAxiosError = true
    error403.response = {
      status: 403,
      data: { code: 403, message: 'workflow write requires admin', data: null },
    }
    saveWorkflowMock.mockRejectedValueOnce(error403)

    const { useWorkflowDesigner } = useWorkflowDesignerModule
    const state = useWorkflowDesigner()
    const { nodes, edges } = makeValidGraph()
    state.nodes.value = nodes
    state.edges.value = edges
    state.meta.value = { workflow_id: 'wf1', entry_point: 'llm_1', state_schema: {} }
    state.isDirty.value = true

    await state.save()

    expect(state.isDirty.value).toBe(true)
    expect(state.fieldErrors.value).toEqual([])
  })

  it('isSaving 在 save 期间为 true，结束后为 false', async () => {
    let resolveSave: (value: unknown) => void
    saveWorkflowMock.mockReturnValueOnce(
      new Promise((resolve) => { resolveSave = resolve }),
    )

    const { useWorkflowDesigner } = useWorkflowDesignerModule
    const state = useWorkflowDesigner()
    const { nodes, edges } = makeValidGraph()
    state.nodes.value = nodes
    state.edges.value = edges
    state.meta.value = { workflow_id: 'wf1', entry_point: 'llm_1', state_schema: {} }

    expect(state.isSaving.value).toBe(false)

    const savePromise = state.save()
    expect(state.isSaving.value).toBe(true)

    resolveSave!({})
    await savePromise
    expect(state.isSaving.value).toBe(false)
  })

  it('并发 save 被 isSaving 守卫阻止（第二次调用直接返回）', async () => {
    let resolveSave: (value: unknown) => void
    saveWorkflowMock.mockReturnValueOnce(
      new Promise((resolve) => { resolveSave = resolve }),
    )

    const { useWorkflowDesigner } = useWorkflowDesignerModule
    const state = useWorkflowDesigner()
    const { nodes, edges } = makeValidGraph()
    state.nodes.value = nodes
    state.edges.value = edges
    state.meta.value = { workflow_id: 'wf1', entry_point: 'llm_1', state_schema: {} }

    const first = state.save()
    const second = state.save()

    resolveSave!({})
    await first
    await second

    expect(saveWorkflowMock).toHaveBeenCalledOnce()
  })
})
