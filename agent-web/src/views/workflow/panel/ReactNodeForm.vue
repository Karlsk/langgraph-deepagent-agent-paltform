<script setup lang="ts">
/**
 * React Agent 节点配置表单。
 *
 * 配置键（ReactNodeConfig, extra="forbid"）：
 * - provider_ref: 模型引用（同 LLM 节点）
 * - tools: 细粒度工具名列表（server__tool 或 builtin 名）
 * - mcp_servers: 服务器级工具关联（选中 server 的所有工具）
 * - system_prompt: 可选系统提示词（支持 {varName} 模板引用输入变量）
 * - max_iterations: 安全迭代上限
 * - inputs: Dify 风格输入变量映射（varName → state dot-path）
 */
import { computed, onMounted, reactive, ref, watch } from 'vue'
import { Delete } from '@element-plus/icons-vue'

import { listMcpServers, listToolCatalog, type McpServerRow, type ToolCatalogEntry } from '@/api/mcp'
import { useProviderModels } from '@/composables/useProviderModels'

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

const { groups, byRef, loading, load } = useProviderModels()

interface InputRow {
  varName: string
  dotPath: string
}

const toolCatalog = ref<ToolCatalogEntry[]>([])
const mcpServerNames = ref<string[]>([])

const formModel = reactive({
  provider_ref: (props.config.provider_ref as string) ?? '',
  tools: (props.config.tools as string[]) ?? [],
  mcp_servers: (props.config.mcp_servers as string[]) ?? [],
  system_prompt: (props.config.system_prompt as string) ?? '',
  max_iterations: (props.config.max_iterations as number) ?? 10,
  inputs: toInputRows(props.config.inputs as Record<string, string> | undefined),
})

watch(
  () => props.config,
  (newConfig) => {
    formModel.provider_ref = (newConfig.provider_ref as string) ?? ''
    formModel.tools = (newConfig.tools as string[]) ?? []
    formModel.mcp_servers = (newConfig.mcp_servers as string[]) ?? []
    formModel.system_prompt = (newConfig.system_prompt as string) ?? ''
    formModel.max_iterations = (newConfig.max_iterations as number) ?? 10
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

function onFieldChange() {
  emit('update:config', {
    ...props.config,
    provider_ref: formModel.provider_ref,
    tools: formModel.tools,
    mcp_servers: formModel.mcp_servers,
    system_prompt: formModel.system_prompt,
    max_iterations: formModel.max_iterations,
    inputs: toInputsDict(formModel.inputs),
  })
}

const selectedModel = computed<string>({
  get() {
    return formModel.provider_ref
  },
  set(value: string) {
    const option = byRef.value.get(value)
    if (option) {
      formModel.provider_ref = option.ref
    } else {
      formModel.provider_ref = value ?? ''
    }
    onFieldChange()
  },
})

const builtinTools = computed(() => toolCatalog.value.filter((t) => t.source === 'builtin'))
const mcpToolsByServer = computed(() => {
  const grouped = new Map<string, ToolCatalogEntry[]>()
  for (const entry of toolCatalog.value) {
    if (entry.source === 'mcp' && entry.server) {
      const list = grouped.get(entry.server) ?? []
      list.push(entry)
      grouped.set(entry.server, list)
    }
  }
  return grouped
})

function addInputRow() {
  formModel.inputs.push({ varName: '', dotPath: '' })
}

function removeInputRow(index: number) {
  formModel.inputs.splice(index, 1)
  onFieldChange()
}

function systemPromptPlaceholder(): string {
  if (formModel.inputs.length > 0) {
    const vars = formModel.inputs.map((r) => r.varName).filter(Boolean)
    return vars.length > 0 ? `可选系统提示词，可用 {${vars.join('} {')}}} 引用输入变量` : '可选系统提示词'
  }
  return '可选系统提示词，可用 {变量名} 引用输入变量'
}

onMounted(async () => {
  void load()
  try {
    const [catalog, servers] = await Promise.all([listToolCatalog(), listMcpServers()])
    toolCatalog.value = catalog
    mcpServerNames.value = servers.map((s: McpServerRow) => s.name)
  } catch {
    // API 失败时下拉框仍可手动输入（allow-create）
  }
})
</script>

<template>
  <div class="react-node-form">
    <el-form label-position="top" :disabled="props.readonly">
      <el-form-item label="模型" prop="provider_ref">
        <el-select
          v-model="selectedModel"
          filterable
          clearable
          allow-create
          default-first-option
          :loading="loading"
          no-data-text="暂无可用模型"
          placeholder="选择 provider 下的模型，或输入模型名"
          style="width: 100%"
          @change="onFieldChange"
        >
          <el-option-group v-for="group in groups" :key="group.providerName" :label="group.providerName">
            <el-option v-for="option in group.options" :key="option.ref" :value="option.ref" :label="option.label" />
          </el-option-group>
        </el-select>
      </el-form-item>

      <el-form-item label="输入变量">
        <div class="react-node-form__inputs">
          <div
            v-for="(row, index) in formModel.inputs"
            :key="index"
            class="react-node-form__input-row"
          >
            <div class="react-node-form__input-header">
              <el-input
                v-model="row.varName"
                placeholder="变量名"
                class="react-node-form__input-var"
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
              placeholder="state dot-path（如 search_result.result）"
              @change="onFieldChange"
            />
          </div>
          <el-button type="primary" text @click="addInputRow">
            + 添加输入变量
          </el-button>
        </div>
        <div class="react-node-form__hint">
          定义输入变量后，System Prompt 中可用 <code>{变量名}</code> 引用；留空则无模板渲染
        </div>
      </el-form-item>

      <el-form-item label="工具列表" prop="tools">
        <el-select
          v-model="formModel.tools"
          multiple
          filterable
          allow-create
          default-first-option
          placeholder="选择或输入工具名（如 search, browser-use__click）"
          style="width: 100%"
          @change="onFieldChange"
        >
          <el-option-group v-if="builtinTools.length > 0" key="builtin" label="内置工具">
            <el-option
              v-for="tool in builtinTools"
              :key="tool.name"
              :value="tool.name"
              :label="tool.name"
            />
          </el-option-group>
          <el-option-group
            v-for="[server, tools] in mcpToolsByServer"
            :key="server"
            :label="`MCP: ${server}`"
          >
            <el-option
              v-for="tool in tools"
              :key="tool.name"
              :value="tool.name"
              :label="tool.name"
            />
          </el-option-group>
        </el-select>
        <div class="react-node-form__hint">细粒度工具白名单，支持 server__tool 格式。</div>
      </el-form-item>

      <el-form-item label="MCP 服务器" prop="mcp_servers">
        <el-select
          v-model="formModel.mcp_servers"
          multiple
          filterable
          allow-create
          default-first-option
          placeholder="选择或输入 MCP 服务器名"
          style="width: 100%"
          @change="onFieldChange"
        >
          <el-option
            v-for="name in mcpServerNames"
            :key="name"
            :value="name"
            :label="name"
          />
        </el-select>
        <div class="react-node-form__hint">选中服务器的所有工具将自动加入。</div>
      </el-form-item>

      <el-form-item label="最大迭代次数" prop="max_iterations">
        <el-input-number
          v-model="formModel.max_iterations"
          :min="1"
          :max="100"
          :step="1"
          @change="onFieldChange"
        />
      </el-form-item>

      <el-form-item label="System Prompt" prop="system_prompt">
        <el-input
          v-model="formModel.system_prompt"
          type="textarea"
          :rows="4"
          :placeholder="systemPromptPlaceholder()"
          @change="onFieldChange"
        />
      </el-form-item>
    </el-form>
  </div>
</template>

<style scoped>
.react-node-form {
  display: flex;
  flex-direction: column;
  gap: 16px;
}

.react-node-form__inputs {
  display: flex;
  flex-direction: column;
  gap: 8px;
  width: 100%;
}

.react-node-form__input-row {
  display: flex;
  flex-direction: column;
  gap: 4px;
  width: 100%;
}

.react-node-form__input-header {
  display: flex;
  gap: 4px;
  align-items: center;
}

.react-node-form__input-var {
  flex: 1;
}

.react-node-form__hint {
  font-size: 12px;
  line-height: 1.4;
  margin-top: 4px;
  color: var(--color-text-tertiary);
}

.react-node-form__hint code {
  font-family: var(--font-mono, ui-monospace, SFMono-Regular, Menlo, monospace);
  font-size: 11px;
  background: var(--color-bg-elevated);
  padding: 1px 4px;
  border-radius: 3px;
}
</style>
