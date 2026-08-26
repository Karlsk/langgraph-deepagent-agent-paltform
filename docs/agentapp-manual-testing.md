# AgentApp 全功能手动测试指南

本文档指导你从零开始手动验证 AgentApp（Agent 资产管理 + 对话）全链路功能：
构建启动 → 认证 → Skill → MCP → SubAgent → AgentApp → Chat（已退役） → HIL（已退役，人工介入）。

> **G1/G2 时效性标注**：Phase 1 G1 单层认证改造后，`POST /auth/session` 与 `/chatbot/*`
> 已退役（调用返回 404），对话运行时待 G3 阶段在新认证面上重建——第 7/8 节已改为退役说明；
> 资产端点直接使用**用户 token**，全文不再有「会话 token」。

> **核实基线**：本文所有端点、环境变量与命令均对照以下源码核实过：
> `app/api/v1/api.py`（路由注册）、`app/api/v1/auth.py`、`app/api/v1/agent_assets_common.py`、
> `app/api/v1/subagents.py`、`app/api/v1/skills.py`、`app/api/v1/apps.py`、`app/api/v1/mcp_servers.py`、
> `app/api/v1/providers.py`、`app/api/v1/chatbot.py`、`app/schemas/agent_apps.py`、`app/schemas/auth.py`、
> `app/schemas/chat.py`、`app/core/config.py`、`Makefile`、`Dockerfile`、`docker-compose.yml`、
> `scripts/docker-entrypoint.sh`。
> 若代码变更，请以源码为准。

## 全文约定

- 所有 API URL 使用 `http://localhost:8000/api/v1/...`（路由前缀来自 `settings.API_V1_STR`，默认值 `/api/v1`）。
- curl 示例统一使用 `$BASE` 与 `$TOKEN` 两个 shell 变量，先执行：

```bash
export BASE=http://localhost:8000/api/v1
export EMAIL="tester@example.com"
export PASSWORD="Test@1234"   # 满足密码强度要求：>=8 位，含大写/小写/数字/特殊字符
```

- **G1 单层认证**（Phase 1 G1，`auth.py` 现只有 register/login/refresh/logout）：
  - `POST /auth/register`、`POST /auth/login` 返回**用户 token**（扁平 `LoginResponse`，含 `access_token` + `refresh_token`）；
  - `/subagents/*`、`/skills/*`、`/apps/*`、`/mcp-servers/*`、`/providers/*`、`/tools/*` 全部端点依赖 `get_current_user`，**直接使用用户 token**；
  - 历史的 `POST /auth/session`（换取会话 token）已退役，调用返回 404 信封；`/chatbot/*` 同步退役（见第 7 节）。
  第 2 节给出 `$TOKEN` 的取值步骤。
- **统一响应信封**：除豁免端点外，所有端点返回 `{code, message, data}` 信封——`code` 数值与
  HTTP status 完全一致（资产创建端点 HTTP 201 且 `code=201`；`POST /auth/register`
  为 HTTP 200 且 `code=200`；DELETE 成功 `data=null`）。
  422 分两种形态：**请求体校验失败**为 `{code:422, message:"Validation error", data:[错误列表]}`；
  **业务规则拒绝**（如重名、immutable name）为 `{code:422, message:"<错误文案>", data:null}`。
  其余错误信封为 `{code:<状态码>, message:"<错误文案>", data:null}`。
  **豁免端点（仍返回裸响应）**：`GET /`、`GET /health`、`GET /api/v1/health`。
  本文所有取值命令均按信封路径提取（如 `["data"]["access_token"]`）。
- 标注 **⚠️ 需外部资源 / 会消耗 token** 的步骤需要真实 LLM API Key（或真实 MCP server 进程）。

---

## 0. 前置条件

### 0.1 环境要求

| 依赖 | 说明 |
|---|---|
| Docker + Docker Compose | `docker-compose.yml` 使用 compose `version: '3.8'` 语法 |
| 端口 | `8000`（API）、`15432`（PostgreSQL，宿主机端口；compose 映射 `15432:5432`，容器内/compose 网络内仍为 `5432`）、`6379`（Valkey，可选）；全栈模式另占 `9090/3000/8080` |
| LLM API Key | 任何对话/生成/测试步骤都需要（`OPENAI_API_KEY`）**⚠️ 需外部资源** |

### 0.2 环境变量清单（docker 环境必须配置的项加 ★）

以下清单对照 `.env.example` 与 `scripts/docker-entrypoint.sh`（entrypoint 会**硬性校验**
`JWT_SECRET_KEY` 与 `OPENAI_API_KEY`，缺失直接退出，exit 1）：

| 变量 | docker 必需 | 默认值（config.py） | 说明 |
|---|---|---|---|
| `APP_ENV` | ★ | development | 决定加载 `.env.<APP_ENV>`；compose 构建参数同名 |
| `OPENAI_API_KEY` | ★ | 空 | LLM 调用；entrypoint 强校验。**Agent 链路中仅作启动时 default provider/model 对的一次性种子来源**（见 0.5 节） |
| `OPENAI_BASE_URL` | 可选 | 空 | 自定义 LLM 端点（OpenAI 兼容代理）；同上仅作 default 对种子，入库后以 `/providers` API 管理（见 0.5 节） |
| `OPENAI_API_BASE` | 可选 | 空 | 与上一行**同值**；系统级 langchain 层读取（见 0.5 节） |
| `JWT_SECRET_KEY` | ★ | compose 中有兜底值 | entrypoint 强校验，生产务必自行设置 |
| `POSTGRES_HOST` | ★ | localhost | 容器内必须填 `db`（compose 服务名） |
| `POSTGRES_PORT` / `POSTGRES_DB` / `POSTGRES_USER` / `POSTGRES_PASSWORD` | ★ | 5432 / food_order_db | db 容器初始化也读取这些值 |
| `DEFAULT_LLM_MODEL` | 建议 | gpt-5-mini | 默认模型 |
| `DATA_ROOT` | 建议 | ./data | 三级 Workspace 根目录（`global/` + `agents/`，容器内相对 `/app` 解析为 `/app/data`）；`SKILLS_ROOT` 已废弃（G2），仅单独设置时回退为其父目录 |
| `MCP_STDIO_ALLOWED_COMMANDS` | 可选 | `python,node,uvx,npx` | stdio 型 MCP server 命令白名单（逗号分隔） |
| `LANGFUSE_TRACING_ENABLED` | 可选 | true | 无 Langfuse 账号时建议设 `false` 避免初始化噪音 |
| `SESSION_NAMING_ENABLED` | 可选 | true | 会话自动命名（额外 LLM 调用） |
| `RATE_LIMIT_*` | 可选 | 见下 | 各端点限流；手动测试建议放宽 |
| `VALKEY_HOST` | 可选 | 空 | 置空即禁用缓存（compose 仍会启动 valkey 容器但不被使用） |

内置限流默认值（`config.py`）：`chat=30/min`、`chat_stream=20/min`、`messages=50/min`、
`register=10/hour`、`login=20/min`、`subagent/skill/agent_app/mcp_server/provider/model_config/tools_catalog=60/min`、
`subagent_test=5/min`、`skill_generate=5/min`。均可用同名大写环境变量覆盖，如
`RATE_LIMIT_CHAT`、`RATE_LIMIT_SUBAGENT_TEST` 等。

### 0.3 可直接复制的 `.env.development` 模板

```bash
APP_ENV=development
PROJECT_NAME="Web Assistant"
VERSION=1.0.0
DEBUG=true
API_V1_STR=/api/v1

# ★ LLM（必填真实值）
# 以下变量在 Agent 链路中仅作启动时 default provider/model 对的一次性种子（入库后以 /providers API 管理，见 0.5 节）；
# 系统级 LLM 调用（会话命名、skill 草稿等）仍直接读取这些变量。
OPENAI_API_KEY=<your-llm-api-key>
DEFAULT_LLM_MODEL=gpt-5-mini
DEFAULT_LLM_TEMPERATURE=0.2
SESSION_NAMING_ENABLED=true
# 无法直连 OpenAI 官方端点时，改用 OpenAI 兼容代理（用法与限制见 0.5 节）：
# OPENAI_BASE_URL=https://your-proxy.example.com/v1
# OPENAI_API_BASE=https://your-proxy.example.com/v1

# ★ JWT（必填）
JWT_SECRET_KEY=<your-jwt-secret-key>
JWT_ALGORITHM=HS256
JWT_ACCESS_TOKEN_EXPIRE_DAYS=30

# ★ 数据库（POSTGRES_HOST 在 docker 内必须是 db）
POSTGRES_HOST=db
POSTGRES_PORT=5432
POSTGRES_DB=mydb
POSTGRES_USER=myuser
POSTGRES_PASSWORD=<your-db-password>
POSTGRES_POOL_SIZE=5
POSTGRES_MAX_OVERFLOW=10

# 三级 Workspace 根目录（容器内相对 /app 解析为 /app/data；SKILLS_ROOT 已废弃）
DATA_ROOT=./data

# MCP stdio 命令白名单（默认即此值，显式写出便于确认）
MCP_STDIO_ALLOWED_COMMANDS=python,node,uvx,npx

# Langfuse：无账号时关闭
LANGFUSE_TRACING_ENABLED=false

# 手动测试建议放宽限流
RATE_LIMIT_DEFAULT="10000 per day,5000 per hour"
RATE_LIMIT_CHAT="100 per minute"
RATE_LIMIT_CHAT_STREAM="100 per minute"
RATE_LIMIT_MESSAGES="200 per minute"
RATE_LIMIT_LOGIN="100 per minute"
RATE_LIMIT_SUBAGENT_TEST="20 per minute"
RATE_LIMIT_SKILL_GENERATE="20 per minute"

# 日志
LOG_LEVEL=DEBUG
LOG_FORMAT=console
```

> 注意：`.env.example` 中 `POSTGRES_HOST=db` 仅适用于 docker 网络内；
> 若改为本地直跑（`make dev`）需改为 `localhost`。

### 0.4 内置工具清单（供 `allowed_tools` 选择）

来自 `app/core/langgraph/tools/__init__.py`（`tools` 列表即工具目录中 `source="builtin"` 的来源）：

| 工具名 | 来源文件 | 说明 |
|---|---|---|
| `duckduckgo_results_json` | `duckduckgo_search.py` | DuckDuckGo 搜索（`DuckDuckGoSearchResults` 实例名，返回最多 10 条结果） |
| `ask_human` | `ask_human.py` | HIL 工具：调用 `interrupt()` 暂停执行向用户提问 |

后续创建 AgentApp / SubAgent 时的 `allowed_tools` 只能从「内置工具（裸名）+ 已注册 MCP server 暴露的
工具（`{server}__{tool}` 命名空间名）」中选取，发布（publish）时会对照实时工具目录做白名单校验
（越界返回 422）。MCP 专项测试见 [mcp-manual-testing.md](mcp-manual-testing.md)。

### 0.5 自定义 LLM 端点（用不了 GPT 官方时）

> **核实结论（Provider 体系改造后）**：LLM 配置分两条链路——
> - **Agent 资产链路（AgentApp / SubAgent / 对话）已 DB 化**：连接配置存于 `provider` 表、
>   模型清单存于 `model_config` 表，经 `/providers` API 管理；AgentApp/SubAgent 的 `model` 字段是
>   **`provider/model` 引用**（NULL 解析为 `default/default`）。`.env` 的 LLM 变量**仅作启动时 default 对的一次性种子**。
> - **系统级链路（会话命名、skill 草稿生成、LLMService 循环回退、evals）仍走 env**：
>   `app/services/llm/registry.py` 的 `ChatOpenAI` 未显式传 `base_url`，依赖链自动回退读环境变量——
>   langchain-openai 读 `OPENAI_API_BASE`，openai SDK 读 `OPENAI_BASE_URL`。

#### a. Agent 链路：经 `/providers` API 配置（推荐方式）

首次启动时 bootstrap 会 insert-if-missing `name=default` 的 Provider + `name=default` 的 ModelConfig，种子来源为：
`OPENAI_API_KEY`（auth_config.api_key）、`OPENAI_BASE_URL` 环境变量（base_url，未设则为空）、
`DEFAULT_LLM_MODEL`（model_id）、`DEFAULT_LLM_TEMPERATURE`（extra_params.temperature）。
**注意：种子不含 `max_tokens`**（刻意保持为空，走 provider 默认值——进程级预算不应冻结入库）；
如需对 Agent 链路限额，用 `PATCH /providers/default/models/default` 显式设置 `extra_params.max_tokens`。
`MAX_TOKENS` 环境变量**不种子到该行**，仅作用于系统级链路（`app/services/llm/registry.py`
以 `max_completion_tokens` 下发）及消息历史裁剪预算（`app/utils/graph.py`）。
**仅 insert-if-missing，不覆盖已存在行**——改 `.env` 后重启**不会**更新库里配置。

切换到 OpenAI 兼容代理（例如 MiniMax-M3）：

```bash
# 方式一：直接 PATCH default 对（NULL 引用的应用随之生效）
curl -s -X PATCH "$BASE/providers/default" \
  -H "Authorization: Bearer $TOKEN" \
  -H "Content-Type: application/json" \
  -d '{
    "auth_config": {"api_key": "<your-proxy-api-key>"},
    "base_url": "https://your-proxy.example.com/v1"
  }'
curl -s -X PATCH "$BASE/providers/default/models/default" \
  -H "Authorization: Bearer $TOKEN" \
  -H "Content-Type: application/json" \
  -d '{"model_id": "MiniMax-M3"}'

# 方式二：新建独立 provider + model，再让 AgentApp 显式引用 "provider/model"
（完整 CRUD 步骤见 5.5 节）
```

**改 env 后想让 default 对同步更新**：`default/default` 是设计约束下**无条件禁删**的对
（`DELETE /providers/default` 与 `DELETE /providers/default/models/default` 恒返回 422，
见 5.5 节负向用例），不能走「删除 + 重启重建」路径；正确做法是直接 PATCH 覆盖（同上方方式一）。

#### b. 系统级链路：env 回退（会话命名 / skill 草稿等）

系统级调用在 `.env.development` 配置两行同值变量后重启生效：

```bash
OPENAI_API_KEY=<your-proxy-api-key>
OPENAI_BASE_URL=https://your-proxy.example.com/v1
OPENAI_API_BASE=https://your-proxy.example.com/v1
```

改完重启容器（`make docker-up ENV=development`）；本地直跑则 `make dev` 重启。

**代理兼容性注意事项**（仅约束系统级链路；Agent 链路的 model_id 完全由你配置，无此限制）：

- **a. 模型名固定**：系统级发往端点的 `model` 名固定为 registry 硬编码的 4 个——
  `gpt-5-mini` / `gpt-5` / `gpt-5.4` / `gpt-5.4-nano`。`DEFAULT_LLM_MODEL` 写成清单外的
  其他名字会在系统级链路**静默降级**到清单第一个模型（当前即 `gpt-5-mini`）：降级发生在
  `LLMService` 初始化时，发现 `DEFAULT_LLM_MODEL` 不在清单内即记录
  `default_model_not_found_using_first` 警告并改用清单首个模型；
  Agent 链路则以 ModelConfig 的 `model_id` 原样下发，不受此清单约束。
- **b. 专有请求参数**：registry 给实例传了 OpenAI 专有的 `reasoning`（如 `{"effort": "low"}`）
  与 `max_completion_tokens` 参数，会随系统级请求体发往端点；对请求字段严格校验的代理可能拒绝。
- **c. `.env.example` 示例值陷阱**：`.env.example` 中 Atlas Cloud 注释段的
  `DEFAULT_LLM_MODEL=deepseek-ai/deepseek-v4-pro` 示例值**不在系统级硬编码清单内**，照抄会使
  default 模型的种子 model_id 为清单外名字（Agent 链路正常、系统级链路静默降级），请注意甄别。

> 另注：代理的 key 在 Agent 链路填入 Provider 的 `auth_config.api_key` 字段（任何响应/日志只返回掩码
> `****`+尾 4 位）；系统级链路直接填 `OPENAI_API_KEY`（entrypoint 强校验非空）。

---

## 1. 构建与启动

### 1.1 准备 env 文件并构建镜像

**目的**：生成 `.env.development` 与 docker 镜像。

```bash
# 若还没有 .env.development，可从模板复制后按 0.3 修改
cp .env.example .env.development

# 构建镜像（内部调用 scripts/build-docker.sh，会校验 env 存在并掩码打印敏感值）
make docker-build ENV=development
```

**预期**：输出 `Docker image fastapi-langgraph-template:development built successfully`。

**失败排查**：
- `.env.development not found`：脚本会自动从 `.env.example` 复制并提醒你修改，改完重跑即可。
- 构建慢/失败：多为拉取基础镜像或 apt 源问题，与代码无关。

### 1.2 启动服务

**目的**：启动 DB + API（最小集）或全栈。

```bash
# 方式 A：仅 DB + app（推荐手动测试用）
make docker-up ENV=development

# 方式 B：全栈（含 Prometheus/Grafana/Valkey/cadvisor）
make stack-up ENV=development
```

**预期**：`db` 与 `app` 容器进入 running/healthy。

**失败排查**：
- `Environment file .env.development not found`：Makefile 的 `load_env_file` 会直接报错，先创建该文件。
- db 反复重启：检查 `POSTGRES_*` 变量是否与已有数据卷 `postgres-data` 中旧库的初始化参数冲突
  （首次启动后修改 POSTGRES_USER/DB 不会重建已有卷，需 `docker volume rm` 清理，见第 9 节）。

### 1.3 数据库迁移（`make docker-up` 已内置自动执行）

**目的**：在 app 启动前创建/升级全部数据表。

> **核实结论**：`docker-compose.yml` 新增了 `migrate` 一次性服务（`restart: "no"`），
> 在 `db` 容器 healthy 后跑 `alembic upgrade head`，随后才启动 `app`
> （依赖条件 `service_completed_successfully`）。
> **`make docker-up` / `make stack-up` 现在是一条龙命令**：db → migrate → app，
> 不需要也不应该再手动跑迁移。`scripts/docker-entrypoint.sh` 与 `app/main.py` 启动流程中
> 都没有 `create_all`/alembic 调用，所有 DDL/DML 都通过 `migrate` 一次性容器驱动。

如果只为了检查迁移历史或手动 downgrade，仍可使用：

```bash
make docker-migrate-history ENV=development   # 查看迁移历史确认到 head
make docker-migrate-downgrade ENV=development  # 回滚一次
```

**预期**：`make docker-up` 后 `docker compose ps` 应能看到 `migrate` 容器 Exit 0、`app` 容器
running；`alembic upgrade head` 依次执行 `b25d38b0cd7c_initial_schema`、
`e4f1a8c2b9d3_agent_assets`、`f3a1b7c9d204_llm_config`、`a3f7e9b1c852_provider_models`，无报错退出。

> 存量库升级说明：`a3f7e9b1c852_provider_models` 建 `provider` / `model_config` / `provider_health`
> 三表，把每行存量 llm_config 拆为 provider + model_config，并把 `agent_app.model` /
> `subagent_config.model` 的非 NULL 值 `X` 改写为 `X/X`（字段语义切换为 `provider/model` 引用，
> NULL 保持 NULL），最后 drop `llm_config` 表。default 对仍由首次启动的 bootstrap 创建。

**失败排查**：
- 连不上 db：确认容器已 healthy（`docker compose ps`），且 `POSTGRES_HOST=db`。
- 迁移报已存在：之前跑过部分迁移，用 `make docker-migrate-history` 对账。

### 1.4 健康检查与启动日志确认

**目的**：确认 API 可用且 AgentApp 预热成功。

```bash
# 健康检查端点（main.py 直接挂在根路径；api_router 下另有 /api/v1/health）
curl -s http://localhost:8000/health
curl -s $BASE/health

# 根端点（返回 name/version/status/environment/swagger_url(/docs)/redoc_url(/redoc)）
curl -s http://localhost:8000/
```

**预期**：`/health` 返回含 `"status"` 的 JSON；`/api/v1/health` 返回
`{"status": "healthy", "version": "1.0.0"}`。

再看启动日志（来自 `app/main.py` 的 lifespan 预热流程）：

```bash
make docker-logs ENV=development    # 或直接：docker compose logs -f app
```

**预期日志关键事件**（structlog 事件名）：
- 默认 AgentApp 引导成功（`ensure_default_agent_app`，对应日志如 `default_agent_app_*`）；
- `mcp_tools_pre_warmed`（MCP 工具预热成功；失败会记 `mcp_tools_pre_warm_failed_degraded`，不阻断启动）；
- `agent_apps_warmed`，带 `count=<已发布应用数>`（含启动前已 publish 的 AgentApp 预热）。

**失败排查**：
- 容器启动即退出：看日志开头是否有
  `ERROR: The following required environment variables are missing`（`JWT_SECRET_KEY` / `OPENAI_API_KEY` 缺失）。
- 端点 500 且日志有数据库错误：回到 1.3 跑迁移。

---

## 2. 认证

### 2.1 注册用户

**目的**：创建用户并拿到用户 token。

```bash
curl -s -X POST "$BASE/auth/register" \
  -H "Content-Type: application/json" \
  -d "{\"email\": \"$EMAIL\", \"password\": \"$PASSWORD\", \"username\": \"manual-tester\"}"
```

**预期**：HTTP 200 且 `code=200`，`data` 为扁平 `LoginResponse`：`data.access_token`、
`data.refresh_token`、`data.token_type="bearer"`、`data.expires_at`、`data.request_id`。
注意：G1 起**不再返回用户 id**；后续步骤（如 6.6 节）需要 `$USER_ID` 时，在 db 容器内查库：
`psql -U <POSTGRES_USER> -d <POSTGRES_DB> -c "SELECT id FROM \"user\" WHERE email='<email>';"`。

**失败排查**：
- 422（信封 `message="Validation error"`，`data` 为错误列表）：密码不满足强度（>=8 位、大小写、数字、特殊字符各至少一个）或 email 格式非法；
- 400，信封 `message` 为 `Email already registered`：换一个 email 或先清库；
- 429，信封 `message="Rate limit exceeded"`：`register` 默认限流 10 次/小时。

### 2.2 登录取 token

**目的**：验证表单登录（注意：**login 是 form 表单，不是 JSON**）。

```bash
curl -s -X POST "$BASE/auth/login" \
  -d "email=$EMAIL" \
  -d "password=$PASSWORD" \
  -d "grant_type=password"
```

**预期**：信封 `data` 为 `LoginResponse`：`data.access_token`、`data.refresh_token`、
`data.token_type="bearer"`、`data.expires_at`。

**失败排查**：401（信封 `message` 提示邮箱或密码错误）；400（`grant_type` 不是 `password`）。

```bash
# 将登录 token 记为用户 token
export USER_TOKEN=$(curl -s -X POST "$BASE/auth/login" \
  -d "email=$EMAIL" -d "password=$PASSWORD" -d "grant_type=password" | python3 -c 'import sys,json;print(json.load(sys.stdin)["data"]["access_token"])')
```

### 2.3 设定 `$TOKEN`（G1：无需会话 token）

> **退役说明**：历史的 `POST /auth/session`（用户 token → 会话 token 两步制）已在 Phase 1 G1
> 移除，现在调用返回 `404` 信封。所有资产端点直接接受用户 token。

```bash
# 直接把登录得到的用户 token 作为全文 $TOKEN
export TOKEN=$USER_TOKEN
```

**约定**：**全文 `$TOKEN` 一律指用户 token**，所有受保护请求都带
`-H "Authorization: Bearer $TOKEN"`。token 过期（默认 7 天）后用 `POST /auth/refresh`
（body 传 `refresh_token`）换新，或重新登录。

---

## 3. Skill 功能

所有端点位于 `$BASE/skills*`，需要用户 token（G1 单层认证，直接用 2.2 节登录 token）。
`name` 规则：`^[a-z0-9][a-z0-9_-]*$`，最长 64 字符，创建后不可改名。

**frontmatter 自动渲染（落盘格式）**：DB 只存纯正文（`body`），所有磁盘写入点
（创建 / PATCH / 自愈 / refresh / workspace-sync）都会自动渲染 YAML frontmatter
（`---\nname: ...\ndescription: ...\n---` + 正文）——这是 deepagents `SkillsMiddleware`
的运行时硬要求（无 frontmatter 的 SKILL.md 会被整个跳过）。`content_hash` 是**渲染后完整文件**
的 sha256；content 端点（3.3）仍只返回纯正文。验证落盘格式：

```bash
curl -s -X POST "$BASE/skills/refresh" -H "Authorization: Bearer $TOKEN"   # 全量刷新（存量旧文件也会补齐 frontmatter）
cat "$DATA_ROOT/global/skills/csv-report/SKILL.md"                          # 头部应见 ---/name:/description:/---
```

Agent 层 / User 层副本（§6.6）为字节级复制，同样自带 frontmatter。

### 3.1 创建全局 Skill（直接输入）

```bash
curl -s -X POST "$BASE/skills" \
  -H "Authorization: Bearer $TOKEN" \
  -H "Content-Type: application/json" \
  -d '{
    "name": "csv-report",
    "description": "生成 CSV 数据周报的技能",
    "body": "# CSV Report\n\n## 步骤\n1. 读取 CSV\n2. 汇总统计\n3. 输出 Markdown 周报"
  }'
```

**预期**：HTTP 201 且 `code=201`，`data` 为 `SkillRead`：`data.name`、`data.description`、
`data.content_hash`（非空 sha256）、`data.version=1`、`data.created_by`（你的 username）。

**失败排查**：422（信封 `data` 为错误列表）：`name` 非法或重名；`body` 为空。

### 3.2 列表与详情

```bash
curl -s "$BASE/skills" -H "Authorization: Bearer $TOKEN"
curl -s "$BASE/skills/csv-report" -H "Authorization: Bearer $TOKEN"

# 分页列表（可选）：page 从 1 开始，pageSize 上限 100，keyword 对 name 大小写不敏感模糊匹配
curl -s "$BASE/skills/page?page=1&pageSize=10&keyword=csv" -H "Authorization: Bearer $TOKEN"
```

**预期**：列表信封 `data` 为元数据数组（不含正文）；详情信封 `data` 同 `SkillRead`。
分页端点信封 `data` 为 `{items, total, page, pageSize}`（`items` 元素同 `SkillRead`）。
不存在时 404，信封 `message` 为 `skill '<name>' not found`。

### 3.3 读取正文

```bash
curl -s "$BASE/skills/csv-report/content" -H "Authorization: Bearer $TOKEN"
```

**预期**：信封 `data` 为 `{"name": "csv-report", "content": "<完整 SKILL.md 正文>"}`，与创建时 body 一致。

### 3.4 PATCH 更新

```bash
curl -s -X PATCH "$BASE/skills/csv-report" \
  -H "Authorization: Bearer $TOKEN" \
  -H "Content-Type: application/json" \
  -d '{"description": "生成 CSV 数据周报（v2，含图表建议）"}'
```

**预期**：信封 `data` 为更新后的 `SkillRead`，`data.version` 自增、`data.content_hash` 变化（若 body 有改动）。
`description` 与 `body` 可单独或同时更新；两者都不传返回 422，信封 `message` 为 `nothing to update`。

**负向用例（name 不可变）**：

```bash
curl -s -X PATCH "$BASE/skills/csv-report" \
  -H "Authorization: Bearer $TOKEN" \
  -H "Content-Type: application/json" \
  -d '{"name": "renamed"}'
```

**预期**：422 信封，`message` 为 `name is immutable and cannot be changed`。

### 3.5 LLM 草稿生成（⚠️ 需外部资源 / 会消耗 token）

```bash
curl -s -X POST "$BASE/skills/generate" \
  -H "Authorization: Bearer $TOKEN" \
  -H "Content-Type: application/json" \
  -d '{"description": "一个帮助撰写 Git 提交信息的技能", "hint": "遵循 Conventional Commits"}'
```

**预期**：信封 `data` 为 `{"draft": "<生成的 SKILL.md 草稿>"}`；**该接口只产出草稿，不落库**。
确认后可把 `draft` 内容作为 `body` 走 3.1 创建。

**失败排查**：500（信封 `message` 含脱敏摘要）说明 LLM 重试后仍失败，检查 `OPENAI_API_KEY`；
429（信封 `message="Rate limit exceeded"`）默认限流 5 次/分钟。

### 3.6 删除（建议放到第 9 节收尾时再测）

`DELETE $BASE/skills/csv-report` 会级联清理该 skill 的用户副本。由于第 6 节创建的
AgentApp 会引用该 skill，**先删 skill 会导致发布校验报 422**，所以请保留到收尾阶段再验证。

### 3.7 workspace-sync 目录对账（dry-run → apply → 幂等 → 导入）

对账语义：扫 `{DATA_ROOT}/global/skills/*/SKILL.md` 与 DB 逐条比对。DB 有 + 文件一致 →
`unchanged`；DB 有 + 文件漂移/缺失 → 以 DB 为准重写（`rewritten`）；DB 缺失 + 文件有 →
导入为新行（`imported`，`created_by="workspace-sync"`）；解析失败 / frontmatter name 与目录名冲突 /
超过 1 MiB 的文件 → 逐项降级记 `invalid`，不阻断其余。无删除对齐：目录多余文件不会被删行。

**第一步：dry-run（零副作用）**

```bash
curl -s "$BASE/skills/workspace-sync" -H "Authorization: Bearer $TOKEN"
```

**预期**：200 信封，`data` 为 `{items, scanned, unchanged, rewritten, imported, invalid}`；
`items` 每项 `{name, action, reason?}`（invalid 项 `name` 是 `<目录>/SKILL.md` 相对路径）。
调用前后文件与 DB 均无变化。

**第二步：放一个无 DB 行的手工文件，验证导入**

```bash
mkdir -p "$DATA_ROOT/global/skills/manual-import"
cat > "$DATA_ROOT/global/skills/manual-import/SKILL.md" <<'EOF'
# manual-import

手工放置的验证文件，无 frontmatter 也能导入。
EOF
curl -s -X POST "$BASE/skills/workspace-sync" -H "Authorization: Bearer $TOKEN"
```

**预期**：200 信封，`manual-import` 出现在 `imported`；`GET $BASE/skills/manual-import` 可查到新行，
`data.created_by == "workspace-sync"`、`data.description` 取正文首行非标题文本（「手工放置的验证文件，…」）；
磁盘文件被重写为规范化格式（头部见 frontmatter，`name: manual-import`）。
带 frontmatter 的文件同样支持导入（frontmatter 的 `name` 必须与目录名一致，否则记 `invalid`）。

**第三步：幂等验证（二次 apply 全 unchanged）**

```bash
curl -s -X POST "$BASE/skills/workspace-sync" -H "Authorization: Bearer $TOKEN"
```

**预期**：`data.imported == 0`、`data.rewritten == 0`，所有条目（含 `manual-import`）均为 `unchanged`。

**失败排查**：`invalid` 条目附 `reason`（如 frontmatter 与目录名冲突、文件超大小、YAML 解析失败），
按 `name` 指向的文件单独修复即可，不影响其余条目。

---

## 4. MCP 功能

所有端点位于 `$BASE/mcp-servers*` 与 `$BASE/tools/catalog`。

### 4.1 准备最小本地 stdio MCP server

将以下脚本保存为 **`app/tmp_mcp_demo_server.py`**（注意：必须放在仓库的 `app/` 目录下，
因为 compose 只把 `./app` 挂载进容器的 `/app/app`（另挂载 `./logs:/app/logs`），脚本才能在容器内被访问到；测试完请删除该文件）：

```python
"""Minimal stdio MCP server for manual testing (2 tools: add / echo)."""

from mcp.server.fastmcp import FastMCP

mcp = FastMCP("demo")


@mcp.tool()
def add(a: int, b: int) -> int:
    """Add two integers and return the sum."""
    return a + b


@mcp.tool()
def echo(text: str) -> str:
    """Echo the input text back prefixed with 'echo:'."""
    return f"echo: {text}"


if __name__ == "__main__":
    mcp.run()
```

**命令选择说明**：stdio 命令的 basename 必须落在 `MCP_STDIO_ALLOWED_COMMANDS`
（默认 `python,node,uvx,npx`）内；shell 解释器（bash/sh/zsh 等）无条件禁止；
`python -c` / `python -m`、`node -e/--eval` 等内联执行模式也被禁止。
容器内 `mcp` 库安装在项目 venv 中，因此命令使用 **`/app/.venv/bin/python`**
（basename 为 `python`，在白名单内），脚本路径为容器内路径 `/app/app/tmp_mcp_demo_server.py`。

### 4.2 注册 MCP server

```bash
curl -s -X POST "$BASE/mcp-servers" \
  -H "Authorization: Bearer $TOKEN" \
  -H "Content-Type: application/json" \
  -d '{
    "name": "demo-stdio",
    "transport": "stdio",
    "command": "/app/.venv/bin/python",
    "args": ["/app/app/tmp_mcp_demo_server.py"],
    "description": "本地演示用 stdio MCP server（add/echo）"
  }'
```

**预期**：HTTP 201 且 `code=201`，信封 `data` 为 `McpServerRead`（含 `data.content_hash`、`data.enabled=true`）。
注册时会**探活**该 server 并做工具名冲突校验（探活超时上限 30 秒；探活失败降级为跳过冲突检查，不阻断创建）。

**失败排查**：
- 422 信封（`message` 为错误文案或 `Validation error`）：`command is required for stdio transport` / `url must not be set for stdio transport`（transport 与字段搭配错误）；
- 422 信封 `message`：`stdio command 'xxx' is not in MCP_STDIO_ALLOWED_COMMANDS (...)`（命令不在白名单）；
- 422 信封 `message`：`... is a forbidden shell interpreter`（使用了 shell 解释器，见 4.5 负向用例）。

### 4.3 查看工具目录，确认 builtin + mcp 与 source 标签

```bash
curl -s "$BASE/tools/catalog" -H "Authorization: Bearer $TOKEN"
```

**预期**：信封 `data` 数组中同时包含：
- 内置工具（裸名）：`{"name": "duckduckgo_results_json", "source": "builtin"}`、
  `{"name": "ask_human", "source": "builtin"}`；
- MCP 工具（`{server}__{tool}` 命名空间名）：`{"name": "demo-stdio__add", "source": "mcp", "server": "demo-stdio"}`、
  `{"name": "demo-stdio__echo", "source": "mcp", "server": "demo-stdio"}`。

**失败排查**：看不到 mcp 工具时查 `make docker-logs`，关注 `mcp_tools_pre_warm_failed_degraded`
与 per-server 加载失败日志；常见原因是脚本路径在容器内不存在或 `mcp` 依赖缺失。

### 4.4 敏感值 `${ENV_VAR}` 占位符用法

MCP server 的 `env`（stdio）与 `headers`（http）中，凡是需要注入密钥的值**只能写成单个
`${ENV_VAR}` 占位符**（运行时从容器环境变量解析），明文 secret 会被接口层直接拒绝。示例：

```bash
# 正确：占位符（要求 app 容器内存在 DEMO_API_TOKEN 环境变量才能实际生效）
curl -s -X POST "$BASE/mcp-servers" \
  -H "Authorization: Bearer $TOKEN" \
  -H "Content-Type: application/json" \
  -d '{
    "name": "demo-http",
    "transport": "http",
    "url": "https://mcp.example.com/mcp",
    "headers": {"Authorization": "${DEMO_API_TOKEN}"},
    "enabled": false,
    "description": "演示占位符用法（注册时的探活不看 enabled 标志；本例因 DEMO_API_TOKEN 占位符无法解析而跳过探活，不实际连接）"
  }'
```

**负向用例（明文 secret 被拒）**：

```bash
curl -s -X POST "$BASE/mcp-servers" \
  -H "Authorization: Bearer $TOKEN" \
  -H "Content-Type: application/json" \
  -d '{
    "name": "demo-plaintext",
    "transport": "http",
    "url": "https://mcp.example.com/mcp",
    "headers": {"Authorization": "Bearer sk-1234567890"}
  }'
```

**预期**：422 信封，`message` 形如 `headers.Authorization must be a ${ENV_VAR} placeholder; plaintext secrets are forbidden`。

### 4.5 负向用例：shell 命令被 422 拒绝

```bash
curl -s -X POST "$BASE/mcp-servers" \
  -H "Authorization: Bearer $TOKEN" \
  -H "Content-Type: application/json" \
  -d '{
    "name": "evil-shell",
    "transport": "stdio",
    "command": "bash",
    "args": ["-c", "echo hacked"]
  }'
```

**预期**：422 信封，`message` 为 `stdio command 'bash' is a forbidden shell interpreter`。

同理可验证 `python -c` 内联模式：把 `command` 改为 `"python"`、`args` 改为 `["-c", "print(1)"]`，
预期 422 信封，`message` 为 `stdio args must not use inline execution modes (-c/-m)`。

### 4.6 跨 server 同名工具并存（命名空间行为）

再注册一个指向**同一脚本**的 server（工具裸名同为 `add`/`echo`）：

```bash
curl -s -X POST "$BASE/mcp-servers" \
  -H "Authorization: Bearer $TOKEN" \
  -H "Content-Type: application/json" \
  -d '{
    "name": "demo-stdio-dup",
    "transport": "stdio",
    "command": "/app/.venv/bin/python",
    "args": ["/app/app/tmp_mcp_demo_server.py"]
  }'
```

**预期**：HTTP **201**（工具目录按 `{server}__{tool}` 命名空间隔离，`demo-stdio__add` 与
`demo-stdio-dup__add` 并存，不再报冲突；冲突检测按 namespaced 名与现有目录比对）。
若探活失败/超时（返回 None），冲突检查被跳过同样创建成功——此时查日志确认
`mcp_server_tool_probe_timeout` / `mcp_server_tool_probe_failed`，属于设计上的降级行为。

> 另可用 `GET/PATCH/DELETE $BASE/mcp-servers/demo-stdio` 验证单条读改删；
> PATCH 同样禁止 `name`，且改 `command/args/transport` 时会重新做白名单与冲突校验。

---

## 5. SubAgent 功能

所有端点位于 `$BASE/subagents*`。

### 5.1 创建（含留空继承字段）

```bash
curl -s -X POST "$BASE/subagents" \
  -H "Authorization: Bearer $TOKEN" \
  -H "Content-Type: application/json" \
  -d '{
    "name": "search-helper",
    "description": "负责网络搜索与信息汇总的子代理",
    "when_to_use": "当用户问题需要查询实时网络信息时使用",
    "system_prompt": "你是一个搜索助手，使用搜索工具查找信息并给出带来源的简洁总结。"
  }'
```

**预期**：HTTP 201 且 `code=201`，信封 `data` 为 `SubAgentRead`：`data.allowed_tools=null`、
`data.model=null`、`data.max_turns=null`（留空即**继承父 AgentApp** 的工具/模型）、
`data.skill_names=null`（留空即继承父 AgentApp 的 skill 全集）、
`data.version=1`、`data.content_hash` 非空。

> `model` 字段的语义是 **`provider/model` 引用**（见 5.5 节）；留空继承父应用引用，
> NULL 最终解析到 `default/default`。
>
> `skill_names` 字段继承语义（与 `allowed_tools` / `model` 对齐）：
> - `null`（留空）：运行时继承父 AgentApp 已发布的 skill 全集；
> - `[]`：显式不绑定任何 skill；
> - `[<name>, ...]`：显式白名单，只绑定列表内的 skill。
>
> 单轮测试（`POST /subagents/<name>/test`）无父级上下文，会将 `null` 按 `[]` 处理。

**失败排查**：422 信封：重名（`message` 为 `subagent 'xxx' already exists`）或 `name` 不符合命名规则（`Validation error` + `data` 错误列表）。

### 5.2 列表 / 详情 / 更新

```bash
curl -s "$BASE/subagents" -H "Authorization: Bearer $TOKEN"
curl -s "$BASE/subagents/search-helper" -H "Authorization: Bearer $TOKEN"

# 分页列表（可选）：同 Skill 分页参数约定
curl -s "$BASE/subagents/page?page=1&pageSize=10&keyword=search" -H "Authorization: Bearer $TOKEN"

curl -s -X PATCH "$BASE/subagents/search-helper" \
  -H "Authorization: Bearer $TOKEN" \
  -H "Content-Type: application/json" \
  -d '{"max_turns": 3}'

# 绑定一个显式 skill 白名单（仅允许 csv-report / doc-export；其它父级 skill 被剔除）
curl -s -X PATCH "$BASE/subagents/search-helper" \
  -H "Authorization: Bearer $TOKEN" \
  -H "Content-Type: application/json" \
  -d '{"skill_names": ["csv-report", "doc-export"]}'

# 显式清空 skill（[] = 绑定 0 个 skill；与 null 继承的语义不同）
curl -s -X PATCH "$BASE/subagents/search-helper" \
  -H "Authorization: Bearer $TOKEN" \
  -H "Content-Type: application/json" \
  -d '{"skill_names": []}'

# 还原成继承父 AgentApp 全集（PATCH null 视为「未提供」，不会清空当前值；
# 若要清除，需要显式传 [] 或带白名单）
```

**预期**：PATCH 后信封 `data` 中 `max_turns=3`、`version` 自增、`content_hash` 更新；
`skill_names` PATCH 后返回更新后的列表。PATCH null 与不带 `skill_names` 字段都被后端视为「未提供」
（不会清空当前值——遵循 PATCH 语义）。空 payload 返回 422 信封，`message` 为 `nothing to update`。
`skill_names` PATCH 时列表元素必须指向已存在的全局 skill（`POST /skills` 创建过），
否则 publish 校验会 422 拒绝（参考 6.2 节）。

### 5.3 单轮测试运行（⚠️ 需外部资源 / 会消耗 token）

```bash
curl -s -X POST "$BASE/subagents/search-helper/test" \
  -H "Authorization: Bearer $TOKEN" \
  -H "Content-Type: application/json" \
  -d '{"prompt": "用一句话介绍你自己。"}'
```

**预期**：信封 `data` 为 `SubAgentTestResult`：`data.final_message`（最终回复）、`data.turns`、
`data.duration_seconds`、`data.model`。这是一次隔离的单发运行，不影响任何会话状态。

**失败排查**：404（信封 `message` 提示 subagent 不存在）；500 多为 LLM 调用失败（查日志 `subagent_test_failed`）；
429（信封 `message="Rate limit exceeded"`）默认限流 5 次/分钟。

### 5.4 负向用例：PATCH name 被 422 拒绝

```bash
curl -s -X PATCH "$BASE/subagents/search-helper" \
  -H "Authorization: Bearer $TOKEN" \
  -H "Content-Type: application/json" \
  -d '{"name": "renamed-helper"}'
```

**预期**：422 信封，`message` 为 `name is immutable and cannot be changed`。

### 5.6 SubAgent / Skill 删除的引用保护

`SubAgent` 被 `AgentApp.subagent_names` 引用时，删除 SubAgent 会被 422 拒绝；同理
`Skill` 被任一 `AgentApp.skill_names` 或 `SubAgentConfig.skill_names` 引用时，删除 Skill
也会被 422 拒绝。需要先解除引用（把 SubAgent 从 AgentApp 移除 / 把 Skill 从 AgentApp 或
SubAgent 移除）才能删除。

#### 5.6.1 删除被 AgentApp 引用的 SubAgent

```bash
# 前置：上一步 demo-assistant 已 subagent_names=["search-helper"]
curl -s -X DELETE "$BASE/subagents/search-helper" -H "Authorization: Bearer $TOKEN"
```

**预期**：422 信封，`message` 形如 `subagent 'search-helper' is referenced by: agent_app:demo-assistant`。
先 `PATCH /apps/$APP_ID` 把 `subagent_names` 清空（或去掉 `search-helper`），再删除 SubAgent 即可。

#### 5.6.2 删除被 AgentApp 或 SubAgent 引用的 Skill

```bash
# 前置：第 3 节创建过 csv-report，且 demo-assistant.skill_names 或 search-helper.skill_names 包含它
curl -s -X DELETE "$BASE/skills/csv-report" -H "Authorization: Bearer $TOKEN"
```

**预期**：422 信封，`message` 形如 `skill 'csv-report' is referenced by: agent_app:demo-assistant, subagent:search-helper`。
先 `PATCH /apps/$APP_ID` 与 `PATCH /subagents/search-helper` 把 `skill_names` 移除
（或改为不包含 `csv-report` 的列表）才能删除 Skill。

> 与 `DELETE /providers/<name>` / `DELETE /providers/<name>/models/<model>` 的「软删 + 引用保护」
> 语义对齐，但**SubAgent / Skill 是物理删除**——成功 DELETE 后 GET 即返回 404。
> 应用层的 422 拒绝确保了资产间的引用一致性；不存在「被禁用的中间状态」。

### 5.5 Provider / Model 管理（providers CRUD + 按需健康探测）

所有端点位于 `$BASE/providers*`。Provider 是连接配置（endpoint + 凭证），其下挂若干
ModelConfig；AgentApp/SubAgent `model` 字段引用 **`provider名/model名`** 对
（NULL 解析为 `default/default`）。`name` 规则同其他资产（`^[a-z0-9][a-z0-9_-]*$`，天然不含 `/`，创建后不可改名）。
**任何响应都不返回 `auth_config` 明文，只返回 `api_key_masked`（`****`+尾 4 位）。**
删除均为**软删**（`deleted=True`）：删 provider 会级联软删其全部 model，并物理删除其健康行。

#### 5.5.1 创建 provider + model

```bash
curl -s -X POST "$BASE/providers" \
  -H "Authorization: Bearer $TOKEN" \
  -H "Content-Type: application/json" \
  -d '{
    "name": "proxy",
    "type": "OPENAI_COMPATIBLE",
    "base_url": "https://your-proxy.example.com/v1",
    "auth_config": {"api_key": "sk-your-proxy-key-1234"}
  }'

curl -s -X POST "$BASE/providers/proxy/models" \
  -H "Authorization: Bearer $TOKEN" \
  -H "Content-Type: application/json" \
  -d '{
    "name": "m3",
    "model_id": "MiniMax-M3",
    "context_size": 204800,
    "extra_params": {"temperature": 0.2}
  }'
```

**预期**：两条均 HTTP 201 且 `code=201`。provider 信封 `data` 含 `api_key_masked`（如 `****1234`）、
**不存在 `auth_config`/`api_key` 字段**；model 信封 `data` 含 `ref="proxy/m3"`、`model_id`、`context_size`、`extra_params`。

**失败排查**：422 信封：重名 / 非 OLLAMA 类型缺 `auth_config.api_key` / model 名含 `/` 或
同 provider 下 `(name)`、`(model_id)` 重复；不存在 provider 下建 model 返回 404 信封。

#### 5.5.2 列表与分页（恒掩码 + model_count + health）

```bash
curl -s "$BASE/providers" -H "Authorization: Bearer $TOKEN"
curl -s "$BASE/providers/proxy" -H "Authorization: Bearer $TOKEN"
curl -s "$BASE/providers/proxy/models" -H "Authorization: Bearer $TOKEN"

# 分页列表：items 附加 model_count（启用且未删的 model 数）与 health 快照
curl -s "$BASE/providers/page?page=1&pageSize=10" -H "Authorization: Bearer $TOKEN"
```

**预期**：列表/详情均含启动时 bootstrap 种子的 `default` provider 与刚创建的 `proxy`；
每条记录只有 `api_key_masked`，无明文。未探测过的 provider `health.status="UNKNOWN"`；
分页越界参数（`page<1` 或 `pageSize>100`）返回 422 信封。

#### 5.5.3 按需连通性探测（test 端点，写回 provider_health）

```bash
curl -s -X POST "$BASE/providers/proxy/test" -H "Authorization: Bearer $TOKEN"
```

**预期**：`code=200`，信封 `data` 为 `{status, latency_ms, error_message}`。探测用 `models.list()`
（零推理成本），无后台任务：

- 成功且延迟 ≤ 阈值（`PROVIDER_HEALTH_DEGRADED_MS`，默认 5000ms）：`status="UP"`、`error_message=null`；
- 成功但延迟超阈值：`status="DEGRADED"`；
- 失败：`status="DOWN"`、`error_message` 非空（截断 500 字符）、`fail_count` 递增；
- 探测后再查 `GET /providers/page`，对应行 `health` 应反映最新状态（UP 时 `fail_count=0` 且 `last_success_at` 非空）；
- 被禁用的 provider 探测返回 422 信封。

#### 5.5.4 PATCH 更新（auth_config 省略 = 保留原值）

```bash
# 只改启用状态，不带 auth_config
curl -s -X PATCH "$BASE/providers/proxy" \
  -H "Authorization: Bearer $TOKEN" \
  -H "Content-Type: application/json" \
  -d '{"enabled": false}'

curl -s -X PATCH "$BASE/providers/proxy/models/m3" \
  -H "Authorization: Bearer $TOKEN" \
  -H "Content-Type: application/json" \
  -d '{"extra_params": {"temperature": 0.5}}'
```

**预期**：`code=200`，字段生效且 `api_key_masked` 不变（原 key 保留）。空 payload 返回 422 信封
（`message` 为 `nothing to update`）；携带 `name` 或显式 null 打在 NOT NULL 字段上返回 422 信封；
把非 OLLAMA provider 的 `auth_config` 改成无 `api_key` 也被 422 拒绝。

#### 5.5.5 DELETE 守卫（软删 + 级联 + 引用保护）

```bash
# 负向 1：default 对禁删
curl -s -X DELETE "$BASE/providers/default" -H "Authorization: Bearer $TOKEN"
curl -s -X DELETE "$BASE/providers/default/models/default" -H "Authorization: Bearer $TOKEN"
```

**预期**：两条均 422 信封，`message` 表明 default 对受保护。

```bash
# 负向 2：被引用的 model 禁删（先把某个 AgentApp 的 model 指向 proxy/m3，
# 或在 6.1 创建 app 时传 "model": "proxy/m3" 后再删）
curl -s -X PATCH "$BASE/apps/$APP_ID" \
  -H "Authorization: Bearer $TOKEN" \
  -H "Content-Type: application/json" \
  -d '{"model": "proxy/m3"}'
curl -s -X DELETE "$BASE/providers/proxy/models/m3" -H "Authorization: Bearer $TOKEN"
curl -s -X DELETE "$BASE/providers/proxy" -H "Authorization: Bearer $TOKEN"
```

**预期**：两条 DELETE 均返回 422 信封，`message` 列出引用方（`agent_app:<name>` / `subagent:<name>`）。
未被引用的可正常删除（`code=200`，信封 `data=null`；随后 GET 返回 404 信封）：
删 provider 会级联软删其全部 model；删 model 只影响单行。

> **语义注记**：编辑 Provider/ModelConfig（PATCH）**不会**把已发布 AgentApp 回退 draft（对比 6.5），
> 但会使模型指纹漂移，下次预热/编译自动用新配置重建运行时。

---

## 6. AgentApp 功能

所有端点位于 `$BASE/apps*`。创建后 `status=draft`，需显式 publish 才能关联用户（三级 Workspace 物化，见 6.6 节）；会话绑定待 G3 重建。

### 6.1 创建（关联 skill + subagent，allowed_tools 含内置 + MCP 工具）

```bash
curl -s -X POST "$BASE/apps" \
  -H "Authorization: Bearer $TOKEN" \
  -H "Content-Type: application/json" \
  -d '{
    "name": "demo-assistant",
    "system_prompt": "你是一个演示助理，可搜索网络、调用 demo 计算工具，并能委派搜索任务给子代理。",
    "allowed_tools": ["duckduckgo_results_json", "demo-stdio__add", "demo-stdio__echo"],
    "skill_names": ["csv-report"],
    "subagent_names": ["search-helper"]
  }'
```

**预期**：HTTP 201 且 `code=201`，信封 `data` 为 `AgentAppRead`：记下 `data.id`（后续记为 `$APP_ID`），
`data.status="draft"`、`data.engine="deepagents"`、`data.version=1`、`data.published_hash=null`。

> 可额外传 `"model": "proxy/m3"`（`provider/model` 引用，见 5.5 节）指定专用模型配置；
> 不传则用 `default/default`。

```bash
export APP_ID=<上一步信封 data 中返回的 id>
```

**说明**：创建阶段**不**校验工具白名单与引用存在性，这些都在 publish 时把关。
`allowed_tools` 留空（null）表示使用引擎默认（目录内全部工具）。

### 6.2 负向用例：白名单越界发布被 422 拒绝

先创建一个 `allowed_tools` 含不存在工具的应用再发布：

```bash
export BAD_APP_ID=$(curl -s -X POST "$BASE/apps" \
  -H "Authorization: Bearer $TOKEN" \
  -H "Content-Type: application/json" \
  -d '{
    "name": "bad-tools-app",
    "system_prompt": "test",
    "allowed_tools": ["no_such_tool"]
  }' | python3 -c 'import sys,json;print(json.load(sys.stdin)["data"]["id"])')

curl -s -X POST "$BASE/apps/$BAD_APP_ID/publish" \
  -H "Authorization: Bearer $TOKEN"
```

**预期**：422 信封，`message` 含 `allowed_tools not in tool catalog: agent_app:bad-tools-app -> no_such_tool`
（应用与子代理的白名单都会对照实时工具目录校验，违规项按 `agent_app:<app名> -> <工具名>` /
`subagent:<子代理名> -> <工具名>` 逐条列出）。

同类可验证的发布失败：`skill_names` 引用不存在的 skill、`subagent_names` 引用不存在的 subagent，
分别返回 422 信封，`message` 为 `referenced skill 'xxx' does not exist` / `referenced subagent 'xxx' does not exist`。
应用或子代理的 `model` 引用不存在/被禁用的 `provider/model` 对（如把 `model` PATCH 成 `ghost/none`）
也返回 422 信封，`message` 列出缺失/禁用的引用。

> **子代理 skill 引用**：发布时 `publish_agent_app` 会展开 `subagent_names` 里每个 SubAgent 的
> `skill_names` 白名单，与应用自身的 `skill_names` 取并集，缺一不可——引用不存在的全局 skill
> 时返回 422 信封，`message` 形如 `referenced skill 'xxx' (subagent '<name>') does not exist`。
> 这避免了「应用能 publish，但运行时子代理拿不到 skill」的运行时失败。

（可用 `DELETE $BASE/apps/$BAD_APP_ID` 清理该脏数据应用。）

### 6.3 正常发布

```bash
curl -s -X POST "$BASE/apps/$APP_ID/publish" \
  -H "Authorization: Bearer $TOKEN"
```

**预期**：信封 `data`（`AgentAppRead`）中 `status="published"`、`published_hash` 变为非空指纹串、
`version` 自增。日志可见 `agent_app_published`。

### 6.4 已发布列表

```bash
curl -s "$BASE/apps/published" -H "Authorization: Bearer $TOKEN"

# 分页列表（可选）：apps 按 id 序；mcp-servers 按 name 序，keyword 对 name 模糊匹配
curl -s "$BASE/apps/page?page=1&pageSize=10" -H "Authorization: Bearer $TOKEN"
curl -s "$BASE/mcp-servers/page?page=1&pageSize=10&keyword=demo" -H "Authorization: Bearer $TOKEN"
```

**预期**：信封 `data` 数组中包含刚发布的 `demo-assistant`（以及系统 default 应用——若其已发布；
default 应用由启动 bootstrap 创建，名称为 `default`）。

### 6.5 PATCH 已发布应用后回退 draft

```bash
curl -s -X PATCH "$BASE/apps/$APP_ID" \
  -H "Authorization: Bearer $TOKEN" \
  -H "Content-Type: application/json" \
  -d '{"system_prompt": "你是一个演示助理（v2 修订版）。"}'
```

**预期**：信封 `data` 中 `status` 从 `published` **回退为 `draft`**、`version` 自增。
这是安全语义：内容编辑会使已发布指纹失效，防止坏配置继续服务线上会话。

```bash
# 重新发布恢复
curl -s -X POST "$BASE/apps/$APP_ID/publish" -H "Authorization: Bearer $TOKEN"
```

**预期**：信封 `data` 中 `status` 重新变为 `published`。

### 6.6 三级 Workspace 同步（G2 冒烟 M1-M6）

三级 Workspace 的物理布局（`DATA_ROOT` 默认 `./data`，docker 环境以卷挂载目录为准）：

```
{DATA_ROOT}/global/skills/<name>/SKILL.md                              # Global 层（唯一原件）
{DATA_ROOT}/agents/<app_id>/skills/<name>/SKILL.md                     # Agent 层（publish 快照）
{DATA_ROOT}/agents/<app_id>/users/<user_id>/skills/<name>/SKILL.md     # User 层（associate 物化）
```

前置：沿用 §6.1 创建的 `$APP_ID`（绑定 `csv-report` skill）；当前用户 id 记为 `$USER_ID`
（G1 起 register 不返回 id，在 db 容器内查库：`SELECT id FROM "user" WHERE email='$EMAIL';`）。
另注册第二个用户得到 `$TOKEN2` / `$USER_ID2`（同样查库取 id，M4 需要）。

#### M1：三层复制（publish 时 Global → Agent）

```bash
curl -s -X POST "$BASE/apps/$APP_ID/publish" -H "Authorization: Bearer $TOKEN"
ls "$DATA_ROOT/global/skills/csv-report/SKILL.md" \
   "$DATA_ROOT/agents/$APP_ID/skills/csv-report/SKILL.md"
```

**预期**：发布 200 后两个文件均存在且内容一致（信封 `data.workspace_hash` 非空、
`data.agent_workspace_status="active"`）。User 层此刻**尚未**物化。

#### M2：关联用户（associate 时 Agent → User）

```bash
curl -s -X POST "$BASE/apps/$APP_ID/associate-user/$USER_ID" \
  -H "Authorization: Bearer $TOKEN"
ls "$DATA_ROOT/agents/$APP_ID/users/$USER_ID/skills/csv-report/SKILL.md"
```

**预期**：200 信封；User 层文件就位，内容 = Agent 层快照（应用自身 + 子代理引用的全局 skill 并集）。

#### M3：PATCH 已发布应用回退 draft（解读 B 四步）

```bash
curl -s -X PATCH "$BASE/apps/$APP_ID" \
  -H "Authorization: Bearer $TOKEN" \
  -H "Content-Type: application/json" \
  -d '{"skill_names": ["csv-report"]}'
```

**预期**：信封 `data` 中 `status` 回退 `draft`、`workspace_hash=null`、`agent_workspace_status="pending"`、
`version` 自增（同 §6.5，这里额外核对 G2 三个新字段）。重新 publish 可恢复。

#### M4：跨用户隔离（per-(app_id, user_id) 独立副本）

```bash
curl -s -X POST "$BASE/apps/$APP_ID/associate-user/$USER_ID2" \
  -H "Authorization: Bearer $TOKEN"
echo tampered >> "$DATA_ROOT/agents/$APP_ID/users/$USER_ID/skills/csv-report/SKILL.md"
diff "$DATA_ROOT/agents/$APP_ID/users/$USER_ID2/skills/csv-report/SKILL.md" \
     "$DATA_ROOT/agents/$APP_ID/users/$USER_ID/skills/csv-report/SKILL.md"
```

**预期**：两份用户副本相互独立；手工改动 user1 的副本不影响 user2 的副本（`diff` 有差异）。

#### M5：Lazy 校验（skill 编辑 + 重新发布后，下一次运行时调用触发 User 层重同步）

```bash
curl -s -X PATCH "$BASE/skills/csv-report" \
  -H "Authorization: Bearer $TOKEN" \
  -H "Content-Type: application/json" \
  -d '{"body": "# csv-report\n\n## Steps\n1. version-2 内容\n"}'
curl -s -X POST "$BASE/apps/$APP_ID/publish" -H "Authorization: Bearer $TOKEN"
# 触发一次 lazy 校验：G1 退役了会话端点，G2 阶段 lazy 校验（§12.1 G3 集成契约）唯一调用方是
# G3 预留入口 get_runtime，直接在 app 容器内调用它触发：
docker exec -w /app <app容器> /app/.venv/bin/python -c "
import asyncio
from app.api.v1.agent_assets_common import get_db_session
from app.services.agents import runtime
db = next(get_db_session())
try:
    asyncio.run(runtime.get_runtime(db, $APP_ID, user_id=$USER_ID))
finally:
    db.close()"
# 随后检查：
grep "version-2" "$DATA_ROOT/agents/$APP_ID/users/$USER_ID/skills/csv-report/SKILL.md"
```

**预期**：`get_runtime` 入口的 `ensure_user_workspace_up_to_date` lazy 校验发现 hash drift，
User 层被重新复制为 version-2 内容（exec 输出可见 `user_workspace_lazy_synced` 日志；
注意 `docker exec` 的日志不会进 `docker logs`，需在 exec 输出里看）。G3 会话 API 上线后，
触发点将改为 session 创建/启动入口（`spec-g3-session.md` §12）。

#### M6：启动期补建（删除 Agent 层后重启自动恢复）

```bash
rm -rf "$DATA_ROOT/agents/$APP_ID/skills"
# 重启服务（make dev 或 docker compose restart app）
ls "$DATA_ROOT/agents/$APP_ID/skills/csv-report/SKILL.md"
```

**预期**：启动期 `ensure_all_agent_workspaces` 检测到 published 应用缺失 Agent 层目录，
从 Global 层重新物化（日志可见 `agent_skills_materialized` 与 `agent_workspace_bootstrap_completed`）。

---

## 7. Chat 全链路（已退役，待 G3 重建）

> **退役说明（Phase 1 G1）**：`/chatbot/*` 全部端点（`/chat`、`/chat/stream`、`/messages`）
> 已随 G1 单层认证改造退役：`app/api/v1/chatbot.py` 现为空 router，`app/api/v1/api.py`
> 不再注册它，任何 `/chatbot/*` 请求返回 **404**。对话运行时将在 G3 阶段基于新认证面
> 与三级 Workspace（G2）重建，接口预留见 `spec-g3-session.md` §12 与
> `spec-g2-workspace.md` §12.1（`ensure_user_workspace_up_to_date` / `get_runtime` 契约）。
>
> **当前可验证的替代面**：
> - 运行时编译链路：`POST /subagents/<name>/test`（§5.3，单轮隔离运行，已含真实 LLM 与工具装配）；
> - 用户级工作区同步：§6.6 M1-M6（含 lazy 校验与启动期补建）。
>
> 本节历史内容（创建绑定会话 → 非流式对话 → SSE 流式 → 消息历史 → 默认助理）保留在
> git 历史中，G3 落地后重写。

---

## 8. HIL（人工介入，已退役，待 G3 重建）

> **退役说明**：HIL 依赖 `/chatbot/chat` 对话端点与已退役的会话机制（见第 7 节），
> 当前无法端到端验证。其机制设计（`interrupt_on` 透传 Human-in-the-loop 中间件、
> 结构化 `{"decisions": [...]}` 批准、非结构化回复默认 reject 的安全语义）将在 G3 对话
> 运行时重建时恢复，届时重写本节（原脚本保留在 git 历史）。

---

## 9. 收尾

### 9.1 清理测试资产（可选，顺带验证删除端点）

```bash
# 删除顺序建议：先应用，再子代理/MCP，最后 skill（避免发布引用校验干扰）
curl -s -X DELETE "$BASE/apps/$APP_ID" -H "Authorization: Bearer $TOKEN"
curl -s -X DELETE "$BASE/subagents/search-helper" -H "Authorization: Bearer $TOKEN"
curl -s -X DELETE "$BASE/mcp-servers/demo-stdio" -H "Authorization: Bearer $TOKEN"
# skill 删除会级联清理用户副本（含三级 Workspace 中各 {DATA_ROOT}/agents/<app_id>/users/<user_id>/skills/ 下的物化文件），
# 并校验「无人引用」（AgentApp.skill_names / SubAgentConfig.skill_names 必须先清空）。
curl -s -X DELETE "$BASE/skills/csv-report" -H "Authorization: Bearer $TOKEN"
```

每个 DELETE 成功返回信封 `{"code": 200, "message": "...", "data": null}`；对不存在的资源返回 404 信封；
被引用的 SubAgent/Skill 返回 422 信封（参考 5.6 节）。另请删除第 4.1 步放入的
`app/tmp_mcp_demo_server.py` 测试脚本文件。

### 9.2 停止容器与清理数据

```bash
make docker-down ENV=development     # 或 make stack-down ENV=development（全栈）
```

- `docker-down/stack-down` **不删除数据卷**；需要彻底重置时手动删除命名卷：
  `postgres-data`（数据库，含全部资产与会话数据）、`valkey-data`、`grafana-storage`，
  例如 `docker volume rm pml-langgraph-agent_postgres-data`（卷名以 `docker volume ls` 为准）。
- Skill/工作区文件目录：三级 Workspace 默认 `DATA_ROOT=./data`（容器内 `/app/data`），
  已通过 `./data:/app/data` bind mount 挂载到宿主机，容器重建**不会**丢失；
  彻底重置时需手动清理宿主机 `data/` 下的 `global/`、`agents/` 子目录（与删数据库卷配套）。

### 9.3 常见问题排查表

| 症状 | 可能原因 | 处置 |
|---|---|---|
| 容器启动即退出，日志有 `required environment variables are missing` | `JWT_SECRET_KEY` 或 `OPENAI_API_KEY` 缺失 | 在 `.env.development` 补齐后 `make docker-up` 重启 |
| 所有数据库相关端点 500 | 未执行迁移 | `make docker-migrate ENV=development` |
| `/health` 正常但 `/api/v1/**` 401/403 | token 过期或使用了 `Authorization` 之外的凭证 | 按第 2 节用 `/auth/refresh` 刷新或重新 `/auth/login` |
| MCP server 创建后目录里看不到工具 | 探活/加载失败或超时（30s） | 看日志 `mcp_server_tool_probe_*`；确认容器内脚本路径与 `/app/.venv/bin/python` 可用 |
| MCP 注册返回 422 命令被拒 | 命令不在 `MCP_STDIO_ALLOWED_COMMANDS`（默认 `python,node,uvx,npx`），或是 shell/内联模式 | 换白名单命令；必要时在 env 中扩展该变量并重启 |
| 子代理测试/对话 500，日志 LLM 401/403 | `OPENAI_API_KEY` 无效或余额不足 | 更换有效 key 后重启容器 |
| 429 Too Many Requests | 触发限流（默认 `chat=30/min` 等） | 等待窗口重置，或按 0.2 节放宽 `RATE_LIMIT_*` 后重启 |
| 发布/关联 422 `Agent app is not published` | 应用处于 draft（含 PATCH 后自动回退） | 重新 `POST /apps/{id}/publish` |
| 发布 422 `allowed_tools not in tool catalog` | `allowed_tools` 超出工具目录（MCP server 被禁用/删除会导致其工具从目录消失） | 用 `/tools/catalog` 对账后修正白名单 |
