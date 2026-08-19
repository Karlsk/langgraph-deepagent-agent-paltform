<script setup lang="ts">
import { computed, ref } from 'vue'
import { useRoute, useRouter } from 'vue-router'
import {
  ChatDotRound,
  Connection,
  Expand,
  Fold,
  FullScreen,
  MagicStick,
  Moon,
  Setting,
  User,
} from '@element-plus/icons-vue'

import { bootstrap as bootstrapAuth, currentUser, logout as logoutAction } from '@/composables/useAuth'
import { notifySuccess } from '@/utils/notify'

bootstrapAuth()

const route = useRoute()
const router = useRouter()
const activeMenu = computed(() => route.path)
const pageTitle = computed(() => String(route.meta.title ?? ''))
const user = currentUser()

const collapsed = ref(false)
function toggleSidebar(): void {
  collapsed.value = !collapsed.value
}

async function toggleFullscreen(): Promise<void> {
  if (document.fullscreenElement) {
    await document.exitFullscreen()
  } else {
    await document.documentElement.requestFullscreen()
  }
}

async function handleLogout(): Promise<void> {
  logoutAction()
  notifySuccess('已注销')
  await router.replace({ name: 'login' })
}
</script>

<template>
  <router-view v-if="route.meta.hideShell" />
  <el-container v-else class="app-shell">
    <el-aside class="app-sidebar" :width="collapsed ? '64px' : '232px'">
      <div class="app-brand">
        <span class="app-brand__mark" aria-hidden="true">A</span>
        <span v-if="!collapsed" class="app-brand__text">
          <strong>Agent Web</strong>
          <small>AI Agent Platform</small>
        </span>
      </div>
      <span v-if="user && !collapsed" class="app-brand__user" :title="user.email">
        {{ user.username ?? user.email }}
      </span>

      <el-menu
        class="app-nav"
        mode="vertical"
        router
        :collapse="collapsed"
        :collapse-transition="false"
        :default-active="activeMenu"
      >
        <el-menu-item index="/chat">
          <el-icon><ChatDotRound /></el-icon>
          <template #title>对话</template>
        </el-menu-item>
        <el-menu-item index="/agent">
          <el-icon><User /></el-icon>
          <template #title>Agent 管理</template>
        </el-menu-item>
        <el-menu-item index="/skill">
          <el-icon><MagicStick /></el-icon>
          <template #title>技能管理</template>
        </el-menu-item>
        <el-menu-item index="/mcp">
          <el-icon><Connection /></el-icon>
          <template #title>MCP 管理</template>
        </el-menu-item>
        <el-menu-item index="/llm">
          <el-icon><Setting /></el-icon>
          <template #title>模型管理</template>
        </el-menu-item>
      </el-menu>

      <div class="app-sidebar__footer">
        <button
          class="app-sidebar__toggle"
          type="button"
          :aria-label="collapsed ? '展开侧边栏' : '折叠侧边栏'"
          @click="toggleSidebar"
        >
          <el-icon>
            <Expand v-if="collapsed" />
            <Fold v-else />
          </el-icon>
        </button>
        <span v-if="!collapsed" class="app-sidebar__version">v0.1.0</span>
      </div>
    </el-aside>

    <el-container class="app-body">
      <el-header class="app-header" height="auto">
        <div class="app-header__actions">
          <button
            class="app-header__icon-btn"
            type="button"
            aria-label="全屏"
            @click="toggleFullscreen"
          >
            <el-icon><FullScreen /></el-icon>
          </button>
          <button
            class="app-header__icon-btn"
            type="button"
            aria-label="切换主题"
          >
            <el-icon><Moon /></el-icon>
          </button>
          <button class="app-header__icon-btn" type="button" aria-label="用户信息">
            <el-avatar :size="28" class="app-user__avatar">
              <el-icon><User /></el-icon>
            </el-avatar>
          </button>
          <button
            v-if="user"
            class="app-header__icon-btn"
            aria-label="注销"
            title="注销"
            @click="handleLogout"
          >
            <span class="app-header__logout-text">注销</span>
          </button>
        </div>
      </el-header>

      <div class="app-crumb-bar">
        <el-breadcrumb separator="/">
          <el-breadcrumb-item :to="{ path: '/' }">首页</el-breadcrumb-item>
        </el-breadcrumb>
        <strong class="app-crumb-bar__title">{{ pageTitle }}</strong>
      </div>

      <el-main class="app-main">
        <router-view />
      </el-main>
    </el-container>
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
  background: var(--color-bg-dark);
  transition: width var(--duration-base) var(--ease-standard);
  overflow: hidden;
}

.app-brand {
  display: flex;
  align-items: center;
  gap: 10px;
  padding: 20px 16px;
  flex-shrink: 0;
}

.app-brand__mark {
  display: grid;
  place-items: center;
  width: 34px;
  height: 34px;
  flex-shrink: 0;
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
  white-space: nowrap;
}

.app-brand__text strong {
  color: var(--color-text-on-dark);
  font-family: var(--app-font-display);
  font-size: 16px;
  font-weight: 800;
  letter-spacing: 0.02em;
}

.app-brand__text small {
  color: var(--color-text-on-dark-muted);
  font-size: 11px;
}

.app-nav {
  flex: 1;
  border-right: none;
  background: transparent;
  overflow-y: auto;
}

.app-nav :deep(.el-menu-item) {
  color: var(--color-text-on-dark-muted);
  transition:
    background var(--duration-base) var(--ease-standard),
    color var(--duration-base) var(--ease-standard);
}

.app-nav :deep(.el-menu-item:hover) {
  background: var(--color-bg-dark-raised);
  color: var(--color-text-on-dark);
}

.app-nav :deep(.el-menu-item.is-active) {
  position: relative;
  background: var(--color-bg-dark-raised);
  color: var(--color-text-on-dark);
  font-weight: 600;
}

.app-nav :deep(.el-menu-item.is-active::before) {
  content: '';
  position: absolute;
  top: 8px;
  bottom: 8px;
  left: 0;
  width: 2px;
  border-radius: var(--radius-sm);
  background: var(--color-primary-500);
}

.app-sidebar__footer {
  display: flex;
  align-items: center;
  justify-content: space-between;
  gap: 8px;
  padding: 12px 16px;
  border-top: 1px solid var(--color-bg-dark-raised);
  flex-shrink: 0;
}

.app-sidebar__toggle {
  display: grid;
  place-items: center;
  width: 32px;
  height: 32px;
  border: none;
  border-radius: var(--radius-md);
  background: transparent;
  color: var(--color-text-on-dark-muted);
  cursor: pointer;
  font-size: 16px;
  transition:
    background var(--duration-base) var(--ease-standard),
    color var(--duration-base) var(--ease-standard);
}

.app-sidebar__toggle:hover {
  background: var(--color-bg-dark-raised);
  color: var(--color-text-on-dark);
}

.app-sidebar__version {
  color: var(--color-text-on-dark-muted);
  font-size: 12px;
  white-space: nowrap;
}

.app-body {
  flex-direction: column;
  min-width: 0;
}

.app-header {
  display: flex;
  align-items: center;
  justify-content: flex-end;
  gap: 24px;
  padding: 0 24px;
  border-bottom: 1px solid var(--color-border-default);
  background: var(--color-bg-surface);
}

.app-header__actions {
  display: flex;
  align-items: center;
  gap: 8px;
}

.app-header__icon-btn {
  display: grid;
  place-items: center;
  width: 32px;
  height: 32px;
  border: none;
  border-radius: 50%;
  background: var(--color-bg-subtle);
  color: var(--color-text-secondary);
  cursor: pointer;
  font-size: 15px;
  transition:
    background var(--duration-base) var(--ease-standard),
    color var(--duration-base) var(--ease-standard);
}

.app-header__icon-btn:hover {
  background: var(--color-primary-50);
  color: var(--color-primary-500);
}

.app-user__avatar {
  background: var(--color-primary-50);
  color: var(--color-primary-500);
}
.app-header__logout-text {
  font-size: 12px;
  color: var(--color-text-secondary);
}
.app-brand__user {
  color: var(--color-text-on-dark-muted);
  font-size: 12px;
  margin-top: 4px;
  margin-left: 44px;
  padding-bottom: 8px;
  border-bottom: 1px solid var(--color-bg-dark-raised);
  white-space: nowrap;
  overflow: hidden;
  text-overflow: ellipsis;
}

.app-crumb-bar {
  display: flex;
  align-items: center;
  gap: 12px;
  height: 48px;
  padding: 0 24px;
  background: var(--color-bg-subtle);
}

.app-crumb-bar__title {
  color: var(--color-text-primary);
  font-size: 14px;
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

@media (max-width: 768px) {
  .app-sidebar {
    width: 64px !important;
  }

  .app-brand {
    justify-content: center;
    padding: 20px 0;
  }

  .app-brand__text,
  .app-sidebar__version {
    display: none;
  }

  .app-sidebar__footer {
    justify-content: center;
  }

  .app-header {
    padding: 0 16px;
  }

  .app-crumb-bar {
    padding: 0 16px;
  }

  .app-main {
    padding: 16px;
  }
}
</style>
