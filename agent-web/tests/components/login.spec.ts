// @vitest-environment happy-dom
/**
 * Login.vue 组件最小覆盖（task-024）：
 * - 品牌区渲染（"Agent Web" 标题 + 蓝色短下划线）
 * - 卡片渲染（标题、字段、主按钮）
 * - "去注册"链接存在且 to 指向 /register；redirect query 透传
 * - 登录提交：调 useAuth.login → notifySuccess → 跳 redirect 目标
 * - 登录失败：调 useAuth.login reject → notifyError + 不跳转
 *
 * G1 阶段实现尚不具备品牌区 + "去注册"链接，本测试当前为 RED；
 * G2 重写 Login.vue 后应转 GREEN。
 */
import { beforeEach, describe, expect, it, vi } from 'vitest'
import { defineComponent, h } from 'vue'
import { RouterLink, createMemoryHistory, createRouter } from 'vue-router'
import { flushPromises, mount } from '@vue/test-utils'
import type { VueWrapper } from '@vue/test-utils'

import Login from '@/views/auth/Login.vue'

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

/** useAuth mock：Login.vue 走 `login(email, password)`；仅暴露 login，避免污染 */
const loginMock = vi.fn()
vi.mock('@/composables/useAuth', () => ({
  login: (email: string, password: string) => loginMock(email, password),
}))

const elMessageFn = elMessageMock

/**
 * Element Plus stub：
 * - ElForm 暴露 validate → resolve；真实行为由 happy-dom jsdom 不能提供；
 * - ElInput 透传 placeholder 输出真实 input，便于 setValue；
 * - ElButton 渲染原生 button 透传插槽内容。
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

const ElFormItemStub = defineComponent({
  name: 'ElFormItem',
  props: { label: String, prop: String },
  setup(_, { slots }) {
    return () => h('div', { class: 'el-form-item-stub' }, slots.default?.())
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

/** 创建一个仅含必要路由的孤立 router，供 Login.vue 的 useRouter/useRoute 使用 */
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

async function mountLogin(
  initialRoute: { name: string; query?: Record<string, string> } = { name: 'login' },
): Promise<{ wrapper: VueWrapper; router: ReturnType<typeof makeRouter> }> {
  const router = makeRouter()
  await router.push(initialRoute)
  await router.isReady()
  const wrapper = mount(Login, {
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

beforeEach(() => {
  formValidateMock = vi.fn().mockResolvedValue(true)
  elMessageFn.mockReset()
  elMessageMock.error.mockReset()
  elMessageMock.success.mockReset()
  elMessageMock.warning.mockReset()
  loginMock.mockReset()
  loginMock.mockResolvedValue(undefined)
})

describe('Login.vue 登录页（task-024）', () => {
  it('渲染左栏品牌区：包含 "Agent Web" 标题 + 蓝色短下划线元素', async () => {
    const { wrapper } = await mountLogin()

    const text = wrapper.text()
    expect(text).toContain('Agent Web')

    // 蓝色短下划线：brand underline 装饰元素，必须在登录页两栏式品牌区
    expect(wrapper.find('.auth-page__brand-underline').exists()).toBe(true)
  })

  it('渲染右栏卡片：含 "Agent Web 登录" 标题、邮箱/密码字段、主登录按钮', async () => {
    const { wrapper } = await mountLogin()

    expect(wrapper.text()).toContain('Agent Web 登录')
    // email 字段
    expect(wrapper.find('input[placeholder="请输入邮箱"]').exists()).toBe(true)
    // password 字段
    expect(wrapper.find('input[placeholder="请输入密码"]').exists()).toBe(true)
    // 主按钮
    expect(wrapper.findAll('button').some((b) => b.text().includes('登录'))).toBe(true)
  })

  it('"去注册"链接渲染：to 指向 /register（不含 redirect query 时）', async () => {
    const { wrapper } = await mountLogin({ name: 'login' })

    const links = wrapper.findAllComponents(RouterLink)
    const registerLink = links.find((link) => link.props('to') !== undefined)
    if (!registerLink) throw new Error('no router-link with `to` prop found')

    const to = registerLink.props('to') as { name?: string } | string
    // to 可能是对象 { name: 'register' } 或字符串 '/register'，两者均允许
    if (typeof to === 'object') {
      expect(to.name).toBe('register')
    } else {
      expect(String(to)).toMatch(/^\/register/)
    }
  })

  it('"去注册"链接透传 redirect query：to.query.redirect 等于当前路由的 redirect', async () => {
    const { wrapper } = await mountLogin({
      name: 'login',
      query: { redirect: '/agent' },
    })

    const links = wrapper.findAllComponents(RouterLink)
    const registerLink = links.find((link) => {
      const t = link.props('to') as { name?: string } | string
      return typeof t === 'object' && t.name === 'register'
    })
    expect(registerLink).toBeDefined()
    const to = registerLink!.props('to') as { name: string; query?: Record<string, string> }
    expect(to.name).toBe('register')
    expect(to.query?.redirect).toBe('/agent')
  })

  it('登录提交：调 useAuth.login → notifySuccess → 跳 redirect 目标 /agent', async () => {
    const { wrapper, router } = await mountLogin({
      name: 'login',
      query: { redirect: '/agent' },
    })

    await wrapper.find('input[placeholder="请输入邮箱"]').setValue('user@example.com')
    await wrapper.find('input[placeholder="请输入密码"]').setValue('Ab1!xyzwa')
    await findButton(wrapper, '登录').trigger('click')
    await flushPromises()

    expect(loginMock).toHaveBeenCalledWith('user@example.com', 'Ab1!xyzwa')
    expect(elMessageFn).toHaveBeenCalledWith(
      expect.objectContaining({ type: 'success', message: '登录成功' }),
    )
    expect(router.currentRoute.value.path).toBe('/agent')
  })

  it('登录提交：未传 redirect 时默认跳 /llm', async () => {
    const { wrapper, router } = await mountLogin({ name: 'login' })

    await wrapper.find('input[placeholder="请输入邮箱"]').setValue('user@example.com')
    await wrapper.find('input[placeholder="请输入密码"]').setValue('Ab1!xyzwa')
    await findButton(wrapper, '登录').trigger('click')
    await flushPromises()

    expect(loginMock).toHaveBeenCalledWith('user@example.com', 'Ab1!xyzwa')
    expect(router.currentRoute.value.path).toBe('/llm')
  })

  it('登录失败：useAuth.login reject → notifyError + 不跳转', async () => {
    loginMock.mockRejectedValueOnce(new Error('账号或密码错误'))
    const { wrapper, router } = await mountLogin({ name: 'login' })

    await wrapper.find('input[placeholder="请输入邮箱"]').setValue('user@example.com')
    await wrapper.find('input[placeholder="请输入密码"]').setValue('bad-password')
    await findButton(wrapper, '登录').trigger('click')
    await flushPromises()

    expect(loginMock).toHaveBeenCalledTimes(1)
    expect(elMessageFn).toHaveBeenCalledWith(
      expect.objectContaining({ type: 'error', message: '账号或密码错误' }),
    )
    expect(router.currentRoute.value.name).toBe('login')
  })

  it('表单校验失败：validate reject → 不调 useAuth.login', async () => {
    formValidateMock = vi.fn().mockRejectedValue(new Error('invalid'))
    const { wrapper } = await mountLogin({ name: 'login' })

    await findButton(wrapper, '登录').trigger('click')
    await flushPromises()

    expect(loginMock).not.toHaveBeenCalled()
  })

  it('reason=expired 提示渲染：橙色 secondary 文案显示', async () => {
    const { wrapper } = await mountLogin({
      name: 'login',
      query: { reason: 'expired' },
    })

    expect(wrapper.text()).toContain('会话已失效')
    // expired 提示使用 warning token
    expect(wrapper.find('.auth-card__notice').exists()).toBe(true)
  })
})
