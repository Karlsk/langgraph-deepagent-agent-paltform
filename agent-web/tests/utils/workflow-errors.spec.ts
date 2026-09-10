/**
 * workflowErrors — 统一错误映射工具测试
 * 纯函数测试，无 DOM 依赖
 */
import { describe, expect, it } from 'vitest'
import { toFieldErrors, type FieldError } from '@/utils/workflowErrors'
import type { GraphValidationError } from '@/composables/useWorkflowGraph'

function makeAxiosError(status: number, data: unknown): Error {
  const error = new Error('Request failed') as Error & {
    isAxiosError: boolean
    response?: { status: number; data: unknown }
  }
  error.isAxiosError = true
  error.response = { status, data }
  return error
}

describe('toFieldErrors — 本地校验错误（GraphValidationError[]）', () => {
  it('空数组 → 空结果', () => {
    expect(toFieldErrors([])).toEqual([])
  })

  it('节点错误含 nodeId → FieldError 含 nodeId', () => {
    const errors: GraphValidationError[] = [
      { field: 'nodes', message: 'Duplicate node name: "llm_1".', nodeId: 'llm_1' },
    ]
    const result = toFieldErrors(errors)
    expect(result).toEqual([
      { field: 'nodes', message: 'Duplicate node name: "llm_1".', nodeId: 'llm_1' },
    ] satisfies FieldError[])
  })

  it('边错误含 edgeId → FieldError 含 edgeId', () => {
    const errors: GraphValidationError[] = [
      { field: 'edges', message: 'Edge source "x" is not valid.', edgeId: 'x-y' },
    ]
    const result = toFieldErrors(errors)
    expect(result).toEqual([
      { field: 'edges', message: 'Edge source "x" is not valid.', edgeId: 'x-y' },
    ] satisfies FieldError[])
  })

  it('全局错误（无 nodeId/edgeId）→ FieldError 仅 field + message', () => {
    const errors: GraphValidationError[] = [
      { field: 'entry_point', message: 'entry_point "foo" does not match any node.' },
    ]
    const result = toFieldErrors(errors)
    expect(result).toEqual([
      { field: 'entry_point', message: 'entry_point "foo" does not match any node.' },
    ] satisfies FieldError[])
  })

  it('多个错误 → 多个 FieldError', () => {
    const errors: GraphValidationError[] = [
      { field: 'nodes', message: 'Duplicate node name: "llm_1".', nodeId: 'llm_1' },
      { field: 'entry_point', message: 'entry_point "x" does not match.' },
    ]
    const result = toFieldErrors(errors)
    expect(result).toHaveLength(2)
    expect(result[0].nodeId).toBe('llm_1')
    expect(result[1].field).toBe('entry_point')
  })
})

describe('toFieldErrors — HTTP 422 错误（axios error + 信封格式）', () => {
  it('信封格式 422 → 提取 message 并尝试匹配节点名', () => {
    const error = makeAxiosError(422, {
      code: 422,
      message: "invalid definition: Node 'llm_1' has invalid config",
      data: null,
    })
    const result = toFieldErrors(error)
    expect(result).toHaveLength(1)
    expect(result[0].message).toContain("Node 'llm_1'")
    expect(result[0].nodeId).toBe('llm_1')
  })

  it('422 含 entry_point 关键词 → field 为 entry_point', () => {
    const error = makeAxiosError(422, {
      code: 422,
      message: 'invalid definition: entry_point "foo" does not match any node',
      data: null,
    })
    const result = toFieldErrors(error)
    expect(result).toHaveLength(1)
    expect(result[0].field).toBe('entry_point')
  })

  it('422 含 edge 关键词 → field 为 edges', () => {
    const error = makeAxiosError(422, {
      code: 422,
      message: 'build-time error: Edge source "x" is dangling',
      data: null,
    })
    const result = toFieldErrors(error)
    expect(result).toHaveLength(1)
    expect(result[0].field).toBe('edges')
  })

  it('422 无特定关键词 → field 为 server', () => {
    const error = makeAxiosError(422, {
      code: 422,
      message: 'SSRF guard: blocked internal URL',
      data: null,
    })
    const result = toFieldErrors(error)
    expect(result).toHaveLength(1)
    expect(result[0].field).toBe('server')
    expect(result[0].message).toContain('SSRF guard')
  })

  it('FastAPI detail 格式（非信封）→ 提取 detail 字符串', () => {
    const error = makeAxiosError(422, {
      detail: "Node 'http_1' config error",
    })
    const result = toFieldErrors(error)
    expect(result).toHaveLength(1)
    expect(result[0].message).toContain("Node 'http_1'")
    expect(result[0].nodeId).toBe('http_1')
  })
})

describe('toFieldErrors — 非 422 / 非 axios 错误', () => {
  it('403 错误 → 空数组（由拦截器 toast 处理）', () => {
    const error = makeAxiosError(403, {
      code: 403,
      message: 'workflow write requires admin',
      data: null,
    })
    expect(toFieldErrors(error)).toEqual([])
  })

  it('500 错误 → 空数组', () => {
    const error = makeAxiosError(500, {
      code: 500,
      message: 'internal server error',
      data: null,
    })
    expect(toFieldErrors(error)).toEqual([])
  })

  it('非 axios 错误 → 空数组', () => {
    expect(toFieldErrors(new Error('random'))).toEqual([])
    expect(toFieldErrors('string error')).toEqual([])
    expect(toFieldErrors(null)).toEqual([])
    expect(toFieldErrors(undefined)).toEqual([])
  })

  it('axios 错误但无 response → 空数组', () => {
    const error = new Error('network error') as Error & { isAxiosError: boolean }
    error.isAxiosError = true
    expect(toFieldErrors(error)).toEqual([])
  })
})
