<script setup lang="ts">
/**
 * 回收站详情弹窗：展示软删 provider 的脱敏快照 + 墓碑下的 model 清单。
 *
 * - model 数据走 listDeletedProviderModels（trash 专属端点，active 列表看不到）；
 * - 每行 model 与底部 provider 均提供「永久删除」硬删逃生口，二次确认
 *   要求输入精确名称（ElMessageBox.prompt + validator）；
 * - 硬删成功后 emit('hard-deleted') 通知父组件刷新回收站列表；
 *   provider 级硬删同时关闭弹窗。
 */
import { computed, ref, watch } from 'vue'
import { ElMessageBox } from 'element-plus'

import WebAgentTable from '@/components/WebAgentTable.vue'
import type { TableColumnConfig } from '@/components/WebAgentTable.vue'
import {
  hardDeleteProvider,
  hardDeleteProviderModel,
  listDeletedProviderModels,
  type DeletedModelConfigRow,
  type DeletedProviderRow,
} from '@/api/provider'
import { notifySuccess } from '@/utils/notify'
import type { PageQuery, PageResult } from '@/types'

const props = defineProps<{
  modelValue: boolean
  provider: DeletedProviderRow | null
}>()

const emit = defineEmits<{
  (e: 'update:modelValue', value: boolean): void
  /** 任一硬删成功后触发，父组件据此刷新列表 */
  (e: 'hard-deleted'): void
}>()

const TYPE_LABELS: Record<string, string> = {
  OPENAI: 'OpenAI',
  ANTHROPIC: 'Anthropic',
  OLLAMA: 'Ollama',
  OPENAI_COMPATIBLE: 'OpenAI 兼容',
}

const visible = computed({
  get: () => props.modelValue,
  set: (value: boolean) => emit('update:modelValue', value),
})

const modelColumns: TableColumnConfig[] = [
  { label: '模型名', prop: 'name', width: 140 },
  { label: 'Model ID', prop: 'model_id', width: 160 },
  { label: '引用', prop: 'ref', slot: 'ref' },
  { label: '墓碑状态', prop: 'deleted', width: 100, slot: 'deleted' },
  { label: '操作', prop: 'actions', width: 110, slot: 'actions' },
]

/** 弹窗打开时重置子表格，让 WebAgentTable 重新挂载拉取当前 provider 的 models */
const modelTableKey = ref(0)

watch(
  () => props.modelValue,
  (open) => {
    if (open) modelTableKey.value += 1
  },
)

async function modelApi(
  query: PageQuery,
): Promise<PageResult<DeletedModelConfigRow>> {
  if (!props.provider) {
    return { items: [], total: 0, page: 1, pageSize: 10 }
  }
  const rows = await listDeletedProviderModels(props.provider.name)
  const pageSize = query.pageSize ?? 10
  const page = query.page ?? 1
  return {
    items: rows,
    total: rows.length,
    page,
    pageSize,
  }
}

/**
 * 硬删二次确认：要求输入精确名称才放行。
 * 返回 null 表示用户取消或名称不匹配（不执行删除）。
 */
async function confirmHardDelete(
  kind: 'provider' | 'model',
  name: string,
): Promise<boolean> {
  const kindLabel = kind === 'provider' ? '提供商' : '模型'
  try {
    const { value } = await ElMessageBox.prompt(
      `永久删除${kindLabel}「${name}」？该操作不可恢复。请输入名称以确认。`,
      '危险操作',
      {
        inputPlaceholder: `请输入${kindLabel}名称`,
        inputValidator: (v: string) => v === name || '名称不匹配',
        confirmButtonText: '永久删除',
        cancelButtonText: '取消',
        type: 'warning',
      },
    )
    return value === name
  } catch {
    return false
  }
}

async function handleHardDeleteModel(row: DeletedModelConfigRow): Promise<void> {
  if (!props.provider) return
  const confirmed = await confirmHardDelete('model', row.name)
  if (!confirmed) return
  await hardDeleteProviderModel(props.provider.name, row.name)
  notifySuccess(`已永久删除模型：${row.name}`)
  emit('hard-deleted')
}

async function handleHardDeleteProvider(): Promise<void> {
  if (!props.provider) return
  const name = props.provider.name
  const confirmed = await confirmHardDelete('provider', name)
  if (!confirmed) return
  await hardDeleteProvider(name)
  notifySuccess(`已永久删除提供商：${name}`)
  emit('hard-deleted')
  visible.value = false
}
</script>

<template>
  <el-dialog
    v-model="visible"
    :title="`回收站详情：${provider?.name ?? ''}`"
    width="640px"
  >
    <div v-if="provider" class="trash-detail">
      <div class="trash-detail__row">
        <span class="trash-detail__label">名称</span>
        <span class="trash-detail__value trash-detail__value--name">{{ provider.name }}</span>
      </div>
      <div class="trash-detail__row">
        <span class="trash-detail__label">类型</span>
        <span class="trash-detail__value">{{ TYPE_LABELS[provider.type] ?? provider.type }}</span>
      </div>
      <div class="trash-detail__row">
        <span class="trash-detail__label">Base URL</span>
        <span class="trash-detail__value">{{ provider.base_url || '—' }}</span>
      </div>
      <div class="trash-detail__row">
        <span class="trash-detail__label">API Key</span>
        <span class="trash-detail__value trash-detail__value--mono">{{ provider.api_key_masked || '—' }}</span>
      </div>
      <div class="trash-detail__row">
        <span class="trash-detail__label">删除时间</span>
        <span class="trash-detail__value">{{ provider.updated_at || '—' }}</span>
      </div>
    </div>

    <el-divider content-position="left">模型清单（墓碑）</el-divider>

    <WebAgentTable
      :key="modelTableKey"
      :columns="modelColumns"
      :api="modelApi"
      :pagination="false"
    >
      <template #ref="{ row }">
        <span class="trash-model-ref">{{ (row as DeletedModelConfigRow).ref }}</span>
      </template>
      <template #deleted="{ row }">
        <el-tag :type="(row as DeletedModelConfigRow).deleted ? 'info' : 'success'" size="small">
          {{ (row as DeletedModelConfigRow).deleted ? '已软删' : '随父软删前仍活跃' }}
        </el-tag>
      </template>
      <template #actions="{ row }">
        <el-button link type="danger" size="small" @click="handleHardDeleteModel(row as DeletedModelConfigRow)">
          永久删除
        </el-button>
      </template>
    </WebAgentTable>

    <template #footer>
      <el-button @click="visible = false">关闭</el-button>
      <el-button
        class="app-btn app-btn--danger"
        type="danger"
        @click="handleHardDeleteProvider"
      >
        永久删除此提供商
      </el-button>
    </template>
  </el-dialog>
</template>

<style scoped>
.trash-detail {
  display: grid;
  gap: 8px;
}

.trash-detail__row {
  display: grid;
  grid-template-columns: 88px 1fr;
  align-items: baseline;
  gap: 12px;
}

.trash-detail__label {
  color: var(--color-text-tertiary);
  font-size: 13px;
}

.trash-detail__value {
  color: var(--color-text-primary);
  word-break: break-all;
}

.trash-detail__value--name {
  font-weight: 600;
}

.trash-detail__value--mono {
  font-family: var(--app-font-display);
  color: var(--color-text-secondary);
  letter-spacing: 0.02em;
}

.trash-model-ref {
  font-family: var(--app-font-display);
  color: var(--color-text-secondary);
}
</style>
