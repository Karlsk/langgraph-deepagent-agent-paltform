<script setup lang="ts">
/**
 * 工作流执行轨迹抽屉（spec-07）：
 * 渲染 metadata.execution_logs 逐节点过程，按 timestamp 升序排列；
 * error 节点标红；logs 缺失时展示降级提示。
 */
import { computed } from 'vue'

import type { ExecutionLogView } from '@/api/workflow'

const props = defineProps<{
  modelValue: boolean
  logs?: ExecutionLogView[]
}>()

defineEmits<{
  'update:modelValue': [value: boolean]
}>()

const sortedLogs = computed(() => {
  if (!props.logs || props.logs.length === 0) return []
  return [...props.logs].sort(
    (a, b) => new Date(a.timestamp).getTime() - new Date(b.timestamp).getTime(),
  )
})

const hasLogs = computed(() => sortedLogs.value.length > 0)

function formatTime(ms: number): string {
  if (ms < 1000) return `${ms}ms`
  return `${(ms / 1000).toFixed(2)}s`
}

function formatJson(data: unknown): string {
  if (data === null || data === undefined) return '—'
  try {
    return JSON.stringify(data, null, 2)
  } catch {
    return String(data)
  }
}
</script>

<template>
  <el-drawer
    :model-value="modelValue"
    title="执行轨迹"
    size="520px"
    @update:model-value="$emit('update:modelValue', $event)"
  >
    <div v-if="!hasLogs" class="trace-empty">
      本次响应未包含执行轨迹
    </div>
    <div v-else class="trace-list">
      <div
        v-for="(log, idx) in sortedLogs"
        :key="idx"
        class="trace-entry"
        :class="{ 'trace-entry--error': !!log.error }"
      >
        <div class="trace-entry__header">
          <span class="trace-entry__name">{{ log.node_name }}</span>
          <el-tag size="small" :type="log.node_type === 'llm' ? '' : 'info'">
            {{ log.node_type }}
          </el-tag>
          <span class="trace-entry__time">{{ formatTime(log.execution_time_ms) }}</span>
        </div>
        <div v-if="log.error" class="trace-entry__error">
          {{ log.error }}
        </div>
        <details class="trace-entry__detail">
          <summary>输入</summary>
          <pre class="trace-entry__json">{{ formatJson(log.input_data) }}</pre>
        </details>
        <details class="trace-entry__detail">
          <summary>输出</summary>
          <pre class="trace-entry__json">{{ formatJson(log.output_data) }}</pre>
        </details>
      </div>
    </div>
  </el-drawer>
</template>

<style scoped>
.trace-empty {
  padding: 32px 0;
  text-align: center;
  color: var(--color-text-tertiary);
}

.trace-list {
  display: flex;
  flex-direction: column;
  gap: 12px;
}

.trace-entry {
  border: 1px solid var(--color-border-default);
  border-radius: var(--radius-md);
  padding: 12px;
  background: var(--color-bg-surface);
}

.trace-entry--error {
  border-color: var(--color-danger-600);
}

.trace-entry__header {
  display: flex;
  align-items: center;
  gap: 8px;
  margin-bottom: 8px;
}

.trace-entry__name {
  font-weight: 600;
  color: var(--color-text-primary);
}

.trace-entry__time {
  margin-left: auto;
  color: var(--color-text-tertiary);
  font-size: 13px;
}

.trace-entry__error {
  color: var(--color-danger-600);
  font-size: 13px;
  margin-bottom: 8px;
}

.trace-entry__detail {
  margin-top: 4px;
}

.trace-entry__detail summary {
  cursor: pointer;
  color: var(--color-text-secondary);
  font-size: 13px;
}

.trace-entry__json {
  margin: 4px 0 0;
  padding: 8px;
  background: var(--color-bg-subtle);
  border-radius: var(--radius-sm);
  font-size: 12px;
  overflow-x: auto;
  color: var(--color-text-primary);
}
</style>
