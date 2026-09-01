<script setup lang="ts">
/**
 * 聊天消息流渲染（G4 spec-g4-chat §9.3 P0 清单）：
 * 纯展示组件，消费 useChatStream 的 ChatViewItem 视图模型——
 * - message：user 右对齐 / assistant 左对齐；streaming 时末尾 assistant
 *   气泡挂闪烁光标（subagent 消息已改由卡片承载，不再内联）
 * - subagent_run：子智能体执行卡片（名称 + 运行态 + 工具计数 + 内容摘要），
 *   整卡可点，emit('open-run', index) 由页面层开抽屉展示完整内容
 * - tool_call：折叠面板（工具名 + 摘要，点击展开完整输出）
 * - summary：「上下文已压缩」灰色细条（§4.3 轮末推送消费端）
 * - decision：历史审批胶囊（已批准 / 已拒绝 N 个操作）
 *
 * 纯文本先行（§9.5）：white-space: pre-wrap，Markdown 渲染记待办。
 */
import { ref } from 'vue'

import type { ChatViewItem } from '@/composables/useChatStream'

defineProps<{
  items: ChatViewItem[]
  /** 流式进行中：末尾 assistant 气泡显示闪烁光标 */
  streaming?: boolean
}>()

defineEmits<{
  /** 点击子智能体执行卡片（携带 items 索引） */
  'open-run': [index: number]
}>()

/** run 卡片摘要：首 80 字符截断 + 总字数（空白压缩成单行预览） */
function runSummary(content: string): string {
  const text = content.replace(/\s+/g, ' ').trim()
  if (text.length === 0) return '（暂无内容）'
  if (text.length <= 80) return text
  return `${text.slice(0, 80)}…（共 ${content.length} 字）`
}

/** tool_call 展开项集合（按 items 索引，默认全折叠） */
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
</script>

<template>
  <div class="chat-message-list">
    <template v-for="(item, index) in items" :key="index">
      <!-- 消息气泡：user 右 / assistant 左 -->
      <div
        v-if="item.kind === 'message'"
        class="chat-message-list__row"
        :class="item.role === 'user'
          ? 'chat-message-list__row--user'
          : 'chat-message-list__row--assistant'"
      >
        <div class="chat-message-list__bubble">
          <span class="chat-message-list__text">{{ item.content }}</span>
          <span
            v-if="streaming && item.role === 'assistant' && index === items.length - 1"
            class="chat-message-list__cursor"
            aria-hidden="true"
          />
        </div>
      </div>

      <!-- 子智能体执行卡片：点击查看完整执行内容 -->
      <div v-else-if="item.kind === 'subagent_run'" class="chat-message-list__row chat-message-list__row--assistant">
        <button type="button" class="chat-message-list__run" @click="$emit('open-run', index)">
          <span class="chat-message-list__run-badge">{{ item.source }}</span>
          <span
            v-if="item.running && streaming"
            class="chat-message-list__run-status chat-message-list__run-status--running"
          >
            执行中…
          </span>
          <span v-if="item.toolCalls.length > 0" class="chat-message-list__run-tools">
            {{ item.toolCalls.length }} 次工具调用
          </span>
          <span class="chat-message-list__run-summary">{{ runSummary(item.content) }}</span>
          <span class="chat-message-list__run-toggle">查看详情</span>
        </button>
      </div>

      <!-- tool_call 折叠面板 -->
      <div v-else-if="item.kind === 'tool_call'" class="chat-message-list__tool">
        <button
          type="button"
          class="chat-message-list__tool-header"
          @click="toggleToolCall(index)"
        >
          <span class="chat-message-list__tool-name">{{ item.name }}</span>
          <span class="chat-message-list__tool-summary">{{ item.content }}</span>
          <span class="chat-message-list__tool-toggle">
            {{ isExpanded(index) ? '收起' : '展开' }}
          </span>
        </button>
        <pre v-if="isExpanded(index)" class="chat-message-list__tool-body">{{ item.content }}</pre>
      </div>

      <!-- 压缩摘要细条 -->
      <div v-else-if="item.kind === 'summary'" class="chat-message-list__summary">
        上下文已压缩：{{ item.content }}
      </div>

      <!-- 历史审批胶囊 -->
      <div v-else class="chat-message-list__decision">
        已批准 {{ item.approved }} / 已拒绝 {{ item.rejected }} 个操作
      </div>
    </template>
  </div>
</template>

<style scoped>
.chat-message-list {
  display: flex;
  flex-direction: column;
  gap: 12px;
}

.chat-message-list__row {
  display: flex;
}

.chat-message-list__row--user {
  justify-content: flex-end;
}

.chat-message-list__row--assistant {
  justify-content: flex-start;
}

.chat-message-list__bubble {
  max-width: 78%;
  padding: 8px 12px;
  border-radius: var(--radius-md, 8px);
  border: 1px solid var(--color-border-default);
  background: var(--color-bg-surface);
  display: flex;
  flex-wrap: wrap;
  align-items: baseline;
  gap: 6px;
}

.chat-message-list__row--user .chat-message-list__bubble {
  background: var(--color-primary-soft, #eef4ff);
  border-color: transparent;
}

.chat-message-list__run {
  display: flex;
  align-items: baseline;
  gap: 8px;
  max-width: 78%;
  padding: 8px 12px;
  border: 1px dashed var(--color-border-default);
  border-radius: var(--radius-md, 8px);
  background: var(--color-bg-subtle, #f8f9fb);
  cursor: pointer;
  text-align: left;
  font: inherit;
  color: inherit;
}

.chat-message-list__run:hover {
  border-color: var(--color-primary, #3b82f6);
}

.chat-message-list__run-badge {
  font-size: 12px;
  padding: 1px 8px;
  border-radius: 999px;
  background: var(--color-primary-soft, #eef4ff);
  color: var(--color-primary, #3b82f6);
  white-space: nowrap;
}

.chat-message-list__run-status--running {
  font-size: 12px;
  color: var(--color-primary, #3b82f6);
  white-space: nowrap;
  animation: chat-message-list-blink 1.2s steps(2) infinite;
}

.chat-message-list__run-tools {
  font-size: 12px;
  color: var(--color-text-secondary);
  white-space: nowrap;
}

.chat-message-list__run-summary {
  flex: 1;
  min-width: 0;
  overflow: hidden;
  text-overflow: ellipsis;
  white-space: nowrap;
  font-size: 13px;
  color: var(--color-text-secondary);
}

.chat-message-list__run-toggle {
  font-size: 12px;
  color: var(--color-text-secondary);
  white-space: nowrap;
}

.chat-message-list__text {
  white-space: pre-wrap;
  word-break: break-word;
}

.chat-message-list__cursor {
  display: inline-block;
  width: 8px;
  height: 1em;
  vertical-align: text-bottom;
  background: var(--color-text-primary);
  animation: chat-message-list-blink 1s steps(2) infinite;
}

@keyframes chat-message-list-blink {
  0%,
  49% {
    opacity: 1;
  }
  50%,
  100% {
    opacity: 0;
  }
}

.chat-message-list__tool {
  border: 1px solid var(--color-border-default);
  border-radius: var(--radius-sm, 6px);
  background: var(--color-bg-surface);
}

.chat-message-list__tool-header {
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

.chat-message-list__tool-name {
  font-family: var(--app-font-display, monospace);
  font-weight: 600;
  color: var(--color-text-primary);
}

.chat-message-list__tool-summary {
  flex: 1;
  min-width: 0;
  overflow: hidden;
  text-overflow: ellipsis;
  white-space: nowrap;
  color: var(--color-text-secondary);
}

.chat-message-list__tool-toggle {
  font-size: 12px;
  color: var(--color-text-secondary);
  white-space: nowrap;
}

.chat-message-list__tool-body {
  margin: 0;
  padding: 8px 10px;
  border-top: 1px solid var(--color-border-default);
  max-height: 280px;
  overflow: auto;
  white-space: pre-wrap;
  word-break: break-word;
  font-size: 12px;
}

.chat-message-list__summary {
  align-self: center;
  padding: 2px 12px;
  border-radius: 999px;
  font-size: 12px;
  color: var(--color-text-secondary);
  background: var(--color-bg-secondary, #f2f4f7);
}

.chat-message-list__decision {
  align-self: center;
  padding: 2px 12px;
  border-radius: 999px;
  font-size: 12px;
  color: var(--color-text-secondary);
  border: 1px dashed var(--color-border-default);
}
</style>
