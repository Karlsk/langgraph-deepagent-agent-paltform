<script setup lang="ts">
/**
 * 工作流执行对话框（spec-07）：
 * JSON 输入 + 三示例预设 + 前端解析校验 + 结果展示（output + metadata）+
 * 「查看轨迹」按钮打开 WorkflowTraceDrawer。
 */
import { computed, ref, watch } from 'vue'

import { executeWorkflow, type ExecutionLogView, type WorkflowExecuteResult } from '@/api/workflow'
import { notifyError } from '@/utils/notify'
import WorkflowTraceDrawer from '@/views/workflow/WorkflowTraceDrawer.vue'

const props = defineProps<{
  modelValue: boolean
  workflowId: string
}>()

const emit = defineEmits<{
  'update:modelValue': [value: boolean]
  executed: [result: WorkflowExecuteResult]
}>()

const PRESETS: Record<string, Record<string, unknown>> = {
  '空输入': {},
  '简单消息': { message: 'hello' },
  '多字段': { user_id: 'u-001', action: 'summarize', max_tokens: 512 },
}

const presetKeys = Object.keys(PRESETS)
const selectedPreset = ref<string>(presetKeys[0])
const inputText = ref(JSON.stringify(PRESETS[presetKeys[0]]!, null, 2))
const jsonError = ref<string | null>(null)
const loading = ref(false)
const result = ref<WorkflowExecuteResult | null>(null)
const traceVisible = ref(false)
const traceLogs = ref<ExecutionLogView[]>([])

watch(() => props.modelValue, (visible) => {
  if (visible) {
    result.value = null
    jsonError.value = null
    loading.value = false
    selectedPreset.value = presetKeys[0]
    inputText.value = JSON.stringify(PRESETS[presetKeys[0]]!, null, 2)
  }
})

function onPresetChange(key: string): void {
  const preset = PRESETS[key]
  if (preset) {
    inputText.value = JSON.stringify(preset, null, 2)
    jsonError.value = null
  }
}

const hasResult = computed(() => result.value !== null)

function formatJson(data: unknown): string {
  if (data === null || data === undefined) return '—'
  try {
    return JSON.stringify(data, null, 2)
  } catch {
    return String(data)
  }
}

function formatTime(ms: unknown): string {
  if (typeof ms !== 'number') return '—'
  if (ms < 1000) return `${ms}ms`
  return `${(ms / 1000).toFixed(2)}s`
}

async function handleExecute(): Promise<void> {
  jsonError.value = null
  let parsed: Record<string, unknown>
  try {
    const raw = JSON.parse(inputText.value)
    if (typeof raw !== 'object' || raw === null || Array.isArray(raw)) {
      throw new Error('输入必须是 JSON 对象')
    }
    parsed = raw
  } catch (err: unknown) {
    const msg = err instanceof Error ? err.message : 'JSON 格式错误'
    jsonError.value = msg
    return
  }

  loading.value = true
  try {
    const res = await executeWorkflow(props.workflowId, parsed)
    result.value = res
    emit('executed', res)
  } catch {
    notifyError('执行失败，请稍后重试')
  } finally {
    loading.value = false
  }
}

function handleOpenTrace(): void {
  traceLogs.value = result.value?.metadata?.execution_logs ?? []
  traceVisible.value = true
}
</script>

<template>
  <el-dialog
    :model-value="modelValue"
    title="执行工作流"
    width="640px"
    @update:model-value="$emit('update:modelValue', $event)"
  >
    <div class="execute-dialog">
      <div class="execute-dialog__input-section">
        <div class="execute-dialog__preset-row">
          <span class="execute-dialog__label">预设示例</span>
          <el-select :model-value="selectedPreset" @change="onPresetChange">
            <el-option
              v-for="key in presetKeys"
              :key="key"
              :label="key"
              :value="key"
            />
          </el-select>
        </div>
        <div class="execute-dialog__input-row">
          <span class="execute-dialog__label">输入 (JSON)</span>
          <el-input
            v-model="inputText"
            type="textarea"
            :rows="6"
            placeholder='{"key": "value"}'
          />
        </div>
        <div v-if="jsonError" class="execute-dialog__json-error">
          {{ jsonError }}
        </div>
      </div>

      <div class="execute-dialog__actions">
        <el-button
          type="primary"
          :loading="loading"
          :disabled="loading"
          @click="handleExecute"
        >
          执行
        </el-button>
      </div>

      <div v-if="hasResult" class="execute-dialog__result">
        <h4 class="execute-dialog__result-title">执行结果</h4>
        <div class="execute-dialog__meta">
          <span>耗时：{{ formatTime(result?.metadata?.total_time_ms) }}</span>
        </div>
        <div class="execute-dialog__output">
          <span class="execute-dialog__label">输出</span>
          <pre class="execute-dialog__json">{{ formatJson(result?.output) }}</pre>
        </div>
        <div class="execute-dialog__actions">
          <el-button link type="primary" @click="handleOpenTrace">
            查看轨迹
          </el-button>
        </div>
      </div>
    </div>

    <WorkflowTraceDrawer
      v-model="traceVisible"
      :logs="traceLogs"
    />
  </el-dialog>
</template>

<style scoped>
.execute-dialog {
  display: flex;
  flex-direction: column;
  gap: 16px;
}

.execute-dialog__label {
  display: block;
  font-size: 13px;
  color: var(--color-text-secondary);
  margin-bottom: 4px;
}

.execute-dialog__preset-row {
  display: flex;
  align-items: flex-end;
  gap: 12px;
}

.execute-dialog__input-row {
  margin-top: 12px;
}

.execute-dialog__json-error {
  color: var(--color-danger-600);
  font-size: 13px;
  margin-top: 4px;
}

.execute-dialog__actions {
  display: flex;
  justify-content: flex-end;
  gap: 8px;
}

.execute-dialog__result {
  border-top: 1px solid var(--color-border-default);
  padding-top: 16px;
  display: flex;
  flex-direction: column;
  gap: 12px;
}

.execute-dialog__result-title {
  margin: 0;
  font-size: 15px;
  color: var(--color-text-primary);
}

.execute-dialog__meta {
  color: var(--color-text-secondary);
  font-size: 13px;
}

.execute-dialog__output {
  display: flex;
  flex-direction: column;
}

.execute-dialog__json {
  margin: 4px 0 0;
  padding: 12px;
  background: var(--color-bg-subtle);
  border-radius: var(--radius-sm);
  font-size: 13px;
  overflow-x: auto;
  color: var(--color-text-primary);
}
</style>
