<script setup lang="ts">
/**
 * 模型提供商管理页：基于 WebAgent 公共组件库的本地 mock CRUD 示例。
 * 零真实 API —— 数据存于组件内 ref，经 paginateLocal 适配 PageResult 契约。
 */
import { ref } from 'vue'
import type { FormRules } from 'element-plus'

import WebAgentFormDialog from '@/components/WebAgentFormDialog.vue'
import WebAgentTable from '@/components/WebAgentTable.vue'
import type { TableColumnConfig } from '@/components/WebAgentTable.vue'
import { useConfirm } from '@/composables/useConfirm'
import { notifySuccess } from '@/utils/notify'
import { paginateLocal } from '@/utils/paginate'
import type { PageQuery, PageResult } from '@/types'

type ProviderType = 'OpenAI' | 'Claude' | 'Gemini' | 'Ollama'

interface ProviderRow {
  id: number
  name: string
  type: ProviderType
  apiKey: string
  baseUrl: string
  /** 状态：启用 / 禁用 */
  enabled: boolean
  createdAt: string
}

const PROVIDER_TYPES: ProviderType[] = ['OpenAI', 'Claude', 'Gemini', 'Ollama']

const providers = ref<ProviderRow[]>([
  {
    id: 1,
    name: 'openai-prod',
    type: 'OpenAI',
    apiKey: 'sk-mock-openai-001',
    baseUrl: 'https://api.openai.com/v1',
    enabled: true,
    createdAt: '2026-06-11 09:20',
  },
  {
    id: 2,
    name: 'claude-main',
    type: 'Claude',
    apiKey: 'sk-ant-mock-002',
    baseUrl: 'https://api.anthropic.com',
    enabled: true,
    createdAt: '2026-06-24 15:40',
  },
  {
    id: 3,
    name: 'gemini-lab',
    type: 'Gemini',
    apiKey: 'aiza-mock-003',
    baseUrl: 'https://generativelanguage.googleapis.com',
    enabled: false,
    createdAt: '2026-07-02 14:30',
  },
  {
    id: 4,
    name: 'ollama-local',
    type: 'Ollama',
    apiKey: 'ollama-mock-004',
    baseUrl: 'http://localhost:11434',
    enabled: true,
    createdAt: '2026-07-18 10:05',
  },
  {
    id: 5,
    name: 'openai-staging',
    type: 'OpenAI',
    apiKey: 'sk-mock-openai-005',
    baseUrl: 'https://staging.openai.com/v1',
    enabled: true,
    createdAt: '2026-08-05 18:12',
  },
])

const columns: TableColumnConfig[] = [
  { label: '名称', prop: 'name', width: 150 },
  { label: '类型', prop: 'type', width: 100 },
  { label: 'Base URL', prop: 'baseUrl' },
  { label: '状态', prop: 'enabled', width: 90, slot: 'status' },
  { label: '创建时间', prop: 'createdAt', width: 170 },
  { label: '操作', prop: 'actions', width: 150, slot: 'actions' },
]

/** mock API：本地数组经 paginateLocal 包装为 PageResult 契约 */
async function api(query: PageQuery): Promise<PageResult<ProviderRow>> {
  await new Promise((resolve) => setTimeout(resolve, 200))
  return paginateLocal(providers.value, query)
}

const tableRef = ref<{ refresh: () => void }>()
const dialogRef = ref<InstanceType<typeof WebAgentFormDialog>>()
const dialogVisible = ref(false)
const editingId = ref<number | null>(null)

const rules: FormRules = {
  name: [{ required: true, message: '请输入提供商名称', trigger: 'blur' }],
  type: [{ required: true, message: '请选择类型', trigger: 'change' }],
  apiKey: [{ required: true, message: '请输入 API Key', trigger: 'blur' }],
  baseUrl: [{ required: true, message: '请输入 Base URL', trigger: 'blur' }],
}

function handleCreate(): void {
  editingId.value = null
  dialogRef.value?.open()
}

function handleEdit(row: ProviderRow): void {
  editingId.value = row.id
  dialogRef.value?.open({ ...row })
}

function formatNow(): string {
  const now = new Date()
  const pad = (value: number) => String(value).padStart(2, '0')
  return `${now.getFullYear()}-${pad(now.getMonth() + 1)}-${pad(now.getDate())} ${pad(now.getHours())}:${pad(now.getMinutes())}`
}

async function handleSubmit(data: Record<string, unknown>): Promise<void> {
  dialogRef.value?.setSubmitting(true)
  // 模拟 API 请求（零真实网络）
  await new Promise((resolve) => setTimeout(resolve, 300))

  if (editingId.value !== null) {
    const target = providers.value.find((item) => item.id === editingId.value)
    if (target) {
      // 合并更新，保留状态与创建时间
      Object.assign(target, data, {
        enabled: target.enabled,
        createdAt: target.createdAt,
      })
    }
  } else {
    const nextId = providers.value.reduce((max, item) => Math.max(max, item.id), 0) + 1
    providers.value.push({
      ...(data as Omit<ProviderRow, 'id' | 'enabled' | 'createdAt'>),
      id: nextId,
      enabled: true,
      createdAt: formatNow(),
    })
  }

  dialogRef.value?.setSubmitting(false)
  dialogRef.value?.close()
  notifySuccess(`已保存：${String(data.name ?? '')}`)
  tableRef.value?.refresh()
}

function handleDelete(row: ProviderRow): void {
  const confirmAndDelete = useConfirm(
    `确定删除提供商「${row.name}」吗？`,
    async () => {
      providers.value = providers.value.filter((item) => item.id !== row.id)
    },
    { title: '删除确认', successMessage: '删除成功' },
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
        <template #status="{ row }">
          <el-tag :type="row.enabled ? 'success' : 'info'" size="small">
            {{ row.enabled ? '启用' : '禁用' }}
          </el-tag>
        </template>
        <template #actions="{ row }">
          <el-button link type="primary" size="small" @click="handleEdit(row)">
            编辑
          </el-button>
          <el-button link type="danger" size="small" @click="handleDelete(row)">
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
      <template #default="{ form }">
        <el-form-item label="名称" prop="name">
          <el-input v-model="form.name" placeholder="请输入提供商名称" />
        </el-form-item>
        <el-form-item label="类型" prop="type">
          <el-select v-model="form.type" placeholder="请选择类型">
            <el-option v-for="item in PROVIDER_TYPES" :key="item" :label="item" :value="item" />
          </el-select>
        </el-form-item>
        <el-form-item label="API Key" prop="apiKey">
          <el-input v-model="form.apiKey" placeholder="请输入 API Key" show-password />
        </el-form-item>
        <el-form-item label="Base URL" prop="baseUrl">
          <el-input v-model="form.baseUrl" placeholder="请输入 Base URL" />
        </el-form-item>
      </template>
    </WebAgentFormDialog>
  </div>
</template>
