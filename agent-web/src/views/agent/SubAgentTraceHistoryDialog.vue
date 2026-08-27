<script setup lang="ts">
/**
 * SubAgentTraceHistoryDialog 子代理测试历史弹窗：
 * - 父组件 SubAgentList 在用户点击「历史」按钮时设置 agentName 并打开本弹窗；
 * - 数据来源：GET /subagents/{name}/traces（分页摘要，不含事件流）；
 * - 行内「详情」按钮上抛 `open-detail`（携带 trace id），由父级打开详情弹窗，
 *   避免三层弹窗嵌套状态耦合；
 * - 请求失败由 request.ts 全局拦截器提示，本组件收敛为空数据，不重复 toast。
 */
import { ref, watch } from 'vue'

import {
  listSubAgentTraces,
  type SubAgentTraceSummary,
} from '@/api/subagents'
import { useRequest } from '@/composables/useRequest'

/* -------------------------------------------------------------------------- */
/*  Props / Emits                                                              */
/* -------------------------------------------------------------------------- */

const props = defineProps<{
  modelValue: boolean
  agentName: string
}>()

const emit = defineEmits<{
  'update:modelValue': [value: boolean]
  'open-detail': [traceId: number]
}>()

/* -------------------------------------------------------------------------- */
/*  状态                                                                       */
/* -------------------------------------------------------------------------- */

const PAGE_SIZE = 10

const page = ref(1)
const total = ref(0)
const rows = ref<SubAgentTraceSummary[]>([])

const { loading, execute } = useRequest(listSubAgentTraces)

async function loadPage(nextPage: number): Promise<void> {
  const result = await execute(props.agentName, { page: nextPage, pageSize: PAGE_SIZE })
  // 失败时 execute 返回 null：保留旧数据（首次加载则维持空），避免 UI 闪烁
  if (result === null) {
    return
  }
  rows.value = result.items
  total.value = result.total
  page.value = result.page
}

/** 弹窗打开时重置到第 1 页并拉取；关闭不清空数据（下次打开重新拉取即可） */
watch(
  () => props.modelValue,
  (visible) => {
    if (visible) {
      void loadPage(1)
    }
  },
  { immediate: true },
)

/* -------------------------------------------------------------------------- */
/*  行为                                                                       */
/* -------------------------------------------------------------------------- */

function closeDialog(): void {
  emit('update:modelValue', false)
}

function handlePageChange(nextPage: number): void {
  void loadPage(nextPage)
}

function handleOpenDetail(row: SubAgentTraceSummary): void {
  emit('open-detail', row.id)
}

/** 耗时格式化（与 SubAgentTestDialog 保持一致：< 1s 显示毫秒） */
function durationLabel(seconds: number): string {
  if (seconds < 1) return `${Math.round(seconds * 1000)}ms`
  return `${seconds.toFixed(2)}s`
}

/** ISO 时间串转本地展示（秒级精度） */
function createdLabel(iso: string): string {
  const date = new Date(iso)
  if (Number.isNaN(date.getTime())) {
    return iso
  }
  return date.toLocaleString(undefined, { hour12: false })
}
</script>

<template>
  <el-dialog
    :model-value="modelValue"
    :title="`测试历史 — ${agentName}`"
    width="880px"
    append-to-body
    :close-on-click-modal="false"
    @update:model-value="emit('update:modelValue', $event)"
  >
    <div v-loading="loading" class="subagent-trace-history">
      <el-table :data="rows" size="small" empty-text="暂无测试记录">
        <el-table-column prop="id" label="ID" width="64" />
        <el-table-column prop="status" label="状态" width="80">
          <template #default="{ row }">
            <el-tag
              :type="(row as SubAgentTraceSummary).status === 'success' ? 'success' : 'danger'"
              size="small"
            >
              {{ (row as SubAgentTraceSummary).status === 'success' ? '成功' : '失败' }}
            </el-tag>
          </template>
        </el-table-column>
        <el-table-column prop="prompt" label="prompt" min-width="200">
          <template #default="{ row }">
            <span class="subagent-trace-history__prompt">{{ (row as SubAgentTraceSummary).prompt }}</span>
          </template>
        </el-table-column>
        <el-table-column prop="model" label="模型" width="150" show-overflow-tooltip />
        <el-table-column prop="turns" label="轮次" width="64" />
        <el-table-column label="耗时" width="80">
          <template #default="{ row }">
            {{ durationLabel((row as SubAgentTraceSummary).duration_seconds) }}
          </template>
        </el-table-column>
        <el-table-column label="创建时间" width="160">
          <template #default="{ row }">
            {{ createdLabel((row as SubAgentTraceSummary).created_at) }}
          </template>
        </el-table-column>
        <el-table-column label="操作" width="72">
          <template #default="{ row }">
            <el-button link type="primary" size="small" @click="handleOpenDetail(row as SubAgentTraceSummary)">
              详情
            </el-button>
          </template>
        </el-table-column>
      </el-table>
      <el-pagination
        class="subagent-trace-history__pagination"
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
  </el-dialog>
</template>

<style scoped>
.subagent-trace-history {
  display: flex;
  flex-direction: column;
  gap: 12px;
  min-height: 200px;
}

.subagent-trace-history__prompt {
  display: -webkit-box;
  -webkit-line-clamp: 2;
  -webkit-box-orient: vertical;
  overflow: hidden;
  color: var(--color-text-secondary);
}

.subagent-trace-history__pagination {
  justify-content: flex-end;
}
</style>
