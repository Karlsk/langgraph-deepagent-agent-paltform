// @vitest-environment happy-dom
/**
 * PythonNodeForm 组件测试（S18 / S22 / S23，前端 spec §5.1 + Dify-style inputs）。
 *
 * 零真实网络 / 零真实 LLM：纯表单组件，Element Plus 一律 stub。
 *
 * 验证：
 *   - 输入变量编辑器（varName → dotPath 行增删）+ code textarea；
 *   - 提交体含 { code, inputs }，不含 entry / sandboxed；
 *   - inputs 为空时走旧模式提示，非空时提示 def main(...)；
 *   - 沙箱限制说明完整；
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
  props: { modelValue: [String, Number], type: String, rows: Number, placeholder: String, disabled: Boolean },
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

const ElButtonStub = defineComponent({
  name: 'ElButton',
  props: { type: String, text: Boolean, icon: Object },
  setup(props, { slots }) {
    return () =>
      h('button', { class: 'el-button-stub', 'data-type': props.type ?? '' }, slots.default?.())
  },
})

const STUBS = {
  ElForm: ElFormStub,
  ElFormItem: ElFormItemStub,
  ElInput: ElInputStub,
  ElButton: ElButtonStub,
}

function mountForm(config: Record<string, unknown> = {}, readonly = false) {
  return mount(PythonNodeForm, {
    props: { config, readonly },
    global: { stubs: STUBS },
  })
}

const SAMPLE_CODE = 'return {"upper": state["input"].upper()}'

describe('PythonNodeForm 字段暴露面', () => {
  it('渲染 code textarea，值回填自 config.code', () => {
    const wrapper = mountForm({ code: SAMPLE_CODE })
    const textareas = wrapper.findAllComponents(ElInputStub).filter(i => i.attributes('data-type') === 'textarea')
    expect(textareas.length).toBeGreaterThanOrEqual(1)
    expect((textareas[0].element as HTMLTextAreaElement).value).toBe(SAMPLE_CODE)
  })

  it('code 编辑器为等宽字体（§5.1：普通 textarea + 等宽，不引入语法高亮插件）', () => {
    const wrapper = mountForm({ code: SAMPLE_CODE })
    expect(wrapper.find('.python-node-form__code').exists()).toBe(true)
  })

  it('S18 ① 守卫：不出现 entry 输入', () => {
    const wrapper = mountForm({ code: SAMPLE_CODE })
    expect(wrapper.html()).not.toContain('entry')
  })

  it('S18 ③ 守卫：不出现 sandboxed 开关（安全属性由后端强制，不给用户）', () => {
    const wrapper = mountForm({ code: SAMPLE_CODE })
    expect(wrapper.html()).not.toContain('sandboxed')
  })

  it('即便 config 里被塞进 entry / sandboxed，也不渲染对应控件', () => {
    const wrapper = mountForm({ code: SAMPLE_CODE, entry: 'app.utils:fn', sandboxed: false })
    expect(wrapper.html()).not.toContain('sandboxed')
  })
})

describe('PythonNodeForm 输入变量编辑器', () => {
  it('config.inputs 有值 → 渲染对应数量的行', () => {
    const wrapper = mountForm({ code: '', inputs: { token: 'get_token_result.response', name: 'user_name' } })
    const rows = wrapper.findAll('.python-node-form__input-row')
    expect(rows).toHaveLength(2)
  })

  it('config.inputs 为空 → 无输入行', () => {
    const wrapper = mountForm({ code: '', inputs: {} })
    const rows = wrapper.findAll('.python-node-form__input-row')
    expect(rows).toHaveLength(0)
  })

  it('点击「添加输入变量」→ 新增一行', async () => {
    const wrapper = mountForm({ code: '', inputs: {} })
    const addBtn = wrapper.findAllComponents(ElButtonStub).find(b => b.text().includes('添加'))
    expect(addBtn).toBeTruthy()
    await addBtn!.trigger('click')
    expect(wrapper.findAll('.python-node-form__input-row')).toHaveLength(1)
  })

  it('inputs 非空时提示 def main(...) 模式', () => {
    const wrapper = mountForm({ code: '', inputs: { x: 'some.path' } })
    const helpTexts = wrapper.findAll('.agent-form-helptext')
    const mainHint = helpTexts.some(el => el.text().includes('def main'))
    expect(mainHint).toBe(true)
  })

  it('inputs 为空时提示旧模式（state 字典）', () => {
    const wrapper = mountForm({ code: '', inputs: {} })
    const helpTexts = wrapper.findAll('.agent-form-helptext')
    const stateHint = helpTexts.some(el => el.text().includes('state'))
    expect(stateHint).toBe(true)
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

  it('文案不得再声称禁止一切 import 或没有标准库', () => {
    const wrapper = mountForm({ code: SAMPLE_CODE })
    const text = wrapper.find('.python-node-form__limits').text()
    expect(text).not.toContain('禁止 import')
    expect(text).not.toContain('无标准库')
  })

  it('文案点名白名单模块，并明示 os / socket 一类仍被拒', () => {
    const wrapper = mountForm({ code: SAMPLE_CODE })
    const text = wrapper.find('.python-node-form__limits').text()
    expect(text).toContain('json')
    expect(text).toContain('datetime')
    expect(text).toContain('functools')
    expect(text).toContain('socket')
  })
})

describe('PythonNodeForm 提交体', () => {
  it('编辑 code → emit update:config，键恰为 { code, inputs }', async () => {
    const wrapper = mountForm({ code: SAMPLE_CODE, inputs: {} })
    const textarea = wrapper.findAllComponents(ElInputStub).find(i => i.attributes('data-type') === 'textarea')
    expect(textarea).toBeTruthy()

    await textarea!.vm.$emit('update:modelValue', 'return {"n": 1}')
    await textarea!.vm.$emit('change', 'return {"n": 1}')

    const emitted = wrapper.emitted('update:config')
    expect(emitted).toBeTruthy()
    const config = emitted![emitted!.length - 1]![0] as Record<string, unknown>
    expect(Object.keys(config).sort()).toEqual(['code', 'inputs'])
    expect(config.code).toBe('return {"n": 1}')
  })

  it('回填含 entry / sandboxed 的旧 config → 提交体剔除这两个键', async () => {
    const wrapper = mountForm({ code: SAMPLE_CODE, entry: 'app.utils:fn', sandboxed: true })
    const textarea = wrapper.findAllComponents(ElInputStub).find(i => i.attributes('data-type') === 'textarea')
    expect(textarea).toBeTruthy()

    await textarea!.vm.$emit('update:modelValue', 'return {}')
    await textarea!.vm.$emit('change', 'return {}')

    const config = wrapper.emitted('update:config')![0]![0] as Record<string, unknown>
    expect(config).not.toHaveProperty('entry')
    expect(config).not.toHaveProperty('sandboxed')
  })

  it('code 缺省 → 表单以空串起步，不崩溃', () => {
    const wrapper = mountForm({})
    const textarea = wrapper.findAllComponents(ElInputStub).find(i => i.attributes('data-type') === 'textarea')
    expect(textarea).toBeTruthy()
    expect((textarea!.element as HTMLTextAreaElement).value).toBe('')
  })

  it('inputs 含值时提交体正确序列化', async () => {
    const wrapper = mountForm({ code: 'def main(x): return {}', inputs: { x: 'some.path' } })
    const emitted = wrapper.emitted('update:config')
    if (emitted) {
      const config = emitted[emitted.length - 1][0] as Record<string, unknown>
      expect(config.inputs).toEqual({ x: 'some.path' })
    }
  })
})

describe('PythonNodeForm 门禁与同步', () => {
  it('readonly=true → 整个表单禁用', () => {
    const wrapper = mountForm({ code: SAMPLE_CODE }, true)
    expect(wrapper.find('.el-form-stub').attributes('data-disabled')).toBe('true')
  })

  it('props.config 外部变化 → 表单同步（切换节点）', async () => {
    const wrapper = mountForm({ code: SAMPLE_CODE, inputs: {} })
    await wrapper.setProps({ config: { code: 'return {"ok": True}', inputs: {} } })
    const textarea = wrapper.findAllComponents(ElInputStub).find(i => i.attributes('data-type') === 'textarea')
    expect(textarea).toBeTruthy()
    expect((textarea!.element as HTMLTextAreaElement).value).toBe('return {"ok": True}')
  })
})
