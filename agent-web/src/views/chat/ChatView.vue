<script setup lang="ts">
/**
 * 会话列表页（G3 spec-g3-session §11.6 议题 8）：
 * 基于后端 /sessions 契约（snake_case 行字段）的分页列表 + 新建 / 重命名 /
 * 级联删除 / 历史导出。聊天区（消息流 / 流式 / HIL）归 G4 接入。
 *
 * 数据源：`listSessions(query)` 走真实后端（`GET /sessions` 资源根分页，
 * 无 `/page` 后缀——议题 3 RESTful 定案），返回 `PageResult<SessionRead>`。
 *
 * 交互：
 * - agent_app 过滤下拉（选项来源 listAgentApps，仅展示 published）；
 * - 新建 / 重命名共用 WebAgentFormDialog（编辑模式锁定 agent_app）；
 * - 删除走 useConfirm，提示「将级联删除对话记录」（L1 checkpoint → L2 JSONL
 *   → L0 行，后端尽力清理语义）；
 * - 行内导出：json / jsonl 二选一下拉，blob → a[download] 触发浏览器保存
 *   （非信封文件下载，§11.5.3 先例）。
 */
import { computed, onMounted, ref } from 'vue'
import type { FormRules } from 'element-plus'

import WebAgentFormDialog from '@/components/WebAgentFormDialog.vue'
import WebAgentTable from '@/components/WebAgentTable.vue'
import type { TableColumnConfig } from '@/components/WebAgentTable.vue'
import { listAgentApps, type AgentAppRow } from '@/api/assets'
import {
  createSession,
  deleteSession,
  exportSessionHistory,
  listSessions,
  updateSession,
  type SessionRead,
} from '@/api/sessions'
import { useConfirm } from '@/composables/useConfirm'
import { notifySuccess } from '@/utils/notify'
import type { PageQuery, PageResult } from '@/types'

const columns: TableColumnConfig[] = [
  { label: '会话名称', prop: 'name', width: 220, slot: 'name' },
  { label: 'Agent 应用', prop: 'agent_app_id', width: 180, slot: 'agentApp' },
  { label: '消息数', prop: 'message_count', width: 100, slot: 'messageCount' },
  { label: '创建时间', prop: 'created_at', width: 180, slot: 'createdAt' },
  { label: '操作', prop: 'actions', width: 240, slot: 'actions' },
]

/** 表格数据源：透传到 listSessions；agentAppId 过滤经 WebAgentTable query 注入 */
async function api(query: PageQuery): Promise<PageResult<SessionRead>> {
  return listSessions(query as PageQuery & { agentAppId?: number })
}

const tableRef = ref<{ refresh: () => void }>()

// ---------------------------------------------------------------------------
// agent_app 过滤与下拉选项
// ---------------------------------------------------------------------------

/** 过滤下拉选中值；null 表示不过滤 */
const filterAppId = ref<number | null>(null)

/** WebAgentTable 的 query prop：变化时重置到第一页并重新请求 */
const tableQuery = computed<Record<string, unknown>>(() => ({
  agentAppId: filterAppId.value ?? undefined,
}))

/** 仅 published 的 AgentApp 可新建会话（后端 associate 会拒绝未发布应用） */
const appOptions = ref<AgentAppRow[]>([])

async function loadAppOptions(): Promise<void> {
  try {
    const rows = await listAgentApps()
    appOptions.value = rows.filter((row) => row.status === 'published')
  } catch {
    appOptions.value = []
  }
}

onMounted(() => {
  void loadAppOptions()
})

/** id → 名称映射：列表列展示友好名（历史行 app 已删时降级显示 id） */
const appNameById = computed<Map<number, string>>(() => {
  const map = new Map<number, string>()
  for (const row of appOptions.value) {
    map.set(row.id, row.name)
  }
  return map
})

function agentAppLabel(row: SessionRead): string {
  if (row.agent_app_id === null) {
    return '—'
  }
  return appNameById.value.get(row.agent_app_id) ?? `#${row.agent_app_id}`
}

/** message_count 仅详情端点填充，列表恒为 null → 「—」 */
function messageCountLabel(row: SessionRead): string {
  return row.message_count === null ? '—' : String(row.message_count)
}

/** created_at 展示：截到秒（后端 ISO 8601 带时区，避免本地时区歧义） */
function createdAtLabel(row: SessionRead): string {
  return row.created_at.slice(0, 19).replace('T', ' ')
}

// ---------------------------------------------------------------------------
// 新建 / 重命名（共用 WebAgentFormDialog）
// ---------------------------------------------------------------------------

const dialogRef = ref<InstanceType<typeof WebAgentFormDialog>>()
const dialogVisible = ref(false)
/** 编辑模式持有目标 session_id；null 表示新建 */
const editingSessionId = ref<string | null>(null)

interface SessionFormShape {
  agent_app_id: number | null
  name: string
}

const rules: FormRules = {
  agent_app_id: [{ required: true, message: '请选择 Agent 应用', trigger: 'change' }],
}

function handleCreate(): void {
  editingSessionId.value = null
  dialogRef.value?.open()
}

function handleRename(row: SessionRead): void {
  editingSessionId.value = row.session_id
  dialogRef.value?.open({
    agent_app_id: row.agent_app_id,
    name: row.name,
  } satisfies SessionFormShape)
}

async function handleSubmit(data: Record<string, unknown>): Promise<void> {
  const form = data as unknown as Partial<SessionFormShape>
  dialogRef.value?.setSubmitting(true)
  try {
    if (editingSessionId.value !== null) {
      const name = (form.name ?? '').trim()
      if (!name) {
        return
      }
      await updateSession(editingSessionId.value, { name })
    } else {
      if (form.agent_app_id === null || form.agent_app_id === undefined) {
        return
      }
      await createSession({
        agent_app_id: Number(form.agent_app_id),
        name: (form.name ?? '').trim(),
      })
    }
  } finally {
    dialogRef.value?.setSubmitting(false)
  }

  dialogRef.value?.close()
  notifySuccess(editingSessionId.value !== null ? '已重命名' : '已创建会话')
  editingSessionId.value = null
  tableRef.value?.refresh()
}

// ---------------------------------------------------------------------------
// 删除（级联提示）与导出（文件下载）
// ---------------------------------------------------------------------------

function handleDelete(row: SessionRead): void {
  const confirmAndDelete = useConfirm(
    `确定删除会话「${row.name || row.session_id}」吗？将级联删除对话记录（checkpoint 与消息历史），该操作不可恢复。`,
    async () => {
      await deleteSession(row.session_id)
    },
    { title: '删除确认', successMessage: '已删除' },
  )
  void confirmAndDelete().then((done) => {
    if (done) tableRef.value?.refresh()
  })
}

/** blob → a[download] 触发浏览器保存；文件名与后端 Content-Disposition 对齐 */
async function handleExport(row: SessionRead, format: 'json' | 'jsonl'): Promise<void> {
  let blob: Blob
  try {
    blob = await exportSessionHistory(row.session_id, format)
  } catch {
    // 错误提示由统一请求层全局拦截器承担（404 越权 / 不存在同文案）
    return
  }
  const url = URL.createObjectURL(blob)
  const anchor = document.createElement('a')
  anchor.href = url
  anchor.download = `${row.session_id}.${format}`
  anchor.click()
  URL.revokeObjectURL(url)
  notifySuccess('已导出会话记录')
}
</script>

<template>
  <div class="page-view">
    <header class="page-view__header">
      <div>
        <h1 class="page-view__title">对话</h1>
        <p class="page-view__desc">
          管理与 Agent 应用的会话：新建、重命名、导出历史与级联删除。
        </p>
      </div>
      <div class="page-view__actions">
        <el-select
          v-model="filterAppId"
          class="chat-app-filter"
          clearable
          placeholder="按 Agent 应用过滤"
        >
          <el-option
            v-for="option in appOptions"
            :key="option.id"
            :label="option.name"
            :value="option.id"
          />
        </el-select>
        <el-button class="app-btn app-btn--primary" @click="handleCreate">
          新建会话
        </el-button>
      </div>
    </header>

    <section class="content-card page-view__body">
      <WebAgentTable ref="tableRef" :columns="columns" :api="api" :query="tableQuery">
        <template #name="{ row }">
          <span class="session-name">{{ (row as SessionRead).name || '未命名会话' }}</span>
        </template>
        <template #agentApp="{ row }">
          <span>{{ agentAppLabel(row as SessionRead) }}</span>
        </template>
        <template #messageCount="{ row }">
          <span>{{ messageCountLabel(row as SessionRead) }}</span>
        </template>
        <template #createdAt="{ row }">
          <span class="session-created-at">{{ createdAtLabel(row as SessionRead) }}</span>
        </template>
        <template #actions="{ row }">
          <el-button link type="primary" size="small" @click="handleRename(row as SessionRead)">
            重命名
          </el-button>
          <el-dropdown @command="(format: string) => handleExport(row as SessionRead, format as 'json' | 'jsonl')">
            <el-button link type="primary" size="small">导出</el-button>
            <template #dropdown>
              <el-dropdown-menu>
                <el-dropdown-item command="json">JSON</el-dropdown-item>
                <el-dropdown-item command="jsonl">JSONL</el-dropdown-item>
              </el-dropdown-menu>
            </template>
          </el-dropdown>
          <el-button link type="danger" size="small" @click="handleDelete(row as SessionRead)">
            删除
          </el-button>
        </template>
      </WebAgentTable>
    </section>

    <WebAgentFormDialog
      ref="dialogRef"
      v-model="dialogVisible"
      title="会话信息"
      width="480px"
      :rules="rules"
      @submit="handleSubmit"
    >
      <template #default="{ form, mode }">
        <el-form-item label="Agent 应用" prop="agent_app_id">
          <el-select
            v-model="form.agent_app_id"
            :disabled="mode === 'edit'"
            no-data-text="暂无已发布的 Agent 应用"
            placeholder="选择要对话的已发布应用"
            style="width: 100%"
          >
            <el-option
              v-for="option in appOptions"
              :key="option.id"
              :label="option.name"
              :value="option.id"
            />
          </el-select>
        </el-form-item>
        <el-form-item label="会话名称" prop="name">
          <el-input v-model="form.name" placeholder="可选；留空创建未命名会话" />
        </el-form-item>
      </template>
    </WebAgentFormDialog>
  </div>
</template>

<style scoped>
.chat-app-filter {
  width: 220px;
}
.session-name {
  font-weight: 600;
  color: var(--color-text-primary);
}
.session-created-at {
  color: var(--color-text-secondary);
  font-family: var(--app-font-display);
  letter-spacing: 0.02em;
}
</style>
