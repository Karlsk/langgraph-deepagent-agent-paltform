<script setup lang="ts">
import { Handle, Position } from '@vue-flow/core'
</script>

<template>
  <div class="end-node">
    <div class="end-node__icon">⏹</div>
    <div class="end-node__label">END</div>
    <Handle type="target" :position="Position.Top" />
  </div>
</template>

<style scoped>
.end-node {
  padding: 12px 16px;
  border-radius: var(--radius-md);
  border: 2px solid var(--color-border-strong);
  background: var(--color-bg-subtle);
  display: flex;
  align-items: center;
  gap: 8px;
  min-width: 80px;
}

.end-node__icon {
  font-size: 14px;
  color: var(--color-text-tertiary);
}

.end-node__label {
  font-weight: 600;
  color: var(--color-text-secondary);
  font-size: 12px;
  letter-spacing: 0.5px;
}
</style>
