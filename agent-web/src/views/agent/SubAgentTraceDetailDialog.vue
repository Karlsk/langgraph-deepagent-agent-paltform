<script setup lang="ts">
/**
 * SubAgentTraceDetailDialog 子代理测试追踪详情弹窗：
 * - 由 SubAgentList 统一持有（历史弹窗「详情」或测试弹窗「查看执行详情」触发）；
 * - 数据来源：GET /subagents/{name}/test-traces/{traceId}（摘要 + 完整事件流）；
 * - 布局：左栏事件流（按 seq 顺序，类型徽标 + 折叠展开），右栏运行概览
 *   （状态 / 轮次 / 耗时 / 模型 / 总 token / prompt / final_message / error）；
 * - 事件字段契约见后端 `app/services/agents/run_tracer.py`；
 * - 长文本后端已做 20000 字符截断，前端仅加滚动容器。
 */
import { computed, ref, watch } from 'vue'

import {
  getSubAgentTestTrace,
  type TraceEvent,
} from '@/api/subagents'
import { useRequest } from '@/composables/useRequest'

/* -------------------------------------------------------------------------- */
/*  Props / Emits                                                              */
/* -------------------------------------------------------------------------- */

const props = defineProps<{
  modelValue: boolean
  agentName: string
  traceId: number | null
}>()

const emit = defineEmits<{
  'update:modelValue': [value: boolean]
}>()

/* -------------------------------------------------------------------------- */
/*  数据加载                                                                   */
/* -------------------------------------------------------------------------- */

const { data: detail, loading, execute } = useRequest(getSubAgentTestTrace)

async function loadDetail(): Promise<void> {
  if (props.traceId === null) {
    return
  }
  await execute(props.agentName, props.traceId)
}

/** 弹窗打开且 traceId 有效时加载；切换 traceId 也重新加载 */
watch(
  () => [props.modelValue, props.traceId],
  ([visible]) => {
    if (visible) {
      void loadDetail()
    }
  },
  { immediate: true },
)

/* -------------------------------------------------------------------------- */
/*  事件流渲染辅助                                                             */
/* -------------------------------------------------------------------------- */

const TYPE_BADGES: Record<TraceEvent['type'], string> = {
  llm_call: 'LLM',
  tool_call: '工具',
  run_finished: '结束',
}

/** 按 seq 升序排列的事件流（后端产出即有序，排序做防御） */
const sortedEvents = computed<TraceEvent[]>(() =>
  [...(detail.value?.events ?? [])].sort((a, b) => a.seq - b.seq),
)

/** 当前展开的事件 seq 集合；默认全部折叠 */
const expandedSeqs = ref<number[]>([])

function toggleEvent(seq: number): void {
  const index = expandedSeqs.value.indexOf(seq)
  if (index >= 0) {
    expandedSeqs.value.splice(index, 1)
  } else {
    expandedSeqs.value.push(seq)
  }
}

function isExpanded(seq: number): boolean {
  return expandedSeqs.value.includes(seq)
}

/** 事件行头摘要：llm_call 显示模型、tool_call 显示工具名、run_finished 显示状态 */
function eventTitle(event: TraceEvent): string {
  if (event.type === 'llm_call') {
    return String(event.model ?? '')
  }
  if (event.type === 'tool_call') {
    return String(event.tool ?? '')
  }
  return String(event.status ?? '')
}

/** 消息 content 可能是字符串或内容块数组，统一转为可展示文本 */
function messageText(content: unknown): string {
  if (typeof content === 'string') {
    return content
  }
  if (content === null || content === undefined) {
    return ''
  }
  return JSON.stringify(content, null, 2)
}

/** 任意 JSON 值格式化展示（工具参数 / tool_calls 等） */
function jsonText(value: unknown): string {
  if (value === null || value === undefined) {
    return ''
  }
  if (typeof value === 'string') {
    return value
  }
  return JSON.stringify(value, null, 2)
}

function asMessages(event: TraceEvent): Array<{ type?: string; content?: unknown }> {
  const raw = event.input_messages
  return Array.isArray(raw) ? (raw as Array<{ type?: string; content?: unknown }>) : []
}

function asToolCalls(event: TraceEvent): Array<{ name?: string; args?: unknown }> {
  const raw = event.tool_calls
  return Array.isArray(raw) ? (raw as Array<{ name?: string; args?: unknown }>) : []
}

/** token 用量（缺失记 0，与后端采集语义一致） */
function tokenUsage(event: TraceEvent): { input: number; output: number; total: number } {
  const raw = event.token_usage
  if (typeof raw !== 'object' || raw === null) {
    return { input: 0, output: 0, total: 0 }
  }
  const usage = raw as { input_tokens?: number; output_tokens?: number; total_tokens?: number }
  return {
    input: usage.input_tokens ?? 0,
    output: usage.output_tokens ?? 0,
    total: usage.total_tokens ?? 0,
  }
}

/** 全部 llm_call 事件的 token 总量（右栏概览展示） */
const totalTokens = computed(() =>
  sortedEvents.value
    .filter((event) => event.type === 'llm_call')
    .reduce((acc, event) => acc + tokenUsage(event).total, 0),
)

/* -------------------------------------------------------------------------- */
/*  通用格式化                                                                 */
/* -------------------------------------------------------------------------- */

function durationLabel(seconds: number): string {
  if (seconds < 1) return `${Math.round(seconds * 1000)}ms`
  return `${seconds.toFixed(2)}s`
}

function closeDialog(): void {
  emit('update:modelValue', false)
}
</script>

<template>
  <el-dialog
    :model-value="modelValue"
    :title="`执行详情 — ${agentName} #${traceId ?? ''}`"
    width="960px"
    append-to-body
    :close-on-click-modal="false"
    @update:model-value="emit('update:modelValue', $event)"
  >
    <div v-loading="loading" class="subagent-trace-detail">
      <template v-if="detail">
        <div class="subagent-trace-detail__columns">
          <!-- 左栏：事件流 -->
          <section class="subagent-trace-detail__events">
            <h3 class="subagent-trace-detail__section-title">事件流（{{ sortedEvents.length }}）</h3>
            <ul v-if="sortedEvents.length > 0" class="subagent-trace-detail__event-list">
              <li
                v-for="event in sortedEvents"
                :key="event.seq"
                class="subagent-trace-detail__event"
                :class="{ 'subagent-trace-detail__event--error': event.status === 'error' }"
              >
                <button type="button" class="subagent-trace-detail__event-header" @click="toggleEvent(event.seq)">
                  <span class="subagent-trace-detail__event-seq">#{{ event.seq }}</span>
                  <span
                    class="subagent-trace-detail__event-badge"
                    :class="`subagent-trace-detail__event-badge--${event.type}`"
                  >
                    {{ TYPE_BADGES[event.type] }}
                  </span>
                  <span class="subagent-trace-detail__event-title">{{ eventTitle(event) }}</span>
                  <span v-if="typeof event.duration_seconds === 'number'" class="subagent-trace-detail__event-duration">
                    {{ durationLabel(event.duration_seconds) }}
                  </span>
                  <span
                    class="subagent-trace-detail__event-status"
                    :class="event.status === 'error' ? 'subagent-trace-detail__event-status--error' : ''"
                  >
                    {{ event.status === 'error' ? '失败' : '成功' }}
                  </span>
                </button>
                <div v-if="isExpanded(event.seq)" class="subagent-trace-detail__event-body">
                  <template v-if="event.type === 'llm_call'">
                    <div class="subagent-trace-detail__field-label">
                      token：{{ tokenUsage(event).input }} in / {{ tokenUsage(event).output }} out
                    </div>
                    <div class="subagent-trace-detail__field-label">输入消息（{{ asMessages(event).length }}）</div>
                    <div
                      v-for="(message, index) in asMessages(event)"
                      :key="index"
                      class="subagent-trace-detail__message"
                    >
                      <span class="subagent-trace-detail__message-type">{{ message.type ?? 'unknown' }}</span>
                      <pre class="subagent-trace-detail__pre">{{ messageText(message.content) }}</pre>
                    </div>
                    <div class="subagent-trace-detail__field-label">输出</div>
                    <pre class="subagent-trace-detail__pre">{{ messageText(event.output_text) }}</pre>
                    <template v-if="asToolCalls(event).length > 0">
                      <div class="subagent-trace-detail__field-label">发起的工具调用</div>
                      <pre class="subagent-trace-detail__pre">{{ jsonText(asToolCalls(event)) }}</pre>
                    </template>
                  </template>
                  <template v-else-if="event.type === 'tool_call'">
                    <div class="subagent-trace-detail__field-label">参数</div>
                    <pre class="subagent-trace-detail__pre">{{ jsonText(event.arguments) }}</pre>
                    <div class="subagent-trace-detail__field-label">返回值</div>
                    <pre class="subagent-trace-detail__pre">{{ jsonText(event.output) }}</pre>
                  </template>
                  <template v-else>
                    <div class="subagent-trace-detail__metrics">
                      <span>status：{{ event.status }}</span>
                      <span>轮次：{{ event.turns }}</span>
                    </div>
                  </template>
                  <pre v-if="event.error" class="subagent-trace-detail__error">{{ event.error }}</pre>
                </div>
              </li>
            </ul>
            <p v-else class="subagent-trace-detail__empty">该运行未采集到事件。</p>
          </section>

          <!-- 右栏：运行概览 -->
          <section class="subagent-trace-detail__overview">
            <h3 class="subagent-trace-detail__section-title">运行概览</h3>
            <div class="subagent-trace-detail__metrics">
              <span>
                状态：
                <el-tag :type="detail.status === 'success' ? 'success' : 'danger'" size="small">
                  {{ detail.status === 'success' ? '成功' : '失败' }}
                </el-tag>
              </span>
              <span>轮次：{{ detail.turns }}</span>
              <span>耗时：{{ durationLabel(detail.duration_seconds) }}</span>
              <span>模型：{{ detail.model }}</span>
              <span>总 token：{{ totalTokens }}</span>
            </div>
            <div class="subagent-trace-detail__field-label">prompt</div>
            <pre class="subagent-trace-detail__pre">{{ detail.prompt }}</pre>
            <div class="subagent-trace-detail__field-label">final_message</div>
            <pre class="subagent-trace-detail__pre subagent-trace-detail__pre--tall">{{ detail.final_message || '（无）' }}</pre>
            <template v-if="detail.error">
              <div class="subagent-trace-detail__field-label">error</div>
              <pre class="subagent-trace-detail__error">{{ detail.error }}</pre>
            </template>
          </section>
        </div>
      </template>
      <p v-else-if="!loading" class="subagent-trace-detail__empty">未加载到追踪数据。</p>
    </div>
    <template #footer>
      <el-button @click="closeDialog">关闭</el-button>
    </template>
  </el-dialog>
</template>

<style scoped>
.subagent-trace-detail {
  min-height: 240px;
}

.subagent-trace-detail__columns {
  display: flex;
  gap: 16px;
  align-items: flex-start;
}

.subagent-trace-detail__events {
  flex: 11;
  min-width: 0;
}

.subagent-trace-detail__overview {
  flex: 9;
  min-width: 0;
  display: flex;
  flex-direction: column;
  gap: 8px;
}

.subagent-trace-detail__section-title {
  margin: 0 0 8px;
  font-size: 13px;
  font-weight: 600;
  color: var(--color-text-secondary);
  letter-spacing: 0.04em;
}

.subagent-trace-detail__event-list {
  margin: 0;
  padding: 0;
  list-style: none;
  display: flex;
  flex-direction: column;
  gap: 8px;
  max-height: 480px;
  overflow: auto;
}

.subagent-trace-detail__event {
  border: 1px solid var(--color-border-default);
  border-radius: var(--radius-sm);
  background: var(--color-bg-surface);
}

.subagent-trace-detail__event--error {
  border-color: var(--color-danger-600);
}

.subagent-trace-detail__event-header {
  display: flex;
  align-items: center;
  gap: 8px;
  width: 100%;
  padding: 8px 12px;
  border: none;
  background: transparent;
  cursor: pointer;
  font: inherit;
  text-align: left;
}

.subagent-trace-detail__event-header:focus-visible {
  outline: 2px solid var(--color-primary-500);
  outline-offset: -2px;
}

.subagent-trace-detail__event-seq {
  font-family: var(--app-font-mono, monospace);
  font-size: 12px;
  color: var(--color-text-tertiary);
}

.subagent-trace-detail__event-badge {
  padding: 1px 8px;
  border-radius: var(--radius-sm);
  font-size: 12px;
  font-weight: 600;
  background: var(--color-bg-subtle);
  color: var(--color-text-secondary);
}

.subagent-trace-detail__event-badge--llm_call {
  background: var(--color-primary-50);
  color: var(--color-primary-600);
}

.subagent-trace-detail__event-badge--tool_call {
  background: var(--color-bg-subtle);
  color: var(--color-success-600);
}

.subagent-trace-detail__event-badge--run_finished {
  background: var(--color-bg-subtle);
  color: var(--color-text-secondary);
}

.subagent-trace-detail__event-title {
  flex: 1;
  min-width: 0;
  overflow: hidden;
  text-overflow: ellipsis;
  white-space: nowrap;
  font-size: 13px;
  color: var(--color-text-primary);
}

.subagent-trace-detail__event-duration {
  font-size: 12px;
  color: var(--color-text-tertiary);
}

.subagent-trace-detail__event-status {
  font-size: 12px;
  color: var(--color-success-600);
}

.subagent-trace-detail__event-status--error {
  color: var(--color-danger-600);
}

.subagent-trace-detail__event-body {
  display: flex;
  flex-direction: column;
  gap: 6px;
  padding: 8px 12px 12px;
  border-top: 1px solid var(--color-border-default);
}

.subagent-trace-detail__message {
  display: flex;
  flex-direction: column;
  gap: 4px;
}

.subagent-trace-detail__message-type {
  font-size: 12px;
  font-weight: 600;
  color: var(--color-text-tertiary);
}

.subagent-trace-detail__field-label {
  font-size: 12px;
  font-weight: 600;
  color: var(--color-text-secondary);
  letter-spacing: 0.04em;
}

.subagent-trace-detail__pre {
  margin: 0;
  padding: 8px;
  background: var(--color-bg-subtle);
  border-radius: var(--radius-sm);
  font-family: var(--app-font-mono, monospace);
  font-size: 12px;
  color: var(--color-text-primary);
  max-height: 200px;
  overflow: auto;
  white-space: pre-wrap;
  word-break: break-word;
  line-height: 1.5;
}

.subagent-trace-detail__pre--tall {
  max-height: 320px;
}

.subagent-trace-detail__error {
  margin: 0;
  padding: 8px;
  background: var(--color-bg-subtle);
  border-radius: var(--radius-sm);
  font-family: var(--app-font-mono, monospace);
  font-size: 12px;
  color: var(--color-danger-600);
  max-height: 200px;
  overflow: auto;
  white-space: pre-wrap;
  word-break: break-word;
  line-height: 1.5;
}

.subagent-trace-detail__metrics {
  display: flex;
  flex-wrap: wrap;
  gap: 16px;
  font-size: 13px;
  color: var(--color-text-secondary);
}

.subagent-trace-detail__empty {
  margin: 0;
  color: var(--color-text-tertiary);
  font-size: 13px;
}

@media (max-width: 768px) {
  .subagent-trace-detail__columns {
    flex-direction: column;
  }
}
</style>
