<script setup lang="ts">
/**
 * 技能管理页：基于后端 Skill CRUD 契约（`/skills` + `/skills/page` + 单条 PATCH/DELETE，
 * `SkillRead` 行字段 name/description/content_hash/version/created_by）的管理视图。
 *
 * 数据源：`listSkillsPage(query)` 走真实后端（`/skills/page`），返回
 * `PageResult<SkillRow>`；列表 / CRUD 全部走 `@/api/assets` 的函数。
 *
 * 表单（共用 WebAgentFormDialog）：
 * - 创建态（mode='create'）：name / description / body 三个必填字段；
 * - 编辑态（mode='edit'）：name 字段 disabled，仅修改 description（与后端
 *   PATCH `description-only` 语义一致；body 是大段 markdown，单独走
 *   查看正文弹窗只读展示，避免一次性回填大段内容导致误改/丢失）。
 */
import { ref, watch } from 'vue'
import type { FormRules } from 'element-plus'
import { Search } from '@element-plus/icons-vue'

import WebAgentFormDialog from '@/components/WebAgentFormDialog.vue'
import WebAgentTable from '@/components/WebAgentTable.vue'
import type { TableColumnConfig } from '@/components/WebAgentTable.vue'
import SkillContentDialog from '@/views/skill/SkillContentDialog.vue'
import SkillGenerateDialog from '@/views/skill/SkillGenerateDialog.vue'
import SkillRefreshReportDialog from '@/views/skill/SkillRefreshReportDialog.vue'
import SkillWorkspaceSyncDialog from '@/views/skill/SkillWorkspaceSyncDialog.vue'
import {
  applySkillWorkspaceSync,
  createSkill,
  deleteSkill,
  getSkillContent,
  listSkillsPage,
  patchSkill,
  planSkillWorkspaceSync,
  refreshAllSkills,
  refreshSkill,
  type SkillCreatePayload,
  type SkillPatchPayload,
  type SkillRefreshReport,
  type SkillRow,
  type SkillSyncReport,
} from '@/api/assets'
import { useConfirm } from '@/composables/useConfirm'
import { useRequest } from '@/composables/useRequest'
import { notifySuccess } from '@/utils/notify'
import type { PageQuery, PageResult } from '@/types'

/** Skill 名规则：与后端 `_name_field` 共享（与 SubAgent/AgentApp/McpServer 一致） */
const NAME_RE = /^[a-z0-9][a-z0-9_-]*$/

const columns: TableColumnConfig[] = [
  { label: '名称', prop: 'name', width: 180, slot: 'name' },
  { label: '描述', prop: 'description', slot: 'description' },
  { label: '版本', prop: 'version', width: 80 },
  { label: '创建者', prop: 'created_by', width: 140 },
  { label: '操作', prop: 'actions', width: 320, slot: 'actions' },
]

/** 表格数据源：直接透传到 listSkillsPage，由后端做分页 / 关键字过滤 */
async function api(query: PageQuery): Promise<PageResult<SkillRow>> {
  return listSkillsPage(query)
}

const keyword = ref('')
/**
 * 表格 query 载荷：仅在 300ms 防抖命中后才整体替换引用，
 * 让 WebAgentTable 内部的 deep watch 只在关键字真正变化时触发 fetchData。
 * 如果直接写 `:query="{ keyword: debouncedKeyword }"`，Vue 每次 render
 * 都会创建新对象字面量，触发额外 fetch。
 */
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
const editingName = ref<string | null>(null)

/** 查看正文弹窗状态 */
const contentDialogVisible = ref(false)
const contentDialogName = ref<string | null>(null)

/** 自动生成正文弹窗状态 */
const generateDialogVisible = ref(false)

/** 磁盘同步报告弹窗状态：report 先写入再开弹窗（v-if 保证弹窗拿到非空报告） */
const refreshDialogVisible = ref(false)
const refreshReport = ref<SkillRefreshReport | null>(null)

/**
 * 同步请求托管：loading/error 由 useRequest 收敛，execute 失败返回 null
 * 不外抛（错误提示由统一请求层拦截器弹出）。
 */
const { execute: executeRefreshAll, loading: refreshingAll } = useRequest(() => refreshAllSkills())
const { execute: executeRefreshRow } = useRequest((name: string) => refreshSkill(name))

/** 目录对账弹窗状态：report 先写入再开弹窗（v-if 保证弹窗拿到非空报告） */
const syncDialogVisible = ref(false)
const syncReport = ref<SkillSyncReport | null>(null)
const syncMode = ref<'preview' | 'applied'>('preview')

/** 对账请求托管：syncPlanning 接工具栏按钮 loading；apply 后弹窗原位切换为执行结果 */
const { execute: executePlanSync, loading: syncPlanning } = useRequest(() =>
  planSkillWorkspaceSync(),
)
const { execute: executeApplySync } = useRequest(() => applySkillWorkspaceSync())

/**
 * 打开报告弹窗（全量 / 单条共用）：先写报告再开弹窗。
 * 关闭时保留 report（v-if 已卸载弹窗，下次打开前必然被新报告覆盖）。
 */
function openRefreshReport(report: SkillRefreshReport): void {
  refreshReport.value = report
  refreshDialogVisible.value = true
}

/** 工具栏「同步磁盘」：全量从 DB 刷新磁盘副本并展示报告 */
async function handleRefreshAll(): Promise<void> {
  const report = await executeRefreshAll()
  if (report) {
    openRefreshReport(report)
  }
}

/** 行内「同步」：单条从 DB 刷新磁盘副本并展示报告 */
async function handleRefreshRow(row: SkillRow): Promise<void> {
  const report = await executeRefreshRow(row.name)
  if (report) {
    openRefreshReport(report)
  }
}

/** 工具栏「目录对账」：dry-run 预览（零写入）并打开对账弹窗 */
async function handleWorkspaceSyncPreview(): Promise<void> {
  const report = await executePlanSync()
  if (report) {
    syncReport.value = report
    syncMode.value = 'preview'
    syncDialogVisible.value = true
  }
}

/** 对账弹窗「应用同步」：执行对账，弹窗原位切换为执行结果并刷新列表（导入会产生新行） */
async function handleWorkspaceSyncApply(): Promise<void> {
  const report = await executeApplySync()
  if (report) {
    syncReport.value = report
    syncMode.value = 'applied'
    tableRef.value?.refresh()
    notifySuccess(
      `目录对账完成：重建 ${report.rewritten} / 导入 ${report.imported} / 无效 ${report.invalid}`,
    )
  }
}

/** 创建场景下当前填写的 description（用于「自动生成」按钮回填 LLM 输入） */
const currentDescription = ref('')

interface SkillFormShape {
  name: string
  description: string
  body: string
}

/** WebAgentFormDialog.open() 不传 data 时为 {}，故字段全部 optional */
type SubmitFormShape = Partial<SkillFormShape>

const rules: FormRules = {
  name: [
    { required: true, message: '请输入技能名称', trigger: 'blur' },
    {
      pattern: NAME_RE,
      message: '以小写字母或数字开头，后续仅含小写字母/数字/下划线/连字符',
      trigger: 'blur',
    },
    { max: 64, message: '名称长度不能超过 64 个字符', trigger: 'blur' },
  ],
  description: [{ required: true, message: '请输入描述', trigger: 'blur' }],
  body: [{ required: true, message: '请输入 SKILL.md 正文', trigger: 'blur' }],
}

function handleCreate(): void {
  editingName.value = null
  currentDescription.value = ''
  dialogRef.value?.open()
}

/**
 * 打开编辑弹窗：先开弹窗（body 字段显示「加载中…」占位文案），再异步拉 body 内容回填，
 * 避免大段 markdown 一次性手动复制到表单。错误由统一拦截器提示。
 */
async function handleEdit(row: SkillRow): Promise<void> {
  editingName.value = row.name
  currentDescription.value = row.description
  dialogRef.value?.open({
    name: row.name,
    description: row.description,
    body: '加载中…',
  } satisfies SkillFormShape)
  try {
    const content = await getSkillContent(row.name)
    const form = dialogRef.value?.getForm()
    if (form) {
      form.body = content.content
    }
  } catch {
    // 拉取失败：清空 body 让用户手动重试（错误提示已由全局拦截器弹）
    const form = dialogRef.value?.getForm()
    if (form) {
      form.body = ''
    }
  }
}

/** 打开 LLM 草稿生成弹窗：把当前 description 输入回填给生成弹窗 */
function handleOpenGenerate(currentDesc: string): void {
  currentDescription.value = (currentDesc ?? '').trim()
  generateDialogVisible.value = true
}

/**
 * LLM 草稿生成成功回调：把 draft 写入创建弹窗的 body 字段。
 * 通过 WebAgentFormDialog.getForm() 拿到的 reactive form 引用直接赋值，
 * 配合 v-model="form.body" 的双向绑定立即触发 textarea 重渲染。
 */
function handleGenerateDraft(draft: string): void {
  const form = dialogRef.value?.getForm()
  if (form) {
    form.body = draft
  }
}

function handleViewContent(row: SkillRow): void {
  contentDialogName.value = row.name
  contentDialogVisible.value = true
}

async function handleSubmit(data: Record<string, unknown>): Promise<void> {
  const form = data as unknown as SubmitFormShape
  const name = (form.name ?? '').trim()
  const description = (form.description ?? '').trim()
  const body = (form.body ?? '').trim()

  // 必填守卫（WebAgentFormDialog.validate 已拦截，这里双保险）— 静默丢弃
  if (!name || !description) {
    return
  }
  // 创建态与编辑态都要求 body 非空；空字符串表示「加载失败 + 用户未填写」场景
  if (!body) {
    return
  }

  dialogRef.value?.setSubmitting(true)
  try {
    if (editingName.value !== null) {
      // 编辑：name 不可改，同时更新 description + body（与后端 PATCH 语义一致；
      // description 与 body 至少传一项避免后端 422 "nothing to update"）
      const payload: SkillPatchPayload = { description, body }
      await patchSkill(editingName.value, payload)
    } else {
      const payload: SkillCreatePayload = { name, description, body }
      await createSkill(payload)
    }
  } finally {
    dialogRef.value?.setSubmitting(false)
  }

  dialogRef.value?.close()
  notifySuccess(`已保存：${name}`)
  tableRef.value?.refresh()
}

function handleDelete(row: SkillRow): void {
  const confirmAndDelete = useConfirm(
    `确定删除技能「${row.name}」吗？该操作不可恢复，并将清理该技能的所有用户副本。`,
    async () => {
      await deleteSkill(row.name)
    },
    { title: '删除技能', successMessage: '已删除' },
  )
  void confirmAndDelete().then((done) => {
    if (done) tableRef.value?.refresh()
  })
}

/** 描述列截断展示：超过 120 字符显示省略号（鼠标 hover 看 title 完整内容） */
function truncatedDescription(text: string): string {
  if (text.length <= 120) return text
  return `${text.slice(0, 120)}…`
}
</script>

<template>
  <div class="page-view">
    <header class="page-view__header">
      <div>
        <h1 class="page-view__title">技能管理</h1>
        <p class="page-view__desc">维护 Agent 可调用的技能资产。</p>
      </div>
      <div class="page-view__actions">
        <el-button class="app-btn app-btn--primary" @click="handleCreate">
          新建技能
        </el-button>
      </div>
    </header>

    <section class="content-card page-view__body">
      <div class="skill-toolbar">
        <el-input
          v-model="keyword"
          class="skill-toolbar__search"
          placeholder="按名称模糊搜索"
          clearable
        >
          <template #prefix>
            <el-icon><Search /></el-icon>
          </template>
        </el-input>
        <el-button
          class="app-btn app-btn--secondary skill-toolbar__refresh"
          :loading="refreshingAll"
          @click="handleRefreshAll"
        >
          同步磁盘
        </el-button>
        <el-button
          class="app-btn app-btn--secondary skill-toolbar__sync"
          :loading="syncPlanning"
          @click="handleWorkspaceSyncPreview"
        >
          目录对账
        </el-button>
      </div>

      <WebAgentTable
        ref="tableRef"
        :columns="columns"
        :api="api"
        :query="queryPayload"
      >
        <template #name="{ row }">
          <span class="skill-name">{{ (row as SkillRow).name }}</span>
        </template>
        <template #description="{ row }">
          <span :title="(row as SkillRow).description">
            {{ truncatedDescription((row as SkillRow).description) || '—' }}
          </span>
        </template>
        <template #actions="{ row }">
          <el-button link type="primary" size="small" @click="handleViewContent(row as SkillRow)">
            查看正文
          </el-button>
          <el-button link type="primary" size="small" @click="handleEdit(row as SkillRow)">
            编辑
          </el-button>
          <el-button link type="success" size="small" @click="handleRefreshRow(row as SkillRow)">
            同步
          </el-button>
          <el-button link type="danger" size="small" @click="handleDelete(row as SkillRow)">
            删除
          </el-button>
        </template>
      </WebAgentTable>
    </section>

    <WebAgentFormDialog
      ref="dialogRef"
      v-model="dialogVisible"
      :title="editingName === null ? '新建技能' : '编辑技能'"
      :rules="rules"
      @submit="handleSubmit"
    >
      <template #default="{ form, mode }">
        <el-form-item label="名称" prop="name">
          <el-input
            v-model="form.name"
            placeholder="请输入技能名称"
            :disabled="mode === 'edit'"
          />
        </el-form-item>
        <el-form-item label="描述" prop="description">
          <el-input
            v-model="form.description"
            type="textarea"
            :rows="2"
            placeholder="请输入技能描述"
          />
        </el-form-item>
        <el-form-item label="正文" prop="body">
          <div class="skill-body-toolbar">
            <span class="skill-body-toolbar__tip">
              {{ mode === 'create'
                ? '直接填写正文，或由 LLM 起草一份草稿后再修改'
                : '修改正文内容，或由 LLM 重新起草（会覆盖当前正文）' }}
            </span>
            <el-button
              class="app-btn app-btn--secondary skill-body-toolbar__btn"
              size="small"
              @click="handleOpenGenerate(form.description as string)"
            >
              自动生成
            </el-button>
          </div>
          <el-input
            v-model="form.body"
            type="textarea"
            :rows="12"
            placeholder="请输入 SKILL.md 正文（Markdown 格式）"
          />
        </el-form-item>
      </template>
    </WebAgentFormDialog>

    <SkillContentDialog
      v-if="contentDialogName"
      v-model="contentDialogVisible"
      :name="contentDialogName"
    />

    <SkillGenerateDialog
      v-model="generateDialogVisible"
      :description="currentDescription"
      @generated="handleGenerateDraft"
    />

    <SkillRefreshReportDialog
      v-if="refreshReport"
      v-model="refreshDialogVisible"
      :report="refreshReport"
    />

    <SkillWorkspaceSyncDialog
      v-if="syncReport"
      v-model="syncDialogVisible"
      :report="syncReport"
      :mode="syncMode"
      @apply="handleWorkspaceSyncApply"
    />
  </div>
</template>

<style scoped>
.skill-name {
  font-weight: 600;
  color: var(--color-text-primary);
}
.skill-toolbar {
  display: flex;
  align-items: center;
  justify-content: flex-start;
  margin-bottom: 16px;
}
.skill-toolbar__search {
  width: 280px;
}
.skill-toolbar__refresh {
  margin-left: 12px;
}
.skill-toolbar__sync {
  margin-left: 12px;
}
.skill-body-toolbar {
  display: flex;
  align-items: center;
  gap: 12px;
  margin-bottom: 8px;
}
.skill-body-toolbar__btn {
  /* 紧凑按钮尺寸，避免占用正文编辑区视觉重心 */
}
.skill-body-toolbar__tip {
  font-size: 12px;
  color: var(--color-text-secondary);
  line-height: 1.4;
}
</style>