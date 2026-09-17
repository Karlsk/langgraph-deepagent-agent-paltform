<script setup lang="ts">
/**
 * Agent 管理页：agent（deepagents）类型 AgentApp 的 CRUD + 发布。
 *
 * AgentApp 是平台实体（伞概念，`engine` 字段区分类型），本页是
 * `/apps` 资源在 agent 引擎形态下的管理入口；未来 workflow 引擎
 * 落地后新增 `/workflow` 页，总览入口见 `/agentapp`。
 *
 * 数据源：`listAgentAppsPage(query)` 走真实后端（`/apps/page`），
 * 返回 `PageResult<AgentAppRow>`；CRUD / 发布全部走 `@/api/agentapps`。
 *
 * 表单：name / system_prompt（必填）；
 * allowed_tools（el-select 多选，选项来源 /tools/catalog）/
 * model（el-select 单选，拉所有 enabled provider 的 models）/
 * skill_names / subagent_names（el-select 多选，选项来源 /skills、/subagents）。
 *
 * 后端 PATCH 语义：skill_names / subagent_names 显式 null 会被 422 拒绝，
 * 清空必须传 []；已发布应用编辑会回退 draft 并需重新发布（表单内提示）。
 */
import { onMounted, ref, watch } from 'vue'
import type { FormRules } from 'element-plus'
import { Search } from '@element-plus/icons-vue'

import WebAgentFormDialog from '@/components/WebAgentFormDialog.vue'
import WebAgentTable from '@/components/WebAgentTable.vue'
import type { TableColumnConfig } from '@/components/WebAgentTable.vue'
import {
  createAgentApp,
  deleteAgentApp,
  listAgentAppsPage,
  patchAgentApp,
  publishAgentApp,
  type AgentAppCreatePayload,
  type AgentAppEngine,
  type AgentAppPatchPayload,
  type AgentAppRow,
} from '@/api/agentapps'
import { listMcpServers, listToolCatalog, type McpServerRow, type ToolCatalogEntry } from '@/api/mcp'
import { listAllProviderModels, type ModelConfigRow } from '@/api/provider'
import { listSkills, type SkillRow } from '@/api/assets'
import { listSubAgents, type SubAgentRow } from '@/api/subagents'
import { listWorkflows, type WorkflowSummary } from '@/api/workflow'
import { useConfirm } from '@/composables/useConfirm'
import { notifySuccess } from '@/utils/notify'
import type { PageQuery, PageResult } from '@/types'

/** AgentApp 名规则：与后端 `_name_field` 共享（与 SubAgent/Skill/McpServer 一致） */
const NAME_RE = /^[a-z0-9][a-z0-9_-]*$/

const columns: TableColumnConfig[] = [
  { label: '名称', prop: 'name', width: 160, slot: 'name' },
  { label: '系统提示', prop: 'system_prompt', slot: 'systemPrompt' },
  { label: '状态', prop: 'status', width: 100, slot: 'status' },
  { label: '模型', prop: 'model', width: 180, slot: 'model' },
  { label: '技能与子代理', prop: 'skill_names', width: 140, slot: 'bindings' },
  { label: '版本', prop: 'version', width: 80 },
  { label: '操作', prop: 'actions', width: 220, slot: 'actions' },
]

/** 表格数据源：直接透传到 listAgentAppsPage，由后端做分页 / 关键字过滤 */
async function api(query: PageQuery): Promise<PageResult<AgentAppRow>> {
  return listAgentAppsPage(query)
}

const keyword = ref('')
/**
 * 表格 query 载荷：仅在 300ms 防抖命中后才整体替换引用，
 * 让 WebAgentTable 内部的 deep watch 只在关键字真正变化时触发 fetchData
 * （对象字面量每次 render 都新建，直接内联会触发额外 fetch）。
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
/** 编辑中的应用 id；null 表示创建态 */
const editingId = ref<number | null>(null)
/** 编辑中的应用是否已发布（弹窗内提示回退 draft 语义） */
const editingPublished = ref(false)
/** 正在发布的行 id（行内发布按钮 loading） */
const publishingId = ref<number | null>(null)

// ---------------------------------------------------------------------------
// 表单选项（onMounted 预加载，单项失败降级空数组）
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

/** skill / subagent 下拉选项：分别取自 listSkills / listSubAgents */
interface NameOption {
  value: string
  label: string
}
const skillOptions = ref<NameOption[]>([])
const subagentOptions = ref<NameOption[]>([])
/** 绑定工作流下拉选项：取自 listWorkflows（engine='workflow' 时使用） */
const workflowOptions = ref<NameOption[]>([])
/** MCP 服务下拉选项：取自 listMcpServers（仅 enabled 的 server） */
const mcpServerOptions = ref<NameOption[]>([])
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

/** 拉 skill 下拉选项：单次失败降级为空数组 */
async function loadSkillOptions(): Promise<void> {
  try {
    const rows = await listSkills()
    skillOptions.value = rows.map((row: SkillRow) => ({ value: row.name, label: row.name }))
  } catch {
    skillOptions.value = []
  }
}

/** 拉子代理下拉选项：单次失败降级为空数组 */
async function loadSubAgentOptions(): Promise<void> {
  try {
    const rows = await listSubAgents()
    subagentOptions.value = rows.map((row: SubAgentRow) => ({ value: row.name, label: row.name }))
  } catch {
    subagentOptions.value = []
  }
}

/** 拉绑定工作流下拉选项：单次失败降级为空数组 */
async function loadWorkflowOptions(): Promise<void> {
  try {
    const rows = await listWorkflows()
    workflowOptions.value = rows.map((row: WorkflowSummary) => ({
      value: row.workflow_id,
      label: row.description ? `${row.workflow_id}（${row.description}）` : row.workflow_id,
    }))
  } catch {
    workflowOptions.value = []
  }
}

/** 拉 MCP 服务下拉选项：仅 enabled 的 server；单次失败降级为空数组 */
async function loadMcpServerOptions(): Promise<void> {
  try {
    const rows = await listMcpServers()
    mcpServerOptions.value = rows
      .filter((r: McpServerRow) => r.enabled)
      .map((r: McpServerRow) => ({ value: r.name, label: r.name }))
  } catch {
    mcpServerOptions.value = []
  }
}

/** 一次性预加载选项；任一失败不影响另一项 */
async function loadFormOptions(): Promise<void> {
  optionsLoading.value = true
  try {
    await Promise.all([
      loadToolCatalogOptions(),
      loadModelOptions(),
      loadSkillOptions(),
      loadSubAgentOptions(),
      loadWorkflowOptions(),
      loadMcpServerOptions(),
    ])
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

interface AgentAppFormShape {
  name: string
  /** 引擎类型：deepagents（技能型）/ workflow（chatflow，绑定工作流）；创建后不可变 */
  engine: AgentAppEngine
  /** 绑定工作流 id（仅 engine='workflow' 提交；空字符串 → 未选择） */
  workflow_id: string
  system_prompt: string
  /** 工具命名空间列表（builtin 裸名 / mcp `{server}__{tool}`）；空数组 → null 引擎默认 */
  allowed_tools: string[]
  /** 关联的 MCP 服务名称列表；空数组 → 不关联任何 MCP server */
  mcp_server_names: string[]
  /** `provider/model` 引用；空字符串 → null（引擎默认） */
  model: string
  /** 绑定的 skill 资产名；始终提交数组（空数组 = 不绑定；后端禁显式 null） */
  skill_names: string[]
  /** 绑定的子代理配置名；语义同 skill_names */
  subagent_names: string[]
  /** 需人工审批的工具名列表（提交时转为 Record<string, boolean>） */
  interrupt_on: string[]
}

/** WebAgentFormDialog.open() 不传 data 时为 {}，字段 optional */
type SubmitFormShape = Partial<AgentAppFormShape>

/** 创建态表单初始值（open() 不携带 data，需手动落默认 engine=deepagents） */
function createFormDefaults(): AgentAppFormShape {
  return {
    name: '',
    engine: 'deepagents',
    workflow_id: '',
    system_prompt: '',
    allowed_tools: [],
    mcp_server_names: [],
    model: '',
    skill_names: [],
    subagent_names: [],
    interrupt_on: [],
  }
}

const rules: FormRules = {
  name: [
    { required: true, message: '请输入应用名称', trigger: 'blur' },
    {
      pattern: NAME_RE,
      message: '以小写字母或数字开头，后续仅含小写字母/数字/下划线/连字符',
      trigger: 'blur',
    },
    { max: 64, message: '名称长度不能超过 64 个字符', trigger: 'blur' },
  ],
  system_prompt: [{ required: true, message: '请输入系统提示', trigger: 'blur' }],
}

function handleCreate(): void {
  editingId.value = null
  editingPublished.value = false
  dialogRef.value?.open()
  // open() 不带 data → formModel 为空；落创建态默认值（engine 必须显式为 deepagents）
  Object.assign(dialogRef.value?.getForm() ?? {}, createFormDefaults())
}

function handleEdit(row: AgentAppRow): void {
  editingId.value = row.id
  editingPublished.value = row.status === 'published'
  dialogRef.value?.open({
    name: row.name,
    engine: row.engine,
    workflow_id: row.workflow_id ?? '',
    system_prompt: row.system_prompt,
    allowed_tools: row.allowed_tools ? [...row.allowed_tools] : [],
    mcp_server_names: [...row.mcp_server_names],
    model: row.model ?? '',
    skill_names: [...row.skill_names],
    subagent_names: [...row.subagent_names],
    interrupt_on: Object.keys(row.interrupt_on ?? {}),
  } satisfies AgentAppFormShape)
}

/**
 * 把表单字段转为后端 payload。
 * - engine='workflow'（chatflow）：仅提交 {name, engine, workflow_id}，
 *   deepagents 专属字段一律不发；patch 仅改绑 workflow_id；
 * - engine='deepagents'：
 *   - allowed_tools：空数组 → null（引擎默认）
 *   - model：trim 后空字符串 → null
 *   - skill_names / subagent_names：始终数组（空数组 = 不绑定 / 清空）
 */
function buildPayload(form: SubmitFormShape): {
  create: AgentAppCreatePayload
  patch: AgentAppPatchPayload
} {
  const name = (form.name ?? '').trim()

  if (form.engine === 'workflow') {
    const workflowId = (form.workflow_id ?? '').trim()
    return {
      create: { name, engine: 'workflow', workflow_id: workflowId },
      patch: { workflow_id: workflowId },
    }
  }

  const allowedTools = Array.isArray(form.allowed_tools) && form.allowed_tools.length > 0
    ? [...form.allowed_tools]
    : null
  const mcpServerNames = Array.isArray(form.mcp_server_names) ? [...form.mcp_server_names] : []
  const model = (form.model ?? '').trim()
  const modelValue = model.length > 0 ? model : null
  const systemPrompt = form.system_prompt ?? ''
  const skillNames = Array.isArray(form.skill_names) ? [...form.skill_names] : []
  const subagentNames = Array.isArray(form.subagent_names) ? [...form.subagent_names] : []
  const interruptOnList = Array.isArray(form.interrupt_on) ? form.interrupt_on : []
  const interruptOn: Record<string, boolean> = interruptOnList.reduce((acc, name) => {
    acc[name] = true
    return acc
  }, {} as Record<string, boolean>)

  const create: AgentAppCreatePayload = {
    name,
    system_prompt: systemPrompt,
    allowed_tools: allowedTools,
    mcp_server_names: mcpServerNames,
    model: modelValue,
    skill_names: skillNames,
    subagent_names: subagentNames,
    interrupt_on: interruptOn,
  }
  const patch: AgentAppPatchPayload = {
    system_prompt: systemPrompt,
    allowed_tools: allowedTools,
    mcp_server_names: mcpServerNames,
    model: modelValue,
    skill_names: skillNames,
    subagent_names: subagentNames,
    interrupt_on: interruptOn,
  }
  return { create, patch }
}

async function handleSubmit(data: Record<string, unknown>): Promise<void> {
  const form = data as unknown as SubmitFormShape
  const name = (form.name ?? '').trim()

  // 必填字段缺失（WebAgentFormDialog.validate 已拦截，这里双保险）— 静默丢弃
  if (form.engine === 'workflow') {
    if (!name || !(form.workflow_id ?? '').trim()) {
      return
    }
  } else if (!name || !form.system_prompt) {
    return
  }

  dialogRef.value?.setSubmitting(true)
  try {
    const { create, patch } = buildPayload(form)
    if (editingId.value !== null) {
      // 编辑：name / engine 不可改；已发布应用编辑后回退 draft，需重新发布
      await patchAgentApp(editingId.value, patch)
    } else {
      await createAgentApp(create)
    }
  } finally {
    dialogRef.value?.setSubmitting(false)
  }

  dialogRef.value?.close()
  notifySuccess(`已保存：${name}`)
  tableRef.value?.refresh()
}

/** 行内发布：校验引用完整性 + 工具白名单由后端完成，422 由全局拦截器弹错 */
async function handlePublish(row: AgentAppRow): Promise<void> {
  publishingId.value = row.id
  try {
    await publishAgentApp(row.id)
  } catch {
    // 错误提示由统一请求层拦截器弹出，此处只放弃后续动作
    return
  } finally {
    publishingId.value = null
  }
  notifySuccess(`已发布：${row.name}`)
  tableRef.value?.refresh()
}

function handleDelete(row: AgentAppRow): void {
  const confirmAndDelete = useConfirm(
    `确定删除 Agent 应用「${row.name}」吗？该操作不可恢复，并将级联清理该应用的 Agent 层 Workspace；系统 default 应用会被后端拒绝。`,
    async () => {
      await deleteAgentApp(row.id)
    },
    { title: '删除确认', successMessage: '已删除' },
  )
  void confirmAndDelete().then((done) => {
    if (done) tableRef.value?.refresh()
  })
}

/** 系统提示列截断展示：超过 100 字符显示省略号（鼠标 hover 看 title 完整内容） */
function truncatedPrompt(text: string): string {
  if (text.length <= 100) return text
  return `${text.slice(0, 100)}…`
}

/** 模型展示：null → 「默认」；否则显示 provider/model 引用 */
function modelLabel(row: AgentAppRow): string {
  return row.model ?? '默认'
}

/** 技能与子代理绑定展示：「N 技能 · M 子代理」 */
function bindingsText(row: AgentAppRow): string {
  return `${row.skill_names.length} 技能 · ${row.subagent_names.length} 子代理`
}
</script>

<template>
  <div class="page-view">
    <header class="page-view__header">
      <div>
        <h1 class="page-view__title">Agent 管理</h1>
        <p class="page-view__desc">
          集中管理 agent（deepagents）类型 AgentApp 的配置与发布：系统提示、
          工具白名单、模型、技能与子代理绑定。
        </p>
      </div>
      <div class="page-view__actions">
        <el-button class="app-btn app-btn--primary" @click="handleCreate">
          新建 Agent
        </el-button>
      </div>
    </header>

    <section class="content-card page-view__body">
      <div class="agent-toolbar">
        <el-input
          v-model="keyword"
          class="agent-toolbar__search"
          placeholder="按名称 / 系统提示模糊搜索"
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
          <span class="agent-name">{{ (row as AgentAppRow).name }}</span>
          <el-tag
            v-if="(row as AgentAppRow).engine === 'workflow'"
            class="agent-engine-tag"
            type="info"
            size="small"
          >
            工作流
          </el-tag>
        </template>
        <template #systemPrompt="{ row }">
          <span :title="(row as AgentAppRow).system_prompt">
            {{ truncatedPrompt((row as AgentAppRow).system_prompt) || '—' }}
          </span>
        </template>
        <template #status="{ row }">
          <el-tag
            :type="(row as AgentAppRow).status === 'published' ? 'success' : 'warning'"
            size="small"
          >
            {{ (row as AgentAppRow).status === 'published' ? '已发布' : '草稿' }}
          </el-tag>
        </template>
        <template #model="{ row }">
          <span class="agent-model">{{ modelLabel(row as AgentAppRow) }}</span>
        </template>
        <template #bindings="{ row }">
          <span>{{ bindingsText(row as AgentAppRow) }}</span>
        </template>
        <template #actions="{ row }">
          <el-button
            link
            type="success"
            size="small"
            :loading="publishingId === (row as AgentAppRow).id"
            @click="handlePublish(row as AgentAppRow)"
          >
            发布
          </el-button>
          <el-button link type="primary" size="small" @click="handleEdit(row as AgentAppRow)">
            编辑
          </el-button>
          <el-button link type="danger" size="small" @click="handleDelete(row as AgentAppRow)">
            删除
          </el-button>
        </template>
      </WebAgentTable>
    </section>

    <WebAgentFormDialog
      ref="dialogRef"
      v-model="dialogVisible"
      :title="editingId === null ? '新建 Agent' : '编辑 Agent'"
      width="640px"
      :rules="rules"
      @submit="handleSubmit"
    >
      <template #default="{ form, mode }">
        <div v-if="editingPublished" class="agent-form-hint">
          该应用已发布：保存后将回退为草稿，需重新发布才会生效。
        </div>
        <el-form-item label="名称" prop="name">
          <el-input
            v-model="form.name"
            placeholder="小写字母、数字、连字符、下划线"
            :disabled="mode === 'edit'"
          />
        </el-form-item>
        <el-form-item label="引擎类型" prop="engine">
          <el-select
            v-model="form.engine"
            :disabled="mode === 'edit'"
            placeholder="选择应用引擎"
            style="width: 100%"
          >
            <el-option label="Agent（deepagents）" value="deepagents" />
            <el-option label="工作流（chatflow）" value="workflow" />
          </el-select>
          <div class="agent-form-helptext">
            引擎创建后不可变。工作流引擎把已注册工作流封装为可对话应用。
          </div>
        </el-form-item>
        <el-form-item
          v-if="form.engine === 'workflow'"
          label="绑定工作流"
          prop="workflow_id"
        >
          <el-select
            v-model="form.workflow_id"
            filterable
            clearable
            :loading="optionsLoading"
            no-data-text="暂无已注册工作流（请先在 工作流管理 页创建并发布）"
            placeholder="选择要绑定的工作流"
            style="width: 100%"
          >
            <el-option
              v-for="option in workflowOptions"
              :key="option.value"
              :label="option.label"
              :value="option.value"
            />
          </el-select>
        </el-form-item>
        <el-form-item
          v-if="form.engine !== 'workflow'"
          label="系统提示"
          prop="system_prompt"
        >
          <el-input
            v-model="form.system_prompt"
            type="textarea"
            :rows="8"
            placeholder="Agent 的角色设定与行为约束"
          />
        </el-form-item>
        <el-form-item v-if="form.engine !== 'workflow'" label="允许的工具" prop="allowed_tools">
          <el-select
            v-model="form.allowed_tools"
            multiple
            collapse-tags
            collapse-tags-tooltip
            filterable
            clearable
            :loading="optionsLoading"
            no-data-text="暂无可用工具（请先在 MCP 管理页启用 server 或配置 builtin）"
            placeholder="留空使用引擎默认工具集"
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
        <el-form-item v-if="form.engine !== 'workflow'" label="MCP 服务" prop="mcp_server_names">
          <el-select
            v-model="form.mcp_server_names"
            multiple
            collapse-tags
            collapse-tags-tooltip
            filterable
            clearable
            :loading="optionsLoading"
            no-data-text="暂无可用 MCP 服务（请先在 MCP 管理页启用 server）"
            placeholder="选择要关联的 MCP 服务"
            style="width: 100%"
          >
            <el-option
              v-for="option in mcpServerOptions"
              :key="option.value"
              :label="option.label"
              :value="option.value"
            />
          </el-select>
        </el-form-item>
        <el-form-item v-if="form.engine !== 'workflow'" label="模型" prop="model">
          <el-select
            v-model="form.model"
            filterable
            clearable
            :loading="optionsLoading"
            no-data-text="暂无可用模型（请先在 模型管理 页启用 provider 并添加 model）"
            placeholder="留空使用引擎默认模型"
            style="width: 100%"
          >
            <el-option
              v-for="option in modelOptions"
              :key="option.value"
              :label="option.label"
              :value="option.value"
            >
              <span class="agent-model-option__ref">{{ option.label }}</span>
              <span class="agent-model-option__provider">{{ option.providerName }}</span>
            </el-option>
          </el-select>
        </el-form-item>
        <el-form-item v-if="form.engine !== 'workflow'" label="关联技能" prop="skill_names">
          <el-select
            v-model="form.skill_names"
            multiple
            collapse-tags
            collapse-tags-tooltip
            filterable
            clearable
            :loading="optionsLoading"
            no-data-text="暂无可用技能（请先在 技能管理 页创建 skill 资产）"
            placeholder="留空不绑定任何技能"
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
        <el-form-item v-if="form.engine !== 'workflow'" label="关联子代理" prop="subagent_names">
          <el-select
            v-model="form.subagent_names"
            multiple
            collapse-tags
            collapse-tags-tooltip
            filterable
            clearable
            :loading="optionsLoading"
            no-data-text="暂无可用子代理（请先在 子代理管理 页创建配置）"
            placeholder="留空不绑定任何子代理"
            style="width: 100%"
          >
            <el-option
              v-for="option in subagentOptions"
              :key="option.value"
              :label="option.label"
              :value="option.value"
            />
          </el-select>
        </el-form-item>
        <el-form-item v-if="form.engine !== 'workflow'" label="审批工具" prop="interrupt_on">
          <el-select
            v-model="form.interrupt_on"
            multiple
            collapse-tags
            collapse-tags-tooltip
            filterable
            clearable
            :loading="optionsLoading"
            no-data-text="暂无可用工具（请先在 MCP 管理页启用 server 或配置 builtin）"
            placeholder="留空表示工具直接执行无需审批"
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
            选中的工具执行前需人工审批（HIL）；留空则所有工具自动执行。
          </div>
        </el-form-item>
      </template>
    </WebAgentFormDialog>
  </div>
</template>

<style scoped>
.agent-name {
  font-weight: 600;
  color: var(--color-text-primary);
}
.agent-engine-tag {
  margin-left: 6px;
}
.agent-model {
  font-family: var(--app-font-display);
  color: var(--color-text-secondary);
  letter-spacing: 0.02em;
}
.agent-toolbar {
  display: flex;
  align-items: center;
  justify-content: flex-start;
  margin-bottom: 16px;
}
.agent-toolbar__search {
  width: 320px;
}
.agent-form-hint {
  font-size: 12px;
  color: var(--color-warning-600);
  line-height: 1.4;
  margin-bottom: 12px;
}
.agent-form-helptext {
  font-size: 12px;
  color: var(--color-text-tertiary);
  line-height: 1.4;
  margin-top: 4px;
}
/* el-select option 自定义渲染：左侧 ref、右侧 provider */
.agent-model-option__ref {
  font-family: var(--app-font-mono, monospace);
}
.agent-model-option__provider {
  float: right;
  color: var(--color-text-tertiary);
  font-size: 12px;
}
</style>
