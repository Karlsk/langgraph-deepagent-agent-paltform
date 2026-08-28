/**
 * 聊天流状态机（G4 spec-g4-chat §9.1/§9.3）。
 *
 * 职责：把 `utils/sse.ts` 产出的 SSE 帧流分发为 UI 可渲染的 ChatViewItem
 * 列表，并维护 streaming / pendingInterrupt / errorMessage 三态：
 * - message 帧按 source 归并（coordinator 归一为 null），subagent 切换开新块
 * - interrupt 帧重建审批卡片，submitDecisions 以 decisions JSON 消息通道
 *   resume（§5.2），本地先投影为胶囊
 * - loadHistory 拉取 L2 行投影并恢复 pending（§5.3）
 *
 * 生命周期：不注册 onBeforeUnmount（无组件实例的调用方会告警），由
 * ChatSessionView 在 onUnmounted 中显式调用 stop()。
 */
import { ref, type Ref } from 'vue'

import { fetchMessages } from '@/api/chat'
import { sseFetch } from '@/utils/sse'
import type { HistoryItem, InterruptPayload, StreamEvent } from '@/types'

/** 消息列表视图模型：流式 assistant 块带归一 source，历史投影不带 */
export type ChatViewItem =
  | { kind: 'message'; role: 'user'; content: string }
  | { kind: 'message'; role: 'assistant'; content: string; source?: string | null }
  | { kind: 'tool_call'; name: string; content: string }
  | { kind: 'summary'; content: string }
  | { kind: 'decision'; approved: number; rejected: number }

type DecisionCapsule = Extract<ChatViewItem, { kind: 'decision' }>

/** decisions JSON 消息通道前缀（与后端 _build_resume_value 生成格式对齐） */
const DECISIONS_PREFIX = '{"decisions"'

/** 网络中断提示语（断线不自动重连，恢复靠用户重发触发 resume，§9.2） */
const DISCONNECTED_MESSAGE = '连接中断，可重新发送消息恢复'

/** 识别 decisions JSON 胶囊；非胶囊或解析失败返回 null（降级普通气泡） */
function parseDecisionCapsule(content: string): DecisionCapsule | null {
  if (!content.trimStart().startsWith(DECISIONS_PREFIX)) return null
  try {
    const parsed = JSON.parse(content) as { decisions?: Array<{ type?: string }> }
    if (!Array.isArray(parsed.decisions)) return null
    const approved = parsed.decisions.filter((d) => d.type === 'approve').length
    const rejected = parsed.decisions.filter((d) => d.type === 'reject').length
    if (approved + rejected === 0) return null
    return { kind: 'decision', approved, rejected }
  } catch {
    return null
  }
}

/** L2 历史行 → 视图模型（tool_call 行 content 取 summary 字段，§6.1） */
function projectHistoryRow(row: HistoryItem): ChatViewItem {
  if (row.type === 'tool_call') {
    return { kind: 'tool_call', name: row.name ?? '', content: row.summary ?? '' }
  }
  if (row.type === 'summary') {
    return { kind: 'summary', content: row.content ?? '' }
  }
  const content = row.content ?? ''
  if (row.role === 'user') {
    const capsule = parseDecisionCapsule(content)
    if (capsule) return capsule
  }
  if (row.role === 'assistant') {
    return { kind: 'message', role: 'assistant', content }
  }
  return { kind: 'message', role: 'user', content }
}

export function useChatStream(sessionId: Ref<string>) {
  const items = ref<ChatViewItem[]>([])
  const streaming = ref(false)
  const pendingInterrupt = ref<InterruptPayload | null>(null)
  const errorMessage = ref<string | null>(null)

  let controller: AbortController | null = null

  /** message 帧归并：同 source 末块拼接；coordinator 归一为 null（§9.3） */
  function appendAssistantChunk(content: string, rawSource: string | undefined | null): void {
    const source = !rawSource || rawSource === 'coordinator' ? null : rawSource
    const last = items.value.at(-1)
    if (last?.kind === 'message' && last.role === 'assistant' && last.source === source) {
      last.content += content
      return
    }
    items.value.push({ kind: 'message', role: 'assistant', content, source })
  }

  function handleFrame(event: StreamEvent): void {
    switch (event.type) {
      case 'message':
        appendAssistantChunk(event.content ?? '', event.source)
        break
      case 'tool_call':
        items.value.push({
          kind: 'tool_call',
          name: event.name ?? '',
          content: event.content ?? '',
        })
        break
      case 'summary':
        items.value.push({ kind: 'summary', content: event.summary_text ?? '' })
        break
      case 'interrupt':
        pendingInterrupt.value = { action_requests: event.action_requests ?? [] }
        break
      case 'error':
        errorMessage.value = event.message ?? '未知错误'
        break
      case 'done':
        // 本轮结束；streaming 由 runStream 的 finally 收尾
        break
    }
  }

  /**
   * 发起一轮流式回合：先落出方向视图（用户气泡 / 审批胶囊），再同步
   * 建立 SSE 连接（signal 与 mock call 在首个 await 前就绪）；payload
   * 即出站 user 消息内容（普通文本 / decisions JSON，§5.2）。
   */
  async function runStream(outgoing: ChatViewItem, payload: string): Promise<void> {
    items.value.push(outgoing)
    pendingInterrupt.value = null
    errorMessage.value = null
    streaming.value = true
    controller = new AbortController()
    try {
      await sseFetch({
        url: '/api/v1/chat/stream',
        headers: { 'X-Session-Id': sessionId.value },
        body: { messages: [{ role: 'user', content: payload }] },
        signal: controller.signal,
        onEvent: (data) => {
          try {
            handleFrame(JSON.parse(data) as StreamEvent)
          } catch {
            // 非 JSON 帧忽略（心跳注释帧已在 sse.ts 过滤，此处兜底）
          }
        },
        onError: () => {
          errorMessage.value = DISCONNECTED_MESSAGE
        },
      })
    } catch {
      // onError 已写入提示；吞掉避免未处理 rejection
    } finally {
      streaming.value = false
    }
  }

  /** 发送用户消息（普通气泡） */
  function send(content: string): Promise<void> {
    return runStream({ kind: 'message', role: 'user', content }, content)
  }

  /** 提交审批决定：decisions JSON 消息通道 resume（§5.2） */
  function submitDecisions(approvals: boolean[]): Promise<void> {
    const approved = approvals.filter(Boolean).length
    const payload = JSON.stringify({
      decisions: approvals.map((ok) => ({ type: ok ? 'approve' : 'reject' })),
    })
    return runStream(
      { kind: 'decision', approved, rejected: approvals.length - approved },
      payload,
    )
  }

  /** 用户点停止 / 组件卸载：abort 当前流（sseFetch 静默收尾） */
  function stop(): void {
    controller?.abort()
  }

  /** 拉取 L2 历史并恢复 pending 审批卡片（§5.3） */
  async function loadHistory(): Promise<void> {
    const data = await fetchMessages(sessionId.value)
    items.value = data.messages.map(projectHistoryRow)
    pendingInterrupt.value = data.pending_interrupt ?? null
  }

  return {
    items,
    streaming,
    pendingInterrupt,
    errorMessage,
    send,
    submitDecisions,
    stop,
    loadHistory,
  }
}
