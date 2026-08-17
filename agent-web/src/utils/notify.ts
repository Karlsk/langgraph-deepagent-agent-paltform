import { ElMessage } from 'element-plus'

/**
 * 统一通知封装：底层调用 ElMessage，统一配置 duration 与 showClose，
 * 保证全站提示信息的一致外观与停留时长。
 */

/** 成功提示：停留 3 秒 */
export function notifySuccess(message: string): void {
  ElMessage({ type: 'success', message, duration: 3000, showClose: true })
}

/** 错误提示：停留 5 秒（错误信息通常需要更长阅读时间） */
export function notifyError(message: string): void {
  ElMessage({ type: 'error', message, duration: 5000, showClose: true })
}

/** 警告提示：停留 3 秒 */
export function notifyWarning(message: string): void {
  ElMessage({ type: 'warning', message, duration: 3000, showClose: true })
}
