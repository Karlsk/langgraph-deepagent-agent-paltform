<script setup lang="ts">
/**
 * 子代理管理页：基于后端 SubAgent 契约（snake_case 行字段）的 CRUD + 单轮测试视图。
 *
 * 数据源：`listSubAgentsPage(query)` 走真实后端（`/subagents/page`），
 * 返回 `PageResult<SubAgentRow>`。
 * 列表 / CRUD / 单轮测试 全部走 `@/api/subagents` 的函数。
 *
 * 表单：name / description / when_to_use / system_prompt（必填）；
 * allowed_tools（el-select 多选，选项来源 /tools/catalog）/ model（el-select 单选，
 * 拉所有 enabled provider 的 models）/ max_turns（el-input-number 1-50）。
 *
 * 单行操作后 `tableRef.refresh()` 拉全量，保证后端 422 → 401 等场景下数据一致。
 */
import { onMounted, ref } from 'vue'
import type { FormRules } from 'element-plus'

import WebAgentFormDialog from '@/components/WebAgentFormDialog.vue'
import WebAgentTable from '@/components/WebAgentTable.vue'
import type { TableColumnConfig } from '@/components/WebAgentTable.vue'
import {
  createSubAgent,
  deleteSubAgent,
  listSubAgentsPage,
  patchSubAgent,
  type SubAgentCreatePayload,
  type SubAgentPatchPayload,
  type SubAgentRow,
} from '@/api/subagents'
import { listToolCatalog, type ToolCatalogEntry } from '@/api/mcp'
import { listAllProviderModels, type ModelConfigRow } from '@/api/provider'
import { listSkills, type SkillRow } from '@/api/assets'
import { useConfirm } from '@/composables/useConfirm'
import { notifySuccess } from '@/utils/notify'
import type { PageQuery, PageResult } from '@/types'

import SubAgentTestDialog from '@/views/agent/SubAgentTestDialog.vue'
import SubAgentTraceHistoryDialog from '@/views/agent/SubAgentTraceHistoryDialog.vue'
import SubAgentTraceDetailDialog from '@/views/agent/SubAgentTraceDetailDialog.vue'

const columns: TableColumnConfig[] = [
  { label: '名称', prop: 'name', width: 160, slot: 'name' },
  { label: '描述', prop: 'description', slot: 'description' },
  { label: '何时使用', prop: 'when_to_use', slot: 'whenToUse' },
  { label: '版本', prop: 'version', width: 80 },
  { label: '工具数', prop: 'allowed_tools', width: 90, slot: 'toolCount' },
  { label: '技能', prop: 'skill_names', width: 90, slot: 'skillNames' },
  { label: '模型', prop: 'model', width: 180, slot: 'model' },
  { label: '操作', prop: 'actions', width: 260, slot: 'actions' },
]

/** 表格数据源：直接透传到 listSubAgentsPage，由后端做分页 / 关键字过滤 */
async function api(query: PageQuery): Promise<PageResult<SubAgentRow>> {
  return listSubAgentsPage(query)
}

const tableRef = ref<{ refresh: () => void }>()
const dialogRef = ref<InstanceType<typeof WebAgentFormDialog>>()
const dialogVisible = ref(false)
const editingName = ref<string | null>(null)

/** 测试运行弹窗状态 */
const testDialogVisible = ref(false)
const testDialogAgentName = ref<string | null>(null)
function openTestDialog(row: SubAgentRow): void {
  testDialogAgentName.value = row.name
  testDialogVisible.value = true
}

/** 测试历史弹窗状态 */
const historyDialogVisible = ref(false)
const historyDialogAgentName = ref<string | null>(null)
function openHistoryDialog(row: SubAgentRow): void {
  historyDialogAgentName.value = row.name
  historyDialogVisible.value = true
}

/** 追踪详情弹窗状态：由历史弹窗「详情」或测试弹窗「查看执行详情」触发 */
const detailDialogVisible = ref(false)
const detailDialogAgentName = ref<string | null>(null)
const detailTraceId = ref<number | null>(null)
function openDetailDialog(agentName: string, traceId: number): void {
  detailDialogAgentName.value = agentName
  detailTraceId.value = traceId
  detailDialogVisible.value = true
}

// ---------------------------------------------------------------------------
// 表单选项（弹窗打开前预加载）
// ---------------------------------------------------------------------------

/** 工具下拉选项：把后端 ToolCatalogEntry 投影为 el-select 友好的 {label, value} */
interface ToolOption {
  value: string
  label: string
  /** 给 el-option-group 分组用（'builtin' 与 'mcp' 隔离） */
  group: 'builtin' | 'mcp'
}

const toolOptions = ref<ToolOption[]>([])
/** model 下拉选项：取自 listAllProviderModels 聚合（每个 provider 下的 ModelConfigRow） */
interface ModelOption {
  value: string
  label: string
  /** 仅用于展示，不参与提交 */
  providerName: string
}
const modelOptions = ref<ModelOption[]>([])

/** skill 下拉选项：取自 listSkills（全局 skill 资产） */
interface SkillOption {
  value: string
  label: string
}
const skillOptions = ref<SkillOption[]>([])
const optionsLoading = ref(false)

/** 把 ToolCatalogEntry 投影为 el-select 友好的分组选项 */
function projectCatalog(entries: ToolCatalogEntry[]): ToolOption[] {
  return entries.map((entry) => {
    if (entry.source === 'mcp' && entry.server) {
      // 后端 entry.name 已是 `${server}__${tool}` 命名空间名，直接作为 value；
      // label 拆掉命名空间前缀，保持「server / 裸工具名」展示
      const prefix = `${entry.server}__`
      const bareName = entry.name.startsWith(prefix) ? entry.name.slice(prefix.length) : entry.name
      return { value: entry.name, label: `${entry.server} / ${bareName}`, group: 'mcp' }
    }
    return { value: entry.name, label: entry.name, group: 'builtin' }
  })
}

/** 拉工具目录：单次失败降级为空数组，下拉渲染「暂无选项」占位 */
async function loadToolCatalogOptions(): Promise<void> {
  try {
    const entries = await listToolCatalog()
    toolOptions.value = projectCatalog(entries)
  } catch {
    toolOptions.value = []
  }
}

/** 拉模型下拉选项：聚合所有 enabled provider 的 models，按 provider_name 排序 */
async function loadModelOptions(): Promise<void> {
  try {
    const models = await listAllProviderModels()
    modelOptions.value = models.map((model: ModelConfigRow) => ({
      value: model.ref,
      label: model.ref,
      providerName: model.provider_name,
    }))
  } catch {
    modelOptions.value = []
  }
}

/** 拉 skill 下拉选项：单次失败降级为空数组，下拉渲染「暂无选项」占位 */
async function loadSkillOptions(): Promise<void> {
  try {
    const rows = await listSkills()
    skillOptions.value = rows.map((row: SkillRow) => ({ value: row.name, label: row.name }))
  } catch {
    skillOptions.value = []
  }
}

/** 一次性预加载三个选项；任一失败不影响另一项 */
async function loadFormOptions(): Promise<void> {
  optionsLoading.value = true
  try {
    await Promise.all([loadToolCatalogOptions(), loadModelOptions(), loadSkillOptions()])
  } finally {
    optionsLoading.value = false
  }
}

onMounted(() => {
  void loadFormOptions()
})

// ---------------------------------------------------------------------------
// 表单形状
// ---------------------------------------------------------------------------

interface SubAgentFormShape {
  name: string
  description: string
  when_to_use: string
  system_prompt: string
  /** 工具命名空间列表（builtin 裸名 / mcp `{server}__{tool}`）；空数组 → null 继承父 AgentApp */
  allowed_tools: string[]
  /** `provider/model` 引用；空字符串 → null → 运行时回退 default/default */
  model: string
  max_turns: number | null
  /**
   * 绑定的 skill 资产名白名单：
   * - 空数组 → 后端 null（继承父 AgentApp）；
   * - 非空数组 → 后端显式白名单；
   * - 留空未选等同于 null。
   */
  skill_names: string[]
}

/** WebAgentFormDialog.open() 不传 data 时为 {}，字段 optional */
type SubmitFormShape = Partial<SubAgentFormShape>

const rules: FormRules = {
  name: [
    { required: true, message: '请输入子代理名称', trigger: 'blur' },
    {
      pattern: /^[a-z0-9][a-z0-9_-]*$/,
      message: '仅允许小写字母、数字、连字符与下划线，且以字母或数字开头',
      trigger: 'blur',
    },
  ],
  description: [{ required: true, message: '请输入描述', trigger: 'blur' }],
  when_to_use: [{ required: true, message: '请输入何时使用', trigger: 'blur' }],
  system_prompt: [{ required: true, message: '请输入系统提示', trigger: 'blur' }],
}

function handleCreate(): void {
  editingName.value = null
  dialogRef.value?.open()
}

function handleEdit(row: SubAgentRow): void {
  editingName.value = row.name
  dialogRef.value?.open({
    name: row.name,
    description: row.description,
    when_to_use: row.when_to_use,
    system_prompt: row.system_prompt,
    allowed_tools: row.allowed_tools ? [...row.allowed_tools] : [],
    model: row.model ?? '',
    max_turns: row.max_turns,
    skill_names: row.skill_names ? [...row.skill_names] : [],
  } satisfies SubAgentFormShape)
}

/**
 * 把表单字段转为后端 payload。
 * - allowed_tools：空数组 → null（继承父 AgentApp）
 * - model：trim 后空字符串 → null
 * - max_turns：null/undefined → 后端由 null 处理
 */
function buildPayload(form: SubmitFormShape): {
  create: SubAgentCreatePayload
  patch: SubAgentPatchPayload
} {
  const allowedTools = Array.isArray(form.allowed_tools) && form.allowed_tools.length > 0
    ? [...form.allowed_tools]
    : null
  const model = (form.model ?? '').trim()
  const modelValue = model.length > 0 ? model : null
  const name = (form.name ?? '').trim()
  const description = (form.description ?? '').trim()
  const whenToUse = (form.when_to_use ?? '').trim()
  const systemPrompt = form.system_prompt ?? ''
  const maxTurns =
    form.max_turns !== undefined && form.max_turns !== null && form.max_turns !== ('' as unknown as number)
      ? Number(form.max_turns)
      : null
  const skillNames = Array.isArray(form.skill_names) && form.skill_names.length > 0
    ? [...form.skill_names]
    : null

  const create: SubAgentCreatePayload = {
    name,
    description,
    when_to_use: whenToUse,
    system_prompt: systemPrompt,
    allowed_tools: allowedTools,
    model: modelValue,
    max_turns: maxTurns,
    skill_names: skillNames,
  }
  const patch: SubAgentPatchPayload = {
    description,
    when_to_use: whenToUse,
    system_prompt: systemPrompt,
    allowed_tools: allowedTools,
    model: modelValue,
    max_turns: maxTurns,
    skill_names: skillNames,
  }
  return { create, patch }
}

async function handleSubmit(data: Record<string, unknown>): Promise<void> {
  const form = data as unknown as SubmitFormShape
  const name = (form.name ?? '').trim()

  // 必填字段缺失（WebAgentFormDialog.validate 已拦截，这里双保险）— 静默丢弃
  if (!name || !form.description || !form.when_to_use || !form.system_prompt) {
    return
  }

  dialogRef.value?.setSubmitting(true)
  try {
    const { create, patch } = buildPayload(form)
    if (editingName.value !== null) {
      // 编辑：name 不可改
      await patchSubAgent(editingName.value, patch)
    } else {
      await createSubAgent(create)
    }
  } finally {
    dialogRef.value?.setSubmitting(false)
  }

  dialogRef.value?.close()
  notifySuccess(`已保存：${name}`)
  tableRef.value?.refresh()
}

function handleDelete(row: SubAgentRow): void {
  const confirmAndDelete = useConfirm(
    `确定删除子代理「${row.name}」吗？该操作不可恢复；若该 SubAgent 被任何 AgentApp 引用，会在删除时由后端 422 拒绝。`,
    async () => {
      await deleteSubAgent(row.name)
    },
    { title: '删除确认', successMessage: '已删除' },
  )
  void confirmAndDelete().then((done) => {
    if (done) tableRef.value?.refresh()
  })
}

/** allowed_tools 数组展示：null → 「—」（继承父应用）；非空 → 「N 项」 */
function toolCountText(row: SubAgentRow): string {
  if (!row.allowed_tools || row.allowed_tools.length === 0) {
    return '—'
  }
  return `${row.allowed_tools.length} 项`
}

/**
 * skill_names 数组展示：
 * - null → 「继承」（继承父 AgentApp 全集）；
 * - [] → 「无」（显式不绑定）；
 * - 非空 → 「N 项」。
 */
function skillNamesText(row: SubAgentRow): string {
  if (row.skill_names === null || row.skill_names === undefined) {
    return '继承'
  }
  if (row.skill_names.length === 0) {
    return '无'
  }
  return `${row.skill_names.length} 项`
}

/** 模型展示：null → 「继承父应用」；否则显示 provider/model 引用 */
function modelLabel(row: SubAgentRow): string {
  return row.model ?? '继承父应用'
}
</script>

<template>
  <div class="page-view">
    <header class="page-view__header">
      <div>
        <h1 class="page-view__title">子代理管理</h1>
        <p class="page-view__desc">
          集中管理可被 AgentApp 委派的子代理：定义系统提示、可用工具与模型引用。
        </p>
      </div>
      <div class="page-view__actions">
        <el-button class="app-btn app-btn--primary" @click="handleCreate">
          新建子代理
        </el-button>
      </div>
    </header>

    <section class="content-card page-view__body">
      <WebAgentTable ref="tableRef" :columns="columns" :api="api">
        <template #name="{ row }">
          <span class="subagent-name">{{ (row as SubAgentRow).name }}</span>
        </template>
        <template #description="{ row }">
          <span class="subagent-description">{{ (row as SubAgentRow).description }}</span>
        </template>
        <template #whenToUse="{ row }">
          <span class="subagent-when-to-use">{{ (row as SubAgentRow).when_to_use }}</span>
        </template>
        <template #toolCount="{ row }">
          <span>{{ toolCountText(row as SubAgentRow) }}</span>
        </template>
        <template #skillNames="{ row }">
          <span>{{ skillNamesText(row as SubAgentRow) }}</span>
        </template>
        <template #model="{ row }">
          <span class="subagent-model">{{ modelLabel(row as SubAgentRow) }}</span>
        </template>
        <template #actions="{ row }">
          <el-button link type="primary" size="small" @click="openTestDialog(row as SubAgentRow)">
            测试
          </el-button>
          <el-button link type="primary" size="small" @click="openHistoryDialog(row as SubAgentRow)">
            历史
          </el-button>
          <el-button link type="primary" size="small" @click="handleEdit(row as SubAgentRow)">
            编辑
          </el-button>
          <el-button link type="danger" size="small" @click="handleDelete(row as SubAgentRow)">
            删除
          </el-button>
        </template>
      </WebAgentTable>
    </section>

    <WebAgentFormDialog
      ref="dialogRef"
      v-model="dialogVisible"
      title="子代理信息"
      width="640px"
      :rules="rules"
      @submit="handleSubmit"
    >
      <template #default="{ form, mode }">
        <el-form-item label="名称" prop="name">
          <el-input
            v-model="form.name"
            placeholder="小写字母、数字、连字符、下划线"
            :disabled="mode === 'edit'"
          />
        </el-form-item>
        <el-form-item label="描述" prop="description">
          <el-input v-model="form.description" placeholder="一句话说明这个子代理的职责" />
        </el-form-item>
        <el-form-item label="何时使用" prop="when_to_use">
          <el-input v-model="form.when_to_use" placeholder="什么场景下让 AgentApp 委派给此子代理" />
        </el-form-item>
        <el-form-item label="系统提示" prop="system_prompt">
          <el-input
            v-model="form.system_prompt"
            type="textarea"
            :rows="12"
            placeholder="子代理的角色设定与行为约束"
          />
        </el-form-item>
        <el-form-item label="允许的工具" prop="allowed_tools">
          <el-select
            v-model="form.allowed_tools"
            multiple
            collapse-tags
            collapse-tags-tooltip
            filterable
            clearable
            :loading="optionsLoading"
            no-data-text="暂无可用工具（请先在 MCP 管理页启用 server 或配置 builtin）"
            placeholder="留空表示继承父 AgentApp"
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
        </el-form-item>
        <el-form-item label="模型" prop="model">
          <el-select
            v-model="form.model"
            filterable
            clearable
            :loading="optionsLoading"
            no-data-text="暂无可用模型（请先在 模型管理 页启用 provider 并添加 model）"
            placeholder="留空继承父应用（默认 default/default）"
            style="width: 100%"
          >
            <el-option
              v-for="option in modelOptions"
              :key="option.value"
              :label="option.label"
              :value="option.value"
            >
              <span class="subagent-model-option__ref">{{ option.label }}</span>
              <span class="subagent-model-option__provider">{{ option.providerName }}</span>
            </el-option>
          </el-select>
        </el-form-item>
        <el-form-item label="最大轮次" prop="max_turns">
          <el-input-number v-model="form.max_turns" :min="1" :max="50" placeholder="正整数；留空继承父应用" />
        </el-form-item>
        <el-form-item label="关联技能" prop="skill_names">
          <el-select
            v-model="form.skill_names"
            multiple
            collapse-tags
            collapse-tags-tooltip
            filterable
            clearable
            :loading="optionsLoading"
            no-data-text="暂无可用技能（请先在 技能管理 页创建 skill 资产）"
            placeholder="留空表示继承父 AgentApp；显式清空（点击清空按钮）则不绑定任何技能"
            style="width: 100%"
          >
            <el-option
              v-for="option in skillOptions"
              :key="option.value"
              :label="option.label"
              :value="option.value"
            />
          </el-select>
        </el-form-item>
      </template>
    </WebAgentFormDialog>

    <SubAgentTestDialog
      v-if="testDialogAgentName"
      v-model="testDialogVisible"
      :agent-name="testDialogAgentName"
      @open-trace="(traceId) => openDetailDialog(testDialogAgentName!, traceId)"
    />

    <SubAgentTraceHistoryDialog
      v-if="historyDialogAgentName"
      v-model="historyDialogVisible"
      :agent-name="historyDialogAgentName"
      @open-detail="(traceId) => openDetailDialog(historyDialogAgentName!, traceId)"
    />

    <SubAgentTraceDetailDialog
      v-if="detailDialogAgentName"
      v-model="detailDialogVisible"
      :agent-name="detailDialogAgentName"
      :trace-id="detailTraceId"
    />
  </div>
</template>

<style scoped>
.subagent-name {
  font-weight: 600;
  color: var(--color-text-primary);
}
.subagent-description,
.subagent-when-to-use {
  color: var(--color-text-secondary);
  display: -webkit-box;
  -webkit-line-clamp: 2;
  -webkit-box-orient: vertical;
  overflow: hidden;
}
.subagent-model {
  font-family: var(--app-font-display);
  color: var(--color-text-secondary);
  letter-spacing: 0.02em;
}
/* el-select option 自定义渲染：左侧 ref、右侧 provider */
.subagent-model-option__ref {
  font-family: var(--app-font-mono, monospace);
}
.subagent-model-option__provider {
  float: right;
  color: var(--color-text-tertiary);
  font-size: 12px;
}
</style>
