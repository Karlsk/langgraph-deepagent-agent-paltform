/**
 * src/api/mcp.ts API 模块契约测试：
 * - mock `@/utils/request` 的 get/post/patch/del，断言 10 个 MCP 接口
 *   的 URL / params / payload 形态与返回类型；
 * - 不发起真实网络请求（vi.mock 在模块加载时替换依赖）。
 */
import { beforeEach, describe, expect, it, vi } from 'vitest'

import {
  callMcpServerTool,
  createMcpServer,
  deleteMcpServer,
  getMcpServer,
  listMcpServerTools,
  listMcpServers,
  listMcpServersPage,
  listStdioManifests,
  patchMcpServer,
  syncStdioManifests,
  type McpServerCreatePayload,
  type McpServerPatchPayload,
  type McpServerRow,
  type McpToolCallRequest,
  type McpToolCallResult,
  type McpToolInfo,
  type StdioSyncReport,
} from '@/api/mcp'
import type { PageResult } from '@/types'

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

/** 最小化 mock 行（与后端 McpServerRead 契约一致） */
function rowFixture(overrides: Partial<McpServerRow> = {}): McpServerRow {
  return {
    name: 'stdio-demo',
    transport: 'stdio',
    command: 'python',
    args: ['/app/server.py'],
    env: { TOKEN: '${TOKEN}' },
    url: null,
    headers: {},
    enabled: true,
    description: 'stdio demo',
    content_hash: 'h1',
    created_by: 'seed',
    ...overrides,
  }
}

describe('@/api/mcp 列表与分页', () => {
  it('listMcpServers: GET /mcp-servers', async () => {
    const rows = [rowFixture()]
    requestMock.get.mockResolvedValueOnce(rows)

    await expect(listMcpServers()).resolves.toEqual(rows)
    expect(requestMock.get).toHaveBeenCalledWith('/mcp-servers')
  })

  it('listMcpServersPage: 默认空参时仅传空 params 对象', async () => {
    const page: PageResult<McpServerRow> = {
      items: [rowFixture()],
      total: 1,
      page: 1,
      pageSize: 10,
    }
    requestMock.get.mockResolvedValueOnce(page)

    await expect(listMcpServersPage()).resolves.toEqual(page)
    expect(requestMock.get).toHaveBeenCalledWith('/mcp-servers/page', {
      params: { page: undefined, pageSize: undefined, keyword: undefined },
    })
  })

  it('listMcpServersPage: 透传 keyword / page / pageSize', async () => {
    const page: PageResult<McpServerRow> = {
      items: [rowFixture({ name: 'echo' })],
      total: 1,
      page: 2,
      pageSize: 5,
    }
    requestMock.get.mockResolvedValueOnce(page)

    await expect(
      listMcpServersPage({ keyword: 'echo', page: 2, pageSize: 5 }),
    ).resolves.toEqual(page)
    expect(requestMock.get).toHaveBeenCalledWith('/mcp-servers/page', {
      params: { page: 2, pageSize: 5, keyword: 'echo' },
    })
  })
})

describe('@/api/mcp CRUD', () => {
  it('getMcpServer: GET /mcp-servers/{name}（name 走 encodeURIComponent）', async () => {
    const row = rowFixture({ name: 'echo server' })
    requestMock.get.mockResolvedValueOnce(row)

    await expect(getMcpServer('echo server')).resolves.toEqual(row)
    expect(requestMock.get).toHaveBeenCalledWith('/mcp-servers/echo%20server')
  })

  it('createMcpServer: POST /mcp-servers，body 透传', async () => {
    const payload: McpServerCreatePayload = {
      name: 'echo-sse',
      transport: 'sse',
      url: 'http://127.0.0.1:9375/sse',
      headers: { Authorization: 'Bearer xxx' },
      enabled: true,
      description: 'echo sse demo',
    }
    const created = rowFixture({
      name: payload.name,
      transport: 'sse',
      url: payload.url,
      headers: payload.headers ?? {},
      command: null,
      args: [],
      env: {},
      enabled: true,
      description: payload.description ?? '',
    })
    requestMock.post.mockResolvedValueOnce(created)

    await expect(createMcpServer(payload)).resolves.toEqual(created)
    expect(requestMock.post).toHaveBeenCalledWith('/mcp-servers', payload)
  })

  it('patchMcpServer: PATCH /mcp-servers/{name}，body 透传', async () => {
    const patchBody: McpServerPatchPayload = { description: 'updated desc', enabled: false }
    const updated = rowFixture({ ...patchBody })
    requestMock.patch.mockResolvedValueOnce(updated)

    await expect(patchMcpServer('stdio-demo', patchBody)).resolves.toEqual(updated)
    expect(requestMock.patch).toHaveBeenCalledWith('/mcp-servers/stdio-demo', patchBody)
  })

  it('deleteMcpServer: DELETE /mcp-servers/{name}，无 body', async () => {
    requestMock.del.mockResolvedValueOnce(null)

    await expect(deleteMcpServer('stdio-demo')).resolves.toBeNull()
    expect(requestMock.del).toHaveBeenCalledWith('/mcp-servers/stdio-demo')
  })
})

describe('@/api/mcp 调试端点', () => {
  it('listMcpServerTools: GET /mcp-servers/{name}/tools', async () => {
    const tools: McpToolInfo[] = [
      {
        name: 'echo',
        description: 'echo back the input',
        args_schema: { type: 'object', properties: { msg: { type: 'string' } } },
      },
    ]
    requestMock.get.mockResolvedValueOnce(tools)

    await expect(listMcpServerTools('stdio-demo')).resolves.toEqual(tools)
    expect(requestMock.get).toHaveBeenCalledWith('/mcp-servers/stdio-demo/tools')
  })

  it('callMcpServerTool: POST /mcp-servers/{name}/call-tool，body 透传', async () => {
    const payload: McpToolCallRequest = { tool_name: 'echo', arguments: { msg: 'hi' } }
    const result: McpToolCallResult = {
      server: 'stdio-demo',
      tool_name: 'echo',
      result: { echoed: 'hi' },
    }
    requestMock.post.mockResolvedValueOnce(result)

    await expect(callMcpServerTool('stdio-demo', payload)).resolves.toEqual(result)
    expect(requestMock.post).toHaveBeenCalledWith(
      '/mcp-servers/stdio-demo/call-tool',
      payload,
    )
  })
})

describe('@/api/mcp stdio manifest 同步', () => {
  it('listStdioManifests: GET /mcp-servers/stdio-manifests（dry-run）', async () => {
    const report: StdioSyncReport = {
      scanned: 3,
      created: ['a', 'b'],
      updated: [],
      unchanged: ['c'],
      skipped: [],
      invalid: [],
    }
    requestMock.get.mockResolvedValueOnce(report)

    await expect(listStdioManifests()).resolves.toEqual(report)
    expect(requestMock.get).toHaveBeenCalledWith('/mcp-servers/stdio-manifests')
  })

  it('syncStdioManifests: POST /mcp-servers/stdio-sync（无 body）', async () => {
    const report: StdioSyncReport = {
      scanned: 3,
      created: ['a'],
      updated: ['b'],
      unchanged: ['c'],
      skipped: [{ name: 'x', reason: 'no command' }],
      invalid: [{ file: '/tmp/bad.json', reason: 'invalid JSON' }],
    }
    requestMock.post.mockResolvedValueOnce(report)

    await expect(syncStdioManifests()).resolves.toEqual(report)
    expect(requestMock.post).toHaveBeenCalledWith('/mcp-servers/stdio-sync')
  })
})