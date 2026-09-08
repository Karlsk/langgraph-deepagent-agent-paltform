<script setup lang="ts">
import { ref, watch, computed } from 'vue'
import {
  isValidS7Condition,
  isValidPath,
  generateEqualityCondition,
  generateTruthyCondition,
} from '@/utils/s7Condition'

interface Props {
  modelValue: boolean
  edge: { source: string; target: string; condition?: string | null }
  nodeNames: string[]
  stateChannels: string[]
  readonly?: boolean
}

const props = withDefaults(defineProps<Props>(), {
  readonly: false,
})

const emit = defineEmits<{
  'update:modelValue': [value: boolean]
  'confirm': [payload: { target: string; condition: string | null }]
}>()

type ConditionType = 'none' | 'equality' | 'truthy'

const conditionType = ref<ConditionType>('none')
const selectedPath = ref('')
const literal = ref('')
const targetRef = ref(props.edge.target)

watch(
  () => props.modelValue,
  (visible) => {
    if (!visible) return

    targetRef.value = props.edge.target
    const condition = props.edge.condition

    if (!condition) {
      conditionType.value = 'none'
      selectedPath.value = ''
      literal.value = ''
    } else if (condition.includes('==')) {
      conditionType.value = 'equality'
      const match = condition.match(/^([^\s=]+)\s*==\s*'([^']*)'$/)
      if (match) {
        selectedPath.value = match[1]
        literal.value = match[2]
      }
    } else {
      conditionType.value = 'truthy'
      selectedPath.value = condition
    }
  },
  { immediate: true },
)

const canConfirm = computed(() => {
  if (!targetRef.value) return false
  if (conditionType.value === 'none') return true
  if (!isValidPath(selectedPath.value)) return false
  if (conditionType.value === 'equality' && literal.value.includes("'")) return false
  return true
})

const pathError = computed(() => {
  if (conditionType.value === 'none') return ''
  if (!selectedPath.value) return ''
  if (!isValidPath(selectedPath.value)) return '路径格式非法（需为合法标识符点分隔）'
  return ''
})

const literalError = computed(() => {
  if (conditionType.value !== 'equality') return ''
  if (literal.value.includes("'")) return '字面量不能包含单引号'
  return ''
})

function handleConfirm() {
  if (!canConfirm.value) return

  let condition: string | null = null
  if (conditionType.value === 'equality') {
    condition = generateEqualityCondition(selectedPath.value, literal.value)
  } else if (conditionType.value === 'truthy') {
    condition = generateTruthyCondition(selectedPath.value)
  }

  if (condition !== null && !isValidS7Condition(condition)) {
    console.error('S7 safety violation: generated condition failed parser', condition)
    return
  }

  emit('confirm', { target: targetRef.value, condition })
  emit('update:modelValue', false)
}

function handleClose() {
  emit('update:modelValue', false)
}
</script>

<template>
  <el-dialog
    :model-value="props.modelValue"
    title="编辑条件边"
    width="560px"
    :close-on-click-modal="false"
    @update:model-value="emit('update:modelValue', $event)"
    @close="handleClose"
  >
    <el-alert
      type="info"
      :closable="false"
      style="margin-bottom: 16px"
    >
      条件边默认 no_match_policy='raise'——所有条件均不命中会运行期报错；如需兜底分支，请保证条件穷尽或由管理员在宿主配置（画布不提供）
    </el-alert>

    <el-form label-width="100px">
      <el-form-item label="目标节点">
        <el-select
          v-model="targetRef"
          :disabled="props.readonly"
          placeholder="选择目标节点"
        >
          <el-option
            v-for="name in props.nodeNames"
            :key="name"
            :label="name"
            :value="name"
          />
          <el-option label="END" value="END" />
        </el-select>
      </el-form-item>

      <el-form-item label="条件类型">
        <el-radio-group
          v-model="conditionType"
          :disabled="props.readonly"
        >
          <el-radio value="none">无条件</el-radio>
          <el-radio value="equality">等值</el-radio>
          <el-radio value="truthy">真值</el-radio>
        </el-radio-group>
      </el-form-item>

      <template v-if="conditionType === 'equality' || conditionType === 'truthy'">
        <el-form-item label="路径">
          <el-select
            v-model="selectedPath"
            filterable
            :disabled="props.readonly"
            placeholder="选择或输入路径"
          >
            <el-option
              v-for="channel in props.stateChannels"
              :key="channel"
              :label="channel"
              :value="channel"
            />
          </el-select>
          <div v-if="pathError" class="condition-edge-dialog__error">
            {{ pathError }}
          </div>
        </el-form-item>
      </template>

      <template v-if="conditionType === 'equality'">
        <el-form-item label="字面量">
          <el-input
            v-model="literal"
            :disabled="props.readonly"
            placeholder="输入匹配值"
          />
          <div v-if="literalError" class="condition-edge-dialog__error">
            {{ literalError }}
          </div>
        </el-form-item>
      </template>
    </el-form>

    <template #footer>
      <el-button @click="handleClose">取消</el-button>
      <el-button
        v-if="!props.readonly"
        type="primary"
        :disabled="!canConfirm"
        @click="handleConfirm"
      >
        确定
      </el-button>
    </template>
  </el-dialog>
</template>

<style scoped>
.condition-edge-dialog__error {
  color: var(--el-color-danger);
  font-size: 12px;
  margin-top: 4px;
}
</style>
