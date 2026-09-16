<script setup lang="ts">
/**
 * 工作流运行历史弹窗：
 * - 父组件 WorkflowListView 在用户点击「历史」按钮时设置 workflowId 并打开本弹窗；
 * - 数据来源：GET /workflows/{id}/runs（分页摘要）；
 * - 行内「轨迹」按钮拉取完整运行详情并打开 WorkflowTraceDrawer。
 */
import { ref, watch } from 'vue'

import {
  getWorkflowRun,
  listWorkflowRuns,
  type ExecutionLogView,
  type WorkflowRunDetail,
  type WorkflowRunSummary,
} from '@/api/workflow'
import { useRequest } from '@/composables/useRequest'
import WorkflowTraceDrawer from '@/views/workflow/WorkflowTraceDrawer.vue'

const props = defineProps<{
  modelValue: boolean
  workflowId: string
}>()

const emit = defineEmits<{
  'update:modelValue': [value: boolean]
}>()

const PAGE_SIZE = 10

const page = ref(1)
const total = ref(0)
const rows = ref<WorkflowRunSummary[]>([])

const { loading, execute } = useRequest(listWorkflowRuns)

async function loadPage(nextPage: number): Promise<void> {
  const result = await execute(props.workflowId, { page: nextPage, pageSize: PAGE_SIZE })
  if (result === null) return
  rows.value = result.items
  total.value = result.total
  page.value = result.page
}

watch(
  () => props.modelValue,
  (visible) => {
    if (visible) void loadPage(1)
  },
  { immediate: true },
)

const traceVisible = ref(false)
const traceLogs = ref<ExecutionLogView[]>([])

async function handleOpenTrace(row: WorkflowRunSummary): Promise<void> {
  const detail: WorkflowRunDetail | null = await getWorkflowRun(props.workflowId, row.run_id)
  if (detail && detail.execution_logs) {
    traceLogs.value = detail.execution_logs
    traceVisible.value = true
  }
}

function closeDialog(): void {
  emit('update:modelValue', false)
}

function handlePageChange(nextPage: number): void {
  void loadPage(nextPage)
}

function formatDuration(ms: number): string {
  if (ms < 1000) return `${Math.round(ms)}ms`
  return `${(ms / 1000).toFixed(2)}s`
}

function createdLabel(iso: string): string {
  const date = new Date(iso)
  if (Number.isNaN(date.getTime())) return iso
  return date.toLocaleString(undefined, { hour12: false })
}
</script>

<template>
  <el-dialog
    :model-value="modelValue"
    :title="`运行历史 — ${workflowId}`"
    width="920px"
    append-to-body
    :close-on-click-modal="false"
    @update:model-value="emit('update:modelValue', $event)"
  >
    <div v-loading="loading" class="run-history">
      <el-table :data="rows" size="small" empty-text="暂无运行记录">
        <el-table-column prop="status" label="状态" width="80">
          <template #default="{ row }">
            <el-tag
              :type="(row as WorkflowRunSummary).status === 'success' ? 'success' : 'danger'"
              size="small"
            >
              {{ (row as WorkflowRunSummary).status === 'success' ? '成功' : '失败' }}
            </el-tag>
          </template>
        </el-table-column>
        <el-table-column label="耗时" width="90">
          <template #default="{ row }">
            {{ formatDuration((row as WorkflowRunSummary).duration_ms) }}
          </template>
        </el-table-column>
        <el-table-column prop="node_count" label="节点数" width="80" />
        <el-table-column prop="error_message" label="错误" min-width="200" show-overflow-tooltip>
          <template #default="{ row }">
            <span class="run-history__error">{{ (row as WorkflowRunSummary).error_message || '—' }}</span>
          </template>
        </el-table-column>
        <el-table-column prop="created_by" label="触发人" width="100" show-overflow-tooltip>
          <template #default="{ row }">
            {{ (row as WorkflowRunSummary).created_by || '—' }}
          </template>
        </el-table-column>
        <el-table-column label="运行时间" width="170">
          <template #default="{ row }">
            {{ createdLabel((row as WorkflowRunSummary).created_at) }}
          </template>
        </el-table-column>
        <el-table-column label="操作" width="72">
          <template #default="{ row }">
            <el-button link type="primary" size="small" @click="handleOpenTrace(row as WorkflowRunSummary)">
              轨迹
            </el-button>
          </template>
        </el-table-column>
      </el-table>
      <el-pagination
        class="run-history__pagination"
        layout="total, prev, pager, next"
        :current-page="page"
        :page-size="PAGE_SIZE"
        :total="total"
        @current-change="handlePageChange"
      />
    </div>
    <template #footer>
      <el-button @click="closeDialog">关闭</el-button>
    </template>

    <WorkflowTraceDrawer v-model="traceVisible" :logs="traceLogs" />
  </el-dialog>
</template>

<style scoped>
.run-history {
  display: flex;
  flex-direction: column;
  gap: 12px;
  min-height: 200px;
}

.run-history__error {
  color: var(--color-text-secondary);
}

.run-history__pagination {
  justify-content: flex-end;
}
</style>
