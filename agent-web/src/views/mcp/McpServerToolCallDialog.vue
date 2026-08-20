<script setup lang="ts">
/**
 * McpServerToolCallDialog MCP 工具调试调用独立弹窗（task-ccc UX 改进最终版）：
 * - props.tool === null 时不渲染（由父组件 McpServerToolsDialog 控制）；
 * - 入参用单个 JSON 文本框接收，提交前校验：合法 JSON + 必填字段覆盖（详见 validate）；
 * - POST /mcp-servers/{name}/call-tool（详见 agent_apps.py L458-481）；
 * - 422/502/504 由 request.ts 拦截器 toast，本地额外把错误体落到红色面板便于排查。
 *
 * 与原 McpServerToolsDialog 行内展开版的差异：
 * - 位置：独立 el-dialog，固定视口中央，不再压在表格底部（修 #1 滚动）；
 * - 「调用」按钮在父组件侧已改为实心 primary（非 link 链接），不在本组件。
 *
 * 简化决策记录：先版本放弃了「JSON Schema → 动态表单」方案（见 docs/workflow-reimpl-plan
 * 之外的早期草稿），原因是该项目 Vue 3.5 + vite-plugin-vue 5.2 编译器在
 * `el-form-item` 嵌套命名 slot + 多 v-for + v-if 组合时触发已知 codegen bug
 * （[Codegen node is missing for element/if/for node]）。回退到 JSON 文本框后稳定。
 * 「根据 JSON Schema 自动生成表单」作为后续 UX 迭代项保留。
 */
import { computed, ref, watch } from 'vue'

import { callMcpServerTool, type McpToolInfo } from '@/api/mcp'
import { notifyError, notifySuccess } from '@/utils/notify'

/* -------------------------------------------------------------------------- */
/*  Props / Emits                                                              */
/* -------------------------------------------------------------------------- */

const props = defineProps<{
  serverName: string
  tool: McpToolInfo | null
  modelValue: boolean
}>()

const emit = defineEmits<{
  'update:modelValue': [value: boolean]
}>()

/* -------------------------------------------------------------------------- */
/*  状态                                                                       */
/* -------------------------------------------------------------------------- */

const argsText = ref<string>('')
const submitting = ref(false)
const result = ref<unknown>(null)
const errorMessage = ref<string | null>(null)
const validationError = ref<string | null>(null)

/** schema.required 的安全取值（兼容 undefined / 非数组），避免模板类型断言 */
const requiredFields = computed<string[]>(() => {
  const required =
    (props.tool?.args_schema.required as string[] | undefined) ?? []
  return Array.isArray(required) ? required : []
})

/* -------------------------------------------------------------------------- */
/*  生命周期                                                                   */
/* -------------------------------------------------------------------------- */

watch(
  () => props.tool,
  (next) => {
    argsText.value = ''
    result.value = null
    errorMessage.value = null
    validationError.value = null
    if (next) {
      // 预填 schema.default 合并为参考 JSON（仅在 properties 全有 default 时）
      const schema = next.args_schema
      const properties =
        (schema.properties as Record<string, unknown> | undefined) ?? {}
      const defaults: Record<string, unknown> = {}
      let hasAnyDefault = false
      for (const [key, def] of Object.entries(properties)) {
        const d = (def as { default?: unknown }).default
        if (d !== undefined) {
          defaults[key] = d
          hasAnyDefault = true
        }
      }
      if (hasAnyDefault) {
        argsText.value = JSON.stringify(defaults, null, 2)
      }
    }
  },
  { immediate: true },
)

/* -------------------------------------------------------------------------- */
/*  工具函数                                                                   */
/* -------------------------------------------------------------------------- */

function closeDialog(): void {
  emit('update:modelValue', false)
}

function handleModelUpdate(value: boolean): void {
  emit('update:modelValue', value)
}

function formatResult(value: unknown): string {
  if (value === null || value === undefined) return ''
  if (typeof value === 'string') return value
  return JSON.stringify(value, null, 2)
}

function namespacedName(): string {
  if (!props.tool) return ''
  return `${props.serverName}__${props.tool.name}`
}

/**
 * 校验入参文本：
 * - 必须是合法 JSON object；
 * - 必填字段（schema.required[]）必须在 arguments 中存在且非空。
 * 返回 { ok, parsed?, error }。
 */
function parseAndValidate(): { ok: true; parsed: Record<string, unknown> } | { ok: false; error: string } {
  const text = argsText.value.trim()
  if (text.length === 0) {
    // 允许空对象（无参工具）：当 schema.required 为空时通过；否则要求显式 {}
    const required =
      (props.tool?.args_schema.required as string[] | undefined) ?? []
    if (required.length === 0) {
      return { ok: true, parsed: {} }
    }
    return { ok: false, error: 'arguments 不能为空（必填字段未提供）' }
  }
  let parsed: unknown
  try {
    parsed = JSON.parse(text)
  } catch (err) {
    return { ok: false, error: `arguments 不是合法 JSON：${(err as Error).message}` }
  }
  if (parsed === null || typeof parsed !== 'object' || Array.isArray(parsed)) {
    return { ok: false, error: 'arguments 必须是 JSON object（不能是数组或基本类型）' }
  }
  const required =
    (props.tool?.args_schema.required as string[] | undefined) ?? []
  const missing: string[] = []
  for (const key of required) {
    const v = (parsed as Record<string, unknown>)[key]
    if (v === undefined || v === null || v === '') missing.push(key)
  }
  if (missing.length > 0) {
    return { ok: false, error: `缺少必填字段：${missing.join(', ')}` }
  }
  return { ok: true, parsed: parsed as Record<string, unknown> }
}

function handleReset(): void {
  argsText.value = ''
  result.value = null
  errorMessage.value = null
  validationError.value = null
}

async function handleSubmit(): Promise<void> {
  if (!props.tool) return
  const v = parseAndValidate()
  if (!v.ok) {
    validationError.value = v.error
    notifyError(v.error)
    return
  }
  validationError.value = null
  submitting.value = true
  result.value = null
  errorMessage.value = null
  try {
    const response = await callMcpServerTool(props.serverName, {
      tool_name: props.tool.name,
      arguments: v.parsed,
    })
    result.value = response.result
    notifySuccess(`已调用：${props.tool.name}`)
  } catch (err: unknown) {
    const message =
      err && typeof err === 'object' && 'response' in err
        ? JSON.stringify(
            (err as { response?: { data?: unknown; status?: number } }).response?.data ??
              (err as { response?: { status?: number } }).response?.status ??
              err,
            null,
            2,
          )
        : err instanceof Error
          ? err.message
          : '调用失败'
    errorMessage.value = typeof message === 'string' ? message : JSON.stringify(message, null, 2)
  } finally {
    submitting.value = false
  }
}

// 模板引用占位（vue-tsc 不跟踪 template → script）
void handleReset
void handleSubmit
void formatResult
void closeDialog
void handleModelUpdate
void namespacedName
</script>

<template>
  <el-dialog :model-value="modelValue" :title="`调试调用 — ${tool?.name ?? ''}`" width="640px" :close-on-click-modal="false" @update:model-value="handleModelUpdate">
    <div v-if="tool" class="mcp-call-dialog">
      <div class="mcp-call-dialog__header">
        <div class="mcp-call-dialog__title-line">
          <code class="mcp-call-dialog__namespaced">{{ namespacedName() }}</code>
          <span v-if="tool.description" class="mcp-call-dialog__desc">{{ tool.description }}</span>
        </div>
      </div>
      <div class="mcp-call-dialog__field">
        <div class="mcp-call-dialog__label">
          <span>arguments（JSON object）</span>
        </div>
        <el-input v-model="argsText" type="textarea" :autosize="{ minRows: 6, maxRows: 18 }" placeholder='{"key": "value"}' class="mcp-call-dialog__control" />
        <p
          v-if="requiredFields.length > 0"
          class="mcp-call-dialog__hint"
        >
          必填字段：{{ requiredFields.join(', ') }}
        </p>
      </div>
      <div v-if="validationError" class="mcp-call-dialog__error">
        <span class="mcp-call-dialog__error-label">参数校验失败</span>
        <pre class="mcp-call-dialog__result mcp-call-dialog__result--err">{{ validationError }}</pre>
      </div>
      <div v-if="errorMessage" class="mcp-call-dialog__error">
        <span class="mcp-call-dialog__error-label">调用失败</span>
        <pre class="mcp-call-dialog__result mcp-call-dialog__result--err">{{ errorMessage }}</pre>
      </div>
      <div
        v-if="!errorMessage && result !== null && result !== undefined"
        class="mcp-call-dialog__success"
      >
        <span class="mcp-call-dialog__success-label">result（成功）</span>
        <pre class="mcp-call-dialog__result mcp-call-dialog__result--ok">{{ formatResult(result) }}</pre>
      </div>
    </div>
    <template #footer>
      <el-button @click="closeDialog">关闭</el-button>
      <el-button @click="handleReset">清空</el-button>
      <el-button type="primary" :loading="submitting" @click="handleSubmit">执行调用</el-button>
    </template>
  </el-dialog>
</template>

<style scoped>
.mcp-call-dialog {
  display: flex;
  flex-direction: column;
  gap: 16px;
}

.mcp-call-dialog__header {
  border-bottom: 1px solid var(--color-border-light, #e5e7eb);
  padding-bottom: 8px;
}

.mcp-call-dialog__title-line {
  display: flex;
  align-items: center;
  gap: 12px;
  flex-wrap: wrap;
}

.mcp-call-dialog__namespaced {
  font-family: var(--app-font-mono, monospace);
  font-size: 13px;
  color: var(--color-primary-600, #2563eb);
  background: var(--color-bg-subtle, #f8fafc);
  padding: 2px 8px;
  border-radius: var(--radius-sm, 4px);
}

.mcp-call-dialog__desc {
  font-size: 12px;
  color: var(--color-text-secondary, #475569);
  line-height: 1.5;
}

.mcp-call-dialog__field {
  display: flex;
  flex-direction: column;
  gap: 6px;
}

.mcp-call-dialog__label {
  font-weight: 500;
  font-size: 13px;
  color: var(--color-text-primary, #0f172a);
}

.mcp-call-dialog__hint {
  margin: 0;
  font-size: 12px;
  color: var(--color-text-secondary, #475569);
}

.mcp-call-dialog__control {
  width: 100%;
}

.mcp-call-dialog__error,
.mcp-call-dialog__success {
  margin-top: 8px;
}

.mcp-call-dialog__error-label {
  display: block;
  font-size: 12px;
  font-weight: 600;
  color: var(--color-danger-600, #dc2626);
  margin-bottom: 4px;
}

.mcp-call-dialog__success-label {
  display: block;
  font-size: 12px;
  font-weight: 600;
  color: var(--color-success-600, #16a34a);
  margin-bottom: 4px;
}

.mcp-call-dialog__result {
  margin: 0;
  padding: 10px 12px;
  border-radius: var(--radius-sm, 4px);
  font-family: var(--app-font-mono, monospace);
  font-size: 12px;
  max-height: 280px;
  overflow: auto;
  white-space: pre-wrap;
  word-break: break-all;
}

.mcp-call-dialog__result--ok {
  background: rgba(22, 163, 74, 0.06);
  border: 1px solid rgba(22, 163, 74, 0.25);
  color: var(--color-text-primary, #0f172a);
}

.mcp-call-dialog__result--err {
  background: rgba(220, 38, 38, 0.06);
  border: 1px solid rgba(220, 38, 38, 0.25);
  color: var(--color-text-primary, #0f172a);
}
</style>