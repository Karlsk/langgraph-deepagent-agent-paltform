<script setup lang="ts">
import { onMounted, onBeforeUnmount, computed, ref } from 'vue'
import { useRoute, useRouter, onBeforeRouteLeave } from 'vue-router'
import { ElMessageBox } from 'element-plus'
import { useWorkflowDesigner } from '@/composables/useWorkflowDesigner'
import { useWorkflowCapabilities } from '@/composables/useWorkflowCapabilities'
import WorkflowCanvas from '@/views/workflow/canvas/WorkflowCanvas.vue'
import NodePalette from '@/views/workflow/canvas/NodePalette.vue'
import NodeConfigPanel from '@/views/workflow/panel/NodeConfigPanel.vue'
import StateSchemaPanel from '@/views/workflow/panel/StateSchemaPanel.vue'
import ConditionEdgeDialog from '@/views/workflow/canvas/ConditionEdgeDialog.vue'
import YamlPreviewDrawer from '@/views/workflow/YamlPreviewDrawer.vue'

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
  fieldErrors,
  isSaving,
  save,
  loadWorkflow,
} = useWorkflowDesigner()

const { canEdit, refresh: refreshCapabilities } = useWorkflowCapabilities()

const isNewMode = computed(() => route.name === 'workflow-new-design')

const yamlPreviewVisible = ref(false)

function onEntryPointChange(event: Event) {
  const value = (event.target as HTMLSelectElement).value
  meta.value = { ...meta.value, entry_point: value }
}

function onWorkflowIdInput(event: Event) {
  const value = (event.target as HTMLInputElement).value
  meta.value = { ...meta.value, workflow_id: value }
}

async function onSave() {
  await save()
  if (!isDirty.value && isNewMode.value && meta.value.workflow_id) {
    await router.replace({
      name: 'workflow-design',
      params: { workflowId: meta.value.workflow_id },
    })
  }
}

onBeforeRouteLeave(async () => {
  if (!isDirty.value) return true
  try {
    await ElMessageBox.confirm('有未保存的修改，确定要离开吗？', '未保存提示', {
      confirmButtonText: '确定离开',
      cancelButtonText: '取消',
      type: 'warning',
    })
    return true
  } catch {
    return false
  }
})

function onBeforeUnload(e: BeforeUnloadEvent) {
  if (isDirty.value) e.preventDefault()
}

onMounted(() => window.addEventListener('beforeunload', onBeforeUnload))
onBeforeUnmount(() => window.removeEventListener('beforeunload', onBeforeUnload))

onMounted(async () => {
  await refreshCapabilities()
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
        <button
          data-testid="yaml-preview-button"
          class="designer-toolbar__btn"
          @click="yamlPreviewVisible = true"
        >
          预览
        </button>
        <button
          v-if="canEdit"
          data-testid="save-button"
          class="designer-toolbar__btn"
          :disabled="isSaving"
          @click="onSave"
        >
          {{ isSaving ? '保存中...' : '保存' }}
        </button>
        <span v-if="isDirty" class="designer-toolbar__dirty">未保存</span>
      </div>
    </div>

    <div v-if="fieldErrors.length" data-testid="field-errors" class="designer-field-errors">
      <div v-for="(err, i) in fieldErrors" :key="i" class="designer-field-errors__item">
        {{ err.message }}
      </div>
    </div>

    <div class="designer-body">
      <NodePalette :readonly="!canEdit" @add-node="handleAddNode" />

      <WorkflowCanvas
        :nodes="nodes"
        :edges="edges"
        :readonly="!canEdit"
        @update:nodes="handleUpdateNodes"
        @update:edges="handleUpdateEdges"
        @select-node="handleSelectNode"
        @connect="handleConnect"
      />

      <NodeConfigPanel
        v-if="selectedNode"
        :node="selectedNode"
        :readonly="!canEdit"
        @update:node="handleUpdateNode"
        @remove-node="handleRemoveNode"
      />
      <StateSchemaPanel
        v-else
        :model-value="meta.state_schema"
        :readonly="!canEdit"
        @update:model-value="handleUpdateSchema"
      />
    </div>

    <ConditionEdgeDialog
      :model-value="conditionDialogVisible"
      :edge="conditionEdge"
      :node-names="nodeNames"
      :state-channels="stateChannels"
      :readonly="!canEdit"
      @update:model-value="conditionDialogVisible = $event"
      @confirm="handleConditionConfirm"
    />

    <YamlPreviewDrawer
      v-model="yamlPreviewVisible"
      :workflow-id="(route.params.workflowId as string) ?? ''"
      :dirty="isDirty"
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

.designer-field-errors {
  padding: 4px 16px;
  border-bottom: 1px solid var(--el-color-danger-light-7);
  background: var(--el-color-danger-light-9);
}

.designer-field-errors__item {
  font-size: 12px;
  color: var(--color-danger-600);
  line-height: 1.4;
}
</style>
