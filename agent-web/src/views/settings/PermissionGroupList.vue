<script setup lang="ts">
/**
 * 权限组管理页：命名工具审批集合的 CRUD。
 *
 * 数据源：`listPermissionGroupsPage(query)` 走后端分页 + 关键字过滤；
 * CRUD 走 `@/api/permission-groups`。
 *
 * 表单（共用 WebAgentFormDialog）：
 * - name（必填，创建后不可改）
 * - description（可选）
 * - tool_names（多选，来源同 AgentList 的工具目录：builtin + mcp 分组）
 */
import { onMounted, ref, watch } from 'vue'
import type { FormRules } from 'element-plus'
import { Search } from '@element-plus/icons-vue'

import WebAgentFormDialog from '@/components/WebAgentFormDialog.vue'
import WebAgentTable from '@/components/WebAgentTable.vue'
import type { TableColumnConfig } from '@/components/WebAgentTable.vue'
import {
  createPermissionGroup,
  deletePermissionGroup,
  listPermissionGroupsPage,
  patchPermissionGroup,
  type PermissionGroupCreatePayload,
  type PermissionGroupPatchPayload,
  type PermissionGroupRow,
} from '@/api/permission-groups'
import { listToolCatalog, type ToolCatalogEntry } from '@/api/mcp'
import { useConfirm } from '@/composables/useConfirm'
import { notifySuccess } from '@/utils/notify'
import type { PageQuery, PageResult } from '@/types'

const NAME_RE = /^[a-z0-9][a-z0-9_-]*$/

const columns: TableColumnConfig[] = [
  { label: '名称', prop: 'name', width: 180, slot: 'name' },
  { label: '描述', prop: 'description', slot: 'description' },
  { label: '工具数', prop: 'tool_count', width: 80, slot: 'tool_count' },
  { label: '创建者', prop: 'created_by', width: 140 },
  { label: '操作', prop: 'actions', width: 160, slot: 'actions' },
]

async function api(query: PageQuery): Promise<PageResult<PermissionGroupRow>> {
  return listPermissionGroupsPage(query)
}

const keyword = ref('')
const queryPayload = ref<Record<string, unknown>>({ keyword: '' })
let debounceTimer: ReturnType<typeof setTimeout> | null = null
watch(keyword, (value) => {
  if (debounceTimer !== null) clearTimeout(debounceTimer)
  debounceTimer = setTimeout(() => {
    queryPayload.value = { keyword: value }
    debounceTimer = null
  }, 300)
})

const tableRef = ref<{ refresh: () => void }>()
const dialogRef = ref<InstanceType<typeof WebAgentFormDialog>>()
const dialogVisible = ref(false)
const editingId = ref<number | null>(null)

interface ToolOption {
  value: string
  label: string
  group: 'builtin' | 'mcp'
}
const toolOptions = ref<ToolOption[]>([])
const optionsLoading = ref(false)

function projectCatalog(entries: ToolCatalogEntry[]): ToolOption[] {
  return entries.map((entry) => {
    if (entry.source === 'mcp' && entry.server) {
      const prefix = `${entry.server}__`
      const bareName = entry.name.startsWith(prefix) ? entry.name.slice(prefix.length) : entry.name
      return { value: entry.name, label: `${entry.server} / ${bareName}`, group: 'mcp' }
    }
    return { value: entry.name, label: entry.name, group: 'builtin' }
  })
}

async function loadToolOptions(): Promise<void> {
  optionsLoading.value = true
  try {
    const entries = await listToolCatalog()
    toolOptions.value = projectCatalog(entries)
  } catch {
    toolOptions.value = []
  } finally {
    optionsLoading.value = false
  }
}

onMounted(() => {
  void loadToolOptions()
})

interface PermissionGroupFormShape {
  name: string
  description: string
  tool_names: string[]
}

type SubmitFormShape = Partial<PermissionGroupFormShape>

const rules: FormRules = {
  name: [
    { required: true, message: '请输入权限组名称', trigger: 'blur' },
    {
      pattern: NAME_RE,
      message: '以小写字母或数字开头，后续仅含小写字母/数字/下划线/连字符',
      trigger: 'blur',
    },
    { max: 64, message: '名称长度不能超过 64 个字符', trigger: 'blur' },
  ],
}

function handleCreate(): void {
  editingId.value = null
  dialogRef.value?.open()
}

function handleEdit(row: PermissionGroupRow): void {
  editingId.value = row.id
  dialogRef.value?.open({
    name: row.name,
    description: row.description,
    tool_names: [...row.tool_names],
  } satisfies PermissionGroupFormShape)
}

async function handleSubmit(data: Record<string, unknown>): Promise<void> {
  const form = data as unknown as SubmitFormShape
  const name = (form.name ?? '').trim()
  const description = (form.description ?? '').trim()
  const toolNames = Array.isArray(form.tool_names) ? [...form.tool_names] : []

  if (!name) return

  dialogRef.value?.setSubmitting(true)
  try {
    if (editingId.value !== null) {
      const payload: PermissionGroupPatchPayload = { description, tool_names: toolNames }
      await patchPermissionGroup(editingId.value, payload)
    } else {
      const payload: PermissionGroupCreatePayload = { name, description, tool_names: toolNames }
      await createPermissionGroup(payload)
    }
  } finally {
    dialogRef.value?.setSubmitting(false)
  }

  dialogRef.value?.close()
  notifySuccess(`已保存：${name}`)
  tableRef.value?.refresh()
}

function handleDelete(row: PermissionGroupRow): void {
  const confirmAndDelete = useConfirm(
    `确定删除权限组「${row.name}」吗？已绑定该权限组的 Agent 将失去审批配置。`,
    async () => {
      await deletePermissionGroup(row.id)
    },
    { title: '删除权限组', successMessage: '已删除' },
  )
  void confirmAndDelete().then((done) => {
    if (done) tableRef.value?.refresh()
  })
}

function truncatedDescription(text: string): string {
  if (!text) return '—'
  if (text.length <= 120) return text
  return `${text.slice(0, 120)}…`
}
</script>

<template>
  <div class="page-view">
    <header class="page-view__header">
      <div>
        <h1 class="page-view__title">权限组管理</h1>
        <p class="page-view__desc">维护命名工具审批集合，Agent 可绑定权限组复用审批配置。</p>
      </div>
      <div class="page-view__actions">
        <el-button class="app-btn app-btn--primary" @click="handleCreate">
          新建权限组
        </el-button>
      </div>
    </header>

    <section class="content-card page-view__body">
      <div class="pg-toolbar">
        <el-input
          v-model="keyword"
          class="pg-toolbar__search"
          placeholder="按名称/描述模糊搜索"
          clearable
        >
          <template #prefix>
            <el-icon><Search /></el-icon>
          </template>
        </el-input>
      </div>

      <WebAgentTable
        ref="tableRef"
        :columns="columns"
        :api="api"
        :query="queryPayload"
      >
        <template #name="{ row }">
          <span class="pg-name">{{ (row as PermissionGroupRow).name }}</span>
        </template>
        <template #description="{ row }">
          <span :title="(row as PermissionGroupRow).description">
            {{ truncatedDescription((row as PermissionGroupRow).description) }}
          </span>
        </template>
        <template #tool_count="{ row }">
          {{ (row as PermissionGroupRow).tool_names.length }}
        </template>
        <template #actions="{ row }">
          <el-button link type="primary" size="small" @click="handleEdit(row as PermissionGroupRow)">
            编辑
          </el-button>
          <el-button link type="danger" size="small" @click="handleDelete(row as PermissionGroupRow)">
            删除
          </el-button>
        </template>
      </WebAgentTable>
    </section>

    <WebAgentFormDialog
      ref="dialogRef"
      v-model="dialogVisible"
      :title="editingId === null ? '新建权限组' : '编辑权限组'"
      :rules="rules"
      @submit="handleSubmit"
    >
      <template #default="{ form, mode }">
        <el-form-item label="名称" prop="name">
          <el-input
            v-model="form.name"
            placeholder="请输入权限组名称"
            :disabled="mode === 'edit'"
          />
        </el-form-item>
        <el-form-item label="描述" prop="description">
          <el-input
            v-model="form.description"
            type="textarea"
            :rows="2"
            placeholder="请输入权限组描述"
          />
        </el-form-item>
        <el-form-item label="审批工具" prop="tool_names">
          <el-select
            v-model="form.tool_names"
            multiple
            collapse-tags
            collapse-tags-tooltip
            filterable
            clearable
            :loading="optionsLoading"
            no-data-text="暂无可用工具"
            placeholder="选择需要人工审批的工具"
            style="width: 100%"
          >
            <el-option-group
              v-for="group in (['builtin', 'mcp'] as const)"
              :key="group"
              :label="group === 'builtin' ? '内置工具' : 'MCP 工具'"
            >
              <el-option
                v-for="option in toolOptions.filter((item) => item.group === group)"
                :key="option.value"
                :label="option.label"
                :value="option.value"
              />
            </el-option-group>
          </el-select>
          <div class="agent-form-helptext">
            选中的工具在 Agent 执行时需人工审批（HIL）；留空表示该权限组不强制任何审批。
          </div>
        </el-form-item>
      </template>
    </WebAgentFormDialog>
  </div>
</template>

<style scoped>
.pg-name {
  font-weight: 600;
  color: var(--color-text-primary);
}
.pg-toolbar {
  display: flex;
  align-items: center;
  justify-content: flex-start;
  margin-bottom: 16px;
}
.pg-toolbar__search {
  width: 280px;
}
</style>
