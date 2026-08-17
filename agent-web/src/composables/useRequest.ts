import { onMounted, ref } from 'vue'
import type { Ref } from 'vue'

export interface UseRequestOptions<A extends unknown[]> {
  /** 挂载时自动执行一次，默认 false */
  immediate?: boolean
  /** immediate 自动执行时的入参 */
  defaultParams?: A
}

export interface UseRequestReturn<T, A extends unknown[]> {
  data: Ref<T | null>
  loading: Ref<boolean>
  error: Ref<unknown>
  execute: (...args: A) => Promise<T | null>
}

/**
 * 请求状态管理 composable：自动管理 data / loading / error 三态，
 * 避免每个页面手写 try-catch-finally 样板代码。
 *
 * 错误提示由统一请求层（request.ts）的全局拦截器承担，此处只做状态收敛：
 * execute 不向外抛异常，失败时保留旧 data（避免 UI 闪烁）并返回 null。
 */
export function useRequest<T, A extends unknown[] = []>(
  api: (...args: A) => Promise<T>,
  options?: UseRequestOptions<A>,
): UseRequestReturn<T, A> {
  const data = ref<T | null>(null) as Ref<T | null>
  const loading = ref(false)
  const error = ref<unknown>(null)

  async function execute(...args: A): Promise<T | null> {
    loading.value = true
    error.value = null
    try {
      const result = await api(...args)
      data.value = result
      return result
    } catch (err: unknown) {
      error.value = err
      return null
    } finally {
      loading.value = false
    }
  }

  if (options?.immediate) {
    onMounted(() => {
      void execute(...((options.defaultParams ?? []) as A))
    })
  }

  return { data, loading, error, execute }
}
