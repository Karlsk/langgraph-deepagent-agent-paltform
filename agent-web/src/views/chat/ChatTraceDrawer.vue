<script setup lang="ts">
/**
 * 运行轨迹抽屉（G4 spec-g4-chat §9.4）：
 * 聊天页顶栏「运行轨迹」入口 → el-drawer 右滑出；
 * `GET /chat/traces`（created_at 倒序由后端保证）渲染行摘要（状态 /
 * 轮次 / 耗时 / 时间），点击行展开事件流——逐事件折叠（默认全收起，
 * 头部仅显摘要：类型徽标 + agent 标签 + 标题 + 耗时 + 状态），
 * 点击展开查看分类型关键字段。
 *
 * 渲染复用模式非复用组件：事件折叠实现参照 SubAgentTraceDetailDialog
 * （expanded 集合 + toggle），不直接引组件（Dialog 形态不符）。
 */
import { ref, watch } from 'vue'

import { fetchChatTraces } from '@/api/chat'
import type { ChatTraceItem } from '@/types'

const props = defineProps<{
  modelValue: boolean
  sessionId: string
}>()

const emit = defineEmits<{
  'update:modelValue': [value: boolean]
}>()

const traces = ref<ChatTraceItem[]>([])
const loading = ref(false)
/** 展开的 trace id 集合（默认全折叠） */
const expandedIds = ref<number[]>([])

async function loadTraces(): Promise<void> {
  loading.value = true
  try {
    traces.value = await fetchChatTraces(props.sessionId)
  } catch {
    // 错误提示由统一请求层全局拦截器承担
    traces.value = []
  } finally {
    loading.value = false
  }
}

watch(
  () => props.modelValue,
  (visible) => {
    if (visible) void loadTraces()
  },
  { immediate: true },
)

function toggleTrace(id: number): void {
  const at = expandedIds.value.indexOf(id)
  if (at >= 0) {
    expandedIds.value.splice(at, 1)
  } else {
    expandedIds.value.push(id)
  }
}

function isExpanded(id: number): boolean {
  return expandedIds.value.includes(id)
}

/** 展开的事件键集合（`${traceId}:${seq}`，默认全折叠） */
const expandedEvents = ref<string[]>([])

function eventKey(traceId: number, event: Record<string, unknown>): string {
  return `${traceId}:${String(event.seq)}`
}

function toggleEvent(traceId: number, event: Record<string, unknown>): void {
  const key = eventKey(traceId, event)
  const at = expandedEvents.value.indexOf(key)
  if (at >= 0) {
    expandedEvents.value.splice(at, 1)
  } else {
    expandedEvents.value.push(key)
  }
}

function isEventExpanded(traceId: number, event: Record<string, unknown>): boolean {
  return expandedEvents.value.includes(eventKey(traceId, event))
}

// ---------------------------------------------------------------------------
// 事件流渲染辅助（events 为后端原始 JSON，防御性提取）
// ---------------------------------------------------------------------------

const TYPE_BADGES: Record<string, string> = {
  llm_call: 'LLM',
  tool_call: '工具',
  run_finished: '结束',
}

function eventBadge(event: Record<string, unknown>): string {
  return TYPE_BADGES[String(event.type)] ?? String(event.type)
}

function eventAgent(event: Record<string, unknown>): string {
  return String(event.agent ?? 'coordinator')
}

function eventTitle(event: Record<string, unknown>): string {
  if (event.type === 'llm_call') return String(event.model ?? '')
  if (event.type === 'tool_call') return String(event.tool ?? '')
  return String(event.status ?? '')
}

function jsonText(value: unknown): string {
  if (value === null || value === undefined) return ''
  if (typeof value === 'string') return value
  return JSON.stringify(value, null, 2)
}

/** 消息 content 可能是字符串或内容块数组，统一转为可展示文本 */
function messageText(content: unknown): string {
  if (typeof content === 'string') return content
  if (content === null || content === undefined) return ''
  return JSON.stringify(content, null, 2)
}

function asMessages(event: Record<string, unknown>): Array<{ type?: string; content?: unknown }> {
  const raw = event.input_messages
  return Array.isArray(raw) ? (raw as Array<{ type?: string; content?: unknown }>) : []
}

function asToolCalls(event: Record<string, unknown>): Array<{ name?: string; args?: unknown }> {
  const raw = event.tool_calls
  return Array.isArray(raw) ? (raw as Array<{ name?: string; args?: unknown }>) : []
}

/** token 用量（缺失记 0，与后端采集语义一致） */
function tokenUsage(event: Record<string, unknown>): { input: number; output: number } {
  const raw = event.token_usage
  if (typeof raw !== 'object' || raw === null) return { input: 0, output: 0 }
  const usage = raw as { input_tokens?: number; output_tokens?: number }
  return { input: usage.input_tokens ?? 0, output: usage.output_tokens ?? 0 }
}

function durationLabel(value: unknown): string {
  if (typeof value !== 'number') return ''
  if (value < 1) return `${Math.round(value * 1000)}ms`
  return `${value.toFixed(2)}s`
}

/** created_at 展示：截到秒（后端 ISO 8601 带时区） */
function createdLabel(trace: ChatTraceItem): string {
  return trace.created_at.slice(0, 19).replace('T', ' ')
}

function close(): void {
  emit('update:modelValue', false)
}
</script>

<template>
  <el-drawer
    :model-value="modelValue"
    title="运行轨迹"
    size="480px"
    @update:model-value="close"
  >
    <div class="chat-trace-drawer">
      <p v-if="loading" class="chat-trace-drawer__hint">加载中…</p>
      <p v-else-if="traces.length === 0" class="chat-trace-drawer__hint">
        暂无运行轨迹（CHAT_TRACE_ENABLED 开启后的会话轮次会记录在此）。
      </p>
      <div v-for="trace in traces" :key="trace.id" class="chat-trace-drawer__item">
        <button
          type="button"
          class="chat-trace-drawer__item-header"
          @click="toggleTrace(trace.id)"
        >
          <el-tag :type="trace.status === 'success' ? 'success' : 'danger'" size="small">
            {{ trace.status === 'success' ? '成功' : '失败' }}
          </el-tag>
          <span class="chat-trace-drawer__metric">轮次 {{ trace.turns }}</span>
          <span class="chat-trace-drawer__metric">{{ trace.duration_seconds.toFixed(1) }}s</span>
          <span class="chat-trace-drawer__time">{{ createdLabel(trace) }}</span>
          <span class="chat-trace-drawer__toggle">{{ isExpanded(trace.id) ? '收起' : '展开' }}</span>
        </button>
        <div v-if="isExpanded(trace.id)" class="chat-trace-drawer__events">
          <ul class="chat-trace-drawer__event-list">
            <li
              v-for="event in trace.events"
              :key="String(event.seq)"
              class="chat-trace-drawer__event"
              :class="{ 'chat-trace-drawer__event--error': event.status === 'error' }"
            >
              <button
                type="button"
                class="chat-trace-drawer__event-head"
                @click="toggleEvent(trace.id, event)"
              >
                <span class="chat-trace-drawer__event-seq">#{{ event.seq }}</span>
                <span class="chat-trace-drawer__badge">{{ eventBadge(event) }}</span>
                <span class="chat-trace-drawer__agent">{{ eventAgent(event) }}</span>
                <span class="chat-trace-drawer__event-title">{{ eventTitle(event) }}</span>
                <span v-if="durationLabel(event.duration_seconds) !== ''" class="chat-trace-drawer__metric">
                  {{ durationLabel(event.duration_seconds) }}
                </span>
                <span
                  class="chat-trace-drawer__event-status"
                  :class="event.status === 'error' ? 'chat-trace-drawer__event-status--error' : ''"
                >
                  {{ event.status === 'error' ? '失败' : '成功' }}
                </span>
                <span class="chat-trace-drawer__toggle">
                  {{ isEventExpanded(trace.id, event) ? '收起' : '展开' }}
                </span>
              </button>
              <div v-if="isEventExpanded(trace.id, event)" class="chat-trace-drawer__event-body">
                <template v-if="event.type === 'llm_call'">
                  <div class="chat-trace-drawer__field-label">
                    token：{{ tokenUsage(event).input }} in / {{ tokenUsage(event).output }} out
                  </div>
                  <div class="chat-trace-drawer__field-label">输入消息（{{ asMessages(event).length }}）</div>
                  <div
                    v-for="(message, index) in asMessages(event)"
                    :key="index"
                    class="chat-trace-drawer__message"
                  >
                    <span class="chat-trace-drawer__message-type">{{ message.type ?? 'unknown' }}</span>
                    <pre class="chat-trace-drawer__event-json">{{ messageText(message.content) }}</pre>
                  </div>
                  <div class="chat-trace-drawer__field-label">输出</div>
                  <pre class="chat-trace-drawer__event-json">{{ messageText(event.output_text) }}</pre>
                  <template v-if="asToolCalls(event).length > 0">
                    <div class="chat-trace-drawer__field-label">发起的工具调用</div>
                    <pre class="chat-trace-drawer__event-json">{{ jsonText(asToolCalls(event)) }}</pre>
                  </template>
                </template>
                <template v-else-if="event.type === 'tool_call'">
                  <div class="chat-trace-drawer__field-label">参数</div>
                  <pre class="chat-trace-drawer__event-json">{{ jsonText(event.arguments) }}</pre>
                  <div class="chat-trace-drawer__field-label">返回值</div>
                  <pre class="chat-trace-drawer__event-json">{{ jsonText(event.output) }}</pre>
                </template>
                <template v-else>
                  <div class="chat-trace-drawer__field-label">
                    status：{{ event.status }} · 轮次：{{ event.turns }}
                  </div>
                </template>
                <pre v-if="event.error" class="chat-trace-drawer__event-error">{{ event.error }}</pre>
              </div>
            </li>
          </ul>
          <p v-if="trace.error" class="chat-trace-drawer__error">{{ trace.error }}</p>
        </div>
      </div>
      <el-button class="chat-trace-drawer__close" @click="close">关闭</el-button>
    </div>
  </el-drawer>
</template>

<style scoped>
.chat-trace-drawer {
  display: flex;
  flex-direction: column;
  gap: 10px;
}

.chat-trace-drawer__hint {
  margin: 0;
  color: var(--color-text-secondary);
  font-size: 13px;
}

.chat-trace-drawer__item {
  border: 1px solid var(--color-border-default);
  border-radius: var(--radius-sm, 6px);
  background: var(--color-bg-surface);
}

.chat-trace-drawer__item-header {
  display: flex;
  align-items: baseline;
  gap: 10px;
  width: 100%;
  padding: 8px 10px;
  border: none;
  background: transparent;
  cursor: pointer;
  text-align: left;
  font: inherit;
  color: inherit;
}

.chat-trace-drawer__metric {
  font-size: 13px;
  white-space: nowrap;
}

.chat-trace-drawer__time {
  flex: 1;
  color: var(--color-text-secondary);
  font-size: 12px;
  font-family: var(--app-font-display, monospace);
}

.chat-trace-drawer__toggle {
  font-size: 12px;
  color: var(--color-text-secondary);
  white-space: nowrap;
}

.chat-trace-drawer__events {
  border-top: 1px solid var(--color-border-default);
  padding: 8px 10px;
}

.chat-trace-drawer__event-list {
  margin: 0;
  padding: 0;
  list-style: none;
  display: flex;
  flex-direction: column;
  gap: 8px;
}

.chat-trace-drawer__event {
  border: 1px solid var(--color-border-default);
  border-radius: var(--radius-sm, 6px);
  padding: 6px 8px;
}

.chat-trace-drawer__event--error {
  border-color: var(--color-danger, #ef4444);
}

.chat-trace-drawer__event-head {
  display: flex;
  align-items: baseline;
  gap: 8px;
  width: 100%;
  padding: 0;
  border: none;
  background: transparent;
  cursor: pointer;
  text-align: left;
  font: inherit;
  color: inherit;
}

.chat-trace-drawer__event-seq {
  font-size: 12px;
  color: var(--color-text-tertiary);
  font-family: var(--app-font-mono, monospace);
  white-space: nowrap;
}

.chat-trace-drawer__event-status {
  font-size: 12px;
  color: var(--color-success, #22c55e);
  white-space: nowrap;
}

.chat-trace-drawer__event-status--error {
  color: var(--color-danger, #ef4444);
}

.chat-trace-drawer__event-body {
  margin-top: 6px;
  border-top: 1px dashed var(--color-border-default);
  padding-top: 6px;
  display: flex;
  flex-direction: column;
  gap: 4px;
}

.chat-trace-drawer__field-label {
  font-size: 12px;
  font-weight: 600;
  color: var(--color-text-secondary);
  letter-spacing: 0.04em;
}

.chat-trace-drawer__message {
  display: flex;
  flex-direction: column;
  gap: 4px;
}

.chat-trace-drawer__message-type {
  font-size: 12px;
  font-weight: 600;
  color: var(--color-text-tertiary);
}

.chat-trace-drawer__event-error {
  margin: 6px 0 0;
  padding: 6px 8px;
  background: var(--color-danger-soft, #fef2f2);
  color: var(--color-danger, #ef4444);
  border-radius: var(--radius-sm, 6px);
  font-size: 12px;
  white-space: pre-wrap;
  word-break: break-word;
}

.chat-trace-drawer__badge {
  font-size: 12px;
  padding: 1px 8px;
  border-radius: 999px;
  background: var(--color-bg-secondary, #f2f4f7);
  color: var(--color-text-secondary);
}

.chat-trace-drawer__agent {
  font-size: 12px;
  padding: 1px 8px;
  border-radius: 999px;
  border: 1px dashed var(--color-border-default);
  color: var(--color-text-primary);
}

.chat-trace-drawer__event-title {
  font-size: 13px;
  font-weight: 600;
  word-break: break-all;
}

.chat-trace-drawer__event-json {
  margin: 6px 0 0;
  padding: 6px 8px;
  max-height: 220px;
  overflow: auto;
  background: var(--color-bg-secondary, #f2f4f7);
  border-radius: var(--radius-sm, 6px);
  font-size: 12px;
  white-space: pre-wrap;
  word-break: break-word;
}

.chat-trace-drawer__error {
  margin: 8px 0 0;
  padding: 6px 10px;
  border-radius: var(--radius-sm, 6px);
  background: var(--color-danger-soft, #fef2f2);
  color: var(--color-danger, #ef4444);
  font-size: 12px;
  word-break: break-word;
}

.chat-trace-drawer__close {
  align-self: flex-end;
}
</style>
