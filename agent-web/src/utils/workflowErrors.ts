/**
 * workflowErrors — 统一错误映射工具（spec-21）
 * 将本地校验错误（GraphValidationError[]）和后端 422 信封错误
 * 统一映射为 FieldError[]，供视图层 inline 展示。
 */
import type { GraphValidationError } from '@/composables/useWorkflowGraph'

export interface FieldError {
  field: string
  message: string
  nodeId?: string
  edgeId?: string
}

export function toFieldErrors(source: GraphValidationError[] | unknown): FieldError[] {
  if (Array.isArray(source)) {
    return mapLocalErrors(source)
  }
  return mapHttp422(source)
}

const EXECUTE_FALLBACK = '执行失败，请稍后重试'

/**
 * 执行失败摘要：信封 message / 旧 detail 优先，其次非 axios Error 的 message。
 * axios 自身的 "Request failed with status code 500" 对用户无信息量，直接走 fallback。
 */
export function toExecuteErrorMessage(error: unknown, fallback: string = EXECUTE_FALLBACK): string {
  if (isAxiosError(error)) {
    const data = (error as AxiosErrorLike).response?.data as Record<string, unknown> | undefined
    return (data ? extractMessage(data) : null) ?? fallback
  }
  if (error instanceof Error && error.message) {
    return error.message
  }
  return fallback
}

function mapLocalErrors(errors: GraphValidationError[]): FieldError[] {
  return errors.map((e) => {
    const field: FieldError = { field: e.field, message: e.message }
    if (e.nodeId) field.nodeId = e.nodeId
    if (e.edgeId) field.edgeId = e.edgeId
    return field
  })
}

function mapHttp422(error: unknown): FieldError[] {
  if (!isAxiosError(error)) return []
  const status = (error as AxiosErrorLike).response?.status
  if (status !== 422) return []

  const data = (error as AxiosErrorLike).response?.data as Record<string, unknown> | undefined
  if (!data) return []

  const message = extractMessage(data)
  if (!message) return []

  const field = detectField(message)
  const result: FieldError = { field, message }

  const nodeId = extractNodeName(message)
  if (nodeId) result.nodeId = nodeId

  return [result]
}

interface AxiosErrorLike {
  isAxiosError: boolean
  response?: { status: number; data: unknown }
}

function isAxiosError(error: unknown): error is AxiosErrorLike {
  return (
    typeof error === 'object' &&
    error !== null &&
    (error as AxiosErrorLike).isAxiosError === true
  )
}

function extractMessage(data: Record<string, unknown>): string | null {
  if (typeof data.message === 'string' && data.message.length > 0) {
    return data.message
  }
  if (typeof data.detail === 'string' && data.detail.length > 0) {
    return data.detail
  }
  return null
}

function detectField(message: string): string {
  if (/entry_point/i.test(message)) return 'entry_point'
  if (/edge/i.test(message)) return 'edges'
  return 'server'
}

function extractNodeName(message: string): string | null {
  const match = message.match(/[Nn]ode['"]?\s+['"]?(\w+)['"]?/)
  return match?.[1] ?? null
}
