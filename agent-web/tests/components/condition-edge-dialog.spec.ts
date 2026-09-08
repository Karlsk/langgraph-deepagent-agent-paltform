// @vitest-environment happy-dom
import { describe, it, expect, vi } from 'vitest'
import { mount } from '@vue/test-utils'
import ElementPlus from 'element-plus'
import ConditionEdgeDialog from '@/views/workflow/canvas/ConditionEdgeDialog.vue'

vi.mock('element-plus', async () => {
  const actual = await vi.importActual('element-plus')
  return {
    ...actual,
    ElDialog: {
      name: 'ElDialog',
      template: '<div class="el-dialog-stub"><slot /><slot name="footer" /></div>',
      props: ['modelValue', 'title', 'width', 'closeOnClickModal'],
      emits: ['update:modelValue', 'close'],
    },
    ElAlert: {
      name: 'ElAlert',
      template: '<div class="el-alert-stub"><slot /></div>',
      props: ['type', 'closable'],
    },
    ElForm: {
      name: 'ElForm',
      template: '<div class="el-form-stub"><slot /></div>',
      props: ['labelWidth'],
    },
    ElFormItem: {
      name: 'ElFormItem',
      template: '<div class="el-form-item-stub"><slot /></div>',
      props: ['label'],
    },
    ElSelect: {
      name: 'ElSelect',
      template: '<select class="el-select-stub" :value="modelValue" @change="$emit(\'update:modelValue\', ($event.target as HTMLSelectElement).value)"><slot /></select>',
      props: ['modelValue', 'disabled', 'placeholder', 'filterable'],
      emits: ['update:modelValue'],
    },
    ElOption: {
      name: 'ElOption',
      template: '<option class="el-option-stub" :value="value">{{ label }}</option>',
      props: ['label', 'value'],
    },
    ElRadioGroup: {
      name: 'ElRadioGroup',
      template: '<div class="el-radio-group-stub"><slot /></div>',
      props: ['modelValue', 'disabled'],
      emits: ['update:modelValue'],
    },
    ElRadio: {
      name: 'ElRadio',
      template: '<label class="el-radio-stub"><input type="radio" :value="value" :checked="modelValue === value" @change="$emit(\'update:modelValue\', value)" /><slot /></label>',
      props: ['value', 'modelValue', 'disabled'],
      emits: ['update:modelValue'],
    },
    ElInput: {
      name: 'ElInput',
      template: '<input class="el-input-stub" :value="modelValue" @input="$emit(\'update:modelValue\', ($event.target as HTMLInputElement).value)" />',
      props: ['modelValue', 'disabled', 'placeholder'],
      emits: ['update:modelValue'],
    },
    ElButton: {
      name: 'ElButton',
      template: '<button class="el-button-stub" :disabled="disabled" @click="$emit(\'click\')"><slot /></button>',
      props: ['type', 'disabled'],
      emits: ['click'],
    },
  }
})

describe('ConditionEdgeDialog', () => {
  const defaultProps = {
    modelValue: true,
    edge: { source: 'check', target: 'notify', condition: null },
    nodeNames: ['check', 'notify'],
    stateChannels: ['input', 'messages', 'history', 'check_result', 'notify_result'],
  }

  function mountDialog(props = {}) {
    return mount(ConditionEdgeDialog, {
      props: { ...defaultProps, ...props },
      global: {
        plugins: [ElementPlus],
      },
    })
  }

  // eslint-disable-next-line @typescript-eslint/no-explicit-any
  function vm(wrapper: ReturnType<typeof mountDialog>): any {
    return wrapper.vm as any
  }

  describe('条件类型: 无条件', () => {
    it('conditionType="none" → confirm 发 { target, condition: null }', async () => {
      const wrapper = mountDialog()
      await vm(wrapper).$nextTick()

      vm(wrapper).conditionType = 'none'
      await vm(wrapper).$nextTick()

      vm(wrapper).handleConfirm()

      const emitted = wrapper.emitted('confirm')
      expect(emitted).toBeTruthy()
      expect(emitted![0][0]).toEqual({ target: 'notify', condition: null })
    })
  })

  describe('条件类型: 等值', () => {
    it('path="check_result.response", literal="OK" → condition === "check_result.response == \'OK\'"', async () => {
      const wrapper = mountDialog()
      await vm(wrapper).$nextTick()

      vm(wrapper).conditionType = 'equality'
      vm(wrapper).selectedPath = 'check_result.response'
      vm(wrapper).literal = 'OK'
      await vm(wrapper).$nextTick()

      vm(wrapper).handleConfirm()

      const emitted = wrapper.emitted('confirm')
      expect(emitted).toBeTruthy()
      expect((emitted![0][0] as any).condition).toBe("check_result.response == 'OK'")
    })

    it('literal 含单引号 → 内联错误，confirm 禁用', async () => {
      const wrapper = mountDialog()
      await vm(wrapper).$nextTick()

      vm(wrapper).conditionType = 'equality'
      vm(wrapper).selectedPath = 'check_result.response'
      vm(wrapper).literal = "it's"
      await vm(wrapper).$nextTick()

      const errors = wrapper.findAll('.condition-edge-dialog__error')
      expect(errors.some((e) => e.text().includes('单引号'))).toBe(true)

      expect(vm(wrapper).canConfirm).toBe(false)
    })
  })

  describe('条件类型: 真值', () => {
    it('path="result.ready" → condition === "result.ready"', async () => {
      const wrapper = mountDialog()
      await vm(wrapper).$nextTick()

      vm(wrapper).conditionType = 'truthy'
      vm(wrapper).selectedPath = 'result.ready'
      await vm(wrapper).$nextTick()

      vm(wrapper).handleConfirm()

      const emitted = wrapper.emitted('confirm')
      expect(emitted).toBeTruthy()
      expect((emitted![0][0] as any).condition).toBe('result.ready')
    })
  })

  describe('path 校验', () => {
    it('非法 path（空格/运算符/数字开头）→ 内联错误，confirm 禁用', async () => {
      const wrapper = mountDialog()
      await vm(wrapper).$nextTick()

      vm(wrapper).conditionType = 'equality'
      vm(wrapper).selectedPath = '123invalid'
      await vm(wrapper).$nextTick()

      const errors = wrapper.findAll('.condition-edge-dialog__error')
      expect(errors.some((e) => e.text().includes('路径格式非法'))).toBe(true)

      expect(vm(wrapper).canConfirm).toBe(false)
    })
  })

  describe('property: 任意输入组合', () => {
    it('生成的 condition 总是通过 isValidS7Condition', async () => {
      const wrapper = mountDialog()
      await vm(wrapper).$nextTick()

      const testCases = [
        { type: 'equality' as const, path: 'check_result.response', literal: 'OK' },
        { type: 'truthy' as const, path: 'result.ready', literal: '' },
      ]

      for (const tc of testCases) {
        vm(wrapper).conditionType = tc.type
        vm(wrapper).selectedPath = tc.path
        vm(wrapper).literal = tc.literal
        await vm(wrapper).$nextTick()

        vm(wrapper).handleConfirm()

        const emitted = wrapper.emitted('confirm')
        if (emitted) {
          const condition = (emitted[emitted.length - 1][0] as any).condition
          if (condition !== null) {
            const { isValidS7Condition } = await import('@/utils/s7Condition')
            expect(isValidS7Condition(condition)).toBe(true)
          }
        }
      }
    })
  })

  describe('readonly 模式', () => {
    it('readonly=true → 控件禁用，confirm 按钮隐藏', async () => {
      const wrapper = mountDialog({ readonly: true })
      await vm(wrapper).$nextTick()

      const html = wrapper.html()
      expect(html).toContain('disabled')

      const confirmBtn = wrapper.findAll('.el-button-stub').find((b) => b.text() === '确定')
      expect(confirmBtn).toBeUndefined()
    })
  })

  describe('初始化回填', () => {
    it('edge.condition="a == \'b\'" → 打开时 type=equality, path=\'a\', literal=\'b\'', async () => {
      const wrapper = mountDialog({
        edge: { source: 'check', target: 'notify', condition: "a == 'b'" },
      })
      await vm(wrapper).$nextTick()

      expect(vm(wrapper).conditionType).toBe('equality')
      expect(vm(wrapper).selectedPath).toBe('a')
      expect(vm(wrapper).literal).toBe('b')
    })

    it('edge.condition="result.ready" → 打开时 type=truthy, path=\'result.ready\'', async () => {
      const wrapper = mountDialog({
        edge: { source: 'check', target: 'notify', condition: 'result.ready' },
      })
      await vm(wrapper).$nextTick()

      expect(vm(wrapper).conditionType).toBe('truthy')
      expect(vm(wrapper).selectedPath).toBe('result.ready')
    })

    it('edge.condition=null → 打开时 type=none', async () => {
      const wrapper = mountDialog({
        edge: { source: 'check', target: 'notify', condition: null },
      })
      await vm(wrapper).$nextTick()

      expect(vm(wrapper).conditionType).toBe('none')
    })
  })

  describe('D6 守卫', () => {
    it('dialog HTML 不含 \'no_match_policy\' 或 \'default_edges\' 编辑控件', async () => {
      const wrapper = mountDialog()
      const html = wrapper.html()
      expect(html).not.toContain('no_match_policy')
      expect(html).not.toContain('default_edges')
    })
  })
})
