<script setup lang="ts">
/**
 * 会话聊天页（G4 spec-g4-chat §9.1/§9.3）：
 * 独立全页（非对话框）——消息流滚动 / HIL 审批 / 轨迹抽屉（F6）需要
 * 稳定空间。路由 `/chat/:sessionId`（name `chatSession`）。
 *
 * 编排职责：useChatStream 状态机 + ChatMessageList / ChatHilCard 渲染 +
 * 输入区（Enter 发送 / Shift+Enter 换行 / streaming 变停止）+ rebuild
 * 灾难重建入口（useConfirm 确认；422/409 错误由统一拦截器提示）。
 *
 * 生命周期：onMounted 拉历史（pending 审批卡随之恢复，§5.3）；
 * onUnmounted 显式 stop()（composable 不自注册卸载钩子）。
 */
import { computed, onMounted, onUnmounted, ref } from 'vue'
import { useRoute, useRouter } from 'vue-router'

import { rebuildSession } from '@/api/chat'
import { useConfirm } from '@/composables/useConfirm'
import { useChatStream } from '@/composables/useChatStream'
import ChatHilCard from '@/views/chat/ChatHilCard.vue'
import ChatMessageList from '@/views/chat/ChatMessageList.vue'

const route = useRoute()
const router = useRouter()

/** 路由参数即会话寻址（透传 X-Session-Id 的消费方） */
const sessionId = computed(() => String(route.params.sessionId ?? ''))

const {
  items,
  streaming,
  pendingInterrupt,
  errorMessage,
  send,
  submitDecisions,
  stop,
  loadHistory,
} = useChatStream(sessionId)

// ---------------------------------------------------------------------------
// 输入区
// ---------------------------------------------------------------------------

const inputText = ref('')

/** pending 审批模式：输入框保持可用，仅提示语义（发送文本 = 全拒绝，§5.2） */
const inputPlaceholder = computed(() =>
  pendingInterrupt.value !== null
    ? '审批待处理：发送文本将拒绝所有待审批操作'
    : '输入消息，Enter 发送 / Shift+Enter 换行',
)

async function handleSend(): Promise<void> {
  const content = inputText.value.trim()
  if (!content || streaming.value) return
  inputText.value = ''
  await send(content)
}

function handleStop(): void {
  stop()
}

function handleHilSubmit(approvals: boolean[]): void {
  void submitDecisions(approvals)
}

// ---------------------------------------------------------------------------
// 灾难重建（§6）
// ---------------------------------------------------------------------------

async function handleRebuild(): Promise<void> {
  const confirmRebuild = useConfirm(
    '确定从 L2 历史重建该会话的运行时状态吗？将清空现有 checkpoint 并按历史消息重灌。',
    async () => {
      await rebuildSession(sessionId.value)
    },
    { title: '重建确认', successMessage: '已重建会话状态' },
  )
  const done = await confirmRebuild()
  if (done) await loadHistory()
}

onMounted(() => {
  void loadHistory()
})

onUnmounted(() => {
  stop()
})
</script>

<template>
  <div class="page-view chat-session">
    <header class="page-view__header">
      <div>
        <h1 class="page-view__title">会话聊天</h1>
        <p class="page-view__desc">
          会话 {{ sessionId }}：流式对话、工具调用与人工审批。
        </p>
      </div>
      <div class="page-view__actions">
        <el-button @click="router.push({ name: 'chat' })">返回列表</el-button>
        <el-button @click="handleRebuild">重建会话</el-button>
      </div>
    </header>

    <section class="content-card page-view__body chat-session__body">
      <div class="chat-session__stream">
        <ChatMessageList :items="items" :streaming="streaming" />
        <ChatHilCard
          v-if="pendingInterrupt !== null"
          :interrupt="pendingInterrupt"
          :submitting="streaming"
          @submit="handleHilSubmit"
        />
        <p v-if="errorMessage !== null" class="chat-session__error" role="alert">
          {{ errorMessage }}
        </p>
      </div>

      <div class="chat-session__composer">
        <textarea
          v-model="inputText"
          class="chat-session__input"
          :placeholder="inputPlaceholder"
          rows="3"
          @keydown.enter.exact.prevent="handleSend"
        />
        <el-button
          v-if="!streaming"
          class="chat-session__send"
          type="primary"
          :disabled="!inputText.trim()"
          @click="handleSend"
        >
          发送
        </el-button>
        <el-button v-else class="chat-session__send" type="danger" @click="handleStop">
          停止
        </el-button>
      </div>
    </section>
  </div>
</template>

<style scoped>
.chat-session__body {
  display: flex;
  flex-direction: column;
  gap: 12px;
  min-height: 0;
}

.chat-session__stream {
  flex: 1;
  min-height: 260px;
  max-height: 62vh;
  overflow: auto;
  display: flex;
  flex-direction: column;
  gap: 12px;
}

.chat-session__error {
  margin: 0;
  padding: 6px 12px;
  border-radius: var(--radius-sm, 6px);
  border: 1px solid var(--color-danger, #ef4444);
  color: var(--color-danger, #ef4444);
  font-size: 12px;
}

.chat-session__composer {
  display: flex;
  align-items: flex-end;
  gap: 8px;
}

.chat-session__input {
  flex: 1;
  resize: vertical;
  padding: 8px 10px;
  border: 1px solid var(--color-border-default);
  border-radius: var(--radius-sm, 6px);
  font: inherit;
  color: inherit;
  background: var(--color-bg-surface);
}

.chat-session__input:focus-visible {
  outline: 2px solid var(--color-primary, #3b82f6);
  outline-offset: 1px;
}
</style>
