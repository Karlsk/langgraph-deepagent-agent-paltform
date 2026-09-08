/**
 * useWorkflowGraph — Vue Flow 图 ↔ WorkflowDefinitionDTO 双向序列化 + 前端预校验（spec-09）。
 * 三个纯函数，不依赖 Vue 响应式。
 */
import type { Node, Edge } from '@vue-flow/core'
import type { WorkflowDefinitionDTO, NodeDTO, EdgeDTO } from '@/api/workflow'

export interface GraphValidationError {
  field: string
  message: string
  nodeId?: string
  edgeId?: string
}

const VALID_NODE_TYPES = new Set(['llm', 'http'])
const END_NODE_ID = 'END'
const GRID_SPACING = 200

const S7_EQUALITY_RE = /^[a-zA-Z_][a-zA-Z0-9_.]*\s*==\s*'[^']*'$/
const S7_TRUTHY_RE = /^[a-zA-Z_][a-zA-Z0-9_.]*$/

function defaultPosition(index: number): { x: number; y: number } {
  const col = index % 4
  const row = Math.floor(index / 4)
  return { x: col * GRID_SPACING, y: row * GRID_SPACING }
}

export function definitionToGraph(def: WorkflowDefinitionDTO): { nodes: Node[]; edges: Edge[] } {
  const layoutNodes = def.ui_layout?.nodes ?? {}

  const nodes: Node[] = def.nodes.map((dto, i) => ({
    id: dto.name,
    type: 'workflow',
    position: layoutNodes[dto.name] ?? defaultPosition(i),
    data: { name: dto.name, type: dto.type, config: dto.config },
  }))

  const hasEndEdges = def.edges.some((e) => e.target === END_NODE_ID)
  if (hasEndEdges) {
    nodes.push({
      id: END_NODE_ID,
      type: 'workflow',
      position: defaultPosition(def.nodes.length),
      data: { name: END_NODE_ID, type: 'end', config: {} },
    })
  }

  const edges: Edge[] = def.edges.map((dto) => ({
    id: `${dto.source}-${dto.target}`,
    source: dto.source,
    target: dto.target,
    data: { condition: dto.condition ?? null },
  }))

  return { nodes, edges }
}

export function graphToDefinition(
  meta: { workflow_id: string; entry_point: string; state_schema: WorkflowDefinitionDTO['state_schema'] },
  nodes: Node[],
  edges: Edge[],
): WorkflowDefinitionDTO {
  const realNodes = nodes.filter((n) => n.id !== END_NODE_ID)

  const nodeDTOs: NodeDTO[] = realNodes.map((n) => ({
    name: n.data!.name as string,
    type: n.data!.type as NodeDTO['type'],
    config: (n.data!.config ?? {}) as Record<string, unknown>,
  }))

  const edgeDTOs: EdgeDTO[] = edges.map((e) => {
    const dto: EdgeDTO = { source: e.source, target: e.target }
    const cond = e.data?.condition as string | null | undefined
    if (cond != null && cond !== '') {
      dto.condition = cond
    }
    return dto
  })

  const uiNodes: Record<string, { x: number; y: number }> = {}
  for (const n of realNodes) {
    uiNodes[n.id] = { x: n.position.x, y: n.position.y }
  }

  return {
    workflow_id: meta.workflow_id,
    entry_point: meta.entry_point,
    nodes: nodeDTOs,
    edges: edgeDTOs,
    state_schema: meta.state_schema,
    ui_layout: { nodes: uiNodes },
  }
}

export function validateGraph(def: WorkflowDefinitionDTO): GraphValidationError[] {
  const errors: GraphValidationError[] = []

  if (def.nodes.length === 0) {
    errors.push({ field: 'nodes', message: 'At least one node is required.' })
    return errors
  }

  const nameSet = new Set<string>()
  for (const node of def.nodes) {
    if (!node.name || node.name.trim() === '') {
      errors.push({ field: 'nodes', message: 'Node name must not be empty.' })
      continue
    }
    if (nameSet.has(node.name)) {
      errors.push({ field: 'nodes', message: `Duplicate node name: "${node.name}".`, nodeId: node.name })
    }
    nameSet.add(node.name)

    if (!VALID_NODE_TYPES.has(node.type)) {
      errors.push({
        field: 'nodes',
        message: `Invalid node type "${node.type}". Must be one of: llm, http.`,
        nodeId: node.name,
      })
    }
  }

  if (!nameSet.has(def.entry_point)) {
    errors.push({ field: 'entry_point', message: `entry_point "${def.entry_point}" does not match any node.` })
  }

  for (const edge of def.edges) {
    const edgeId = `${edge.source}-${edge.target}`
    if (!nameSet.has(edge.source)) {
      errors.push({ field: 'edges', message: `Edge source "${edge.source}" is not a valid node.`, edgeId })
    }
    if (edge.target !== END_NODE_ID && !nameSet.has(edge.target)) {
      errors.push({ field: 'edges', message: `Edge target "${edge.target}" is not a valid node.`, edgeId })
    }
    if (edge.condition != null && edge.condition !== '') {
      if (!isValidS7Condition(edge.condition)) {
        errors.push({
          field: 'condition',
          message: `Condition "${edge.condition}" does not match S7 grammar.`,
          edgeId,
        })
      }
    }
  }

  return errors
}

function isValidS7Condition(condition: string): boolean {
  return S7_EQUALITY_RE.test(condition.trim()) || S7_TRUTHY_RE.test(condition.trim())
}
