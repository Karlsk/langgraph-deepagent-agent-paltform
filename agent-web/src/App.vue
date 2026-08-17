<script setup lang="ts">
import { computed } from 'vue'
import { useRoute } from 'vue-router'
import {
  Connection,
  MagicStick,
  Setting,
  User,
  ChatDotRound,
} from '@element-plus/icons-vue'

const route = useRoute()
const activeMenu = computed(() => route.path)
const pageTitle = computed(() => String(route.meta.title ?? ''))
</script>

<template>
  <el-container class="app-shell">
    <el-header class="app-header" height="auto">
      <div class="app-brand">
        <span class="app-brand__mark" aria-hidden="true">A</span>
        <span class="app-brand__text">
          <strong>Agent Web</strong>
          <small>AI Agent Platform</small>
        </span>
      </div>

      <el-menu
        class="app-nav"
        mode="horizontal"
        router
        :default-active="activeMenu"
        :ellipsis="false"
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

      <div class="app-user">
        <el-avatar :size="32" class="app-user__avatar">
          <el-icon><User /></el-icon>
        </el-avatar>
        <span class="app-user__name">管理员</span>
      </div>
    </el-header>

    <el-main class="app-main">
      <el-breadcrumb class="app-breadcrumb" separator="/">
        <el-breadcrumb-item :to="{ path: '/' }">首页</el-breadcrumb-item>
        <el-breadcrumb-item>{{ pageTitle }}</el-breadcrumb-item>
      </el-breadcrumb>
      <router-view />
    </el-main>
  </el-container>
</template>

<style scoped>
.app-shell {
  height: 100%;
  flex-direction: column;
  background: var(--color-bg-canvas);
}

.app-header {
  display: flex;
  align-items: center;
  gap: 24px;
  padding: 0 24px;
  border-bottom: 1px solid var(--color-border-default);
  background: var(--color-bg-surface);
}

.app-brand {
  display: flex;
  align-items: center;
  gap: 10px;
  flex-shrink: 0;
}

.app-brand__mark {
  display: grid;
  place-items: center;
  width: 34px;
  height: 34px;
  border-radius: var(--radius-md);
  background: linear-gradient(
    135deg,
    var(--color-primary-500),
    var(--color-accent-500)
  );
  color: var(--color-bg-surface);
  font-family: var(--app-font-display);
  font-size: 16px;
  font-weight: 700;
}

.app-brand__text {
  display: flex;
  flex-direction: column;
  gap: 1px;
  line-height: 1.2;
}

.app-brand__text strong {
  color: var(--color-text-primary);
  font-family: var(--app-font-display);
  font-size: 16px;
  font-weight: 800;
  letter-spacing: 0.02em;
}

.app-brand__text small {
  color: var(--color-text-tertiary);
  font-size: 11px;
}

.app-nav {
  flex: 1;
  min-width: 0;
  border-bottom: none;
  background: transparent;
}

.app-nav :deep(.el-menu-item) {
  border-bottom: 2px solid transparent;
  color: var(--color-text-secondary);
  transition:
    border-color var(--duration-base) var(--ease-standard),
    color var(--duration-base) var(--ease-standard);
}

.app-nav :deep(.el-menu-item:hover) {
  color: var(--color-text-primary);
  background: transparent;
}

.app-nav :deep(.el-menu-item.is-active) {
  border-bottom-color: var(--color-primary-500);
  color: var(--color-primary-500);
  font-weight: 600;
}

.app-user {
  display: flex;
  align-items: center;
  gap: 10px;
  flex-shrink: 0;
}

.app-user__avatar {
  background: var(--color-primary-50);
  color: var(--color-primary-500);
}

.app-user__name {
  color: var(--color-text-primary);
  font-size: 13px;
  font-weight: 600;
}

.app-main {
  display: flex;
  flex-direction: column;
  gap: 16px;
  padding: 24px;
  overflow-y: auto;
  background: var(--color-bg-canvas);
}

.app-breadcrumb {
  flex-shrink: 0;
}

@media (max-width: 768px) {
  .app-header {
    flex-wrap: wrap;
    gap: 8px;
    padding: 12px 16px;
  }

  .app-nav {
    flex-basis: 100%;
    order: 3;
    overflow-x: auto;
  }

  .app-user {
    margin-left: auto;
  }

  .app-main {
    padding: 16px;
  }
}
</style>
