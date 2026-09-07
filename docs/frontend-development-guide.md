# 前端开发规范（agent-web）

| 项 | 值 |
| --- | --- |
| 文档角色 | 前端开发者指南：技术栈、设计/代码/测试规范、五大基础组件与统一请求层的开发视角总结 |
| 权威来源 | [`agent-web/README.md`](../agent-web/README.md)（唯一权威）；本文档为开发指南视角的展开，与 README 冲突时以 README 为准 |
| 产品规格 | [`docs/frontend-spec.md`](frontend-spec.md)（完整产品规格） |
| 项目红线 | [`AGENTS.md`](../AGENTS.md) Frontend 章节 |
| 适用范围 | `agent-web/`（Vue 3 + TypeScript + Vite + Element Plus 管理前端） |

> 本文档面向**新增前端功能的开发者**，把 `agent-web/README.md` 的规范整理为"怎么落地"的操作视角。
> 所有契约细节以 README 为准；完整产品需求以 `docs/frontend-spec.md` 为准。

---

## 1. 技术栈选型与快速开始

### 1.1 技术栈

| 类别 | 选型 | 版本 |
| --- | --- | --- |
| 框架 | Vue（组合式 API） | ^3.5.13 |
| 语言 | TypeScript（strict） | ~5.7.3 |
| 构建 | Vite | ^6.0.7 |
| UI 库 | Element Plus + @element-plus/icons-vue | ^2.9.3 |
| 路由 | vue-router | ^4.5.0 |
| HTTP | axios | ^1.7.9 |
| 测试 | Vitest + @vue/test-utils + happy-dom | ^3.2.4 |

环境要求：Node.js >= 20（见 `package.json` engines），npm 管理。

### 1.2 快速开始

```bash
npm install        # 安装依赖
npm run dev        # 开发服务器（默认 5173），/api 代理到 BACKEND_URL（默认 http://localhost:8000）
npm run dev:docker # Docker 模式（读 .env.docker，代理 http://app:8000）
npm run build      # vue-tsc -b 类型检查 + vite build
npm run type-check # 仅 vue-tsc -b
npm test           # vitest run（一次性运行全部测试）
```

### 1.3 代理与 baseURL 三处同步（关键约束）

- Vite `/api` 代理 target 取 `BACKEND_URL`（`loadEnv` 全量加载）：本地默认 `http://localhost:8000`，
  Docker 模式读 `agent-web/.env.docker` 的 `http://app:8000`。
- **代理无 rewrite**：后端 `API_V1_STR=/api/v1` 与 `src/utils/request.ts` 的 `baseURL: '/api/v1'` 一致。
- 若修改 `API_V1_STR`，**必须同步三处**：后端 `API_V1_STR` + Vite 代理前缀 + `request.ts` 的 `baseURL`。

### 1.4 目录结构

```
src/
  api/           # API 模块：对接后端端点（assets.ts 为五大资产模块范式）
  components/    # 通用组件：WebAgentTable、WebAgentFormDialog
  composables/   # 可复用逻辑：useRequest、useConfirm、useAuth、useChatStream ...
  router/        # 路由定义（懒加载，meta.title 驱动面包屑）
  styles/        # index.css：全局设计令牌与通用样式（唯一全局样式入口）
  types/         # 跨层契约类型：PageQuery / ApiResponse / PageResult
  utils/         # 基础设施：request.ts、notify.ts、paginate.ts、sse.ts ...
  views/         # 按业务域划分的页面（agent/skill/mcp/provider/chat/auth/bundle）
tests/           # Vitest 测试（design-tokens / router / request / notify / 组件与视图）
```

依赖方向自顶向下：`views → components/composables → utils/types`，反向不引用。

---

## 2. 设计规范（Design Tokens）

### 2.1 设计令牌体系

所有视觉变量集中在 `src/styles/index.css` 的 `:root`，分四类：

| 类别 | 令牌示例 | 值 |
| --- | --- | --- |
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

**Element Plus 主题映射**：`index.css` 用 `color-mix(in srgb, ...)` 从语义令牌派生全套 `--el-color-*`
（light-3/5/7/8/9、dark-2、rgb），并映射边框、背景、文字变量，使 `el-button`、`el-table`、`el-dialog`
等组件自动继承平台主题。

**颜色唯一来源约束**：禁止在组件中硬编码十六进制/rgb 色值；新增颜色必须先在 `:root` 定义语义令牌，
再按需映射 `--el-*`。

**契约执行者**：`tests/design-tokens.spec.ts` 以字符串断言锁定令牌值、`--el-*` 映射与 `App.vue` shell 结构。
**改动令牌或 shell 布局时，必须同步更新该测试**（否则 CI 红）。

### 2.2 页面骨架

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

- `.content-card`：白底、1px 边框、`--radius-lg` 圆角、`--shadow-sm`、20px 内边距。
- `.page-view__body`：`min-height: 320px`。
- 可选 `.page-view__eyebrow`：品牌色小标签（大写、0.18em 字距）。

### 2.3 按钮规范

所有 `el-button` 统一加 `class="app-btn"` 并搭配修饰类，不得自定义圆角/背景：

| 类 | 外观 |
| --- | --- |
| `app-btn app-btn--primary` | 品牌渐变（primary → accent）、白字、hover 提亮 |
| `app-btn app-btn--secondary` | 白底描边，hover 变品牌色描边+文字 |
| `app-btn app-btn--danger` | danger 实心，hover 提亮 |

表格行内操作使用 `el-button link`（`type="primary"` / `type="danger"`）。

### 2.4 响应式与无障碍

- `@media (max-width: 768px)`：页头纵向堆叠、操作区取消左外边距（`App.vue` 另收窄侧边栏）。
- `:focus-visible` 统一焦点轮廓：`2px solid var(--color-primary-500)`。
- `@media (prefers-reduced-motion: reduce)`：动画/过渡压缩至 0.01ms。

---

## 3. 五大基础组件使用范式

五个基础构件是 CRUD 页面的标准积木：**WebAgentTable / WebAgentFormDialog / useConfirm / useRequest / notify**。
新增列表页优先组合它们，而非从零手写。

### 3.1 WebAgentTable（`src/components/WebAgentTable.vue`）

泛型列表表格：分页请求、三态托管、列配置 + 具名插槽自定义单元格。

```ts
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

用法要点：

- `column.slot` 存在时，以同名具名作用域插槽渲染单元格：`<template #status="{ row }">`。
- 后端**真分页**端点直接传入 `api`；**mock/全量数组**用 `paginateLocal`（§4.4）包装，组件零改动切换。
- 请求失败收敛为空数据（错误提示由统一请求层全局拦截器承担，组件不重复弹错）。

### 3.2 WebAgentFormDialog（`src/components/WebAgentFormDialog.vue`）

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

用法要点：

- 默认插槽提供作用域 `{ form, mode }`，`form` 为 reactive 表单模型；组件暴露 `getForm()` 供父组件访问/修改表单模型。
- 确定时先 `formRef.validate()`，通过才 emit `submit`（携带 `{ ...formModel }`）。
- 关闭自动重置回 `open()` 时的初始快照（新增为空、编辑为原始数据）。
- **提交方异步流程范式**：`setSubmitting(true)` → 请求 → `setSubmitting(false)` → `close()` → `notifySuccess` → 表格 `refresh()`。

### 3.3 useConfirm（`src/composables/useConfirm.ts`）

一行完成"确认删除 → 执行 → 成功提示"。

```ts
function useConfirm(
  message: string,
  api: () => Promise<unknown>,
  options?: { title?: string; successMessage?: string },
): () => Promise<boolean>
// resolve true = 确认并执行成功；false = 用户取消或执行失败
```

调用方通常在 resolve `true` 后刷新表格。错误提示由全局拦截器承担，不弹重复错误。

### 3.4 useRequest（`src/composables/useRequest.ts`）

请求三态管理，替代手写 try-catch-finally。

```ts
function useRequest<T, A extends unknown[] = []>(
  api: (...args: A) => Promise<T>,
  options?: { immediate?: boolean; defaultParams?: A },
): { data: Ref<T | null>; loading: Ref<boolean>; error: Ref<unknown>; execute: (...args: A) => Promise<T | null> }
```

- `execute` **不向外抛异常**；失败时保留旧 `data`（避免 UI 闪烁）并返回 `null`。
- 错误提示由统一请求层全局拦截器承担，此处只做状态收敛。

### 3.5 notify（`src/utils/notify.ts`）

统一 ElMessage 封装，全站提示一致外观与停留时长：

| 函数 | 停留 |
| --- | --- |
| `notifySuccess(message)` | 3s |
| `notifyError(message)` | 5s |
| `notifyWarning(message)` | 3s |

均带 `showClose: true`。**业务代码不直接调 `ElMessage`**（`request.ts` 拦截器除外）。

### 3.6 组合范式（CRUD 页面）

完整 CRUD 页面示例见 `src/views/provider/ProviderList.vue`：
WebAgentTable 列表 + WebAgentFormDialog 新增/编辑 + useConfirm 删除 + notify 提示，
对应测试 `tests/components/provider-list.spec.ts`（EP stub + fake timers 全流程）。

典型新增/编辑提交流程：

```ts
async function onSubmit(data: Record<string, unknown>) {
  dialogRef.value?.setSubmitting(true)
  try {
    await (editing.value ? updateRow(data) : createRow(data))   // api 模块函数
    dialogRef.value?.setSubmitting(false)
    dialogRef.value?.close()
    notifySuccess(editing.value ? '更新成功' : '创建成功')
    tableRef.value?.refresh()
  } catch {
    dialogRef.value?.setSubmitting(false)   // 错误提示已由拦截器承担，此处只收敛 loading
  }
}
```

典型删除流程：

```ts
const confirmDelete = useConfirm('确认删除该记录？', () => deleteRow(id), { successMessage: '删除成功' })
if (await confirmDelete()) tableRef.value?.refresh()
```

---

## 4. 统一请求层契约

### 4.1 request.ts（`src/utils/request.ts`）

统一 axios 实例与响应信封处理：

- `baseURL: '/api/v1'`，`timeout: 15000`。
- **信封契约**：后端统一返回 `{ code, message, data }`，`code` 数值与 HTTP status 完全一致
  （成功 2xx、创建 201；错误为对应错误码）。
- **isEnvelope 守卫**：要求三字段齐全（`data` 键必须存在，值可为 null），避免把形状碰撞的裸响应误判为信封；
  豁免端点（`/health`、SSE 流）返回裸响应原样透传。
- **成功**：2xx（含 201）自动解包，调用方直接拿到 `data` 载荷。
- **失败**：非 2xx 提取可读文案（信封 `message` 优先，回退 FastAPI `detail`），`ElMessage.error` 提示后 reject。
- token 注入与 401 处理目前为 TODO 占位（待接入认证体系）。

导出四个泛型方法，返回解包后的业务数据：

```ts
get<T>(url, config?): Promise<T>
post<T>(url, data?, config?): Promise<T>
put<T>(url, data?, config?): Promise<T>
del<T = void>(url, config?): Promise<T>   // DELETE 的 data 恒为 null
```

> axios 支持 per-request timeout override（`config.timeout`），长耗时端点（如 LLM 生成）可局部放宽。

### 4.2 API 模块约定（`src/api/assets.ts` 范式）

- 模块函数返回值即**解包后的 `data` 载荷**（信封处理由拦截器完成）。
- 全量列表端点（`GET /<module>`）返回**裸数组**；分页端点（`GET /<module>/page`）返回 `PageResult<T>`
  （后端 `pageSize` 为驼峰，行字段为 snake_case）。
- `PageQuery` 经 `toParams` 透传为 `page/pageSize/keyword` 查询参数。
- 每个后端资源定义对应 `Row` 接口（如 `SubAgentRow`、`LlmConfigRow`）。

新增 API 模块步骤：在 `src/api/` 下新建文件，定义 list/get/create/update/delete 端点及其请求/响应类型
（含 `Row` 接口），函数返回解包后的载荷。

### 4.3 类型契约（`src/types/index.ts`）

```ts
interface PageQuery { page?: number; pageSize?: number; keyword?: string }
interface ApiResponse<T = unknown> { code: number; message: string; data: T | null }
interface PageResult<T> { items: T[]; total: number; page: number; pageSize: number }
```

### 4.4 paginateLocal（`src/utils/paginate.ts`）

本地分页适配器：把裸列表包装为 `PageResult<T>`，支持可选 `filter` 谓词。仅用于 mock 数据或已持有全量数组
的场景；与真分页端点共享同一契约，WebAgentTable 两种数据源无缝切换。

---

## 5. 代码规范

- **组件写法**：`<script setup lang="ts">` + 组合式 API；TS strict，函数签名全类型标注。
- **依赖方向**：`views → components/composables → utils/types`，反向不引用。
- **命名**：通用组件 `WebAgent` 前缀（如 `WebAgentTable`）；TS/样式文件小驼峰或 kebab-case；
  CSS 类名 BEM 风格（`block__element--modifier`）。
- **样式作用域**：组件局部样式写 `<style scoped>`，穿透 Element Plus 用 `:deep()`；全局样式只进 `src/styles/index.css`。
- **状态字段**：不引入 Pinia/SSR/monorepo 工具链；页面级状态用组件内 `ref`/`reactive`。
- **配置纪律**：`tsconfig*.json`、`package.json` 严格 JSON，不得含注释（pre-commit `check-json`）。
- **路由**：`src/router/index.ts` 全懒加载，`meta.title` 驱动面包屑；新增页面须在此注册路由。

### 5.1 骨架期红线（AGENTS.md）

- 不实现计划外的业务逻辑；视图按 `docs/frontend-spec.md` 的规划推进。
- **不擅自引入**状态管理（Pinia 等）、SSR 框架（Nuxt）、monorepo 工具。
- 所有 HTTP 请求走 `src/utils/request.ts`（baseURL `/api/v1`，经 `BACKEND_URL` 代理）；
  改 `API_V1_STR` 须同步 Vite 代理规则与 `request.ts` baseURL。

---

## 6. 测试规范（Vitest + happy-dom）

- **运行环境**：Vitest + happy-dom；`npm test` 一次性运行全部测试。
- **Element Plus stub 策略**：EP 组件一律 **stub**（不做真实渲染），聚焦业务逻辑与交互流；
  范例见 `tests/components/provider-list.spec.ts`。
- **零真实依赖**：零真实网络、零真实 LLM 调用；外部依赖（api 模块）用 mock 替代。
- **fake timers**：覆盖异步时序（如 notify 停留、loading 收敛、防抖）。

### 6.1 CRUD 页面测试覆盖点

新增 CRUD 页面测试应覆盖：

1. **挂载渲染**：列表加载、列渲染、分页。
2. **新增提交**：打开对话框（新增模式）→ 填表 → submit → 调用 create API → 成功提示 → 表格刷新。
3. **编辑回填**：打开对话框（编辑模式，深拷贝回填）→ 修改 → submit → 调用 update API。
4. **删除确认**：useConfirm 确认 → 调用 delete API → 成功提示 → 刷新；取消路径 resolve false。
5. **执行流程/异常**：请求失败时 loading 收敛、错误提示由拦截器承担（不重复弹）。

### 6.2 契约测试

- `tests/design-tokens.spec.ts`：锁定设计令牌值、`--el-*` 映射、App.vue shell 结构（改令牌/shell 必同步改此测试）。
- `tests/request.spec.ts`：锁定信封解包、isEnvelope 守卫、错误文案提取。
- `tests/router.spec.ts`：锁定路由表与懒加载、`meta.title`。
- `tests/notify.spec.ts` / `tests/paginate.spec.ts` / `tests/use-confirm.spec.ts` / `tests/use-request.spec.ts`：
  基础构件单元契约。

---

## 7. 新增前端功能的标准流程

以"新增一个后端资源的管理列表页"为例：

1. **API 模块**：在 `src/api/` 新建 `<resource>.ts`，定义 list/page/get/create/update/delete 端点与 `Row` 类型，
   函数返回解包后的载荷（§4.2）。
2. **列表页**：在 `src/views/<domain>/` 新建 `<Resource>List.vue`，用 `.page-view` 骨架（§2.2）+
   WebAgentTable（列表）+ WebAgentFormDialog（新增/编辑）+ useConfirm（删除）+ notify（提示）（§3）。
3. **路由**：在 `src/router/index.ts` 注册懒加载路由，设 `meta.title`（§5）。
4. **测试**：在 `tests/components/` 新建 `<resource>-list.spec.ts`，EP stub + fake timers，覆盖 §6.1 五点。
5. **自检**：`npm run type-check` 零错误；`npm test` 全绿；无硬编码色值（§2.1）；无直接 `ElMessage`（§3.5）。

> 提示：仓库提供 `frontend-feature-codegen` skill，可从后端测试文档生成匹配的 API 文件 + 列表页 +
> Vitest spec + 路由入口，遵循本文档的全部范式。

---

## 8. 相关文档

- 前端规范唯一权威：[`agent-web/README.md`](../agent-web/README.md)
- 完整产品规格：[`docs/frontend-spec.md`](frontend-spec.md)
- 项目级 Agent 开发指南（含前端红线）：[`AGENTS.md`](../AGENTS.md)
- 后端统一响应信封（Workflow API 出口）：[`docs/workflow-api-and-trace.md`](workflow-api-and-trace.md)
