<script setup lang="ts">
/**
 * McpServerToolsDialog MCP server 工具查看 + 调试调用弹窗：
 * - 顶部「刷新」按钮：调 listMcpServerTools(serverName) 实时拉取（不走池缓存）；
 * - 工具名以加粗形式展示，并在「命名空间」列提示「{server}__{tool}」形态
 *   （catalog / allowed_tools 实际使用的形态）；
 * - args_schema 字段以格式化 JSON 展示，便于调试时核对入参结构；
 * - 每行提供「调用」按钮，行内展开 JSON 输入面板 + 实时调用 + 结果展示
 *   （POST /mcp-servers/{name}/call-tool，详见 agent_apps.py L458-481）。
 *
 * 错误处理：失败时由 request.ts 拦截器统一 toast；本地只清空表格避免遗留状态。
 */
import { onMounted, reactive, ref, watch } from 'vue'

import { callMcpServerTool, listMcpServerTools, type McpToolInfo } from '@/api/mcp'

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

/** 行内调用面板状态：key=tool.name；同一时刻只允许展开一行 */
interface CallState {
  argumentsText: string
  loading: boolean
  result: unknown
  error: string | null
}

const callStates = reactive<Record<string, CallState>>({})

function ensureCallState(toolName: string): CallState {
  if (!callStates[toolName]) {
    callStates[toolName] = {
      argumentsText: '{\n  \n}',
      loading: false,
      result: null,
      error: null,
    }
  }
  return callStates[toolName]
}

function resetResults(): void {
  tools.value = []
  // 清空所有行内调用状态
  for (const key of Object.keys(callStates)) {
    callStates[key].loading = false
    callStates[key].result = null
    callStates[key].error = null
  }
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

/** 命名空间名：catalog / allowed_tools 实际使用的形态 */
function namespacedName(toolName: string): string {
  return `${props.serverName}__${toolName}`
}

/** args_schema 序列化为可读 JSON（缩进 2 空格，过滤 undefined） */
function formatSchema(schema: Record<string, unknown>): string {
  return JSON.stringify(schema, null, 2)
}

/** result 序列化为可读 JSON（result 可能是 string/content-block list/任意结构） */
function formatResult(result: unknown): string {
  if (result === null || result === undefined) return ''
  if (typeof result === 'string') return result
  return JSON.stringify(result, null, 2)
}

/** 解析用户输入的 arguments 文本为对象；非法时返回 null */
function parseArguments(text: string): Record<string, unknown> | null {
  const trimmed = text.trim()
  if (trimmed.length === 0) return {}
  try {
    const parsed: unknown = JSON.parse(trimmed)
    if (parsed === null || typeof parsed !== 'object' || Array.isArray(parsed)) {
      return null
    }
    return parsed as Record<string, unknown>
  } catch {
    return null
  }
}

async function handleInvoke(toolName: string): Promise<void> {
  const state = ensureCallState(toolName)
  const args = parseArguments(state.argumentsText)
  if (args === null) {
    state.error = 'arguments 必须是 JSON object（例如 {} 或 {"key":"value"}）'
    state.result = null
    return
  }
  state.loading = true
  state.error = null
  state.result = null
  try {
    const response = await callMcpServerTool(props.serverName, {
      tool_name: toolName,
      arguments: args,
    })
    state.result = response.result
  } catch (err: unknown) {
    // 拦截器已 toast；这里把 axios 错误体/消息落到行内 result 面板便于排查
    const message =
      err && typeof err === 'object' && 'response' in err
        ? JSON.stringify(
            (err as { response?: { data?: unknown; status?: number } }).response?.data ??
              (err as { response?: { status?: number } }).response?.status ??
              err,
            null,
            2,
          )
        : err instanceof Error
          ? err
          : '调用失败'
    state.error = typeof message === 'string' ? message : JSON.stringify(message, null, 2)
  } finally {
    state.loading = false
  }
}

function handleCancelInvoke(toolName: string): void {
  const state = callStates[toolName]
  if (!state) return
  state.argumentsText = '{\n  \n}'
  state.loading = false
  state.result = null
  state.error = null
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
        行内「调用」按钮可直接触发工具调试调用。
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
      <el-table-column label="args_schema" min-width="220">
        <template #default="{ row }">
          <pre class="mcp-tools-dialog__schema">{{ formatSchema((row as McpToolInfo).args_schema) }}</pre>
        </template>
      </el-table-column>
      <el-table-column label="调用" width="120" fixed="right">
        <template #default="{ row }">
          <el-button
            link
            type="primary"
            :disabled="callStates[(row as McpToolInfo).name]?.loading"
            @click="ensureCallState((row as McpToolInfo).name)"
          >
            调用
          </el-button>
        </template>
      </el-table-column>

      <!-- 行内展开：调用面板（args 输入 + 结果/错误展示） -->
      <template #append>
        <tr
          v-for="tool in tools"
          v-show="callStates[tool.name]"
          :key="`call-${tool.name}`"
          class="mcp-tools-dialog__call-row"
        >
          <td colspan="5">
            <div class="mcp-tools-dialog__call-panel">
              <div class="mcp-tools-dialog__call-header">
                <span class="mcp-tools-dialog__call-title">
                  调试调用 — <code>{{ tool.name }}</code>
                </span>
                <el-button link type="info" @click="handleCancelInvoke(tool.name)">
                  收起
                </el-button>
              </div>

              <label class="mcp-tools-dialog__label">
                arguments（JSON object）
              </label>
              <el-input
                type="textarea"
                :model-value="callStates[tool.name]?.argumentsText ?? '{\n  \n}'"
                :autosize="{ minRows: 3, maxRows: 8 }"
                placeholder='{"key":"value"}'
                @update:model-value="(v: string) => { ensureCallState(tool.name).argumentsText = v }"
              />

              <div class="mcp-tools-dialog__call-actions">
                <el-button
                  type="primary"
                  :loading="callStates[tool.name]?.loading"
                  @click="handleInvoke(tool.name)"
                >
                  执行调用
                </el-button>
                <el-button @click="handleCancelInvoke(tool.name)">清空</el-button>
              </div>

              <template v-if="callStates[tool.name]?.result !== null && callStates[tool.name]?.result !== undefined">
                <label class="mcp-tools-dialog__label mcp-tools-dialog__label--ok">
                  result（成功）
                </label>
                <pre class="mcp-tools-dialog__result mcp-tools-dialog__result--ok">{{ formatResult(callStates[tool.name]?.result) }}</pre>
              </template>

              <template v-if="callStates[tool.name]?.error">
                <label class="mcp-tools-dialog__label mcp-tools-dialog__label--err">
                  error（失败）
                </label>
                <pre class="mcp-tools-dialog__result mcp-tools-dialog__result--err">{{ callStates[tool.name]?.error }}</pre>
              </template>
            </div>
          </td>
        </tr>
      </template>
    </el-table>

    <template #footer>
      <el-button @click="handleClose">关闭</el-button>
    </template>
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

.mcp-tools-dialog__call-row > td {
  background: var(--color-bg-subtle, #f8fafc);
  border-top: 1px solid var(--color-border-light, #e5e7eb);
}

.mcp-tools-dialog__call-panel {
  padding: 12px 16px 16px;
  display: flex;
  flex-direction: column;
  gap: 8px;
}

.mcp-tools-dialog__call-header {
  display: flex;
  align-items: center;
  justify-content: space-between;
}

.mcp-tools-dialog__call-title {
  font-weight: 600;
  font-size: 13px;
  color: var(--color-text-primary);
}

.mcp-tools-dialog__call-title code {
  font-family: var(--app-font-mono, monospace);
  background: var(--color-bg-elevated, #fff);
  padding: 1px 6px;
  border-radius: var(--radius-sm);
  border: 1px solid var(--color-border-light, #e5e7eb);
  margin-left: 4px;
}

.mcp-tools-dialog__label {
  font-size: 12px;
  font-weight: 500;
  color: var(--color-text-secondary);
  margin-top: 4px;
}

.mcp-tools-dialog__label--ok {
  color: var(--color-success-600, #16a34a);
}

.mcp-tools-dialog__label--err {
  color: var(--color-danger-600, #dc2626);
}

.mcp-tools-dialog__call-actions {
  display: flex;
  gap: 8px;
  margin-top: 4px;
}

.mcp-tools-dialog__result {
  margin: 0;
  padding: 10px 12px;
  border-radius: var(--radius-sm);
  font-family: var(--app-font-mono, monospace);
  font-size: 12px;
  max-height: 240px;
  overflow: auto;
  white-space: pre-wrap;
  word-break: break-all;
}

.mcp-tools-dialog__result--ok {
  background: rgba(22, 163, 74, 0.06);
  border: 1px solid rgba(22, 163, 74, 0.25);
  color: var(--color-text-primary, #0f172a);
}

.mcp-tools-dialog__result--err {
  background: rgba(220, 38, 38, 0.06);
  border: 1px solid rgba(220, 38, 38, 0.25);
  color: var(--color-text-primary, #0f172a);
}
</style>