<script setup lang="ts">
/**
 * 磁盘同步报告弹窗：展示 refreshAllSkills / refreshSkill 返回的
 * SkillRefreshReport（per-skill 明细 + 四态计数）。
 *
 * 纯只读展示组件：数据由父组件在打开前写入 props.report，弹窗自身不发请求。
 * action 四态文案与 tag 颜色：
 * - rewritten  → success「已重建」（磁盘缺失/漂移，已从 DB 重写）
 * - unchanged  → info「已是最新」（hash 一致，磁盘未动）
 * - backfilled → warning「已回填 DB」（legacy 行从磁盘回填正文）
 * - missing    → danger「双丢不可恢复」（DB 与磁盘均已丢失）
 */
import type { SkillRefreshAction, SkillRefreshReport } from '@/api/assets'

const props = defineProps<{
  /** v-model 控制显隐 */
  modelValue: boolean
  /** 父组件在打开前写入的刷新报告（保证非空才挂载） */
  report: SkillRefreshReport
}>()

const emit = defineEmits<{
  'update:modelValue': [value: boolean]
}>()

/** action → 展示文案 */
const ACTION_LABELS: Record<SkillRefreshAction, string> = {
  rewritten: '已重建',
  unchanged: '已是最新',
  backfilled: '已回填 DB',
  missing: '双丢不可恢复',
}

/** action → el-tag type（missing 用 danger，其余对齐语义色） */
const ACTION_TAG_TYPES: Record<SkillRefreshAction, 'success' | 'info' | 'warning' | 'danger'> = {
  rewritten: 'success',
  unchanged: 'info',
  backfilled: 'warning',
  missing: 'danger',
}

function actionLabel(action: SkillRefreshAction): string {
  return ACTION_LABELS[action]
}

function actionTagType(action: SkillRefreshAction): 'success' | 'info' | 'warning' | 'danger' {
  return ACTION_TAG_TYPES[action]
}
</script>

<template>
  <el-dialog
    :model-value="props.modelValue"
    title="磁盘同步报告"
    width="560px"
    :close-on-click-modal="false"
    @update:model-value="emit('update:modelValue', $event)"
  >
    <p class="refresh-report-summary">
      共 {{ props.report.total }} 项：
      <span class="refresh-report-summary__item">已重建 {{ props.report.rewritten }}</span>
      <span class="refresh-report-summary__item">已是最新 {{ props.report.unchanged }}</span>
      <span class="refresh-report-summary__item">已回填 DB {{ props.report.backfilled }}</span>
      <span class="refresh-report-summary__item refresh-report-summary__item--danger">
        不可恢复 {{ props.report.missing }}
      </span>
    </p>
    <el-table :data="props.report.items" size="small" border>
      <el-table-column prop="name" label="名称" min-width="180" />
      <el-table-column label="结果" width="160">
        <template #default="{ row }">
          <el-tag :type="actionTagType(row.action)" size="small">
            {{ actionLabel(row.action) }}
          </el-tag>
        </template>
      </el-table-column>
    </el-table>
  </el-dialog>
</template>

<style scoped>
.refresh-report-summary {
  margin: 0 0 12px;
  color: var(--color-text-secondary);
  font-size: 13px;
}
.refresh-report-summary__item {
  margin-left: 12px;
}
.refresh-report-summary__item--danger {
  color: var(--color-danger-600);
}
</style>
