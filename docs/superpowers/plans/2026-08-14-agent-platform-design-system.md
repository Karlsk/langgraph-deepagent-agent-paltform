# AI Agent 平台设计系统 Implementation Plan

> **For agentic workers:** REQUIRED SUB-SKILL: Use superpowers:subagent-driven-development (recommended) or superpowers:executing-plans to implement this plan task-by-task. Steps use checkbox (`- [ ]`) syntax for tracking.

**Goal:** 将已确认的“电紫与薄荷”设计系统以 CSS 变量形式接入 Vue 管理后台的全局主题和应用壳层。

**Architecture:** `agent-web/src/styles/index.css` 是唯一的设计令牌源，提供基础色、语义色、形状、阴影与动效变量，并同步覆盖 Element Plus 主题变量。`agent-web/src/App.vue` 仅消费这些令牌以实现深色侧边栏和浅色工作区；测试读取令牌源文件，确保核心令牌和减少动态效果支持不会在后续样式调整中丢失。

**Tech Stack:** Vue 3、TypeScript、Vite、Element Plus、Vitest、CSS Custom Properties。

## Execution Record

- Implemented on `master` in commits `e309419`, `078cb2b`, `73ee325`, `cd5ae4a`, and `5c74cff`.
- Final verification on 2026-08-17: 17 frontend tests passed; type-check and production build passed; `git diff --check` passed.
- The browser visual check completed against the running Vite application before the final accessibility fix; the final fix is covered by design-token regression tests and a fresh production build.

## Global Constraints

- 所有品牌、语义、布局、圆角、阴影、动效数值只能在 `agent-web/src/styles/index.css` 的令牌区定义；页面组件不得硬编码品牌色十六进制值。
- 侧栏使用 `#171725` 语义令牌，工作区使用 `#F8F8FC` 语义令牌；主操作使用 `#635BFF`，正向状态使用 `#36D6B0` / `#168B69`。
- 保留当前五条路由和 Provider 连通性逻辑；不引入 Pinia、SSR、额外 UI 库或业务功能。
- Element Plus 主题色必须从语义令牌映射，不能与设计系统形成第二套独立色值。
- 全局动效必须在 `prefers-reduced-motion: reduce` 下禁用或极大缩短。
- JSON 配置保持严格 JSON，不添加注释。

---

### Task 1: 为设计令牌建立回归测试

**Files:**
- Create: `agent-web/tests/design-tokens.spec.ts`
- Modify: `agent-web/tsconfig.test.json`

**Interfaces:**
- Consumes: `agent-web/src/styles/index.css` 中的 `:root` 令牌定义。
- Produces: 对核心色彩、形状、动效与减少动态效果规则的自动化回归保护。

- [x] **Step 1: 写出失败的令牌测试**

```ts
import { readFileSync } from 'node:fs'
import { fileURLToPath } from 'node:url'
import { describe, expect, it } from 'vitest'

const stylesheet = readFileSync(
  fileURLToPath(new URL('../src/styles/index.css', import.meta.url)),
  'utf8',
)

describe('design tokens', () => {
  it('defines the approved A-palette semantic tokens', () => {
    expect(stylesheet).toContain('--color-primary-500: #635bff;')
    expect(stylesheet).toContain('--color-accent-500: #36d6b0;')
    expect(stylesheet).toContain('--color-bg-sidebar: #171725;')
    expect(stylesheet).toContain('--color-bg-canvas: #f8f8fc;')
  })

  it('defines motion tokens and honors reduced-motion preferences', () => {
    expect(stylesheet).toContain('--duration-fast: 120ms;')
    expect(stylesheet).toContain('--ease-standard: cubic-bezier(.2, .8, .2, 1);')
    expect(stylesheet).toContain('@media (prefers-reduced-motion: reduce)')
  })
})
```

- [x] **Step 2: 运行测试并确认其因缺少新令牌而失败**

Run: `cd agent-web && npm test -- tests/design-tokens.spec.ts`

Expected: FAIL，断言找不到 `--color-primary-500`。

- [x] **Step 3: 让 Vitest 纳入 Node 文件系统类型**

在 `agent-web/tsconfig.test.json` 的 `include` 数组添加 `tests/**/*.ts`（若已有则保持），并在 `compilerOptions.types` 中保留 `vitest/globals` 与 Node 类型，使上述测试的 `node:fs` 和 `node:url` 导入可通过类型检查。

- [x] **Step 4: 暂不改生产样式，再次运行测试确认仍为 RED**

Run: `cd agent-web && npm test -- tests/design-tokens.spec.ts`

Expected: FAIL，且失败原因仍是缺少设计令牌而不是 TypeScript 或模块解析错误。

- [x] **Step 5: 提交测试基线**

```bash
git add agent-web/tests/design-tokens.spec.ts agent-web/tsconfig.test.json
git commit -m "test(agent-web): cover design system tokens"
```

### Task 2: 在全局样式中实现设计令牌与 Element Plus 映射

**Files:**
- Modify: `agent-web/src/styles/index.css`
- Test: `agent-web/tests/design-tokens.spec.ts`

**Interfaces:**
- Consumes: Task 1 的字符串回归测试与设计规范 `docs/superpowers/specs/2026-08-14-agent-platform-design-system-design.md`。
- Produces: `--color-*`、`--radius-*`、`--shadow-*`、`--duration-*`、`--ease-standard` CSS 变量，以及对应的 `--el-color-*` 覆盖。

- [x] **Step 1: 确认 Task 1 测试仍然失败**

Run: `cd agent-web && npm test -- tests/design-tokens.spec.ts`

Expected: FAIL，定位为设计令牌尚未写入样式表。

- [x] **Step 2: 在 `:root` 中写入基础与语义令牌**

在文件顶层的 `:root` 中定义下列值，统一使用小写十六进制：

```css
--color-primary-50: #f1f0ff;
--color-primary-500: #635bff;
--color-primary-600: #5248e8;
--color-accent-500: #36d6b0;
--color-success-600: #168b69;
--color-warning-600: #b86808;
--color-danger-600: #c93d55;
--color-bg-canvas: #f8f8fc;
--color-bg-subtle: #f1f1f6;
--color-bg-surface: #ffffff;
--color-bg-sidebar: #171725;
--color-text-primary: #24242e;
--color-text-secondary: #666674;
--color-text-tertiary: #90909d;
--color-text-on-dark: #f4f4fa;
--color-text-on-dark-muted: #a9a9bc;
--color-border-default: #e4e4ec;
--color-border-strong: #d2d2df;
--radius-sm: 6px;
--radius-md: 8px;
--radius-lg: 12px;
--radius-xl: 16px;
--shadow-sm: 0 1px 2px rgba(23, 23, 37, 0.05);
--shadow-md: 0 8px 24px rgba(23, 23, 37, 0.1);
--shadow-lg: 0 20px 48px rgba(23, 23, 37, 0.16);
--duration-fast: 120ms;
--duration-base: 180ms;
--duration-slow: 240ms;
--ease-standard: cubic-bezier(.2, .8, .2, 1);
```

- [x] **Step 3: 用语义令牌覆盖 Element Plus 颜色**

```css
--el-color-primary: var(--color-primary-500);
--el-color-primary-dark-2: var(--color-primary-600);
--el-color-success: var(--color-success-600);
--el-color-warning: var(--color-warning-600);
--el-color-danger: var(--color-danger-600);
--el-border-color: var(--color-border-default);
--el-border-color-light: var(--color-border-default);
--el-bg-color: var(--color-bg-surface);
--el-fill-color-light: var(--color-bg-subtle);
--el-text-color-primary: var(--color-text-primary);
--el-text-color-regular: var(--color-text-secondary);
--el-text-color-secondary: var(--color-text-tertiary);
```

将现有 `.app-card`、`.app-content-card` 和页面骨架样式改为仅使用上述令牌；卡片圆角改用 `var(--radius-lg)`，静态阴影改用 `var(--shadow-sm)`。

- [x] **Step 4: 加入键盘焦点和减少动态效果规则**

```css
:where(button, a, input, textarea, select, [tabindex]):focus-visible {
  outline: 2px solid var(--color-primary-500);
  outline-offset: 2px;
}

@media (prefers-reduced-motion: reduce) {
  *, *::before, *::after {
    animation-duration: 0.01ms !important;
    animation-iteration-count: 1 !important;
    scroll-behavior: auto !important;
    transition-duration: 0.01ms !important;
  }
}
```

- [x] **Step 5: 运行回归测试，确认 GREEN**

Run: `cd agent-web && npm test -- tests/design-tokens.spec.ts`

Expected: PASS，2 个测试均通过。

- [x] **Step 6: 提交全局令牌**

```bash
git add agent-web/src/styles/index.css agent-web/tests/design-tokens.spec.ts
git commit -m "feat(agent-web): add purple mint design tokens"
```

### Task 3: 将应用壳层迁移到深色侧栏与浅色工作区

**Files:**
- Modify: `agent-web/src/App.vue`
- Modify: `agent-web/src/styles/index.css`
- Test: `agent-web/tests/router.spec.ts`

**Interfaces:**
- Consumes: Task 2 的 `--color-*`、`--radius-*`、`--shadow-*` 和动效变量。
- Produces: 深色导航、亮色当前态、浅色画布和有焦点反馈的管理后台壳层；既有 `/chat`、`/agent`、`/skill`、`/mcp`、`/llm` 路由不变。

- [x] **Step 1: 运行路由测试确认现有导航行为为 GREEN**

Run: `cd agent-web && npm test -- tests/router.spec.ts`

Expected: PASS，5 个命名路由均仍可解析。

- [x] **Step 2: 为壳层写失败的视觉语义断言**

在 `agent-web/tests/design-tokens.spec.ts` 添加：

```ts
it('uses semantic tokens for the application shell', () => {
  const shell = readFileSync(
    fileURLToPath(new URL('../src/App.vue', import.meta.url)),
    'utf8',
  )

  expect(shell).toContain('background: var(--color-bg-sidebar);')
  expect(shell).toContain('color: var(--color-text-on-dark);')
  expect(shell).toContain('background: var(--color-bg-canvas);')
})
```

- [x] **Step 3: 运行新增断言并确认 RED**

Run: `cd agent-web && npm test -- tests/design-tokens.spec.ts`

Expected: FAIL，当前 `App.vue` 仍使用白色侧栏和旧应用变量。

- [x] **Step 4: 更新 `App.vue` 的壳层样式**

将 `.app-sidebar` 改为 `background: var(--color-bg-sidebar)` 与 `border-right: 1px solid rgba(255, 255, 255, 0.08)`；品牌主文字和页脚使用深色表面令牌。菜单移除固定 `text-color` 与 `active-text-color` 属性，让 scoped 样式以 `--color-text-on-dark-muted`、`--color-text-on-dark` 和低透明度白色背景处理默认、hover 和 active 状态。将主标记渐变改为 `var(--color-primary-500)` 到 `var(--color-accent-500)`，并将所有圆角、阴影、动效替换成 Task 2 令牌。

将 `.app-main` 和 `.app-shell` 画布改用 `var(--color-bg-canvas)`；内容卡片保留白色表面和低层级边框，避免在高密度表格场景使用重阴影。

- [x] **Step 5: 验证样式与路由回归均通过**

Run: `cd agent-web && npm test -- tests/design-tokens.spec.ts tests/router.spec.ts`

Expected: PASS，令牌与路由测试均通过。

- [x] **Step 6: 提交壳层迁移**

```bash
git add agent-web/src/App.vue agent-web/src/styles/index.css agent-web/tests/design-tokens.spec.ts
git commit -m "feat(agent-web): apply dark sidebar design system"
```

### Task 4: 完整验证与浏览器视觉检查

**Files:**
- Verify: `agent-web/src/styles/index.css`
- Verify: `agent-web/src/App.vue`
- Verify: `agent-web/tests/*.spec.ts`

**Interfaces:**
- Consumes: Tasks 1–3 的代码与测试。
- Produces: 可复现的构建证据和对 `/llm` 页面视觉层级的浏览器确认。

- [x] **Step 1: 运行完整前端测试**

Run: `cd agent-web && npm test`

Expected: PASS，所有 request、router、provider connection 与 design tokens 测试通过。

- [x] **Step 2: 运行类型检查和生产构建**

Run: `cd agent-web && npm run type-check && npm run build`

Expected: 两个命令均以 exit code 0 结束。

- [x] **Step 3: 在浏览器确认视觉结果**

打开本地 Vite 的 `/llm` 路由，确认以下事实：

1. 左侧导航是深曜石色，品牌和当前菜单文本清晰可读。
2. 右侧工作区是雾白浅底，内容卡片保持白色和轻边框。
3. 主色呈电紫，连接失败/成功状态仍分别保留危险/成功语义色。
4. 未改变 Provider 连通性提示文案与路由导航行为。

- [x] **Step 4: 最终检查并提交**

```bash
git diff --check
git status --short
git add agent-web/src/App.vue agent-web/src/styles/index.css agent-web/tests/design-tokens.spec.ts agent-web/tsconfig.test.json
git commit -m "feat(agent-web): complete admin design system"
```

若任务 1–3 的提交已经包含所有文件，最后一条 `git commit` 不执行；只报告现有提交 SHA 和完整验证结果。
