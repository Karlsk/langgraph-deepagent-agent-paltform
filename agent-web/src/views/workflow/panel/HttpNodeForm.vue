<script setup lang="ts">
import { computed, reactive, watch } from 'vue'

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
  url: props.config.url ?? '',
  method: props.config.method ?? 'POST',
  body_template: props.config.body_template ?? '',
  response_path: props.config.response_path ?? null,
  mock_enabled: props.config.mock_enabled ?? false,
  mock_responses: props.config.mock_responses ?? {},
})

watch(
  () => props.config,
  (newConfig) => {
    formModel.url = newConfig.url ?? ''
    formModel.method = newConfig.method ?? 'POST'
    formModel.body_template = newConfig.body_template ?? ''
    formModel.response_path = newConfig.response_path ?? null
    formModel.mock_enabled = newConfig.mock_enabled ?? false
    formModel.mock_responses = newConfig.mock_responses ?? {}
  },
  { deep: true },
)

function onFieldChange() {
  emit('update:config', { ...formModel })
}

const methodOptions = [
  { value: 'GET', label: 'GET' },
  { value: 'POST', label: 'POST' },
  { value: 'PUT', label: 'PUT' },
  { value: 'PATCH', label: 'PATCH' },
  { value: 'DELETE', label: 'DELETE' },
]

const mockResponsesText = computed(() => {
  try {
    return JSON.stringify(formModel.mock_responses, null, 2)
  } catch {
    return '{}'
  }
})

function onMockResponsesChange(text: string) {
  try {
    formModel.mock_responses = JSON.parse(text)
    onFieldChange()
  } catch {
    // Invalid JSON, don't emit
  }
}
</script>

<template>
  <div class="http-node-form">
    <el-form label-position="top" :disabled="props.readonly">
      <el-form-item label="URL" prop="url">
        <el-input v-model="formModel.url" placeholder="https://api.example.com" @change="onFieldChange" />
        <div class="agent-form-helptext">服务端将做 SSRF 白名单校验</div>
      </el-form-item>

      <el-form-item label="Method" prop="method">
        <el-select v-model="formModel.method" @change="onFieldChange">
          <el-option
            v-for="opt in methodOptions"
            :key="opt.value"
            :value="opt.value"
            :label="opt.label"
          />
        </el-select>
      </el-form-item>

      <el-form-item label="Body Template" prop="body_template">
        <el-input
          v-model="formModel.body_template"
          type="textarea"
          :rows="3"
          placeholder='{"query": "{input}"}'
          @change="onFieldChange"
        />
        <div class="agent-form-helptext">支持 {input} 占位符</div>
      </el-form-item>

      <el-form-item label="Response Path" prop="response_path">
        <el-input v-model="formModel.response_path" placeholder="data" @change="onFieldChange" />
      </el-form-item>

      <el-form-item label="Mock 模式" prop="mock_enabled">
        <el-switch v-model="formModel.mock_enabled" @change="onFieldChange" />
      </el-form-item>

      <el-form-item v-if="formModel.mock_enabled" label="Mock Responses" prop="mock_responses">
        <el-input
          :model-value="mockResponsesText"
          type="textarea"
          :rows="5"
          :disabled="!formModel.mock_enabled || props.readonly"
          @change="onMockResponsesChange"
        />
        <div class="agent-form-helptext">JSON 格式，键为 "{method} {url}"</div>
      </el-form-item>
    </el-form>
  </div>
</template>

<style scoped>
.http-node-form {
  display: flex;
  flex-direction: column;
  gap: 16px;
}

.agent-form-helptext {
  font-size: 12px;
  color: var(--color-text-tertiary);
  margin-top: 4px;
}
</style>
