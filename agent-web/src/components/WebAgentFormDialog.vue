<script lang="ts">
/** 表单弹窗模式：open(data) 为编辑，open() 为新增 */
export type FormDialogMode = 'create' | 'edit'
</script>

<script setup lang="ts">
import { nextTick, reactive, ref } from 'vue'
import type { FormInstance, FormRules } from 'element-plus'

const props = withDefaults(
  defineProps<{
    modelValue: boolean
    title: string
    width?: string
    rules?: FormRules
  }>(),
  { width: '560px', rules: undefined },
)

const emit = defineEmits<{
  'update:modelValue': [value: boolean]
  submit: [data: Record<string, unknown>]
}>()

const formRef = ref<FormInstance>()
const formModel = reactive<Record<string, unknown>>({})
const submitting = ref(false)
const mode = ref<FormDialogMode>('create')
let snapshot: Record<string, unknown> = {}

/** 深拷贝（表单数据均为可序列化纯数据），避免外部引用被意外修改 */
function deepCopy(source: Record<string, unknown>): Record<string, unknown> {
  return JSON.parse(JSON.stringify(source)) as Record<string, unknown>
}

/** 保持 reactive 引用稳定：先清空旧字段再写入新值 */
function assignModel(source: Record<string, unknown>): void {
  for (const key of Object.keys(formModel)) {
    delete formModel[key]
  }
  Object.assign(formModel, source)
}

/** 传 data 为编辑模式，不传为新增模式 */
function open(data?: Record<string, unknown>): void {
  mode.value = data === undefined ? 'create' : 'edit'
  snapshot = data === undefined ? {} : deepCopy(data)
  assignModel(deepCopy(snapshot))
  submitting.value = false
  emit('update:modelValue', true)
  void nextTick(() => formRef.value?.clearValidate())
}

function close(): void {
  emit('update:modelValue', false)
}

/** 关闭时自动重置：恢复 open() 时的初始快照（新增模式为空） */
function handleClose(): void {
  assignModel(deepCopy(snapshot))
  submitting.value = false
  formRef.value?.clearValidate()
}

async function handleConfirm(): Promise<void> {
  if (!formRef.value) {
    return
  }
  try {
    await formRef.value.validate()
  } catch {
    // 校验失败不提交
    return
  }
  emit('submit', { ...formModel })
}

function setSubmitting(value: boolean): void {
  submitting.value = value
}

defineExpose({ open, close, setSubmitting })
</script>

<template>
  <el-dialog
    :model-value="props.modelValue"
    :title="props.title"
    :width="props.width"
    @update:model-value="emit('update:modelValue', $event)"
    @close="handleClose"
  >
    <el-form ref="formRef" :model="formModel" :rules="props.rules" label-width="auto">
      <slot :form="formModel" :mode="mode" />
    </el-form>
    <template #footer>
      <el-button class="app-btn app-btn--secondary" @click="close">取消</el-button>
      <el-button
        class="app-btn app-btn--primary"
        :loading="submitting"
        @click="handleConfirm"
      >
        确定
      </el-button>
    </template>
  </el-dialog>
</template>
