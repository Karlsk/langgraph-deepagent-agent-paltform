import { describe, expect, it } from 'vitest'

import { isBackendConnected } from '@/views/provider/connection'

describe('后端连接状态', () => {
  it('健康检查成功时返回已连接', async () => {
    await expect(
      isBackendConnected(async () => ({ status: 'healthy', version: '1.0.0' })),
    ).resolves.toBe(true)
  })

  it('健康检查失败时返回未连接', async () => {
    await expect(
      isBackendConnected(async () => {
        throw new Error('backend unavailable')
      }),
    ).resolves.toBe(false)
  })
})
