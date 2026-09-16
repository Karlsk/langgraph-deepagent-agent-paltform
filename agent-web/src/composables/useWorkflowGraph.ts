/**
 * useWorkflowGraph — Vue Flow 图 ↔ WorkflowDefinitionDTO 双向序列化 + 前端预校验（spec-09）。
 * 三个纯函数，不依赖 Vue 响应式。
 */
import type { Node, Edge } from '@vue-flow/core'
import type { WorkflowDefinitionDTO, NodeDTO, EdgeDTO, WorkflowNodeType } from '@/api/workflow'
import { isValidS7Condition } from '@/utils/s7Condition'

export interface GraphValidationError {
  field: string
  message: string
  nodeId?: string
  edgeId?: string
}

const VALID_NODE_TYPES = new Set<WorkflowNodeType>(['llm', 'http', 'python', 'subworkflow', 'react'])
const END_NODE_ID = 'END'
const GRID_SPACING = 200

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
        message: `Invalid node type "${node.type}". Must be one of: llm, http, python, subworkflow, react.`,
        nodeId: node.name,
      })
    }

    // python 预校验只是省去一次必然失败的 PUT；安全边界始终在后端（S18）
    if (node.type === 'python') {
      if ('entry' in node.config) {
        errors.push({
          field: 'nodes',
          message: `Python node "${node.name}" must not use entry mode: it cannot be sandboxed.`,
          nodeId: node.name,
        })
      }
      const code = node.config.code
      if (typeof code !== 'string' || code.trim() === '') {
        errors.push({
          field: 'nodes',
          message: `Python node "${node.name}" requires non-empty code.`,
          nodeId: node.name,
        })
      }
    }

    // 只校验结构：被引用工作流是否存在是运行期检查（S18），前端无注册表可问
    if (node.type === 'subworkflow') {
      const ref = node.config.workflow_id
      if (typeof ref !== 'string' || ref.trim() === '') {
        errors.push({
          field: 'nodes',
          message: `Subworkflow node "${node.name}" requires a non-empty workflow_id.`,
          nodeId: node.name,
        })
      }
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
