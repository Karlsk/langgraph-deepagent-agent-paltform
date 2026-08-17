<script setup lang="ts">
import { computed, ref } from 'vue'
import { useRoute } from 'vue-router'
import {
  Connection,
  Expand,
  Fold,
  MagicStick,
  Setting,
  User,
  ChatDotRound,
} from '@element-plus/icons-vue'

const route = useRoute()
const activeMenu = computed(() => route.path)
const isSidebarCollapsed = ref(false)
const sidebarWidth = computed(() =>
  isSidebarCollapsed.value
    ? 'var(--sidebar-width-collapsed)'
    : 'var(--sidebar-width-expanded)',
)
</script>

<template>
  <el-container class="app-shell">
    <el-aside id="app-sidebar" class="app-sidebar" :width="sidebarWidth">
      <div class="app-brand" :class="{ 'app-brand--collapsed': isSidebarCollapsed }">
        <span class="app-brand__mark" aria-hidden="true">H</span>
        <span v-if="!isSidebarCollapsed" class="app-brand__text">
          <strong>Hify</strong>
          <small>AI Agent Platform</small>
        </span>
      </div>

      <el-menu
        class="app-menu"
        :class="{ 'app-menu--collapsed': isSidebarCollapsed }"
        router
        :default-active="activeMenu"
        :collapse="isSidebarCollapsed"
        :collapse-transition="false"
      >
        <el-menu-item index="/chat">
          <el-icon><ChatDotRound /></el-icon>
          <span>对话</span>
        </el-menu-item>
        <el-menu-item index="/agent">
          <el-icon><User /></el-icon>
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
          <el-icon><Setting /></el-icon>
          <span>模型管理</span>
        </el-menu-item>
      </el-menu>

      <div
        class="app-sidebar__footer"
        :class="{ 'app-sidebar__footer--collapsed': isSidebarCollapsed }"
      >
        <button
          type="button"
          class="app-sidebar__toggle"
          :aria-expanded="!isSidebarCollapsed"
          aria-controls="app-sidebar"
          :aria-label="isSidebarCollapsed ? '展开侧栏' : '折叠侧栏'"
          :title="isSidebarCollapsed ? '展开侧栏' : '折叠侧栏'"
          @click="isSidebarCollapsed = !isSidebarCollapsed"
        >
          <el-icon><Expand v-if="isSidebarCollapsed" /><Fold v-else /></el-icon>
          <span v-if="!isSidebarCollapsed">折叠侧栏</span>
        </button>
        <span class="app-sidebar__version">
          {{ isSidebarCollapsed ? 'v0.1' : 'Version 0.1.0' }}
        </span>
      </div>
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
  overflow: hidden;
  border-right: 1px solid var(--color-sidebar-border);
  background: var(--color-bg-sidebar);
  background-image:
    radial-gradient(
      circle at 12% 0%,
      color-mix(in srgb, var(--color-primary-500) 12%, transparent) 0%,
      transparent 34%
    ),
    radial-gradient(
      circle at 88% 100%,
      color-mix(in srgb, var(--color-accent-500) 8%, transparent) 0%,
      transparent 32%
    );
  transition: width var(--duration-base) var(--ease-standard);
}

.app-brand {
  display: flex;
  align-items: center;
  gap: 12px;
  min-height: 82px;
  padding: 20px 18px 18px;
  white-space: nowrap;
}

.app-brand--collapsed {
  justify-content: center;
  padding-inline: 0;
}

.app-brand__mark {
  display: grid;
  place-items: center;
  width: 38px;
  height: 38px;
  border-radius: var(--radius-md);
  border: 1px solid color-mix(in srgb, var(--color-primary-500) 42%, transparent);
  background: color-mix(in srgb, var(--color-primary-500) 16%, transparent);
  box-shadow:
    0 0 20px color-mix(in srgb, var(--color-primary-500) 16%, transparent),
    inset 0 1px 0 rgba(255, 255, 255, 0.08);
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
  background: linear-gradient(
    115deg,
    var(--color-primary-500) 0%,
    var(--color-accent-500) 100%
  );
  background-clip: text;
  -webkit-background-clip: text;
  font-family: var(--app-font-display);
  font-size: 20px;
  font-weight: 800;
  letter-spacing: 0.02em;
  -webkit-text-fill-color: transparent;
}

.app-brand__text small {
  color: var(--color-text-on-dark-muted);
  font-size: 11px;
}

.app-menu {
  flex: 1;
  overflow-y: auto;
  border-right: none;
  padding: 4px 10px;
  background: transparent;
}

.app-menu :deep(.el-menu-item) {
  position: relative;
  margin-bottom: 4px;
  border-radius: var(--radius-md);
  color: var(--color-text-on-dark);
  white-space: nowrap;
  transition:
    background-color var(--duration-base) var(--ease-standard),
    color var(--duration-base) var(--ease-standard);
}

.app-menu :deep(.el-menu-item.is-active) {
  background: var(--color-sidebar-active);
  color: var(--color-text-on-dark);
  font-weight: 600;
  box-shadow: inset var(--sidebar-indicator-width) 0 0 var(--color-primary-500);
}

.app-menu :deep(.el-menu-item:hover) {
  background: var(--color-sidebar-hover);
  color: var(--color-text-on-dark);
}

.app-menu--collapsed :deep(.el-menu-item) {
  padding-inline: 0;
}

.app-sidebar__footer {
  display: flex;
  flex-direction: column;
  gap: 10px;
  padding: 16px 20px;
  border-top: 1px solid var(--color-sidebar-border);
  color: var(--color-text-on-dark-muted);
  font-family: var(--app-font-display);
  font-size: 11px;
  letter-spacing: 0.12em;
  text-transform: uppercase;
}

.app-sidebar__footer--collapsed {
  align-items: center;
  padding-inline: 8px;
}

.app-sidebar__toggle {
  display: flex;
  align-items: center;
  justify-content: flex-start;
  gap: 10px;
  width: 100%;
  min-height: 36px;
  padding: 0 10px;
  border: 1px solid transparent;
  border-radius: var(--radius-md);
  background: transparent;
  color: var(--color-text-on-dark-muted);
  cursor: pointer;
  font-family: inherit;
  font-size: 13px;
  transition:
    background-color var(--duration-base) var(--ease-standard),
    color var(--duration-base) var(--ease-standard);
}

.app-sidebar__toggle:hover {
  border-color: var(--color-sidebar-border);
  background: var(--color-sidebar-hover);
  color: var(--color-text-on-dark);
}

.app-sidebar__footer--collapsed .app-sidebar__toggle {
  justify-content: center;
  padding-inline: 0;
}

.app-sidebar__version {
  color: var(--color-text-on-dark-muted);
  font-family: var(--app-font-display);
  font-size: 10px;
  letter-spacing: 0.1em;
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
