// @vitest-environment happy-dom
/**
 * useRequest 三态管理测试：immediate 依赖组件挂载生命周期，
 * 故以最小宿主组件承载（零真实网络，api 均为本地 stub）。
 */
import { describe, expect, it, vi } from 'vitest'
import { defineComponent, h } from 'vue'
import { flushPromises, mount } from '@vue/test-utils'

import { useRequest } from '@/composables/useRequest'
import type { UseRequestOptions, UseRequestReturn } from '@/composables/useRequest'

/** 以最小宿主组件挂载 composable，返回其响应式状态句柄 */
function withSetup<T, A extends unknown[]>(
  api: (...args: A) => Promise<T>,
  options?: UseRequestOptions<A>,
): UseRequestReturn<T, A> {
  let state!: UseRequestReturn<T, A>
  mount(
    defineComponent({
      setup() {
        state = useRequest(api, options)
        return () => h('div')
      },
    }),
  )
  return state
}

describe('useRequest 请求状态管理', () => {
  it('execute 成功：写入 data、返回结果、error 为 null', async () => {
    const api = vi.fn(async (id: number) => ({ id, name: 'demo' }))
    const state = withSetup(api)

    expect(state.loading.value).toBe(false)
    const result = await state.execute(1)

    expect(api).toHaveBeenCalledWith(1)
    expect(result).toEqual({ id: 1, name: 'demo' })
    expect(state.data.value).toEqual({ id: 1, name: 'demo' })
    expect(state.error.value).toBeNull()
    expect(state.loading.value).toBe(false)
  })

  it('execute 失败：写入 error、返回 null 且不向外抛异常、保留旧 data', async () => {
    const boom = new Error('backend exploded')
    const api = vi
      .fn()
      .mockResolvedValueOnce('first')
      .mockRejectedValueOnce(boom)
    const state = withSetup(api)

    await state.execute()
    const result = await state.execute()

    expect(result).toBeNull()
    expect(state.error.value).toBe(boom)
    // 失败时保留旧 data，避免 UI 闪烁
    expect(state.data.value).toBe('first')
    expect(state.loading.value).toBe(false)
  })

  it('loading 状态机：请求期间为 true，结束后回落 false', async () => {
    let resolveApi!: (value: string) => void
    const api = vi.fn(
      () =>
        new Promise<string>((resolve) => {
          resolveApi = resolve
        }),
    )
    const state = withSetup(api)

    const pending = state.execute()
    expect(state.loading.value).toBe(true)

    resolveApi('done')
    await pending
    expect(state.loading.value).toBe(false)
  })

  it('immediate + defaultParams：挂载后自动执行一次', async () => {
    const api = vi.fn(async (id: number) => ({ id }))
    const state = withSetup(api, { immediate: true, defaultParams: [7] })

    await flushPromises()

    expect(api).toHaveBeenCalledTimes(1)
    expect(api).toHaveBeenCalledWith(7)
    expect(state.data.value).toEqual({ id: 7 })
  })
})
