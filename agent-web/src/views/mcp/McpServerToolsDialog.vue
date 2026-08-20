<script setup lang="ts">
/**
 * McpServerToolsDialog MCP server 工具查看弹窗：
 * - 顶部「刷新」按钮：调 listMcpServerTools(serverName) 实时拉取（不走池缓存）；
 * - 工具名以加粗形式展示，并在「命名空间」列提示「{server}__{tool}」形态
 *   （catalog / allowed_tools 实际使用的形态）；
 * - args_schema 字段以格式化 JSON 展示，便于调试时核对入参结构；
 * - 每行提供实心「调用」按钮，点击后弹出 McpServerToolCallDialog 独立弹窗
 *   （POST /mcp-servers/{name}/call-tool，详见 agent_apps.py L458-481）。
 *
 * 错误处理：失败时由 request.ts 拦截器统一 toast；本地只清空表格避免遗留状态。
 */
import { onMounted, ref, watch } from 'vue'

import McpServerToolCallDialog from '@/views/mcp/McpServerToolCallDialog.vue'
import { listMcpServerTools, type McpToolInfo } from '@/api/mcp'

const props = defineProps<{
  /** 当前 server 名称（来自 McpList 行） */
  serverName: string
  modelValue: boolean
}>()

const emit = defineEmits<{
  'update:modelValue': [value: boolean]
}>()

const tools = ref<McpToolInfo[]>([])
const loading = ref(false)

/** 「调用」独立弹窗状态 */
const callDialogTool = ref<McpToolInfo | null>(null)
const callDialogVisible = ref(false)

function resetResults(): void {
  tools.value = []
  callDialogTool.value = null
  callDialogVisible.value = false
}

watch(
  [() => props.modelValue, () => props.serverName],
  () => resetResults(),
)

async function handleFetch(): Promise<void> {
  resetResults()
  loading.value = true
  try {
    tools.value = await listMcpServerTools(props.serverName)
  } catch {
    // 统一请求层拦截器已提示错误；本地仅清空表格避免遗留状态。
    resetResults()
  } finally {
    loading.value = false
  }
}

function handleClose(): void {
  resetResults()
  emit('update:modelValue', false)
}

/**
 * 监听弹窗打开：每次 modelValue 切换为 true 时拉取（避免每次切 tab 都点刷新）。
 * 关闭重置状态由 `watch([modelValue, serverName])` 完成；onMounted 仅在
 * McpList 的 `v-if` 首次创建本组件时触发，确保初始挂载即拉取（因为 watch
 * 默认不在 initial 值触发）。
 */
onMounted(() => {
  if (props.modelValue) {
    void handleFetch()
  }
})

watch(
  () => props.modelValue,
  (open) => {
    if (open) {
      void handleFetch()
    }
  },
)

/** 行「调用」按钮：把当前 tool 传给独立弹窗并打开 */
function handleCallClick(row: McpToolInfo): void {
  callDialogTool.value = row
  callDialogVisible.value = true
}

/** 命名空间名：catalog / allowed_tools 实际使用的形态 */
function namespacedName(toolName: string): string {
  return `${props.serverName}__${toolName}`
}

/** args_schema 序列化为可读 JSON（缩进 2 空格，过滤 undefined） */
function formatSchema(schema: Record<string, unknown>): string {
  return JSON.stringify(schema, null, 2)
}
</script>

<template>
  <el-dialog
    :model-value="modelValue"
    :title="`MCP 工具列表 — ${serverName}`"
    width="900px"
    @update:model-value="emit('update:modelValue', $event)"
    @close="handleClose"
  >
    <div class="mcp-tools-dialog__toolbar">
      <el-button
        class="app-btn app-btn--secondary"
        :loading="loading"
        @click="handleFetch"
      >
        刷新
      </el-button>
      <span class="mcp-tools-dialog__hint">
        实时探测该 server 当前暴露的工具（不读池缓存）；工具名为后端裸名，
        catalog / allowed_tools 中实际使用时需冠以命名空间「{{ namespacedName('xxx') }}」。
        行末「调用」按钮可弹出独立调试弹窗。
      </span>
    </div>

    <el-table
      :data="tools"
      v-loading="loading"
      empty-text="暂无工具（请检查 server 配置或尝试刷新）"
      stripe
      size="small"
      row-key="name"
    >
      <el-table-column label="工具名（裸名）" min-width="160">
        <template #default="{ row }">
          <span class="mcp-tools-dialog__tool-name">
            {{ (row as McpToolInfo).name }}
          </span>
        </template>
      </el-table-column>
      <el-table-column label="命名空间（catalog）" min-width="220">
        <template #default="{ row }">
          <code class="mcp-tools-dialog__namespaced">
            {{ namespacedName((row as McpToolInfo).name) }}
          </code>
        </template>
      </el-table-column>
      <el-table-column label="描述" min-width="220">
        <template #default="{ row }">
          <span>{{ (row as McpToolInfo).description || '—' }}</span>
        </template>
      </el-table-column>
      <el-table-column label="args_schema" min-width="240">
        <template #default="{ row }">
          <pre class="mcp-tools-dialog__schema">{{ formatSchema((row as McpToolInfo).args_schema) }}</pre>
        </template>
      </el-table-column>
      <el-table-column label="操作" width="100" fixed="right">
        <template #default="{ row }">
          <el-button
            size="small"
            type="primary"
            class="mcp-tools-dialog__call-btn"
            @click="handleCallClick(row as McpToolInfo)"
          >
            调用
          </el-button>
        </template>
      </el-table-column>
    </el-table>

    <template #footer>
      <el-button @click="handleClose">关闭</el-button>
    </template>

    <McpServerToolCallDialog
      v-model="callDialogVisible"
      :server-name="serverName"
      :tool="callDialogTool"
    />
  </el-dialog>
</template>

<style scoped>
.mcp-tools-dialog__toolbar {
  display: flex;
  align-items: center;
  gap: 12px;
  margin-bottom: 12px;
}

.mcp-tools-dialog__hint {
  font-size: 12px;
  color: var(--color-text-secondary);
  line-height: 1.4;
}

.mcp-tools-dialog__tool-name {
  font-weight: 600;
  color: var(--color-text-primary);
  font-family: var(--app-font-mono, monospace);
}

.mcp-tools-dialog__namespaced {
  font-family: var(--app-font-mono, monospace);
  font-size: 12px;
  color: var(--color-primary-600);
  background: var(--color-bg-subtle);
  padding: 2px 6px;
  border-radius: var(--radius-sm);
}

.mcp-tools-dialog__schema {
  margin: 0;
  padding: 8px 10px;
  background: var(--color-bg-subtle);
  border-radius: var(--radius-sm);
  font-family: var(--app-font-mono, monospace);
  font-size: 12px;
  color: var(--color-text-primary);
  max-height: 120px;
  overflow: auto;
  white-space: pre-wrap;
  word-break: break-all;
}

.mcp-tools-dialog__call-btn {
  width: 64px;
}
</style>