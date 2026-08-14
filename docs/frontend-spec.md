# Frontend 规范（agent-web）

本文档描述 `agent-web/` 前端骨架的技术栈、目录结构、关键配置与开发约定。

> **核实基线**：本文所有配置均对照以下实际文件核实过：
> `agent-web/vite.config.ts`、`agent-web/.env.docker`、`agent-web/package.json`、
> `agent-web/src/main.ts`、`agent-web/src/App.vue`、`agent-web/src/router/index.ts`、
> `agent-web/src/utils/request.ts`、`agent-web/src/styles/index.css`、`agent-web/tsconfig.json`、
> `app/core/config.py`（API_V1_STR）、`Makefile`（web-* 目标）、`docker-compose.yml`、
> `.pre-commit-config.yaml`。若代码变更，请以源码为准。

> **骨架期定位**：当前 agent-web 处于骨架阶段——仅含页面框架、路由与占位视图，
> 不承载任何业务逻辑。骨架期红线见本文第 8 节与 `AGENTS.md` 的 Frontend 章节。

---

## 1. 技术栈选型

| 选型 | 版本（package.json） | 选型理由 |
|---|---|---|
| Vue 3 | `^3.5.13` | Composition API + `<script setup>`，与团队 TS 优先策略契合 |
| TypeScript | `~5.7.3`（strict 模式） | 全仓类型安全一致性；`tsconfig.json` 开启 `strict`、`noUnusedLocals`、`noUnusedParameters` |
| Vite 6 | `^6.0.7` | 秒级冷启动 + HMR，原生 ESM，构建即 `vite build` |
| Element Plus | `^2.9.3` | 成熟的中后台组件库，覆盖表格/表单/对话框等运营控制台高频场景 |
| vue-router | `^4.5.0` | Vue 官方路由，HTML5 history 模式 |
| axios | `^1.7.9` | 拦截器机制便于统一注入 token 与错误处理（见 `src/utils/request.ts`） |
| @element-plus/icons-vue | `^2.3.2` | 侧边栏菜单图标 |

运行环境：`engines.node >= 20`（package.json 声明）；包管理器为 **npm**（仓库提交 `package-lock.json`，Makefile 使用 `npm ci`）。

### 1.1 Element Plus 引入方式

当前为**全量引入**（`src/main.ts`）：

```ts
import ElementPlus from 'element-plus'
import 'element-plus/dist/index.css'

app.use(ElementPlus)
```

骨架期简单优先：零配置、无构建插件依赖。全量 CSS 体积在内部运营控制台场景可接受。

**后续升级路径（按需引入）**：组件数量增多、关注产物体积时，迁移到
`unplugin-vue-components` + `ElementPlusResolver`：

```bash
npm i -D unplugin-vue-components unplugin-auto-import
```

```ts
// vite.config.ts（示意，未落地）
import AutoImport from 'unplugin-auto-import/vite'
import Components from 'unplugin-vue-components/vite'
import { ElementPlusResolver } from 'unplugin-vue-components/resolvers'

plugins: [
  vue(),
  AutoImport({ resolvers: [ElementPlusResolver()] }),
  Components({ resolvers: [ElementPlusResolver()] }),
]
```

迁移时同步移除 `main.ts` 的全量 `app.use(ElementPlus)` 与全量 CSS 引入；
主题色 CSS 变量覆盖方式（见第 5 节）不受影响。

---

## 2. 目录结构

```
agent-web/
├── .env.docker            # Docker 网络内的代理目标配置（BACKEND_URL=http://app:8000）
├── index.html             # SPA 入口 HTML
├── package.json           # 依赖与 scripts（npm，engines node>=20）
├── package-lock.json      # 锁文件（必须提交）
├── tsconfig.json          # 应用 TS 配置（strict，严格 JSON 无注释）
├── tsconfig.node.json     # vite.config.ts 的 TS 配置（project reference）
├── vite.config.ts         # Vite 配置：@ 别名 + /api 代理
├── env.d.ts               # Vite 客户端类型声明
└── src/
    ├── main.ts            # 应用入口：全量注册 Element Plus + router
    ├── App.vue            # 根布局：左侧边栏导航 + 右侧内容卡片
    ├── router/
    │   └── index.ts       # 路由表：/chat /agent /skill /mcp /llm，全懒加载
    ├── styles/
    │   └── index.css      # 全局样式：Element Plus 主题变量 + 卡片/页面骨架样式
    ├── types/
    │   └── index.ts       # 集中存放的共享类型定义
    ├── utils/
    │   └── request.ts     # 统一 axios 实例（baseURL /api/v1 + 信封解包拦截器 + get/post/put/del）
    └── views/             # 页面级组件，按业务域分目录，一域一文件
        ├── agent/AgentList.vue      # /agent  Agent 管理（占位）
        ├── chat/ChatView.vue        # /chat   对话（占位）
        ├── mcp/McpList.vue          # /mcp    MCP 管理（占位）
        ├── provider/ProviderList.vue# /llm    模型服务（LLM provider 管理，占位）
        └── skill/SkillList.vue      # /skill  技能管理（占位）
```

职责约定：

- `views/`：仅放路由直接引用的页面组件；按业务域（agent/chat/mcp/provider/skill）建子目录。
- `router/`：路由表集中定义，路由 `meta.title` 用于页面标题语义。
- `types/`：共享/接口类型集中于此，不散落在各组件内。
- `utils/`：与业务无关的基础工具；所有 HTTP 请求统一走 `utils/request.ts`。
- `styles/`：全局样式与主题变量；组件私有样式用 `<style scoped>`。

### 2.1 路由表

| 路径 | 组件 | meta.title |
|---|---|---|
| `/` | 重定向 → `/chat` | — |
| `/chat` | `views/chat/ChatView.vue` | 对话 |
| `/agent` | `views/agent/AgentList.vue` | Agent 管理 |
| `/skill` | `views/skill/SkillList.vue` | 技能管理 |
| `/mcp` | `views/mcp/McpList.vue` | MCP 管理 |
| `/llm` | `views/provider/ProviderList.vue` | 模型服务（LLM provider 管理） |

全部路由组件懒加载（`() => import('@/views/...')`），history 模式为 `createWebHistory()`。

---

## 3. 关键配置：BACKEND_URL 代理机制

### 3.1 机制

`vite.config.ts` 以函数式配置，在 `/api` 前缀代理上取环境变量 `BACKEND_URL`：

```ts
const fileEnv = loadEnv(mode, process.cwd(), '')
const env = {
  ...fileEnv,
  ...Object.fromEntries(
    Object.entries(process.env).filter(([, v]) => v !== undefined),
  ),
}

server: {
  port: 5173,
  proxy: {
    '/api': {
      target: env.BACKEND_URL || 'http://localhost:8000',
      changeOrigin: true,
    },
  },
}
```

要点：

- `loadEnv` 第三个参数为 `''`（**全量加载**，含非 `VITE_` 前缀变量）。
- **取值优先级：shell 环境变量 > `.env` 文件 > 默认值**（与 dotenv 惯例一致，
  `process.env` 覆盖在 `loadEnv` 结果之上）。
- **本地开发**：无 `BACKEND_URL` 时默认 `http://localhost:8000`（对应 `make dev` 起的后端）。
- **Docker 环境**：`agent-web/.env.docker` 提供 `BACKEND_URL=http://app:8000`——`app` 是
  `docker-compose.yml` 中的后端服务名（compose 网络内 DNS），`npm run dev:docker` 通过
  `vite --mode docker` 加载该文件。**该 mode 供 vite 运行在 Docker 网络内（未来 web
  容器化）时使用**；宿主机无法解析 `app`，宿主机开发（含 `make stack-up` 场景——compose
  已发布 `8000:8000`）直接用 `make web-dev`（默认 `localhost:8000`）即可。
- `changeOrigin: true`：改写 Host 头以匹配目标服务。

### 3.2 代理为何不做 rewrite

后端路由前缀为 `API_V1_STR = /api/v1`（`app/core/config.py`，默认值 `/api/v1`），
前端 axios `baseURL` 同为 `/api/v1`（`src/utils/request.ts`）。请求路径天然携带 `/api` 前缀，
代理原样转发即可，**无需 rewrite**。

> **⚠️ 变更约束**：若运维通过环境变量修改后端 `API_V1_STR`，必须**同步**以下三处：
> 1. `agent-web/vite.config.ts` 的代理匹配前缀（当前为 `/api`）；
> 2. `agent-web/src/utils/request.ts` 的 `baseURL`（当前为 `/api/v1`）；
> 3. 未来生产 nginx 反代的 location 规则（见第 7 节预留方案）。
> 三处不一致将导致前端请求 404，且不会有任何编译期报错。

---

## 4. 启动与开发命令

前端命令（在 `agent-web/` 目录下，npm）：

| 命令 | 说明 |
|---|---|
| `npm install` | 安装依赖（CI/首次建议 `npm ci`） |
| `npm run dev` | 本地开发服务器（端口 5173，代理 `/api` → localhost:8000） |
| `npm run dev:docker` | `--mode docker`（代理 → `http://app:8000`）；仅供 vite 运行在 Docker 网络内（未来 web 容器化）时使用，宿主机无法解析 `app` |
| `npm run build` | 类型检查 + 生产构建（`vue-tsc -b && vite build`） |
| `npm run type-check` | 仅类型检查（`vue-tsc -b`） |
| `npm run preview` | 本地预览生产构建产物 |

Makefile 封装目标（仓库根目录）：

| 目标 | 等价命令 |
|---|---|
| `make web-install` | `cd agent-web && npm ci` |
| `make web-dev` | `cd agent-web && npm run dev` |
| `make web-dev-docker` | `cd agent-web && npm run dev:docker`（仅供 vite 运行在 Docker 网络内时使用） |
| `make web-build` | `cd agent-web && npm run build` |
| `make web-clean` | 删除 `agent-web/node_modules`、`agent-web/dist` 与 `*.tsbuildinfo` 缓存 |

典型开发流：

```bash
# 后端（Docker 全栈，compose 已发布 8000:8000）+ 前端（宿主机）
make stack-up ENV=development
make web-install
make web-dev            # 默认代理 localhost:8000，浏览器访问 http://localhost:5173
                        # 注意：宿主机不要用 web-dev-docker（无法解析 compose 网络内的 `app`）

# 后端（本机 uvicorn）+ 前端（本机）
make dev
make web-dev
```

---

## 5. UI 约定：蓝白卡片式布局

### 5.1 主题色

全局样式在 `src/styles/index.css`，通过覆盖 Element Plus CSS 变量实现主题定制：

```css
:root {
  --el-color-primary: #2f6bff;          /* 品牌主色 */
  --el-color-primary-light-3: #6692ff;
  --el-color-primary-light-5: #94b1ff;
  --el-color-primary-light-7: #c0d1ff;
  --el-color-primary-light-8: #d7e1ff;
  --el-color-primary-light-9: #edf2ff;
  --el-color-primary-dark-2: #1f52d6;

  --app-bg: #f4f7fd;        /* 页面底色（浅蓝灰） */
  --app-border: #e6ecf7;    /* 边框色 */
  --app-ink: #101c33;       /* 主文本色 */
  --app-ink-muted: #5b6b85; /* 次级文本色 */
}
```

约定：**新增颜色一律扩展 CSS 变量，禁止在组件内硬编码品牌色系十六进制值**
（`#2f6bff` 及其派生色只能出现在 `styles/index.css` 与品牌渐变定义处）。

### 5.2 布局规范

- **骨架布局**（`App.vue`）：固定左侧边栏（宽 232px，白底，品牌区 + `el-menu` 导航 + 页脚）
  + 右侧内容区（`--app-bg` 底色，内嵌单个白色内容卡片）。
- **卡片规范**：
  - 通用卡片用 `.app-card` 类：白底、`1px solid var(--app-border)`、圆角 12px、
    轻投影 `0 1px 2px rgba(16,28,51,0.04)`；
  - `el-card` 内容卡片统一挂 `.app-content-card` 类（同规格，body padding 24px）。
- **页面骨架**：页面级视图使用 `.page-view` 结构（header：eyebrow + title + desc；body 区
  最小高 320px），样式类已在 `styles/index.css` 提供，新页面直接复用。
- 菜单激活态：文字色 `#2f6bff`、背景 `--el-color-primary-light-9`。
- 组件私有样式写 `<style scoped>`，跨组件共享样式提升到 `styles/index.css`。

---

## 6. 命名与代码风格约定

- **文件命名**：小写 + 连字符/下划线风格仅用于配置与工具文件（`request.ts`、`index.css`）；
  Vue 组件文件一律 **PascalCase**（`ChatView.vue`、`ProviderList.vue`）。
- **组件命名**：页面组件以业务域命名（列表页 `XxxList.vue`，视图页 `XxxView.vue`），
  存放于 `src/views/<业务域>/`。
- **类型**：共享类型集中在 `src/types/`；组件私有类型就近定义。所有函数签名带类型标注。
  `ApiResponse<T>`（后端统一响应信封 `{ code, message, data }`）即定义于此。
- **HTTP 请求**：统一使用 `src/utils/request.ts` 导出的 axios 实例与泛型方法，**禁止**在组件内
  `new` 独立 axios 实例或直接 `fetch`。该实例已配置 `baseURL: '/api/v1'`、超时 15s，
  响应拦截器按后端统一响应信封 `{code, message, data}`（code 数值与 HTTP status 一致）解包：
    信封判定要求**三字段齐全**（`code` 为 number、`message` 为 string 且 `data` 键存在，值可为
    `null`），避免将形状碰撞的裸响应（如 `{code, message}` 无 `data` 键）误判解包出 `undefined`；
  - **成功**：code 为 2xx（含创建类端点的 `code=201`）时自动解包返回 `data`，
    判断逻辑为 `code >= 200 && code < 300`，不得写死仅 200；
  - **非 2xx**：`ElMessage.error(message)` 提示后 reject；
  - **错误分支**（HTTP 异常/网络错误）：兼容新信封形态（取 `message`）与旧 FastAPI
    `{ detail }` 形态的回退过渡；
  - **豁免端点**（`/health`、SSE 流等裸响应）：非信封响应原样透传；
  - 另导出泛型方法 `get/post/put/del`，返回值即解包后的 `data`（DELETE 的 `data` 为
    `null`，建议以 `void` 承接）。token 注入与 401 处理留有 TODO 占位，待认证体系接入后补齐。
- **别名**：源码内引用一律用 `@/` 别名（vite alias 与 tsconfig `paths` 双重配置），
  不用相对路径跨层级。
- **语言**：UI 文案使用中文；代码标识符使用英文。

---

## 7. 严格 JSON 约束

仓库 pre-commit 配置 `check-json` 钩子（`.pre-commit-config.yaml`，仅豁免 `.vscode/`），
因此 `agent-web/` 下所有 JSON 文件——**`tsconfig.json`、`tsconfig.node.json`、
`package.json`、`package-lock.json`**——必须是严格 JSON：

- **不得包含注释**（`//`、`/* */`）；
- **不得包含尾随逗号**。

Vite 脚手架生成的 tsconfig 常带注释（JSONC 风格），本项目已去除；后续修改 tsconfig
时严禁加回注释。需要注释说明的配置意图，写到本文档。

---

## 8. 骨架期红线

- 不实现任何业务逻辑：视图仅占位，请求层仅保留拦截器骨架。
- 不擅自引入状态管理库（Pinia 等）、SSR 框架（Nuxt）、monorepo 工具链
  （pnpm workspace / turborepo 等）——需经方案评审后再演进。
- 不在 JSON 配置文件中写注释（见第 7 节）。
- 不引入未经评估的第三方 UI 库与工具库；Element Plus 已覆盖常见中后台需求。

---

## 9. Docker / CI 接入预留方案（仅设计，未落地）

以下为未来将 agent-web 容器化接入 `docker-compose.yml` 与 CI 的设计预案，当前**不落地**。

### 9.1 镜像构建：多阶段 Dockerfile（示意）

```dockerfile
# stage 1: 构建
FROM node:20-alpine AS build
WORKDIR /web
COPY agent-web/package*.json ./
RUN npm ci
COPY agent-web/ ./
RUN npm run build          # vue-tsc -b && vite build

# stage 2: 托管
FROM nginx:alpine
COPY --from=build /web/dist /usr/share/nginx/html
COPY deploy/nginx.conf.template /etc/nginx/templates/default.conf.template
```

要点：构建产物仅 `dist/` 静态文件；生产镜像不含 node_modules 与源码。

### 9.2 nginx 反代：变量式 proxy_pass + compose DNS resolver

nginx 默认在**启动时**解析 `proxy_pass` 中的域名，若后端容器尚未就绪会导致 web 容器
启动失败。规避方式：用变量 + 显式指定 compose 内置 DNS（`127.0.0.11`），
将解析推迟到请求时：

```nginx
location /api/ {
    resolver 127.0.0.11 valid=10s ipv6=off;
    set $backend "http://app:8000";
    proxy_pass $backend;      # 变量式：请求期解析，后端未就绪时不阻塞 nginx 启动
    proxy_set_header Host $host;
}
```

配合 `envsubst`（nginx 官方镜像的 `/docker-entrypoint.d/20-envsubst-on-templates.sh`）
把 `$backend` 主机做成模板变量，即可按环境注入。**注意**：使用变量式 `proxy_pass` 时，
location 前缀不再自动拼接到上游 URL，模板中需显式带上原路径（如
`proxy_pass $backend$request_uri;`）。

### 9.3 compose 接入约束

- web 服务必须加入 compose 现有 `monitoring` 网络（`docker-compose.yml` 中所有服务
  共用的 bridge 网络），否则无法通过服务名 `app` 访问后端；
- web 容器的 `BACKEND_URL`/nginx 模板变量与 `agent-web/.env.docker` 保持同一语义
  （默认 `http://app:8000`）。

### 9.4 后端 Dockerfile 不改造

现有后端 `Dockerfile` 为**纯 Python 单阶段构建**（python:3.13-slim + uv 安装依赖），
前端接入不对其做任何改造；前端静态资源由独立 nginx 容器托管，与后端容器解耦。

### 9.5 CI 预留

CI 流水线可增加前端 job：`npm ci` → `npm run type-check`（或 `npm run build`）→
（可选）构建镜像并推送。构建失败即阻断合并，与后端 `make check` 并列。
