/**
 * useExecuteForm composable 测试（S21 前端侧）。
 *
 * 零真实网络、零 Element Plus：纯逻辑。锁定
 *   - 保留键过滤（messages / history / input / *_result 不作为自定义字段）
 *   - state_schema 类型 → 控件映射
 *   - 简单模式 payload 组装（主输入 + 自定义字段）
 *   - JSON / 数字字段非法时返回 null 并记录错误
 *   - 高级模式沿用裸 JSON 校验
 */
import { beforeEach, describe, expect, it, vi } from 'vitest'
import { nextTick, ref } from 'vue'

import type { StateFieldDTO } from '@/api/workflow'
import {
  buildExecuteFields,
  controlForFieldType,
  isReservedInputKey,
  useExecuteForm,
} from '@/composables/useExecuteForm'

function field(type: string, extra?: Partial<StateFieldDTO>): StateFieldDTO {
  return { type, description: '', reducer: null, ...extra }
}

const SCHEMA: Record<string, StateFieldDTO> = {
  input: field('str'),
  messages: field('list'),
  history: field('list'),
  user_id: field('str', { description: '调用方标识' }),
  max_tokens: field('int'),
  temperature: field('float'),
  verbose: field('bool'),
  payload: field('dict'),
  tags: field('list'),
  ask_result: field('any'),
}

beforeEach(() => {
  vi.clearAllMocks()
})

describe('isReservedInputKey', () => {
  it('messages / history / input 与 *_result 为保留键', () => {
    expect(isReservedInputKey('messages')).toBe(true)
    expect(isReservedInputKey('history')).toBe(true)
    expect(isReservedInputKey('input')).toBe(true)
    expect(isReservedInputKey('ask_result')).toBe(true)
    expect(isReservedInputKey('user_id')).toBe(false)
  })
})

describe('controlForFieldType', () => {
  it('str→text，int/float→number，bool→checkbox，其余→json', () => {
    expect(controlForFieldType('str')).toBe('text')
    expect(controlForFieldType('int')).toBe('number')
    expect(controlForFieldType('float')).toBe('number')
    expect(controlForFieldType('bool')).toBe('checkbox')
    expect(controlForFieldType('list')).toBe('json')
    expect(controlForFieldType('dict')).toBe('json')
    expect(controlForFieldType('object')).toBe('json')
    expect(controlForFieldType('any')).toBe('json')
    expect(controlForFieldType('List[str]')).toBe('json')
  })
})

describe('buildExecuteFields', () => {
  it('排除保留键，按声明顺序返回可编辑字段', () => {
    const fields = buildExecuteFields(SCHEMA)
    expect(fields.map((f) => f.key)).toEqual([
      'user_id',
      'max_tokens',
      'temperature',
      'verbose',
      'payload',
      'tags',
    ])
  })

  it('schema 缺失或为空 → 空数组', () => {
    expect(buildExecuteFields(undefined)).toEqual([])
    expect(buildExecuteFields({})).toEqual([])
  })

  it('字段携带控件类型与描述', () => {
    const fields = buildExecuteFields(SCHEMA)
    const userId = fields.find((f) => f.key === 'user_id')
    expect(userId?.control).toBe('text')
    expect(userId?.description).toBe('调用方标识')
  })
})

describe('useExecuteForm — 简单模式', () => {
  it('初始为简单模式，主输入为空', () => {
    const form = useExecuteForm(ref(SCHEMA))
    expect(form.mode.value).toBe('simple')
    expect(form.primaryInput.value).toBe('')
    expect(form.fields.value.map((f) => f.key)).toContain('user_id')
  })

  it('主输入 + 文本字段 → payload 含 input 与自定义键', () => {
    const form = useExecuteForm(ref(SCHEMA))
    form.primaryInput.value = 'hello'
    form.fieldValues.value = { user_id: 'u-001' }
    expect(form.buildPayload()).toEqual({ input: 'hello', user_id: 'u-001' })
  })

  it('主输入为空白 → 不写 input 键', () => {
    const form = useExecuteForm(ref(SCHEMA))
    form.primaryInput.value = '   '
    expect(form.buildPayload()).toEqual({})
  })

  it('数字字段解析为 number，空值省略', () => {
    const form = useExecuteForm(ref(SCHEMA))
    form.fieldValues.value = { max_tokens: '512', temperature: '0.2', user_id: '' }
    expect(form.buildPayload()).toEqual({ max_tokens: 512, temperature: 0.2 })
  })

  it('布尔字段：无默认值则省略，勾选后写入 true', () => {
    const form = useExecuteForm(ref(SCHEMA))
    expect(form.buildPayload()).toEqual({})
    form.fieldValues.value = { verbose: 'true' }
    expect(form.buildPayload()).toEqual({ verbose: true })
  })

  it('声明了 default 的字段在 reset 后被预填', () => {
    const form = useExecuteForm(ref({ region: field('str', { default: 'cn-north' }) }))
    form.reset()
    expect(form.fieldValues.value.region).toBe('cn-north')
    expect(form.buildPayload()).toEqual({ region: 'cn-north' })
  })

  it('JSON 字段解析为对象/数组', () => {
    const form = useExecuteForm(ref(SCHEMA))
    form.fieldValues.value = { payload: '{"a": 1}', tags: '["x", "y"]' }
    expect(form.buildPayload()).toEqual({ payload: { a: 1 }, tags: ['x', 'y'] })
  })

  it('数字非法 → payload 为 null，fieldErrors 记录该键', () => {
    const form = useExecuteForm(ref(SCHEMA))
    form.fieldValues.value = { max_tokens: 'abc' }
    expect(form.buildPayload()).toBeNull()
    expect(form.fieldErrors.value.max_tokens).toBeTruthy()
  })

  it('JSON 非法 → payload 为 null，fieldErrors 记录该键', () => {
    const form = useExecuteForm(ref(SCHEMA))
    form.fieldValues.value = { payload: '{bad json}' }
    expect(form.buildPayload()).toBeNull()
    expect(form.fieldErrors.value.payload).toBeTruthy()
  })

  it('reset() 清空主输入与字段错误', () => {
    const form = useExecuteForm(ref(SCHEMA))
    form.primaryInput.value = 'hello'
    form.fieldValues.value = { max_tokens: 'abc' }
    form.buildPayload()
    form.reset()
    expect(form.primaryInput.value).toBe('')
    expect(form.fieldErrors.value).toEqual({})
  })

  it('schema 后到达 → 字段列表与默认值随之更新', async () => {
    const schema = ref<Record<string, StateFieldDTO> | undefined>(undefined)
    const form = useExecuteForm(schema)
    expect(form.fields.value).toEqual([])

    schema.value = { city: field('str', { default: 'Beijing' }) }
    await nextTick()

    expect(form.fields.value.map((f) => f.key)).toEqual(['city'])
    expect(form.buildPayload()).toEqual({ city: 'Beijing' })
  })
})

describe('useExecuteForm — 高级模式', () => {
  it('裸 JSON 对象原样作为 payload', () => {
    const form = useExecuteForm(ref(SCHEMA))
    form.mode.value = 'advanced'
    form.advancedText.value = '{"input": "hello", "messages": []}'
    expect(form.buildPayload()).toEqual({ input: 'hello', messages: [] })
  })

  it('非法 JSON → null + jsonError', () => {
    const form = useExecuteForm(ref(SCHEMA))
    form.mode.value = 'advanced'
    form.advancedText.value = '{invalid'
    expect(form.buildPayload()).toBeNull()
    expect(form.jsonError.value).toBeTruthy()
  })

  it('JSON 非对象 → null + jsonError', () => {
    const form = useExecuteForm(ref(SCHEMA))
    form.mode.value = 'advanced'
    form.advancedText.value = '[1, 2]'
    expect(form.buildPayload()).toBeNull()
    expect(form.jsonError.value).toMatch(/对象/)
  })

  it('高级模式忽略主输入与自定义字段', () => {
    const form = useExecuteForm(ref(SCHEMA))
    form.primaryInput.value = 'ignored'
    form.fieldValues.value = { user_id: 'ignored' }
    form.mode.value = 'advanced'
    form.advancedText.value = '{"input": "from-json"}'
    expect(form.buildPayload()).toEqual({ input: 'from-json' })
  })
})
