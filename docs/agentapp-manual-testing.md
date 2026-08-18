# AgentApp 全功能手动测试指南

本文档指导你从零开始手动验证 AgentApp（Agent 资产管理 + 对话）全链路功能：
构建启动 → 认证 → Skill → MCP → SubAgent → AgentApp → Chat → HIL（人工介入）。

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

- **两类 token 的关键区别**（来自 `auth.py` 的依赖注入）：
  - `POST /auth/register`、`POST /auth/login` 返回的是**用户 token**；
  - `POST /auth/session` 用用户 token 换取**会话 token**；
  - `/subagents/*`、`/skills/*`、`/apps/*`、`/mcp-servers/*`、`/providers/*`、`/tools/*` 与 `/chatbot/*` 全部端点都依赖 `get_current_session`，**必须使用会话 token**。
  第 2 节会给出切换 `$TOKEN` 的明确步骤。
- **统一响应信封**：除豁免端点外，所有端点返回 `{code, message, data}` 信封——`code` 数值与
  HTTP status 完全一致（资产创建端点 HTTP 201 且 `code=201`；`POST /auth/register`
  为 HTTP 200 且 `code=200`；DELETE 成功 `data=null`）。
  422 分两种形态：**请求体校验失败**为 `{code:422, message:"Validation error", data:[错误列表]}`；
  **业务规则拒绝**（如重名、immutable name）为 `{code:422, message:"<错误文案>", data:null}`。
  其余错误信封为 `{code:<状态码>, message:"<错误文案>", data:null}`。
  **豁免端点（仍返回裸响应）**：`GET /`、`GET /health`、`GET /api/v1/health`、
  `POST /chatbot/chat/stream`（SSE）。本文所有取值命令均按信封路径提取（如 `["data"]["token"]["access_token"]`）。
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
| `SKILLS_ROOT` | 建议 | ./data/skills | SKILL.md 资产根目录（容器内相对 `/app`） |
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

# Skill 资产目录（容器内相对 /app 解析为 /app/data/skills）
SKILLS_ROOT=./data/skills

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
| `duckduckgo_search` | `duckduckgo_search.py` | DuckDuckGo 搜索，返回最多 10 条结果 |
| `ask_human` | `ask_human.py` | HIL 工具：调用 `interrupt()` 暂停执行向用户提问 |

后续创建 AgentApp / SubAgent 时的 `allowed_tools` 只能从「内置工具 + 已注册 MCP server 暴露的工具」中
选取，发布（publish）时会对照实时工具目录做白名单校验（越界返回 422）。

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

### 1.3 执行数据库迁移（必须手动，不会自动执行）

**目的**：创建全部数据表。

> **核实结论**：`scripts/docker-entrypoint.sh` **不会自动跑迁移**（脚本内注释明确写着
> "Run migrations if necessary with `make docker-migrate`"），`app/main.py` 启动流程中也没有
> `create_all`/alembic 调用。**首次启动必须先迁移，否则所有读写数据库的端点都会 500。**

```bash
make docker-migrate ENV=development     # 在 app 容器内执行 /app/.venv/bin/alembic upgrade head
make docker-migrate-history ENV=development   # （可选）查看迁移历史确认到 head
```

**预期**：alembic 依次执行迁移脚本（含 `b25d38b0cd7c_initial_schema`、`e4f1a8c2b9d3_agent_assets`、
`f3a1b7c9d204_llm_config`、`a3f7e9b1c852_provider_models`），无报错退出。

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

**预期**：HTTP 200 且 `code=200`，`data` 为 `UserResponse`：`data.id`（整数）、`data.email`、
`data.username`、`data.token.access_token`、`data.token.token_type="bearer"`、`data.token.expires_at`。

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

**预期**：信封 `data` 为 `TokenResponse`：`data.access_token`、`data.token_type="bearer"`、`data.expires_at`。

**失败排查**：401（信封 `message` 提示邮箱或密码错误）；400（`grant_type` 不是 `password`）。

```bash
# 将登录 token 记为用户 token
export USER_TOKEN=$(curl -s -X POST "$BASE/auth/login" \
  -d "email=$EMAIL" -d "password=$PASSWORD" -d "grant_type=password" | python3 -c 'import sys,json;print(json.load(sys.stdin)["data"]["access_token"])')
```

### 2.3 创建会话并切换到会话 token

**目的**：后续所有资产端点（`/subagents/*`、`/skills/*`、`/apps/*`、`/mcp-servers/*`、`/providers/*`、`/tools/*`）与 `/chatbot/*` 端点都要求**会话 token**。此处先不绑定
AgentApp（body 可省略），第 7 节再创建绑定会话。

```bash
export SESSION_RESP=$(curl -s -X POST "$BASE/auth/session" \
  -H "Authorization: Bearer $USER_TOKEN")
echo "$SESSION_RESP"
export TOKEN=$(echo "$SESSION_RESP" | python3 -c 'import sys,json;print(json.load(sys.stdin)["data"]["token"]["access_token"])')
```

**预期**：信封 `data` 含 `data.session_id`、`data.name`、`data.token.access_token`。

**约定**：**从此处起，全文 `$TOKEN` 一律指会话 token**，所有受保护请求都带
`-H "Authorization: Bearer $TOKEN"`。

**失败排查**：
- 401 信封：`$USER_TOKEN` 无效或过期；
- 422 信封（`message` 为 `Agent app is not published`）/ 404 信封（`message` 为 `Agent app not found`）：只有传了 `agent_app_id` 时才会出现
  （分别对应目标应用未发布 / 不存在）。

---

## 3. Skill 功能

所有端点位于 `$BASE/skills*`，需要会话 token。
`name` 规则：`^[a-z0-9][a-z0-9_-]*$`，最长 64 字符，创建后不可改名。

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
- 内置工具：`{"name": "duckduckgo_search", "source": "builtin"}`、`{"name": "ask_human", "source": "builtin"}`；
- MCP 工具：`{"name": "add", "source": "mcp", "server": "demo-stdio"}`、
  `{"name": "echo", "source": "mcp", "server": "demo-stdio"}`。

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

### 4.6 负向用例：重名工具被拒绝

再注册一个指向**同一脚本**的 server（工具名 `add`/`echo` 与 `demo-stdio` 冲突）：

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

**预期**：探活成功时返回 422 信封，`message` 指出工具名冲突（collision）。
若探活失败/超时（返回 None），冲突检查会被跳过而创建成功（HTTP 201 信封）——此时查日志确认
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
`data.version=1`、`data.content_hash` 非空。

> `model` 字段的语义是 **`provider/model` 引用**（见 5.5 节）；留空继承父应用引用，
> NULL 最终解析到 `default/default`。

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
```

**预期**：PATCH 后信封 `data` 中 `max_turns=3`、`version` 自增、`content_hash` 更新；
空 payload 返回 422 信封，`message` 为 `nothing to update`。

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

所有端点位于 `$BASE/apps*`。创建后 `status=draft`，需显式 publish 才能被会话绑定。

### 6.1 创建（关联 skill + subagent，allowed_tools 含内置 + MCP 工具）

```bash
curl -s -X POST "$BASE/apps" \
  -H "Authorization: Bearer $TOKEN" \
  -H "Content-Type: application/json" \
  -d '{
    "name": "demo-assistant",
    "system_prompt": "你是一个演示助理，可搜索网络、调用 demo 计算工具，并能委派搜索任务给子代理。",
    "allowed_tools": ["duckduckgo_search", "add", "echo"],
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

---

## 7. Chat 全链路

端点位于 `$BASE/chatbot/*`（`/chat`、`/chat/stream`、`/messages`）。
对话步骤均 **⚠️ 需外部资源 / 会消耗 token**。

### 7.1 创建绑定 AgentApp 的会话

```bash
export SESSION_RESP=$(curl -s -X POST "$BASE/auth/session" \
  -H "Authorization: Bearer $USER_TOKEN" \
  -H "Content-Type: application/json" \
  -d "{\"agent_app_id\": $APP_ID}")
echo "$SESSION_RESP"
export TOKEN=$(echo "$SESSION_RESP" | python3 -c 'import sys,json;print(json.load(sys.stdin)["data"]["token"]["access_token"])')
export SESSION_ID=$(echo "$SESSION_RESP" | python3 -c 'import sys,json;print(json.load(sys.stdin)["data"]["session_id"])')
```

**预期**：信封 `data` 返回新的 `session_id` 与会话 token。此后 `$TOKEN` 切换为该会话 token。

**失败排查**：404 信封（Agent app 不存在）；422 信封，`message` 为 `Agent app is not published`（对应未发布或已回退 draft 的应用）。

### 7.2 非流式对话

```bash
curl -s -X POST "$BASE/chatbot/chat" \
  -H "Authorization: Bearer $TOKEN" \
  -H "Content-Type: application/json" \
  -d '{"messages": [{"role": "user", "content": "请用 add 工具计算 17+25，并告诉我结果。"}]}'
```

**预期**：信封 `data.messages` 数组，最后一条为 `role="assistant"` 的回复（内容应给出 42）。
消息体约束：`content` 长度 1~3000，role 限 `user|assistant|system`。

**失败排查**：500 信封，查日志 `chat_request_failed`，常见为 LLM key 无效（401）或工具调用异常。

### 7.3 SSE 流式对话

```bash
curl -N -X POST "$BASE/chatbot/chat/stream" \
  -H "Authorization: Bearer $TOKEN" \
  -H "Content-Type: application/json" \
  -d '{"messages": [{"role": "user", "content": "用 echo 工具回显：hello agent app"}]}'
```

**预期**：`Content-Type: text/event-stream`，逐帧输出 `data: {...}` JSON，帧字段为：
- `content`：当前增量文本；
- `source`：来源标签——主代理为 `"coordinator"`、子代理为其名字、运行时生成帧（如中断提示）为 `"system"`；
- `done`：结束帧为 `{"content": "", "done": true}`；流内异常时也会以一帧 `done=true` 携带错误文本收尾。

### 7.4 消息历史读取与清空

```bash
curl -s "$BASE/chatbot/messages" -H "Authorization: Bearer $TOKEN"
```

**预期**：信封 `data.messages` 中包含本会话的完整历史（user/assistant 消息）。

```bash
curl -s -X DELETE "$BASE/chatbot/messages" -H "Authorization: Bearer $TOKEN"
```

**预期**：信封 `{"code": 200, "message": "Chat history cleared successfully", "data": null}`；
再次 `GET /messages` 信封 `data.messages` 返回空列表。

### 7.5 默认助理对话（不绑定 AgentApp）

```bash
export DEFAULT_RESP=$(curl -s -X POST "$BASE/auth/session" \
  -H "Authorization: Bearer $USER_TOKEN")
export DEFAULT_TOKEN=$(echo "$DEFAULT_RESP" | python3 -c 'import sys,json;print(json.load(sys.stdin)["data"]["token"]["access_token"])')

curl -s -X POST "$BASE/chatbot/chat" \
  -H "Authorization: Bearer $DEFAULT_TOKEN" \
  -H "Content-Type: application/json" \
  -d '{"messages": [{"role": "user", "content": "你好，介绍一下你自己。"}]}'
```

**预期**：信封 `data.messages` 正常返回回复。不传 `agent_app_id` 的会话在运行时回退到系统默认 AgentApp
（`name="default"`，由启动 bootstrap / 惰性重建保证存在），使用默认系统提示与默认模型。

---

## 8. HIL（人工介入，可选进阶）

> ⚠️ 本节全程消耗 token。原理：AgentApp 的 `interrupt_on`（`dict[工具名, bool]`）透传给引擎的
> Human-in-the-loop 中间件；命中工具调用时图执行暂停，对话侧收到中断提示；
> 用户下一条消息若是结构化 `{"decisions": [...]}` JSON 则按决策续跑，
> **否则（纯文本、坏 JSON 一律）按 reject 处理——不结构化回复永远不可能静默批准待决动作**。

### 8.1 创建并发布一个对搜索工具中断的应用

```bash
export HIL_APP_ID=$(curl -s -X POST "$BASE/apps" \
  -H "Authorization: Bearer $TOKEN" \
  -H "Content-Type: application/json" \
  -d '{
    "name": "hil-demo",
    "system_prompt": "你是一个演示助理。当用户要求搜索时，必须调用 duckduckgo_search 工具。",
    "allowed_tools": ["duckduckgo_search"],
    "interrupt_on": {"duckduckgo_search": true}
  }' | python3 -c 'import sys,json;print(json.load(sys.stdin)["data"]["id"])')

curl -s -X POST "$BASE/apps/$HIL_APP_ID/publish" -H "Authorization: Bearer $TOKEN"
```

**预期**：发布成功，信封 `data.status="published"`。

### 8.2 绑定会话并触发中断

```bash
export HIL_RESP=$(curl -s -X POST "$BASE/auth/session" \
  -H "Authorization: Bearer $USER_TOKEN" \
  -H "Content-Type: application/json" \
  -d "{\"agent_app_id\": $HIL_APP_ID}")
export HIL_TOKEN=$(echo "$HIL_RESP" | python3 -c 'import sys,json;print(json.load(sys.stdin)["data"]["token"]["access_token"])')

curl -s -X POST "$BASE/chatbot/chat" \
  -H "Authorization: Bearer $HIL_TOKEN" \
  -H "Content-Type: application/json" \
  -d '{"messages": [{"role": "user", "content": "搜索一下今天的科技新闻"}]}'
```

**预期**：信封 `data.messages` 的最后一条**不是**搜索结果，而是中断提示（中断载荷的字符串化展示，或兜底文案
"Waiting for input."）。此时该线程处于暂停态，等待续跑输入。

### 8.3 批准续跑

```bash
curl -s -X POST "$BASE/chatbot/chat" \
  -H "Authorization: Bearer $HIL_TOKEN" \
  -H "Content-Type: application/json" \
  -d '{"messages": [{"role": "user", "content": "{\"decisions\":[{\"type\":\"approve\"}]}"}]}'
```

**预期**：图从暂停点恢复，工具实际执行，信封 `data.messages` 返回含搜索结果的正常回复。
多个待决动作时 `decisions` 数组需按数量给出逐项决策。

### 8.4 验证纯文本回复默认 reject 的安全语义

再触发一次中断后，用纯文本回复：

```bash
curl -s -X POST "$BASE/chatbot/chat" \
  -H "Authorization: Bearer $HIL_TOKEN" \
  -H "Content-Type: application/json" \
  -d '{"messages": [{"role": "user", "content": "好的，执行吧"}]}'
```

**预期**：因回复不是合法 `{"decisions":[...]}` 结构，被解析为**等量的 reject 决策**，
工具调用被拒绝（不会执行搜索）。这正是「默认拒绝」的安全兜底。

---

## 9. 收尾

### 9.1 清理测试资产（可选，顺带验证删除端点）

```bash
# 删除顺序建议：先应用，再子代理/MCP，最后 skill（避免发布引用校验干扰）
curl -s -X DELETE "$BASE/apps/$HIL_APP_ID" -H "Authorization: Bearer $TOKEN"
curl -s -X DELETE "$BASE/apps/$APP_ID" -H "Authorization: Bearer $TOKEN"
curl -s -X DELETE "$BASE/subagents/search-helper" -H "Authorization: Bearer $TOKEN"
curl -s -X DELETE "$BASE/mcp-servers/demo-stdio" -H "Authorization: Bearer $TOKEN"
# skill 删除会级联清理用户副本（含 {SKILLS_ROOT}/users/<user_id>/ 下的物化文件）
curl -s -X DELETE "$BASE/skills/csv-report" -H "Authorization: Bearer $TOKEN"
```

每个 DELETE 成功返回信封 `{"code": 200, "message": "...", "data": null}`；对不存在的资源返回 404 信封。
另请删除第 4.1 步放入的 `app/tmp_mcp_demo_server.py` 测试脚本文件。

### 9.2 停止容器与清理数据

```bash
make docker-down ENV=development     # 或 make stack-down ENV=development（全栈）
```

- `docker-down/stack-down` **不删除数据卷**；需要彻底重置时手动删除命名卷：
  `postgres-data`（数据库，含全部资产与会话数据）、`valkey-data`、`grafana-storage`，
  例如 `docker volume rm pml-langgraph-agent_postgres-data`（卷名以 `docker volume ls` 为准）。
- Skill 文件目录：默认 `SKILLS_ROOT=./data/skills` 相对容器工作目录 `/app`，**未挂载为卷**，
  容器重建即消失；若你曾改为宿主机路径，请手动清理对应目录下的 `global/` 与 `users/` 子目录。

### 9.3 常见问题排查表

| 症状 | 可能原因 | 处置 |
|---|---|---|
| 容器启动即退出，日志有 `required environment variables are missing` | `JWT_SECRET_KEY` 或 `OPENAI_API_KEY` 缺失 | 在 `.env.development` 补齐后 `make docker-up` 重启 |
| 所有数据库相关端点 500 | 未执行迁移 | `make docker-migrate ENV=development` |
| `/health` 正常但 `/api/v1/**` 401/403 | 用了用户 token 调资产/对话端点，或 token 过期 | 按第 2 节用 `/auth/session` 换会话 token |
| MCP server 创建后目录里看不到工具 | 探活/加载失败或超时（30s） | 看日志 `mcp_server_tool_probe_*`；确认容器内脚本路径与 `/app/.venv/bin/python` 可用 |
| MCP 注册返回 422 命令被拒 | 命令不在 `MCP_STDIO_ALLOWED_COMMANDS`（默认 `python,node,uvx,npx`），或是 shell/内联模式 | 换白名单命令；必要时在 env 中扩展该变量并重启 |
| 对话 500，日志 LLM 401/403 | `OPENAI_API_KEY` 无效或余额不足 | 更换有效 key 后重启容器 |
| 429 Too Many Requests | 触发限流（默认 `chat=30/min` 等） | 等待窗口重置，或按 0.2 节放宽 `RATE_LIMIT_*` 后重启 |
| 会话绑定应用 422 `Agent app is not published` | 应用处于 draft（含 PATCH 后自动回退） | 重新 `POST /apps/{id}/publish` |
| 发布 422 `allowed_tools not in tool catalog` | `allowed_tools` 超出工具目录（MCP server 被禁用/删除会导致其工具从目录消失） | 用 `/tools/catalog` 对账后修正白名单 |
