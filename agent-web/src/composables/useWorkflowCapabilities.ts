/**
 * useWorkflowCapabilities composable（spec-19）。
 *
 * 查询当前用户的工作流编辑能力（can_edit），支持保守降级：
 * - API 请求失败 → canEdit=false（只读）
 * - loaded 状态：初始 false，请求后 true
 * - refresh() 重新拉取
 *
 * 模块单例模式：canEdit/loaded 为模块级 ref，所有调用方共享同一份状态。
 */
import { ref } from 'vue'
import { getWorkflowCapabilities } from '@/api/workflow'

const canEdit = ref(false)
const loaded = ref(false)

export function useWorkflowCapabilities() {
  async function refresh(): Promise<void> {
    try {
      const { can_edit } = await getWorkflowCapabilities()
      canEdit.value = can_edit
    } catch {
      canEdit.value = false
    } finally {
      loaded.value = true
    }
  }

  return { canEdit, loaded, refresh }
}
