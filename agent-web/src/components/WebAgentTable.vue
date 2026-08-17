<script lang="ts">
/** 表格列配置：slot 存在时以同名具名作用域插槽自定义单元格 */
export interface TableColumnConfig {
  label: string
  prop: string
  width?: string | number
  slot?: string
}
</script>

<script setup lang="ts" generic="T">
import { onMounted, ref, watch } from 'vue'

import { useRequest } from '@/composables/useRequest'
import type { PageQuery, PageResult } from '@/types'

const props = withDefaults(
  defineProps<{
    columns: TableColumnConfig[]
    /** 分页契约先行：后端上真分页前，父组件可用 paginateLocal 包装裸列表 */
    api: (query: PageQuery) => Promise<PageResult<T>>
    pagination?: boolean
    /** 额外过滤条件（如 keyword），变化时重置到第一页并重新请求 */
    query?: Record<string, unknown>
    immediate?: boolean
    defaultPageSize?: number
  }>(),
  {
    pagination: true,
    query: undefined,
    immediate: true,
    defaultPageSize: 10,
  },
)

const page = ref(1)
const pageSize = ref(props.defaultPageSize)
const total = ref(0)
const rows = ref<T[]>([]) as { value: T[] }

// loading / error 三态由 useRequest 托管；rows/total 由本组件按分页语义收敛
const { loading, execute } = useRequest((query: PageQuery) => props.api(query))

async function fetchData(): Promise<void> {
  const result = await execute({
    page: page.value,
    pageSize: pageSize.value,
    ...props.query,
  })
  if (result) {
    rows.value = result.items
    total.value = result.total
  } else {
    // 错误提示由统一请求层全局拦截器承担，此处只收敛为空数据
    rows.value = []
    total.value = 0
  }
}

function handleCurrentChange(next: number): void {
  page.value = next
  void fetchData()
}

function handleSizeChange(size: number): void {
  pageSize.value = size
  page.value = 1
  void fetchData()
}

/** 保留当前页重新请求 */
function refresh(): void {
  void fetchData()
}

watch(
  () => props.query,
  () => {
    page.value = 1
    void fetchData()
  },
  { deep: true },
)

onMounted(() => {
  if (props.immediate) {
    void fetchData()
  }
})

defineExpose({ refresh })
</script>

<template>
  <div class="web-agent-table">
    <el-table v-loading="loading" :data="rows">
      <template v-for="column in columns" :key="column.prop">
        <el-table-column
          v-if="column.slot"
          :label="column.label"
          :prop="column.prop"
          :width="column.width"
        >
          <template #default="{ row }">
            <slot :name="column.slot" :row="row" />
          </template>
        </el-table-column>
        <el-table-column
          v-else
          :label="column.label"
          :prop="column.prop"
          :width="column.width"
        />
      </template>
      <template #empty>
        <el-empty description="暂无数据" />
      </template>
    </el-table>
    <el-pagination
      v-if="pagination"
      class="web-agent-table__pagination"
      :current-page="page"
      :page-size="pageSize"
      :total="total"
      layout="total, sizes, prev, pager, next"
      @current-change="handleCurrentChange"
      @size-change="handleSizeChange"
    />
  </div>
</template>

<style scoped>
.web-agent-table__pagination {
  margin-top: 16px;
  justify-content: flex-end;
}
</style>
