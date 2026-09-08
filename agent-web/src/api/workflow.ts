/**
 * Workflow API 模块：对接后端 workflow 资源（GET /workflows 列表、GET /workflows/{id}
 * 详情、PUT /workflows/{id} 全量注册、DELETE /workflows/{id} 删除、
 * POST /workflows/{id}/execute 执行）。
 *
 * 约定：
 * - 响应信封 {code, message, data} 已由 request.ts 拦截器解包，本模块函数
 *   返回值即 data 载荷；
 * - 行字段一律 snake_case（与后端 WorkflowDefinition / ExecutionLog Pydantic
 *   schema 对齐）；
 * - execute 端点可能长时间运行，timeout 透传 600s（10 分钟）。
 */
import { del, get, post, put } from '@/utils/request'

export interface WorkflowSummary {
  workflow_id: string
  node_count: number
  entry_point: string
  description?: string
}

export interface StateFieldDTO {
  type: string
  default?: unknown
  description?: string
  reducer?: 'add' | 'last' | null
}

export interface NodeDTO {
  name: string
  type: 'llm' | 'http'
  config: Record<string, unknown>
}

export interface EdgeDTO {
  source: string
  target: string
  condition?: string | null
}

export interface UiLayoutDTO {
  nodes?: Record<string, { x: number; y: number }>
}

export interface WorkflowDefinitionDTO {
  workflow_id: string
  entry_point: string
  nodes: NodeDTO[]
  edges: EdgeDTO[]
  state_schema: Record<string, StateFieldDTO>
  ui_layout?: UiLayoutDTO
}

export interface ExecutionLogView {
  node_name: string
  node_type: string
  timestamp: string
  input_data: unknown
  output_data: unknown
  execution_time_ms: number
  error: string | null
}

export interface WorkflowExecuteResult {
  output: Record<string, unknown>
  metadata: Record<string, unknown> & { execution_logs?: ExecutionLogView[] }
}

export function listWorkflows(): Promise<WorkflowSummary[]> {
  return get<WorkflowSummary[]>('/workflows')
}

export function getWorkflow(
  id: string,
  format: 'json' | 'yaml' = 'json',
): Promise<WorkflowDefinitionDTO | { yaml_text: string }> {
  return get<WorkflowDefinitionDTO | { yaml_text: string }>('/workflows/' + id, {
    params: { format },
  })
}

export function saveWorkflow(
  id: string,
  definition: WorkflowDefinitionDTO,
): Promise<WorkflowDefinitionDTO> {
  return put<WorkflowDefinitionDTO>('/workflows/' + id, definition)
}

export function deleteWorkflow(id: string): Promise<null> {
  return del<null>('/workflows/' + id)
}

export function executeWorkflow(
  id: string,
  input: Record<string, unknown>,
): Promise<WorkflowExecuteResult> {
  return post<WorkflowExecuteResult>('/workflows/' + id + '/execute', { input }, { timeout: 600000 })
}
