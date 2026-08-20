<script setup lang="ts">
/**
 * McpServerToolCallDialog 测试桩组件：仅用于 McpServerToolsDialog 的组件级
 * spec 中通过 `findComponent(McpServerToolCallDialogStub)` 验证父组件传
 * 来的 props（modelValue / tool / serverName），不模拟任何真实交互。
 * 真实表单行为由 `tests/components/mcp-server-tool-call-dialog.spec.ts` 覆盖。
 */
import { defineComponent } from 'vue'

defineComponent({
  name: 'McpServerToolCallDialogStub',
})

defineProps<{
  serverName: string
  tool: unknown
  modelValue: boolean
}>()
</script>

<template>
  <div class="mcp-tool-call-dialog-stub" />
</template>