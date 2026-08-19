<script setup lang="ts">
/**
 * ModelDiscoverDialog 上游模型发现弹窗（task-025）：
 * - 顶部"拉取上游模型"按钮：调 discoverProviderModels(providerName) 拉取
 *   上游 /models 列表并展示到 el-table；
 * - 勾选行 → 底部"创建 N 个"按钮按 id/name/model_id 循环调
 *   createProviderModel 创建；
 * - 部分失败降级：成功 N 失败 M 弹通知；emit 'created' 让父组件刷新；
 * - ANTHROPIC provider 应在父组件禁用入口（"从上游发现"按钮 disabled）。
 */
import { ref, watch } from 'vue'

import {
  createProviderModel,
  discoverProviderModels,
  type RemoteModelInfo,
} from '@/api/provider'
import { notifyError, notifyWarning } from '@/utils/notify'

const props = defineProps<{
  /** 当前 provider 名称（来自 ProviderList 行） */
  providerName: string
  modelValue: boolean
}>()

const emit = defineEmits<{
  'update:modelValue': [value: boolean]
  /** 创建完成后通知父组件刷新模型列表（哪怕部分失败） */
  created: []
}>()

const models = ref<RemoteModelInfo[]>([])
const selectedRows = ref<RemoteModelInfo[]>([])
const loading = ref(false)
const creating = ref(false)

function resetResults(): void {
  models.value = []
  selectedRows.value = []
}

watch(
  [() => props.modelValue, () => props.providerName],
  () => resetResults(),
)

async function handleFetch(): Promise<void> {
  resetResults()
  loading.value = true
  try {
    models.value = await discoverProviderModels(props.providerName)
  } catch {
    // 统一请求层拦截器已提示错误；本地仅清空表格避免遗留状态。
    resetResults()
  } finally {
    loading.value = false
  }
}

function handleSelectionChange(rows: RemoteModelInfo[]): void {
  selectedRows.value = rows
}

function handleClose(): void {
  resetResults()
  emit('update:modelValue', false)
}

async function handleCreateSelected(): Promise<void> {
  const targets = [...selectedRows.value]
  if (targets.length === 0 || creating.value) {
    return
  }
  creating.value = true
  let succeeded = 0
  let failed = 0
  try {
    for (const row of targets) {
      try {
        await createProviderModel(props.providerName, {
          name: row.id,
          model_id: row.id,
          enabled: true,
        })
        succeeded += 1
      } catch {
        failed += 1
      }
    }
  } finally {
    creating.value = false
  }

  if (failed === 0) {
    notifyWarning(`已创建 ${succeeded} 个模型`)
  } else if (succeeded === 0) {
    notifyError(`创建失败：${failed} 个模型均未创建`)
  } else {
    notifyWarning(`部分成功：成功 ${succeeded} / 失败 ${failed}`)
  }

  emit('created')
}
</script>

<template>
  <el-dialog
    :model-value="modelValue"
    title="从上游发现模型"
    width="720px"
    @update:model-value="emit('update:modelValue', $event)"
    @close="handleClose"
  >
    <div class="discover-dialog__toolbar">
      <el-button
        class="app-btn app-btn--secondary"
        :loading="loading"
        @click="handleFetch"
      >
        拉取上游模型
      </el-button>
      <span class="discover-dialog__hint">
        将拉取上游 <code>/models</code> 列表（不含密钥），勾选后批量创建到本 provider 下。
      </span>
    </div>

    <el-table
      :data="models"
      v-loading="loading"
      empty-text="暂无数据（请先拉取上游模型）"
      stripe
      size="small"
      @selection-change="handleSelectionChange"
    >
      <el-table-column type="selection" width="48" />
      <el-table-column prop="id" label="Model ID" min-width="220" />
      <el-table-column prop="owned_by" label="来源" width="140">
        <template #default="{ row }">
          {{ (row as RemoteModelInfo).owned_by ?? '—' }}
        </template>
      </el-table-column>
    </el-table>

    <template #footer>
      <el-button @click="handleClose">关闭</el-button>
      <el-button
        class="app-btn app-btn--primary"
        :disabled="selectedRows.length === 0 || creating"
        :loading="creating"
        @click="handleCreateSelected"
      >
        创建 {{ selectedRows.length }} 个
      </el-button>
    </template>
  </el-dialog>
</template>

<style scoped>
.discover-dialog__toolbar {
  display: flex;
  align-items: center;
  gap: 12px;
  margin-bottom: 12px;
}
.discover-dialog__hint {
  font-size: 12px;
  color: var(--color-text-secondary);
}
.discover-dialog__hint code {
  font-family: var(--app-font-mono, monospace);
}
</style>