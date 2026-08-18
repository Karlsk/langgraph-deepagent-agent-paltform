<script setup lang="ts">
/**
 * 模型提供商管理页：基于后端 Provider / Model 契约（snake_case 行字段 +
 * OPENAI/ANTHROPIC/OLLAMA/OPENAI_COMPATIBLE 类型枚举）的 CRUD 视图。
 *
 * 本期实现：
 * - 数据源：本地 5 行 mock（嵌套 ProviderRowWithMeta 形状，与后端契约一致），
 *   经 paginateLocal 适配 PageResult 契约；
 * - 列：名称 / 类型 / Base URL / API Key（脱敏只读） / 模型数 / 健康状态 tag /
 *   启用状态 tag / 操作（编辑 / 测试连接 / 删除）；
 * - 表单：name / type / base_url / api_key（创建非 OLLAMA 必填，编辑选填；
 *   留空 → PATCH 不携带 auth_config，沿用后端"省略即保留"语义）。
 *
 * 下期切换到真实 API 时，仅需把 api() 替换为 listProvidersPage()，行结构与
 * 类型契约均已对齐；request.ts token 注入 TODO 关闭后启用。
 */
import { ref } from 'vue'
import type { FormRules } from 'element-plus'

import WebAgentFormDialog from '@/components/WebAgentFormDialog.vue'
import WebAgentTable from '@/components/WebAgentTable.vue'
import type { TableColumnConfig } from '@/components/WebAgentTable.vue'
import type {
  ProviderHealthSnapshot,
  ProviderRowWithMeta,
  ProviderType,
} from '@/api/provider'
import { useConfirm } from '@/composables/useConfirm'
import { notifySuccess } from '@/utils/notify'
import { paginateLocal } from '@/utils/paginate'
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

/** 5 行本地 mock（嵌套 ProviderRowWithMeta，与后端契约一致） */
const providers = ref<ProviderRowWithMeta[]>([
  {
    provider: {
      id: 1,
      name: 'openai-prod',
      type: 'OPENAI',
      base_url: 'https://api.openai.com/v1',
      api_key_masked: '****open',
      enabled: true,
      created_by: 'seed',
      created_at: '2026-06-11 09:20',
      updated_at: '2026-06-11 09:20',
    },
    model_count: 3,
    health: { status: 'UP', last_check_at: '2026-08-18 10:00', last_success_at: '2026-08-18 10:00', fail_count: 0, latency_ms: 214, error_message: null },
  },
  {
    provider: {
      id: 2,
      name: 'anthropic-main',
      type: 'ANTHROPIC',
      base_url: 'https://api.anthropic.com',
      api_key_masked: '****mock',
      enabled: true,
      created_by: 'seed',
      created_at: '2026-06-24 15:40',
      updated_at: '2026-06-24 15:40',
    },
    model_count: 2,
    health: { status: 'UP', last_check_at: '2026-08-18 10:00', last_success_at: '2026-08-18 10:00', fail_count: 0, latency_ms: 412, error_message: null },
  },
  {
    provider: {
      id: 3,
      name: 'openai-compatible-lab',
      type: 'OPENAI_COMPATIBLE',
      base_url: 'https://generativelanguage.googleapis.com/v1beta',
      api_key_masked: '****aiza',
      enabled: false,
      created_by: 'seed',
      created_at: '2026-07-02 14:30',
      updated_at: '2026-07-02 14:30',
    },
    model_count: 1,
    health: { status: 'UNKNOWN', last_check_at: null, last_success_at: null, fail_count: 0, latency_ms: null, error_message: null },
  },
  {
    provider: {
      id: 4,
      name: 'ollama-local',
      type: 'OLLAMA',
      base_url: 'http://localhost:11434',
      api_key_masked: '',
      enabled: true,
      created_by: 'seed',
      created_at: '2026-07-18 10:05',
      updated_at: '2026-07-18 10:05',
    },
    model_count: 0,
    health: { status: 'UNKNOWN', last_check_at: null, last_success_at: null, fail_count: 0, latency_ms: null, error_message: null },
  },
  {
    provider: {
      id: 5,
      name: 'openai-staging',
      type: 'OPENAI',
      base_url: 'https://staging.openai.com/v1',
      api_key_masked: '****005',
      enabled: true,
      created_by: 'seed',
      created_at: '2026-08-05 18:12',
      updated_at: '2026-08-05 18:12',
    },
    model_count: 2,
    health: { status: 'DEGRADED', last_check_at: '2026-08-18 09:30', last_success_at: '2026-08-18 09:30', fail_count: 0, latency_ms: 6500, error_message: null },
  },
])

const columns: TableColumnConfig[] = [
  { label: '名称', prop: 'name', width: 160, slot: 'name' },
  { label: '类型', prop: 'type', width: 110, slot: 'type' },
  { label: 'Base URL', prop: 'base_url', slot: 'baseUrl' },
  { label: 'API Key', prop: 'api_key_masked', width: 150, slot: 'apiKey' },
  { label: '模型数', prop: 'model_count', width: 80 },
  { label: '健康状态', prop: 'health', width: 100, slot: 'health' },
  { label: '状态', prop: 'enabled', width: 80, slot: 'status' },
  { label: '操作', prop: 'actions', width: 230, slot: 'actions' },
]

/** mock API：本地数组经 paginateLocal 包装为 PageResult 契约 */
async function api(query: PageQuery): Promise<PageResult<ProviderRowWithMeta>> {
  await new Promise((resolve) => setTimeout(resolve, 200))
  return paginateLocal(providers.value, query)
}

const tableRef = ref<{ refresh: () => void }>()
const dialogRef = ref<InstanceType<typeof WebAgentFormDialog>>()
const dialogVisible = ref(false)
const editingName = ref<string | null>(null)

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

/** 模拟按需连通性探测：200ms 后随机返回 UP / DEGRADED / DOWN，写回该行 health */
async function handleTestConnection(row: ProviderRowWithMeta): Promise<void> {
  await new Promise((resolve) => setTimeout(resolve, 200))
  const dice = Math.random()
  const nextStatus: ProviderHealthSnapshot['status'] =
    dice < 0.7 ? 'UP' : dice < 0.9 ? 'DEGRADED' : 'DOWN'
  const latencyMs = nextStatus === 'DOWN' ? null : Math.floor(100 + Math.random() * 5000)
  const errorMessage = nextStatus === 'DOWN' ? 'connection refused (mock)' : null
  row.health = {
    status: nextStatus,
    last_check_at: new Date().toISOString().replace('T', ' ').slice(0, 16),
    last_success_at: nextStatus === 'UP' ? new Date().toISOString().replace('T', ' ').slice(0, 16) : row.health.last_success_at,
    fail_count: nextStatus === 'DOWN' ? row.health.fail_count + 1 : 0,
    latency_ms: latencyMs,
    error_message: errorMessage,
  }
  notifySuccess(`已探测：${HEALTH_TAG[nextStatus].label}${latencyMs !== null ? `（${latencyMs}ms）` : ''}`)
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
  await new Promise((resolve) => setTimeout(resolve, 300))

  if (editingName.value !== null) {
    // 编辑：name 不可改，仅修改 type / base_url / auth_config（省略 = 保留）
    const target = providers.value.find((item) => item.provider.name === editingName.value)
    if (target) {
      target.provider.type = type
      target.provider.base_url = baseUrl
      // 仅当 api_key 非空时携带，等价于 PATCH 不带 auth_config → 后端保留原值
      if (apiKey) {
        target.provider.api_key_masked = `****${apiKey.slice(-4)}`
      }
    }
  } else {
    const nextId = providers.value.reduce(
      (max, item) => Math.max(max, item.provider.id),
      0,
    ) + 1
    providers.value.push({
      provider: {
        id: nextId,
        name,
        type,
        base_url: baseUrl,
        api_key_masked: apiKey ? `****${apiKey.slice(-4)}` : '',
        enabled: true,
        created_by: 'user',
        created_at: new Date().toISOString().replace('T', ' ').slice(0, 16),
        updated_at: null,
      },
      model_count: 0,
      health: { status: 'UNKNOWN', last_check_at: null, last_success_at: null, fail_count: 0, latency_ms: null, error_message: null },
    })
  }

  dialogRef.value?.setSubmitting(false)
  dialogRef.value?.close()
  notifySuccess(`已保存：${name}`)
  tableRef.value?.refresh()
}

function handleDelete(row: ProviderRowWithMeta): void {
  const confirmAndDelete = useConfirm(
    `确定删除提供商「${row.provider.name}」吗？`,
    async () => {
      providers.value = providers.value.filter(
        (item) => item.provider.name !== row.provider.name,
      )
    },
    { title: '删除确认', successMessage: '删除成功' },
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
            删除
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
        <el-form-item label="名称" prop="name">
          <el-input
            v-model="form.name"
            placeholder="请输入提供商名称"
            :disabled="mode === 'edit'"
          />
        </el-form-item>
        <el-form-item label="类型" prop="type">
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