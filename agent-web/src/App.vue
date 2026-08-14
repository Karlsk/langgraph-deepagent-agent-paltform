<script setup lang="ts">
import { computed } from 'vue'
import { useRoute } from 'vue-router'
import {
  ChatLineRound,
  Connection,
  Cpu,
  MagicStick,
  SetUp,
} from '@element-plus/icons-vue'

const route = useRoute()
const activeMenu = computed(() => route.path)
</script>

<template>
  <el-container class="app-shell">
    <el-aside class="app-sidebar" width="232px">
      <div class="app-brand">
        <span class="app-brand__mark" aria-hidden="true">A</span>
        <span class="app-brand__text">
          <strong>Agent Web</strong>
          <small>智能体运营控制台</small>
        </span>
      </div>

      <el-menu
        class="app-menu"
        router
        :default-active="activeMenu"
        text-color="#5b6b85"
        active-text-color="#2f6bff"
      >
        <el-menu-item index="/chat">
          <el-icon><ChatLineRound /></el-icon>
          <span>对话</span>
        </el-menu-item>
        <el-menu-item index="/agent">
          <el-icon><Cpu /></el-icon>
          <span>Agent 管理</span>
        </el-menu-item>
        <el-menu-item index="/skill">
          <el-icon><MagicStick /></el-icon>
          <span>技能管理</span>
        </el-menu-item>
        <el-menu-item index="/mcp">
          <el-icon><Connection /></el-icon>
          <span>MCP 管理</span>
        </el-menu-item>
        <el-menu-item index="/llm">
          <el-icon><SetUp /></el-icon>
          <span>模型服务</span>
        </el-menu-item>
      </el-menu>

      <div class="app-sidebar__footer">API v1 · 骨架预览</div>
    </el-aside>

    <el-main class="app-main">
      <div class="app-content app-card">
        <router-view />
      </div>
    </el-main>
  </el-container>
</template>

<style scoped>
.app-shell {
  height: 100%;
  background: var(--app-bg);
}

.app-sidebar {
  display: flex;
  flex-direction: column;
  border-right: 1px solid var(--app-border);
  background: #ffffff;
}

.app-brand {
  display: flex;
  align-items: center;
  gap: 12px;
  padding: 20px 20px 18px;
}

.app-brand__mark {
  display: grid;
  place-items: center;
  width: 38px;
  height: 38px;
  border-radius: 10px;
  background: linear-gradient(135deg, #2f6bff 0%, #1f52d6 100%);
  box-shadow: 0 6px 16px rgba(47, 107, 255, 0.28);
  color: #ffffff;
  font-family: var(--app-font-display);
  font-size: 18px;
  font-weight: 700;
}

.app-brand__text {
  display: flex;
  flex-direction: column;
  gap: 2px;
  line-height: 1.2;
}

.app-brand__text strong {
  color: var(--app-ink);
  font-family: var(--app-font-display);
  font-size: 15px;
  letter-spacing: 0.01em;
}

.app-brand__text small {
  color: var(--app-ink-muted);
  font-size: 11px;
}

.app-menu {
  flex: 1;
  border-right: none;
  padding: 4px 12px;
}

.app-menu :deep(.el-menu-item) {
  margin-bottom: 4px;
  border-radius: 8px;
}

.app-menu :deep(.el-menu-item.is-active) {
  background: var(--el-color-primary-light-9);
  font-weight: 600;
}

.app-menu :deep(.el-menu-item:hover) {
  background: var(--app-bg);
}

.app-sidebar__footer {
  padding: 16px 20px;
  border-top: 1px solid var(--app-border);
  color: var(--app-ink-muted);
  font-family: var(--app-font-display);
  font-size: 11px;
  letter-spacing: 0.12em;
  text-transform: uppercase;
}

.app-main {
  padding: 24px;
  overflow-y: auto;
}

.app-content {
  height: 100%;
  overflow: hidden;
}
</style>
