<script setup lang="ts">
/**
 * 提供商回收站页：列出软删墓碑 provider，提供硬删除逃生口。
 *
 * - 数据源 listDeletedProviders()（GET /providers/deleted，后端按
 *   updated_at desc 返回全量）；关键字 + 类型过滤在前端完成
 *   （墓碑量预期 < 100，后端 trash 端点暂无 query param）；
 * - 「永久删除」是硬删逃生口：ElMessageBox.prompt 要求输入精确名称，
 *   确认后走 hardDeleteProvider（?hard=true + X-Confirm-Hard-Delete）；
 * - 详情弹窗（ProviderTrashDetailDialog）内可查看墓碑下的 model 清单
 *   并执行 model 级硬删；
 * - 不提供恢复按钮：后端未交付 restore 端点，同名重建需先在此硬删清墓碑。
 */
import { ref } from 'vue'
import { ElMessageBox } from 'element-plus'

import WebAgentTable from '@/components/WebAgentTable.vue'
import type { TableColumnConfig } from '@/components/WebAgentTable.vue'
import ProviderTrashDetailDialog from '@/views/provider/ProviderTrashDetailDialog.vue'
import {
  hardDeleteProvider,
  listDeletedProviders,
  type DeletedProviderRow,
  type ProviderType,
} from '@/api/provider'
import { notifySuccess } from '@/utils/notify'
import { paginateLocal } from '@/utils/paginate'
import type { PageQuery, PageResult } from '@/types'

const PROVIDER_TYPES: ProviderType[] = [
  'OPENAI',
  'ANTHROPIC',
  'OLLAMA',
  'OPENAI_COMPATIBLE',
]

const TYPE_LABELS: Record<ProviderType, string> = {
  OPENAI: 'OpenAI',
  ANTHROPIC: 'Anthropic',
  OLLAMA: 'Ollama',
  OPENAI_COMPATIBLE: 'OpenAI 兼容',
}

const columns: TableColumnConfig[] = [
  { label: '名称', prop: 'name', width: 160, slot: 'name' },
  { label: '类型', prop: 'type', width: 130, slot: 'type' },
  { label: 'Base URL', prop: 'base_url', slot: 'baseUrl' },
  { label: 'API Key', prop: 'api_key_masked', width: 150, slot: 'apiKey' },
  { label: '删除时间', prop: 'updated_at', width: 180, slot: 'deletedAt' },
  { label: '操作', prop: 'actions', width: 180, slot: 'actions' },
]

const tableRef = ref<{ refresh: () => void }>()

const keyword = ref('')
const typeFilter = ref<ProviderType | ''>('')

/** 过滤条件变化时经 WebAgentTable 的 query watch 触发重新请求（重置回第一页） */
function filterQuery(): Record<string, unknown> {
  return {
    keyword: keyword.value,
    type: typeFilter.value === '' ? undefined : typeFilter.value,
  }
}

/**
 * 数据源：全量拉取后前端过滤 + 本地分页。
 * WebAgentTable 坚持 PageResult 契约，paginateLocal 包装裸数组；
 * type 过滤经 WebAgentTable 的 query 通道透传（PageQuery 之外的扩展键）。
 */
async function api(query: PageQuery): Promise<PageResult<DeletedProviderRow>> {
  const rows = await listDeletedProviders()
  const kw = String(query.keyword ?? '').trim().toLowerCase()
  const type = (query as PageQuery & { type?: ProviderType }).type
  return paginateLocal(rows, query, (row) => {
    const hitKeyword = !kw || row.name.toLowerCase().includes(kw)
    const hitType = !type || row.type === type
    return hitKeyword && hitType
  })
}

const detailVisible = ref(false)
const detailRow = ref<DeletedProviderRow | null>(null)

function openDetail(row: DeletedProviderRow): void {
  detailRow.value = row
  detailVisible.value = true
}

/** 详情弹窗内硬删成功后：刷新列表；若当前详情行已被删掉则同步关闭 */
function handleHardDeleted(): void {
  tableRef.value?.refresh()
  if (detailRow.value) {
    void listDeletedProviders().then((rows) => {
      if (!rows.some((row) => row.name === detailRow.value?.name)) {
        detailVisible.value = false
      }
    })
  }
}

/** 硬删逃生口二次确认：输入精确名称才放行 */
async function confirmHardDelete(row: DeletedProviderRow): Promise<void> {
  try {
    const { value } = await ElMessageBox.prompt(
      `永久删除提供商「${row.name}」？该操作不可恢复，名称释放后可重建同名 provider。请输入名称以确认。`,
      '危险操作',
      {
        inputPlaceholder: '请输入提供商名称',
        inputValidator: (v: string) => v === row.name || '名称不匹配',
        confirmButtonText: '永久删除',
        cancelButtonText: '取消',
        type: 'warning',
      },
    )
    if (value !== row.name) return
  } catch {
    return
  }
  await hardDeleteProvider(row.name)
  notifySuccess(`已永久删除：${row.name}`)
  tableRef.value?.refresh()
}
</script>

<template>
  <div class="page-view">
    <header class="page-view__header">
      <div>
        <h1 class="page-view__title">提供商回收站</h1>
        <p class="page-view__desc">
          列出已软删但仍占用唯一名称的提供商；永久删除后同名即可重建。
        </p>
      </div>
      <div class="page-view__actions">
        <el-button @click="$router.push('/llm')">返回活跃列表</el-button>
      </div>
    </header>

    <section class="content-card page-view__body">
      <div class="trash-toolbar">
        <el-input
          v-model="keyword"
          class="trash-toolbar__keyword"
          placeholder="按名称搜索"
          clearable
        />
        <el-select
          v-model="typeFilter"
          class="trash-toolbar__type"
          placeholder="按类型筛选"
          clearable
        >
          <el-option
            v-for="item in PROVIDER_TYPES"
            :key="item"
            :label="TYPE_LABELS[item]"
            :value="item"
          />
        </el-select>
      </div>

      <WebAgentTable
        ref="tableRef"
        :columns="columns"
        :api="api"
        :query="filterQuery()"
      >
        <template #name="{ row }">
          <span class="trash-name">{{ (row as DeletedProviderRow).name }}</span>
        </template>
        <template #type="{ row }">
          <span>{{ TYPE_LABELS[(row as DeletedProviderRow).type] }}</span>
        </template>
        <template #baseUrl="{ row }">
          <span>{{ (row as DeletedProviderRow).base_url || '—' }}</span>
        </template>
        <template #apiKey="{ row }">
          <span class="trash-api-key">{{ (row as DeletedProviderRow).api_key_masked || '—' }}</span>
        </template>
        <template #deletedAt="{ row }">
          <span>{{ (row as DeletedProviderRow).updated_at || '—' }}</span>
        </template>
        <template #actions="{ row }">
          <el-button link type="primary" size="small" @click="openDetail(row as DeletedProviderRow)">
            查看详情
          </el-button>
          <el-button
            link
            type="danger"
            size="small"
            @click="confirmHardDelete(row as DeletedProviderRow)"
          >
            永久删除
          </el-button>
        </template>
      </WebAgentTable>
    </section>

    <ProviderTrashDetailDialog
      v-if="detailRow"
      v-model="detailVisible"
      :provider="detailRow"
      @hard-deleted="handleHardDeleted"
    />
  </div>
</template>

<style scoped>
.trash-toolbar {
  display: flex;
  gap: 12px;
  margin-bottom: 16px;
}

.trash-toolbar__keyword {
  max-width: 260px;
}

.trash-toolbar__type {
  max-width: 180px;
}

.trash-name {
  font-weight: 600;
  color: var(--color-text-primary);
}

.trash-api-key {
  font-family: var(--app-font-display);
  color: var(--color-text-secondary);
  letter-spacing: 0.02em;
}
</style>
