/**
 * 密码强度工具（task-024）：
 * 与后端 `UserCreate.validate_password` 字段语义对齐。
 *
 * 校验规则：
 * - 长度：8 ≤ len ≤ 64（后端 PasswordStr 64 上限，超出仍报错，保持前端体验一致）
 * - 必须包含：大写字母 / 小写字母 / 数字 / 特殊符号 各至少一个
 *
 * 返回首个不通过原因（中文字面量，与前端 notifyError 文案一致）；
 * 通过则返回 null。
 *
 * 设计动机：避免 422 校验往返；前端预校验失败立即 notifyError 返回，
 * 由用户修正后再发注册请求。
 */

/** 8-64 位，与后端 PasswordStr 上限对齐；64 上限防 DoS（极端长字符串 hash）。 */
const MIN_LENGTH = 8
const MAX_LENGTH = 64

/**
 * 校验密码强度。返回首个不通过原因字符串；通过则返回 null。
 *
 * @example
 * validatePasswordStrength('Ab1!xyzwa') // => null
 * validatePasswordStrength('Aa1!')      // => '密码至少 8 位'
 */
export function validatePasswordStrength(pwd: string): string | null {
  if (pwd.length < MIN_LENGTH) {
    return '密码至少 8 位'
  }
  if (pwd.length > MAX_LENGTH) {
    return `密码不能超过 ${MAX_LENGTH} 位`
  }
  if (!/[A-Z]/.test(pwd)) {
    return '密码需包含大写字母'
  }
  if (!/[a-z]/.test(pwd)) {
    return '密码需包含小写字母'
  }
  if (!/[0-9]/.test(pwd)) {
    return '密码需包含数字'
  }
  if (!/[^A-Za-z0-9]/.test(pwd)) {
    return '密码需包含特殊字符'
  }
  return null
}
