export interface PaletteItem {
  type: 'llm' | 'http'
  label: string
  icon: string
}

export const PALETTE_ITEMS: PaletteItem[] = [
  { type: 'llm', label: 'LLM 节点', icon: '🤖' },
  { type: 'http', label: 'HTTP 节点', icon: '🌐' },
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
    body_template: '',
    response_path: null,
    mock_enabled: false,
    mock_responses: {},
    timeout: 30.0,
    max_retries: 0,
    retry_base_delay: 1.0,
  },
}

export function nextNodeName(type: 'llm' | 'http', existingNames: string[]): string {
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
