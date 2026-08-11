# FastAPI LangGraph Agent Template

A production-ready template for building AI agent backends with FastAPI and LangGraph. Handles the hard parts — stateful conversations, long-term memory, tool calling, observability, rate limiting, auth — so you can focus on your agent logic.

**Built for AI engineers** who want a solid foundation, not a tutorial project.

---

## Powered by Atlas Cloud — Drop-in LLM Backend for LangGraph Agents

<div align="center">
  <a href="https://www.atlascloud.ai/?utm_source=github&utm_medium=link&utm_campaign=fastapi-langgraph-agent-production-ready-template">
    <picture>
      <source media="(prefers-color-scheme: dark)" srcset="docs/atlas-cloud-logo-dark.png"/>
      <img src="docs/atlas-cloud-logo.png" alt="Atlas Cloud" width="200"/>
    </picture>
  </a>
</div>

[**Atlas Cloud**](https://www.atlascloud.ai/?utm_source=github&utm_medium=link&utm_campaign=fastapi-langgraph-agent-production-ready-template) provides an **OpenAI-compatible LLM API** that integrates seamlessly into this FastAPI + LangGraph template — no code changes to your agent graph needed. Just swap `OPENAI_BASE_URL` and `OPENAI_API_KEY` to access **DeepSeek, Qwen, GLM, Kimi, MiniMax, Gemini, Claude, GPT** and more through a single unified endpoint.

The `LLMRegistry` in this template uses `langchain_openai.ChatOpenAI` — Atlas Cloud is wire-compatible, so you get instant access to 59+ curated reasoning models without touching any LangGraph logic.

### Quick Setup

**Step 1 — Get your free API key:** [atlascloud.ai/console/coding-plan](https://www.atlascloud.ai/console/coding-plan)

**Step 2 — Update `.env.development`:**

```env
OPENAI_API_KEY=<your-atlascloud-key>
OPENAI_BASE_URL=https://api.atlascloud.ai/v1
DEFAULT_LLM_MODEL=deepseek-ai/deepseek-v4-pro
```

**Step 3 — Or use directly in code:**

```python
from langchain_openai import ChatOpenAI

llm = ChatOpenAI(
    model="deepseek-ai/deepseek-v4-pro",
    openai_api_base="https://api.atlascloud.ai/v1",
    openai_api_key="<your-atlascloud-key>",
    max_tokens=512,  # reasoning model requires max_tokens >= 512
)
```

This works as a drop-in replacement anywhere `ChatOpenAI` is used in your LangGraph agent — including the `LLMRegistry`, the circular fallback service, and mem0 long-term memory.

<details>
<summary>📋 Full model catalog (59 LLMs available)</summary>

| Model ID | Provider |
|---|---|
| `deepseek-ai/DeepSeek-V3-0324` | DeepSeek |
| `deepseek-ai/deepseek-r1-0528` | DeepSeek |
| `deepseek-ai/DeepSeek-V3.1` | DeepSeek |
| `deepseek-ai/DeepSeek-V3.1-Terminus` | DeepSeek |
| `deepseek-ai/DeepSeek-V3.2-Exp` | DeepSeek |
| `deepseek-ai/deepseek-v3.2` | DeepSeek |
| `qwen/qwen3-32b` | Alibaba Qwen |
| `qwen/qwen3-8b` | Alibaba Qwen |
| `qwen/qwen3-235b-a22b-thinking-2507` | Alibaba Qwen |
| `qwen/qwen3-30b-a3b` | Alibaba Qwen |
| `qwen/qwen3-30b-a3b-thinking-2507` | Alibaba Qwen |
| `Qwen/Qwen3-Coder` | Alibaba Qwen |
| `Qwen/Qwen3-235B-A22B-Instruct-2507` | Alibaba Qwen |
| `Qwen/Qwen3-Next-80B-A3B-Instruct` | Alibaba Qwen |
| `Qwen/Qwen3-Next-80B-A3B-Thinking` | Alibaba Qwen |
| `Qwen/Qwen3-30B-A3B-Instruct-2507` | Alibaba Qwen |
| `Qwen/Qwen3-VL-235B-A22B-Instruct` | Alibaba Qwen |
| `moonshotai/Kimi-K2-Instruct` | Moonshot AI |
| `moonshotai/Kimi-K2-Instruct-0905` | Moonshot AI |
| `moonshotai/Kimi-K2-Thinking` | Moonshot AI |
| `moonshotai/kimi-k2.5` | Moonshot AI |
| `zai-org/GLM-4.6` | Zhipu AI |
| `zai-org/glm-4.7` | Zhipu AI |
| `MiniMaxAI/MiniMax-M2` | MiniMax |
| `minimaxai/minimax-m2.1` | MiniMax |
| `google/gemini-2.5-flash` | Google |
| `google/gemini-2.5-flash-preview-202509` | Google |
| `google/gemini-2.5-flash-lite` | Google |
| `google/gemini-2.5-flash-lite-preview-202509` | Google |
| `google/gemini-2.5-pro` | Google |
| `google/gemini-3-flash-preview` | Google |
| `google/gemini-2.0-flash` | Google |
| `google/gemini-2.0-flash-lite` | Google |
| `openai/gpt-5.1` | OpenAI |
| `openai/gpt-5.1-chat` | OpenAI |
| `openai/gpt-5.1-codex` | OpenAI |
| `openai/gpt-5.1-codex-mini` | OpenAI |
| `openai/gpt-5.1-codex-max` | OpenAI |
| `openai/gpt-4o` | OpenAI |
| `openai/gpt-4o-mini` | OpenAI |
| `openai/gpt-4.1` | OpenAI |
| `openai/gpt-4.1-mini` | OpenAI |
| `openai/gpt-4.1-nano` | OpenAI |
| `openai/o1` | OpenAI |
| `openai/o3` | OpenAI |
| `openai/o3-mini` | OpenAI |
| `openai/o4-mini` | OpenAI |
| `openai/o3-pro` | OpenAI |
| `openai/gpt-5` | OpenAI |
| `openai/gpt-5-chat` | OpenAI |
| `openai/gpt-5-codex` | OpenAI |
| `openai/gpt-5-mini` | OpenAI |
| `openai/gpt-5-nano` | OpenAI |
| `openai/gpt-5-pro` | OpenAI |
| `openai/gpt-5.2` | OpenAI |
| `openai/gpt-5.2-chat` | OpenAI |
| `anthropic/claude-sonnet-4-20250514` | Anthropic |
| `anthropic/claude-haiku-4.5-20251001` | Anthropic |
| `anthropic/claude-sonnet-4.5-20250929` | Anthropic |
| `anthropic/claude-opus-4.1-20250805` | Anthropic |
| `anthropic/claude-opus-4-20250514` | Anthropic |
| `anthropic/claude-opus-4.5-20251101` | Anthropic |

[View live model list →](https://www.atlascloud.ai/?utm_source=github&utm_medium=link&utm_campaign=fastapi-langgraph-agent-production-ready-template)

</details>

---

## What's included

- **LangGraph** stateful agent with checkpointing, tool calling, and human-in-the-loop support
- **Long-term memory** via mem0 + pgvector — semantic search per user, cache-backed
- **LLM service** with circular model fallback, exponential backoff retries, and total timeout budget
- **Langfuse** tracing on all LLM calls; Prometheus metrics + Grafana dashboards
- **JWT auth** with session management; rate limiting via slowapi
- **Alembic** migrations; optional Valkey/Redis cache layer
- **Structured logging** with request/session/user context on every line
- **Workflow engine** — declarative YAML workflows compiled to LangGraph, runnable via CLI and HTTP API

## Quickstart

```bash
git clone <repo-url> my-agent && cd my-agent
cp .env.example .env.development   # fill in your keys
make install
make docker-up                     # starts API + PostgreSQL
```

Open [http://localhost:8000/docs](http://localhost:8000/docs) to see the interactive API.

> For local development without Docker see [docs/getting-started.md](docs/getting-started.md).

## Documentation

| Guide | What it covers |
|---|---|
| [Getting Started](docs/getting-started.md) | Prerequisites, local setup, first API call |
| [Architecture](docs/architecture.md) | System design, request flow, component diagrams |
| [Configuration](docs/configuration.md) | All environment variables with defaults |
| [Authentication](docs/authentication.md) | JWT flow, sessions, endpoint reference |
| [Database & Migrations](docs/database.md) | Schema, Alembic migrations, pgvector |
| [LLM Service](docs/llm-service.md) | Models, retries, fallback, timeout budget |
| [Memory](docs/memory.md) | mem0 long-term memory, cache layer |
| [Observability](docs/observability.md) | Langfuse, structured logging, Prometheus, profiling |
| [Evaluation](docs/evaluation.md) | Eval framework, custom metrics, reports |
| [Docker](docs/docker.md) | Docker, Compose, full monitoring stack |
| [Workflow Engine](#workflow-engine) | Declarative YAML workflows (CLI + HTTP), see also [architecture overview](docs/workflow-reimpl-plan/00-架构总览.md) |

## Project structure

```
app/
  api/v1/          # Route handlers
  core/
    langgraph/     # Agent graph + tools
    prompts/       # System prompt template
    cache.py       # Valkey/Redis + in-memory fallback
    config.py      # Settings
    middleware.py  # Metrics, logging context, profiling
    limiter.py     # Rate limiting
  models/          # SQLModel ORM models
  schemas/         # Pydantic request/response schemas
  services/        # LLM, database, memory services
alembic/           # Database migrations
evals/             # LLM evaluation framework
```

## Contributing

PRs welcome. Please read [docs/getting-started.md](docs/getting-started.md) to get your environment set up, then follow the coding conventions in [AGENTS.md](AGENTS.md).

Report security issues privately — see [SECURITY.md](SECURITY.md).

## License

See [LICENSE](LICENSE).

## Workflow Engine

Declarative workflow engine: YAML DSL → compiled LangGraph 图，CLI 与 HTTP 双入口共用同一
`ApiResponse` 信封。设计与完整规范见
[docs/workflow-reimpl-plan/00-架构总览.md](docs/workflow-reimpl-plan/00-架构总览.md)（三层：
DSL 模型 → 图编译 → 注册表运行时）。

```
YAML (config/*.yaml) → models 解析/校验 → GraphBuilder 编译 StateGraph
    → WorkflowRegistry（per-workflow RLock + 运行级日志收集）
    → CLI (python -m app.workflow) / FastAPI (POST /api/v1/workflows/{id}/execute)
```

### 快速开始

```bash
make install                              # uv sync + pre-commit
cp .env.example .env                      # 填入 OPENAI_API_KEY（LLM 示例需要）

# 示例位于 app/workflow/config/examples/：
#   minimal.yaml          LLM 单节点（需 OPENAI_API_KEY）
#   http_demo.yaml        HTTP 节点 mock 演示（无需任何 key）
#   condition_branch.yaml LLM → 条件边 → HTTP 组合（需 OPENAI_API_KEY）

# HTTP mock 示例（零外部依赖，验证安装成功）
uv run python -m app.workflow run --workflow demo_http --input '{"input":"hi"}'

# LLM 示例（S5：LLM 节点从 state.messages 取对话内容，dict 形态自动转换）
uv run python -m app.workflow run --workflow demo_minimal \
  --input '{"messages": [{"role": "user", "content": "hi"}]}'

# 跑测试（零真实网络 / 零真实 LLM）
uv run pytest -m unit -q
uv run pytest -m integration -q
```

HTTP API（宿主启动时已注入注册表，示例目录为 `app/workflow/config/examples/`）：

```bash
make dev   # 或 make docker-up
curl -X POST http://localhost:8000/api/v1/workflows/demo_http/execute \
  -H 'Content-Type: application/json' -d '{"input": "hi"}'
```

### 目录结构

```
app/workflow/
  models.py         # YAML DSL Pydantic 模型 + 异常族（WorkflowEngineError 基类）
  state.py          # 动态 state 模型工厂（reducer/预声明 {node}_result）
  nodes/            # BaseNode + LLMNode + HTTPNode + PythonNode + 插件注册工厂
  graph_builder.py  # 校验 → 建图 → 条件路由 → 编译
  registry.py       # 进程级注册表：per-workflow RLock + 运行级日志收集
  cli.py            # CLI 入口与 ApiResponse 信封（stdout 信封 / stderr 日志）
  api.py            # FastAPI 路由（app.state 注入注册表）
  logging_conf.py   # structlog 配置 + 密钥脱敏处理器（H6）
  config/examples/  # 三个可运行示例 YAML
```

### FAQ（工作流引擎）

**reducer 语义是什么？**
state 字段在 YAML `state_schema` 里显式声明 `reducer: add`（列表追加合并）或
`reducer: last`（后写覆盖）；不声明则为普通 LastValue 字段。引擎不对任何字段名做
特殊化处理（C2）；未声明的 `history` 会自动注入为 add 通道。

**双写开关 / `{node}_result` 是什么？**
每个节点输出会双写：平铺字段写回同名 state 键（需在 `state_schema` 声明，否则被
langgraph 丢弃，S4），同时整包写入构建期预声明的 `{node}_result` 槽位（EXP-G8），
条件边与后续节点一般从 `{node}_result` 读取上游输出。

**条件表达式支持什么语法？**
仅两种（绝不 eval，S7）：`path == '字面量'`（点路径取值与字面量相等比较）与
`path`（真值判断）。所有条件均不命中时按 `no_match_policy` 处理：默认 `raise`
抛 `ConditionNotMatchedError`；宿主可配置 `default` 并提供 `default_edges` 兜底分支。

**错误信封长什么样？**
CLI 与 HTTP 共用 `ApiResponse`：`{"success": bool, "data": ..., "error": str|null,
"metadata": {...}}`。失败时 error 为脱敏后的摘要（密钥片段替换为 `***`，H6），
CLI 失败退出码为 1；HTTP 未找到 workflow 返回 404，执行失败返回 500，均携带失败信封。

**LLM 示例为什么传 messages 而不是 input？**
LLM 节点契约（S5）从 `state.messages` 取对话内容；`{"role": ..., "content": ...}`
dict 形态由 langchain 本地转换为消息对象，无需真实调用即可在 YAML 中声明
`messages: {type: list}` 后从 CLI/HTTP 传入。

### 扩展指南：自定义节点类型

```python
from typing import Any, override
from langchain_core.runnables import Runnable

from app.workflow.nodes.base import BaseNode
from app.workflow.nodes.factory import register_node_type
from app.workflow.utils import convert_state_to_dict, map_output_to_state


class MyNode(BaseNode):
    @override
    def build_runnable(self) -> Runnable:
        def func(state: Any) -> dict[str, Any]:
            state_dict = convert_state_to_dict(state)          # R3 入口：不 mutate 输入
            output = {"answer": 42}
            return map_output_to_state(self.name, output, state_dict)  # R3 出口：双写

        return self.wrap_runnable(func)                        # 自动日志/异常包装

    @override
    def validate_config(self) -> bool:
        return True


register_node_type("my_node", MyNode)   # 需在 registry 构建前注册
```

随后在 YAML 中使用 `type: my_node`。**缓存约束（H4）**：自定义节点与引擎模块一律
禁止模块级无界缓存（模块级 dict / `functools.lru_cache`）；如需跨运行复用，
由宿主组装点显式持有并经构造参数传入（参照 `app.state.workflow_registry` 注入模式）。
**密钥约束（H6）**：节点不得在配置中接收明文密钥，一律经环境变量解析；
日志只输出摘要，不输出完整 state。

### SDN 巡检示例（通用 python 节点实战）

`app/sdn/` 演示用 YAML 编排移植外部项目的 SDN 接口告警巡检 Agent（引擎零改动）：
token → 告警查询 → 逐条告警循环检查设备（YAML 条件回边）→ LLM 汇总报告。

```bash
cp app/sdn/config/sdn_alert_inspection.template.yaml app/sdn/config/sdn_alert_inspection.yaml
# 在副本中填入 SDN 控制器凭据（副本已加 .gitignore，不入库）；LLM 凭据走 .env
uv run python -m app.sdn run --workflow sdn_alert_inspection --input '{}'
# stdout 输出 ApiResponse 信封，报告在 data.report
```

要点：

- 业务逻辑全部写在 `type: python` 节点的 YAML 内联 `code`（或 `entry: 模块:函数`）里，
  HTTP 调用用内置 HTTPNode；循环由条件回边表达（`has_next == 'true'`）。
- **python 节点为进程内可信代码执行，非隔离沙箱**（langchain-sandbox 已弃维护，排除）；
  代码必须 `return dict`，日志只记代码长度/entry 名，不落代码内容。
- 入口 `app/sdn/__main__.py` 有两处进程级调整并有注释标注：自签证书放宽 httpx verify
  （仅本入口进程）、`LANGGRAPH_DEFAULT_RECURSION_LIMIT=200`（循环步数超默认 25）。
- DEBUG 级日志会输出 HTTP 完整响应（含 token），验证请用默认 INFO 级。

## FAQ

### General

**What is this template?**
A production-ready foundation for AI agent backends built on FastAPI + LangGraph. It bundles the components you'd otherwise wire up by hand: stateful conversations, long-term memory, tool calling, observability, rate limiting, and JWT auth.

**How does this differ from a basic LangGraph setup?**
The base LangGraph quickstart stops at "agent runs locally". This template adds Alembic migrations, mem0 + pgvector long-term memory, Langfuse tracing, Prometheus + Grafana dashboards, JWT sessions, slowapi rate limiting, structured logging with per-request context, and a circular-fallback LLM service — production concerns you'd otherwise build separately.

### Setup & Configuration

**Do I need Docker?**
Recommended but not required. `make docker-up` starts the API + PostgreSQL together. For local-only setup see [docs/getting-started.md](docs/getting-started.md).

**Which LLM providers are supported?**
Today: **OpenAI only** via the `LLMRegistry` in `app/services/llm/registry.py`. Multi-provider support (Anthropic, Google, OpenRouter) via LangChain's `init_chat_model` is planned — see [#51](https://github.com/wassim249/fastapi-langgraph-agent-production-ready-template/issues/51). Configure your model via `DEFAULT_LLM_MODEL` in `.env.development`.

**How do I configure long-term memory?**
Long-term memory is self-hosted: mem0 runs in-process and persists into your existing PostgreSQL via pgvector — there is no separate mem0 cloud account or API key. You only need a working `OPENAI_API_KEY` (used for fact extraction + embeddings) and the pgvector extension enabled. See [docs/memory.md](docs/memory.md) for details.

### Development

**How do I add a custom tool?**
Drop a LangChain `@tool`-decorated function in `app/core/langgraph/tools/` and register it in the `tools` list exported from that package. The agent picks it up on next start; no graph changes needed.

**How does the LLM service handle failures?**
Two layers: (1) per-call exponential-backoff retry via `tenacity`, (2) **circular fallback** — if the active model exhausts its retries, the service rotates to the next model in `LLMRegistry` and continues. A total timeout budget caps the whole call so latency stays bounded. See [docs/llm-service.md](docs/llm-service.md).

**Can I use this without Langfuse?**
Yes. Set `LANGFUSE_TRACING_ENABLED=false` (or omit the Langfuse keys). The agent runs unchanged; structured logs still capture request/session/user context.

### Troubleshooting

**The API won't start**
- Ensure PostgreSQL is running (`make docker-up` brings it up alongside the API)
- Confirm `.env.development` exists — copy from `.env.example` and fill in required keys
- Apply migrations: `make migrate`

**Memory / semantic search returns nothing**
- Verify the `pgvector` extension is enabled in your PostgreSQL instance
- Confirm `OPENAI_API_KEY` is valid (mem0 calls OpenAI for fact extraction + embeddings)
- Check `LONG_TERM_MEMORY_MODEL` and `LONG_TERM_MEMORY_EMBEDDER_MODEL` are set in `.env.development`

**Rate limiting is too aggressive**
Limits are defined in `app/core/limiter.py` (slowapi). Adjust per-route decorators or the default rate in that file. See [docs/configuration.md](docs/configuration.md) for the related env vars.
