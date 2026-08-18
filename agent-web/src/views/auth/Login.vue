<script setup lang="ts">
/**
 * 登录页：邮箱 + 密码登录。
 *
 * 流程：
 * 1. /auth/login（form 表单） → 用户 token；
 * 2. /auth/session（Bearer 用户 token） → 会话 token；
 * 3. 写入 authStorage 并跳转到 redirect（默认 /llm）。
 *
 * 错误处理：401（密码错误）等由统一请求层全局拦截器提示；
 * 401 触发跳转的情况下，request.ts 拦截器会跳过登录页本身（避免反复跳）。
 */
import { computed, reactive, ref } from 'vue'
import type { FormInstance, FormRules } from 'element-plus'
import { useRoute, useRouter } from 'vue-router'

import { login as loginAction } from '@/composables/useAuth'
import { notifyError, notifySuccess } from '@/utils/notify'

const route = useRoute()
const router = useRouter()

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
    const redirect = (route.query.redirect as string | undefined) ?? '/llm'
    await router.replace(redirect)
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
    <div class="auth-card">
      <header class="auth-card__header">
        <h1 class="auth-card__title">Agent Web 登录</h1>
        <p class="auth-card__desc">登录后会话 token 注入所有受保护资源请求。</p>
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
          />
        </el-form-item>
        <el-form-item label="密码" prop="password">
          <el-input
            v-model="form.password"
            type="password"
            placeholder="请输入密码"
            show-password
            autocomplete="current-password"
            @keyup.enter="handleSubmit"
          />
        </el-form-item>
        <el-form-item>
          <el-button
            class="app-btn app-btn--primary auth-card__submit"
            :loading="submitting"
            @click="handleSubmit"
          >
            登录
          </el-button>
        </el-form-item>
      </el-form>
    </div>
  </div>
</template>

<style scoped>
.auth-page {
  display: grid;
  place-items: center;
  min-height: 100vh;
  background: var(--color-bg-canvas);
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
.auth-card__title {
  font-size: 20px;
  font-weight: 600;
  color: var(--color-text-primary);
}
.auth-card__desc {
  margin-top: 6px;
  font-size: 13px;
  color: var(--color-text-secondary);
}
.auth-card__notice {
  margin-top: 8px;
  font-size: 13px;
  color: var(--color-warning-600);
}
.auth-card__form {
  margin-top: 24px;
}
.auth-card__submit {
  width: 100%;
}
</style>