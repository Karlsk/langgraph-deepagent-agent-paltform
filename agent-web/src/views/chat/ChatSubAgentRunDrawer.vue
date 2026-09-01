<script setup lang="ts">
/**
 * 子智能体执行详情抽屉：
 * 聊天流中「子智能体执行卡片」点击入口 → el-drawer 右滑出；
 * 展示该次运行收集的完整文本 + 工具调用明细（逐条折叠，默认收起，
 * 模式复用 ChatMessageList 的 tool 面板不引组件）。流式期间打开可
 * 实时跟随（内容来自 useChatStream 归并的 subagent_run 视图模型，
 * 响应式引用）。与 ChatTraceDrawer 同形态（480px 右滑），数据源为
 * 本地会话收集，不依赖后端 trace 行 / CHAT_TRACE_ENABLED。
 */
import { computed, ref } from 'vue'

const props = defineProps<{
  modelValue: boolean
  /** 选中运行的 subagent 名（null = 未选中） */
  source: string | null
  /** 选中运行收集的完整文本 */
  content: string
  /** 选中运行收集的工具调用（名称 + 输出） */
  toolCalls?: Array<{ name: string; content: string }>
}>()

const emit = defineEmits<{
  'update:modelValue': [value: boolean]
}>()

/** 抽屉标题：子智能体名缺失时降级为通用标题 */
const title = computed(() =>
  props.source !== null ? `子智能体执行 — ${props.source}` : '子智能体执行',
)

/** 文本与工具调用均空才降级为提示 */
const isEmpty = computed(
  () => props.content.trim() === '' && (props.toolCalls?.length ?? 0) === 0,
)

/** 工具调用展开项集合（按索引，默认全折叠） */
const expandedIndexes = ref<number[]>([])

function toggleToolCall(index: number): void {
  const at = expandedIndexes.value.indexOf(index)
  if (at >= 0) {
    expandedIndexes.value.splice(at, 1)
  } else {
    expandedIndexes.value.push(index)
  }
}

function isExpanded(index: number): boolean {
  return expandedIndexes.value.includes(index)
}

function close(): void {
  emit('update:modelValue', false)
}
</script>

<template>
  <el-drawer :model-value="modelValue" :title="title" size="480px" @update:model-value="close">
    <div class="chat-subagent-run-drawer">
      <template v-if="!isEmpty">
        <pre v-if="content.trim() !== ''" class="chat-subagent-run-drawer__content">{{ content }}</pre>
        <div
          v-for="(call, index) in toolCalls ?? []"
          :key="index"
          class="chat-subagent-run-drawer__tool"
        >
          <button
            type="button"
            class="chat-subagent-run-drawer__tool-header"
            @click="toggleToolCall(index)"
          >
            <span class="chat-subagent-run-drawer__tool-name">{{ call.name }}</span>
            <span class="chat-subagent-run-drawer__tool-summary">{{ call.content }}</span>
            <span class="chat-subagent-run-drawer__tool-toggle">
              {{ isExpanded(index) ? '收起' : '展开' }}
            </span>
          </button>
          <pre
            v-if="isExpanded(index)"
            class="chat-subagent-run-drawer__tool-body"
          >{{ call.content }}</pre>
        </div>
      </template>
      <p v-else class="chat-subagent-run-drawer__hint">
        {{ source !== null ? '该子智能体尚未产出文本内容。' : '未选中任何子智能体运行。' }}
      </p>
    </div>
  </el-drawer>
</template>

<style scoped>
.chat-subagent-run-drawer {
  display: flex;
  flex-direction: column;
  gap: 12px;
  min-height: 0;
}

.chat-subagent-run-drawer__content {
  margin: 0;
  padding: 10px 12px;
  border: 1px solid var(--color-border-default);
  border-radius: var(--radius-sm, 6px);
  background: var(--color-bg-surface);
  white-space: pre-wrap;
  word-break: break-word;
  font-size: 13px;
  line-height: 1.6;
}

.chat-subagent-run-drawer__tool {
  border: 1px solid var(--color-border-default);
  border-radius: var(--radius-sm, 6px);
  background: var(--color-bg-surface);
}

.chat-subagent-run-drawer__tool-header {
  display: flex;
  align-items: baseline;
  gap: 8px;
  width: 100%;
  padding: 6px 10px;
  border: none;
  background: transparent;
  cursor: pointer;
  text-align: left;
  font: inherit;
  color: inherit;
}

.chat-subagent-run-drawer__tool-name {
  font-family: var(--app-font-display, monospace);
  font-weight: 600;
  color: var(--color-text-primary);
}

.chat-subagent-run-drawer__tool-summary {
  flex: 1;
  min-width: 0;
  overflow: hidden;
  text-overflow: ellipsis;
  white-space: nowrap;
  color: var(--color-text-secondary);
}

.chat-subagent-run-drawer__tool-toggle {
  font-size: 12px;
  color: var(--color-text-secondary);
  white-space: nowrap;
}

.chat-subagent-run-drawer__tool-body {
  margin: 0;
  padding: 8px 10px;
  border-top: 1px solid var(--color-border-default);
  max-height: 280px;
  overflow: auto;
  white-space: pre-wrap;
  word-break: break-word;
  font-size: 12px;
}

.chat-subagent-run-drawer__hint {
  margin: 0;
  color: var(--color-text-secondary);
  font-size: 13px;
}
</style>
