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
          <span>模型管理</span>
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
  background: var(--color-bg-canvas);
}

.app-sidebar {
  display: flex;
  flex-direction: column;
  border-right: 1px solid rgba(255, 255, 255, 0.08);
  background: var(--color-bg-sidebar);
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
  border-radius: var(--radius-md);
  background: linear-gradient(
    135deg,
    var(--color-primary-500) 0%,
    var(--color-accent-500) 100%
  );
  box-shadow: var(--shadow-sm);
  color: var(--color-text-on-dark);
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
  color: var(--color-text-on-dark);
  font-family: var(--app-font-display);
  font-size: 15px;
  letter-spacing: 0.01em;
}

.app-brand__text small {
  color: var(--color-text-on-dark-muted);
  font-size: 11px;
}

.app-menu {
  flex: 1;
  border-right: none;
  padding: 4px 12px;
  background: transparent;
}

.app-menu :deep(.el-menu-item) {
  margin-bottom: 4px;
  border-radius: var(--radius-md);
  color: var(--color-text-on-dark-muted);
  transition:
    background-color var(--duration-base) var(--ease-standard),
    color var(--duration-base) var(--ease-standard);
}

.app-menu :deep(.el-menu-item.is-active) {
  background: rgba(255, 255, 255, 0.16);
  color: var(--color-text-on-dark);
  font-weight: 600;
}

.app-menu :deep(.el-menu-item:hover) {
  background: rgba(255, 255, 255, 0.08);
  color: var(--color-text-on-dark);
}

.app-sidebar__footer {
  padding: 16px 20px;
  border-top: 1px solid rgba(255, 255, 255, 0.08);
  color: var(--color-text-on-dark-muted);
  font-family: var(--app-font-display);
  font-size: 11px;
  letter-spacing: 0.12em;
  text-transform: uppercase;
}

.app-main {
  padding: 24px;
  overflow-y: auto;
  background: var(--color-bg-canvas);
}

.app-content {
  height: 100%;
  overflow: hidden;
}
</style>
