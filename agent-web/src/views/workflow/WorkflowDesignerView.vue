<script setup lang="ts">
import { onMounted, computed } from 'vue'
import { useRoute, useRouter } from 'vue-router'
import { useWorkflowDesigner } from '@/composables/useWorkflowDesigner'
import WorkflowCanvas from '@/views/workflow/canvas/WorkflowCanvas.vue'
import NodePalette from '@/views/workflow/canvas/NodePalette.vue'
import NodeConfigPanel from '@/views/workflow/panel/NodeConfigPanel.vue'
import StateSchemaPanel from '@/views/workflow/panel/StateSchemaPanel.vue'
import ConditionEdgeDialog from '@/views/workflow/canvas/ConditionEdgeDialog.vue'

const route = useRoute()
const router = useRouter()

const {
  nodes,
  edges,
  meta,
  selectedNode,
  isDirty,
  conditionDialogVisible,
  conditionEdge,
  nodeNames,
  stateChannels,
  handleAddNode,
  handleConnect,
  handleConditionConfirm,
  handleSelectNode,
  handleUpdateNode,
  handleRemoveNode,
  handleUpdateSchema,
  handleUpdateNodes,
  handleUpdateEdges,
  loadWorkflow,
} = useWorkflowDesigner()

const isNewMode = computed(() => route.name === 'workflow-new-design')

function onEntryPointChange(event: Event) {
  const value = (event.target as HTMLSelectElement).value
  meta.value = { ...meta.value, entry_point: value }
}

function onWorkflowIdInput(event: Event) {
  const value = (event.target as HTMLInputElement).value
  meta.value = { ...meta.value, workflow_id: value }
}

onMounted(async () => {
  const workflowId = route.params.workflowId as string | undefined
  if (workflowId) {
    try {
      await loadWorkflow(workflowId)
    } catch {
      router.push({ name: 'workflow' })
    }
  }
})
</script>

<template>
  <div class="workflow-designer-view">
    <div class="designer-toolbar">
      <div class="designer-toolbar__field">
        <label>工作流 ID</label>
        <input
          data-testid="workflow-id-input"
          type="text"
          :value="meta.workflow_id"
          :disabled="!isNewMode"
          @input="onWorkflowIdInput"
        />
      </div>

      <div class="designer-toolbar__field">
        <label>入口节点</label>
        <select
          data-testid="entry-point-select"
          :value="meta.entry_point"
          @change="onEntryPointChange"
        >
          <option value="" disabled>选择入口节点</option>
          <option v-for="name in nodeNames" :key="name" :value="name">{{ name }}</option>
        </select>
      </div>

      <div class="designer-toolbar__actions">
        <button class="designer-toolbar__btn" disabled title="YAML 预览由 spec-22 实现">
          预览
        </button>
        <button class="designer-toolbar__btn" disabled title="保存流程由 spec-21 实现">
          保存
        </button>
        <span v-if="isDirty" class="designer-toolbar__dirty">未保存</span>
      </div>
    </div>

    <div class="designer-body">
      <NodePalette @add-node="handleAddNode" />

      <WorkflowCanvas
        :nodes="nodes"
        :edges="edges"
        @update:nodes="handleUpdateNodes"
        @update:edges="handleUpdateEdges"
        @select-node="handleSelectNode"
        @connect="handleConnect"
      />

      <NodeConfigPanel
        v-if="selectedNode"
        :node="selectedNode"
        @update:node="handleUpdateNode"
        @remove-node="handleRemoveNode"
      />
      <StateSchemaPanel
        v-else
        :model-value="meta.state_schema"
        @update:model-value="handleUpdateSchema"
      />
    </div>

    <ConditionEdgeDialog
      :model-value="conditionDialogVisible"
      :edge="conditionEdge"
      :node-names="nodeNames"
      :state-channels="stateChannels"
      @update:model-value="conditionDialogVisible = $event"
      @confirm="handleConditionConfirm"
    />
  </div>
</template>

<style scoped>
.workflow-designer-view {
  display: flex;
  flex-direction: column;
  height: 100%;
  background: var(--color-bg-surface);
}

.designer-toolbar {
  display: flex;
  align-items: center;
  gap: 16px;
  padding: 8px 16px;
  border-bottom: 1px solid var(--color-border-default);
  background: var(--color-bg-elevated);
  flex-shrink: 0;
}

.designer-toolbar__field {
  display: flex;
  align-items: center;
  gap: 6px;
}

.designer-toolbar__field label {
  font-size: 12px;
  font-weight: 500;
  color: var(--color-text-secondary);
  white-space: nowrap;
}

.designer-toolbar__field input,
.designer-toolbar__field select {
  padding: 4px 8px;
  border: 1px solid var(--color-border-default);
  border-radius: var(--radius-md);
  font-size: 13px;
  color: var(--color-text-primary);
  background: var(--color-bg-surface);
}

.designer-toolbar__field input:disabled,
.designer-toolbar__field select:disabled {
  opacity: 0.6;
  cursor: not-allowed;
}

.designer-toolbar__actions {
  display: flex;
  align-items: center;
  gap: 8px;
  margin-left: auto;
}

.designer-toolbar__btn {
  padding: 4px 12px;
  border: 1px solid var(--color-border-default);
  border-radius: var(--radius-md);
  font-size: 13px;
  cursor: pointer;
  background: var(--color-bg-surface);
  color: var(--color-text-primary);
}

.designer-toolbar__btn:disabled {
  opacity: 0.5;
  cursor: not-allowed;
}

.designer-toolbar__dirty {
  font-size: 12px;
  color: var(--color-warning-600);
  font-weight: 500;
}

.designer-body {
  display: flex;
  flex: 1;
  overflow: hidden;
}
</style>
