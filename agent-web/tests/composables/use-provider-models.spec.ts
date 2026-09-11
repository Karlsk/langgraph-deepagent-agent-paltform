// @vitest-environment happy-dom
/**
 * useProviderModels composable 测试（workflow-llm-provider-integration Stage D）。
 *
 * 零真实网络：mock `@/api/provider`。
 * 验证：聚合分组、enabled 双重过滤、provider type → llm_type 映射、
 *       空目录与请求失败的降级、模块级单例共享。
 */
import { beforeEach, describe, expect, it, vi } from 'vitest'

import { PROVIDER_TYPE_TO_LLM_TYPE, useProviderModels } from '@/composables/useProviderModels'
import type { ModelConfigRow, ProviderRow } from '@/api/provider'

const listProvidersMock = vi.fn()
const listAllProviderModelsMock = vi.fn()
vi.mock('@/api/provider', () => ({
  listProviders: () => listProvidersMock(),
  listAllProviderModels: () => listAllProviderModelsMock(),
}))

function makeProvider(name: string, type: ProviderRow['type'], enabled = true): ProviderRow {
  return {
    id: 1,
    name,
    type,
    base_url: '',
    api_key_masked: '****',
    enabled,
    created_by: null,
    created_at: null,
    updated_at: null,
  }
}

function makeModel(providerName: string, name: string, modelId: string, enabled = true): ModelConfigRow {
  return {
    id: 1,
    provider_name: providerName,
    name,
    model_id: modelId,
    ref: `${providerName}/${name}`,
    context_size: null,
    extra_params: {},
    enabled,
    created_by: null,
    created_at: null,
    updated_at: null,
  }
}

beforeEach(() => {
  vi.clearAllMocks()
  listProvidersMock.mockResolvedValue([])
  listAllProviderModelsMock.mockResolvedValue([])
})

describe('PROVIDER_TYPE_TO_LLM_TYPE', () => {
  it('四种 provider type 全部映射到 LLMConfig 的两值域', () => {
    expect(PROVIDER_TYPE_TO_LLM_TYPE.OPENAI).toBe('openai')
    expect(PROVIDER_TYPE_TO_LLM_TYPE.ANTHROPIC).toBe('anthropic')
    // OpenAI 兼容路径：与 build_chat_model 现状一致（所有类型都走 ChatOpenAI）
    expect(PROVIDER_TYPE_TO_LLM_TYPE.OPENAI_COMPATIBLE).toBe('openai')
    expect(PROVIDER_TYPE_TO_LLM_TYPE.OLLAMA).toBe('openai')
  })
})

describe('useProviderModels', () => {
  it('聚合 provider + model 为分组，选项携带 ref/modelId/llmType', async () => {
    listProvidersMock.mockResolvedValue([
      makeProvider('acme', 'OPENAI'),
      makeProvider('claude-co', 'ANTHROPIC'),
    ])
    listAllProviderModelsMock.mockResolvedValue([
      makeModel('acme', 'gpt-4o', 'gpt-4o-2024-08-06'),
      makeModel('claude-co', 'sonnet', 'claude-sonnet-4-5'),
    ])

    const { groups, byRef, refresh } = useProviderModels()
    await refresh()

    expect(groups.value).toHaveLength(2)
    const acme = groups.value.find((g) => g.providerName === 'acme')
    expect(acme?.options).toHaveLength(1)
    expect(acme?.options[0]).toEqual({
      ref: 'acme/gpt-4o',
      label: 'gpt-4o',
      modelId: 'gpt-4o-2024-08-06',
      providerName: 'acme',
      llmType: 'openai',
    })
    expect(byRef.value.get('claude-co/sonnet')?.llmType).toBe('anthropic')
  })

  it('过滤未 enabled 的 model（listAllProviderModels 只过滤了 provider）', async () => {
    listProvidersMock.mockResolvedValue([makeProvider('acme', 'OPENAI')])
    listAllProviderModelsMock.mockResolvedValue([
      makeModel('acme', 'on', 'on-id', true),
      makeModel('acme', 'off', 'off-id', false),
    ])

    const { groups, refresh } = useProviderModels()
    await refresh()

    const labels = groups.value[0]!.options.map((o) => o.label)
    expect(labels).toEqual(['on'])
  })

  it('过滤未 enabled 的 provider，其 model 不出现在任何分组', async () => {
    listProvidersMock.mockResolvedValue([
      makeProvider('live', 'OPENAI', true),
      makeProvider('dead', 'OPENAI', false),
    ])
    listAllProviderModelsMock.mockResolvedValue([
      makeModel('live', 'm1', 'm1-id'),
      makeModel('dead', 'm2', 'm2-id'),
    ])

    const { groups, refresh } = useProviderModels()
    await refresh()

    expect(groups.value.map((g) => g.providerName)).toEqual(['live'])
  })

  it('没有 model 的 provider 不产生空分组', async () => {
    listProvidersMock.mockResolvedValue([
      makeProvider('has-models', 'OPENAI'),
      makeProvider('empty', 'OLLAMA'),
    ])
    listAllProviderModelsMock.mockResolvedValue([makeModel('has-models', 'm1', 'm1-id')])

    const { groups, refresh } = useProviderModels()
    await refresh()

    expect(groups.value.map((g) => g.providerName)).toEqual(['has-models'])
  })

  it('空目录 → groups 为空，不抛错', async () => {
    const { groups, byRef, loading, loaded, refresh } = useProviderModels()
    await refresh()

    expect(groups.value).toEqual([])
    expect(byRef.value.size).toBe(0)
    expect(loading.value).toBe(false)
    expect(loaded.value).toBe(true)
  })

  it('listProviders 失败 → 降级为空目录，不抛错', async () => {
    listProvidersMock.mockRejectedValue(new Error('network down'))
    listAllProviderModelsMock.mockResolvedValue([makeModel('acme', 'm1', 'm1-id')])

    const { groups, loading, loaded, refresh } = useProviderModels()
    await expect(refresh()).resolves.toBeUndefined()

    expect(groups.value).toEqual([])
    expect(loading.value).toBe(false)
    expect(loaded.value).toBe(true)
  })

  it('listAllProviderModels 失败 → 降级为空目录，不抛错', async () => {
    listProvidersMock.mockResolvedValue([makeProvider('acme', 'OPENAI')])
    listAllProviderModelsMock.mockRejectedValue(new Error('network down'))

    const { groups, loaded, refresh } = useProviderModels()
    await expect(refresh()).resolves.toBeUndefined()

    expect(groups.value).toEqual([])
    expect(loaded.value).toBe(true)
  })

  it('byModelId 支持反查；同 model_id 跨 provider 时返回全部命中', async () => {
    listProvidersMock.mockResolvedValue([
      makeProvider('acme', 'OPENAI'),
      makeProvider('claude-co', 'ANTHROPIC'),
    ])
    listAllProviderModelsMock.mockResolvedValue([
      makeModel('acme', 'gpt-4o', 'shared-id'),
      makeModel('claude-co', 'proxy-4o', 'shared-id'),
      makeModel('acme', 'mini', 'unique-id'),
    ])

    const { byModelId, refresh } = useProviderModels()
    await refresh()

    expect(byModelId.value.get('shared-id')).toHaveLength(2)
    expect(byModelId.value.get('shared-id')?.map((o) => o.llmType)).toEqual(['openai', 'anthropic'])
    expect(byModelId.value.get('unique-id')?.[0]?.ref).toBe('acme/mini')
    expect(byModelId.value.get('missing')).toBeUndefined()
  })

  it('模块级单例：两个调用方共享同一份 groups', async () => {
    listProvidersMock.mockResolvedValue([makeProvider('acme', 'OPENAI')])
    listAllProviderModelsMock.mockResolvedValue([makeModel('acme', 'm1', 'm1-id')])

    const first = useProviderModels()
    await first.refresh()
    const second = useProviderModels()

    expect(second.groups.value).toBe(first.groups.value)
    expect(second.loaded.value).toBe(true)
  })

  it('load() 已加载则不重复请求，force 可强制刷新', async () => {
    listProvidersMock.mockResolvedValue([makeProvider('acme', 'OPENAI')])
    listAllProviderModelsMock.mockResolvedValue([makeModel('acme', 'm1', 'm1-id')])

    const { load, refresh } = useProviderModels()
    await refresh()
    listProvidersMock.mockClear()

    await load()
    expect(listProvidersMock).not.toHaveBeenCalled()

    await load(true)
    expect(listProvidersMock).toHaveBeenCalledTimes(1)
  })
})
