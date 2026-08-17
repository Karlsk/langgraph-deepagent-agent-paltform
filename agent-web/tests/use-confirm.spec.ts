import { beforeEach, describe, expect, it, vi } from 'vitest'

import { ElMessage, ElMessageBox } from 'element-plus'
import type { MessageBoxData } from 'element-plus'
import { useConfirm } from '@/composables/useConfirm'

vi.mock('element-plus', () => ({
  ElMessage: vi.fn(),
  ElMessageBox: { confirm: vi.fn() },
}))

// confirm 真实返回 MessageBoxData，测试仅关心 resolve/reject 语义，以类型断言收敛
const confirmMock = vi.mocked(ElMessageBox.confirm)
const confirmed = 'confirm' as unknown as MessageBoxData
const messageMock = vi.mocked(ElMessage)

beforeEach(() => {
  vi.clearAllMocks()
})

describe('useConfirm 删除确认全流程', () => {
  it('确认后调用 API，弹成功提示并 resolve true', async () => {
    confirmMock.mockResolvedValue(confirmed)
    const api = vi.fn().mockResolvedValue(undefined)
    const run = useConfirm('确定删除该记录吗？', api)

    await expect(run()).resolves.toBe(true)

    expect(confirmMock).toHaveBeenCalledWith(
      '确定删除该记录吗？',
      '提示',
      expect.objectContaining({
        type: 'warning',
        confirmButtonText: '确定',
        cancelButtonText: '取消',
      }),
    )
    expect(api).toHaveBeenCalledTimes(1)
    expect(messageMock).toHaveBeenCalledWith({
      type: 'success',
      message: '操作成功',
      duration: 3000,
      showClose: true,
    })
  })

  it('自定义 title 与 successMessage 生效', async () => {
    confirmMock.mockResolvedValue(confirmed)
    const api = vi.fn().mockResolvedValue(undefined)
    const run = useConfirm('确认下架？', api, {
      title: '下架确认',
      successMessage: '已下架',
    })

    await run()

    expect(confirmMock).toHaveBeenCalledWith(
      '确认下架？',
      '下架确认',
      expect.anything(),
    )
    expect(messageMock).toHaveBeenCalledWith(
      expect.objectContaining({ message: '已下架' }),
    )
  })

  it('取消确认：不调 API、不弹提示、resolve false', async () => {
    confirmMock.mockRejectedValue('cancel')
    const api = vi.fn()
    const run = useConfirm('确定删除？', api)

    await expect(run()).resolves.toBe(false)

    expect(api).not.toHaveBeenCalled()
    expect(messageMock).not.toHaveBeenCalled()
  })

  it('API 失败：resolve false 且不弹成功提示（错误提示由全局拦截器承担）', async () => {
    confirmMock.mockResolvedValue(confirmed)
    const api = vi.fn().mockRejectedValue(new Error('server error'))
    const run = useConfirm('确定删除？', api)

    await expect(run()).resolves.toBe(false)

    expect(api).toHaveBeenCalledTimes(1)
    expect(messageMock).not.toHaveBeenCalled()
  })
})
