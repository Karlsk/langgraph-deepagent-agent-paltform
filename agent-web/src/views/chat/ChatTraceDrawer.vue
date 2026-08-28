<script setup lang="ts">
/**
 * 运行轨迹抽屉（G4 spec-g4-chat §9.4）：
 * 聊天页顶栏「运行轨迹」入口 → el-drawer 右滑出；
 * `GET /chat/traces`（created_at 倒序由后端保证）渲染行摘要（状态 /
 * 轮次 / 耗时 / 时间），点击行展开完整事件流——每事件类型徽标 + agent
 * 字段标签（B6 补齐，区分 coordinator / subagent 名）+ 事件 JSON。
 *
 * 渲染复用模式非复用组件：事件展开列表参照 SubAgentTraceDetailDialog
 * 的折叠实现（expanded 集合 + toggle），不直接引组件（Dialog 形态不符）。
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
            >
              <div class="chat-trace-drawer__event-head">
                <span class="chat-trace-drawer__badge">{{ eventBadge(event) }}</span>
                <span class="chat-trace-drawer__agent">{{ eventAgent(event) }}</span>
                <span class="chat-trace-drawer__event-title">{{ eventTitle(event) }}</span>
              </div>
              <pre class="chat-trace-drawer__event-json">{{ jsonText(event) }}</pre>
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

.chat-trace-drawer__event-head {
  display: flex;
  align-items: baseline;
  gap: 8px;
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
