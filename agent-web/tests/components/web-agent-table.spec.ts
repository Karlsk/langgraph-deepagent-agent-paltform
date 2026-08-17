// @vitest-environment happy-dom
/**
 * WebAgentTable 组件测试：stub 掉 Element Plus 组件（不做真实渲染），
 * api 以 paginateLocal 包装本地裸列表，验证 PageResult 契约端到端（零真实网络）。
 */
import { describe, expect, it, vi } from 'vitest'
import { defineComponent, h } from 'vue'
import { flushPromises, mount } from '@vue/test-utils'
import type { Component } from 'vue'

import WebAgentTable from '@/components/WebAgentTable.vue'
import { paginateLocal } from '@/utils/paginate'
import type { PageQuery, PageResult } from '@/types'

interface Row {
  id: number
  name: string
}

const ElTableStub = defineComponent({
  name: 'ElTable',
  props: { data: { type: Array, default: () => [] as unknown[] } },
  setup(props, { slots }) {
    return () =>
      h('div', { class: 'el-table-stub' }, [
        props.data.length === 0 && slots.empty ? slots.empty() : undefined,
        slots.default ? slots.default() : undefined,
      ])
  },
})

const ElPaginationStub = defineComponent({
  name: 'ElPagination',
  props: {
    currentPage: Number,
    pageSize: Number,
    total: Number,
    layout: String,
  },
  emits: ['current-change', 'size-change'],
  setup(props) {
    return () =>
      h('div', { class: 'el-pagination-stub', 'data-total': String(props.total) })
  },
})

const rows25: Row[] = Array.from({ length: 25 }, (_, index) => ({
  id: index + 1,
  name: `name-${index + 1}`,
}))

function makeApi(items: Row[] = rows25) {
  return vi.fn(
    async (query: PageQuery): Promise<PageResult<Row>> => paginateLocal(items, query),
  )
}

function mountTable(extraProps: Record<string, unknown> = {}, api = makeApi()) {
  const wrapper = mount(WebAgentTable as Component, {
    props: {
      columns: [{ label: '名称', prop: 'name' }],
      api,
      ...extraProps,
    },
    global: {
      stubs: {
        ElTable: ElTableStub,
        ElTableColumn: true,
        ElPagination: ElPaginationStub,
        ElEmpty: true,
      },
      directives: { loading: () => undefined },
    },
  })
  return { wrapper, api }
}

describe('WebAgentTable 通用列表表格', () => {
  it('挂载自动请求第 1 页并渲染 rows 与 total', async () => {
    const { wrapper, api } = mountTable()
    await flushPromises()

    expect(api).toHaveBeenCalledWith({ page: 1, pageSize: 10 })
    expect(wrapper.findComponent(ElTableStub).props('data')).toEqual(
      rows25.slice(0, 10),
    )
    expect(wrapper.findComponent(ElPaginationStub).props('total')).toBe(25)
  })

  it('切换页码：带新 page 重新请求并渲染对应切片', async () => {
    const { wrapper, api } = mountTable()
    await flushPromises()

    wrapper.findComponent(ElPaginationStub).vm.$emit('current-change', 2)
    await flushPromises()

    expect(api).toHaveBeenLastCalledWith({ page: 2, pageSize: 10 })
    expect(wrapper.findComponent(ElTableStub).props('data')).toEqual(
      rows25.slice(10, 20),
    )
  })

  it('切换每页条数：pageSize 生效且重置到第 1 页', async () => {
    const { wrapper, api } = mountTable()
    await flushPromises()

    wrapper.findComponent(ElPaginationStub).vm.$emit('size-change', 5)
    await flushPromises()

    expect(api).toHaveBeenLastCalledWith({ page: 1, pageSize: 5 })
    expect(wrapper.findComponent(ElTableStub).props('data')).toHaveLength(5)
  })

  it('空数据渲染 el-empty', async () => {
    const { wrapper } = mountTable({}, makeApi([]))
    await flushPromises()

    expect(wrapper.findComponent({ name: 'ElEmpty' }).exists()).toBe(true)
  })

  it('refresh() 保留当前页重新请求', async () => {
    const { wrapper, api } = mountTable()
    await flushPromises()
    wrapper.findComponent(ElPaginationStub).vm.$emit('current-change', 2)
    await flushPromises()
    api.mockClear()

    ;(wrapper.vm as { refresh: () => void }).refresh()
    await flushPromises()

    expect(api).toHaveBeenCalledWith({ page: 2, pageSize: 10 })
  })

  it('query 变化：重置到第 1 页并携带新过滤条件重新请求', async () => {
    const { wrapper, api } = mountTable({ query: { keyword: 'a' } })
    await flushPromises()
    wrapper.findComponent(ElPaginationStub).vm.$emit('current-change', 2)
    await flushPromises()

    await wrapper.setProps({ query: { keyword: 'b' } })
    await flushPromises()

    expect(api).toHaveBeenLastCalledWith({ page: 1, pageSize: 10, keyword: 'b' })
  })

  it('api 失败：收敛为空数据与 total 0（错误提示由全局拦截器承担）', async () => {
    const failingApi = vi.fn(async (): Promise<PageResult<Row>> => {
      throw new Error('server error')
    })
    const { wrapper } = mountTable({}, failingApi)
    await flushPromises()

    expect(wrapper.findComponent(ElTableStub).props('data')).toEqual([])
    expect(wrapper.findComponent(ElPaginationStub).props('total')).toBe(0)
    expect(wrapper.findComponent({ name: 'ElEmpty' }).exists()).toBe(true)
  })

  it('pagination=false 时不渲染分页器', async () => {
    const { wrapper } = mountTable({ pagination: false })
    await flushPromises()

    expect(wrapper.findComponent(ElPaginationStub).exists()).toBe(false)
  })
})
