import { beforeEach, describe, expect, it, vi } from 'vitest'

import { ElMessage } from 'element-plus'
import { notifyError, notifySuccess, notifyWarning } from '@/utils/notify'

vi.mock('element-plus', () => ({
  ElMessage: vi.fn(),
}))

beforeEach(() => {
  vi.clearAllMocks()
})

describe('统一通知封装', () => {
  it('notifySuccess：success 类型、3 秒停留、可手动关闭', () => {
    notifySuccess('保存成功')

    expect(ElMessage).toHaveBeenCalledWith({
      type: 'success',
      message: '保存成功',
      duration: 3000,
      showClose: true,
    })
  })

  it('notifyWarning：warning 类型、3 秒停留、可手动关闭', () => {
    notifyWarning('请先选择记录')

    expect(ElMessage).toHaveBeenCalledWith({
      type: 'warning',
      message: '请先选择记录',
      duration: 3000,
      showClose: true,
    })
  })

  it('notifyError：error 类型、5 秒停留（唯一差异项）、可手动关闭', () => {
    notifyError('删除失败')

    expect(ElMessage).toHaveBeenCalledWith({
      type: 'error',
      message: '删除失败',
      duration: 5000,
      showClose: true,
    })
  })
})
