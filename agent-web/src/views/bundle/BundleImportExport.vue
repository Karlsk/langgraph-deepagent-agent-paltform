<script setup lang="ts">
/**
 * 配置迁移页：Bundle 导出 / 导入。
 *
 * 一级侧边栏入口「配置迁移」→ 页面内二级 el-tabs：
 *   Tab 1「导出配置」：按模块勾选 → 下载 JSON bundle
 *   Tab 2「导入配置」：上传 JSON → 预览 → 选择性导入
 */
import { ref, computed, onMounted } from 'vue'
import {
  Download,
  Upload,
  FolderOpened,
  Check,
  Refresh,
} from '@element-plus/icons-vue'

import {
  getBundleCatalog,
  exportBundle,
  previewBundleImport,
  importBundle,
  type CatalogItem,
  type CatalogResponse,
  type PreviewItem,
  type PreviewResponse,
  type ImportResponse,
  type EntitySelector,
} from '@/api/bundle'
import { notifySuccess } from '@/utils/notify'

// ---------------------------------------------------------------------------
// State
// ---------------------------------------------------------------------------

const activeTab = ref('export')

// --- Export state ---
const catalog = ref<CatalogResponse | null>(null)
const loadingCatalog = ref(false)
const exporting = ref(false)

/** Per-entity-type checkbox selection (names array) */
const exportSelection = ref<Record<string, string[]>>({
  providers: [],
  skills: [],
  subagents: [],
  apps: [],
  mcps: [],
})

/** Per-entity-type "select all" checkbox state */
const selectAll = ref<Record<string, boolean>>({
  providers: false,
  skills: false,
  subagents: false,
  apps: false,
  mcps: false,
})

const ENTITY_LABELS: Record<string, string> = {
  providers: '提供商 Providers',
  skills: '技能 Skills',
  subagents: '子代理 SubAgents',
  apps: 'Agent 应用',
  mcps: 'MCP 服务器',
}

const ENTITY_TYPES = ['providers', 'skills', 'subagents', 'apps', 'mcps'] as const

// --- Import state ---
const importFile = ref<File | null>(null)
const importPreview = ref<PreviewResponse | null>(null)
const loadingPreview = ref(false)
const importing = ref(false)
const importResult = ref<ImportResponse | null>(null)

/** Per-entity-type checkbox for import (names to import) */
const importSelection = ref<Record<string, string[]>>({
  providers: [],
  skills: [],
  subagents: [],
  apps: [],
  mcps: [],
})

// ---------------------------------------------------------------------------
// Export logic
// ---------------------------------------------------------------------------

async function loadCatalog(): Promise<void> {
  loadingCatalog.value = true
  try {
    catalog.value = await getBundleCatalog()
  } finally {
    loadingCatalog.value = false
  }
}

function toggleSelectAll(entityType: string): void {
  if (!catalog.value) return
  if (selectAll.value[entityType]) {
    exportSelection.value[entityType] = catalog.value[entityType as keyof CatalogResponse].map(
      (item: CatalogItem) => item.name,
    )
  } else {
    exportSelection.value[entityType] = []
  }
}

function onSelectionChange(entityType: string): void {
  if (!catalog.value) return
  const total = catalog.value[entityType as keyof CatalogResponse].length
  selectAll.value[entityType] =
    exportSelection.value[entityType].length === total && total > 0
}

async function handleExport(): Promise<void> {
  exporting.value = true
  try {
    // Build selection: omit types with no selection, use '*' for full, else name list
    const selection: Record<string, EntitySelector> = {}
    for (const entityType of ENTITY_TYPES) {
      const names = exportSelection.value[entityType]
      if (!names || names.length === 0) continue
      if (selectAll.value[entityType]) {
        selection[entityType] = '*'
      } else {
        selection[entityType] = names
      }
    }

    if (Object.keys(selection).length === 0) {
      return
    }

    const blob = await exportBundle(selection)
    // Trigger browser download
    const url = URL.createObjectURL(blob as unknown as Blob)
    const a = document.createElement('a')
    const today = new Date().toISOString().slice(0, 10)
    a.href = url
    a.download = `bundle-${today}.json`
    document.body.appendChild(a)
    a.click()
    document.body.removeChild(a)
    URL.revokeObjectURL(url)
    notifySuccess('导出成功')
  } finally {
    exporting.value = false
  }
}

// ---------------------------------------------------------------------------
// Import logic
// ---------------------------------------------------------------------------

function handleFileChange(file: File): void {
  importFile.value = file
  importPreview.value = null
  importResult.value = null
  // Reset import selection
  for (const entityType of ENTITY_TYPES) {
    importSelection.value[entityType] = []
  }
}

async function handlePreview(): Promise<void> {
  if (!importFile.value) return
  loadingPreview.value = true
  try {
    importPreview.value = await previewBundleImport(importFile.value)
    // Pre-select all "create" items
    for (const entityType of ENTITY_TYPES) {
      const items = importPreview.value[entityType as keyof PreviewResponse] || []
      importSelection.value[entityType] = items
        .filter((item: PreviewItem) => item.action === 'create')
        .map((item: PreviewItem) => item.name)
    }
  } finally {
    loadingPreview.value = false
  }
}

async function handleImport(): Promise<void> {
  if (!importFile.value || !importPreview.value) return

  // Build selection from checkboxes
  const selection: Record<string, EntitySelector> = {}
  for (const entityType of ENTITY_TYPES) {
    const names = importSelection.value[entityType]
    if (names && names.length > 0) {
      selection[entityType] = names
    }
  }

  if (Object.keys(selection).length === 0) return

  // Read file content as JSON
  const content = await importFile.value.text()
  const bundle = JSON.parse(content) as Record<string, unknown>

  importing.value = true
  try {
    importResult.value = await importBundle(bundle, selection)
    notifySuccess('导入完成')
  } finally {
    importing.value = false
  }
}

function resetImport(): void {
  importFile.value = null
  importPreview.value = null
  importResult.value = null
  for (const entityType of ENTITY_TYPES) {
    importSelection.value[entityType] = []
  }
}

// ---------------------------------------------------------------------------
// Helpers
// ---------------------------------------------------------------------------

function getStatusTagType(action: string): '' | 'success' | 'warning' | 'info' {
  if (action === 'create') return 'success'
  if (action === 'skip') return 'info'
  return ''
}

function getImportStatusType(status: string): '' | 'success' | 'warning' | 'danger' {
  if (status === 'created') return 'success'
  if (status === 'skipped') return 'warning'
  if (status === 'error') return 'danger'
  return ''
}

const totalSelected = computed(() => {
  let count = 0
  for (const entityType of ENTITY_TYPES) {
    count += exportSelection.value[entityType].length
  }
  return count
})

const hasPreview = computed(() => {
  if (!importPreview.value) return false
  return ENTITY_TYPES.some(
    (t) => (importPreview.value![t as keyof PreviewResponse] || []).length > 0,
  )
})

// ---------------------------------------------------------------------------
// Lifecycle
// ---------------------------------------------------------------------------

onMounted(() => {
  loadCatalog()
})
</script>

<template>
  <div class="bundle-page">
    <el-tabs v-model="activeTab" class="bundle-tabs">
      <!-- ============================== Export Tab ============================== -->
      <el-tab-pane label="导出配置" name="export">
        <div class="bundle-section">
          <p class="bundle-section__hint">
            选择要导出的配置模块和条目，导出为 JSON 文件用于跨环境迁移。
          </p>

          <div v-if="loadingCatalog" class="bundle-loading">
            <el-icon class="is-loading"><Refresh /></el-icon>
            <span>加载中...</span>
          </div>

          <template v-else-if="catalog">
            <div
              v-for="entityType in ENTITY_TYPES"
              :key="entityType"
              class="bundle-card"
            >
              <div class="bundle-card__header">
                <h3 class="bundle-card__title">{{ ENTITY_LABELS[entityType] }}</h3>
                <el-checkbox
                  v-model="selectAll[entityType]"
                  :disabled="(catalog[entityType as keyof CatalogResponse] || []).length === 0"
                  @change="toggleSelectAll(entityType)"
                >
                  全选
                </el-checkbox>
              </div>

              <div
                v-if="(catalog[entityType as keyof CatalogResponse] || []).length === 0"
                class="bundle-card__empty"
              >
                暂无数据
              </div>

              <el-checkbox-group
                v-else
                v-model="exportSelection[entityType]"
                @change="onSelectionChange(entityType)"
              >
                <div class="bundle-card__items">
                  <el-checkbox
                    v-for="item in catalog[entityType as keyof CatalogResponse]"
                    :key="item.name"
                    :value="item.name"
                    class="bundle-card__item"
                  >
                    <span class="bundle-card__item-name">{{ item.name }}</span>
                    <span v-if="item.description" class="bundle-card__item-desc">
                      {{ item.description }}
                    </span>
                  </el-checkbox>
                </div>
              </el-checkbox-group>
            </div>
          </template>

          <div class="bundle-actions">
            <el-button
              type="primary"
              :icon="Download"
              :loading="exporting"
              :disabled="totalSelected === 0"
              @click="handleExport"
            >
              导出 ({{ totalSelected }} 条目)
            </el-button>
          </div>
        </div>
      </el-tab-pane>

      <!-- ============================== Import Tab ============================== -->
      <el-tab-pane label="导入配置" name="import">
        <div class="bundle-section">
          <p class="bundle-section__hint">
            上传 JSON bundle 文件，预览后选择要导入的条目。同名条目默认跳过。
          </p>

          <!-- Upload area -->
          <div v-if="!importFile" class="bundle-upload">
            <el-upload
              class="bundle-upload__area"
              drag
              :auto-upload="false"
              :show-file-list="false"
              accept=".json"
              :on-change="(file: any) => handleFileChange(file.raw)"
            >
              <el-icon class="el-icon--upload"><Upload /></el-icon>
              <div class="el-upload__text">
                拖拽文件到此处，或 <em>点击选择</em>
              </div>
              <template #tip>
                <div class="el-upload__tip">仅支持 .json 格式的 bundle 文件</div>
              </template>
            </el-upload>
          </div>

          <!-- File loaded, preview button -->
          <div v-else-if="!importPreview && !importResult" class="bundle-file-info">
            <el-icon><FolderOpened /></el-icon>
            <span>{{ importFile.name }}</span>
            <el-button type="primary" :loading="loadingPreview" @click="handlePreview">
              解析预览
            </el-button>
            <el-button @click="resetImport">重新选择</el-button>
          </div>

          <!-- Preview result -->
          <template v-else-if="importPreview && !importResult">
            <div
              v-for="entityType in ENTITY_TYPES"
              :key="entityType"
              class="bundle-card"
            >
              <div class="bundle-card__header">
                <h3 class="bundle-card__title">{{ ENTITY_LABELS[entityType] }}</h3>
              </div>

              <div
                v-if="(importPreview[entityType as keyof PreviewResponse] || []).length === 0"
                class="bundle-card__empty"
              >
                无条目
              </div>

              <template v-else>
                <el-checkbox-group v-model="importSelection[entityType]">
                  <div class="bundle-card__items">
                    <el-checkbox
                      v-for="item in importPreview[entityType as keyof PreviewResponse]"
                      :key="item.name"
                      :value="item.name"
                      :disabled="item.action === 'skip'"
                      class="bundle-card__item"
                    >
                      <span class="bundle-card__item-name">{{ item.name }}</span>
                      <el-tag
                        :type="getStatusTagType(item.action)"
                        size="small"
                        class="bundle-card__tag"
                      >
                        {{ item.action === 'create' ? '新增' : '跳过' }}
                      </el-tag>
                      <span v-if="item.reason" class="bundle-card__item-reason">
                        ({{ item.reason }})
                      </span>
                    </el-checkbox>
                  </div>
                </el-checkbox-group>
              </template>
            </div>

            <div class="bundle-actions">
              <el-button @click="resetImport">重新选择</el-button>
              <el-button
                type="primary"
                :icon="Check"
                :loading="importing"
                :disabled="!hasPreview"
                @click="handleImport"
              >
                执行导入
              </el-button>
            </div>
          </template>

          <!-- Import result -->
          <template v-else-if="importResult">
            <el-alert
              type="success"
              :closable="false"
              show-icon
              class="bundle-result-alert"
            >
              <template #title>
                导入完成
              </template>
            </el-alert>

            <div
              v-for="entityType in ENTITY_TYPES"
              :key="entityType"
              class="bundle-card"
            >
              <div class="bundle-card__header">
                <h3 class="bundle-card__title">{{ ENTITY_LABELS[entityType] }}</h3>
              </div>

              <div
                v-if="(importResult[entityType as keyof ImportResponse] || []).length === 0"
                class="bundle-card__empty"
              >
                无条目
              </div>

              <div v-else class="bundle-card__items">
                <div
                  v-for="item in importResult[entityType as keyof ImportResponse]"
                  :key="item.name"
                  class="bundle-result-item"
                >
                  <span class="bundle-card__item-name">{{ item.name }}</span>
                  <el-tag
                    :type="getImportStatusType(item.status)"
                    size="small"
                  >
                    {{ item.status === 'created' ? '已创建' : item.status === 'skipped' ? '已跳过' : '错误' }}
                  </el-tag>
                  <span v-if="item.message" class="bundle-card__item-reason">
                    ({{ item.message }})
                  </span>
                </div>
              </div>
            </div>

            <div class="bundle-actions">
              <el-button type="primary" @click="resetImport">
                继续导入
              </el-button>
            </div>
          </template>
        </div>
      </el-tab-pane>
    </el-tabs>
  </div>
</template>

<style scoped>
.bundle-page {
  max-width: 960px;
}

.bundle-tabs {
  background: var(--color-bg-surface);
  border-radius: var(--radius-lg);
  padding: 16px 24px;
}

.bundle-section__hint {
  color: var(--color-text-secondary);
  font-size: 14px;
  margin-bottom: 16px;
}

.bundle-loading {
  display: flex;
  align-items: center;
  gap: 8px;
  padding: 32px 0;
  color: var(--color-text-secondary);
}

.bundle-card {
  background: var(--color-bg-subtle);
  border: 1px solid var(--color-border-default);
  border-radius: var(--radius-md);
  padding: 16px;
  margin-bottom: 12px;
}

.bundle-card__header {
  display: flex;
  align-items: center;
  justify-content: space-between;
  margin-bottom: 12px;
}

.bundle-card__title {
  font-size: 15px;
  font-weight: 600;
  color: var(--color-text-primary);
  margin: 0;
}

.bundle-card__empty {
  color: var(--color-text-secondary);
  font-size: 13px;
  padding: 8px 0;
}

.bundle-card__items {
  display: flex;
  flex-direction: column;
  gap: 8px;
}

.bundle-card__item {
  display: flex;
  align-items: center;
  gap: 8px;
}

.bundle-card__item-name {
  font-weight: 500;
  color: var(--color-text-primary);
}

.bundle-card__item-desc {
  color: var(--color-text-secondary);
  font-size: 13px;
  margin-left: 4px;
}

.bundle-card__item-reason {
  color: var(--color-text-secondary);
  font-size: 12px;
}

.bundle-card__tag {
  margin-left: 4px;
}

.bundle-upload__area {
  width: 100%;
}

.bundle-file-info {
  display: flex;
  align-items: center;
  gap: 12px;
  padding: 16px;
  background: var(--color-bg-subtle);
  border: 1px solid var(--color-border-default);
  border-radius: var(--radius-md);
  margin-bottom: 16px;
}

.bundle-file-info .el-icon {
  font-size: 20px;
  color: var(--color-primary-500);
}

.bundle-actions {
  display: flex;
  justify-content: flex-end;
  gap: 12px;
  margin-top: 16px;
  padding-top: 16px;
  border-top: 1px solid var(--color-border-default);
}

.bundle-result-alert {
  margin-bottom: 16px;
}

.bundle-result-item {
  display: flex;
  align-items: center;
  gap: 8px;
  padding: 4px 0;
}
</style>
