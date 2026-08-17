<script setup lang="ts">
import { onMounted, ref } from 'vue'

import { isBackendConnected } from './connection'

const connected = ref<boolean | null>(null)

onMounted(async () => {
  connected.value = await isBackendConnected()
})
</script>

<template>
  <div class="page-view">
    <header class="page-view__header">
      <div>
        <h1 class="page-view__title">模型管理</h1>
        <p class="page-view__desc">管理模型服务提供商与接入配置。</p>
      </div>
      <div class="page-view__actions">
        <el-button class="app-btn app-btn--secondary">次要操作</el-button>
        <el-button class="app-btn app-btn--primary">新建提供商</el-button>
      </div>
    </header>
    <section class="content-card page-view__body">
      <div>模型提供商管理</div>
      <p v-if="connected === true" class="connection-status connection-status--connected">
        后端已连接：Hify is running
      </p>
      <p v-else-if="connected === false" class="connection-status connection-status--disconnected">
        后端未连接
      </p>
    </section>
  </div>
</template>

<style scoped>
.connection-status--connected {
  color: var(--el-color-success);
}

.connection-status--disconnected {
  color: var(--el-color-danger);
}
</style>
