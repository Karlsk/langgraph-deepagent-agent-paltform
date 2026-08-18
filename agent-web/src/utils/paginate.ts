import type { PageQuery, PageResult } from '@/types'

/**
 * 本地分页适配器：把裸列表包装为 PageResult<T>。
 *
 * 后端已提供真分页端点（GET /<module>/page，返回 PageResult，见
 * @/api/assets.ts 的 listXxxPage 系列）；本适配器仅用于 mock 数据或
 * 已持有全量数组时的前端分页。WebAgentTable 坚持 PageResult 契约先行，
 * 两种数据源可无缝切换，组件零改动。
 */
export function paginateLocal<T>(
  items: T[],
  query: PageQuery,
  filter?: (item: T) => boolean,
): PageResult<T> {
  const page = query.page ?? 1
  const pageSize = query.pageSize ?? 10
  const filtered = filter ? items.filter(filter) : items
  const start = (page - 1) * pageSize

  return {
    items: filtered.slice(start, start + pageSize),
    total: filtered.length,
    page,
    pageSize,
  }
}
