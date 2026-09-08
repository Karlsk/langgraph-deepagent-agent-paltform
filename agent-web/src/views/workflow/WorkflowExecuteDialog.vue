<script setup lang="ts">
/**
 * 工作流执行对话框（spec-07 占位）：
 * 当前仅接收 workflow_id 并渲染占位文案，
 * spec-07 将替换为完整的执行表单 + 轨迹展示。
 */
defineProps<{
  modelValue: boolean
  workflowId: string
}>()

defineEmits<{
  'update:modelValue': [value: boolean]
}>()
</script>

<template>
  <el-dialog
    :model-value="modelValue"
    title="执行工作流"
    width="600px"
    @update:model-value="$emit('update:modelValue', $event)"
  >
    <p>工作流执行（占位 — spec-07 实现）：{{ workflowId }}</p>
  </el-dialog>
</template>
