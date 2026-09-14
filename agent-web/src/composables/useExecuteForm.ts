/**
 * useExecuteForm — 执行对话框的入参表单状态（S21 前端侧）。
 *
 * 简单模式：一个主输入 + 由 state_schema 派生的自定义字段，提交时组装成
 * `{input, ...自定义键}`；后端 `synthesize_run_input` 会把字符串 `input`
 * 合成为一条 user 消息，因此这里不再要求调用方手写 messages 结构。
 * 高级模式：沿用裸 JSON 文本框，原样透传（可自带 messages）。
 *
 * 保留键（messages / history / input / *_result）不作为自定义字段渲染：
 * 前两者由引擎维护，input 就是主输入本身，*_result 是节点输出槽位。
 */
import { computed, ref, watch, type ComputedRef, type Ref } from 'vue'

import type { StateFieldDTO } from '@/api/workflow'

export type ExecuteMode = 'simple' | 'advanced'
export type ExecuteFieldControl = 'text' | 'number' | 'checkbox' | 'json'

export interface ExecuteField {
  key: string
  type: string
  control: ExecuteFieldControl
  description: string
}

export interface ExecuteForm {
  mode: Ref<ExecuteMode>
  primaryInput: Ref<string>
  fieldValues: Ref<Record<string, string>>
  advancedText: Ref<string>
  fields: ComputedRef<ExecuteField[]>
  fieldErrors: Ref<Record<string, string>>
  jsonError: Ref<string | null>
  reset: () => void
  buildPayload: () => Record<string, unknown> | null
}

const RESERVED_INPUT_KEYS = new Set(['messages', 'history', 'input'])
const NUMBER_TYPES = new Set(['int', 'float'])
const EMPTY_ADVANCED_TEXT = '{}'

export function isReservedInputKey(key: string): boolean {
  return RESERVED_INPUT_KEYS.has(key) || key.endsWith('_result')
}

export function controlForFieldType(type: string): ExecuteFieldControl {
  if (type === 'str') return 'text'
  if (NUMBER_TYPES.has(type)) return 'number'
  if (type === 'bool') return 'checkbox'
  return 'json'
}

export function buildExecuteFields(schema?: Record<string, StateFieldDTO> | null): ExecuteField[] {
  if (!schema) return []
  return Object.entries(schema)
    .filter(([key]) => !isReservedInputKey(key))
    .map(([key, field]) => ({
      key,
      type: field.type,
      control: controlForFieldType(field.type),
      description: field.description ?? '',
    }))
}

function seedValue(field: StateFieldDTO | undefined): string {
  const defaultValue = field?.default
  if (defaultValue === undefined || defaultValue === null || defaultValue === '') return ''
  if (typeof defaultValue === 'object') return JSON.stringify(defaultValue)
  return String(defaultValue)
}

interface ParsedField {
  value?: unknown
  error?: string
  omit?: boolean
}

function parseFieldValue(field: ExecuteField, raw: string): ParsedField {
  const trimmed = raw.trim()
  if (field.control === 'checkbox') {
    return trimmed === '' ? { omit: true } : { value: trimmed === 'true' }
  }
  if (trimmed === '') return { omit: true }
  if (field.control === 'number') {
    const parsed = Number(trimmed)
    return Number.isNaN(parsed) ? { error: '必须是数字' } : { value: parsed }
  }
  if (field.control === 'json') {
    try {
      return { value: JSON.parse(trimmed) }
    } catch {
      return { error: '必须是合法 JSON' }
    }
  }
  return { value: trimmed }
}

export function useExecuteForm(schema: Ref<Record<string, StateFieldDTO> | undefined>): ExecuteForm {
  const mode = ref<ExecuteMode>('simple')
  const primaryInput = ref('')
  const fieldValues = ref<Record<string, string>>({})
  const advancedText = ref(EMPTY_ADVANCED_TEXT)
  const fieldErrors = ref<Record<string, string>>({})
  const jsonError = ref<string | null>(null)

  const fields = computed(() => buildExecuteFields(schema.value))

  function seedDefaults(): void {
    const source = schema.value ?? {}
    const seeded: Record<string, string> = {}
    for (const field of fields.value) {
      seeded[field.key] = seedValue(source[field.key])
    }
    fieldValues.value = seeded
  }

  function reset(): void {
    mode.value = 'simple'
    primaryInput.value = ''
    advancedText.value = EMPTY_ADVANCED_TEXT
    fieldErrors.value = {}
    jsonError.value = null
    seedDefaults()
  }

  function buildAdvancedPayload(): Record<string, unknown> | null {
    let parsed: unknown
    try {
      parsed = JSON.parse(advancedText.value)
    } catch (err: unknown) {
      jsonError.value = err instanceof Error ? `JSON 解析失败：${err.message}` : 'JSON 解析失败'
      return null
    }
    if (typeof parsed !== 'object' || parsed === null || Array.isArray(parsed)) {
      jsonError.value = '输入必须是 JSON 对象'
      return null
    }
    return parsed as Record<string, unknown>
  }

  function buildSimplePayload(): Record<string, unknown> | null {
    const payload: Record<string, unknown> = {}
    const primary = primaryInput.value.trim()
    if (primary) payload.input = primary

    const errors: Record<string, string> = {}
    for (const field of fields.value) {
      const parsed = parseFieldValue(field, fieldValues.value[field.key] ?? '')
      if (parsed.error) {
        errors[field.key] = parsed.error
        continue
      }
      if (parsed.omit) continue
      payload[field.key] = parsed.value
    }
    if (Object.keys(errors).length > 0) {
      fieldErrors.value = errors
      return null
    }
    return payload
  }

  function buildPayload(): Record<string, unknown> | null {
    fieldErrors.value = {}
    jsonError.value = null
    return mode.value === 'advanced' ? buildAdvancedPayload() : buildSimplePayload()
  }

  // 定义是打开对话框后异步拉取的，schema 到达时按声明默认值重新播种。
  watch(schema, seedDefaults, { deep: true, immediate: true })

  return {
    mode,
    primaryInput,
    fieldValues,
    advancedText,
    fields,
    fieldErrors,
    jsonError,
    reset,
    buildPayload,
  }
}
