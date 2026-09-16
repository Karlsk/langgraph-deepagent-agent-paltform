<script setup lang="ts">
/**
 * PythonNodeForm — python 节点配置（S18 / S22，前端 spec §5.1）。
 *
 * 两个区域：
 * - 输入变量（inputs）：每行一个 变量名 → state dot-path 映射；为空时走旧模式（state.get）
 * - 代码（code）：inputs 非空时代码须定义 def main(var1, var2, ...) -> dict
 *
 * `entry` 无法沙箱化、`sandboxed` 由后端强制 true，两者都不进提交体。
 */
import { reactive, watch } from 'vue'
import { Delete } from '@element-plus/icons-vue'

interface Props {
  config: Record<string, unknown>
  readonly?: boolean
}

const props = withDefaults(defineProps<Props>(), {
  readonly: false,
})

const emit = defineEmits<{
  'update:config': [config: Record<string, unknown>]
}>()

interface InputRow {
  varName: string
  dotPath: string
}

const formModel = reactive<{
  code: string
  inputs: InputRow[]
}>({
  code: (props.config.code as string) ?? '',
  inputs: toInputRows(props.config.inputs as Record<string, string> | undefined),
})

watch(
  () => props.config,
  (newConfig) => {
    formModel.code = (newConfig.code as string) ?? ''
    formModel.inputs = toInputRows(newConfig.inputs as Record<string, string> | undefined)
  },
  { deep: true },
)

function toInputRows(inputs: Record<string, string> | undefined): InputRow[] {
  if (!inputs || typeof inputs !== 'object') return []
  return Object.entries(inputs).map(([varName, dotPath]) => ({ varName, dotPath }))
}

function toInputsDict(rows: InputRow[]): Record<string, string> {
  const result: Record<string, string> = {}
  for (const row of rows) {
    const key = row.varName.trim()
    if (key) {
      result[key] = row.dotPath.trim()
    }
  }
  return result
}

function codePlaceholder(): string {
  if (formModel.inputs.length > 0) {
    return 'def main(token_response):\n    return {"access_token": token_response.get("access_token", "")}'
  }
  return 'return {"result": state["input"]}'
}

function addInputRow() {
  formModel.inputs.push({ varName: '', dotPath: '' })
}

function removeInputRow(index: number) {
  formModel.inputs.splice(index, 1)
  onFieldChange()
}

function onFieldChange() {
  emit('update:config', {
    code: formModel.code,
    inputs: toInputsDict(formModel.inputs),
  })
}
</script>

<template>
  <div class="python-node-form">
    <el-form label-position="top" :disabled="props.readonly">
      <el-form-item label="输入变量">
        <div class="python-node-form__inputs">
          <div
            v-for="(row, index) in formModel.inputs"
            :key="index"
            class="python-node-form__input-row"
          >
            <div class="python-node-form__input-header">
              <el-input
                v-model="row.varName"
                placeholder="变量名"
                class="python-node-form__input-var"
                @change="onFieldChange"
              />
              <el-button
                type="danger"
                :icon="Delete"
                text
                @click="removeInputRow(index)"
              />
            </div>
            <el-input
              v-model="row.dotPath"
              placeholder="state dot-path（如 get_token_result.response）"
              @change="onFieldChange"
            />
          </div>
          <el-button type="primary" text @click="addInputRow">
            + 添加输入变量
          </el-button>
        </div>
        <div class="agent-form-helptext">
          定义输入变量后，代码须写 <code>def main(变量1, 变量2, ...) -> dict</code>；留空则走旧模式（直接用 state 字典）
        </div>
      </el-form-item>

      <el-form-item label="代码" prop="code">
        <div class="python-node-form__code">
          <el-input
            v-model="formModel.code"
            type="textarea"
            :rows="12"
            spellcheck="false"
            :placeholder="codePlaceholder()"
            @change="onFieldChange"
          />
        </div>
        <div class="agent-form-helptext">
          {{ formModel.inputs.length > 0 ? '代码须定义 def main(...) 函数，参数名与上方输入变量一一对应，返回 dict' : '代码内可用 state（工作流状态字典）；返回的键会双写进 state' }}
        </div>
      </el-form-item>
    </el-form>

    <div class="python-node-form__limits">
      <div class="python-node-form__limits-title">沙箱限制</div>
      <ul>
        <li>必须 return 一个 dict，否则视为执行失败</li>
        <li>只能 import 白名单内的标准库（json / re / math / random / datetime / collections / functools 等）；os / sys / socket 等一律拒绝</li>
        <li>无文件与网络访问，无 open / exec / eval / getattr 等内建</li>
        <li>禁止 dunder 标识符（如 __class__），内省逃逸链被封死</li>
        <li>超时与内存均有上限，超限即被终止</li>
        <li>保存时静态校验，不合法由后端返回 422（含行号与规则名）</li>
      </ul>
    </div>
  </div>
</template>

<style scoped>
.python-node-form {
  display: flex;
  flex-direction: column;
  gap: 16px;
}

.python-node-form__inputs {
  display: flex;
  flex-direction: column;
  gap: 8px;
  width: 100%;
}

.python-node-form__input-row {
  display: flex;
  flex-direction: column;
  gap: 4px;
  width: 100%;
}

.python-node-form__input-header {
  display: flex;
  gap: 4px;
  align-items: center;
}

.python-node-form__input-var {
  flex: 1;
}

.python-node-form__code :deep(textarea) {
  font-family: var(--font-mono, ui-monospace, SFMono-Regular, Menlo, monospace);
  font-size: 13px;
  line-height: 1.6;
  tab-size: 4;
}

.agent-form-helptext {
  font-size: 12px;
  color: var(--color-text-tertiary);
  margin-top: 4px;
}

.agent-form-helptext code {
  font-family: var(--font-mono, ui-monospace, SFMono-Regular, Menlo, monospace);
  font-size: 11px;
  background: var(--color-bg-elevated);
  padding: 1px 4px;
  border-radius: 3px;
}

.python-node-form__limits {
  padding: 12px;
  border-radius: var(--radius-md);
  border: 1px solid var(--color-border-default);
  background: var(--color-bg-elevated);
}

.python-node-form__limits-title {
  font-size: 12px;
  font-weight: 600;
  color: var(--color-text-secondary);
  margin-bottom: 6px;
}

.python-node-form__limits ul {
  margin: 0;
  padding-left: 18px;
}

.python-node-form__limits li {
  font-size: 12px;
  line-height: 1.7;
  color: var(--color-text-tertiary);
}
</style>
