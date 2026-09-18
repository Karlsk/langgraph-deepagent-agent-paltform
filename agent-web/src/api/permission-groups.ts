/**
 * Permission Group API 模块：对接后端 `/permission-groups` 前缀的权限组资源
 * 全部端点（`app/api/v1/permission_groups.py`，5 个端点）。
 *
 * 权限组是命名的工具审批集合，AgentApp 可绑定权限组以复用审批配置。
 * 两层权限系统：
 * 1. permission_preset（枚举）：none / strict / destructive_only
 * 2. permission_group_id（外键）：指向本模块管理的命名权限组
 *
 * 解析优先级（assembly.py resolve_interrupt_on）：
 * permission_group_id > permission_preset > interrupt_on（legacy）
 *
 * 约定：
 * - 响应信封 {code, message, data} 已由 request.ts 拦截器解包，本模块函数
 *   返回值即 data 载荷；
 * - 全量列表端点（GET /permission-groups）返回裸数组；分页端点
 *   （GET /permission-groups/page）返回 PageResult<PermissionGroupRow>；
 * - 路径参数 group_id 为整数主键，无需 encodeURIComponent；
 * - name 全局唯一，创建重名后端 422。
 */
import { del, get, patch, post } from '@/utils/request'
import type { PageQuery, PageResult } from '@/types'

/** 权限预设枚举值（与后端 PERMISSION_PRESETS 一致） */
export type PermissionPreset = 'none' | 'strict' | 'destructive_only'

/** 权限组资产行（对应后端 PermissionGroupRead 全字段） */
export interface PermissionGroupRow {
  id: number
  name: string
  /** 需要人工审批的工具名称列表 */
  tool_names: string[]
  description: string
  created_by: string | null
}

/**
 * 权限组创建 payload（POST /permission-groups）。
 * name 必填且全局唯一；tool_names 默认为空列表；description 默认为空字符串。
 */
export interface PermissionGroupCreatePayload {
  name: string
  tool_names?: string[]
  description?: string
}

/**
 * 权限组部分更新 payload（PATCH /permission-groups/{group_id}，name 不可改）。
 * 空 payload 后端 422 拒绝（"nothing to update"）。
 */
export interface PermissionGroupPatchPayload {
  tool_names?: string[]
  description?: string
}

/** 把 PageQuery 透传为后端查询参数（page/pageSize/keyword） */
function toParams(query: PageQuery): Record<string, unknown> {
  return { page: query.page, pageSize: query.pageSize, keyword: query.keyword }
}

/** 全量列表：GET /permission-groups（按 id 升序的裸数组） */
export function listPermissionGroups(): Promise<PermissionGroupRow[]> {
  return get<PermissionGroupRow[]>('/permission-groups')
}

/** 分页列表：GET /permission-groups/page（keyword 模糊匹配 name/description） */
export function listPermissionGroupsPage(
  query: PageQuery = {},
): Promise<PageResult<PermissionGroupRow>> {
  return get<PageResult<PermissionGroupRow>>('/permission-groups/page', { params: toParams(query) })
}

/** 单条详情：GET /permission-groups/{group_id}（不存在 404） */
export function getPermissionGroup(groupId: number): Promise<PermissionGroupRow> {
  return get<PermissionGroupRow>(`/permission-groups/${groupId}`)
}

/** 创建权限组：POST /permission-groups（201；重名 422） */
export function createPermissionGroup(
  payload: PermissionGroupCreatePayload,
): Promise<PermissionGroupRow> {
  return post<PermissionGroupRow>('/permission-groups', payload)
}

/** 部分更新：PATCH /permission-groups/{group_id}（name 不可改） */
export function patchPermissionGroup(
  groupId: number,
  payload: PermissionGroupPatchPayload,
): Promise<PermissionGroupRow> {
  return patch<PermissionGroupRow>(`/permission-groups/${groupId}`, payload)
}

/** 删除权限组：DELETE /permission-groups/{group_id} */
export function deletePermissionGroup(groupId: number): Promise<null> {
  return del<null>(`/permission-groups/${groupId}`)
}
