/**
 * 密码强度工具单元测试（task-024）。
 *
 * 覆盖 validatePasswordStrength 的 5 类校验分支、合法密码、长度边界，
 * 与后端 UserCreate.validate_password 字段语义对齐。
 */
import { describe, expect, it } from 'vitest'

import { validatePasswordStrength } from '@/utils/password'

describe('validatePasswordStrength 密码强度校验', () => {
  it('长度不足 8 位：返回 "密码至少 8 位"', () => {
    expect(validatePasswordStrength('Aa1!')).toBe('密码至少 8 位')
  })

  it('缺大写字母：返回 "密码需包含大写字母"', () => {
    expect(validatePasswordStrength('abcdefg1!')).toBe('密码需包含大写字母')
  })

  it('缺小写字母：返回 "密码需包含小写字母"', () => {
    expect(validatePasswordStrength('ABCDEFG1!')).toBe('密码需包含小写字母')
  })

  it('缺数字：返回 "密码需包含数字"', () => {
    expect(validatePasswordStrength('Abcdefgh!')).toBe('密码需包含数字')
  })

  it('缺特殊字符：返回 "密码需包含特殊字符"', () => {
    expect(validatePasswordStrength('Abcdefg1')).toBe('密码需包含特殊字符')
  })

  it('合法密码（包含全部 5 类）：返回 null', () => {
    expect(validatePasswordStrength('Abcdefg1!')).toBeNull()
  })

  it('长度边界 7（不通过）', () => {
    expect(validatePasswordStrength('Ab1!xyz')).toBe('密码至少 8 位')
  })

  it('长度边界 8（通过）', () => {
    expect(validatePasswordStrength('Ab1!xyzw')).toBeNull()
  })

  it('长度边界 9（通过）', () => {
    expect(validatePasswordStrength('Ab1!xyzwa')).toBeNull()
  })
})