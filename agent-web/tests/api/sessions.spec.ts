/**
 * src/api/sessions.ts API 模块契约测试（G3 spec-g3-session §11.6/§11.8）：
 * - mock `@/utils/request` 的 get/post/patch/del，断言各函数的 URL /
 *   参数与返回类型透传；
 * - 不发起真实网络请求（vi.mock 在模块加载时替换依赖）；
 * - export 走非信封文件下载（responseType blob，越权场景后端返回 404，
 *   由统一请求层拦截器弹错并 reject，此处断言 reject 透传）。
 */
import { beforeEach, describe, expect, it, vi } from 'vitest'

import {
  createSession,
  deleteSession,
  exportSessionHistory,
  getSession,
  listSessions,
  updateSession,
  type SessionRead,
} from '@/api/sessions'

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

/** 与后端 SessionRead 契约一致的一行样例 */
const ROW: SessionRead = {
  session_id: '7d2f0c9e-9a41-4a1f-8f3e-3c1b0f2a9d55',
  name: '需求评审对话',
  agent_app_id: 3,
  created_at: '2026-08-27T08:00:00+00:00',
  updated_at: null,
  message_count: null,
}

describe('sessions.ts 列表契约（GET /sessions 根分页）', () => {
  it('listSessions 默认发 GET /sessions 并透传 page/pageSize', async () => {
    const page = { items: [ROW], total: 1, page: 1, pageSize: 20 }
    requestMock.get.mockResolvedValue(page)

    const result = await listSessions({ page: 1, pageSize: 20 })

    expect(requestMock.get).toHaveBeenCalledWith('/sessions', {
      params: { page: 1, pageSize: 20, agent_app_id: undefined },
    })
    expect(result).toEqual(page)
  })

  it('listSessions 把 agentAppId 映射为后端 agent_app_id 过滤参数', async () => {
    const page = { items: [], total: 0, page: 1, pageSize: 10 }
    requestMock.get.mockResolvedValue(page)

    await listSessions({ page: 1, pageSize: 10, agentAppId: 7 })

    expect(requestMock.get).toHaveBeenCalledWith('/sessions', {
      params: { page: 1, pageSize: 10, agent_app_id: 7 },
    })
  })
})

describe('sessions.ts 单条 / CRUD 契约', () => {
  it('getSession 发 GET /sessions/{session_id}', async () => {
    requestMock.get.mockResolvedValue({ ...ROW, message_count: 4 })

    const result = await getSession(ROW.session_id)

    expect(requestMock.get).toHaveBeenCalledWith(`/sessions/${ROW.session_id}`)
    expect(result.message_count).toBe(4)
  })

  it('createSession 发 POST /sessions 并透传 payload（agent_app_id 必填）', async () => {
    requestMock.post.mockResolvedValue(ROW)

    const result = await createSession({ agent_app_id: 3, name: '需求评审对话' })

    expect(requestMock.post).toHaveBeenCalledWith('/sessions', {
      agent_app_id: 3,
      name: '需求评审对话',
    })
    expect(result).toEqual(ROW)
  })

  it('updateSession 发 PATCH /sessions/{session_id}（仅 name 可改）', async () => {
    requestMock.patch.mockResolvedValue({ ...ROW, name: '新名字' })

    const result = await updateSession(ROW.session_id, { name: '新名字' })

    expect(requestMock.patch).toHaveBeenCalledWith(`/sessions/${ROW.session_id}`, {
      name: '新名字',
    })
    expect(result.name).toBe('新名字')
  })

  it('deleteSession 发 DELETE /sessions/{session_id} 并以 void 承接', async () => {
    requestMock.del.mockResolvedValue(null)

    await deleteSession(ROW.session_id)

    expect(requestMock.del).toHaveBeenCalledWith(`/sessions/${ROW.session_id}`)
  })
})

describe('sessions.ts 导出契约（非信封文件下载）', () => {
  it('exportSessionHistory 默认 json 并以 blob 接收', async () => {
    const blob = new Blob(['{}'], { type: 'application/json' })
    requestMock.get.mockResolvedValue(blob)

    const result = await exportSessionHistory(ROW.session_id)

    expect(requestMock.get).toHaveBeenCalledWith(`/sessions/${ROW.session_id}/export`, {
      params: { format: 'json' },
      responseType: 'blob',
    })
    expect(result).toBe(blob)
  })

  it('exportSessionHistory 支持 jsonl 格式参数透传', async () => {
    const blob = new Blob([''], { type: 'application/x-ndjson' })
    requestMock.get.mockResolvedValue(blob)

    await exportSessionHistory(ROW.session_id, 'jsonl')

    expect(requestMock.get).toHaveBeenCalledWith(`/sessions/${ROW.session_id}/export`, {
      params: { format: 'jsonl' },
      responseType: 'blob',
    })
  })

  it('越权 / 不存在的 session 导出：404 reject 原样透传给调用方', async () => {
    requestMock.get.mockRejectedValue(new Error('404'))

    await expect(exportSessionHistory('ghost')).rejects.toThrow('404')
  })
})
