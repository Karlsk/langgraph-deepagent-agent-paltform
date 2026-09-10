/**
 * useWorkflowCapabilities composable 测试（spec-19）。
 *
 * 零真实网络：mock `@/api/workflow` 的 getWorkflowCapabilities，
 * 验证能力查询与保守降级：
 *   - can_edit=true → canEdit.value === true
 *   - can_edit=false → canEdit.value === false
 *   - API 请求失败 → 保守降级 canEdit.value === false
 *   - loaded 状态：初始 false，请求后 true
 *   - refresh() 重新拉取
 */
import { beforeEach, describe, expect, it, vi } from 'vitest'

const workflowApiMock = {
  getWorkflowCapabilities: vi.fn(),
}

vi.mock('@/api/workflow', () => ({
  getWorkflowCapabilities: (...args: unknown[]) => workflowApiMock.getWorkflowCapabilities(...args),
}))

let useWorkflowCapabilitiesModule: typeof import('@/composables/useWorkflowCapabilities')

beforeEach(async () => {
  vi.clearAllMocks()
  vi.resetModules()
  useWorkflowCapabilitiesModule = await import('@/composables/useWorkflowCapabilities')
})

describe('useWorkflowCapabilities', () => {
  it('初始状态：canEdit=false, loaded=false', () => {
    const { canEdit, loaded } = useWorkflowCapabilitiesModule.useWorkflowCapabilities()
    expect(canEdit.value).toBe(false)
    expect(loaded.value).toBe(false)
  })

  it('can_edit=true → canEdit.value === true, loaded=true', async () => {
    workflowApiMock.getWorkflowCapabilities.mockResolvedValueOnce({ can_edit: true })
    const { canEdit, loaded, refresh } = useWorkflowCapabilitiesModule.useWorkflowCapabilities()

    await refresh()

    expect(canEdit.value).toBe(true)
    expect(loaded.value).toBe(true)
    expect(workflowApiMock.getWorkflowCapabilities).toHaveBeenCalledOnce()
  })

  it('can_edit=false → canEdit.value === false, loaded=true', async () => {
    workflowApiMock.getWorkflowCapabilities.mockResolvedValueOnce({ can_edit: false })
    const { canEdit, loaded, refresh } = useWorkflowCapabilitiesModule.useWorkflowCapabilities()

    await refresh()

    expect(canEdit.value).toBe(false)
    expect(loaded.value).toBe(true)
  })

  it('API 请求失败 → 保守降级 canEdit.value === false, loaded=true', async () => {
    workflowApiMock.getWorkflowCapabilities.mockRejectedValueOnce(new Error('Network error'))
    const { canEdit, loaded, refresh } = useWorkflowCapabilitiesModule.useWorkflowCapabilities()

    await refresh()

    expect(canEdit.value).toBe(false)
    expect(loaded.value).toBe(true)
  })

  it('refresh() 重新拉取并更新状态', async () => {
    workflowApiMock.getWorkflowCapabilities
      .mockResolvedValueOnce({ can_edit: false })
      .mockResolvedValueOnce({ can_edit: true })

    const { canEdit, refresh } = useWorkflowCapabilitiesModule.useWorkflowCapabilities()

    await refresh()
    expect(canEdit.value).toBe(false)

    await refresh()
    expect(canEdit.value).toBe(true)
    expect(workflowApiMock.getWorkflowCapabilities).toHaveBeenCalledTimes(2)
  })
})
