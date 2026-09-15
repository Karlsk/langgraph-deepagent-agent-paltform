<script setup lang="ts">
import { computed, reactive, ref, watch } from 'vue'

interface Props {
  config: Record<string, unknown>
  readonly?: boolean
}

const props = withDefaults(defineProps<Props>(), {
  readonly: false,
})

const emit = defineEmits<{
  'update:config': [config: Record<string, unknown>]
}>()

const formModel = reactive<Record<string, unknown>>({
  url: props.config.url ?? '',
  method: props.config.method ?? 'POST',
  body_template: props.config.body_template ?? '',
  response_path: props.config.response_path ?? null,
  mock_enabled: props.config.mock_enabled ?? false,
  mock_responses: props.config.mock_responses ?? {},
})

interface HeaderRow {
  id: number
  key: string
  value: string
}

let nextHeaderId = 0

function isPlainObject(v: unknown): v is Record<string, unknown> {
  return typeof v === 'object' && v !== null && !Array.isArray(v)
}

function rowsFromHeaders(headers: unknown): HeaderRow[] {
  if (!isPlainObject(headers)) return []
  return Object.entries(headers)
    .filter(([, v]) => typeof v === 'string')
    .map(([key, value]) => ({ id: nextHeaderId++, key, value: value as string }))
}

function headersFromRows(rows: HeaderRow[]): Record<string, string> {
  const result: Record<string, string> = {}
  for (const row of rows) {
    if (row.key) {
      result[row.key] = row.value
    }
  }
  return result
}

function sameHeaders(a: Record<string, string>, b: Record<string, string>): boolean {
  const ka = Object.keys(a)
  const kb = Object.keys(b)
  if (ka.length !== kb.length) return false
  return ka.every((k) => a[k] === b[k])
}

const headerRows = ref<HeaderRow[]>(rowsFromHeaders(props.config.headers))

watch(
  () => props.config,
  (newConfig) => {
    formModel.url = newConfig.url ?? ''
    formModel.method = newConfig.method ?? 'POST'
    formModel.body_template = newConfig.body_template ?? ''
    formModel.response_path = newConfig.response_path ?? null
    formModel.mock_enabled = newConfig.mock_enabled ?? false
    formModel.mock_responses = newConfig.mock_responses ?? {}

    const incoming = isPlainObject(newConfig.headers) ? newConfig.headers : {}
    const current = headersFromRows(headerRows.value)
    if (!sameHeaders(current, incoming as Record<string, string>)) {
      headerRows.value = rowsFromHeaders(newConfig.headers)
    }
  },
  { deep: true },
)

function emitConfig() {
  emit('update:config', { ...props.config, ...formModel, headers: headersFromRows(headerRows.value) })
}

function onFieldChange() {
  emitConfig()
}

function addHeaderRow() {
  headerRows.value = [...headerRows.value, { id: nextHeaderId++, key: '', value: '' }]
  emitConfig()
}

function removeHeaderRow(id: number) {
  headerRows.value = headerRows.value.filter((r) => r.id !== id)
  emitConfig()
}

function onHeaderKeyChange(id: number, newKey: string) {
  headerRows.value = headerRows.value.map((r) => (r.id === id ? { ...r, key: newKey } : r))
  emitConfig()
}

function onHeaderValueChange(id: number, newValue: string) {
  headerRows.value = headerRows.value.map((r) => (r.id === id ? { ...r, value: newValue } : r))
  emitConfig()
}

const methodOptions = [
  { value: 'GET', label: 'GET' },
  { value: 'POST', label: 'POST' },
  { value: 'PUT', label: 'PUT' },
  { value: 'PATCH', label: 'PATCH' },
  { value: 'DELETE', label: 'DELETE' },
]

const mockResponsesText = computed(() => {
  try {
    return JSON.stringify(formModel.mock_responses, null, 2)
  } catch {
    return '{}'
  }
})

function onMockResponsesChange(text: string) {
  try {
    formModel.mock_responses = JSON.parse(text)
    onFieldChange()
  } catch {
    // Invalid JSON, don't emit
  }
}
</script>

<template>
  <div class="http-node-form">
    <el-form label-position="top" :disabled="props.readonly">
      <el-form-item label="URL" prop="url">
        <el-input v-model="formModel.url" placeholder="https://api.example.com" @change="onFieldChange" />
        <div class="agent-form-helptext">服务端将做 SSRF 白名单校验</div>
      </el-form-item>

      <el-form-item label="Method" prop="method">
        <el-select v-model="formModel.method" @change="onFieldChange">
          <el-option
            v-for="opt in methodOptions"
            :key="opt.value"
            :value="opt.value"
            :label="opt.label"
          />
        </el-select>
      </el-form-item>

      <el-form-item label="Headers" prop="headers">
        <div class="http-node-form__headers">
          <div
            v-for="row in headerRows"
            :key="row.id"
            class="http-node-form__header-row"
          >
            <el-input
              data-testid="header-key"
              :model-value="row.key"
              :disabled="props.readonly"
              placeholder="Header-Name"
              @change="(val: string) => onHeaderKeyChange(row.id, val)"
            />
            <el-input
              data-testid="header-value"
              :model-value="row.value"
              :disabled="props.readonly"
              placeholder="value"
              @change="(val: string) => onHeaderValueChange(row.id, val)"
            />
            <button
              v-if="!props.readonly"
              type="button"
              class="http-node-form__header-delete"
              @click="removeHeaderRow(row.id)"
            >
              删除
            </button>
          </div>
          <button
            v-if="!props.readonly"
            type="button"
            class="http-node-form__header-add"
            @click="addHeaderRow"
          >
            新增 Header
          </button>
        </div>
      </el-form-item>

      <el-form-item label="Body Template" prop="body_template">
        <el-input
          v-model="formModel.body_template"
          type="textarea"
          :rows="3"
          placeholder='{"query": "{input}"}'
          @change="onFieldChange"
        />
        <div class="agent-form-helptext">支持 {input} 占位符</div>
      </el-form-item>

      <el-form-item label="Response Path" prop="response_path">
        <el-input v-model="formModel.response_path" placeholder="data" @change="onFieldChange" />
      </el-form-item>

      <el-form-item label="Mock 模式" prop="mock_enabled">
        <el-switch v-model="formModel.mock_enabled" @change="onFieldChange" />
      </el-form-item>

      <el-form-item v-if="formModel.mock_enabled" label="Mock Responses" prop="mock_responses">
        <el-input
          :model-value="mockResponsesText"
          type="textarea"
          :rows="5"
          :disabled="!formModel.mock_enabled || props.readonly"
          @change="onMockResponsesChange"
        />
        <div class="agent-form-helptext">JSON 格式，键为 "{method} {url}"</div>
      </el-form-item>
    </el-form>
  </div>
</template>

<style scoped>
.http-node-form {
  display: flex;
  flex-direction: column;
  gap: 16px;
}

.agent-form-helptext {
  font-size: 12px;
  color: var(--color-text-tertiary);
  margin-top: 4px;
}

.http-node-form__headers {
  display: flex;
  flex-direction: column;
  gap: 8px;
  width: 100%;
}

.http-node-form__header-row {
  display: flex;
  gap: 8px;
  align-items: center;
}

.http-node-form__header-row .el-input-stub,
.http-node-form__header-row :deep(input) {
  flex: 1;
}

.http-node-form__header-delete,
.http-node-form__header-add {
  padding: 4px 12px;
  border: 1px solid var(--color-border-default);
  border-radius: var(--radius-md);
  font-size: 12px;
  cursor: pointer;
  background: var(--color-bg-surface);
  color: var(--color-text-secondary);
  white-space: nowrap;
}

.http-node-form__header-delete {
  color: var(--color-danger-600);
  border-color: var(--color-danger-600);
}

.http-node-form__header-add {
  align-self: flex-start;
}
</style>
