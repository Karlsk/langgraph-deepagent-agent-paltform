<script setup lang="ts">
/**
 * LLM 节点配置表单（spec-12，2026-09-11 接入 provider 体系）。
 *
 * 模型不再是自由文本：下拉选项来自 provider 系统，按 provider 分组。选中后写入
 * `provider_ref`，后端据此从 provider 表解析真实端点与凭据（CONTRACT S20）。
 *
 * 键映射（`LLMConfig` 是 extra="forbid" 的冻结契约，只能写这些键）：
 * - `ModelConfigRow.ref`      → `provider_ref`
 * - `ModelConfigRow.model_id` → `model_name`（发给供应商的标识，非展示名）
 * - `ProviderRow.type`        → `llm_type`（大写四值域收窄为小写两值域）
 *
 * 保留 `allow-create` 手填路径：裸模型名 → `provider_ref` 置空，运行时走
 * `OPENAI_API_KEY` / `OPENAI_BASE_URL` 环境变量。这样既有 YAML 与纯 env 部署不受影响。
 *
 * H6 红线：本表单绝不提供 api_key 字段，config 不承载任何密钥。
 */
import { computed, onMounted, reactive, watch } from 'vue'

import { useProviderModels } from '@/composables/useProviderModels'

interface Props {
  config: Record<string, unknown>
  readonly?: boolean
}

const props = withDefaults(defineProps<Props>(), {
  readonly: false,
})

const emit = defineEmits<{
  'update:config': [config: Record<string, unknown>]
}>()

const { groups, byRef, byModelId, loading, load } = useProviderModels()

const formModel = reactive<Record<string, unknown>>({
  llm_type: props.config.llm_type ?? 'openai',
  model_name: props.config.model_name ?? 'gpt-4o-mini',
  provider_ref: props.config.provider_ref ?? null,
  temperature: props.config.temperature ?? 0.7,
  system_prompt: props.config.system_prompt ?? '',
})

watch(
  () => props.config,
  (newConfig) => {
    formModel.llm_type = newConfig.llm_type ?? 'openai'
    formModel.model_name = newConfig.model_name ?? 'gpt-4o-mini'
    formModel.provider_ref = newConfig.provider_ref ?? null
    formModel.temperature = newConfig.temperature ?? 0.7
    formModel.system_prompt = newConfig.system_prompt ?? ''
  },
  { deep: true },
)

function onFieldChange() {
  // 先铺 props.config 再覆盖表单键：避免丢掉表单不认识的既有键
  // （如 nodeCatalog 注入的 max_retries / retry_base_delay）
  emit('update:config', { ...props.config, ...formModel })
}

const hasProviderRef = computed(
  () => typeof formModel.provider_ref === 'string' && formModel.provider_ref.length > 0,
)

/**
 * 模型下拉的绑定值。
 *
 * getter 三级回填：provider_ref 命中 → 用 model_name 反查 model_id → 都失败则原样
 * 显示 model_name（历史值/手填值不丢）。反查有多个 provider 提供同一 model_id 时，
 * 按 llm_type 消歧。
 * setter 区分「选中目录项」与「手填裸名」两条路径。
 */
const selectedModel = computed<string>({
  get() {
    const ref = formModel.provider_ref
    if (typeof ref === 'string' && ref) return ref
    const modelName = typeof formModel.model_name === 'string' ? formModel.model_name : ''
    if (!modelName) return ''
    const candidates = byModelId.value.get(modelName)
    if (candidates && candidates.length > 0) {
      const match = candidates.find((option) => option.llmType === formModel.llm_type) ?? candidates[0]!
      return match.ref
    }
    return modelName
  },
  set(value: string) {
    const option = byRef.value.get(value)
    if (option) {
      formModel.provider_ref = option.ref
      formModel.model_name = option.modelId
      formModel.llm_type = option.llmType
    } else {
      // 手填（allow-create）或清空：不落 provider_ref，运行时走 env 路径
      formModel.provider_ref = null
      formModel.model_name = value ?? ''
    }
    onFieldChange()
  },
})

const llmTypeOptions = [
  { value: 'openai', label: 'OpenAI' },
  { value: 'anthropic', label: 'Anthropic' },
]

onMounted(() => {
  void load()
})
</script>

<template>
  <div class="llm-node-form">
    <el-form label-position="top" :disabled="props.readonly">
      <el-form-item label="模型" prop="model_name">
        <el-select
          v-model="selectedModel"
          data-testid="model-select"
          filterable
          clearable
          allow-create
          default-first-option
          :loading="loading"
          no-data-text="暂无可用模型（请先在 模型管理 页启用 provider 并添加 model）"
          placeholder="选择 provider 下的模型，或输入模型名走环境变量"
          class="llm-node-form__model-select"
        >
          <el-option-group v-for="group in groups" :key="group.providerName" :label="group.providerName">
            <el-option v-for="option in group.options" :key="option.ref" :value="option.ref" :label="option.label">
              <span class="llm-node-form__option-name">{{ option.label }}</span>
              <span class="llm-node-form__option-id">{{ option.modelId }}</span>
            </el-option>
          </el-option-group>
        </el-select>
        <div class="llm-node-form__hint">
          选中 provider 模型后，运行时使用该 provider 的端点与凭据；手填模型名则走
          OPENAI_API_KEY / OPENAI_BASE_URL 环境变量。
        </div>
      </el-form-item>

      <el-form-item label="LLM 类型" prop="llm_type">
        <el-select
          v-model="formModel.llm_type"
          data-testid="llm-type-select"
          :disabled="props.readonly || hasProviderRef"
          @change="onFieldChange"
        >
          <el-option v-for="opt in llmTypeOptions" :key="opt.value" :value="opt.value" :label="opt.label" />
        </el-select>
        <div v-if="hasProviderRef" class="llm-node-form__hint">由所选 provider 决定，不可单独修改。</div>
      </el-form-item>

      <el-form-item label="Temperature" prop="temperature">
        <el-input-number
          v-model="formModel.temperature"
          :min="0"
          :max="2"
          :step="0.1"
          :precision="1"
          @change="onFieldChange"
        />
      </el-form-item>

      <el-form-item label="System Prompt" prop="system_prompt">
        <el-input
          v-model="formModel.system_prompt"
          type="textarea"
          :rows="4"
          placeholder="You are a helpful assistant."
          @change="onFieldChange"
        />
      </el-form-item>
    </el-form>
  </div>
</template>

<style scoped>
.llm-node-form {
  display: flex;
  flex-direction: column;
  gap: 16px;
}

.llm-node-form__model-select {
  width: 100%;
}

.llm-node-form__hint {
  font-size: 12px;
  line-height: 1.4;
  margin-top: 4px;
  color: var(--color-text-tertiary);
}

/* el-select option 自定义渲染：左侧模型名、右侧 model_id */
.llm-node-form__option-name {
  font-family: var(--app-font-mono, monospace);
}

.llm-node-form__option-id {
  float: right;
  color: var(--color-text-tertiary);
  font-size: 12px;
}
</style>
