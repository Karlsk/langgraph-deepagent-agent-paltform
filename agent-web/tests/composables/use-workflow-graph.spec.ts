/**
 * useWorkflowGraph 纯函数测试（spec-09）。
 * 覆盖：definitionToGraph / graphToDefinition / validateGraph 三个纯函数。
 * 无需 DOM（纯函数，不依赖 Vue 响应式）。
 */
import { describe, expect, it } from 'vitest'
import type { WorkflowDefinitionDTO } from '@/api/workflow'
import {
  definitionToGraph,
  graphToDefinition,
  validateGraph,
} from '@/composables/useWorkflowGraph'
import { conditionBranchDef, httpDemoDef } from '../fixtures/workflow-definitions'

describe('definitionToGraph', () => {
  it('maps each NodeDTO to a Vue Flow Node with id=name, type=workflow, data carrying business fields', () => {
    const { nodes } = definitionToGraph(conditionBranchDef)
    const realNodes = nodes.filter((n) => n.id !== 'END')
    expect(realNodes).toHaveLength(3)
    for (const dto of conditionBranchDef.nodes) {
      const node = realNodes.find((n) => n.id === dto.name)
      expect(node).toBeDefined()
      expect(node!.type).toBe('workflow')
      expect(node!.data).toEqual(expect.objectContaining({ name: dto.name, type: dto.type, config: dto.config }))
    }
  })

  it('maps each EdgeDTO to a Vue Flow Edge with data.condition preserved', () => {
    const { edges } = definitionToGraph(conditionBranchDef)
    expect(edges).toHaveLength(4)
    const condEdge = edges.find((e) => e.source === 'check' && e.target === 'notify')
    expect(condEdge).toBeDefined()
    expect(condEdge!.data?.condition).toBe("check_result.response == 'OK'")
  })

  it('maps target "END" to an edge pointing to a virtual END node', () => {
    const { nodes, edges } = definitionToGraph(conditionBranchDef)
    const endNode = nodes.find((n) => n.id === 'END')
    expect(endNode).toBeDefined()
    const endEdges = edges.filter((e) => e.target === 'END')
    expect(endEdges).toHaveLength(2)
  })

  it('restores position from ui_layout.nodes when present', () => {
    const { nodes } = definitionToGraph(conditionBranchDef)
    const checkNode = nodes.find((n) => n.id === 'check')
    expect(checkNode!.position).toEqual({ x: 200, y: 0 })
    const notifyNode = nodes.find((n) => n.id === 'notify')
    expect(notifyNode!.position).toEqual({ x: 0, y: 200 })
  })

  it('assigns default position when ui_layout is missing (does not crash)', () => {
    const { nodes } = definitionToGraph(httpDemoDef)
    const fetchNode = nodes.find((n) => n.id === 'fetch')
    expect(fetchNode).toBeDefined()
    expect(fetchNode!.position).toEqual(expect.objectContaining({ x: expect.any(Number), y: expect.any(Number) }))
  })

  it('generates edge ids as source-target', () => {
    const { edges } = definitionToGraph(conditionBranchDef)
    const ids = edges.map((e) => e.id)
    expect(ids).toContain('check-notify')
    expect(ids).toContain('check-summarize')
    expect(ids).toContain('notify-END')
  })
})

describe('graphToDefinition', () => {
  it('reconstructs NodeDTO list from Vue Flow nodes (excluding virtual END)', () => {
    const { nodes, edges } = definitionToGraph(conditionBranchDef)
    const meta = {
      workflow_id: conditionBranchDef.workflow_id,
      entry_point: conditionBranchDef.entry_point,
      state_schema: conditionBranchDef.state_schema,
    }
    const result = graphToDefinition(meta, nodes, edges)
    expect(result.nodes).toHaveLength(3)
    expect(result.nodes.map((n) => n.name)).toEqual(expect.arrayContaining(['check', 'notify', 'summarize']))
    for (const node of result.nodes) {
      const original = conditionBranchDef.nodes.find((n) => n.name === node.name)!
      expect(node.type).toBe(original.type)
      expect(node.config).toEqual(original.config)
    }
  })

  it('reconstructs EdgeDTO list with condition preserved (END target stays as "END")', () => {
    const { nodes, edges } = definitionToGraph(conditionBranchDef)
    const meta = {
      workflow_id: conditionBranchDef.workflow_id,
      entry_point: conditionBranchDef.entry_point,
      state_schema: conditionBranchDef.state_schema,
    }
    const result = graphToDefinition(meta, nodes, edges)
    expect(result.edges).toHaveLength(4)
    const condEdge = result.edges.find((e) => e.source === 'check' && e.target === 'notify')
    expect(condEdge!.condition).toBe("check_result.response == 'OK'")
    const endEdge = result.edges.find((e) => e.source === 'notify' && e.target === 'END')
    expect(endEdge).toBeDefined()
  })

  it('writes back ui_layout.nodes from node positions', () => {
    const { nodes, edges } = definitionToGraph(conditionBranchDef)
    const meta = {
      workflow_id: conditionBranchDef.workflow_id,
      entry_point: conditionBranchDef.entry_point,
      state_schema: conditionBranchDef.state_schema,
    }
    const result = graphToDefinition(meta, nodes, edges)
    expect(result.ui_layout).toBeDefined()
    expect(result.ui_layout!.nodes!.check).toEqual({ x: 200, y: 0 })
    expect(result.ui_layout!.nodes!.notify).toEqual({ x: 0, y: 200 })
    expect(result.ui_layout!.nodes!.summarize).toEqual({ x: 400, y: 200 })
    expect(result.ui_layout!.nodes!['END']).toBeUndefined()
  })

  it('preserves meta fields (workflow_id, entry_point, state_schema)', () => {
    const { nodes, edges } = definitionToGraph(conditionBranchDef)
    const meta = {
      workflow_id: 'custom_id',
      entry_point: 'check',
      state_schema: conditionBranchDef.state_schema,
    }
    const result = graphToDefinition(meta, nodes, edges)
    expect(result.workflow_id).toBe('custom_id')
    expect(result.entry_point).toBe('check')
    expect(result.state_schema).toEqual(conditionBranchDef.state_schema)
  })
})

describe('round-trip: definitionToGraph → graphToDefinition', () => {
  it('conditionBranchDef round-trip preserves all fields', () => {
    const graph = definitionToGraph(conditionBranchDef)
    const meta = {
      workflow_id: conditionBranchDef.workflow_id,
      entry_point: conditionBranchDef.entry_point,
      state_schema: conditionBranchDef.state_schema,
    }
    const restored = graphToDefinition(meta, graph.nodes, graph.edges)
    expect(restored.workflow_id).toBe(conditionBranchDef.workflow_id)
    expect(restored.entry_point).toBe(conditionBranchDef.entry_point)
    expect(restored.nodes).toEqual(conditionBranchDef.nodes)
    expect(restored.edges).toEqual(conditionBranchDef.edges)
    expect(restored.state_schema).toEqual(conditionBranchDef.state_schema)
    expect(restored.ui_layout!.nodes).toEqual(conditionBranchDef.ui_layout!.nodes)
  })

  it('httpDemoDef round-trip preserves all fields', () => {
    const graph = definitionToGraph(httpDemoDef)
    const meta = {
      workflow_id: httpDemoDef.workflow_id,
      entry_point: httpDemoDef.entry_point,
      state_schema: httpDemoDef.state_schema,
    }
    const restored = graphToDefinition(meta, graph.nodes, graph.edges)
    expect(restored.nodes).toEqual(httpDemoDef.nodes)
    expect(restored.edges).toEqual(httpDemoDef.edges)
  })
})

describe('validateGraph', () => {
  it('returns no errors for a valid definition', () => {
    expect(validateGraph(conditionBranchDef)).toEqual([])
  })

  it('returns no errors for httpDemoDef', () => {
    expect(validateGraph(httpDemoDef)).toEqual([])
  })

  it('reports error when nodes array is empty', () => {
    const def: WorkflowDefinitionDTO = {
      workflow_id: 'empty',
      entry_point: 'a',
      nodes: [],
      edges: [],
      state_schema: {},
    }
    const errors = validateGraph(def)
    expect(errors.length).toBeGreaterThan(0)
    expect(errors.some((e) => e.field === 'nodes')).toBe(true)
  })

  it('reports error when node names are duplicated', () => {
    const def: WorkflowDefinitionDTO = {
      workflow_id: 'dup',
      entry_point: 'a',
      nodes: [
        { name: 'a', type: 'llm', config: {} },
        { name: 'a', type: 'http', config: {} },
      ],
      edges: [],
      state_schema: {},
    }
    const errors = validateGraph(def)
    expect(errors.some((e) => e.field === 'nodes' && e.nodeId === 'a')).toBe(true)
  })

  it('reports error when node name is empty', () => {
    const def: WorkflowDefinitionDTO = {
      workflow_id: 'blank',
      entry_point: 'a',
      nodes: [{ name: '', type: 'llm', config: {} }],
      edges: [],
      state_schema: {},
    }
    const errors = validateGraph(def)
    expect(errors.some((e) => e.field === 'nodes')).toBe(true)
  })

  it('reports error when entry_point does not match any node', () => {
    const def: WorkflowDefinitionDTO = {
      workflow_id: 'bad_entry',
      entry_point: 'nonexistent',
      nodes: [{ name: 'a', type: 'llm', config: {} }],
      edges: [],
      state_schema: {},
    }
    const errors = validateGraph(def)
    expect(errors.some((e) => e.field === 'entry_point')).toBe(true)
  })

  it('reports error when edge source is not a valid node', () => {
    const def: WorkflowDefinitionDTO = {
      workflow_id: 'dangling_src',
      entry_point: 'a',
      nodes: [{ name: 'a', type: 'llm', config: {} }],
      edges: [{ source: 'ghost', target: 'a' }],
      state_schema: {},
    }
    const errors = validateGraph(def)
    expect(errors.some((e) => e.field === 'edges' && e.edgeId === 'ghost-a')).toBe(true)
  })

  it('reports error when edge target is not a valid node and not "END"', () => {
    const def: WorkflowDefinitionDTO = {
      workflow_id: 'dangling_tgt',
      entry_point: 'a',
      nodes: [{ name: 'a', type: 'llm', config: {} }],
      edges: [{ source: 'a', target: 'ghost' }],
      state_schema: {},
    }
    const errors = validateGraph(def)
    expect(errors.some((e) => e.field === 'edges' && e.edgeId === 'a-ghost')).toBe(true)
  })

  it('accepts edge target "END" without error', () => {
    const def: WorkflowDefinitionDTO = {
      workflow_id: 'end_ok',
      entry_point: 'a',
      nodes: [{ name: 'a', type: 'llm', config: {} }],
      edges: [{ source: 'a', target: 'END' }],
      state_schema: {},
    }
    expect(validateGraph(def)).toEqual([])
  })

  it('reports error when node type is "python" (not in whitelist)', () => {
    const def: WorkflowDefinitionDTO = {
      workflow_id: 'bad_type',
      entry_point: 'a',
      nodes: [{ name: 'a', type: 'python' as any, config: {} }],
      edges: [],
      state_schema: {},
    }
    const errors = validateGraph(def)
    expect(errors.some((e) => e.field === 'nodes' && e.nodeId === 'a')).toBe(true)
  })

  it('reports error for illegal condition: "a or b" (not S7 grammar)', () => {
    const def: WorkflowDefinitionDTO = {
      workflow_id: 'bad_cond',
      entry_point: 'a',
      nodes: [
        { name: 'a', type: 'llm', config: {} },
        { name: 'b', type: 'llm', config: {} },
      ],
      edges: [{ source: 'a', target: 'b', condition: 'a or b' }],
      state_schema: {},
    }
    const errors = validateGraph(def)
    expect(errors.some((e) => e.field === 'condition')).toBe(true)
  })

  it('reports error for incomplete condition: "a.b == " (missing literal)', () => {
    const def: WorkflowDefinitionDTO = {
      workflow_id: 'incomplete_cond',
      entry_point: 'a',
      nodes: [
        { name: 'a', type: 'llm', config: {} },
        { name: 'b', type: 'llm', config: {} },
      ],
      edges: [{ source: 'a', target: 'b', condition: 'a.b == ' }],
      state_schema: {},
    }
    const errors = validateGraph(def)
    expect(errors.some((e) => e.field === 'condition')).toBe(true)
  })

  it('accepts valid S7 condition: "a.b == \'OK\'"', () => {
    const def: WorkflowDefinitionDTO = {
      workflow_id: 'good_cond',
      entry_point: 'a',
      nodes: [
        { name: 'a', type: 'llm', config: {} },
        { name: 'b', type: 'llm', config: {} },
      ],
      edges: [{ source: 'a', target: 'b', condition: "a.b == 'OK'" }],
      state_schema: {},
    }
    const errors = validateGraph(def)
    const condErrors = errors.filter((e) => e.field === 'condition')
    expect(condErrors).toEqual([])
  })

  it('accepts valid S7 truthy condition: "result.ready"', () => {
    const def: WorkflowDefinitionDTO = {
      workflow_id: 'truthy_cond',
      entry_point: 'a',
      nodes: [
        { name: 'a', type: 'llm', config: {} },
        { name: 'b', type: 'llm', config: {} },
      ],
      edges: [{ source: 'a', target: 'b', condition: 'result.ready' }],
      state_schema: {},
    }
    const errors = validateGraph(def)
    const condErrors = errors.filter((e) => e.field === 'condition')
    expect(condErrors).toEqual([])
  })

  it('accepts edge with no condition (null/undefined)', () => {
    const def: WorkflowDefinitionDTO = {
      workflow_id: 'no_cond',
      entry_point: 'a',
      nodes: [
        { name: 'a', type: 'llm', config: {} },
        { name: 'b', type: 'http', config: {} },
      ],
      edges: [
        { source: 'a', target: 'b' },
        { source: 'a', target: 'b', condition: null },
      ],
      state_schema: {},
    }
    const errors = validateGraph(def)
    const condErrors = errors.filter((e) => e.field === 'condition')
    expect(condErrors).toEqual([])
  })
})
