<script setup lang="ts">
import { Handle, Position } from '@vue-flow/core'

interface Props {
  data: { name: string; type: 'llm' | 'http'; config: Record<string, unknown> }
}

const props = defineProps<Props>()
</script>

<template>
  <div class="workflow-node" :class="`workflow-node--${props.data.type}`">
    <div class="workflow-node__icon">
      <span v-if="props.data.type === 'llm'">🤖</span>
      <span v-else-if="props.data.type === 'http'">🌐</span>
    </div>
    <div class="workflow-node__name">{{ props.data.name }}</div>
    <Handle type="target" :position="Position.Top" />
    <Handle type="source" :position="Position.Bottom" />
  </div>
</template>

<style scoped>
.workflow-node {
  padding: 12px 16px;
  border-radius: var(--radius-md);
  border: 2px solid var(--color-node-llm);
  background: var(--color-bg-surface);
  display: flex;
  align-items: center;
  gap: 8px;
  min-width: 120px;
}

.workflow-node--llm {
  border-color: var(--color-node-llm);
}

.workflow-node--http {
  border-color: var(--color-node-http);
}

.workflow-node__icon {
  font-size: 18px;
}

.workflow-node__name {
  font-weight: 500;
  color: var(--color-text-primary);
}
</style>
