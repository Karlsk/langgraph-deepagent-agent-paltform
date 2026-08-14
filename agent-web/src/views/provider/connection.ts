import { getHealth } from '@/api/health'

/** 检查后端健康接口是否可访问。 */
export async function isBackendConnected(
  healthRequest: () => Promise<unknown> = getHealth,
): Promise<boolean> {
  try {
    await healthRequest()
    return true
  } catch {
    return false
  }
}
