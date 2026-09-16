import type { WorkflowNodeType } from '@/api/workflow'

export interface PaletteItem {
  type: WorkflowNodeType
  label: string
  icon: string
}

export const PALETTE_ITEMS: PaletteItem[] = [
  { type: 'llm', label: 'LLM 节点', icon: '🤖' },
  { type: 'http', label: 'HTTP 节点', icon: '🌐' },
  { type: 'python', label: 'Python 节点', icon: '🐍' },
  { type: 'subworkflow', label: '子工作流节点', icon: '🧩' },
]

export const DEFAULT_CONFIGS: Record<string, Record<string, unknown>> = {
  llm: {
    llm_type: 'openai',
    model_name: 'gpt-4o-mini',
    provider_ref: null,
    system_prompt: '',
    temperature: 0.7,
    max_retries: 3,
    retry_base_delay: 1.0,
  },
  http: {
    url: '',
    method: 'POST',
    headers: {},
    body_template: '',
    response_path: null,
    mock_enabled: false,
    mock_responses: {},
    timeout: 30.0,
    max_retries: 0,
    retry_base_delay: 1.0,
  },
  // code + inputs：inputs 声明变量名 → state dot-path 映射，代码用 def main(...) 接收（Dify 风格）
  python: {
    code: '',
    inputs: {},
  },
  // 被引用工作流的存在性是运行期检查（S18），故 workflow_id 允许先留空、后保存
  subworkflow: {
    workflow_id: '',
    input_map: {},
    inherit_input: false,
  },
}

export function nextNodeName(type: WorkflowNodeType, existingNames: string[]): string {
  const prefix = type
  const existingNumbers = existingNames
    .filter(name => name.startsWith(`${prefix}_`))
    .map(name => parseInt(name.split('_')[1], 10))
    .filter(n => !isNaN(n))

  let counter = 1
  while (existingNumbers.includes(counter)) {
    counter++
  }
  return `${prefix}_${counter}`
}
