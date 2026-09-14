<script setup lang="ts">
/**
 * 工作流执行对话框（spec-07 / S21）：
 * 简单模式 = 主输入 + 由 state_schema 派生的自定义字段（后端把字符串 input
 * 合成为 messages，调用方无需手写消息结构）；高级模式 = 裸 JSON 原样透传。
 * 结果展示（output + metadata）+「查看轨迹」按钮打开 WorkflowTraceDrawer。
 */
import { computed, ref, watch } from 'vue'

import {
  executeWorkflow,
  getWorkflow,
  type ExecutionLogView,
  type StateFieldDTO,
  type WorkflowExecuteResult,
} from '@/api/workflow'
import { useExecuteForm } from '@/composables/useExecuteForm'
import { toExecuteErrorMessage } from '@/utils/workflowErrors'
import ExecuteInputFields from '@/views/workflow/panel/ExecuteInputFields.vue'
import WorkflowTraceDrawer from '@/views/workflow/WorkflowTraceDrawer.vue'

const props = defineProps<{
  modelValue: boolean
  workflowId: string
}>()

const emit = defineEmits<{
  'update:modelValue': [value: boolean]
  executed: [result: WorkflowExecuteResult]
}>()

const MODE_OPTIONS = [
  { value: 'simple', label: '简单模式' },
  { value: 'advanced', label: '高级模式（JSON）' },
] as const

const stateSchema = ref<Record<string, StateFieldDTO> | undefined>(undefined)
const {
  mode,
  primaryInput,
  fieldValues,
  advancedText,
  fields,
  fieldErrors,
  jsonError,
  reset,
  buildPayload,
} = useExecuteForm(stateSchema)

const loading = ref(false)
const result = ref<WorkflowExecuteResult | null>(null)
const executeError = ref<string | null>(null)
const traceVisible = ref(false)
const traceLogs = ref<ExecutionLogView[]>([])

async function loadSchema(): Promise<void> {
  try {
    const definition = await getWorkflow(props.workflowId, 'json')
    stateSchema.value = 'state_schema' in definition ? definition.state_schema : undefined
  } catch {
    // 定义拉取失败时降级为「只有主输入」，不阻断执行。
    stateSchema.value = undefined
  }
}

watch(
  () => props.modelValue,
  (visible) => {
    if (!visible) return
    result.value = null
    executeError.value = null
    loading.value = false
    reset()
    void loadSchema()
  },
  { immediate: true },
)

const hasResult = computed(() => result.value !== null)

const outputFields = computed(() => {
  if (!result.value) return null
  const { metadata: _meta, ...rest } = result.value
  return rest
})

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
  executeError.value = null
  // 结果区只反映最近一次提交：否则校验失败时旧输出会与新错误同屏，被误读为本次结果。
  result.value = null
  const payload = buildPayload()
  if (payload === null) return

  loading.value = true
  try {
    const res = await executeWorkflow(props.workflowId, payload)
    result.value = res
    emit('executed', res)
  } catch (err: unknown) {
    // 请求层已 toast 过一次，这里把摘要留在对话框内便于对照输入排查。
    executeError.value = toExecuteErrorMessage(err)
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
        <div class="execute-dialog__mode-row">
          <label
            v-for="option in MODE_OPTIONS"
            :key="option.value"
            class="execute-dialog__mode"
          >
            <input
              v-model="mode"
              type="radio"
              name="execute-input-mode"
              :value="option.value"
            />
            <span>{{ option.label }}</span>
          </label>
        </div>

        <div v-if="mode === 'simple'" class="execute-dialog__simple">
          <div class="execute-dialog__primary">
            <span class="execute-dialog__label">主输入</span>
            <el-input
              v-model="primaryInput"
              type="textarea"
              :rows="4"
              placeholder="要发给工作流的内容"
            />
            <p class="execute-dialog__hint">
              主输入会自动转换为一条 user 消息发给工作流；需要完整的消息结构请切换到高级模式。
            </p>
          </div>

          <ExecuteInputFields
            v-model="fieldValues"
            :fields="fields"
            :errors="fieldErrors"
          />
        </div>

        <div v-else class="execute-dialog__advanced">
          <span class="execute-dialog__label">输入 (JSON)</span>
          <el-input
            v-model="advancedText"
            type="textarea"
            :rows="6"
            placeholder='{"input": "hello"}'
          />
        </div>

        <div v-if="jsonError" class="execute-dialog__json-error">
          {{ jsonError }}
        </div>
        <div v-if="executeError" class="execute-dialog__error">
          {{ executeError }}
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
          <span>耗时：{{ formatTime(result?.metadata?.duration_ms) }}</span>
        </div>
        <div class="execute-dialog__output">
          <span class="execute-dialog__label">输出</span>
          <pre class="execute-dialog__json">{{ formatJson(outputFields) }}</pre>
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

.execute-dialog__input-section {
  display: flex;
  flex-direction: column;
  gap: 12px;
}

.execute-dialog__mode-row {
  display: flex;
  gap: 16px;
}

.execute-dialog__mode {
  display: inline-flex;
  align-items: center;
  gap: 6px;
  font-size: 13px;
  color: var(--color-text-secondary);
  cursor: pointer;
}

.execute-dialog__simple {
  display: flex;
  flex-direction: column;
  gap: 12px;
}

.execute-dialog__hint {
  margin: 4px 0 0;
  font-size: 12px;
  line-height: 1.5;
  color: var(--color-text-tertiary);
}

.execute-dialog__json-error,
.execute-dialog__error {
  color: var(--color-danger-600);
  font-size: 13px;
  line-height: 1.5;
  word-break: break-word;
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
