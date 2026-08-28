<script setup lang="ts">
/**
 * HIL 审批卡片（G4 spec-g4-chat §5.2/§9.3）：
 * interrupt 帧 / pending 恢复（§5.3）重建的审批入口——
 * - action_requests 逐卡列出：tool 名 + args 折叠查看；
 * - 每卡「批准 / 拒绝」独立选择，默认全部批准（§5.2 主路径）；
 * - 「提交决定」emit 布尔数组，由 useChatStream.submitDecisions 走
 *   decisions JSON 消息通道 resume；submitting 期间锁定提交钮。
 */
import { ref, watch } from 'vue'

import type { InterruptPayload } from '@/types'

const props = defineProps<{
  interrupt: InterruptPayload
  /** 提交进行中（流式 resume 未返回前）锁定按钮防重复提交 */
  submitting?: boolean
}>()

const emit = defineEmits<{
  submit: [approvals: boolean[]]
}>()

/** 与 action_requests 一一对应的选择状态；interrupt 变化时重置为全批准 */
const approvals = ref<boolean[]>([])

watch(
  () => props.interrupt,
  (value) => {
    approvals.value = value.action_requests.map(() => true)
  },
  { immediate: true, deep: true },
)

function submit(): void {
  emit('submit', [...approvals.value])
}
</script>

<template>
  <div class="chat-hil-card">
    <div class="chat-hil-card__title">
      待审批操作（{{ interrupt.action_requests.length }}）——请逐项确认
    </div>
    <div
      v-for="(action, index) in interrupt.action_requests"
      :key="index"
      class="chat-hil-card__action"
      :class="approvals[index] ? '' : 'chat-hil-card__action--rejected'"
    >
      <div class="chat-hil-card__action-head">
        <span class="chat-hil-card__tool">{{ action.tool }}</span>
        <div class="chat-hil-card__choice">
          <el-button
            size="small"
            :type="approvals[index] ? 'primary' : 'default'"
            @click="approvals[index] = true"
          >
            批准
          </el-button>
          <el-button
            size="small"
            :type="approvals[index] ? 'default' : 'danger'"
            @click="approvals[index] = false"
          >
            拒绝
          </el-button>
        </div>
      </div>
      <details class="chat-hil-card__args">
        <summary>参数</summary>
        <pre class="chat-hil-card__args-body">{{ JSON.stringify(action.args, null, 2) }}</pre>
      </details>
    </div>
    <el-button
      class="chat-hil-card__submit"
      type="primary"
      :disabled="submitting"
      @click="submit"
    >
      提交决定
    </el-button>
  </div>
</template>

<style scoped>
.chat-hil-card {
  display: flex;
  flex-direction: column;
  gap: 10px;
  padding: 12px;
  border: 1px solid var(--color-border-default);
  border-left: 3px solid var(--color-primary, #3b82f6);
  border-radius: var(--radius-md, 8px);
  background: var(--color-bg-surface);
}

.chat-hil-card__title {
  font-size: 13px;
  font-weight: 600;
  color: var(--color-text-primary);
}

.chat-hil-card__action {
  display: flex;
  flex-direction: column;
  gap: 6px;
  padding: 8px 10px;
  border: 1px solid var(--color-border-default);
  border-radius: var(--radius-sm, 6px);
}

.chat-hil-card__action--rejected {
  opacity: 0.7;
}

.chat-hil-card__action-head {
  display: flex;
  align-items: center;
  justify-content: space-between;
  gap: 8px;
}

.chat-hil-card__tool {
  font-family: var(--app-font-display, monospace);
  font-weight: 600;
}

.chat-hil-card__choice {
  display: flex;
  gap: 4px;
}

.chat-hil-card__args summary {
  cursor: pointer;
  font-size: 12px;
  color: var(--color-text-secondary);
}

.chat-hil-card__args-body {
  margin: 6px 0 0;
  padding: 6px 8px;
  max-height: 200px;
  overflow: auto;
  background: var(--color-bg-secondary, #f2f4f7);
  border-radius: var(--radius-sm, 6px);
  font-size: 12px;
  white-space: pre-wrap;
  word-break: break-word;
}

.chat-hil-card__submit {
  align-self: flex-end;
}
</style>
