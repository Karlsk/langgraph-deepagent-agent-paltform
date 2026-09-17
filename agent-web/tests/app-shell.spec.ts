// @vitest-environment happy-dom
/**
 * App 壳层顶栏测试：
 * - 右上角用户按钮为 el-dropdown：触发器为头像按钮，下拉内展示当前用户
 *   （用户名 / 邮箱）与「注销」项；
 * - 顶栏不再有独立的「注销」按钮（收纳进下拉）；
 * - 点击「注销」→ logout() + 成功提示 + 跳转 login。
 *
 * 零真实渲染：el-* 组件经 global.components 注册为受控 stub；
 * el-dropdown 系 stub 用 provide/inject 把 item 点击冒泡为 dropdown 的
 * command 事件（与 chat-view.spec.ts 同款模式）。
 */
import { beforeEach, describe, expect, it, vi } from 'vitest'
import { defineComponent, h, inject, provide } from 'vue'
import { mount } from '@vue/test-utils'

import App from '@/App.vue'

const { logoutMock, notifySuccessMock, replaceMock } = vi.hoisted(() => ({
  logoutMock: vi.fn(),
  notifySuccessMock: vi.fn(),
  replaceMock: vi.fn(),
}))

const testUser = { id: 1, email: 'demo@example.com', username: 'demo' }
let currentUserValue: typeof testUser | null = testUser

vi.mock('@/composables/useAuth', () => ({
  bootstrap: vi.fn(),
  currentUser: () => currentUserValue,
  logout: (...args: unknown[]) => logoutMock(...args),
}))

vi.mock('@/utils/notify', () => ({
  notifySuccess: (...args: unknown[]) => notifySuccessMock(...args),
}))

const routeMock = { path: '/agent', fullPath: '/agent', meta: { title: 'Agent 管理' } }
vi.mock('vue-router', () => ({
  useRoute: () => routeMock,
  useRouter: () => ({ replace: (...args: unknown[]) => replaceMock(...args) }),
}))

/** 透传 stub：只渲染默认插槽，阻断真实 EP 组件渲染 */
function passThrough(name: string) {
  return defineComponent({
    name,
    inheritAttrs: false,
    setup(_, { slots }) {
      return () => h('div', { class: `${name.toLowerCase()}-stub` }, slots.default?.())
    },
  })
}

/** el-dropdown stub：item 点击经 provide/inject 冒泡 command */
const COMMAND_KEY = Symbol('app-shell-dropdown-command')

const ElDropdownStub = defineComponent({
  name: 'ElDropdown',
  emits: ['command'],
  setup(_, { emit, slots }) {
    provide(COMMAND_KEY, (command: string) => emit('command', command))
    return () =>
      h('div', { class: 'el-dropdown-stub' }, [
        slots.default ? slots.default() : undefined,
        slots.dropdown ? slots.dropdown() : undefined,
      ])
  },
})

const ElDropdownMenuStub = defineComponent({
  name: 'ElDropdownMenu',
  setup(_, { slots }) {
    return () => h('div', { class: 'el-dropdown-menu-stub' }, slots.default?.())
  },
})

const ElDropdownItemStub = defineComponent({
  name: 'ElDropdownItem',
  props: { command: String, disabled: Boolean },
  setup(props, { slots }) {
    const emitCommand = inject<(command: string) => void>(COMMAND_KEY)
    return () =>
      h(
        'button',
        {
          class: 'el-dropdown-item-stub',
          disabled: props.disabled,
          onClick: () => emitCommand?.(props.command ?? ''),
        },
        slots.default?.(),
      )
  },
})

/** ElSubMenu 需同时渲染 #title 与默认插槽 */
const ElSubMenuStub = defineComponent({
  name: 'ElSubMenu',
  setup(_, { slots }) {
    return () =>
      h('div', { class: 'el-sub-menu-stub' }, [
        slots.title ? slots.title() : undefined,
        slots.default ? slots.default() : undefined,
      ])
  },
})

function mountApp() {
  return mount(App, {
    global: {
      components: {
        ElContainer: passThrough('ElContainer'),
        ElAside: passThrough('ElAside'),
        ElHeader: passThrough('ElHeader'),
        ElMain: passThrough('ElMain'),
        ElMenu: passThrough('ElMenu'),
        ElMenuItem: passThrough('ElMenuItem'),
        ElSubMenu: ElSubMenuStub,
        ElIcon: passThrough('ElIcon'),
        ElAvatar: passThrough('ElAvatar'),
        ElBreadcrumb: passThrough('ElBreadcrumb'),
        ElBreadcrumbItem: passThrough('ElBreadcrumbItem'),
        ElDropdown: ElDropdownStub,
        ElDropdownMenu: ElDropdownMenuStub,
        ElDropdownItem: ElDropdownItemStub,
      },
    },
  })
}

beforeEach(() => {
  vi.clearAllMocks()
  currentUserValue = testUser
})

describe('App 壳层：右上角用户下拉', () => {
  it('头像为 el-dropdown 触发器；下拉内展示用户名与邮箱', () => {
    const wrapper = mountApp()

    const trigger = wrapper.find('button[aria-label="用户信息"]')
    expect(trigger.exists()).toBe(true)
    // 触发器包在 el-dropdown stub 内
    expect(trigger.element.parentElement?.classList.contains('el-dropdown-stub')).toBe(true)

    const menu = wrapper.find('.el-dropdown-menu-stub')
    expect(menu.exists()).toBe(true)
    expect(menu.text()).toContain('demo')
    expect(menu.text()).toContain('demo@example.com')
  })

  it('顶栏不再有独立「注销」按钮；注销项只存在于下拉内', () => {
    const wrapper = mountApp()

    const headerActions = wrapper.find('.app-header__actions')
    // 独立注销按钮（旧实现 aria-label="注销"）已移除
    expect(headerActions.find('button[aria-label="注销"]').exists()).toBe(false)

    const logoutItems = wrapper.findAll('button.el-dropdown-item-stub').filter((item) =>
      item.text().includes('注销'),
    )
    expect(logoutItems.length).toBe(1)
  })

  it('点击「注销」：调用 logout + 成功提示 + 跳转 login', async () => {
    const wrapper = mountApp()

    const logoutItem = wrapper
      .findAll('button.el-dropdown-item-stub')
      .find((item) => item.text().includes('注销'))
    expect(logoutItem).toBeDefined()

    await logoutItem!.trigger('click')
    await vi.waitFor(() => {
      expect(logoutMock).toHaveBeenCalledTimes(1)
      expect(notifySuccessMock).toHaveBeenCalledWith('已注销')
      expect(replaceMock).toHaveBeenCalledWith({ name: 'login' })
    })
  })

  it('未登录（currentUser 为 null）：下拉显示「未登录」且注销项禁用', () => {
    currentUserValue = null
    const wrapper = mountApp()

    const menu = wrapper.find('.el-dropdown-menu-stub')
    expect(menu.text()).toContain('未登录')

    const logoutItem = wrapper
      .findAll('button.el-dropdown-item-stub')
      .find((item) => item.text().includes('注销'))
    expect(logoutItem).toBeDefined()
    expect(logoutItem!.attributes('disabled')).toBeDefined()
  })
})
