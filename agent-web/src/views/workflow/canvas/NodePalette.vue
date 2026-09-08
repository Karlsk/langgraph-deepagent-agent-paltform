<script setup lang="ts">
import { PALETTE_ITEMS } from './nodeCatalog'

interface Props {
  readonly?: boolean
}

const props = withDefaults(defineProps<Props>(), {
  readonly: false,
})

const emit = defineEmits<{
  'add-node': [payload: { type: 'llm' | 'http'; position: { x: number; y: number } }]
}>()

function onDragStart(event: DragEvent, type: 'llm' | 'http') {
  if (props.readonly) return
  event.dataTransfer?.setData('application/json', JSON.stringify({ type }))
}

function onClick(type: 'llm' | 'http') {
  if (props.readonly) return
  emit('add-node', { type, position: { x: 0, y: 0 } })
}
</script>

<template>
  <div class="node-palette" :class="{ 'node-palette--readonly': props.readonly }">
    <div class="node-palette__title">节点类型</div>
    <div
      v-for="item in PALETTE_ITEMS"
      :key="item.type"
      class="node-palette__item"
      :class="`node-palette__item--${item.type}`"
      :draggable="!props.readonly"
      :aria-disabled="props.readonly"
      @dragstart="onDragStart($event, item.type)"
      @click="onClick(item.type)"
    >
      <span class="node-palette__icon">{{ item.icon }}</span>
      <span class="node-palette__label">{{ item.label }}</span>
    </div>
  </div>
</template>

<style scoped>
.node-palette {
  padding: 16px;
  background: var(--color-bg-surface);
  border-right: 1px solid var(--color-border-default);
  width: 200px;
}

.node-palette--readonly {
  opacity: 0.5;
  pointer-events: none;
}

.node-palette__title {
  font-weight: 600;
  color: var(--color-text-primary);
  margin-bottom: 12px;
  font-size: 14px;
}

.node-palette__item {
  display: flex;
  align-items: center;
  gap: 8px;
  padding: 10px 12px;
  border-radius: var(--radius-md);
  border: 2px solid var(--color-border-default);
  background: var(--color-bg-elevated);
  cursor: grab;
  margin-bottom: 8px;
  transition: border-color var(--duration-fast) var(--ease-standard);
}

.node-palette__item:hover:not([aria-disabled="true"]) {
  border-color: var(--color-node-llm);
}

.node-palette__item--llm:hover:not([aria-disabled="true"]) {
  border-color: var(--color-node-llm);
}

.node-palette__item--http:hover:not([aria-disabled="true"]) {
  border-color: var(--color-node-http);
}

.node-palette__item[aria-disabled="true"] {
  cursor: not-allowed;
}

.node-palette__icon {
  font-size: 18px;
}

.node-palette__label {
  font-weight: 500;
  color: var(--color-text-primary);
  font-size: 14px;
}
</style>
