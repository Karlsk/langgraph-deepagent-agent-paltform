<script setup lang="ts">
import { ref, watch } from 'vue'
import type { Node } from '@vue-flow/core'
import type { WorkflowNodeType } from '@/api/workflow'
import LlmNodeForm from './LlmNodeForm.vue'
import HttpNodeForm from './HttpNodeForm.vue'
import PythonNodeForm from './PythonNodeForm.vue'

interface Props {
  node: Node | null
  readonly?: boolean
}

const props = withDefaults(defineProps<Props>(), {
  node: null,
  readonly: false,
})

const emit = defineEmits<{
  'update:node': [patch: { name?: string; config?: Record<string, unknown> }]
  'remove-node': [id: string]
}>()

const nodeName = ref(props.node?.data?.name ?? '')

watch(
  () => props.node,
  (newNode) => {
    nodeName.value = newNode?.data?.name ?? ''
  },
)

function onNameChange() {
  if (!props.node) return
  emit('update:node', { name: nodeName.value })
}

function onConfigUpdate(config: Record<string, unknown>) {
  if (!props.node) return
  emit('update:node', { config })
}

function onDelete() {
  if (!props.node) return
  emit('remove-node', props.node.id)
}

function getNodeType(): WorkflowNodeType | null {
  if (!props.node) return null
  return props.node.data?.type ?? null
}

function getNodeConfig(): Record<string, unknown> {
  return props.node?.data?.config ?? {}
}
</script>

<template>
  <div class="node-config-panel">
    <template v-if="!props.node">
      <div class="node-config-panel__empty">请选择一个节点进行配置</div>
    </template>

    <template v-else>
      <div class="node-config-panel__header">
        <div class="node-config-panel__name-input">
          <label>节点名称</label>
          <input
            v-model="nodeName"
            type="text"
            :disabled="props.readonly"
            @change="onNameChange"
          />
        </div>
      </div>

      <div class="node-config-panel__body">
        <LlmNodeForm
          v-if="getNodeType() === 'llm'"
          :config="getNodeConfig()"
          :readonly="props.readonly"
          @update:config="onConfigUpdate"
        />
        <HttpNodeForm
          v-else-if="getNodeType() === 'http'"
          :config="getNodeConfig()"
          :readonly="props.readonly"
          @update:config="onConfigUpdate"
        />
        <PythonNodeForm
          v-else-if="getNodeType() === 'python'"
          :config="getNodeConfig()"
          :readonly="props.readonly"
          @update:config="onConfigUpdate"
        />
      </div>

      <div v-if="!props.readonly" class="node-config-panel__footer">
        <button class="node-config-panel__delete-btn" @click="onDelete">删除节点</button>
      </div>
    </template>
  </div>
</template>

<style scoped>
.node-config-panel {
  display: flex;
  flex-direction: column;
  height: 100%;
  background: var(--color-bg-surface);
  border-left: 1px solid var(--color-border-default);
  width: 320px;
  padding: 16px;
}

.node-config-panel__empty {
  display: flex;
  align-items: center;
  justify-content: center;
  height: 100%;
  color: var(--color-text-tertiary);
  font-size: 14px;
}

.node-config-panel__header {
  margin-bottom: 16px;
}

.node-config-panel__name-input {
  display: flex;
  flex-direction: column;
  gap: 4px;
}

.node-config-panel__name-input label {
  font-size: 12px;
  font-weight: 500;
  color: var(--color-text-secondary);
}

.node-config-panel__name-input input {
  padding: 8px 12px;
  border: 1px solid var(--color-border-default);
  border-radius: var(--radius-md);
  font-size: 14px;
  color: var(--color-text-primary);
  background: var(--color-bg-elevated);
  transition: border-color var(--duration-fast) var(--ease-standard);
}

.node-config-panel__name-input input:focus {
  outline: none;
  border-color: var(--color-border-strong);
}

.node-config-panel__name-input input:disabled {
  opacity: 0.5;
  cursor: not-allowed;
}

.node-config-panel__body {
  flex: 1;
  overflow-y: auto;
}

.node-config-panel__footer {
  margin-top: 16px;
  padding-top: 16px;
  border-top: 1px solid var(--color-border-default);
}

.node-config-panel__delete-btn {
  width: 100%;
  padding: 8px 16px;
  background: var(--color-danger-500);
  color: white;
  border: none;
  border-radius: var(--radius-md);
  font-size: 14px;
  font-weight: 500;
  cursor: pointer;
  transition: background var(--duration-fast) var(--ease-standard);
}

.node-config-panel__delete-btn:hover {
  background: var(--color-danger-600);
}
</style>
