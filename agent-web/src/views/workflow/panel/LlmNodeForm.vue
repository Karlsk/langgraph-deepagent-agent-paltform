<script setup lang="ts">
import { reactive, watch } from 'vue'

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

const formModel = reactive<Record<string, unknown>>({
  llm_type: props.config.llm_type ?? 'openai',
  model_name: props.config.model_name ?? 'gpt-4o-mini',
  temperature: props.config.temperature ?? 0.7,
  system_prompt: props.config.system_prompt ?? '',
})

watch(
  () => props.config,
  (newConfig) => {
    formModel.llm_type = newConfig.llm_type ?? 'openai'
    formModel.model_name = newConfig.model_name ?? 'gpt-4o-mini'
    formModel.temperature = newConfig.temperature ?? 0.7
    formModel.system_prompt = newConfig.system_prompt ?? ''
  },
  { deep: true },
)

function onFieldChange() {
  emit('update:config', { ...formModel })
}

const llmTypeOptions = [
  { value: 'openai', label: 'OpenAI' },
  { value: 'anthropic', label: 'Anthropic' },
]
</script>

<template>
  <div class="llm-node-form">
    <el-form label-position="top" :disabled="props.readonly">
      <el-form-item label="LLM 类型" prop="llm_type">
        <el-select v-model="formModel.llm_type" @change="onFieldChange">
          <el-option
            v-for="opt in llmTypeOptions"
            :key="opt.value"
            :value="opt.value"
            :label="opt.label"
          />
        </el-select>
      </el-form-item>

      <el-form-item label="模型名称" prop="model_name">
        <el-input v-model="formModel.model_name" placeholder="gpt-4o-mini" @change="onFieldChange" />
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
</style>
