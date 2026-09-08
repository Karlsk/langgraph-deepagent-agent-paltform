<script setup lang="ts">
/**
 * WorkflowCanvasSmoke — spec-08 冒烟组件。
 * 验证 Vue Flow 依赖可用 + 画布 token 生效。
 * @deprecated spec-10 将替换为完整画布交互组件。
 */
import { VueFlow } from '@vue-flow/core'
import { Background } from '@vue-flow/background'
import { Controls } from '@vue-flow/controls'
import { MiniMap } from '@vue-flow/minimap'
import type { Node, Edge } from '@vue-flow/core'

const nodes: Node[] = [
  { id: '1', label: 'LLM Node', position: { x: 100, y: 100 } },
  { id: '2', label: 'HTTP Node', position: { x: 400, y: 300 } },
]

const edges: Edge[] = [
  { id: 'e1-2', source: '1', target: '2', label: 'edge' },
]
</script>

<template>
  <div class="workflow-canvas-smoke">
    <VueFlow :nodes="nodes" :edges="edges" :fit-view-on-init="true">
      <Background />
      <Controls />
      <MiniMap />
    </VueFlow>
  </div>
</template>

<style scoped>
.workflow-canvas-smoke {
  width: 100%;
  height: 400px;
  background: var(--color-canvas-bg);
}
</style>
