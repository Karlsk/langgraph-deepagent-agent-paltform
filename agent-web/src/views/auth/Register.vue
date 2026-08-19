<script setup lang="ts">
/**
 * 注册页（task-024）：
 * 左品牌 + 右卡片两栏式；4 字段表单（email / username / password /
 * confirmPassword）+ 客户端密码强度预校验 + confirmPassword 一致性校验。
 *
 * 提交流程：
 * 1. ElForm validate（Element Plus 内置规则：email 格式 + 必填）
 * 2. validatePasswordStrength 前端预校验（避免 422 往返）
 * 3. POST /auth/register
 * 4. 成功 → 跳 /login?redirect=...（手动登录，不调 createSession）
 * 5. 失败 → notifyError + 停留
 */
import { reactive, ref } from 'vue'
import type { FormInstance, FormRules } from 'element-plus'
import { Lock, Message, User } from '@element-plus/icons-vue'
import { useRouter } from 'vue-router'

import { register } from '@/api/auth'
import { useRedirectTarget } from '@/composables/useRedirectTarget'
import { notifyError, notifySuccess } from '@/utils/notify'
import { validatePasswordStrength } from '@/utils/password'

const router = useRouter()
const { redirectQuery } = useRedirectTarget()

const formRef = ref<FormInstance>()
const submitting = ref(false)

const form = reactive({
  email: '',
  username: '',
  password: '',
  confirmPassword: '',
})

/** 二次密码确认：与 password 字段一致（自定义 validator，提交时由 ElForm 调用） */
const validateConfirmPassword = (
  _rule: unknown,
  value: string,
  callback: (error?: Error) => void,
): void => {
  if (value === '' || value === form.password) {
    callback()
  } else {
    callback(new Error('两次输入的密码不一致'))
  }
}

const rules: FormRules = {
  email: [
    { required: true, message: '请输入邮箱', trigger: 'blur' },
    { type: 'email', message: '邮箱格式不正确', trigger: 'blur' },
  ],
  username: [
    { max: 50, message: '用户名不能超过 50 字符', trigger: 'blur' },
  ],
  password: [
    { required: true, message: '请输入密码', trigger: 'blur' },
  ],
  confirmPassword: [
    { required: true, message: '请再次输入密码', trigger: 'blur' },
    { validator: validateConfirmPassword, trigger: 'blur' },
  ],
}

async function handleSubmit(): Promise<void> {
  if (!formRef.value) return
  try {
    await formRef.value.validate()
  } catch {
    return
  }
  // 前端密码强度预校验：避免 422 往返；同时响应"密码至少 8 位"等友好文案
  const pwErr = validatePasswordStrength(form.password)
  if (pwErr !== null) {
    notifyError(pwErr)
    return
  }
  submitting.value = true
  try {
    await register({
      email: form.email,
      password: form.password,
      username: form.username || null,
    })
    notifySuccess('注册成功，请登录')
    await router.replace({ name: 'login', query: redirectQuery.value })
  } catch (error: unknown) {
    const message =
      error instanceof Error && error.message ? error.message : '注册失败'
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

    <!-- 右栏：注册表单卡片 -->
    <main class="auth-page__main">
      <section class="auth-card" aria-labelledby="auth-register-title">
        <header class="auth-card__header">
          <h2 id="auth-register-title" class="auth-card__title">Agent Web 注册</h2>
          <p class="auth-card__desc">注册账号以访问完整功能。</p>
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
              autocomplete="email"
            >
              <template #prefix><el-icon><Message /></el-icon></template>
            </el-input>
          </el-form-item>
          <el-form-item label="用户名（可选）" prop="username">
            <el-input
              v-model="form.username"
              placeholder="请输入用户名（可选）"
              maxlength="50"
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
              autocomplete="new-password"
              @keyup.enter="handleSubmit"
            >
              <template #prefix><el-icon><Lock /></el-icon></template>
            </el-input>
          </el-form-item>
          <el-form-item label="确认密码" prop="confirmPassword">
            <el-input
              v-model="form.confirmPassword"
              type="password"
              placeholder="请再次输入密码"
              show-password
              autocomplete="new-password"
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
              注册
            </el-button>
          </el-form-item>
        </el-form>

        <div class="auth-card__footer">
          已有账号？
          <router-link :to="{ name: 'login', query: redirectQuery }">
            立即登录
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
  margin-top: 4px;
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

/* 响应式：< 768px 单栏，左栏收缩到顶部 */
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
