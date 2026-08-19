<script setup lang="ts">
/**
 * 模型提供商管理页：基于后端 Provider / Model 契约（snake_case 行字段 +
 * OPENAI/ANTHROPIC/OLLAMA/OPENAI_COMPATIBLE 类型枚举）的 CRUD 视图。
 *
 * 数据源：`listProvidersPage(query)` 走真实后端（`/providers/page`），
 * 返回 `PageResult<ProviderRowWithMeta>`（行附 model_count + health）。
 * 列表 / CRUD / 测试连接 全部走 `@/api/provider` 的函数。
 *
 * 表单：name / type / base_url / api_key（创建非 OLLAMA 必填，编辑选填；
 * 留空 → PATCH 不携带 auth_config，沿用后端"省略即保留"语义）。
 *
 * 单行操作后统一 `tableRef.refresh()` 拉全量，保证后端 422 → 401 等场景下
 * 数据一致；测试连接只回写 ConnectionTestResult 快照，刷新策略同上。
 */
import { ref } from 'vue'
import type { FormRules } from 'element-plus'
import { Delete } from '@element-plus/icons-vue'
import { useRouter } from 'vue-router'

import WebAgentFormDialog from '@/components/WebAgentFormDialog.vue'
import WebAgentTable from '@/components/WebAgentTable.vue'
import type { TableColumnConfig } from '@/components/WebAgentTable.vue'
import ProviderModelDialog from '@/views/provider/ProviderModelDialog.vue'
import {
  createProvider,
  deleteProvider,
  listProvidersPage,
  testProviderConnection,
  updateProvider,
  type ProviderCreatePayload,
  type ProviderHealthSnapshot,
  type ProviderRowWithMeta,
  type ProviderType,
} from '@/api/provider'
import { useConfirm } from '@/composables/useConfirm'
import { notifySuccess } from '@/utils/notify'
import type { PageQuery, PageResult } from '@/types'

/** Provider / Model 资源类型枚举（与 SQLModel Provider.type 字段对齐） */
const PROVIDER_TYPES: ProviderType[] = [
  'OPENAI',
  'ANTHROPIC',
  'OLLAMA',
  'OPENAI_COMPATIBLE',
]

/** 类型枚举到中文标签的映射（仅前端展示用） */
const TYPE_LABELS: Record<ProviderType, string> = {
  OPENAI: 'OpenAI',
  ANTHROPIC: 'Anthropic',
  OLLAMA: 'Ollama',
  OPENAI_COMPATIBLE: 'OpenAI 兼容',
}

/** 健康状态 tag 映射（el-tag type + 中文文案） */
const HEALTH_TAG: Record<
  ProviderHealthSnapshot['status'],
  { type: 'success' | 'danger' | 'warning' | 'info'; label: string }
> = {
  UP: { type: 'success', label: '正常' },
  DOWN: { type: 'danger', label: '不可用' },
  DEGRADED: { type: 'warning', label: '缓慢' },
  UNKNOWN: { type: 'info', label: '未探测' },
}

const columns: TableColumnConfig[] = [
  { label: '名称', prop: 'name', width: 160, slot: 'name' },
  { label: '类型', prop: 'type', width: 110, slot: 'type' },
  { label: 'Base URL', prop: 'base_url', slot: 'baseUrl' },
  { label: 'API Key', prop: 'api_key_masked', width: 150, slot: 'apiKey' },
  { label: '模型数', prop: 'model_count', width: 80 },
  { label: '健康状态', prop: 'health', width: 100, slot: 'health' },
  { label: '状态', prop: 'enabled', width: 80, slot: 'status' },
  { label: '操作', prop: 'actions', width: 290, slot: 'actions' },
]

/** 表格数据源：直接透传到 listProvidersPage，由后端做分页 / 关键字过滤 */
async function api(query: PageQuery): Promise<PageResult<ProviderRowWithMeta>> {
  return listProvidersPage(query)
}

/** 跳转提供商回收站（软删墓碑 + 硬删除逃生口） */
function goTrash(): void {
  void router.push('/llm/trash')
}

const router = useRouter()
const tableRef = ref<{ refresh: () => void }>()
const dialogRef = ref<InstanceType<typeof WebAgentFormDialog>>()
const dialogVisible = ref(false)
const editingName = ref<string | null>(null)

/** ProviderModelDialog 状态：保存当前弹窗对应的 provider 名 + 类型，控制弹窗可见性 */
const modelDialogVisible = ref(false)
const modelDialogProviderName = ref<string | null>(null)
const modelDialogProviderType = ref<ProviderType | null>(null)
function handleManageModels(row: ProviderRowWithMeta): void {
  modelDialogProviderName.value = row.provider.name
  modelDialogProviderType.value = row.provider.type
  modelDialogVisible.value = true
}

interface ProviderFormShape {
  name: string
  type: ProviderType
  base_url: string
  /** 编辑态下空字符串表示"保留原值"；非空 = 替换 */
  api_key: string
}

/** 表单提交时的部分字段：WebAgentFormDialog.open() 不传 data 时为 {}，字段 optional */
type SubmitFormShape = Partial<ProviderFormShape>

const rules: FormRules = {
  name: [{ required: true, message: '请输入提供商名称', trigger: 'blur' }],
  type: [{ required: true, message: '请选择类型', trigger: 'change' }],
  base_url: [{ required: true, message: '请输入 Base URL', trigger: 'blur' }],
}

/** 弹窗内的 api_key 校验：在 WebAgentFormDialog.validate 后由 handleSubmit 二次校验 */
function handleCreate(): void {
  editingName.value = null
  dialogRef.value?.open()
}

function handleEdit(row: ProviderRowWithMeta): void {
  editingName.value = row.provider.name
  // 编辑回填：name/type/base_url 直接来自行；api_key 留空 → 提交时省略 auth_config
  dialogRef.value?.open({
    name: row.provider.name,
    type: row.provider.type,
    base_url: row.provider.base_url,
    api_key: '',
  } satisfies ProviderFormShape)
}

/**
 * 按需连通性探测：调 `/providers/{name}/test` 拿 ConnectionTestResult，
 * 持久化的 health 行已由后端写入 provider_health；前端仅展示结果，
 * 刷新表格从 `/providers/page` 拉最新 health 快照。
 */
async function handleTestConnection(row: ProviderRowWithMeta): Promise<void> {
  const result = await testProviderConnection(row.provider.name)
  const label = HEALTH_TAG[result.status].label
  const latency =
    result.latency_ms !== null ? `（${result.latency_ms}ms）` : ''
  notifySuccess(`已探测：${label}${latency}`)
  tableRef.value?.refresh()
}

async function handleSubmit(data: Record<string, unknown>): Promise<void> {
  // open() 透传空对象或 WebAgentFormDialog 部分填充时字段可能缺失，使用 Partial + 可选链
  const form = data as unknown as SubmitFormShape
  const name = (form.name ?? '').trim()
  const type = form.type
  const baseUrl = (form.base_url ?? '').trim()
  const apiKey = (form.api_key ?? '').trim()

  // 必填字段缺失（WebAgentFormDialog.validate 已拦截，这里双保险）— 静默丢弃
  if (!name || !type || !baseUrl) {
    return
  }

  // 创建时非 OLLAMA 必须提供 api_key（对齐后端 ProviderCreate 校验）
  if (editingName.value === null && type !== 'OLLAMA' && !apiKey) {
    notifySuccess('请输入 API Key（非 OLLAMA 类型必填）')
    return
  }

  dialogRef.value?.setSubmitting(true)
  try {
    if (editingName.value !== null) {
      // 编辑：name 不可改，仅修改 type / base_url / auth_config（省略 = 保留）
      const payload: Partial<ProviderCreatePayload> = {
        type,
        base_url: baseUrl,
      }
      // 仅当 api_key 非空时携带，等价于 PATCH 不带 auth_config → 后端保留原值
      if (apiKey) {
        payload.auth_config = { api_key: apiKey }
      }
      await updateProvider(editingName.value, payload)
    } else {
      const payload: ProviderCreatePayload = {
        name,
        type,
        base_url: baseUrl,
        auth_config: apiKey ? { api_key: apiKey } : undefined,
        enabled: true,
      }
      await createProvider(payload)
    }
  } finally {
    dialogRef.value?.setSubmitting(false)
  }

  dialogRef.value?.close()
  notifySuccess(`已保存：${name}`)
  tableRef.value?.refresh()
}

function handleDelete(row: ProviderRowWithMeta): void {
  const confirmAndDelete = useConfirm(
    `确定软删除提供商「${row.provider.name}」吗？该操作不会物理清除数据，名称仍占用唯一索引；如需重建同名请先到回收站永久清理。`,
    async () => {
      await deleteProvider(row.provider.name)
    },
    { title: '软删除确认', successMessage: '已软删除' },
  )
  void confirmAndDelete().then((done) => {
    if (done) tableRef.value?.refresh()
  })
}

/** 健康 tag 的 tooltip 内容（latency / error / last_check） */
function healthTooltip(row: ProviderRowWithMeta): string {
  const parts: string[] = []
  if (row.health.latency_ms !== null) parts.push(`延迟：${row.health.latency_ms}ms`)
  if (row.health.error_message) parts.push(`错误：${row.health.error_message}`)
  if (row.health.last_check_at) parts.push(`最近探测：${row.health.last_check_at}`)
  return parts.join('\n') || '尚未探测'
}
</script>

<template>
  <div class="page-view">
    <header class="page-view__header">
      <div>
        <h1 class="page-view__title">模型提供商管理</h1>
        <p class="page-view__desc">集中管理 LLM 提供商的接入凭证与端点配置。</p>
      </div>
      <div class="page-view__actions">
        <el-button @click="goTrash">
          <el-icon class="provider-trash-icon"><Delete /></el-icon>
          <span>回收站</span>
        </el-button>
        <el-button class="app-btn app-btn--primary" @click="handleCreate">
          新增提供商
        </el-button>
      </div>
    </header>

    <section class="content-card page-view__body">
      <WebAgentTable ref="tableRef" :columns="columns" :api="api">
        <template #name="{ row }">
          <span class="provider-name">{{ (row as ProviderRowWithMeta).provider.name }}</span>
        </template>
        <template #type="{ row }">
          <span>{{ TYPE_LABELS[(row as ProviderRowWithMeta).provider.type] }}</span>
        </template>
        <template #baseUrl="{ row }">
          <span>{{ (row as ProviderRowWithMeta).provider.base_url || '—' }}</span>
        </template>
        <template #apiKey="{ row }">
          <span class="provider-api-key">{{ (row as ProviderRowWithMeta).provider.api_key_masked || '—' }}</span>
        </template>
        <template #health="{ row }">
          <el-tag
            :type="HEALTH_TAG[(row as ProviderRowWithMeta).health.status].type"
            size="small"
            :title="healthTooltip(row as ProviderRowWithMeta)"
          >
            {{ HEALTH_TAG[(row as ProviderRowWithMeta).health.status].label }}
          </el-tag>
        </template>
        <template #status="{ row }">
          <el-tag :type="(row as ProviderRowWithMeta).provider.enabled ? 'success' : 'info'" size="small">
            {{ (row as ProviderRowWithMeta).provider.enabled ? '启用' : '禁用' }}
          </el-tag>
        </template>
        <template #actions="{ row }">
          <el-button link type="primary" size="small" @click="handleManageModels(row as ProviderRowWithMeta)">
            模型管理
          </el-button>
          <el-button link type="primary" size="small" @click="handleEdit(row as ProviderRowWithMeta)">
            编辑
          </el-button>
          <el-button
            link
            type="primary"
            size="small"
            :disabled="!(row as ProviderRowWithMeta).provider.enabled"
            @click="handleTestConnection(row as ProviderRowWithMeta)"
          >
            测试连接
          </el-button>
          <el-button link type="danger" size="small" @click="handleDelete(row as ProviderRowWithMeta)">
            软删除
          </el-button>
        </template>
      </WebAgentTable>
    </section>

    <WebAgentFormDialog
      ref="dialogRef"
      v-model="dialogVisible"
      title="提供商信息"
      :rules="rules"
      @submit="handleSubmit"
    >
      <template #default="{ form, mode }">
        <el-form-item label="Name" prop="name">
          <el-input
            v-model="form.name"
            placeholder="请输入提供商名称"
            :disabled="mode === 'edit'"
          />
        </el-form-item>
        <el-form-item label="Type" prop="type">
          <el-select v-model="form.type" placeholder="请选择类型">
            <el-option
              v-for="item in PROVIDER_TYPES"
              :key="item"
              :label="TYPE_LABELS[item]"
              :value="item"
            />
          </el-select>
        </el-form-item>
        <el-form-item label="Base URL" prop="base_url">
          <el-input v-model="form.base_url" placeholder="请输入 Base URL" />
        </el-form-item>
        <el-form-item label="API Key" prop="api_key">
          <el-input
            v-model="form.api_key"
            placeholder="编辑时留空表示保持不变"
            show-password
          />
        </el-form-item>
      </template>
    </WebAgentFormDialog>

    <ProviderModelDialog
      v-if="modelDialogProviderName && modelDialogProviderType"
      v-model="modelDialogVisible"
      :provider-name="modelDialogProviderName"
      :provider-type="modelDialogProviderType"
    />
  </div>
</template>

<style scoped>
.provider-name {
  font-weight: 600;
  color: var(--color-text-primary);
}
.provider-api-key {
  font-family: var(--app-font-display);
  color: var(--color-text-secondary);
  letter-spacing: 0.02em;
}
</style>