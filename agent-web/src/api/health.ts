import { get } from '@/utils/request'

/** `/api/v1/health` 返回的服务健康状态。 */
export interface HealthStatus {
  status: string
  version: string
}

/** 获取 API 服务健康状态。 */
export function getHealth(): Promise<HealthStatus> {
  return get<HealthStatus>('health')
}
