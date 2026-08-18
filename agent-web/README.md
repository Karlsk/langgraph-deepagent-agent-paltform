# Agent Web 前端规范

Agent 平台管理前端（Vue 3 + TypeScript + Vite + Element Plus）。本文档是前端设计
规范、代码规范、基础组件库与统一请求层的唯一权威说明；完整产品规格另见
[`docs/frontend-spec.md`](../docs/frontend-spec.md)。

## 1. 技术栈与环境

| 类别 | 选型 | 版本 |
|---|---|---|
| 框架 | Vue（组合式 API） | ^3.5.13 |
| 语言 | TypeScript（strict） | ~5.7.3 |
| 构建 | Vite | ^6.0.7 |
| UI 库 | Element Plus + @element-plus/icons-vue | ^2.9.3 |
| 路由 | vue-router | ^4.5.0 |
| HTTP | axios | ^1.7.9 |
| 测试 | Vitest + @vue/test-utils + happy-dom | ^3.2.4 |

环境要求：Node.js >= 20（见 `package.json` engines）。

## 2. 快速开始

```bash
npm install        # 安装依赖
npm run dev        # 开发服务器（默认 5173），/api 代理到 BACKEND_URL（默认 http://localhost:8000）
npm run dev:docker # Docker 模式（读 .env.docker，代理 http://app:8000）
npm run build      # vue-tsc -b 类型检查 + vite build
npm run type-check # 仅 vue-tsc -b
npm test           # vitest run（一次性运行全部测试）
```

代理无 rewrite：后端 `API_V1_STR=/api/v1` 与 `src/utils/request.ts` 的 baseURL
一致；若修改 `API_V1_STR`，须同步 Vite 代理规则与 baseURL。

## 3. 目录结构

```
src/
  api/           # API 模块：对接后端端点（assets.ts 为五大资产模块范式）
  components/    # 通用组件：WebAgentTable、WebAgentFormDialog
  composables/   # 可复用逻辑：useRequest、useConfirm
  router/        # 路由定义（懒加载，meta.title 驱动面包屑）
  styles/        # index.css：全局设计令牌与通用样式（唯一全局样式入口）
  types/         # 跨层契约类型：PageQuery / ApiResponse / PageResult
  utils/         # 基础设施：request.ts、notify.ts、paginate.ts
  views/         # 按业务域划分的页面（agent/skill/mcp/provider/chat）
tests/           # Vitest 测试（design-tokens / router / request / 组件与视图）
```

依赖方向自顶向下：`views → components/composables → utils/types`，反向不引用。

## 4. 设计规范（Design）

### 4.1 设计令牌体系

所有视觉变量集中在 `src/styles/index.css` 的 `:root`，分四类：

| 类别 | 令牌示例 | 值 |
|---|---|---|
| 品牌/语义色 | `--color-primary-500` | `#635bff` |
| | `--color-primary-50` / `-600` | `#f1f0ff` / `#5248e8` |
| | `--color-accent-500` | `#36d6b0` |
| | `--color-success-600` / `-warning-600` / `-danger-600` | `#168b69` / `#b86808` / `#c93d55` |
| 背景/文字/边框 | `--color-bg-canvas` / `-subtle` / `-surface` | `#f8f8fc` / `#f1f1f6` / `#ffffff` |
| | `--color-bg-dark` / `-dark-raised` | `#0b0f16` / `#161b26`（深色侧边栏与暗色主题预留） |
| | `--color-text-primary` / `-secondary` / `-tertiary` | `#24242e` / `#666674` / `#90909d` |
| | `--color-text-on-dark` / `-muted` | `#f1f1f6` / `#9a9ab0` |
| | `--color-border-default` / `-strong` | `#e4e4ec` / `#d2d2df` |
| 圆角/阴影/动效 | `--radius-sm/md/lg/xl` | 6 / 8 / 12 / 16px |
| | `--shadow-sm/md/lg` | 三档阴影 |
| | `--duration-fast/base/slow`、`--ease-standard` | 120 / 180 / 240ms、`cubic-bezier(.2,.8,.2,1)` |

Element Plus 主题映射：`index.css` 用 `color-mix(in srgb, ...)` 从语义令牌派生全套
`--el-color-*`（light-3/5/7/8/9、dark-2、rgb），并映射边框、背景、文字变量，使
`el-button`、`el-table`、`el-dialog` 等组件自动继承平台主题。

**颜色唯一来源约束**：禁止在组件中硬编码十六进制/rgb 色值；新增颜色必须先在
`:root` 定义语义令牌，再按需映射 `--el-*`。

**契约执行者**：`tests/design-tokens.spec.ts` 以字符串断言锁定令牌值、`--el-*`
映射与 App.vue shell 结构。改动令牌或 shell 布局时，必须同步更新该测试。

### 4.2 页面骨架

新页面统一使用 `.page-view` 骨架（`index.css` 提供）：

```html
<div class="page-view">
  <header class="page-view__header">
    <div>
      <h1 class="page-view__title">页面标题</h1>
      <p class="page-view__desc">页面描述。</p>
    </div>
    <div class="page-view__actions"><!-- 右侧按钮区 --></div>
  </header>
  <section class="content-card page-view__body"><!-- 内容 --></section>
</div>
```

- `.content-card`：白底、1px 边框、`--radius-lg` 圆角、`--shadow-sm`、20px 内边距
- `.page-view__body`：`min-height: 320px`
- 可选 `.page-view__eyebrow`：品牌色小标签（大写、0.18em 字距）

### 4.3 按钮规范

所有 `el-button` 统一加 `class="app-btn"` 并搭配修饰类，不得自定义圆角/背景：

| 类 | 外观 |
|---|---|
| `app-btn app-btn--primary` | 品牌渐变（primary → accent）、白字、hover 提亮 |
| `app-btn app-btn--secondary` | 白底描边，hover 变品牌色描边+文字 |
| `app-btn app-btn--danger` | danger 实心，hover 提亮 |

表格行内操作使用 `el-button link`（`type="primary"` / `type="danger"`）。

### 4.4 响应式与无障碍

- `@media (max-width: 768px)`：页头纵向堆叠、操作区取消左外边距（App.vue 另收窄侧边栏）
- `:focus-visible` 统一焦点轮廓：`2px solid var(--color-primary-500)`
- `@media (prefers-reduced-motion: reduce)`：动画/过渡压缩至 0.01ms

## 5. 代码规范

- **组件写法**：`<script setup lang="ts">` + 组合式 API；TS strict，函数签名全类型标注
- **依赖方向**：views → components/composables → utils/types，反向不引用
- **命名**：通用组件 `WebAgent` 前缀（如 `WebAgentTable`）；TS/样式文件小驼峰或 kebab-case；CSS 类名 BEM 风格（`block__element--modifier`）
- **样式作用域**：组件局部样式写 `<style scoped>`，穿透 Element Plus 用 `:deep()`；全局样式只进 `src/styles/index.css`
- **状态字段**：不引入 Pinia/SSR/monorepo 工具链；页面级状态用组件内 `ref`/`reactive`
- **配置纪律**：`tsconfig*.json`、`package.json` 严格 JSON，不得含注释（pre-commit `check-json`）
- **测试规范**：Vitest + happy-dom；Element Plus 组件一律 stub（不做真实渲染）；零真实网络、零真实 LLM 调用；fake timers 覆盖异步时序

## 6. 五个基础组件

### 6.1 WebAgentTable（`src/components/WebAgentTable.vue`）

泛型列表表格：分页请求、三态托管、列配置 + 具名插槽自定义单元格。

```ts
// 关键契约
interface TableColumnConfig { label: string; prop: string; width?: string | number; slot?: string }
props: {
  columns: TableColumnConfig[]
  api: (query: PageQuery) => Promise<PageResult<T>>  // 分页契约先行
  pagination?: boolean      // 默认 true
  query?: Record<string, unknown>  // 额外过滤条件，变化时重置到第 1 页
  immediate?: boolean       // 默认 true（挂载即请求）
  defaultPageSize?: number  // 默认 10
}
expose: { refresh(): void } // 保留当前页重新请求
```

- `column.slot` 存在时，以同名具名作用域插槽渲染单元格：`<template #status="{ row }">`
- 后端真分页端点直接传入；mock/全量数组用 `paginateLocal` 包装，组件零改动切换
- 请求失败收敛为空数据（错误提示由统一请求层全局拦截器承担）

### 6.2 WebAgentFormDialog（`src/components/WebAgentFormDialog.vue`）

带校验与快照恢复的表单弹窗。

```ts
props: { modelValue: boolean; title: string; width?: string; rules?: FormRules }
emit: { 'update:modelValue'; submit: [data: Record<string, unknown>] }
expose: {
  open(data?): void          // 不传 data = 新增模式；传 data = 编辑模式（深拷贝回填）
  close(): void
  setSubmitting(v: boolean): void  // 驱动确定按钮 loading
}
```

- 默认插槽提供作用域 `{ form, mode }`，`form` 为 reactive 表单模型
- 确定时先 `formRef.validate()`，通过才 emit `submit`（携带 `{ ...formModel }`）
- 关闭自动重置回 `open()` 时的初始快照（新增为空、编辑为原始数据）
- 提交方异步流程范式：`setSubmitting(true)` → 请求 → `setSubmitting(false)` → `close()` → `notifySuccess` → 表格 `refresh()`

### 6.3 useConfirm（`src/composables/useConfirm.ts`）

一行完成"确认删除 → 执行 → 成功提示"。

```ts
function useConfirm(
  message: string,
  api: () => Promise<unknown>,
  options?: { title?: string; successMessage?: string },
): () => Promise<boolean>
// resolve true = 确认并执行成功；false = 用户取消或执行失败
```

调用方通常在 resolve true 后刷新表格。错误提示由全局拦截器承担，不弹重复错误。

### 6.4 useRequest（`src/composables/useRequest.ts`）

请求三态管理，替代手写 try-catch-finally。

```ts
function useRequest<T, A extends unknown[] = []>(
  api: (...args: A) => Promise<T>,
  options?: { immediate?: boolean; defaultParams?: A },
): { data: Ref<T | null>; loading: Ref<boolean>; error: Ref<unknown>; execute: (...args: A) => Promise<T | null> }
```

- `execute` 不向外抛异常；失败时保留旧 `data`（避免 UI 闪烁）并返回 `null`
- 错误提示由统一请求层全局拦截器承担，此处只做状态收敛

### 6.5 notify（`src/utils/notify.ts`）

统一 ElMessage 封装，全站提示一致外观与停留时长：

| 函数 | 停留 |
|---|---|
| `notifySuccess(message)` | 3s |
| `notifyError(message)` | 5s |
| `notifyWarning(message)` | 3s |

均带 `showClose: true`。业务代码不直接调 `ElMessage`（request.ts 拦截器除外）。

### 组合范式

完整 CRUD 页面示例见 `src/views/provider/ProviderList.vue`（mock 数据）：
WebAgentTable 列表 + WebAgentFormDialog 新增/编辑 + useConfirm 删除 + notify 提示，
对应测试 `tests/components/provider-list.spec.ts`（EP stub + fake timers 全流程）。

## 7. 统一请求层

### 7.1 request.ts（`src/utils/request.ts`）

统一 axios 实例与响应信封处理：

- `baseURL: '/api/v1'`，`timeout: 15000`
- **信封契约**：后端统一返回 `{ code, message, data }`，`code` 数值与 HTTP status
  完全一致（成功 2xx、创建 201；错误为对应错误码）
- **isEnvelope 守卫**：要求三字段齐全（`data` 键必须存在，值可为 null），避免把
  形状碰撞的裸响应误判为信封；豁免端点（`/health`、SSE 流）返回裸响应原样透传
- **成功**：2xx（含 201）自动解包，调用方直接拿到 `data` 载荷
- **失败**：非 2xx 提取可读文案（信封 `message` 优先，回退 FastAPI `detail`），
  `ElMessage.error` 提示后 reject
- token 注入与 401 处理目前为 TODO 占位（待接入认证体系）

导出四个泛型方法，返回解包后的业务数据：

```ts
get<T>(url, config?): Promise<T>
post<T>(url, data?, config?): Promise<T>
put<T>(url, data?, config?): Promise<T>
del<T = void>(url, config?): Promise<T>   // DELETE 的 data 恒为 null
```

### 7.2 API 模块约定（`src/api/assets.ts` 范式）

- 模块函数返回值即解包后的 `data` 载荷（信封处理由拦截器完成）
- 全量列表端点（`GET /<module>`）返回裸数组；分页端点（`GET /<module>/page`）返回
  `PageResult<T>`（后端 `pageSize` 为驼峰，行字段为 snake_case）
- `PageQuery` 经 `toParams` 透传为 `page/pageSize/keyword` 查询参数
- 每个后端资源定义对应 `Row` 接口（如 `SubAgentRow`、`LlmConfigRow`）

### 7.3 paginateLocal（`src/utils/paginate.ts`）

本地分页适配器：把裸列表包装为 `PageResult<T>`，支持可选 `filter` 谓词。仅用于
mock 数据或已持有全量数组的场景；与真分页端点共享同一契约，WebAgentTable 两种
数据源无缝切换。

### 7.4 类型契约（`src/types/index.ts`）

```ts
interface PageQuery { page?: number; pageSize?: number; keyword?: string }
interface ApiResponse<T = unknown> { code: number; message: string; data: T | null }
interface PageResult<T> { items: T[]; total: number; page: number; pageSize: number }
```

## 8. 相关文档

- 完整前端产品规格：[`docs/frontend-spec.md`](../docs/frontend-spec.md)
- 项目级 Agent 开发指南（含前端红线）：[`AGENTS.md`](../AGENTS.md)
