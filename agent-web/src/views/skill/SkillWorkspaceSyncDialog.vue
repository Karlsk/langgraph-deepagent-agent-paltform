<script setup lang="ts">
/**
 * 目录对账弹窗：展示 planSkillWorkspaceSync / applySkillWorkspaceSync 返回的
 * SkillSyncReport（per-entry 明细 + 五分支计数）。
 *
 * 纯只读展示组件：数据由父组件在打开前写入 props.report，弹窗自身不发请求。
 * preview 模式 footer 提供「应用同步」按钮（emit apply），applied 模式仅展示
 * 执行结果。action 四态文案与 tag 颜色：
 * - unchanged → info「无变化」（DB 行与磁盘文件一致）
 * - rewritten → success「已从 DB 重建」（漂移/缺失文件以 DB 为准重写）
 * - imported  → success「已导入」（磁盘独有文件导入为新 DB 行）
 * - invalid   → danger「无效文件」（解析失败/名称冲突/超大，逐项降级）
 */
import type { SkillSyncAction, SkillSyncReport } from '@/api/assets'

const props = defineProps<{
  /** v-model 控制显隐 */
  modelValue: boolean
  /** 父组件在打开前写入的对账报告（保证非空才挂载） */
  report: SkillSyncReport
  /** preview = dry-run 预览（footer 可应用）；applied = 执行结果 */
  mode: 'preview' | 'applied'
}>()

const emit = defineEmits<{
  'update:modelValue': [value: boolean]
  /** preview 模式 footer「应用同步」点击转发（由父组件执行 apply） */
  apply: []
}>()

/** action → 展示文案 */
const ACTION_LABELS: Record<SkillSyncAction, string> = {
  unchanged: '无变化',
  rewritten: '已从 DB 重建',
  imported: '已导入',
  invalid: '无效文件',
}

/** action → el-tag type（invalid 用 danger，其余对齐语义色） */
const ACTION_TAG_TYPES: Record<SkillSyncAction, 'success' | 'info' | 'danger'> = {
  unchanged: 'info',
  rewritten: 'success',
  imported: 'success',
  invalid: 'danger',
}

function actionLabel(action: SkillSyncAction): string {
  return ACTION_LABELS[action]
}

function actionTagType(action: SkillSyncAction): 'success' | 'info' | 'danger' {
  return ACTION_TAG_TYPES[action]
}

/** 原因列：invalid 条目展示 reason，其余显示占位符 */
function reasonOf(entry: { action: SkillSyncAction; reason?: string | null }): string {
  return entry.action === 'invalid' && entry.reason ? entry.reason : '—'
}
</script>

<template>
  <el-dialog
    :model-value="props.modelValue"
    :title="props.mode === 'preview' ? '目录对账预览（未写入）' : '目录对账结果'"
    width="640px"
    :close-on-click-modal="false"
    @update:model-value="emit('update:modelValue', $event)"
  >
    <p class="sync-report-summary">
      扫描 {{ props.report.scanned }} 个文件：
      <span class="sync-report-summary__item">无变化 {{ props.report.unchanged }}</span>
      <span class="sync-report-summary__item">已从 DB 重建 {{ props.report.rewritten }}</span>
      <span class="sync-report-summary__item">已导入 {{ props.report.imported }}</span>
      <span class="sync-report-summary__item sync-report-summary__item--danger">
        无效 {{ props.report.invalid }}
      </span>
    </p>
    <el-table :data="props.report.items" size="small" border>
      <el-table-column prop="name" label="名称" min-width="200" show-overflow-tooltip />
      <el-table-column label="结果" width="150">
        <template #default="{ row }">
          <el-tag :type="actionTagType(row.action)" size="small">
            {{ actionLabel(row.action) }}
          </el-tag>
        </template>
      </el-table-column>
      <el-table-column label="原因" min-width="160" show-overflow-tooltip>
        <template #default="{ row }">
          {{ reasonOf(row) }}
        </template>
      </el-table-column>
    </el-table>
    <p v-if="props.mode === 'preview'" class="sync-report-hint">
      仅预览；应用后将按 DB 为准重写漂移文件并导入目录独有技能。
    </p>
    <template #footer>
      <el-button @click="emit('update:modelValue', false)">关闭</el-button>
      <el-button
        v-if="props.mode === 'preview'"
        class="app-btn app-btn--primary"
        @click="emit('apply')"
      >
        应用同步
      </el-button>
    </template>
  </el-dialog>
</template>

<style scoped>
.sync-report-summary {
  margin: 0 0 12px;
  color: var(--color-text-secondary);
  font-size: 13px;
}
.sync-report-summary__item {
  margin-left: 12px;
}
.sync-report-summary__item--danger {
  color: var(--color-danger-600);
}
.sync-report-hint {
  margin: 12px 0 0;
  color: var(--color-text-tertiary);
  font-size: 12px;
  line-height: 1.4;
}
</style>
