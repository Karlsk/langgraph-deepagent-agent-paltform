/**
 * src/api/workflow.ts API 模块契约测试：
 * - mock `@/utils/request` 的 get/post/put/del，断言 5 个 workflow 接口
 *   的 URL / params / payload / timeout 形态与返回类型；
 * - 不发起真实网络请求（vi.mock 在模块加载时替换依赖）。
 */
import { beforeEach, describe, expect, it, vi } from 'vitest'

import {
  deleteWorkflow,
  executeWorkflow,
  getWorkflow,
  listWorkflows,
  saveWorkflow,
  type EdgeDTO,
  type ExecutionLogView,
  type NodeDTO,
  type StateFieldDTO,
  type UiLayoutDTO,
  type WorkflowDefinitionDTO,
  type WorkflowExecuteResult,
  type WorkflowSummary,
} from '@/api/workflow'

const requestMock = vi.hoisted(() => ({
  get: vi.fn(),
  post: vi.fn(),
  put: vi.fn(),
  del: vi.fn(),
}))

vi.mock('@/utils/request', () => requestMock)

beforeEach(() => {
  vi.clearAllMocks()
})

function summaryFixture(overrides: Partial<WorkflowSummary> = {}): WorkflowSummary {
  return {
    workflow_id: 'demo-flow',
    node_count: 3,
    entry_point: 'start',
    description: 'Demo workflow',
    ...overrides,
  }
}

function definitionFixture(overrides: Partial<WorkflowDefinitionDTO> = {}): WorkflowDefinitionDTO {
  return {
    workflow_id: 'demo-flow',
    entry_point: 'start',
    nodes: [{ name: 'start', type: 'llm', config: { prompt: 'hello' } } satisfies NodeDTO],
    edges: [{ source: 'start', target: 'end' } satisfies EdgeDTO],
    state_schema: { messages: { type: 'list', reducer: 'add' } satisfies StateFieldDTO },
    ui_layout: { nodes: { start: { x: 0, y: 0 } } } satisfies UiLayoutDTO,
    ...overrides,
  }
}

describe('@/api/workflow 列表与详情', () => {
  it('listWorkflows: GET /workflows → 返回解包后数组', async () => {
    const rows = [summaryFixture(), summaryFixture({ workflow_id: 'another-flow' })]
    requestMock.get.mockResolvedValueOnce(rows)

    await expect(listWorkflows()).resolves.toEqual(rows)
    expect(requestMock.get).toHaveBeenCalledWith('/workflows')
  })

  it('getWorkflow: 默认 format=json → GET /workflows/{id}', async () => {
    const def = definitionFixture()
    requestMock.get.mockResolvedValueOnce(def)

    await expect(getWorkflow('demo-flow')).resolves.toEqual(def)
    expect(requestMock.get).toHaveBeenCalledWith('/workflows/demo-flow', {
      params: { format: 'json' },
    })
  })

  it('getWorkflow: format=yaml → params 携带 format=yaml', async () => {
    const yamlPayload = { yaml_text: 'workflow_id: demo-flow\n' }
    requestMock.get.mockResolvedValueOnce(yamlPayload)

    await expect(getWorkflow('demo-flow', 'yaml')).resolves.toEqual(yamlPayload)
    expect(requestMock.get).toHaveBeenCalledWith('/workflows/demo-flow', {
      params: { format: 'yaml' },
    })
  })
})

describe('@/api/workflow 写路径（spec-16 预留）', () => {
  it('saveWorkflow: PUT /workflows/{id} 全量 body', async () => {
    const def = definitionFixture()
    requestMock.put.mockResolvedValueOnce(def)

    await expect(saveWorkflow('demo-flow', def)).resolves.toEqual(def)
    expect(requestMock.put).toHaveBeenCalledWith('/workflows/demo-flow', def)
  })

  it('deleteWorkflow: DEL /workflows/{id}', async () => {
    requestMock.del.mockResolvedValueOnce(null)

    await expect(deleteWorkflow('demo-flow')).resolves.toBeNull()
    expect(requestMock.del).toHaveBeenCalledWith('/workflows/demo-flow')
  })
})

describe('@/api/workflow 执行', () => {
  it('executeWorkflow: POST /workflows/{id}/execute 携带 input + timeout 600s', async () => {
    const result: WorkflowExecuteResult = {
      output: { messages: ['hi'] },
      metadata: {
        workflow_id: 'demo-flow',
        success: true,
        node_count: 1,
        execution_time_ms: 120,
        execution_logs: [
          {
            node_name: 'start',
            node_type: 'llm',
            timestamp: '2026-09-07T10:00:00Z',
            input_data: { prompt: 'hello' },
            output_data: { text: 'hi' },
            execution_time_ms: 100,
            error: null,
          } satisfies ExecutionLogView,
        ],
      },
    }
    requestMock.post.mockResolvedValueOnce(result)

    const input = { messages: ['hello'] }
    await expect(executeWorkflow('demo-flow', input)).resolves.toEqual(result)
    expect(requestMock.post).toHaveBeenCalledWith(
      '/workflows/demo-flow/execute',
      { input },
      { timeout: 600000 },
    )
  })
})
