/**
 * 重定向目标 composable（task-024）：
 * 抽取 login ↔ register 双向跳转时的 redirect query 处理，
 * 避免两个组件内重复 `(route.query.redirect as string | undefined) ?? '/llm'` 模式。
 *
 * 设计动机：
 * - 单一来源：DEFAULT_REDIRECT 与 fallback 行为集中维护；
 * - 类型收敛：`redirect` 始终为 string（vue-router 的 `LocationQueryValue` 联合太宽）；
 * - 给 router-link 的 query 与 router.replace 的 redirect 字段共用同一份语义。
 */
import { computed } from 'vue'
import type { ComputedRef } from 'vue'
import { useRoute } from 'vue-router'

/** 未携带 redirect query 时的兜底目标：登录成功后的默认入口 */
export const DEFAULT_REDIRECT = '/llm'

export interface UseRedirectTargetResult {
  /** 解析后的 redirect 目标路径：兜底 DEFAULT_REDIRECT */
  redirect: ComputedRef<string>
  /** 透传给 router-link / router.replace 的 query 对象；始终含 redirect 字段 */
  redirectQuery: ComputedRef<Record<string, string>>
}

/**
 * 提取当前路由的 redirect query：
 * - redirect: 始终为 string（兜底 DEFAULT_REDIRECT）
 * - redirectQuery: 用于 router-link 的 query prop，保证 redirect 字段存在
 *   （fallback DEFAULT_REDIRECT），满足"跳到下一站时仍能继续透传"
 */
export function useRedirectTarget(): UseRedirectTargetResult {
  const route = useRoute()

  const rawRedirect = computed(() => route.query.redirect)

  const redirect = computed<string>(() => {
    const raw = rawRedirect.value
    return typeof raw === 'string' && raw.length > 0 ? raw : DEFAULT_REDIRECT
  })

  const redirectQuery = computed<Record<string, string>>(() => ({
    redirect: redirect.value,
  }))

  return { redirect, redirectQuery }
}
