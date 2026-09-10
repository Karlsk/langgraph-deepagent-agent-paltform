import { ref, computed, shallowRef, type Ref, type ComputedRef, type ShallowRef } from 'vue'
import type { Node, Edge } from '@vue-flow/core'
import type { WorkflowDefinitionDTO, StateFieldDTO } from '@/api/workflow'
import { getWorkflow } from '@/api/workflow'
import { definitionToGraph } from '@/composables/useWorkflowGraph'
import { nextNodeName, DEFAULT_CONFIGS } from '@/views/workflow/canvas/nodeCatalog'
import { deriveStateChannels } from '@/utils/s7Condition'
import { notifyError } from '@/utils/notify'

export interface WorkflowDesignerState {
  nodes: ShallowRef<Node[]>
  edges: ShallowRef<Edge[]>
  meta: Ref<{
    workflow_id: string
    entry_point: string
    state_schema: Record<string, StateFieldDTO>
  }>
  selectedNodeId: Ref<string | null>
  selectedNode: ComputedRef<Node | null>
  isDirty: Ref<boolean>
  isLoading: Ref<boolean>
  conditionDialogVisible: Ref<boolean>
  conditionEdge: ComputedRef<{ source: string; target: string; condition: string | null }>
  pendingEdge: Ref<{ source: string; target: string } | null>
  nodeNames: ComputedRef<string[]>
  stateChannels: ComputedRef<string[]>
  handleAddNode: (payload: { type: 'llm' | 'http'; position: { x: number; y: number } }) => void
  handleConnect: (connection: { source: string; target: string }) => void
  handleConditionConfirm: (payload: { target: string; condition: string | null }) => void
  handleSelectNode: (id: string | null) => void
  handleUpdateNode: (payload: { name?: string; config?: Record<string, unknown> }) => void
  handleRemoveNode: (id: string) => void
  handleUpdateSchema: (schema: Record<string, StateFieldDTO>) => void
  handleUpdateNodes: (newNodes: Node[]) => void
  handleUpdateEdges: (newEdges: Edge[]) => void
  loadWorkflow: (workflowId: string) => Promise<void>
}

export function useWorkflowDesigner(): WorkflowDesignerState {
  const nodes = shallowRef<Node[]>([])
  const edges = shallowRef<Edge[]>([])
  const meta = ref<{
    workflow_id: string
    entry_point: string
    state_schema: Record<string, StateFieldDTO>
  }>({
    workflow_id: '',
    entry_point: '',
    state_schema: {},
  })

  const selectedNodeId = ref<string | null>(null)
  const isDirty = ref(false)
  const isLoading = ref(false)

  const conditionDialogVisible = ref(false)
  const pendingEdge = ref<{ source: string; target: string } | null>(null)

  const selectedNode = computed<Node | null>(() => {
    const found = nodes.value.find((n: Node) => n.id === selectedNodeId.value)
    return found ?? null
  })

  const nodeNames = computed(() =>
    nodes.value.filter((n) => n.type === 'workflow').map((n) => n.data!.name as string),
  )

  const stateChannels = computed(() =>
    deriveStateChannels(meta.value.state_schema, nodeNames.value),
  )

  const conditionEdge = computed(() => ({
    source: pendingEdge.value?.source ?? '',
    target: pendingEdge.value?.target ?? '',
    condition: null as string | null,
  }))

  function isSourceConditional(sourceId: string): boolean {
    return edges.value.some((e) => e.source === sourceId && e.data?.condition)
  }

  function handleAddNode(payload: { type: 'llm' | 'http'; position: { x: number; y: number } }) {
    const existingNames = nodeNames.value
    const name = nextNodeName(payload.type, existingNames)
    const id = name

    const newNode: Node = {
      id,
      type: 'workflow',
      position: payload.position,
      data: {
        name,
        type: payload.type,
        config: { ...DEFAULT_CONFIGS[payload.type] },
      },
    }

    nodes.value = [...nodes.value, newNode]
    isDirty.value = true
  }

  function handleConnect(connection: { source: string; target: string }) {
    if (isSourceConditional(connection.source)) {
      pendingEdge.value = { source: connection.source, target: connection.target }
      conditionDialogVisible.value = true
      return
    }

    const newEdge: Edge = {
      id: `${connection.source}-${connection.target}`,
      source: connection.source,
      target: connection.target,
      data: { condition: null },
    }
    edges.value = [...edges.value, newEdge]
    isDirty.value = true
  }

  function handleConditionConfirm(payload: { target: string; condition: string | null }) {
    if (!pendingEdge.value) return

    const newEdge: Edge = {
      id: `${pendingEdge.value.source}-${payload.target}`,
      source: pendingEdge.value.source,
      target: payload.target,
      data: { condition: payload.condition },
    }
    edges.value = [...edges.value, newEdge]
    isDirty.value = true

    conditionDialogVisible.value = false
    pendingEdge.value = null
  }

  function handleSelectNode(id: string | null) {
    selectedNodeId.value = id
  }

  function handleUpdateNode(payload: { name?: string; config?: Record<string, unknown> }) {
    if (!selectedNodeId.value) return

    nodes.value = nodes.value.map((n) => {
      if (n.id !== selectedNodeId.value) return n

      const updatedData = { ...n.data }
      if (payload.config !== undefined) {
        updatedData.config = payload.config
      }
      if (payload.name !== undefined) {
        updatedData.name = payload.name
      }

      const updatedNode: Node = { ...n, data: updatedData }

      if (payload.name !== undefined && payload.name !== n.id) {
        updatedNode.id = payload.name
        if (meta.value.entry_point === n.id) {
          meta.value = { ...meta.value, entry_point: payload.name }
        }
      }

      return updatedNode
    })

    if (payload.name !== undefined && payload.name !== selectedNodeId.value) {
      const oldId = selectedNodeId.value
      const newName = payload.name

      edges.value = edges.value.map((e) => ({
        ...e,
        id: `${e.source === oldId ? newName : e.source}-${e.target === oldId ? newName : e.target}`,
        source: e.source === oldId ? newName : e.source,
        target: e.target === oldId ? newName : e.target,
      }))

      selectedNodeId.value = newName
    }

    isDirty.value = true
  }

  function handleRemoveNode(id: string) {
    nodes.value = nodes.value.filter((n) => n.id !== id)
    edges.value = edges.value.filter((e) => e.source !== id && e.target !== id)

    if (selectedNodeId.value === id) {
      selectedNodeId.value = null
    }

    isDirty.value = true
  }

  function handleUpdateSchema(schema: Record<string, StateFieldDTO>) {
    meta.value = { ...meta.value, state_schema: schema }
    isDirty.value = true
  }

  function handleUpdateNodes(newNodes: Node[]) {
    nodes.value = newNodes
  }

  function handleUpdateEdges(newEdges: Edge[]) {
    edges.value = newEdges
  }

  async function loadWorkflow(workflowId: string) {
    isLoading.value = true
    try {
      const result = await getWorkflow(workflowId, 'json')
      const dto = result as WorkflowDefinitionDTO
      const graph = definitionToGraph(dto)

      nodes.value = graph.nodes
      edges.value = graph.edges
      meta.value = {
        workflow_id: dto.workflow_id,
        entry_point: dto.entry_point,
        state_schema: dto.state_schema,
      }
      isDirty.value = false
    } catch {
      notifyError('加载工作流定义失败')
      throw new Error('load_workflow_failed')
    } finally {
      isLoading.value = false
    }
  }

  return {
    nodes,
    edges,
    meta,
    selectedNodeId,
    selectedNode,
    isDirty,
    isLoading,
    conditionDialogVisible,
    conditionEdge,
    pendingEdge,
    nodeNames,
    stateChannels,
    handleAddNode,
    handleConnect,
    handleConditionConfirm,
    handleSelectNode,
    handleUpdateNode,
    handleRemoveNode,
    handleUpdateSchema,
    handleUpdateNodes,
    handleUpdateEdges,
    loadWorkflow,
  }
}
