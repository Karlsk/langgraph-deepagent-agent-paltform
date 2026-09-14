// @vitest-environment happy-dom
/**
 * PythonNodeForm 组件测试（S18 / S22，前端 spec §5.1）。
 *
 * 零真实网络 / 零真实 LLM：纯表单组件，Element Plus 一律 stub。
 *
 * 验证：
 *   - 只暴露 `code` 一个可编辑字段（等宽 textarea）；
 *   - **绝不**出现 `entry` 输入与 `sandboxed` 开关（S18 ①③：entry 无法沙箱化、
 *     sandboxed 是安全属性由后端强制），提交体也不含这两个键；
 *   - 面板明示沙箱限制（无 import / 无文件与网络 / 须 return dict / 超时与内存有上限）；
 *   - 前端不做 eval / 预览执行 / 语法高亮（§5.1）；
 *   - readonly 门禁与 props.config 外部变化同步。
 */
import { describe, expect, it } from 'vitest'
import { defineComponent, h } from 'vue'
import { mount } from '@vue/test-utils'

import PythonNodeForm from '@/views/workflow/panel/PythonNodeForm.vue'

const ElFormStub = defineComponent({
  name: 'ElForm',
  props: { labelPosition: String, disabled: { type: Boolean, default: false } },
  setup(props, { slots }) {
    return () => h('div', { class: 'el-form-stub', 'data-disabled': String(props.disabled) }, slots.default?.())
  },
})

const ElFormItemStub = defineComponent({
  name: 'ElFormItem',
  props: { label: String, prop: String },
  setup(props, { slots }) {
    return () => h('div', { class: 'el-form-item-stub', 'data-label': props.label ?? '' }, slots.default?.())
  },
})

const ElInputStub = defineComponent({
  name: 'ElInput',
  props: { modelValue: String, type: String, rows: Number, placeholder: String, disabled: Boolean },
  emits: ['update:modelValue', 'change'],
  setup(props) {
    return () =>
      h('textarea', {
        class: 'el-input-stub',
        'data-type': props.type ?? 'text',
        'data-rows': String(props.rows ?? ''),
        value: props.modelValue ?? '',
        placeholder: props.placeholder ?? '',
      })
  },
})

const ElSwitchStub = defineComponent({
  name: 'ElSwitch',
  props: { modelValue: Boolean },
  emits: ['update:modelValue', 'change'],
  setup(props) {
    return () => h('div', { class: 'el-switch-stub', 'data-value': String(props.modelValue) })
  },
})

const STUBS = {
  ElForm: ElFormStub,
  ElFormItem: ElFormItemStub,
  ElInput: ElInputStub,
  ElSwitch: ElSwitchStub,
}

function mountForm(config: Record<string, unknown> = {}, readonly = false) {
  return mount(PythonNodeForm, {
    props: { config, readonly },
    global: { stubs: STUBS },
  })
}

const SAMPLE_CODE = 'return {"upper": state["input"].upper()}'

describe('PythonNodeForm 字段暴露面', () => {
  it('渲染一个 code textarea，值回填自 config.code', () => {
    const wrapper = mountForm({ code: SAMPLE_CODE })
    const inputs = wrapper.findAllComponents(ElInputStub)
    expect(inputs).toHaveLength(1)
    expect(inputs[0].attributes('data-type')).toBe('textarea')
    expect((inputs[0].element as HTMLTextAreaElement).value).toBe(SAMPLE_CODE)
  })

  it('code 编辑器为等宽字体（§5.1：普通 textarea + 等宽，不引入语法高亮插件）', () => {
    const wrapper = mountForm({ code: SAMPLE_CODE })
    expect(wrapper.find('.python-node-form__code').exists()).toBe(true)
  })

  it('S18 ① 守卫：不出现 entry 输入', () => {
    const wrapper = mountForm({ code: SAMPLE_CODE })
    expect(wrapper.html()).not.toContain('entry')
    expect(wrapper.findAllComponents(ElInputStub)).toHaveLength(1)
  })

  it('S18 ③ 守卫：不出现 sandboxed 开关（安全属性由后端强制，不给用户）', () => {
    const wrapper = mountForm({ code: SAMPLE_CODE })
    expect(wrapper.html()).not.toContain('sandboxed')
    expect(wrapper.findAllComponents(ElSwitchStub)).toHaveLength(0)
  })

  it('即便 config 里被塞进 entry / sandboxed，也不渲染对应控件', () => {
    const wrapper = mountForm({ code: SAMPLE_CODE, entry: 'app.utils:fn', sandboxed: false })
    expect(wrapper.findAllComponents(ElInputStub)).toHaveLength(1)
    expect(wrapper.findAllComponents(ElSwitchStub)).toHaveLength(0)
  })
})

describe('PythonNodeForm 沙箱限制说明', () => {
  it('明示禁止 import、文件与网络访问', () => {
    const wrapper = mountForm({ code: SAMPLE_CODE })
    const text = wrapper.find('.python-node-form__limits').text()
    expect(text).toContain('import')
    expect(text).toContain('文件')
    expect(text).toContain('网络')
  })

  it('明示须 return dict，且超时与内存有上限', () => {
    const wrapper = mountForm({ code: SAMPLE_CODE })
    const text = wrapper.find('.python-node-form__limits').text()
    expect(text).toContain('dict')
    expect(text).toContain('超时')
    expect(text).toContain('内存')
  })

  it('明示校验以后端 422 的行号 + 规则名为准（前端不二次解释）', () => {
    const wrapper = mountForm({ code: SAMPLE_CODE })
    expect(wrapper.find('.python-node-form__limits').text()).toContain('422')
  })

  it('不做前端执行 / 预览：没有运行或校验按钮', () => {
    const wrapper = mountForm({ code: SAMPLE_CODE })
    expect(wrapper.findAll('button')).toHaveLength(0)
  })
})

describe('PythonNodeForm 提交体', () => {
  it('编辑 code → emit update:config，键恰为 { code }', async () => {
    const wrapper = mountForm({ code: SAMPLE_CODE })
    const input = wrapper.findComponent(ElInputStub)

    await input.vm.$emit('update:modelValue', 'return {"n": len(state["items"])}')
    await input.vm.$emit('change', 'return {"n": len(state["items"])}')

    const emitted = wrapper.emitted('update:config')
    expect(emitted).toBeTruthy()
    const config = emitted![emitted!.length - 1]![0] as Record<string, unknown>
    expect(Object.keys(config)).toEqual(['code'])
    expect(config.code).toBe('return {"n": len(state["items"])}')
  })

  it('回填含 entry / sandboxed 的旧 config → 提交体剔除这两个键（§14 验收）', async () => {
    const wrapper = mountForm({ code: SAMPLE_CODE, entry: 'app.utils:fn', sandboxed: true })
    const input = wrapper.findComponent(ElInputStub)

    await input.vm.$emit('update:modelValue', 'return {}')
    await input.vm.$emit('change', 'return {}')

    const config = wrapper.emitted('update:config')![0]![0] as Record<string, unknown>
    expect(config).not.toHaveProperty('entry')
    expect(config).not.toHaveProperty('sandboxed')
  })

  it('code 缺省 → 表单以空串起步，不崩溃', () => {
    const wrapper = mountForm({})
    expect((wrapper.findComponent(ElInputStub).element as HTMLTextAreaElement).value).toBe('')
  })
})

describe('PythonNodeForm 门禁与同步', () => {
  it('readonly=true → 整个表单禁用', () => {
    const wrapper = mountForm({ code: SAMPLE_CODE }, true)
    expect(wrapper.find('.el-form-stub').attributes('data-disabled')).toBe('true')
  })

  it('props.config 外部变化 → 表单同步（切换节点）', async () => {
    const wrapper = mountForm({ code: SAMPLE_CODE })
    await wrapper.setProps({ config: { code: 'return {"ok": True}' } })
    expect((wrapper.findComponent(ElInputStub).element as HTMLTextAreaElement).value).toBe('return {"ok": True}')
  })
})
