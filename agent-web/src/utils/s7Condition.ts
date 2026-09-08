import type { StateFieldDTO } from '@/api/workflow'

const S7_PATH_RE = /^[A-Za-z_][A-Za-z0-9_]*(\.[A-Za-z_][A-Za-z0-9_]*)*$/
const S7_EQUALITY_RE = /^[A-Za-z_][A-Za-z0-9_.]*\s*==\s*'((?:''|[^'])*)'$/

export function isValidS7Condition(condition: string): boolean {
  const trimmed = condition.trim()
  return S7_EQUALITY_RE.test(trimmed) || S7_PATH_RE.test(trimmed)
}

export function isValidPath(path: string): boolean {
  return S7_PATH_RE.test(path)
}

export function escapeLiteral(literal: string): string {
  return literal.replace(/'/g, "''")
}

export function unescapeLiteral(escaped: string): string {
  return escaped.replace(/''/g, "'")
}

export function generateEqualityCondition(path: string, literal: string): string {
  if (!isValidPath(path)) {
    throw new Error(`Invalid path: ${path}`)
  }
  if (literal.includes("'")) {
    throw new Error(`Literal cannot contain single quotes: ${literal}`)
  }
  return `${path} == '${literal}'`
}

export function generateTruthyCondition(path: string): string {
  if (!isValidPath(path)) {
    throw new Error(`Invalid path: ${path}`)
  }
  return path
}

export function deriveStateChannels(
  stateSchema: Record<string, StateFieldDTO>,
  nodeNames: string[],
): string[] {
  const channels = new Set<string>()

  for (const key of Object.keys(stateSchema)) {
    channels.add(key)
  }

  if (!channels.has('history')) {
    channels.add('history')
  }

  for (const nodeName of nodeNames) {
    const resultKey = `${nodeName}_result`
    if (!channels.has(resultKey)) {
      channels.add(resultKey)
    }
  }

  return Array.from(channels)
}
