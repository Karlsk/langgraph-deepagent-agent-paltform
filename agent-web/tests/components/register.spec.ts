// @vitest-environment happy-dom
/**
 * Register.vue 组件最小覆盖（task-024）：
 * - 渲染品牌区（"Agent Web" 标题 + 蓝色短下划线）
 * - 渲染卡片 + 4 字段（email / username / password / confirmPassword）+ 主按钮
 * - 密码不一致：ElForm.validate 失败 → 不调 register（密码一致性由 Element Plus
 *   的 confirmPassword 自定义 validator 完成，stub 通过读取 DOM input 值来模拟）
 * - 密码强度不足：validatePasswordStrength 拦截，不发请求
 * - 提交成功：register mock resolve → notifySuccess → 跳 /login?redirect=...
 * - 提交失败：register mock reject → notifyError + 停留
 */
import { beforeEach, describe, expect, it, vi } from 'vitest'
import { defineComponent, h } from 'vue'
import { RouterLink, createMemoryHistory, createRouter } from 'vue-router'
import { flushPromises, mount } from '@vue/test-utils'
import type { VueWrapper } from '@vue/test-utils'

import Register from '@/views/auth/Register.vue'

/** element-plus mock：ElMessage 既可作为函数调用（notify.ts），也作为对象方法（统一拦截器） */
const { elMessageMock } = vi.hoisted(() => {
  const fn = vi.fn()
  return {
    elMessageMock: Object.assign(fn, {
      error: vi.fn(),
      success: vi.fn(),
      warning: vi.fn(),
    }),
  }
})

vi.mock('element-plus', () => ({
  ElMessage: elMessageMock,
}))

/** register API mock */
const registerMock = vi.fn()
vi.mock('@/api/auth', () => ({
  register: (payload: unknown) => registerMock(payload),
}))

/** validatePasswordStrength mock：null 表示通过；非 null 表示具体文案 */
const validatePasswordStrengthMock = vi.fn()
vi.mock('@/utils/password', () => ({
  validatePasswordStrength: (pwd: string) => validatePasswordStrengthMock(pwd),
}))

const elMessageFn = elMessageMock

/**
 * 表单校验钩子：在 ElFormStub 的 validate 被调用时，本钩子读取 stub
 * 暴露的 input value，按 Element Plus 行为给出结果（reject 当校验失败）。
 *
 * 钩子签名：async () => Promise<true | never>
 *
 * 由测试在挂载前赋值；可基于场景返回 reject 模拟"密码不一致"等失败场景。
 */
let formValidateMock: ReturnType<typeof vi.fn>

const ElFormStub = defineComponent({
  name: 'ElForm',
  setup(_, { expose, slots }) {
    expose({
      validate: () => formValidateMock(),
      clearValidate: () => undefined,
    })
    return () => h('form', { class: 'el-form-stub' }, slots.default?.())
  },
})

/** ElFormItem stub：保留 prop 透传，便于按字段断言 */
const ElFormItemStub = defineComponent({
  name: 'ElFormItem',
  props: { label: String, prop: String },
  setup(props, { slots }) {
    return () =>
      h('div', { class: 'el-form-item-stub', 'data-prop': props.prop }, slots.default?.())
  },
})

const ElInputStub = defineComponent({
  name: 'ElInput',
  props: {
    modelValue: { type: [String, Number], default: '' },
    placeholder: String,
    type: String,
    showPassword: Boolean,
    autocomplete: String,
  },
  emits: ['update:modelValue'],
  setup(props, { emit }) {
    return () =>
      h('input', {
        class: 'el-input-stub',
        placeholder: props.placeholder,
        type: props.type === 'password' ? 'password' : 'text',
        value: props.modelValue ?? '',
        onInput: (event: Event) =>
          emit('update:modelValue', (event.target as HTMLInputElement).value),
      })
  },
})

const ElButtonStub = defineComponent({
  name: 'ElButton',
  props: { loading: Boolean },
  emits: ['click'],
  setup(_, { emit, slots, attrs }) {
    return () =>
      h(
        'button',
        {
          class: 'el-button-stub',
          'data-loading': String((attrs as { loading?: boolean }).loading ?? false),
          onClick: () => emit('click'),
        },
        slots.default?.(),
      )
  },
})

const ElIconStub = defineComponent({
  name: 'ElIcon',
  setup(_, { slots }) {
    return () => h('i', { class: 'el-icon-stub' }, slots.default?.())
  },
})

function makeRouter() {
  return createRouter({
    history: createMemoryHistory(),
    routes: [
      { path: '/login', name: 'login', component: { template: '<div />' } },
      { path: '/register', name: 'register', component: { template: '<div />' } },
      { path: '/llm', name: 'llm', component: { template: '<div />' } },
      { path: '/agent', name: 'agent', component: { template: '<div />' } },
    ],
  })
}

async function mountRegister(
  initialRoute: { name: string; query?: Record<string, string> } = { name: 'register' },
): Promise<{ wrapper: VueWrapper; router: ReturnType<typeof makeRouter> }> {
  const router = makeRouter()
  await router.push(initialRoute)
  await router.isReady()
  const wrapper = mount(Register, {
    global: {
      plugins: [router],
      stubs: {
        ElForm: ElFormStub,
        ElFormItem: ElFormItemStub,
        ElInput: ElInputStub,
        ElButton: ElButtonStub,
        ElIcon: ElIconStub,
        'router-link': RouterLink,
      },
    },
  })
  await flushPromises()
  return { wrapper, router }
}

function findButton(wrapper: VueWrapper, text: string) {
  const button = wrapper
    .findAll('button')
    .find((item) => item.text().includes(text))
  if (!button) {
    throw new Error(`button "${text}" not found`)
  }
  return button
}

/**
 * 默认 validate：通过。两份密码字段是否一致由 Element Plus 真实
 * 表单的 confirmPassword 自定义 validator 完成；ElFormStub.validate
 * 由测试按场景覆写为 reject 即可。
 */
beforeEach(() => {
  formValidateMock = vi.fn().mockResolvedValue(true)
  elMessageFn.mockReset()
  elMessageMock.error.mockReset()
  elMessageMock.success.mockReset()
  elMessageMock.warning.mockReset()
  registerMock.mockReset()
  registerMock.mockResolvedValue({
    id: 1,
    email: 'new@example.com',
    username: null,
    token: {
      access_token: 't',
      token_type: 'bearer',
      expires_at: '2099-01-01',
    },
  })
  validatePasswordStrengthMock.mockReset()
  validatePasswordStrengthMock.mockReturnValue(null)
})

describe('Register.vue 注册页（task-024）', () => {
  it('渲染左栏品牌区："Agent Web" 标题 + 蓝色短下划线元素', async () => {
    const { wrapper } = await mountRegister()

    expect(wrapper.text()).toContain('Agent Web')
    expect(wrapper.find('.auth-page__brand-underline').exists()).toBe(true)
  })

  it('渲染右栏卡片：4 字段（email / username / password / confirmPassword）+ 主按钮 "注册"', async () => {
    const { wrapper } = await mountRegister()

    expect(wrapper.text()).toContain('Agent Web 注册')
    expect(wrapper.find('input[placeholder="请输入邮箱"]').exists()).toBe(true)
    expect(wrapper.find('input[placeholder="请输入用户名（可选）"]').exists()).toBe(true)
    expect(wrapper.find('input[placeholder="请输入密码"]').exists()).toBe(true)
    expect(wrapper.find('input[placeholder="请再次输入密码"]').exists()).toBe(true)
    expect(wrapper.findAll('button').some((b) => b.text().includes('注册'))).toBe(true)
  })

  it('密码不一致：ElForm.validate 失败 → 不调 register', async () => {
    // 模拟 Element Plus 真实行为：confirmPassword 不一致时 form validate 返回 reject
    formValidateMock = vi.fn().mockRejectedValue(new Error('两次输入的密码不一致'))
    const { wrapper } = await mountRegister()

    await wrapper.find('input[placeholder="请输入邮箱"]').setValue('user@example.com')
    await wrapper.find('input[placeholder="请输入密码"]').setValue('Ab1!xyzwa')
    await wrapper.find('input[placeholder="请再次输入密码"]').setValue('different-pw')
    await findButton(wrapper, '注册').trigger('click')
    await flushPromises()

    expect(formValidateMock).toHaveBeenCalled()
    expect(registerMock).not.toHaveBeenCalled()
    expect(elMessageFn).not.toHaveBeenCalled()
  })

  it('密码强度不足：validatePasswordStrength 返回错误 → notifyError + 不发请求', async () => {
    validatePasswordStrengthMock.mockReturnValue('密码至少 8 位')
    const { wrapper } = await mountRegister()

    await wrapper.find('input[placeholder="请输入邮箱"]').setValue('user@example.com')
    await wrapper.find('input[placeholder="请输入密码"]').setValue('Aa1!')
    await wrapper.find('input[placeholder="请再次输入密码"]').setValue('Aa1!')
    await findButton(wrapper, '注册').trigger('click')
    await flushPromises()

    expect(validatePasswordStrengthMock).toHaveBeenCalledWith('Aa1!')
    expect(elMessageFn).toHaveBeenCalledWith(
      expect.objectContaining({ type: 'error', message: '密码至少 8 位' }),
    )
    expect(registerMock).not.toHaveBeenCalled()
  })

  it('提交成功：register mock resolve → notifySuccess → 跳 /login?redirect=/agent', async () => {
    const { wrapper, router } = await mountRegister({
      name: 'register',
      query: { redirect: '/agent' },
    })

    await wrapper.find('input[placeholder="请输入邮箱"]').setValue('new@example.com')
    await wrapper.find('input[placeholder="请输入用户名（可选）"]').setValue('new-user')
    await wrapper.find('input[placeholder="请输入密码"]').setValue('Ab1!xyzwa')
    await wrapper.find('input[placeholder="请再次输入密码"]').setValue('Ab1!xyzwa')
    await findButton(wrapper, '注册').trigger('click')
    await flushPromises()

    expect(registerMock).toHaveBeenCalledWith({
      email: 'new@example.com',
      password: 'Ab1!xyzwa',
      username: 'new-user',
    })
    expect(elMessageFn).toHaveBeenCalledWith(
      expect.objectContaining({ type: 'success', message: '注册成功，请登录' }),
    )
    expect(router.currentRoute.value.name).toBe('login')
    expect(router.currentRoute.value.query.redirect).toBe('/agent')
  })

  it('提交失败：register mock reject → notifyError + 停留注册页', async () => {
    registerMock.mockRejectedValueOnce(new Error('Email already registered'))
    const { wrapper, router } = await mountRegister({
      name: 'register',
      query: { redirect: '/agent' },
    })

    await wrapper.find('input[placeholder="请输入邮箱"]').setValue('dup@example.com')
    await wrapper.find('input[placeholder="请输入密码"]').setValue('Ab1!xyzwa')
    await wrapper.find('input[placeholder="请再次输入密码"]').setValue('Ab1!xyzwa')
    await findButton(wrapper, '注册').trigger('click')
    await flushPromises()

    expect(registerMock).toHaveBeenCalledTimes(1)
    expect(elMessageFn).toHaveBeenCalledWith(
      expect.objectContaining({ type: 'error', message: 'Email already registered' }),
    )
    expect(router.currentRoute.value.name).toBe('register')
  })

  it('未传 redirect 时：跳 /login 后 query.redirect 兜底为 /llm', async () => {
    const { wrapper, router } = await mountRegister({ name: 'register' })

    await wrapper.find('input[placeholder="请输入邮箱"]').setValue('new@example.com')
    await wrapper.find('input[placeholder="请输入密码"]').setValue('Ab1!xyzwa')
    await wrapper.find('input[placeholder="请再次输入密码"]').setValue('Ab1!xyzwa')
    await findButton(wrapper, '注册').trigger('click')
    await flushPromises()

    expect(router.currentRoute.value.name).toBe('login')
    expect(router.currentRoute.value.query.redirect).toBe('/llm')
  })
})
