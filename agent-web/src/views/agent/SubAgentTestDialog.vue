<script setup lang="ts">
/**
 * SubAgentTestDialog 子代理单轮测试运行独立弹窗（task-dde SubAgent 前端适配）：
 * - 父组件 SubAgentList 在用户点击「测试」按钮时设置 agentName 并打开本弹窗；
 * - 入参：单行 prompt（textarea）；
 * - 调用：POST /subagents/{name}/test（testSubAgent），⚠️ 消耗 LLM token；
 * - 结果：final_message（可滚动展示）/ turns / duration_seconds / model；
 *   结果区附「查看执行详情」入口（trace_id 非空时），上抛 `open-trace` 由父级
 *   打开追踪详情弹窗；
 * - 失败：由 request.ts 全局拦截器 ElMessage.error 提示；本组件不重复 toast；
 * - 受 RATE_LIMIT_SUBAGENT_TEST 限流（默认 5 次/分钟）。
 */
import { ref, watch } from 'vue'

import { testSubAgent, type SubAgentTestResult } from '@/api/subagents'
import { notifySuccess } from '@/utils/notify'

/* -------------------------------------------------------------------------- */
/*  Props / Emits                                                              */
/* -------------------------------------------------------------------------- */

const props = defineProps<{
  modelValue: boolean
  agentName: string
}>()

const emit = defineEmits<{
  'update:modelValue': [value: boolean]
  'open-trace': [traceId: number]
}>()

/* -------------------------------------------------------------------------- */
/*  状态                                                                       */
/* -------------------------------------------------------------------------- */

const promptText = ref<string>('')
const submitting = ref(false)
const result = ref<SubAgentTestResult | null>(null)

/** 弹窗打开时重置状态；agentName 变化时同步刷新（同一弹窗复用打开不同 agent） */
watch(
  () => props.modelValue,
  (visible) => {
    if (visible) {
      promptText.value = ''
      result.value = null
      submitting.value = false
    }
  },
)

/* -------------------------------------------------------------------------- */
/*  行为                                                                       */
/* -------------------------------------------------------------------------- */

function closeDialog(): void {
  emit('update:modelValue', false)
}

async function handleSubmit(): Promise<void> {
  const text = promptText.value.trim()
  if (text.length === 0) {
    return
  }
  submitting.value = true
  try {
    result.value = await testSubAgent(props.agentName, { prompt: text })
    notifySuccess(`已测试：${props.agentName}`)
  } finally {
    submitting.value = false
  }
}

function durationLabel(seconds: number): string {
  if (seconds < 1) return `${Math.round(seconds * 1000)}ms`
  return `${seconds.toFixed(2)}s`
}

/** trace 落盘成功时上抛详情入口；落盘失败（trace_id 为 null）不展示按钮 */
function handleOpenTrace(): void {
  if (result.value?.trace_id != null) {
    emit('open-trace', result.value.trace_id)
  }
}
</script>

<template>
  <el-dialog
    :model-value="modelValue"
    :title="`测试子代理 — ${agentName}`"
    width="640px"
    append-to-body
    :close-on-click-modal="false"
    @update:model-value="emit('update:modelValue', $event)"
  >
    <div class="subagent-test-dialog">
      <p class="subagent-test-dialog__notice">
        单轮测试会真实调用 LLM，消耗 token；不影响任何会话状态。
      </p>
      <el-form label-width="auto">
        <el-form-item label="prompt" required>
          <el-input
            v-model="promptText"
            type="textarea"
            :rows="6"
            placeholder="例如：用一句话介绍你自己"
          />
        </el-form-item>
      </el-form>

      <div v-if="result" class="subagent-test-dialog__result">
        <div class="subagent-test-dialog__metrics">
          <span>轮次：{{ result.turns }}</span>
          <span>耗时：{{ durationLabel(result.duration_seconds) }}</span>
          <span>模型：{{ result.model }}</span>
          <el-button
            v-if="result.trace_id != null"
            link
            type="primary"
            size="small"
            @click="handleOpenTrace"
          >
            查看执行详情
          </el-button>
        </div>
        <div class="subagent-test-dialog__field-label">final_message</div>
        <pre class="subagent-test-dialog__message">{{ result.final_message }}</pre>
      </div>
    </div>
    <template #footer>
      <el-button @click="closeDialog">关闭</el-button>
      <el-button
        class="app-btn app-btn--primary"
        :loading="submitting"
        :disabled="promptText.trim().length === 0"
        @click="handleSubmit"
      >
        执行测试
      </el-button>
    </template>
  </el-dialog>
</template>

<style scoped>
.subagent-test-dialog {
  display: flex;
  flex-direction: column;
  gap: 16px;
}

.subagent-test-dialog__notice {
  margin: 0;
  padding: 8px 12px;
  background: var(--color-bg-subtle);
  border-radius: var(--radius-sm);
  font-size: 12px;
  color: var(--color-text-secondary);
}

.subagent-test-dialog__result {
  display: flex;
  flex-direction: column;
  gap: 8px;
  padding: 12px;
  background: var(--color-bg-subtle);
  border-radius: var(--radius-md);
  border: 1px solid var(--color-border-default);
}

.subagent-test-dialog__metrics {
  display: flex;
  flex-wrap: wrap;
  gap: 16px;
  font-size: 13px;
  color: var(--color-text-secondary);
}

.subagent-test-dialog__field-label {
  font-size: 12px;
  font-weight: 600;
  color: var(--color-text-secondary);
  letter-spacing: 0.04em;
}

.subagent-test-dialog__message {
  margin: 0;
  padding: 12px;
  background: var(--color-bg-surface);
  border-radius: var(--radius-sm);
  border: 1px solid var(--color-border-default);
  font-family: var(--app-font-mono, monospace);
  font-size: 13px;
  color: var(--color-text-primary);
  max-height: 320px;
  overflow: auto;
  white-space: pre-wrap;
  word-break: break-word;
  line-height: 1.6;
}
</style>