<script setup lang="ts">
import { ref, watch } from 'vue'

import { getWorkflow } from '@/api/workflow'
import { notifyError, notifySuccess } from '@/utils/notify'

const props = withDefaults(
  defineProps<{
    modelValue: boolean
    workflowId: string
    dirty?: boolean
  }>(),
  { dirty: false },
)

const emit = defineEmits<{
  'update:modelValue': [value: boolean]
}>()

const yamlText = ref('')
const loading = ref(false)
const notFound = ref(false)
const fetchError = ref(false)

watch(
  () => props.modelValue,
  async (open) => {
    if (!open) return

    yamlText.value = ''
    notFound.value = false
    fetchError.value = false

    if (!props.workflowId) {
      notFound.value = true
      return
    }

    loading.value = true
    try {
      const result = await getWorkflow(props.workflowId, 'yaml')
      if ('yaml_text' in result) {
        yamlText.value = result.yaml_text
      }
    } catch (error: unknown) {
      const err = error as { response?: { status?: number } }
      if (err?.response?.status === 404) {
        notFound.value = true
      } else {
        fetchError.value = true
        notifyError('加载 YAML 失败')
      }
    } finally {
      loading.value = false
    }
  },
  { immediate: true },
)

async function onCopy() {
  try {
    await navigator.clipboard.writeText(yamlText.value)
    notifySuccess('已复制到剪贴板')
  } catch {
    notifyError('复制失败')
  }
}
</script>

<template>
  <el-drawer
    :model-value="modelValue"
    title="YAML 预览"
    size="520px"
    @update:model-value="emit('update:modelValue', $event)"
  >
    <div class="yaml-preview-drawer__body">
      <div v-if="dirty" class="yaml-preview-drawer__dirty">
        预览为已保存版本，未保存的画布改动不包含在内；保存后再预览可看到最新 YAML
      </div>

      <div v-if="!workflowId || notFound" class="yaml-preview-drawer__not-found">
        请先保存后再预览 YAML
      </div>

      <div v-else-if="fetchError" class="yaml-preview-drawer__error">
        加载 YAML 失败，请重试
      </div>

      <div v-else-if="loading" class="yaml-preview-drawer__loading">
        加载中…
      </div>

      <template v-else-if="yamlText">
        <div class="yaml-preview-drawer__toolbar">
          <button class="yaml-preview-drawer__copy" @click="onCopy">
            复制
          </button>
        </div>
        <pre class="yaml-preview-drawer__code" v-text="yamlText"></pre>
      </template>
    </div>
  </el-drawer>
</template>

<style scoped>
.yaml-preview-drawer__body {
  display: flex;
  flex-direction: column;
  gap: 12px;
}

.yaml-preview-drawer__dirty {
  padding: 8px 12px;
  border-radius: var(--radius-sm);
  background: var(--el-color-warning-light-9);
  color: var(--color-warning-600);
  font-size: 13px;
  line-height: 1.5;
}

.yaml-preview-drawer__not-found {
  padding: 32px 0;
  text-align: center;
  color: var(--color-text-tertiary);
}

.yaml-preview-drawer__error {
  padding: 12px;
  border-radius: var(--radius-sm);
  background: var(--el-color-danger-light-9);
  color: var(--color-danger-600);
  font-size: 13px;
}

.yaml-preview-drawer__loading {
  padding: 32px 0;
  text-align: center;
  color: var(--color-text-tertiary);
}

.yaml-preview-drawer__toolbar {
  display: flex;
  justify-content: flex-end;
}

.yaml-preview-drawer__copy {
  padding: 4px 12px;
  border: 1px solid var(--color-border-default);
  border-radius: var(--radius-sm);
  background: var(--color-bg-surface);
  color: var(--color-text-primary);
  font-size: 13px;
  cursor: pointer;
}

.yaml-preview-drawer__copy:hover {
  background: var(--color-bg-subtle);
}

.yaml-preview-drawer__code {
  margin: 0;
  padding: 12px;
  background: var(--color-bg-subtle);
  border-radius: var(--radius-sm);
  font-size: 12px;
  line-height: 1.6;
  overflow-x: auto;
  color: var(--color-text-primary);
  white-space: pre;
  font-family: ui-monospace, SFMono-Regular, Menlo, Monaco, Consolas, monospace;
}
</style>
