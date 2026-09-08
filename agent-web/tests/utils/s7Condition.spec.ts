/**
 * S7 条件解析器 + 生成器 + stateChannels 推导工具测试
 * 纯函数测试，无 DOM 依赖
 */
import { describe, expect, it } from 'vitest'
import {
  isValidS7Condition,
  isValidPath,
  escapeLiteral,
  unescapeLiteral,
  generateEqualityCondition,
  generateTruthyCondition,
  deriveStateChannels,
} from '@/utils/s7Condition'

describe('isValidS7Condition', () => {
  it('接受等值条件 "check_result.response == \'OK\'"', () => {
    expect(isValidS7Condition("check_result.response == 'OK'")).toBe(true)
  })

  it('接受真值条件 "result.ready"', () => {
    expect(isValidS7Condition('result.ready')).toBe(true)
  })

  it('接受最小等值条件 "a == \'b\'"', () => {
    expect(isValidS7Condition("a == 'b'")).toBe(true)
  })

  it('接受单字符真值 "x"', () => {
    expect(isValidS7Condition('x')).toBe(true)
  })

  it('拒绝布尔运算 "a or b"', () => {
    expect(isValidS7Condition('a or b')).toBe(false)
  })

  it('拒绝不完整等值 "a.b == "', () => {
    expect(isValidS7Condition('a.b == ')).toBe(false)
  })

  it('拒绝空字符串', () => {
    expect(isValidS7Condition('')).toBe(false)
  })

  it('拒绝数字开头 "123abc"', () => {
    expect(isValidS7Condition('123abc')).toBe(false)
  })

  it('拒绝双点路径 "a..b"', () => {
    expect(isValidS7Condition('a..b')).toBe(false)
  })

  it('拒绝未闭合引号 "a == \'unclosed"', () => {
    expect(isValidS7Condition("a == 'unclosed")).toBe(false)
  })

  it('接受带空格的等值条件（trim 后合法）', () => {
    expect(isValidS7Condition("  a == 'b'  ")).toBe(true)
  })
})

describe('isValidPath', () => {
  it('接受简单标识符 "result"', () => {
    expect(isValidPath('result')).toBe(true)
  })

  it('接受点分隔路径 "check_result.response"', () => {
    expect(isValidPath('check_result.response')).toBe(true)
  })

  it('接受多层路径 "a.b.c.d"', () => {
    expect(isValidPath('a.b.c.d')).toBe(true)
  })

  it('拒绝空字符串', () => {
    expect(isValidPath('')).toBe(false)
  })

  it('拒绝数字开头 "123"', () => {
    expect(isValidPath('123')).toBe(false)
  })

  it('拒绝尾点 "a."', () => {
    expect(isValidPath('a.')).toBe(false)
  })

  it('拒绝首点 ".a"', () => {
    expect(isValidPath('.a')).toBe(false)
  })

  it('拒绝双点 "a..b"', () => {
    expect(isValidPath('a..b')).toBe(false)
  })

  it('拒绝含空格 "a b"', () => {
    expect(isValidPath('a b')).toBe(false)
  })

  it('拒绝含连字符 "a-b"', () => {
    expect(isValidPath('a-b')).toBe(false)
  })

  it('拒绝含运算符 "a==b"', () => {
    expect(isValidPath('a==b')).toBe(false)
  })
})

describe('escapeLiteral / unescapeLiteral', () => {
  it('escapeLiteral("hello") === "hello"', () => {
    expect(escapeLiteral('hello')).toBe('hello')
  })

  it('escapeLiteral("it\'s") === "it\'\'s"（SQL 风格）', () => {
    expect(escapeLiteral("it's")).toBe("it''s")
  })

  it('escapeLiteral("a\'\'b") === "a\'\'\'\'b"', () => {
    expect(escapeLiteral("a''b")).toBe("a''''b")
  })

  it('escapeLiteral("") === ""', () => {
    expect(escapeLiteral('')).toBe('')
  })

  it('unescapeLiteral(escapeLiteral(x)) === x（往返）', () => {
    const original = "it's a test"
    expect(unescapeLiteral(escapeLiteral(original))).toBe(original)
  })

  it('unescapeLiteral("it\'\'s") === "it\'s"', () => {
    expect(unescapeLiteral("it''s")).toBe("it's")
  })
})

describe('generateEqualityCondition', () => {
  it('生成 "a.b == \'OK\'"', () => {
    expect(generateEqualityCondition('a.b', 'OK')).toBe("a.b == 'OK'")
  })

  it('生成 "check_result.response == \'OK\'"', () => {
    expect(generateEqualityCondition('check_result.response', 'OK')).toBe(
      "check_result.response == 'OK'",
    )
  })

  it('literal 含单引号 → 抛出错误', () => {
    expect(() => generateEqualityCondition('a', "it's")).toThrow(
      /cannot contain single quotes/i,
    )
  })

  it('invalid path → 抛出错误', () => {
    expect(() => generateEqualityCondition('a..b', 'OK')).toThrow(/invalid path/i)
  })

  it('property: 生成的 condition 总是通过 isValidS7Condition', () => {
    const paths = ['a', 'result.ready', 'check_result.response', 'x.y.z']
    const literals = ['OK', 'true', '123', 'test_value']

    for (const path of paths) {
      for (const literal of literals) {
        const condition = generateEqualityCondition(path, literal)
        expect(isValidS7Condition(condition)).toBe(true)
      }
    }
  })
})

describe('generateTruthyCondition', () => {
  it('生成 "result.ready"', () => {
    expect(generateTruthyCondition('result.ready')).toBe('result.ready')
  })

  it('invalid path → 抛出错误', () => {
    expect(() => generateTruthyCondition('a..b')).toThrow(/invalid path/i)
  })

  it('property: 生成的 condition 总是通过 isValidS7Condition', () => {
    const paths = ['a', 'result.ready', 'check_result.response', 'x.y.z']

    for (const path of paths) {
      const condition = generateTruthyCondition(path)
      expect(isValidS7Condition(condition)).toBe(true)
    }
  })
})

describe('deriveStateChannels', () => {
  it('state_schema={input, messages}, nodes=[check, notify] → 包含所有通道', () => {
    const stateSchema = {
      input: { type: 'str' },
      messages: { type: 'list' },
    }
    const nodeNames = ['check', 'notify']

    const channels = deriveStateChannels(stateSchema, nodeNames)

    expect(channels).toContain('input')
    expect(channels).toContain('messages')
    expect(channels).toContain('history')
    expect(channels).toContain('check_result')
    expect(channels).toContain('notify_result')
  })

  it('state_schema 已有 history → 不重复', () => {
    const stateSchema = {
      history: { type: 'list' },
      input: { type: 'str' },
    }
    const nodeNames: string[] = []

    const channels = deriveStateChannels(stateSchema, nodeNames)

    const historyCount = channels.filter((c) => c === 'history').length
    expect(historyCount).toBe(1)
  })

  it('state_schema 已有 check_result → 不重复', () => {
    const stateSchema = {
      check_result: { type: 'any' },
    }
    const nodeNames = ['check']

    const channels = deriveStateChannels(stateSchema, nodeNames)

    const checkResultCount = channels.filter((c) => c === 'check_result').length
    expect(checkResultCount).toBe(1)
  })

  it('空 state_schema, 空 nodes → ["history"]', () => {
    const channels = deriveStateChannels({}, [])
    expect(channels).toEqual(['history'])
  })

  it('空 state_schema, nodes=[a] → ["history", "a_result"]', () => {
    const channels = deriveStateChannels({}, ['a'])
    expect(channels).toContain('history')
    expect(channels).toContain('a_result')
  })
})
