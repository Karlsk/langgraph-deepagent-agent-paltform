/**
 * fetch-based SSE 客户端（spec-g4-chat §9.2）。
 *
 * 排除原生 EventSource（无法携带 Authorization / X-Session-Id 自定义
 * header）与第三方库（自研 ~100 行，收益不足）。分帧按 SSE 规范：
 * 缓冲区以 `\n\n` 切帧、`data:` 行拼接载荷、冒号开头注释行（15s 心跳
 * `: ping`）跳过、跨 chunk 半帧靠缓冲区拼接。
 *
 * 断线语义：网络中断（未收到 done 帧）→ `onError` 回调后 rethrow，
 * 前端提示「连接中断，可重新发送消息恢复」；不自动重连——G4 恢复
 * 语义靠用户重发消息触发 resume，非浏览器自动重连流。
 */
import { refreshUserToken } from '@/composables/useAuth'
import { clearAuth, getUserToken } from '@/utils/authStorage'

export interface SseOptions {
  url: string
  headers?: Record<string, string>
  /** POST 请求体（JSON 序列化；POST /chat/stream 的 messages 载荷） */
  body?: unknown
  /** 中断控制（切会话 / 用户点停止 / 组件卸载）。abort 静默结束。 */
  signal?: AbortSignal
  onEvent: (data: string) => void
  onError?: (error: unknown) => void
}

function isAbortError(error: unknown): boolean {
  return error instanceof DOMException && error.name === 'AbortError'
}

/** 组装请求头：Accept + 调用方 headers + 用户 token（未登录不发）。 */
function buildHeaders(
  extra: Record<string, string> | undefined,
  hasBody: boolean,
): Record<string, string> {
  const headers: Record<string, string> = { Accept: 'text/event-stream' }
  if (hasBody) headers['Content-Type'] = 'application/json'
  Object.assign(headers, extra)
  const token = getUserToken()
  if (token) headers.Authorization = `Bearer ${token}`
  return headers
}

/**
 * 解析缓冲区：以 `\n\n` 切出所有完整帧，返回载荷列表与剩余半帧。
 * 注释行（`: ping` 心跳）与无 `data:` 行的帧被自然丢弃。
 */
export function extractFrames(buffer: string): { events: string[]; rest: string } {
  const events: string[] = []
  let rest = buffer
  for (;;) {
    const index = rest.indexOf('\n\n')
    if (index === -1) break
    const raw = rest.slice(0, index)
    rest = rest.slice(index + 2)
    const data = raw
      .split('\n')
      .filter((line) => line.startsWith('data:'))
      .map((line) => line.slice(5).replace(/^ /, ''))
      .join('\n')
    if (data) events.push(data)
  }
  return { events, rest }
}

async function redirectToLogin(): Promise<void> {
  const { default: router } = await import('@/router')
  if (router.currentRoute.value.name === 'login') return
  const redirect = router.currentRoute.value.fullPath
  await router.replace({ name: 'login', query: { redirect, reason: 'expired' } })
}

/** 建立连接；连接建立期 401 → refresh 成功后重试一次，再失败走清空 + 跳 login。 */
async function connect(options: SseOptions): Promise<Response> {
  const hasBody = options.body !== undefined
  const body = hasBody ? JSON.stringify(options.body) : undefined
  let response = await fetch(options.url, {
    method: 'POST',
    headers: buildHeaders(options.headers, hasBody),
    body,
    signal: options.signal,
  })

  if (response.status === 401) {
    const newToken = await refreshUserToken()
    if (newToken) {
      const headers = buildHeaders(options.headers, hasBody)
      headers.Authorization = `Bearer ${newToken}`
      response = await fetch(options.url, {
        method: 'POST',
        headers,
        body,
        signal: options.signal,
      })
    }
  }

  if (!response.ok || !response.body) {
    if (response.status === 401) {
      clearAuth()
      void redirectToLogin()
    }
    throw new Error(`SSE connect failed: ${response.status}`)
  }
  return response
}

/**
 * 发起 SSE 流并逐帧回调，流结束（服务端关闭 / abort）后 resolve。
 *
 * - abort：静默 resolve（用户主动操作，非错误）
 * - 其它错误：`onError` 回调后 rethrow，由调用方提示
 */
export async function sseFetch(options: SseOptions): Promise<void> {
  try {
    const response = await connect(options)
    const reader = response.body!.getReader()
    const decoder = new TextDecoder()
    let buffer = ''
    for (;;) {
      const { done, value } = await reader.read()
      if (done) break
      buffer += decoder.decode(value, { stream: true })
      const { events, rest } = extractFrames(buffer)
      buffer = rest
      for (const event of events) options.onEvent(event)
    }
  } catch (error) {
    if (options.signal?.aborted || isAbortError(error)) return
    options.onError?.(error)
    throw error
  }
}
