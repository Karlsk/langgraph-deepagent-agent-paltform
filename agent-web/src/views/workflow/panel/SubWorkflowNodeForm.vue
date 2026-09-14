<script setup lang="ts">
/**
 * SubWorkflowNodeForm — subworkflow 节点配置（S23 / S24，前端 spec §5.1）。
 *
 * 下拉列出已注册工作流并排除当前正在编辑的 id：自引用是最常见的误操作，这里挡掉是
 * 体验层；权威判定在后端 S23 的运行栈环检测（跨工作流的间接环前端根本看不见）。
 *
 * 前端**不**校验被引用工作流是否存在——存在性是运行期检查（S18），因此可以先存外层、
 * 后存内层。目录拉取失败时已保存的值照样保留，绝不因目录不可用而清空 config。
 *
 * 提交体键恰为 {workflow_id, input_map, inherit_input}：后端 SubWorkflowNodeConfig 是
 * extra="forbid"（S14），夹带旧键会被 422 拒掉。
 */
import { computed, onMounted, reactive, ref, watch } from 'vue'

import { listWorkflows } from '@/api/workflow'
import type { WorkflowSummary } from '@/api/workflow'

interface Props {
  config: Record<string, unknown>
  readonly?: boolean
  /** 当前正在编辑的工作流 id，用于在下拉里排除自引用。 */
  excludeWorkflowId?: string
}

const props = withDefaults(defineProps<Props>(), {
  readonly: false,
  excludeWorkflowId: '',
})

const emit = defineEmits<{
  'update:config': [config: Record<string, unknown>]
}>()

function isPlainObject(value: unknown): value is Record<string, unknown> {
  return typeof value === 'object' && value !== null && !Array.isArray(value)
}

interface SubWorkflowFormModel {
  workflow_id: string
  input_map: Record<string, unknown>
  inherit_input: boolean
}

function readModel(config: Record<string, unknown>): SubWorkflowFormModel {
  return {
    workflow_id: typeof config.workflow_id === 'string' ? config.workflow_id : '',
    input_map: isPlainObject(config.input_map) ? { ...config.input_map } : {},
    inherit_input: config.inherit_input === true,
  }
}

const formModel = reactive<SubWorkflowFormModel>(readModel(props.config))

watch(
  () => props.config,
  (newConfig) => {
    Object.assign(formModel, readModel(newConfig))
  },
  { deep: true },
)

function onFieldChange() {
  emit('update:config', { ...formModel })
}

function onWorkflowIdChange(value: unknown) {
  formModel.workflow_id = typeof value === 'string' ? value : ''
  onFieldChange()
}

function onInheritInputChange(value: unknown) {
  formModel.inherit_input = value === true
  onFieldChange()
}

const summaries = ref<WorkflowSummary[]>([])
const loading = ref(false)
const loadFailed = ref(false)

const workflowOptions = computed(() =>
  summaries.value.filter((s) => s.workflow_id !== props.excludeWorkflowId),
)

const noDataText = computed(() => {
  if (loading.value) return '加载中…'
  if (loadFailed.value) return '工作流目录加载失败，暂无可选项'
  return '暂无已注册的工作流'
})

const inputMapText = computed(() => {
  try {
    return JSON.stringify(formModel.input_map, null, 2)
  } catch {
    return '{}'
  }
})

function onInputMapChange(text: string) {
  try {
    const parsed: unknown = JSON.parse(text)
    if (!isPlainObject(parsed)) return
    formModel.input_map = parsed
    onFieldChange()
  } catch {
    // 非法 JSON：不 emit，避免把坏值静默写进 config
  }
}

async function load(): Promise<void> {
  loading.value = true
  try {
    summaries.value = await listWorkflows()
    loadFailed.value = false
  } catch {
    summaries.value = []
    loadFailed.value = true
  } finally {
    loading.value = false
  }
}

onMounted(() => {
  void load()
})
</script>

<template>
  <div class="subworkflow-node-form">
    <el-form label-position="top" :disabled="props.readonly">
      <el-form-item label="被引用工作流" prop="workflow_id">
        <el-select
          :model-value="formModel.workflow_id"
          filterable
          clearable
          placeholder="选择要嵌套执行的工作流"
          :loading="loading"
          :no-data-text="noDataText"
          @update:model-value="onWorkflowIdChange"
        >
          <el-option
            v-for="opt in workflowOptions"
            :key="opt.workflow_id"
            :value="opt.workflow_id"
            :label="opt.description ? `${opt.workflow_id} — ${opt.description}` : opt.workflow_id"
          />
        </el-select>
        <div class="agent-form-helptext">
          是否存在由运行期校验；环与嵌套深度由后端守护（S23）
        </div>
      </el-form-item>

      <el-form-item label="继承外层输入" prop="inherit_input">
        <el-switch
          :model-value="formModel.inherit_input"
          @update:model-value="onInheritInputChange"
        />
        <div class="agent-form-helptext">开启后把外层 state 整体作为内层入参，再由映射覆盖</div>
      </el-form-item>

      <el-form-item label="输入映射" prop="input_map">
        <el-input
          :model-value="inputMapText"
          type="textarea"
          :rows="5"
          placeholder='{"query": "input"}'
          @change="onInputMapChange"
        />
        <div class="agent-form-helptext">
          JSON 对象，键为内层入参名，值为外层 state 的点路径（如 "result.data"）
        </div>
      </el-form-item>
    </el-form>

    <div class="subworkflow-node-form__limits">
      <div class="subworkflow-node-form__limits-title">嵌套语义</div>
      <ul>
        <li>内层结果只写入 {节点名}_result，不双写，避免覆盖外层同名字段</li>
        <li>内层日志按「调用节点名/内层节点名」前缀并入外层轨迹（S24）</li>
        <li>环（含跨工作流的间接环）与超过最大嵌套深度会在运行期报错</li>
        <li>内层的 LLM / HTTP 调用会真实发生，注意配额与耗时</li>
      </ul>
    </div>
  </div>
</template>

<style scoped>
.subworkflow-node-form {
  display: flex;
  flex-direction: column;
  gap: 16px;
}

.subworkflow-node-form :deep(.el-select) {
  width: 100%;
}

.agent-form-helptext {
  font-size: 12px;
  color: var(--color-text-tertiary);
  margin-top: 4px;
}

.subworkflow-node-form__limits {
  padding: 12px;
  border-radius: var(--radius-md);
  border: 1px solid var(--color-border-default);
  background: var(--color-bg-elevated);
}

.subworkflow-node-form__limits-title {
  font-size: 12px;
  font-weight: 600;
  color: var(--color-text-secondary);
  margin-bottom: 6px;
}

.subworkflow-node-form__limits ul {
  margin: 0;
  padding-left: 18px;
}

.subworkflow-node-form__limits li {
  font-size: 12px;
  line-height: 1.7;
  color: var(--color-text-tertiary);
}
</style>
