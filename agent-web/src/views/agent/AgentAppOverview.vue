<script setup lang="ts">
/**
 * AgentApp 总览页（`/agentapp`）：概念页，只读、无写操作。
 *
 * AgentApp 是平台实体（伞概念，`engine` 字段区分类型：当前恒 `deepagents`，
 * 未来 `workflow`）。本页职责：
 * - 统计卡：总数 / 已发布 / 草稿（数据源 GET /apps 全量）；
 * - 紧凑只读清单：名称 / 类型 / 状态 / 版本 + 「管理」跳转按钮；
 * - 管理入口按引擎类型分流：deepagents → `/agent`（Agent 管理，完整
 *   CRUD + 发布）；其他引擎类型暂禁用并提示（为未来 `/workflow` 预留）。
 *
 * 发布 / 编辑 / 删除等写操作不在本页，归各引擎管理页。
 */
import { computed, ref } from 'vue'
import { useRouter } from 'vue-router'

import WebAgentTable from '@/components/WebAgentTable.vue'
import type { TableColumnConfig } from '@/components/WebAgentTable.vue'
import { listAgentApps, type AgentAppRow } from '@/api/agentapps'
import { paginateLocal } from '@/utils/paginate'
import type { PageQuery, PageResult } from '@/types'

const router = useRouter()

const columns: TableColumnConfig[] = [
  { label: '名称', prop: 'name', width: 200, slot: 'name' },
  { label: '类型', prop: 'engine', width: 140, slot: 'engine' },
  { label: '状态', prop: 'status', width: 110, slot: 'status' },
  { label: '版本', prop: 'version', width: 90 },
  { label: '操作', prop: 'actions', width: 140, slot: 'actions' },
]

/**
 * 全量行缓存：表格 api 每次拉取后写入，统计卡由此派生。
 * 单一数据源避免统计与清单双份请求不一致。
 */
const allRows = ref<AgentAppRow[]>([])

/** 表格数据源：GET /apps 全量本地分页（紧凑只读清单，无搜索、强制单页） */
async function api(query: PageQuery): Promise<PageResult<AgentAppRow>> {
  const rows = await listAgentApps()
  allRows.value = rows
  return paginateLocal(rows, { ...query, page: 1, pageSize: Math.max(rows.length, 1) })
}

const tableRef = ref<{ refresh: () => void }>()

const totalCount = computed(() => allRows.value.length)
const publishedCount = computed(
  () => allRows.value.filter((row) => row.status === 'published').length,
)
const draftCount = computed(() => totalCount.value - publishedCount.value)

/** 刷新：重新拉取全量（统计卡随 allRows 派生自动更新） */
function handleRefresh(): void {
  tableRef.value?.refresh()
}

/** 引擎类型展示名：为未来更多引擎类型留 switch 扩展位 */
function engineLabel(row: AgentAppRow): string {
  if (row.engine === 'deepagents') {
    return 'Agent'
  }
  return row.engine
}

/** 「管理」跳转：按引擎类型分流；非 deepagents 暂不支持（按钮禁用） */
function canManage(row: AgentAppRow): boolean {
  return row.engine === 'deepagents'
}

function handleManage(row: AgentAppRow): void {
  if (!canManage(row)) {
    return
  }
  void router.push('/agent')
}
</script>

<template>
  <div class="page-view">
    <header class="page-view__header">
      <div>
        <h1 class="page-view__title">AgentApp 总览</h1>
        <p class="page-view__desc">
          全平台 AgentApp 只读视图：统计与状态一览；配置、发布等管理操作
          请前往对应引擎类型的管理页。
        </p>
      </div>
      <div class="page-view__actions">
        <el-button class="app-btn app-btn--secondary" @click="handleRefresh">
          刷新
        </el-button>
      </div>
    </header>

    <section class="content-card page-view__body">
      <div class="overview-stats">
        <div class="overview-stats__card">
          <span class="overview-stats__value">{{ totalCount }}</span>
          <span class="overview-stats__label">总数</span>
        </div>
        <div class="overview-stats__card">
          <span class="overview-stats__value overview-stats__value--published">
            {{ publishedCount }}
          </span>
          <span class="overview-stats__label">已发布</span>
        </div>
        <div class="overview-stats__card">
          <span class="overview-stats__value overview-stats__value--draft">
            {{ draftCount }}
          </span>
          <span class="overview-stats__label">草稿</span>
        </div>
      </div>

      <WebAgentTable
        ref="tableRef"
        :columns="columns"
        :api="api"
        :pagination="false"
      >
        <template #name="{ row }">
          <span class="overview-name">{{ (row as AgentAppRow).name }}</span>
        </template>
        <template #engine="{ row }">
          <span>{{ engineLabel(row as AgentAppRow) }}</span>
        </template>
        <template #status="{ row }">
          <el-tag
            :type="(row as AgentAppRow).status === 'published' ? 'success' : 'warning'"
            size="small"
          >
            {{ (row as AgentAppRow).status === 'published' ? '已发布' : '草稿' }}
          </el-tag>
        </template>
        <template #actions="{ row }">
          <el-button
            link
            type="primary"
            size="small"
            :disabled="!canManage(row as AgentAppRow)"
            :title="canManage(row as AgentAppRow) ? undefined : '该类型管理页尚未开放'"
            @click="handleManage(row as AgentAppRow)"
          >
            管理
          </el-button>
        </template>
      </WebAgentTable>
    </section>
  </div>
</template>

<style scoped>
.overview-stats {
  display: flex;
  gap: 16px;
  margin-bottom: 20px;
}
.overview-stats__card {
  flex: 1;
  display: flex;
  flex-direction: column;
  gap: 4px;
  padding: 16px 20px;
  background: var(--color-bg-subtle);
  border: 1px solid var(--color-border-default);
  border-radius: 8px;
}
.overview-stats__value {
  font-size: 24px;
  font-weight: 600;
  color: var(--color-text-primary);
}
.overview-stats__value--published {
  color: var(--color-success-600);
}
.overview-stats__value--draft {
  color: var(--color-warning-600);
}
.overview-stats__label {
  font-size: 13px;
  color: var(--color-text-secondary);
}
.overview-name {
  font-weight: 600;
  color: var(--color-text-primary);
}
</style>
