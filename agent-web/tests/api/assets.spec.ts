/**
 * src/api/assets.ts API 模块契约测试（scope 限本次新增的 skill 磁盘刷新与目录对账）：
 * - mock `@/utils/request` 的 get/post，断言各函数的 URL 与返回类型透传；
 * - 不发起真实网络请求（vi.mock 在模块加载时替换依赖）。
 */
import { beforeEach, describe, expect, it, vi } from 'vitest'

import {
  applySkillWorkspaceSync,
  planSkillWorkspaceSync,
  refreshAllSkills,
  refreshSkill,
  type SkillRefreshReport,
  type SkillSyncReport,
} from '@/api/assets'

const requestMock = vi.hoisted(() => ({
  get: vi.fn(),
  post: vi.fn(),
  patch: vi.fn(),
  del: vi.fn(),
}))

vi.mock('@/utils/request', () => requestMock)

beforeEach(() => {
  vi.clearAllMocks()
})

describe('assets.ts skill 磁盘刷新契约（dual-store refresh API）', () => {
  it('refreshAllSkills 发 POST /skills/refresh 并透传报告', async () => {
    const report: SkillRefreshReport = {
      items: [{ name: 'pdf-export', action: 'rewritten' }],
      total: 1,
      rewritten: 1,
      unchanged: 0,
      backfilled: 0,
      missing: 0,
    }
    requestMock.post.mockResolvedValue(report)

    const result = await refreshAllSkills()

    expect(requestMock.post).toHaveBeenCalledWith('/skills/refresh')
    expect(result).toEqual(report)
  })

  it('refreshSkill 发 POST /skills/{name}/refresh（name 做 URL 编码）', async () => {
    const report: SkillRefreshReport = {
      items: [{ name: 'pdf-export', action: 'unchanged' }],
      total: 1,
      rewritten: 0,
      unchanged: 1,
      backfilled: 0,
      missing: 0,
    }
    requestMock.post.mockResolvedValue(report)

    const result = await refreshSkill('pdf-export')

    expect(requestMock.post).toHaveBeenCalledWith('/skills/pdf-export/refresh')
    expect(result).toEqual(report)
  })
})

describe('assets.ts skill 目录对账契约（workspace-sync API）', () => {
  it('planSkillWorkspaceSync 发 GET /skills/workspace-sync 并透传预览报告', async () => {
    const report: SkillSyncReport = {
      items: [
        { name: 'pdf-export', action: 'unchanged' },
        { name: 'stray', action: 'imported' },
      ],
      scanned: 2,
      unchanged: 1,
      rewritten: 0,
      imported: 1,
      invalid: 0,
    }
    requestMock.get.mockResolvedValue(report)

    const result = await planSkillWorkspaceSync()

    expect(requestMock.get).toHaveBeenCalledWith('/skills/workspace-sync')
    expect(result).toEqual(report)
  })

  it('applySkillWorkspaceSync 发 POST /skills/workspace-sync 并透传执行报告', async () => {
    const report: SkillSyncReport = {
      items: [
        { name: 'broken/SKILL.md', action: 'invalid', reason: 'file exceeds the sync limit' },
      ],
      scanned: 3,
      unchanged: 2,
      rewritten: 0,
      imported: 0,
      invalid: 1,
    }
    requestMock.post.mockResolvedValue(report)

    const result = await applySkillWorkspaceSync()

    expect(requestMock.post).toHaveBeenCalledWith('/skills/workspace-sync')
    expect(result).toEqual(report)
  })
})
