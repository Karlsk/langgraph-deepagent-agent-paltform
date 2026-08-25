<script setup lang="ts">
/**
 * 登录页（Phase 1 G1 单层用户 token）：
 * 左品牌 + 右卡片；含字段图标 + "去注册"入口 + 错误回显。
 *
 * 流程（Phase 1 G1 单层）：
 * 1. /auth/login（form 表单） → 同时拿 access_token（7 天）+ refresh_token（30 天）；
 * 2. access_token 写入 localStorage `auth.userToken`（持久化）；
 *    refresh_token 仅内存保存（`authStorage.setRefreshToken`）—— 不进
 *    localStorage，避免 XSS 直接盗用（spec §5.2 + R7）。
 * 3. request.ts 请求拦截器自动从 `getUserToken()` 读取 access_token
 *    注入 Authorization 头；401 时由 request.ts refresh 拦截器自动
 *    调 /auth/refresh 旋转并重发原请求。
 * 4. 写入 authStorage 并跳转到 redirect（默认 /llm）。
 *
 * 错误处理：401（密码错误）等由统一请求层全局拦截器提示；
 * 401 触发跳转的情况下，request.ts 拦截器会跳过登录页本身（避免反复跳）。
 */
import { computed, reactive, ref } from 'vue'
import type { FormInstance, FormRules } from 'element-plus'
import { Lock, User } from '@element-plus/icons-vue'
import { useRoute, useRouter } from 'vue-router'

import { login as loginAction } from '@/composables/useAuth'
import { useRedirectTarget } from '@/composables/useRedirectTarget'
import { notifyError, notifySuccess } from '@/utils/notify'

const route = useRoute()
const router = useRouter()
const { redirect: redirectTarget, redirectQuery } = useRedirectTarget()

const formRef = ref<FormInstance>()
const submitting = ref(false)

const form = reactive({
  email: '',
  password: '',
})

const rules: FormRules = {
  email: [
    { required: true, message: '请输入邮箱', trigger: 'blur' },
    { type: 'email', message: '邮箱格式不正确', trigger: 'blur' },
  ],
  password: [{ required: true, message: '请输入密码', trigger: 'blur' }],
}

/** 会话过期提示：守卫触发 /login?reason=expired 时显示 */
const reasonMessage = computed((): string => {
  const reason = route.query.reason
  return reason === 'expired' ? '会话已失效，请重新登录' : ''
})

async function handleSubmit(): Promise<void> {
  if (!formRef.value) return
  try {
    await formRef.value.validate()
  } catch {
    return
  }
  submitting.value = true
  try {
    await loginAction(form.email, form.password)
    notifySuccess('登录成功')
    await router.replace(redirectTarget.value)
  } catch (error: unknown) {
    const message =
      error instanceof Error && error.message ? error.message : '登录失败'
    notifyError(message)
  } finally {
    submitting.value = false
  }
}
</script>

<template>
  <div class="auth-page">
    <!-- 左栏：品牌区 -->
    <aside class="auth-page__brand">
      <div class="auth-page__brand-inner">
        <span class="auth-page__brand-mark" aria-hidden="true">A</span>
        <h1 class="auth-page__brand-title">Agent Web</h1>
        <span class="auth-page__brand-underline" aria-hidden="true" />
        <p class="auth-page__brand-subtitle">AI 智能体平台</p>
        <p class="auth-page__brand-slogan">为开发者打造的 AI Agent 一站式管理平台</p>
      </div>
    </aside>

    <!-- 右栏：登录卡片 -->
    <main class="auth-page__main">
      <section class="auth-card" aria-labelledby="auth-login-title">
        <header class="auth-card__header">
          <h2 id="auth-login-title" class="auth-card__title">Agent Web 登录</h2>
          <p class="auth-card__desc">登录后用户 access token 注入所有受保护资源请求。</p>
          <p v-if="reasonMessage" class="auth-card__notice">{{ reasonMessage }}</p>
        </header>

        <el-form
          ref="formRef"
          :model="form"
          :rules="rules"
          label-width="auto"
          class="auth-card__form"
          @submit.prevent="handleSubmit"
        >
          <el-form-item label="邮箱" prop="email">
            <el-input
              v-model="form.email"
              type="email"
              placeholder="请输入邮箱"
              autocomplete="username"
            >
              <template #prefix><el-icon><User /></el-icon></template>
            </el-input>
          </el-form-item>
          <el-form-item label="密码" prop="password">
            <el-input
              v-model="form.password"
              type="password"
              placeholder="请输入密码"
              show-password
              autocomplete="current-password"
              @keyup.enter="handleSubmit"
            >
              <template #prefix><el-icon><Lock /></el-icon></template>
            </el-input>
          </el-form-item>
          <el-form-item>
            <el-button
              class="auth-card__submit"
              :loading="submitting"
              native-type="submit"
              @click="handleSubmit"
            >
              登录
            </el-button>
          </el-form-item>
        </el-form>

        <div class="auth-card__footer">
          还没有账号？
          <router-link :to="{ name: 'register', query: redirectQuery }">
            立即注册
          </router-link>
        </div>
      </section>
    </main>
  </div>
</template>

<style scoped>
.auth-page {
  display: grid;
  grid-template-columns: 1fr 1fr;
  min-height: 100vh;
  background: var(--color-bg-canvas);
}

/* 左栏品牌区 */
.auth-page__brand {
  display: grid;
  place-items: center;
  background: var(--color-bg-canvas);
  padding: 24px;
}
.auth-page__brand-inner {
  display: flex;
  flex-direction: column;
  align-items: center;
  gap: 12px;
  text-align: center;
}
.auth-page__brand-mark {
  display: grid;
  place-items: center;
  width: 56px;
  height: 56px;
  border-radius: 50%;
  background: linear-gradient(
    135deg,
    var(--color-primary-500),
    var(--color-accent-500)
  );
  color: var(--color-bg-surface);
  font-family: var(--app-font-display);
  font-size: 24px;
  font-weight: 700;
  box-shadow: 0 6px 18px rgba(15, 23, 42, 0.12);
}
.auth-page__brand-title {
  margin: 0;
  color: var(--color-text-primary);
  font-family: var(--app-font-display);
  font-size: 32px;
  font-weight: 700;
  letter-spacing: 0.02em;
}
.auth-page__brand-underline {
  display: block;
  width: 56px;
  height: 3px;
  background: var(--color-primary-500);
  border-radius: 2px;
}
.auth-page__brand-subtitle {
  margin: 0;
  color: var(--color-text-secondary);
  font-size: 14px;
}
.auth-page__brand-slogan {
  margin: 16px 0 0 0;
  color: var(--color-text-secondary);
  opacity: 0.7;
  font-size: 13px;
}

/* 右栏：表单卡片居中 */
.auth-page__main {
  display: grid;
  place-items: center;
  padding: 24px;
}
.auth-card {
  width: min(420px, 100%);
  background: var(--color-bg-surface);
  border: 1px solid var(--color-border-default);
  border-radius: var(--radius-lg);
  padding: 32px;
  box-shadow: var(--shadow-card, 0 2px 8px rgba(15, 23, 42, 0.06));
}
.auth-card__header {
  display: flex;
  flex-direction: column;
  gap: 6px;
}
.auth-card__title {
  margin: 0;
  color: var(--color-text-primary);
  font-family: var(--app-font-display);
  font-size: 20px;
  font-weight: 600;
}
.auth-card__desc {
  margin: 0;
  color: var(--color-text-secondary);
  font-size: 13px;
}
.auth-card__notice {
  margin: 4px 0 0 0;
  font-size: 13px;
  color: var(--color-warning-600);
}
.auth-card__form {
  margin-top: 24px;
}
.auth-card__submit {
  width: 100%;
  height: 40px;
  background: var(--color-primary-500);
  border-color: var(--color-primary-500);
  color: var(--color-bg-surface);
  font-weight: 600;
}
.auth-card__submit:hover {
  background: var(--color-primary-600);
  border-color: var(--color-primary-600);
}
.auth-card__footer {
  margin-top: 16px;
  text-align: center;
  font-size: 13px;
  color: var(--color-text-secondary);
}
.auth-card__footer a {
  color: var(--color-primary-500);
  text-decoration: none;
  margin-left: 4px;
}
.auth-card__footer a:hover {
  color: var(--color-primary-600);
  text-decoration: underline;
}

/* 字段前缀图标样式收敛 */
.auth-card :deep(.el-input__prefix-inner .el-icon) {
  color: var(--color-text-secondary);
}
.auth-card :deep(.el-form-item__label) {
  color: var(--color-text-secondary);
  font-size: 13px;
}

/* 响应式：< 768px 单栏 */
@media (max-width: 767.98px) {
  .auth-page {
    grid-template-columns: 1fr;
    grid-template-rows: auto 1fr;
  }
  .auth-page__brand {
    padding: 24px;
  }
  .auth-page__brand-inner {
    flex-direction: row;
    flex-wrap: wrap;
    justify-content: center;
    gap: 12px;
  }
  .auth-page__brand-mark {
    width: 40px;
    height: 40px;
    font-size: 18px;
  }
  .auth-page__brand-title {
    font-size: 24px;
  }
  .auth-page__brand-underline {
    order: 99;
  }
  .auth-page__brand-slogan {
    display: none;
  }
}
@media (max-width: 479.98px) {
  .auth-card {
    padding: 24px;
  }
}
</style>
