<script setup lang="ts">
/**
 * Provider 模型清单弹窗：管理单个 provider 下的 model config。
 *
 * 数据源：`/providers/{name}/models`（listProviderModels / createProviderModel /
 * updateProviderModel / deleteProviderModel），由 `@/api/provider` 提供。
 *
 * UI 结构：外层 el-dialog + 顶部工具栏（新增模型按钮）+ 模型表格 + 内嵌
 * WebAgentFormDialog（form 字段：name / model_id / context_size / enabled）。
 *
 * 删除走 useConfirm（与 ProviderList 一致的二次确认语义）；错误由统一请求层
 * 拦截器提示；删除 / 创建 / 编辑 成功后刷新本地 models 数组。
 */
import { computed, onMounted, reactive, ref, watch } from 'vue'
import type { FormRules } from 'element-plus'

import WebAgentFormDialog from '@/components/WebAgentFormDialog.vue'
import ModelDiscoverDialog from '@/views/provider/ModelDiscoverDialog.vue'
import {
  createProviderModel,
  deleteProviderModel,
  listProviderModels,
  updateProviderModel,
  type ModelConfigRow,
  type ModelConfigCreatePayload,
  type ProviderType,
} from '@/api/provider'
import { useConfirm } from '@/composables/useConfirm'
import { notifySuccess } from '@/utils/notify'

const props = defineProps<{
  /** 当前 provider 唯一名称（来自 ProviderList 行） */
  providerName: string
  /**
   * Provider 类型：用于"从上游发现"按钮在 ANTHROPIC 时禁用（ANTHROPIC 不支持
   * 上游 /models 端点）。ProviderList 行已携带该字段，从父组件透传即可。
   */
  providerType?: ProviderType
  modelValue: boolean
}>()

const emit = defineEmits<{
  'update:modelValue': [value: boolean]
}>()

const models = ref<ModelConfigRow[]>([])
const loading = ref(false)
const dialogRef = ref<InstanceType<typeof WebAgentFormDialog>>()
const innerDialogVisible = ref(false)
const editingName = ref<string | null>(null)
const discoverVisible = ref(false)
/** ANTHROPIC provider 不支持上游 /models 端点：禁用以避免 422。 */
const discoverDisabled = computed(() => props.providerType === 'ANTHROPIC')

interface ModelFormShape {
  name: string
  model_id: string
  context_size: number | null
  enabled: boolean
}

const formState = reactive<ModelFormShape>({
  name: '',
  model_id: '',
  context_size: null,
  enabled: true,
})

const rules: FormRules = {
  name: [{ required: true, message: '请输入模型名称', trigger: 'blur' }],
  model_id: [{ required: true, message: '请输入模型 ID', trigger: 'blur' }],
}

async function refresh(): Promise<void> {
  loading.value = true
  try {
    models.value = await listProviderModels(props.providerName)
  } finally {
    loading.value = false
  }
}

/** 监听 dialog 打开：每次拉一次最新（避免依赖 emit 顺序） */
watch(
  () => props.modelValue,
  (open) => {
    if (open) {
      void refresh()
    }
  },
)

onMounted(() => {
  if (props.modelValue) {
    void refresh()
  }
})

function handleClose(): void {
  emit('update:modelValue', false)
}

function handleCreate(): void {
  editingName.value = null
  formState.name = ''
  formState.model_id = ''
  formState.context_size = null
  formState.enabled = true
  // 用 WebAgentFormDialog.open() 透传空对象作为 reset；再让模板对 create 模式显示字段
  dialogRef.value?.open()
}

function handleEdit(row: ModelConfigRow): void {
  editingName.value = row.name
  formState.name = row.name
  formState.model_id = row.model_id
  formState.context_size = row.context_size
  formState.enabled = row.enabled
  // 透传整行作为 initial data：WebAgentFormDialog 走 edit 模式（name disabled）
  dialogRef.value?.open({
    name: row.name,
    model_id: row.model_id,
    context_size: row.context_size,
    enabled: row.enabled,
  } as unknown as Record<string, unknown>)
}

async function handleSubmit(data: Record<string, unknown>): Promise<void> {
  const raw = data as unknown as Partial<ModelFormShape>
  const name = (raw.name ?? '').trim()
  const modelId = (raw.model_id ?? '').trim()
  const contextSize =
    typeof raw.context_size === 'number'
      ? raw.context_size
      : raw.context_size === null || raw.context_size === ''
        ? null
        : Number(raw.context_size)
  const enabled = raw.enabled !== false

  if (!name || !modelId) {
    return
  }

  dialogRef.value?.setSubmitting(true)
  try {
    if (editingName.value !== null) {
      const payload: Partial<ModelConfigCreatePayload> = {
        model_id: modelId,
        context_size: Number.isNaN(contextSize) ? null : contextSize,
        enabled,
      }
      await updateProviderModel(props.providerName, editingName.value, payload)
    } else {
      const payload: ModelConfigCreatePayload = {
        name,
        model_id: modelId,
        context_size: Number.isNaN(contextSize) ? null : contextSize,
        enabled,
      }
      await createProviderModel(props.providerName, payload)
    }
  } finally {
    dialogRef.value?.setSubmitting(false)
  }

  dialogRef.value?.close()
  notifySuccess(`已保存：${name}`)
  await refresh()
}

function handleDelete(row: ModelConfigRow): void {
  const confirmAndDelete = useConfirm(
    `确定删除模型「${row.name}」吗？`,
    async () => {
      await deleteProviderModel(props.providerName, row.name)
    },
    { title: '删除确认', successMessage: '删除成功' },
  )
  void confirmAndDelete().then(async (done) => {
    if (done) await refresh()
  })
}

/** 表格内嵌表格工具列：编辑 / 删除 */
</script>

<template>
  <el-dialog
    :model-value="modelValue"
    :title="`${providerName} · 模型管理`"
    width="720px"
    @update:model-value="emit('update:modelValue', $event)"
    @close="handleClose"
  >
    <div class="model-dialog__toolbar">
      <el-button class="app-btn app-btn--primary" :disabled="loading" @click="handleCreate">
        新增模型
      </el-button>
      <el-button
        class="app-btn app-btn--secondary"
        :disabled="loading || discoverDisabled"
        @click="discoverVisible = true"
      >
        从上游发现
      </el-button>
      <span v-if="loading" class="model-dialog__loading">加载中…</span>
    </div>

    <el-table
      :data="models"
      v-loading="loading"
      empty-text="暂无模型"
      stripe
      size="small"
    >
      <el-table-column prop="name" label="名称" min-width="120" />
      <el-table-column prop="model_id" label="Model ID" min-width="200" />
      <el-table-column prop="context_size" label="上下文" width="100">
        <template #default="{ row }">
          {{ (row as ModelConfigRow).context_size ?? '—' }}
        </template>
      </el-table-column>
      <el-table-column prop="enabled" label="状态" width="80">
        <template #default="{ row }">
          <el-tag
            :type="(row as ModelConfigRow).enabled ? 'success' : 'info'"
            size="small"
          >
            {{ (row as ModelConfigRow).enabled ? '启用' : '禁用' }}
          </el-tag>
        </template>
      </el-table-column>
      <el-table-column label="操作" width="160">
        <template #default="{ row }">
          <el-button link type="primary" size="small" @click="handleEdit(row as ModelConfigRow)">
            编辑
          </el-button>
          <el-button
            link
            type="danger"
            size="small"
            @click="handleDelete(row as ModelConfigRow)"
          >
            删除
          </el-button>
        </template>
      </el-table-column>
    </el-table>

    <WebAgentFormDialog
      ref="dialogRef"
      v-model="innerDialogVisible"
      title="模型信息"
      :rules="rules"
      @submit="handleSubmit"
    >
      <template #default="{ form, mode }">
        <el-form-item label="名称" prop="name">
          <el-input
            v-model="form.name"
            placeholder="请输入模型名称"
            :disabled="mode === 'edit'"
          />
        </el-form-item>
        <el-form-item label="Model ID" prop="model_id">
          <el-input v-model="form.model_id" placeholder="请输入模型 ID" />
        </el-form-item>
        <el-form-item label="上下文大小" prop="context_size">
          <el-input-number
            v-model="form.context_size"
            :min="0"
            placeholder="留空表示不限制"
            controls-position="right"
            style="width: 100%"
          />
        </el-form-item>
        <el-form-item label="启用" prop="enabled">
          <el-switch v-model="form.enabled" />
        </el-form-item>
      </template>
    </WebAgentFormDialog>

    <ModelDiscoverDialog
      v-model="discoverVisible"
      :provider-name="providerName"
      @created="refresh"
    />
  </el-dialog>
</template>

<style scoped>
.model-dialog__toolbar {
  display: flex;
  align-items: center;
  gap: 12px;
  margin-bottom: 12px;
}
.model-dialog__loading {
  font-size: 12px;
  color: var(--color-text-secondary);
}
</style>