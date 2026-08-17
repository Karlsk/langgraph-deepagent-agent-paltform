import { describe, expect, it } from 'vitest'

import { paginateLocal } from '@/utils/paginate'

const items = Array.from({ length: 25 }, (_, index) => ({ id: index + 1 }))

describe('paginateLocal 本地分页适配器', () => {
  it('默认 page=1 / pageSize=10：返回首页切片与总数', () => {
    const result = paginateLocal(items, {})

    expect(result.items).toHaveLength(10)
    expect(result.items[0]).toEqual({ id: 1 })
    expect(result.items[9]).toEqual({ id: 10 })
    expect(result).toMatchObject({ total: 25, page: 1, pageSize: 10 })
  })

  it('中间页切片正确', () => {
    const result = paginateLocal(items, { page: 3, pageSize: 10 })

    expect(result.items[0]).toEqual({ id: 21 })
    expect(result.items).toHaveLength(5)
    expect(result.total).toBe(25)
  })

  it('超出页码返回空 items，total 不变', () => {
    const result = paginateLocal(items, { page: 99, pageSize: 10 })

    expect(result.items).toEqual([])
    expect(result.total).toBe(25)
    expect(result).toMatchObject({ page: 99, pageSize: 10 })
  })

  it('filter 谓词生效：total 为过滤后总数', () => {
    const result = paginateLocal(
      items,
      { page: 1, pageSize: 10 },
      (item) => item.id % 2 === 0,
    )

    expect(result.total).toBe(12)
    expect(result.items).toHaveLength(10)
    expect(result.items.every((item) => item.id % 2 === 0)).toBe(true)
  })

  it('空列表兼容', () => {
    const result = paginateLocal([], { page: 1, pageSize: 10 })

    expect(result).toEqual({ items: [], total: 0, page: 1, pageSize: 10 })
  })
})
