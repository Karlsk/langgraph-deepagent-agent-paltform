<script setup lang="ts">
/**
 * PythonNodeForm — python 节点配置（S18 / S22，前端 spec §5.1）。
 *
 * 只暴露 `code`：`entry` 可加载任意仓库模块、无法沙箱化，后端一律拒绝；`sandboxed`
 * 是安全属性而非用户偏好，由后端强制覆写为 true。两者都不进提交体。
 * 前端不做 eval / 预览执行 / 语法高亮——校验以后端 422 的行号 + 规则名为准。
 */
import { reactive, watch } from 'vue'

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

const formModel = reactive<Record<string, unknown>>({
  code: props.config.code ?? '',
})

watch(
  () => props.config,
  (newConfig) => {
    formModel.code = newConfig.code ?? ''
  },
  { deep: true },
)

function onFieldChange() {
  emit('update:config', { ...formModel })
}
</script>

<template>
  <div class="python-node-form">
    <el-form label-position="top" :disabled="props.readonly">
      <el-form-item label="代码" prop="code">
        <div class="python-node-form__code">
          <el-input
            v-model="formModel.code"
            type="textarea"
            :rows="12"
            spellcheck="false"
            placeholder='return {"result": state["input"]}'
            @change="onFieldChange"
          />
        </div>
        <div class="agent-form-helptext">代码内可用 state（工作流状态字典）；返回的键会双写进 state</div>
      </el-form-item>
    </el-form>

    <div class="python-node-form__limits">
      <div class="python-node-form__limits-title">沙箱限制</div>
      <ul>
        <li>必须 return 一个 dict，否则视为执行失败</li>
        <li>禁止 import：无第三方库，也无标准库</li>
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
