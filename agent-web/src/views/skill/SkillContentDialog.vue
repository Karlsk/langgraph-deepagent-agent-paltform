<script setup lang="ts">
/**
 * 技能正文查看弹窗：仅展示当前技能的原始 SKILL.md 内容，
 * 不提供编辑入口（编辑 description 用主视图的创建/编辑弹窗）。
 *
 * 数据源：`getSkillContent(name)`（`/skills/{name}/content`），
 * 返回 `{name, content}`；统一请求层已自动解包信封。
 *
 * 打开时机由父组件控制：父组件先 `contentDialogName.value = row.name` 再
 * `contentDialogVisible.value = true`；弹窗 watch modelValue 变化异步拉取。
 * 关闭时清空 content，避免下次打开闪烁旧内容。
 */
import { ref, watch } from 'vue'

import { getSkillContent } from '@/api/assets'
import { useRequest } from '@/composables/useRequest'

const props = defineProps<{
  /** v-model 控制显隐 */
  modelValue: boolean
  /** 目标技能名（父组件保证 name 已就绪再开弹窗） */
  name: string
}>()

const emit = defineEmits<{
  'update:modelValue': [value: boolean]
}>()

const content = ref('')

/**
 * 内容拉取：useRequest 托管 loading/error；错误提示交给统一请求层全局拦截器。
 */
const { execute, loading } = useRequest((skillName: string) => getSkillContent(skillName))

async function fetchContent(name: string): Promise<void> {
  const result = await execute(name)
  if (result) {
    content.value = result.content
  }
}

/** 监听 modelValue 变化：开启则拉取，关闭则清空
 *  - immediate: true 让组件 mount 时（如 modelValue 已为 true）也执行一次回调，
 *    避免「点击查看正文 → 父组件同步设置 name+visible → 子组件 mount 时 props
 *    已是 true，但默认 lazy watch 不触发」导致的拉取缺失 */
watch(
  () => props.modelValue,
  (visible) => {
    if (visible) {
      void fetchContent(props.name)
    } else {
      content.value = ''
    }
  },
  { immediate: true },
)

function handleClose(): void {
  emit('update:modelValue', false)
}
</script>

<template>
  <el-dialog
    :model-value="props.modelValue"
    title="技能正文"
    width="720px"
    :close-on-click-modal="false"
    @update:model-value="emit('update:modelValue', $event)"
    @close="handleClose"
  >
    <div v-loading="loading" class="skill-content-wrapper">
      <pre v-if="content" class="skill-content">{{ content }}</pre>
      <el-empty v-else-if="!loading" description="暂无正文" />
    </div>
  </el-dialog>
</template>

<style scoped>
.skill-content-wrapper {
  min-height: 80px;
}
.skill-content {
  margin: 0;
  padding: 16px;
  background: var(--color-bg-subtle);
  border-radius: var(--radius-md);
  color: var(--color-text-primary);
  font-family: var(--app-font-display);
  font-size: 13px;
  line-height: 1.6;
  white-space: pre-wrap;
  word-break: break-word;
  max-height: 60vh;
  overflow-y: auto;
}
</style>