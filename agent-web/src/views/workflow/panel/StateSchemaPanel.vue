<script setup lang="ts">
import { computed, ref } from 'vue'
import type { StateFieldDTO } from '@/api/workflow'

interface Props {
  modelValue: Record<string, StateFieldDTO>
  readonly?: boolean
}

const props = withDefaults(defineProps<Props>(), {
  readonly: false,
})

const emit = defineEmits<{
  'update:modelValue': [schema: Record<string, StateFieldDTO>]
}>()

const TYPE_OPTIONS = [
  { value: 'str', label: 'str' },
  { value: 'int', label: 'int' },
  { value: 'float', label: 'float' },
  { value: 'bool', label: 'bool' },
  { value: 'list', label: 'list' },
  { value: 'dict', label: 'dict' },
  { value: 'object', label: 'object' },
  { value: 'any', label: 'any' },
  { value: 'List[str]', label: 'List[str]' },
  { value: 'Dict[str, Any]', label: 'Dict[str, Any]' },
]

const REDUCER_OPTIONS = [
  { value: null, label: '无' },
  { value: 'add', label: 'add' },
  { value: 'last', label: 'last' },
]

const fieldNames = computed(() => Object.keys(props.modelValue))

const newFieldName = ref('')
const nameErrors = ref<Record<string, string>>({})

function isValidIdentifier(name: string): boolean {
  if (!name) return false
  if (/^\d/.test(name)) return false
  if (/[^a-zA-Z0-9_]/.test(name)) return false
  return true
}

function validateName(name: string, originalKey: string): string | null {
  if (!isValidIdentifier(name)) {
    return '字段名必须是合法标识符（字母/下划线开头，仅含字母数字下划线）'
  }
  if (name !== originalKey && fieldNames.value.includes(name)) {
    return '字段名不能重复'
  }
  return null
}

function onNameChange(originalKey: string, newName: string) {
  const error = validateName(newName, originalKey)
  if (error) {
    nameErrors.value = { ...nameErrors.value, [originalKey]: error }
    return
  }
  const { [originalKey]: _, ...rest } = nameErrors.value
  nameErrors.value = rest

  if (newName === originalKey) return

  const updated: Record<string, StateFieldDTO> = {}
  for (const [key, value] of Object.entries(props.modelValue)) {
    if (key === originalKey) {
      updated[newName] = { ...value }
    } else {
      updated[key] = { ...value }
    }
  }
  emit('update:modelValue', updated)
}

function onTypeChange(key: string, newType: string) {
  const updated = {
    ...props.modelValue,
    [key]: { ...props.modelValue[key], type: newType },
  }
  emit('update:modelValue', updated)
}

function onReducerChange(key: string, newReducer: string) {
  const reducerValue = newReducer === '' ? null : (newReducer as 'add' | 'last')
  const updated = {
    ...props.modelValue,
    [key]: { ...props.modelValue[key], reducer: reducerValue },
  }
  emit('update:modelValue', updated)
}

function onDefaultChange(key: string, newDefault: string) {
  const updated = {
    ...props.modelValue,
    [key]: { ...props.modelValue[key], default: newDefault },
  }
  emit('update:modelValue', updated)
}

function onDescriptionChange(key: string, newDescription: string) {
  const updated = {
    ...props.modelValue,
    [key]: { ...props.modelValue[key], description: newDescription },
  }
  emit('update:modelValue', updated)
}

function onDelete(key: string) {
  const { [key]: _, ...rest } = props.modelValue
  emit('update:modelValue', rest)
}

function onAdd() {
  const name = newFieldName.value.trim()
  const error = validateName(name, '')
  if (error) {
    nameErrors.value = { ...nameErrors.value, __new__: error }
    return
  }
  const updated = {
    ...props.modelValue,
    [name]: { type: 'str', description: '', reducer: null },
  }
  newFieldName.value = ''
  emit('update:modelValue', updated)
}
</script>

<template>
  <div class="state-schema-panel" :class="{ 'state-schema-panel--readonly': props.readonly }">
    <div class="state-schema-panel__header">
      <div class="state-schema-panel__title">State Schema</div>
      <div class="state-schema-panel__hint">
        仅此处声明的通道 + history + 执行过节点的 {node}_result 会出现在 execute output
      </div>
    </div>

    <div class="state-schema-panel__rows">
      <div
        v-for="(field, key) in modelValue"
        :key="key"
        class="state-schema-panel__row"
      >
        <div class="state-schema-panel__field state-schema-panel__name-input">
          <label>字段名</label>
          <input
            type="text"
            :value="key"
            :disabled="props.readonly"
            @input="onNameChange(key as string, ($event.target as HTMLInputElement).value)"
          />
          <span v-if="nameErrors[key]" class="state-schema-panel__error">{{ nameErrors[key] }}</span>
        </div>

        <div class="state-schema-panel__field state-schema-panel__type-select">
          <label>类型</label>
          <select
            :value="field.type"
            :disabled="props.readonly"
            @change="onTypeChange(key as string, ($event.target as HTMLSelectElement).value)"
          >
            <option v-for="opt in TYPE_OPTIONS" :key="opt.value" :value="opt.value">{{ opt.label }}</option>
          </select>
        </div>

        <div class="state-schema-panel__field state-schema-panel__reducer-select">
          <label>Reducer</label>
          <select
            :value="field.reducer ?? ''"
            :disabled="props.readonly"
            @change="onReducerChange(key as string, ($event.target as HTMLSelectElement).value)"
          >
            <option v-for="opt in REDUCER_OPTIONS" :key="opt.label" :value="opt.value ?? ''">{{ opt.label }}</option>
          </select>
        </div>

        <div class="state-schema-panel__field state-schema-panel__default-input">
          <label>默认值</label>
          <input
            type="text"
            :value="field.default ?? ''"
            :disabled="props.readonly"
            @change="onDefaultChange(key as string, ($event.target as HTMLInputElement).value)"
          />
        </div>

        <div class="state-schema-panel__field state-schema-panel__description-input">
          <label>描述</label>
          <input
            type="text"
            :value="field.description ?? ''"
            :disabled="props.readonly"
            @change="onDescriptionChange(key as string, ($event.target as HTMLInputElement).value)"
          />
        </div>

        <button
          v-if="!props.readonly"
          class="state-schema-panel__delete-btn"
          @click="onDelete(key as string)"
        >
          删除
        </button>
      </div>
    </div>

    <div v-if="!props.readonly" class="state-schema-panel__add-row">
      <div class="state-schema-panel__add-name-input">
        <input
          v-model="newFieldName"
          type="text"
          placeholder="新字段名"
        />
        <span v-if="nameErrors.__new__" class="state-schema-panel__error">{{ nameErrors.__new__ }}</span>
      </div>
      <button class="state-schema-panel__add-btn" @click="onAdd">新增字段</button>
    </div>
  </div>
</template>

<style scoped>
.state-schema-panel {
  display: flex;
  flex-direction: column;
  height: 100%;
  background: var(--color-bg-surface);
  border-left: 1px solid var(--color-border-default);
  width: 400px;
  padding: 16px;
  overflow-y: auto;
}

.state-schema-panel--readonly {
  opacity: 0.8;
}

.state-schema-panel__header {
  margin-bottom: 16px;
}

.state-schema-panel__title {
  font-weight: 600;
  font-size: 16px;
  color: var(--color-text-primary);
  margin-bottom: 8px;
}

.state-schema-panel__hint {
  font-size: 12px;
  color: var(--color-text-tertiary);
  line-height: 1.5;
}

.state-schema-panel__rows {
  display: flex;
  flex-direction: column;
  gap: 12px;
}

.state-schema-panel__row {
  display: flex;
  flex-direction: column;
  gap: 8px;
  padding: 12px;
  border: 1px solid var(--color-border-default);
  border-radius: var(--radius-md);
  background: var(--color-bg-elevated);
}

.state-schema-panel__field {
  display: flex;
  flex-direction: column;
  gap: 4px;
}

.state-schema-panel__field label {
  font-size: 12px;
  font-weight: 500;
  color: var(--color-text-secondary);
}

.state-schema-panel__field input,
.state-schema-panel__field select {
  padding: 6px 10px;
  border: 1px solid var(--color-border-default);
  border-radius: var(--radius-md);
  font-size: 13px;
  color: var(--color-text-primary);
  background: var(--color-bg-surface);
  transition: border-color var(--duration-fast) var(--ease-standard);
}

.state-schema-panel__field input:focus,
.state-schema-panel__field select:focus {
  outline: none;
  border-color: var(--color-border-strong);
}

.state-schema-panel__field input:disabled,
.state-schema-panel__field select:disabled {
  opacity: 0.5;
  cursor: not-allowed;
}

.state-schema-panel__error {
  font-size: 11px;
  color: var(--color-danger-600);
}

.state-schema-panel__delete-btn {
  align-self: flex-end;
  padding: 4px 12px;
  background: var(--color-danger-600);
  color: var(--color-bg-surface);
  border: none;
  border-radius: var(--radius-md);
  font-size: 12px;
  cursor: pointer;
  transition: opacity var(--duration-fast) var(--ease-standard);
}

.state-schema-panel__delete-btn:hover {
  opacity: 0.85;
}

.state-schema-panel__add-row {
  display: flex;
  align-items: flex-end;
  gap: 8px;
  margin-top: 16px;
  padding-top: 16px;
  border-top: 1px solid var(--color-border-default);
}

.state-schema-panel__add-name-input {
  flex: 1;
  display: flex;
  flex-direction: column;
  gap: 4px;
}

.state-schema-panel__add-name-input input {
  padding: 6px 10px;
  border: 1px solid var(--color-border-default);
  border-radius: var(--radius-md);
  font-size: 13px;
  color: var(--color-text-primary);
  background: var(--color-bg-surface);
}

.state-schema-panel__add-name-input input:focus {
  outline: none;
  border-color: var(--color-border-strong);
}

.state-schema-panel__add-btn {
  padding: 6px 16px;
  background: var(--color-primary-500);
  color: var(--color-bg-surface);
  border: none;
  border-radius: var(--radius-md);
  font-size: 13px;
  font-weight: 500;
  cursor: pointer;
  transition: opacity var(--duration-fast) var(--ease-standard);
  white-space: nowrap;
}

.state-schema-panel__add-btn:hover {
  opacity: 0.85;
}
</style>
