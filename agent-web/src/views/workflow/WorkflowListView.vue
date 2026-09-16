<script setup lang="ts">
/**
 * 工作流列表页（spec-06）：基于后端 WorkflowSummary 契约，
 * 展示已注册 workflow 摘要，提供「设计 / 执行 / 删除」入口。
 *
 * 数据源：`listWorkflows()` 返回裸数组（服务端暂无分页），
 * 前端用 `paginateLocal` 包装为 PageResult 供 WebAgentTable 消费。
 *
 * 操作列：设计 → router.push 跳设计器；执行 → 打开 spec-07 对话框；
 * 删除 → useConfirm + deleteWorkflow，成功后 refresh。
 */
import { onMounted, ref } from 'vue'
import { useRouter } from 'vue-router'

import WebAgentTable from '@/components/WebAgentTable.vue'
import type { TableColumnConfig } from '@/components/WebAgentTable.vue'
import WorkflowExecuteDialog from '@/views/workflow/WorkflowExecuteDialog.vue'
import WorkflowRunHistoryDialog from '@/views/workflow/WorkflowRunHistoryDialog.vue'
import {
  deleteWorkflow,
  listWorkflows,
  type WorkflowSummary,
} from '@/api/workflow'
import { useConfirm } from '@/composables/useConfirm'
import { useWorkflowCapabilities } from '@/composables/useWorkflowCapabilities'
import { paginateLocal } from '@/utils/paginate'
import type { PageQuery, PageResult } from '@/types'

const columns: TableColumnConfig[] = [
  { label: 'Workflow ID', prop: 'workflow_id', width: 200 },
  { label: '节点数', prop: 'node_count', width: 100 },
  { label: '入口', prop: 'entry_point', width: 150 },
  { label: '描述', prop: 'description', slot: 'description' },
  { label: '操作', prop: 'actions', width: 300, slot: 'actions' },
]

const router = useRouter()
const tableRef = ref<{ refresh: () => void }>()
const { canEdit, refresh: refreshCapabilities } = useWorkflowCapabilities()

onMounted(() => {
  void refreshCapabilities()
})

async function api(query: PageQuery): Promise<PageResult<WorkflowSummary>> {
  const items = await listWorkflows()
  return paginateLocal(items, query)
}

function handleDesign(row: WorkflowSummary): void {
  void router.push(`/workflow/${row.workflow_id}/design`)
}

const executeDialogVisible = ref(false)
const executeWorkflowId = ref<string | null>(null)

function handleExecute(row: WorkflowSummary): void {
  executeWorkflowId.value = row.workflow_id
  executeDialogVisible.value = true
}

const historyDialogVisible = ref(false)
const historyWorkflowId = ref<string | null>(null)

function handleHistory(row: WorkflowSummary): void {
  historyWorkflowId.value = row.workflow_id
  historyDialogVisible.value = true
}

function handleCreate(): void {
  void router.push({ name: 'workflow-new-design' })
}

function handleDelete(row: WorkflowSummary): void {
  const confirmAndDelete = useConfirm(
    `确定删除工作流「${row.workflow_id}」吗？此操作不可恢复。`,
    () => deleteWorkflow(row.workflow_id),
    { title: '删除确认', successMessage: '已删除' },
  )
  void confirmAndDelete().then((done) => {
    if (done) tableRef.value?.refresh()
  })
}
</script>

<template>
  <div class="page-view">
    <header class="page-view__header">
      <div>
        <h1 class="page-view__title">工作流管理</h1>
        <p class="page-view__desc">查看和管理已注册的声明式工作流。</p>
      </div>
      <el-button type="primary" @click="handleCreate">新建工作流</el-button>
    </header>

    <section class="content-card page-view__body">
      <WebAgentTable
        ref="tableRef"
        :columns="columns"
        :api="api"
        :default-page-size="20"
      >
        <template #description="{ row }">
          <span>{{ (row as WorkflowSummary).description || '—' }}</span>
        </template>
        <template #actions="{ row }">
          <el-button link type="primary" size="small" @click="handleDesign(row as WorkflowSummary)">
            设计
          </el-button>
          <el-button link type="primary" size="small" @click="handleExecute(row as WorkflowSummary)">
            执行
          </el-button>
          <el-button link type="primary" size="small" @click="handleHistory(row as WorkflowSummary)">
            历史
          </el-button>
          <el-button
            v-if="canEdit"
            link
            type="danger"
            size="small"
            @click="handleDelete(row as WorkflowSummary)"
          >
            删除
          </el-button>
        </template>
      </WebAgentTable>
    </section>

    <WorkflowExecuteDialog
      v-if="executeWorkflowId"
      v-model="executeDialogVisible"
      :workflow-id="executeWorkflowId"
      @executed="() => tableRef?.refresh()"
    />

    <WorkflowRunHistoryDialog
      v-if="historyWorkflowId"
      v-model="historyDialogVisible"
      :workflow-id="historyWorkflowId"
    />
  </div>
</template>
