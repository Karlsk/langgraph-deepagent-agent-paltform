<script setup lang="ts">
import { onMounted, ref } from 'vue'

import { isBackendConnected } from './connection'

const connected = ref<boolean | null>(null)

onMounted(async () => {
  connected.value = await isBackendConnected()
})
</script>

<template>
  <div>
    <div>模型提供商管理</div>
    <p v-if="connected === true" class="connection-status connection-status--connected">
      后端已连接：Hify is running
    </p>
    <p v-else-if="connected === false" class="connection-status connection-status--disconnected">
      后端未连接
    </p>
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
