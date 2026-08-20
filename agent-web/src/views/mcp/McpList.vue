<script setup lang="ts">
/**
 * MCP 管理页：基于后端 MCP server 契约（`/mcp-servers` + `/mcp-servers/page` +
 * 调试端点 `/mcp-servers/{name}/tools`、`/call-tool` + stdio manifest 同步
 * `/mcp-servers/stdio-manifests`、`/stdio-sync`）的 CRUD + 调试视图。
 *
 * 数据源：`listMcpServersPage(query)` 走真实后端，返回 `PageResult<McpServerRow>`。
 * 列表 / CRUD / 调试 / 同步全部走 `@/api/mcp` 的函数。
 *
 * 表单：单弹窗复用 create/edit 双模式（沿用 WebAgentFormDialog.open(data?)），
 *   - 公共：name / transport（el-select 切换）/ description / enabled；
 *   - stdio：command（必填）/ args（动态数组行）/ env（动态 key-value 行）；
 *   - sse/http：url（必填）/ headers（动态 key-value 行）；
 *   - 切换 transport 时隐藏对端字段（不清空，便于回切保留输入）。
 *
 * 操作列：「查看工具」（弹工具列表）/「测试连接」（探活 + 行级健康 tag）/「编辑」/「删除」。
 *
 * 错误矩阵（docs/mcp-manual-testing.md §4/§8）：
 *   - 422：name 含 __、transport 配对、明文 secret、shell command、unknown tool、缺必填参数
 *   - 404：server 不存在
 *   - 502：端点不可达 / 类型错（server 权威）/ 工具执行失败
 *   - 504：超时
 * 全部由 request.ts 拦截器统一 toast；本地不弹重复错误。
 */
import { reactive, ref, watch } from 'vue'
import type { FormRules } from 'element-plus'
import { Search } from '@element-plus/icons-vue'

import WebAgentFormDialog from '@/components/WebAgentFormDialog.vue'
import WebAgentTable from '@/components/WebAgentTable.vue'
import type { TableColumnConfig } from '@/components/WebAgentTable.vue'
import McpServerToolsDialog from '@/views/mcp/McpServerToolsDialog.vue'
import {
  createMcpServer,
  deleteMcpServer,
  listMcpServerTools,
  listMcpServersPage,
  listStdioManifests,
  patchMcpServer,
  syncStdioManifests,
  type McpServerRow,
  type McpTransport,
  type StdioSyncReport,
} from '@/api/mcp'
import { useConfirm } from '@/composables/useConfirm'
import { notifyError, notifySuccess } from '@/utils/notify'
import type { PageQuery, PageResult } from '@/types'

/** MCP 名规则：与后端 NAME_PATTERN + 禁止 "__" 一致 */
const NAME_RE = /^[a-z0-9][a-z0-9_-]*$/

/** 全部 transport 取值（与后端 Literal["stdio","sse","http"] 一致） */
const TRANSPORTS: McpTransport[] = ['stdio', 'sse', 'http']

/** transport → 中文标签 */
const TRANSPORT_LABELS: Record<McpTransport, string> = {
  stdio: 'stdio',
  sse: 'sse',
  http: 'http',
}

/** transport → el-tag type（与现有 enabled/health tag 调色板一致） */
const TRANSPORT_TAG: Record<McpTransport, 'info' | 'success' | 'warning'> = {
  stdio: 'info',
  sse: 'success',
  http: 'warning',
}

/** 健康状态：行级缓存（key=serverName → 最近一次探活结果） */
interface HealthSnapshot {
  status: 'up' | 'down' | 'timeout' | 'unknown'
  message: string
}
const HEALTH_TAG: Record<HealthSnapshot['status'], { type: 'success' | 'danger' | 'warning' | 'info'; label: string }> = {
  up: { type: 'success', label: '正常' },
  down: { type: 'danger', label: '不可达' },
  timeout: { type: 'warning', label: '超时' },
  unknown: { type: 'info', label: '未探测' },
}

/** transport 切换时清空对端字段（避免误传 command 给 sse 或 url 给 stdio 触发 422） */
function clearOpposingFields(form: McpFormShape, transport: McpTransport): void {
  if (transport === 'stdio') {
    form.url = ''
    form.headers = {}
  } else {
    form.command = ''
    form.args = []
    form.env = {}
  }
}

// ---------------------------------------------------------------------------
// 表格列定义
// ---------------------------------------------------------------------------

const columns: TableColumnConfig[] = [
  { label: '名称', prop: 'name', width: 180, slot: 'name' },
  { label: '传输', prop: 'transport', width: 100, slot: 'transport' },
  { label: '状态', prop: 'enabled', width: 140, slot: 'status' },
  { label: '描述', prop: 'description', slot: 'description' },
  { label: '创建者', prop: 'created_by', width: 140 },
  { label: '操作', prop: 'actions', width: 360, slot: 'actions' },
]

async function api(query: PageQuery): Promise<PageResult<McpServerRow>> {
  return listMcpServersPage(query)
}

// ---------------------------------------------------------------------------
// 搜索（300ms 防抖）
// ---------------------------------------------------------------------------

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

// ---------------------------------------------------------------------------
// 引用与状态
// ---------------------------------------------------------------------------

const tableRef = ref<{ refresh: () => void }>()
const dialogRef = ref<InstanceType<typeof WebAgentFormDialog>>()
const dialogVisible = ref(false)
const editingName = ref<string | null>(null)
/** 编辑模式快照：用于 patch diff（仅发送实际修改的字段） */
const editSnapshot = ref<McpFormSnapshot | null>(null)

/** 「查看工具」弹窗状态 */
const toolsDialogVisible = ref(false)
const toolsDialogServerName = ref<string | null>(null)

/** 「预览 manifests」弹窗状态（只读报告） */
const previewDialogVisible = ref(false)
const previewReport = ref<StdioSyncReport | null>(null)

/** 行级健康状态缓存（key=serverName） */
const healthMap = reactive<Record<string, HealthSnapshot>>({})

/** env / header 新增输入框临时值（form 外本地状态，避免污染 snapshot diff） */
const newEnvKey = ref('')
const newEnvValue = ref('')
const newHeaderKey = ref('')
const newHeaderValue = ref('')

/** 新增 env kv 行：trim key 后写入 form.env；key 重复时覆盖 */
function addEnvKv(env: Record<string, string>, key: string, value: string): void {
  const trimmedKey = key.trim()
  if (trimmedKey.length === 0) return
  env[trimmedKey] = value
}

/** 重置三个新增输入框的临时值 */
function clearNewEnvInputs(): void {
  newEnvKey.value = ''
  newEnvValue.value = ''
}

function clearNewHeaderInputs(): void {
  newHeaderKey.value = ''
  newHeaderValue.value = ''
}

/** 从输入框写入一行 env（trim 后写入；key 重复时覆盖） */
function addEnvFromInput(form: Record<string, unknown>): void {
  if (newEnvKey.value.trim().length === 0) return
  addEnvKv(asForm(form).env, newEnvKey.value, newEnvValue.value)
  clearNewEnvInputs()
}

/** 从输入框写入一行 header（trim 后写入；key 重复时覆盖） */
function addHeaderFromInput(form: Record<string, unknown>): void {
  if (newHeaderKey.value.trim().length === 0) return
  addEnvKv(asForm(form).headers, newHeaderKey.value, newHeaderValue.value)
  clearNewHeaderInputs()
}

/**
 * 模板辅助函数（避免在模板内嵌 TypeScript 类型断言，因 Vue 模板解析器只接受 JS 子集）：
 * - envKeys/headerKeys：v-for 列表（替代 `Object.keys(form.env as Record<string,string>)`）
 * - envValue/headerValue：单行值（替代 `(form.env as Record<string,string>)[key]`）
 * - setEnvValue/setHeaderValue：写回（替代 `(form.env as Record<string,string>)[key] = $event as string`）
 * - removeEnv/removeHeader：删除行（替代 `delete (form.env as Record<string,string>)[key]`）
 * - formEnv/formHeaders：供 addEnvKv 拿 Record 引用（避免模板内 `as`）
 */
type FormWithEnv = McpFormShape & { env: Record<string, string>; headers: Record<string, string>; args: string[] }

function asForm(form: Record<string, unknown>): FormWithEnv {
  return form as unknown as FormWithEnv
}

function envKeys(form: Record<string, unknown>): string[] {
  const env = asForm(form).env
  return env ? Object.keys(env) : []
}

function headerKeys(form: Record<string, unknown>): string[] {
  const headers = asForm(form).headers
  return headers ? Object.keys(headers) : []
}

function envValue(form: Record<string, unknown>, key: string): string {
  return asForm(form).env[key] ?? ''
}

function headerValue(form: Record<string, unknown>, key: string): string {
  return asForm(form).headers[key] ?? ''
}

function setEnvValue(form: Record<string, unknown>, key: string, value: string): void {
  asForm(form).env[key] = value
}

function setHeaderValue(form: Record<string, unknown>, key: string, value: string): void {
  asForm(form).headers[key] = value
}

function removeEnv(form: Record<string, unknown>, key: string): void {
  delete asForm(form).env[key]
}

function removeHeader(form: Record<string, unknown>, key: string): void {
  delete asForm(form).headers[key]
}

/** args 数组访问辅助：避免模板内 `form.args[i]` 被推断为 unknown */
function argAt(form: Record<string, unknown>, index: number): string {
  return asForm(form).args[index] ?? ''
}

function setArgAt(form: Record<string, unknown>, index: number, value: string): void {
  asForm(form).args[index] = value
}

function removeArg(form: Record<string, unknown>, index: number): void {
  asForm(form).args.splice(index, 1)
}

function addArg(form: Record<string, unknown>): void {
  asForm(form).args.push('')
}

// ---------------------------------------------------------------------------
// 表单结构
// ---------------------------------------------------------------------------

interface McpFormShape {
  name: string
  transport: McpTransport
  description: string
  enabled: boolean
  // stdio 字段
  command: string
  args: string[]
  env: Record<string, string>
  // sse/http 字段
  url: string
  headers: Record<string, string>
}

/** WebAgentFormDialog.open() 不传 data 时为 {}，故字段全部 optional */
type SubmitFormShape = Partial<McpFormShape>

/** 编辑模式 diff 用快照：仅取可参与 PATCH 的字段 */
interface McpFormSnapshot {
  transport: McpTransport
  description: string
  enabled: boolean
  command: string
  args: string[]
  env: Record<string, string>
  url: string
  headers: Record<string, string>
}

function snapshotFromForm(form: McpFormShape): McpFormSnapshot {
  return {
    transport: form.transport,
    description: form.description,
    enabled: form.enabled,
    command: form.command,
    args: [...form.args],
    env: { ...form.env },
    url: form.url,
    headers: { ...form.headers },
  }
}

function snapshotEqual(a: McpFormSnapshot, b: McpFormSnapshot): boolean {
  if (a.transport !== b.transport) return false
  if (a.description !== b.description) return false
  if (a.enabled !== b.enabled) return false
  if (a.command !== b.command) return false
  if (a.url !== b.url) return false
  if (JSON.stringify(a.args) !== JSON.stringify(b.args)) return false
  if (JSON.stringify(a.env) !== JSON.stringify(b.env)) return false
  if (JSON.stringify(a.headers) !== JSON.stringify(b.headers)) return false
  return true
}

/** 从行数据反推表单值（编辑模式回填） */
function formFromRow(row: McpServerRow): McpFormShape {
  return {
    name: row.name,
    transport: row.transport,
    description: row.description,
    enabled: row.enabled,
    command: row.command ?? '',
    args: [...row.args],
    env: { ...row.env },
    url: row.url ?? '',
    headers: { ...row.headers },
  }
}

/**
  表单校验规则：
  - name pattern 必填且禁止 "__"（与后端 McpServerCreate.validate_transport_fields 一致）
  - transport 必填
  - description 可选
  - command/url 在 transport 切换时由组件层条件校验（FormRules 不能跨字段依赖）
  */
const rules: FormRules = {
  name: [
    { required: true, message: '请输入 MCP server 名称', trigger: 'blur' },
    {
      pattern: NAME_RE,
      message: '以小写字母或数字开头，后续仅含小写字母/数字/下划线/连字符',
      trigger: 'blur',
    },
    { max: 64, message: '名称长度不能超过 64 个字符', trigger: 'blur' },
    {
      validator: (_rule, value: string, callback) => {
        if (typeof value === 'string' && value.includes('__')) {
          callback(new Error('名称禁止包含 "__"（{server  __tool} 命名空间分隔符）'))
        } else {
          callback()
        }
      },
      trigger: 'blur',
    },
  ],
  transport: [{ required: true, message: '请选择传输类型', trigger: 'change' }],
  command: [{ required: true, message: 'stdio 传输必须填写 command', trigger: 'blur' }],
  url: [{ required: true, message: 'sse/http 传输必须填写 url', trigger: 'blur' }],
}

// ---------------------------------------------------------------------------
// 弹窗控制
// ---------------------------------------------------------------------------

function handleCreate(): void {
  editingName.value = null
  editSnapshot.value = null
  // 创建态不传 data，避免 WebAgentFormDialog 切到 edit 模式（mode 决定 name 是否 disabled）；
  // 预填 transport/enabled 走 getForm() 拿 reactive 表单引用写入，Vue 响应式触发 v-if 切换。
  dialogRef.value?.open()
  const form = dialogRef.value?.getForm()
  if (form) {
    form.transport = 'stdio'
    form.enabled = true
  }
}

function handleEdit(row: McpServerRow): void {
  editingName.value = row.name
  const initial = formFromRow(row)
  // WebAgentFormDialog.open() 形参类型是 Record<string, unknown>；McpFormShape
  // 是结构化类型，先转 unknown 再展开为 Record 让 TS 类型对齐（运行时本就兼容）。
  dialogRef.value?.open({ ...(initial as unknown as Record<string, unknown>) })
  editSnapshot.value = snapshotFromForm(initial)
}

/**
 * 监听 transport 变化：清空对端字段（避免向后端误传 command+url 同时存在）。
 * 该 watch 安装于 WebAgentFormDialog 的 form reactive 对象（通过 dialogRef.getForm()
 * 拿到引用），确保用户切换 transport 立即生效。
 */
watch(
  () => dialogRef.value?.getForm() as McpFormShape | undefined,
  (form) => {
    if (!form) return
    watch(
      () => form.transport,
      (next) => clearOpposingFields(form, next as McpTransport),
    )
  },
  { immediate: true },
)

// ---------------------------------------------------------------------------
// 工具查看 + 测试连接（共用 listMcpServerTools 作为探活）
// ---------------------------------------------------------------------------

function handleViewTools(row: McpServerRow): void {
  toolsDialogServerName.value = row.name
  toolsDialogVisible.value = true
}

/**
 * 测试连接：复用 listMcpServerTools 作为探活（与 manual-testing.md §4.1 「list
 * 端点等价于探活」一致）。更新行级 healthMap 缓存，让该行的健康 tag 实时刷新。
 *
 * 注：端点返回的错误（404/422/504/502）由 request.ts 拦截器统一 toast，本地仅
 * 维护成功/失败的健康状态映射。
 */
async function handleTestConnection(row: McpServerRow): Promise<void> {
  try {
    await listMcpServerTools(row.name)
    healthMap[row.name] = { status: 'up', message: '正常' }
    notifySuccess(`已探测：${row.name}  正常`)
  } catch (err: unknown) {
    // 拦截器已 toast；这里根据 axios 状态码推断更精确的健康 tag 文案
    let status: HealthSnapshot['status'] = 'down'
    let message = '不可达'
    if (err && typeof err === 'object' && 'response' in err) {
      const statusCode = (err as { response?: { status?: number } }).response?.status
      if (statusCode === 504) {
        status = 'timeout'
        message = '超时'
      } else if (statusCode === 422) {
        status = 'down'
        message = '配置错误'
      }
    }
    healthMap[row.name] = { status, message }
  }
  // 刷新列表以触发行级 tag 重渲染（rows.value 引用未变，需 forceUpdate）
  tableRef.value?.refresh()
}

// ---------------------------------------------------------------------------
// 删除
// ---------------------------------------------------------------------------

function handleDelete(row: McpServerRow): void {
  const confirmAndDelete = useConfirm(
    `确定删除 MCP server「${row.name}」吗？该操作不可恢复，且 tools/catalog 中该 server 的工具将立即失效。`,
    async () => {
      await deleteMcpServer(row.name)
    },
    { title: '删除 MCP server', successMessage: '已删除' },
  )
  void confirmAndDelete().then((done) => {
    if (done) {
      delete healthMap[row.name]
      tableRef.value?.refresh()
    }
  })
}

// ---------------------------------------------------------------------------
// 提交（create/edit 双模式）
// ---------------------------------------------------------------------------

/** 过滤空行：env/headers 跳过空 key/value；args 跳过空字符串 */
function cleanEnv(env: Record<string, string>): Record<string, string> {
  const result: Record<string, string> = {}
  for (const [k, v] of Object.entries(env)) {
    if (k.trim().length > 0 && v.trim().length > 0) result[k] = v
  }
  return result
}

function cleanArgs(args: string[]): string[] {
  return args.map((s) => s.trim()).filter((s) => s.length > 0)
}

async function handleSubmit(data: Record<string, unknown>): Promise<void> {
  const form = data as unknown as SubmitFormShape
  const name = (form.name ?? '').trim()
  const transport = form.transport as McpTransport | undefined
  const description = (form.description ?? '').trim()
  const enabled = form.enabled !== false

  // 必填守卫（WebAgentFormDialog.validate 已拦截，这里双保险）— 静默丢弃
  if (!name || !transport) {
    return
  }

  dialogRef.value?.setSubmitting(true)
  try {
    if (editingName.value === null) {
      // ===== 创建模式：组装完整 payload =====
      const payload: Parameters<typeof createMcpServer>[0] = {
        name,
        transport,
        description,
        enabled,
      }
      if (transport === 'stdio') {
        const command = (form.command ?? '').trim()
        if (!command) {
          notifyError('stdio 传输必须填写 command')
          return
        }
        payload.command = command
        payload.args = cleanArgs(form.args ?? [])
        payload.env = cleanEnv(form.env ?? {})
      } else {
        const url = (form.url ?? '').trim()
        if (!url) {
          notifyError(`${transport} 传输必须填写 url`)
          return
        }
        payload.url = url
        payload.headers = cleanEnv(form.headers ?? {})
      }
      await createMcpServer(payload)
      notifySuccess(`已保存：${name}`)
    } else {
      // ===== 编辑模式：仅携带变化的字段（避免 422 nothing to update） =====
      const snapshot = editSnapshot.value
      if (!snapshot) return
      const current: McpFormSnapshot = {
        transport,
        description,
        enabled,
        command: (form.command ?? '').trim(),
        args: cleanArgs(form.args ?? []),
        env: cleanEnv(form.env ?? {}),
        url: (form.url ?? '').trim(),
        headers: cleanEnv(form.headers ?? {}),
      }
      if (snapshotEqual(snapshot, current)) {
        notifySuccess('无修改')
        dialogRef.value?.close()
        return
      }
      const patch: Parameters<typeof patchMcpServer>[1] = {}
      if (current.transport !== snapshot.transport) {
        patch.transport = current.transport
        if (current.transport === 'stdio') {
          patch.command = current.command
          patch.args = current.args
          patch.env = current.env
          patch.url = undefined
          patch.headers = undefined
        } else {
          patch.url = current.url
          patch.headers = current.headers
          patch.command = undefined
          patch.args = undefined
          patch.env = undefined
        }
      } else {
        // transport 未变：按需包含对端字段
        if (current.transport === 'stdio') {
          if (current.command !== snapshot.command) patch.command = current.command
          if (JSON.stringify(current.args) !== JSON.stringify(snapshot.args)) patch.args = current.args
          if (JSON.stringify(current.env) !== JSON.stringify(snapshot.env)) patch.env = current.env
        } else {
          if (current.url !== snapshot.url) patch.url = current.url
          if (JSON.stringify(current.headers) !== JSON.stringify(snapshot.headers)) {
            patch.headers = current.headers
          }
        }
      }
      if (current.description !== snapshot.description) patch.description = current.description
      if (current.enabled !== snapshot.enabled) patch.enabled = current.enabled
      await patchMcpServer(editingName.value, patch)
      notifySuccess(`已保存：${editingName.value}`)
    }
  } finally {
    dialogRef.value?.setSubmitting(false)
  }

  dialogRef.value?.close()
  editSnapshot.value = null
  tableRef.value?.refresh()
}

// ---------------------------------------------------------------------------
// stdio manifest 同步 / 预览
// ---------------------------------------------------------------------------

async function handlePreviewManifests(): Promise<void> {
  try {
    previewReport.value = await listStdioManifests()
    previewDialogVisible.value = true
  } catch {
    // 拦截器已提示错误
  }
}

async function handleSyncManifests(): Promise<void> {
  try {
    const report = await syncStdioManifests()
    notifySuccess(
      `同步完成：新建 ${report.created.length} / 更新 ${report.updated.length} / 跳过 ${report.skipped.length} / 无效 ${report.invalid.length}`,
    )
    tableRef.value?.refresh()
  } catch {
    // 拦截器已提示错误
  }
}

/** 预览弹窗内「应用同步」按钮：关闭预览弹窗后执行同步 */
async function handleApplySync(): Promise<void> {
  previewDialogVisible.value = false
  await handleSyncManifests()
}

// ---------------------------------------------------------------------------
// 展示辅助
// ---------------------------------------------------------------------------

function healthFor(name: string): HealthSnapshot {
  return healthMap[name] ?? { status: 'unknown', message: '未探测' }
}

function truncatedDescription(text: string): string {
  if (text.length <= 80) return text
  return `${text.slice(0, 80)}…`
}
</script>

<template>
  <div class="page-view">
    <header class="page-view__header">
      <div>
        <h1 class="page-view__title">MCP 管理</h1>
        <p class="page-view__desc">配置与管理 MCP 工具服务连接。</p>
      </div>
      <div class="page-view__actions">
        <el-button class="app-btn app-btn--secondary" @click="handlePreviewManifests">
          预览 manifests
        </el-button>
        <el-button class="app-btn app-btn--secondary" @click="handleSyncManifests">
          同步 manifests
        </el-button>
        <el-button class="app-btn app-btn--primary" @click="handleCreate">
          新建 MCP
        </el-button>
      </div>
    </header>

    <section class="content-card page-view__body">
      <div class="mcp-toolbar">
        <el-input
          v-model="keyword"
          class="mcp-toolbar__search"
          placeholder="按名称模糊搜索"
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
          <span class="mcp-name">{{ (row as McpServerRow).name }}</span>
        </template>
        <template #transport="{ row }">
          <el-tag :type="TRANSPORT_TAG[(row as McpServerRow).transport]" size="small">
            {{ TRANSPORT_LABELS[(row as McpServerRow).transport] }}
          </el-tag>
        </template>
        <template #status="{ row }">
          <div class="mcp-status-cell">
            <el-tag
              :type="(row as McpServerRow).enabled ? 'success' : 'info'"
              size="small"
            >
              {{ (row as McpServerRow).enabled ? '启用' : '禁用' }}
            </el-tag>
            <el-tag
              :type="HEALTH_TAG[healthFor((row as McpServerRow).name).status].type"
              size="small"
            >
              {{ HEALTH_TAG[healthFor((row as McpServerRow).name).status].label }}
            </el-tag>
          </div>
        </template>
        <template #description="{ row }">
          <span :title="(row as McpServerRow).description">
            {{ truncatedDescription((row as McpServerRow).description) || '—' }}
          </span>
        </template>
        <template #actions="{ row }">
          <el-button link type="primary" size="small" @click="handleViewTools(row as McpServerRow)">
            查看工具
          </el-button>
          <el-button
            link
            type="primary"
            size="small"
            @click="handleTestConnection(row as McpServerRow)"
          >
            测试连接
          </el-button>
          <el-button link type="primary" size="small" @click="handleEdit(row as McpServerRow)">
            编辑
          </el-button>
          <el-button link type="danger" size="small" @click="handleDelete(row as McpServerRow)">
            删除
          </el-button>
        </template>
      </WebAgentTable>
    </section>

    <!-- 创建 / 编辑弹窗 -->
    <WebAgentFormDialog
      ref="dialogRef"
      v-model="dialogVisible"
      :title="editingName === null ? '新建 MCP server' : `编辑 MCP server — ${editingName}`"
      width="640px"
      :rules="rules"
      @submit="handleSubmit"
    >
      <template #default="{ form, mode }">
        <el-form-item label="名称" prop="name">
          <el-input
            v-model="form.name"
            placeholder="请输入 MCP server 名称"
            :disabled="mode === 'edit'"
          />
        </el-form-item>
        <el-form-item label="传输" prop="transport">
          <el-select v-model="form.transport" placeholder="请选择传输类型">
            <el-option
              v-for="item in TRANSPORTS"
              :key="item"
              :label="TRANSPORT_LABELS[item]"
              :value="item"
            />
          </el-select>
        </el-form-item>
        <el-form-item label="描述">
          <el-input
            v-model="form.description"
            type="textarea"
            :rows="2"
            placeholder="可选：MCP server 用途说明"
          />
        </el-form-item>
        <el-form-item label="启用">
          <el-switch v-model="form.enabled" />
        </el-form-item>

        <!-- ===== stdio 条件字段 ===== -->
        <template v-if="form.transport === 'stdio'">
          <el-form-item label="command" prop="command">
            <el-input
              v-model="form.command"
              placeholder="例如：python（basename 必须在 MCP_STDIO_ALLOWED_COMMANDS 白名单）"
            />
          </el-form-item>
          <el-form-item label="args">
            <div class="mcp-kv-rows">
              <div v-for="(_, i) in form.args" :key="`arg-${i}`" class="mcp-kv-rows__row">
                <el-input
                  :model-value="argAt(form, i)"
                  placeholder="例如：/app/mcp-servers/stdio_demo.py"
                  @update:model-value="setArgAt(form, i, $event)"
                />
                <el-button
                  class="app-btn app-btn--secondary mcp-kv-rows__btn"
                  size="small"
                  @click="removeArg(form, i)"
                >
                  移除
                </el-button>
              </div>
              <el-button
                class="app-btn app-btn--secondary"
                size="small"
                @click="addArg(form)"
              >
                + 添加参数
              </el-button>
            </div>
          </el-form-item>
          <el-form-item label="env">
            <div class="mcp-kv-rows">
              <div
                v-for="key in envKeys(form)"
                :key="`env-${key}`"
                class="mcp-kv-rows__row mcp-kv-rows__row--kv"
              >
                <el-input :model-value="key" placeholder="KEY" disabled />
                <el-input
                  :model-value="envValue(form, key)"
                  placeholder="VALUE，例如 ${MY_TOKEN}"
                  @update:model-value="setEnvValue(form, key, $event)"
                />
                <el-button
                  class="app-btn app-btn--secondary mcp-kv-rows__btn"
                  size="small"
                  @click="removeEnv(form, key)"
                >
                  移除
                </el-button>
              </div>
              <div class="mcp-kv-rows__add">
                <el-input
                  v-model="newEnvKey"
                  placeholder="新增 KEY"
                  class="mcp-kv-rows__add-input"
                />
                <el-input
                  v-model="newEnvValue"
                  placeholder="新增 VALUE"
                  class="mcp-kv-rows__add-input"
                />
                <el-button
                  class="app-btn app-btn--secondary"
                  size="small"
                  :disabled="newEnvKey.trim().length === 0"
                  @click="addEnvFromInput(form)"
                >
                  + 添加环境变量
                </el-button>
              </div>
            </div>
          </el-form-item>
        </template>

        <!-- ===== sse / http 条件字段 ===== -->
        <template v-else>
          <el-form-item label="url" prop="url">
            <el-input
              v-model="form.url"
              :placeholder="form.transport === 'sse' ? '例如：http://127.0.0.1:9375/sse' : '例如：http://host:port/mcp'"
            />
          </el-form-item>
          <el-form-item label="headers">
            <div class="mcp-kv-rows">
              <div
                v-for="key in headerKeys(form)"
                :key="`hdr-${key}`"
                class="mcp-kv-rows__row mcp-kv-rows__row--kv"
              >
                <el-input :model-value="key" placeholder="HEADER" disabled />
                <el-input
                  :model-value="headerValue(form, key)"
                  placeholder="VALUE，例如 ${MY_TOKEN}"
                  @update:model-value="setHeaderValue(form, key, $event)"
                />
                <el-button
                  class="app-btn app-btn--secondary mcp-kv-rows__btn"
                  size="small"
                  @click="removeHeader(form, key)"
                >
                  移除
                </el-button>
              </div>
              <div class="mcp-kv-rows__add">
                <el-input
                  v-model="newHeaderKey"
                  placeholder="新增 HEADER"
                  class="mcp-kv-rows__add-input"
                />
                <el-input
                  v-model="newHeaderValue"
                  placeholder="新增 VALUE"
                  class="mcp-kv-rows__add-input"
                />
                <el-button
                  class="app-btn app-btn--secondary"
                  size="small"
                  :disabled="newHeaderKey.trim().length === 0"
                  @click="addHeaderFromInput(form)"
                >
                  + 添加 header
                </el-button>
              </div>
            </div>
          </el-form-item>
        </template>
      </template>
    </WebAgentFormDialog>

    <!-- 查看工具弹窗 -->
    <McpServerToolsDialog
      v-if="toolsDialogServerName"
      v-model="toolsDialogVisible"
      :server-name="toolsDialogServerName"
    />

    <!-- 预览 manifest 报告弹窗（只读） -->
    <el-dialog
      v-model="previewDialogVisible"
      title="stdio manifest 同步预览"
      width="720px"
    >
      <div v-if="previewReport" class="mcp-preview">
        <p class="mcp-preview__summary">
          扫描 <strong>{{ previewReport.scanned }}</strong> 个文件；
          将新建 <strong>{{ previewReport.created.length }}</strong>
          / 更新 <strong>{{ previewReport.updated.length }}</strong>
          / 无变化 <strong>{{ previewReport.unchanged.length }}</strong>
          / 跳过 <strong>{{ previewReport.skipped.length }}</strong>
          / 无效 <strong>{{ previewReport.invalid.length }}</strong>
        </p>
        <div v-if="previewReport.created.length > 0" class="mcp-preview__section">
          <h4 class="mcp-preview__title">将新建</h4>
          <ul>
            <li v-for="name in previewReport.created" :key="`c-${name}`">
              <code>{{ name }}</code>
            </li>
          </ul>
        </div>
        <div v-if="previewReport.updated.length > 0" class="mcp-preview__section">
          <h4 class="mcp-preview__title">将更新</h4>
          <ul>
            <li v-for="name in previewReport.updated" :key="`u-${name}`">
              <code>{{ name }}</code>
            </li>
          </ul>
        </div>
        <div v-if="previewReport.unchanged.length > 0" class="mcp-preview__section">
          <h4 class="mcp-preview__title">无变化</h4>
          <ul>
            <li v-for="name in previewReport.unchanged" :key="`n-${name}`">
              <code>{{ name }}</code>
            </li>
          </ul>
        </div>
        <div v-if="previewReport.skipped.length > 0" class="mcp-preview__section">
          <h4 class="mcp-preview__title">跳过</h4>
          <ul>
            <li v-for="item in previewReport.skipped" :key="`sk-${item.name}`">
              <code>{{ item.name }}</code>: {{ item.reason }}
            </li>
          </ul>
        </div>
        <div v-if="previewReport.invalid.length > 0" class="mcp-preview__section">
          <h4 class="mcp-preview__title">无效</h4>
          <ul>
            <li v-for="item in previewReport.invalid" :key="`iv-${item.file}`">
              <code>{{ item.file }}</code>: {{ item.reason }}
            </li>
          </ul>
        </div>
        <p class="mcp-preview__hint">
          仅预览未写入数据库；需应用请点页头「同步 manifests」。
        </p>
      </div>
      <template #footer>
        <el-button @click="previewDialogVisible = false">关闭</el-button>
        <el-button
          class="app-btn app-btn--primary"
          @click="handleApplySync"
        >
          应用同步
        </el-button>
      </template>
    </el-dialog>
  </div>
</template>

<style scoped>
.mcp-name {
  font-weight: 600;
  color: var(--color-text-primary);
}
.mcp-toolbar {
  display: flex;
  align-items: center;
  justify-content: flex-start;
  margin-bottom: 16px;
}
.mcp-toolbar__search {
  width: 280px;
}
.mcp-status-cell {
  display: inline-flex;
  gap: 6px;
  align-items: center;
}
.mcp-kv-rows {
  display: flex;
  flex-direction: column;
  gap: 8px;
  width: 100%;
}
.mcp-kv-rows__row {
  display: flex;
  align-items: center;
  gap: 8px;
}
.mcp-kv-rows__row--kv :deep(.el-input) {
  flex: 1;
}
.mcp-kv-rows__btn {
  flex-shrink: 0;
}
.mcp-kv-rows__add {
  display: flex;
  align-items: center;
  gap: 8px;
  margin-top: 4px;
}
.mcp-kv-rows__add-input {
  flex: 1;
}
.mcp-preview__summary {
  margin: 0 0 16px;
  font-size: 14px;
  color: var(--color-text-primary);
}
.mcp-preview__section {
  margin-bottom: 16px;
}
.mcp-preview__title {
  margin: 0 0 8px;
  font-size: 13px;
  font-weight: 600;
  color: var(--color-text-secondary);
}
.mcp-preview__section ul {
  margin: 0;
  padding-left: 20px;
}
.mcp-preview__section li {
  font-size: 12px;
  color: var(--color-text-primary);
  margin-bottom: 4px;
}
.mcp-preview__hint {
  margin: 8px 0 0;
  font-size: 12px;
  color: var(--color-text-secondary);
}
</style>