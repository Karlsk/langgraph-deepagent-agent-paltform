<script setup lang="ts">
import { VueFlow } from '@vue-flow/core'
import { Background } from '@vue-flow/background'
import { Controls } from '@vue-flow/controls'
import { MiniMap } from '@vue-flow/minimap'
import type { Node, Edge, Connection, NodeMouseEvent } from '@vue-flow/core'
import WorkflowNode from './WorkflowNode.vue'
import EndNode from './EndNode.vue'

interface Props {
  nodes: Node[]
  edges: Edge[]
  readonly?: boolean
}

const props = withDefaults(defineProps<Props>(), {
  readonly: false,
})

const emit = defineEmits<{
  'update:nodes': [nodes: Node[]]
  'update:edges': [edges: Edge[]]
  'select-node': [id: string | null]
  'connect': [edge: { source: string; target: string }]
  'remove-node': [id: string]
  'remove-edge': [id: string]
}>()

const nodeTypes = {
  workflow: WorkflowNode,
  end: EndNode,
} as any

function onConnect(connection: Connection) {
  emit('connect', { source: connection.source, target: connection.target })
}

function onNodeClick(nodeMouseEvent: NodeMouseEvent) {
  emit('select-node', nodeMouseEvent.node.id)
}

function onPaneClick() {
  emit('select-node', null)
}
</script>

<template>
  <div class="workflow-canvas">
    <VueFlow
      v-model:nodes="props.nodes"
      v-model:edges="props.edges"
      :node-types="nodeTypes"
      :nodes-draggable="!props.readonly"
      :connectable="!props.readonly"
      :elements-selectable="!props.readonly"
      @connect="onConnect"
      @node-click="onNodeClick"
      @pane-click="onPaneClick"
    >
      <Background />
      <Controls />
      <MiniMap />
    </VueFlow>
  </div>
</template>

<style scoped>
.workflow-canvas {
  width: 100%;
  height: 100%;
  background: var(--color-canvas-bg);
}
</style>
