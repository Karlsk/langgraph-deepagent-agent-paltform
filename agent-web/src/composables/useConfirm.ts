import { ElMessageBox } from 'element-plus'

import { notifySuccess } from '@/utils/notify'

export interface UseConfirmOptions {
  /** 确认框标题，默认“提示” */
  title?: string
  /** 成功提示文案，默认“操作成功” */
  successMessage?: string
}

/**
 * 删除确认 composable：一行代码完成“确认删除 → 调接口 → 提示成功”全流程。
 *
 * 返回的函数 resolve true 表示确认并执行成功；resolve false 表示用户取消
 * 或 API 失败（错误提示由统一请求层全局拦截器承担，不弹重复错误）。
 */
export function useConfirm(
  message: string,
  api: () => Promise<unknown>,
  options?: UseConfirmOptions,
): () => Promise<boolean> {
  return async (): Promise<boolean> => {
    try {
      await ElMessageBox.confirm(message, options?.title ?? '提示', {
        type: 'warning',
        confirmButtonText: '确定',
        cancelButtonText: '取消',
      })
    } catch {
      // 用户点击取消或关闭确认框，不调用 API
      return false
    }

    try {
      await api()
    } catch {
      // 错误提示由全局拦截器承担，此处只收敛为失败态
      return false
    }

    notifySuccess(options?.successMessage ?? '操作成功')
    return true
  }
}
