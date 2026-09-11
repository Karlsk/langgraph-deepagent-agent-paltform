/**
 * useProviderModels composable（workflow-llm-provider-integration Stage D）。
 *
 * 为工作流设计器的 LLM 节点表单提供「按 provider 分组的模型下拉」数据源。
 *
 * 为什么需要它：LLM 节点的 `model_name` 曾是自由文本，既不校验模型是否存在，
 * 运行时也用不上「模型管理」页配置的 provider。现在下拉选项直接来自 provider
 * 系统，选中后写入 `provider_ref`，后端据此解析真实端点与凭据（CONTRACT S20）。
 *
 * 关键映射（后端 `LLMConfig` 是 `extra="forbid"` 的冻结契约，只认这些键）：
 * - `ModelConfigRow.ref`        → `config.provider_ref`
 * - `ModelConfigRow.model_id`   → `config.model_name`（发给供应商 API 的标识，**不是** display name）
 * - `ProviderRow.type`          → `config.llm_type`（大写四值域收窄为小写两值域）
 *
 * 模块单例模式（对齐 `useWorkflowCapabilities`）：LlmNodeForm 随节点选中反复挂载，
 * 模块级 ref 避免每次挂载都重发 N+1 请求。
 */
import { ref, type Ref } from 'vue'

import { listAllProviderModels, listProviders } from '@/api/provider'
import type { ProviderType } from '@/api/provider'

/** LLMConfig.llm_type 的合法值域（CONTRACT §4.7 冻结为 Literal 两值） */
export type LlmType = 'openai' | 'anthropic'

/**
 * provider type → llm_type 映射。
 *
 * `OPENAI_COMPATIBLE` 与 `OLLAMA` 都归到 `openai`：后端 `build_chat_model`
 * 本就把所有类型走 OpenAI 兼容路径（llm_store.py 注明 type 只区分 auth_config
 * 形态与 UI），且走 provider_ref 时 llm_type 不参与客户端选择，仅作展示与
 * env 回退用途，故此映射安全。
 */
export const PROVIDER_TYPE_TO_LLM_TYPE: Record<ProviderType, LlmType> = {
  OPENAI: 'openai',
  ANTHROPIC: 'anthropic',
  OPENAI_COMPATIBLE: 'openai',
  OLLAMA: 'openai',
}

/** 下拉选项：一个 provider 下的一个已启用模型 */
export interface ProviderModelOption {
  /** 选项 value，写入 config.provider_ref */
  ref: string
  /** 展示名（ModelConfig.name） */
  label: string
  /** 发给供应商的模型标识，写入 config.model_name */
  modelId: string
  providerName: string
  llmType: LlmType
}

/** el-option-group 分组：一个 provider 及其全部可用模型 */
export interface ProviderModelGroup {
  providerName: string
  options: ProviderModelOption[]
}

const groups: Ref<ProviderModelGroup[]> = ref([])
const byRef: Ref<Map<string, ProviderModelOption>> = ref(new Map())
/**
 * `model_id` → 全部命中的选项。
 *
 * 用数组而非单值：`model_id` 只在 provider 内唯一，两个 provider 可以都提供
 * `gpt-4o`。调用方按 `llm_type` 消歧。用于编辑「早于 provider_ref 存在」的
 * 工作流时，从 `model_name` 反查下拉选中项。
 */
const byModelId: Ref<Map<string, ProviderModelOption[]>> = ref(new Map())
const loading = ref(false)
const loaded = ref(false)

export function useProviderModels() {
  async function refresh(): Promise<void> {
    loading.value = true
    try {
      // listAllProviderModels 只过滤了 provider 的 enabled，model 的 enabled 需在此补过滤
      const [providers, models] = await Promise.all([listProviders(), listAllProviderModels()])
      const typeByName = new Map<string, ProviderType>()
      for (const provider of providers) {
        if (provider.enabled) typeByName.set(provider.name, provider.type)
      }

      const grouped = new Map<string, ProviderModelOption[]>()
      const index = new Map<string, ProviderModelOption>()
      const modelIndex = new Map<string, ProviderModelOption[]>()
      for (const model of models) {
        const providerType = typeByName.get(model.provider_name)
        // 未启用的 provider 或未启用的 model 都不进选项
        if (!model.enabled || providerType === undefined) continue
        const option: ProviderModelOption = {
          ref: model.ref,
          label: model.name,
          modelId: model.model_id,
          providerName: model.provider_name,
          llmType: PROVIDER_TYPE_TO_LLM_TYPE[providerType],
        }
        const bucket = grouped.get(model.provider_name)
        if (bucket) bucket.push(option)
        else grouped.set(model.provider_name, [option])
        index.set(option.ref, option)
        const modelBucket = modelIndex.get(option.modelId)
        if (modelBucket) modelBucket.push(option)
        else modelIndex.set(option.modelId, [option])
      }

      groups.value = [...grouped.entries()].map(([providerName, options]) => ({ providerName, options }))
      byRef.value = index
      byModelId.value = modelIndex
    } catch {
      // 保守降级：目录为空时表单回落到手填路径，不抛错、不白屏
      groups.value = []
      byRef.value = new Map()
      byModelId.value = new Map()
    } finally {
      loading.value = false
      loaded.value = true
    }
  }

  /**
   * 幂等加载：已加载过则直接返回，避免节点间来回切换时重复请求。
   *
   * @param force 传 true 强制重新拉取（等价 refresh）
   */
  async function load(force = false): Promise<void> {
    if (loaded.value && !force) return
    await refresh()
  }

  return { groups, byRef, byModelId, loading, loaded, refresh, load }
}
