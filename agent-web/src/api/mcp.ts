/**
 * MCP Server API 模块：对接后端 MCP server 资产（见 `app/api/v1/mcp_servers.py`）
 * 与 `app/schemas/agent_apps.py` 的 Pydantic DTO。
 *
 * 模块职责：
 * - CRUD：`POST/GET/PATCH/DELETE /mcp-servers[/<name>]`
 * - 工具调试：`GET /mcp-servers/<name>/tools`、`POST /mcp-servers/<name>/call-tool`
 * - stdio manifest 自动发现：`GET /mcp-servers/stdio-manifests`、`POST /mcp-servers/stdio-sync`
 *
 * 约定：
 * - 响应信封 {code, message, data} 已由 request.ts 拦截器解包，本模块函数
 *   返回值即 data 载荷；
 * - 全量列表端点（GET /mcp-servers）返回裸数组；分页端点（GET /mcp-servers/page）
 *   返回 PageResult<T>（后端字段 pageSize 为驼峰，其余行字段为 snake_case）；
 * - 所有端点需登录态（get_current_session），token 注入由 request.ts 拦截器
 *   统一处理；
 * - transport 取值 `'stdio' | 'sse' | 'http'`（后端 sse/http 共享 url 必填约束，
 *   `http` 是 `streamable_http` 的运行时别名；详见 `docs/mcp-manual-testing.md` §2）。
 */
import { del, get, patch, post } from '@/utils/request'
import type { PageQuery, PageResult } from '@/types'

// ---------------------------------------------------------------------------
// 类型（与后端 Pydantic DTO 1:1 对齐）
// ---------------------------------------------------------------------------

/** MCP server 传输后端（与后端 McpServerRead.transport 一致） */
export type McpTransport = 'stdio' | 'sse' | 'http'

/** MCP server 资产行（对应后端 McpServerRead） */
export interface McpServerRow {
  name: string
  transport: McpTransport
  command: string | null
  args: string[]
  env: Record<string, string>
  url: string | null
  headers: Record<string, string>
  enabled: boolean
  description: string
  content_hash: string
  created_by: string | null
}

/** 创建 payload（对应后端 McpServerCreate）；前端在提交前按 transport 组装必填字段 */
export interface McpServerCreatePayload {
  name: string
  transport: McpTransport
  command?: string
  args?: string[]
  env?: Record<string, string>
  url?: string
  headers?: Record<string, string>
  enabled?: boolean
  description?: string
}

/** 部分更新 payload（对应后端 McpServerUpdate；name 不可改） */
export interface McpServerPatchPayload {
  transport?: McpTransport
  command?: string
  args?: string[]
  env?: Record<string, string>
  url?: string
  headers?: Record<string, string>
  enabled?: boolean
  description?: string
}

/** 工具目录条目（对应后端 ToolCatalogEntry）；builtin 工具 server=null */
export interface ToolCatalogEntry {
  name: string
  source: 'builtin' | 'mcp'
  server: string | null
}

/** 调试端点单条工具信息（对应后端 McpToolInfo；裸工具名，无 {server}__ 前缀） */
export interface McpToolInfo {
  name: string
  description: string
  args_schema: Record<string, unknown>
}

/** 调试调用请求（对应后端 McpToolCallRequest） */
export interface McpToolCallRequest {
  tool_name: string
  arguments: Record<string, unknown>
}

/** 调试调用响应（对应后端 McpToolCallResult） */
export interface McpToolCallResult {
  server: string
  tool_name: string
  result: unknown
}

/** stdio manifest 同步报告 — skipped 项（被跳过的 server name + 原因） */
export interface StdioSyncSkippedItem {
  name: string
  reason: string
}

/** stdio manifest 同步报告 — invalid 项（坏文件路径 + 原因） */
export interface StdioSyncInvalidItem {
  file: string
  reason: string
}

/** stdio manifest 同步报告（与 docs/mcp-manual-testing.md §5.1 一致） */
export interface StdioSyncReport {
  scanned: number
  created: string[]
  updated: string[]
  unchanged: string[]
  skipped: StdioSyncSkippedItem[]
  invalid: StdioSyncInvalidItem[]
}

// ---------------------------------------------------------------------------
// 通用 helper
// ---------------------------------------------------------------------------

/** 把 PageQuery 透传为后端查询参数（page/pageSize/keyword） */
function toParams(query: PageQuery): Record<string, unknown> {
  return { page: query.page, pageSize: query.pageSize, keyword: query.keyword }
}

// ---------------------------------------------------------------------------
// CRUD
// ---------------------------------------------------------------------------

/** 全量列表：GET /mcp-servers */
export function listMcpServers(): Promise<McpServerRow[]> {
  return get<McpServerRow[]>('/mcp-servers')
}

/** 分页列表：GET /mcp-servers/page */
export function listMcpServersPage(
  query: PageQuery = {},
): Promise<PageResult<McpServerRow>> {
  return get<PageResult<McpServerRow>>('/mcp-servers/page', { params: toParams(query) })
}

/** 单条：GET /mcp-servers/{name} */
export function getMcpServer(name: string): Promise<McpServerRow> {
  return get<McpServerRow>(`/mcp-servers/${encodeURIComponent(name)}`)
}

/** 创建：POST /mcp-servers */
export function createMcpServer(payload: McpServerCreatePayload): Promise<McpServerRow> {
  return post<McpServerRow>('/mcp-servers', payload)
}

/** 局部更新（name 不可改）：PATCH /mcp-servers/{name} */
export function patchMcpServer(
  name: string,
  payload: McpServerPatchPayload,
): Promise<McpServerRow> {
  return patch<McpServerRow>(`/mcp-servers/${encodeURIComponent(name)}`, payload)
}

/** 物理删除：DELETE /mcp-servers/{name} */
export function deleteMcpServer(name: string): Promise<null> {
  return del<null>(`/mcp-servers/${encodeURIComponent(name)}`)
}

// ---------------------------------------------------------------------------
// 工具调试端点（实时探测，不读缓存）
// ---------------------------------------------------------------------------

/** 工具清单（裸名 + JSON Schema）：GET /mcp-servers/{name}/tools */
export function listMcpServerTools(name: string): Promise<McpToolInfo[]> {
  return get<McpToolInfo[]>(`/mcp-servers/${encodeURIComponent(name)}/tools`)
}

/** 工具调用（裸 tool_name）：POST /mcp-servers/{name}/call-tool */
export function callMcpServerTool(
  name: string,
  payload: McpToolCallRequest,
): Promise<McpToolCallResult> {
  return post<McpToolCallResult>(
    `/mcp-servers/${encodeURIComponent(name)}/call-tool`,
    payload,
  )
}

// ---------------------------------------------------------------------------
// 全局工具目录（SubAgent 允许的工具下拉选项来源）
// ---------------------------------------------------------------------------

/**
 * 全量工具目录：GET /tools/catalog。
 *
 * 运行时已注入的 builtin 工具 + 所有 enabled MCP server 注册的工具聚合视图，
 * 每条带 source / server 标记（参见 {@link ToolCatalogEntry}）。
 *
 * SubAgent 允许的工具下拉选项与后端 {@code allowed_tools} 字段命名空间一致：
 * - builtin：`entry.name`（裸名，如 `duckduckgo_results_json`）
 * - mcp：`entry.name` 已是 `${server}__${tool}` 命名空间名（如 `demo-stdio__echo`），
 *   直接作为 allowed_tools 取值，勿再拼接前缀（否则产生双前缀失效名）
 *
 * 响应内容由后端 `build_tool_catalog` 服务函数实时聚合，不读缓存；
 * tool 注册状态变化后调用本接口可拿到最新快照。
 */
export function listToolCatalog(): Promise<ToolCatalogEntry[]> {
  return get<ToolCatalogEntry[]>('/tools/catalog')
}

// ---------------------------------------------------------------------------
// stdio manifest 自动发现
// ---------------------------------------------------------------------------

/** 预览同步计划（dry-run，不写库不探测）：GET /mcp-servers/stdio-manifests */
export function listStdioManifests(): Promise<StdioSyncReport> {
  return get<StdioSyncReport>('/mcp-servers/stdio-manifests')
}

/** 执行同步（幂等 upsert by name）：POST /mcp-servers/stdio-sync */
export function syncStdioManifests(): Promise<StdioSyncReport> {
  return post<StdioSyncReport>('/mcp-servers/stdio-sync')
}