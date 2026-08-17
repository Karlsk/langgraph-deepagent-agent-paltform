import type { PageQuery, PageResult } from '@/types'

/**
 * 本地分页适配器：把后端裸列表包装为 PageResult<T>。
 *
 * 后端当前所有列表端点均返回裸 list（无分页参数），WebAgentTable 坚持
 * PageResult 契约先行 —— 父组件现阶段用本函数做前端分页，未来后端提供
 * 真分页端点后只需替换 api 实现（透传 query 到 config.params），组件零改动。
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
