<script setup lang="ts">
/**
 * ExecuteInputFields — 执行对话框简单模式的自定义字段区（S21 前端侧）。
 *
 * 字段由 state_schema 派生（保留键已被 useExecuteForm 过滤），一律以字符串
 * 承载输入值，类型解析与校验集中在 composable，便于单测。
 * 数字字段用 type="text" + inputmode="decimal"：浏览器原生 number 输入会把
 * 非法字符静默清空，用户就看不到「必须是数字」的提示了。
 */
import type { ExecuteField } from '@/composables/useExecuteForm'

interface Props {
  fields: ExecuteField[]
  modelValue: Record<string, string>
  errors?: Record<string, string>
}

const props = withDefaults(defineProps<Props>(), {
  errors: () => ({}),
})

const emit = defineEmits<{
  'update:modelValue': [values: Record<string, string>]
}>()

function onInput(key: string, event: Event): void {
  const target = event.target as HTMLInputElement | HTMLTextAreaElement
  emit('update:modelValue', { ...props.modelValue, [key]: target.value })
}

function onCheck(key: string, event: Event): void {
  const target = event.target as HTMLInputElement
  emit('update:modelValue', { ...props.modelValue, [key]: target.checked ? 'true' : 'false' })
}
</script>

<template>
  <div class="execute-fields">
    <p v-if="fields.length === 0" class="execute-fields__empty">
      该工作流没有可编辑的自定义字段
    </p>

    <div
      v-for="field in fields"
      :key="field.key"
      class="execute-field"
      :class="{ 'execute-field--error': errors[field.key] }"
    >
      <label class="execute-field__label">
        <span class="execute-field__key">{{ field.key }}</span>
        <span class="execute-field__type">{{ field.type }}</span>
        <span v-if="field.description" class="execute-field__desc">{{ field.description }}</span>
      </label>

      <input
        v-if="field.control === 'text'"
        type="text"
        class="execute-field__text"
        :value="modelValue[field.key] ?? ''"
        :placeholder="field.key"
        @input="onInput(field.key, $event)"
      />
      <input
        v-else-if="field.control === 'number'"
        type="text"
        inputmode="decimal"
        class="execute-field__number"
        :value="modelValue[field.key] ?? ''"
        placeholder="数字"
        @input="onInput(field.key, $event)"
      />
      <input
        v-else-if="field.control === 'checkbox'"
        type="checkbox"
        class="execute-field__checkbox"
        :checked="modelValue[field.key] === 'true'"
        @change="onCheck(field.key, $event)"
      />
      <textarea
        v-else
        class="execute-field__json"
        rows="3"
        :value="modelValue[field.key] ?? ''"
        placeholder="JSON，例如 {&quot;key&quot;: &quot;value&quot;}"
        @input="onInput(field.key, $event)"
      />

      <span v-if="errors[field.key]" class="execute-field__error">{{ errors[field.key] }}</span>
    </div>
  </div>
</template>

<style scoped>
.execute-fields {
  display: flex;
  flex-direction: column;
  gap: 12px;
}

.execute-fields__empty {
  margin: 0;
  font-size: 12px;
  color: var(--color-text-tertiary);
}

.execute-field {
  display: flex;
  flex-direction: column;
  gap: 4px;
  padding: 10px 12px;
  border: 1px solid var(--color-border-default);
  border-radius: var(--radius-md);
  background: var(--color-bg-elevated);
}

.execute-field--error {
  border-color: var(--color-danger-600);
}

.execute-field__label {
  display: flex;
  align-items: baseline;
  gap: 8px;
  font-size: 12px;
  font-weight: 500;
  color: var(--color-text-secondary);
}

.execute-field__key {
  font-family: var(--font-mono, monospace);
  color: var(--color-text-primary);
}

.execute-field__type {
  padding: 0 6px;
  border-radius: var(--radius-sm);
  background: var(--color-bg-subtle);
  font-size: 11px;
  color: var(--color-text-tertiary);
}

.execute-field__desc {
  font-weight: 400;
  color: var(--color-text-tertiary);
}

.execute-field__text,
.execute-field__number,
.execute-field__json {
  padding: 6px 10px;
  border: 1px solid var(--color-border-default);
  border-radius: var(--radius-md);
  font-size: 13px;
  color: var(--color-text-primary);
  background: var(--color-bg-surface);
  transition: border-color var(--duration-fast) var(--ease-standard);
}

.execute-field__json {
  font-family: var(--font-mono, monospace);
  resize: vertical;
}

.execute-field__text:focus,
.execute-field__number:focus,
.execute-field__json:focus {
  outline: none;
  border-color: var(--color-border-strong);
}

.execute-field__checkbox {
  width: 16px;
  height: 16px;
  align-self: flex-start;
}

.execute-field__error {
  font-size: 11px;
  color: var(--color-danger-600);
}
</style>
