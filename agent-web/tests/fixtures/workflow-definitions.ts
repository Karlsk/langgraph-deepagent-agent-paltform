/**
 * 工作流定义 DTO fixture（spec-09）。
 * 等价于后端 condition_branch.yaml / http_demo.yaml 示例。
 */
import type { WorkflowDefinitionDTO } from '@/api/workflow'

export const conditionBranchDef: WorkflowDefinitionDTO = {
  workflow_id: 'condition_branch_demo',
  entry_point: 'check',
  nodes: [
    {
      name: 'check',
      type: 'llm',
      config: {
        llm_type: 'openai',
        model_name: 'gpt-4o-mini',
        temperature: 0.0,
        system_prompt: '判断输入是否需要人工复核。只输出 NEED_REVIEW 或 OK。',
      },
    },
    {
      name: 'notify',
      type: 'http',
      config: {
        url: 'https://example.com/api/notify',
        method: 'POST',
        body_template: '{"input": "{input}"}',
        response_path: 'result',
        mock_enabled: true,
        mock_responses: {
          'POST https://example.com/api/notify': '{"result": {"ticket": "DEMO-1"}}',
        },
      },
    },
    {
      name: 'summarize',
      type: 'llm',
      config: {
        llm_type: 'openai',
        model_name: 'gpt-4o-mini',
        system_prompt: 'Summarize the input in one sentence.',
      },
    },
  ],
  edges: [
    { source: 'check', target: 'notify', condition: "check_result.response == 'OK'" },
    { source: 'check', target: 'summarize', condition: "check_result.response == 'NEED_REVIEW'" },
    { source: 'notify', target: 'END' },
    { source: 'summarize', target: 'END' },
  ],
  state_schema: {
    input: { type: 'str', description: '用户输入' },
    messages: { type: 'list', description: 'langchain 消息列表' },
  },
  ui_layout: {
    nodes: {
      check: { x: 200, y: 0 },
      notify: { x: 0, y: 200 },
      summarize: { x: 400, y: 200 },
    },
  },
}

export const httpDemoDef: WorkflowDefinitionDTO = {
  workflow_id: 'demo_http',
  entry_point: 'fetch',
  nodes: [
    {
      name: 'fetch',
      type: 'http',
      config: {
        url: 'https://example.com/api/items',
        method: 'POST',
        body_template: '{"query": "{input}"}',
        response_path: 'data',
        mock_enabled: true,
        mock_responses: {
          'POST https://example.com/api/items': '{"data": {"items": ["alpha", "beta"]}}',
        },
      },
    },
  ],
  edges: [{ source: 'fetch', target: 'END' }],
  state_schema: {
    input: { type: 'str', description: '查询字符串' },
  },
}
