<script setup lang="ts">
/**
 * 技能 LLM 草稿生成弹窗：用户在创建场景下没有现成正文时，可输入描述与可选
 * hint 调 `/skills/generate` 拉一份 SKILL.md 草稿；草稿会通过 `generated`
 * 事件 emit 给父组件，父组件再把它写回创建弹窗的 body 字段供用户继续微调。
 *
 * 数据源：`generateSkill({description, hint})`，返回 `{draft}`；
 * 统一请求层已自动解包信封；错误提示由 request.ts 全局拦截器承担。
 *
 * 打开时机由父组件控制：父组件设置 generateDialogVisible = true，
 * 子组件 watch 触发并用 props.description 回填 description 输入框；
 * 关闭时重置 description / hint / submitting。
 */
import { reactive, ref, watch } from 'vue'
import type { FormInstance, FormRules } from 'element-plus'

import { generateSkill } from '@/api/assets'

const props = defineProps<{
  /** v-model 控制显隐 */
  modelValue: boolean
  /**
   * 父组件当前表单的 description（创建场景下可能已填过）。
   * 作为 description 输入框的初始回填默认值，减少用户重复输入。
   */
  description?: string
}>()

const emit = defineEmits<{
  'update:modelValue': [value: boolean]
  /** 草稿生成成功后，把 draft 抛给父组件写入 body */
  generated: [draft: string]
}>()

interface GenerateFormShape {
  description: string
  hint: string
}

const formRef = ref<FormInstance>()
const submitting = ref(false)

const formModel = reactive<GenerateFormShape>({
  description: '',
  hint: '',
})

const rules: FormRules = {
  description: [
    { required: true, message: '请输入技能描述，作为 LLM 生成草稿的依据', trigger: 'blur' },
    { min: 5, message: '描述过于简短，至少 5 个字符', trigger: 'blur' },
  ],
  hint: [],
}

async function fetchDraft(): Promise<void> {
  if (!formRef.value) return
  try {
    await formRef.value.validate()
  } catch {
    return
  }
  submitting.value = true
  try {
    const result = await generateSkill({
      description: formModel.description.trim(),
      hint: formModel.hint.trim(),
    })
    emit('generated', result.draft)
    emit('update:modelValue', false)
  } finally {
    submitting.value = false
  }
}

function handleClose(): void {
  // 关闭时清空 hint（description 由下次 open 时 props 回填），避免下次打开残留
  formModel.hint = ''
  submitting.value = false
  formRef.value?.clearValidate()
}

/**
 * watch modelValue → 打开时按 props.description 回填 description 输入框；
 * immediate:true 保证首次 mount 时如果父组件已把 modelValue 置 true 也能回填。
 */
watch(
  () => props.modelValue,
  (visible) => {
    if (visible) {
      formModel.description = (props.description ?? '').trim()
      formModel.hint = ''
      submitting.value = false
    }
  },
  { immediate: true },
)
</script>

<template>
  <el-dialog
    :model-value="props.modelValue"
    title="自动生成技能正文"
    width="560px"
    :close-on-click-modal="false"
    @update:model-value="emit('update:modelValue', $event)"
    @close="handleClose"
  >
    <el-form ref="formRef" :model="formModel" :rules="rules" label-width="auto">
      <el-form-item label="技能描述" prop="description">
        <el-input
          v-model="formModel.description"
          type="textarea"
          :rows="3"
          placeholder="例如：解析 PDF 文档并提取关键文本与元数据"
        />
      </el-form-item>
      <el-form-item label="补充提示" prop="hint">
        <el-input
          v-model="formModel.hint"
          type="textarea"
          :rows="2"
          placeholder="可选：补充额外要求（输出语言、章节结构等）"
        />
      </el-form-item>
      <p class="skill-generate-tip">
        生成结果为草稿，不会自动保存；生成后会在「新建技能」弹窗的正文框里展示，可继续手动修改。
      </p>
    </el-form>
    <template #footer>
      <el-button class="app-btn app-btn--secondary" @click="emit('update:modelValue', false)">
        取消
      </el-button>
      <el-button
        class="app-btn app-btn--primary"
        :loading="submitting"
        @click="fetchDraft"
      >
        生成草稿
      </el-button>
    </template>
  </el-dialog>
</template>

<style scoped>
.skill-generate-tip {
  margin: 0;
  font-size: 12px;
  color: var(--color-text-secondary);
  line-height: 1.6;
}
</style>